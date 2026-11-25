import hashlib
import json
import logging
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .engines.base import ConversionEngine, EngineError, EngineInfo, ErrorCode
from .engines.registry import configured_registry
from .filenames import upload_basename
from .artifacts import ExportIntegrityError, build_archive, document_markdown, figure_index


def conversion_worker_count() -> int:
    try:
        workers = int(os.getenv('PDF2MD_CONVERSION_WORKERS', '1'))
    except ValueError as exc:
        raise ValueError('PDF2MD_CONVERSION_WORKERS must be a positive integer') from exc
    if workers < 1:
        raise ValueError('PDF2MD_CONVERSION_WORKERS must be a positive integer')
    return workers


ROOT = Path(os.getenv('APP_DATA', '/data')).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
MAX_BYTES = int(os.getenv('MAX_PDF_BYTES', str(100 * 1024 * 1024)))
POOL = ThreadPoolExecutor(max_workers=conversion_worker_count())
ACTIVE = set()
LOCK = threading.Lock()
ENGINES = configured_registry()
DEFAULT_ENGINE = 'docling'
logger = logging.getLogger(__name__)
app = FastAPI(title='PDF to Markdown API', version='0.1.0')


def job_dir(job_id: str) -> Path:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(400, 'Invalid job identifier')
    path = ROOT / job_id
    if not path.is_dir():
        raise HTTPException(404, 'Job not found')
    return path


def write_json(path: Path, value: dict):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def status(path: Path, state: str, error: str | None = None, **details: object):
    obj = json.loads((path / 'status.json').read_text())
    obj.update(state=state, error=error, updated_at=datetime.now(timezone.utc).isoformat(), **details)
    write_json(path / 'status.json', obj)


def convert(job_id: str, engine: ConversionEngine, source_filename: str):
    path = ROOT / job_id
    try:
        status(path, 'running')
        result = engine.convert(path / 'input.pdf', path, source_filename=source_filename)
        pages = [''] * max((page.source_page for page in result.pages), default=0)
        for page in result.pages:
            pages[page.source_page - 1] = page.markdown
        markdown, page_anchors = document_markdown(result.markdown, pages)
        encoded = markdown.encode('utf-8')
        output = path / 'output.md'
        (path / 'output.tmp').write_bytes(encoded)
        os.replace(path / 'output.tmp', output)
        write_json(path / 'pages.json', {'pages': pages, 'page_anchors': page_anchors})
        write_json(path / 'image-index.json', figure_index(path, result.images))
        write_json(path / 'metadata.json', {
            'original_markdown_sha256': hashlib.sha256(encoded).hexdigest(),
            'converted_markdown_mtime_ns': output.stat().st_mtime_ns,
        })
        status(path, 'complete', engine_version=result.engine_version, warnings=result.warnings)
    except EngineError as exc:
        logger.warning('Conversion job %s failed (%s)', job_id, exc.code, exc_info=True)
        status(path, 'failed', str(exc), error_code=exc.code)
    except Exception:
        logger.exception('Conversion job %s failed', job_id)
        status(path, 'failed', 'Conversion failed. See server logs.', error_code='conversion_failed')
    finally:
        with LOCK:
            ACTIVE.discard(job_id)


class EditRequest(BaseModel):
    markdown: str


class EnginesResponse(BaseModel):
    default_engine: str
    engines: list[EngineInfo]


class EngineErrorDetail(BaseModel):
    code: ErrorCode
    message: str


@app.on_event('startup')
def recover():
    for path in ROOT.iterdir():
        if path.is_dir() and (path / 'status.json').exists():
            obj = json.loads((path / 'status.json').read_text())
            if obj['state'] in ('queued', 'running'):
                status(path, 'interrupted', 'Conversion interrupted by service restart')


@app.get('/api/health')
def health():
    return {'ok': True, 'engines': [
        {'id': engine.id, 'available': engine.available} for engine in ENGINES.describe()
    ]}


@app.get('/api/engines')
def list_engines() -> EnginesResponse:
    return EnginesResponse(default_engine=DEFAULT_ENGINE, engines=ENGINES.describe())


@app.post('/api/jobs', status_code=202)
async def create_job(file: UploadFile = File(...), engine: str = DEFAULT_ENGINE):
    filename = upload_basename(file.filename or '')
    if not filename.lower().endswith('.pdf'):
        raise HTTPException(422, 'Upload a PDF file')
    try:
        adapter = await run_in_threadpool(ENGINES.select, engine)
    except EngineError as exc:
        error = EngineErrorDetail(code=exc.code, message=str(exc))
        raise HTTPException(422 if exc.code == 'unknown_engine' else 503, error.model_dump()) from exc
    job_id = str(uuid.uuid4())
    path = ROOT / job_id
    path.mkdir(mode=0o700)
    try:
        size = 0
        checksum = hashlib.sha256()
        with (path / 'input.pdf').open('wb') as dest:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, 'PDF exceeds upload size limit')
                checksum.update(chunk)
                dest.write(chunk)
        with (path / 'input.pdf').open('rb') as source:
            valid_pdf = size >= 5 and source.read(5) == b'%PDF-'
        if not valid_pdf:
            raise HTTPException(422, 'File is not a PDF')
        write_json(path / 'status.json', {'id': job_id, 'filename': filename,
            'source_pdf_sha256': checksum.hexdigest(),
            'engine': adapter.id, 'adapter_version': adapter.adapter_version, 'engine_options': dict(adapter.config.options),
            'state': 'queued', 'error': None, 'created_at': datetime.now(timezone.utc).isoformat()})
        with LOCK:
            ACTIVE.add(job_id)
        POOL.submit(convert, job_id, adapter, filename)
        return {'id': job_id}
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    path = job_dir(job_id)
    obj = json.loads((path / 'status.json').read_text())
    if obj['state'] == 'complete':
        obj['markdown'] = (path / 'output.md').read_text(encoding='utf-8')
        pages = json.loads((path / 'pages.json').read_text(encoding='utf-8'))
        obj['pages'] = pages['pages']
        obj['page_anchors'] = pages.get('page_anchors', False)
        obj['images'] = json.loads((path / 'image-index.json').read_text(encoding='utf-8'))['images']
    return obj


@app.get('/api/jobs/{job_id}/pdf')
def pdf(job_id: str):
    return FileResponse(job_dir(job_id) / 'input.pdf', media_type='application/pdf')


@app.get('/api/jobs/{job_id}/assets/{asset_path:path}')
def asset(job_id: str, asset_path: str):
    path = job_dir(job_id)
    images = (path / 'images').resolve()
    if '\0' in asset_path:
        raise HTTPException(404, 'Asset not found')
    target = (images / asset_path).resolve()
    if (not images.is_relative_to(path.resolve()) or not target.is_relative_to(images)
            or target.suffix.lower() != '.png' or not target.is_file()):
        raise HTTPException(404, 'Asset not found')
    return FileResponse(target, media_type='image/png')


@app.put('/api/jobs/{job_id}/markdown')
def edit(job_id: str, payload: EditRequest):
    path = job_dir(job_id)
    if json.loads((path / 'status.json').read_text())['state'] != 'complete':
        raise HTTPException(409, 'Job not complete')
    encoded = payload.markdown.encode('utf-8')
    if len(encoded) > 20 * 1024 * 1024:
        raise HTTPException(413, 'Markdown too large')
    tmp = path / 'output.tmp'
    tmp = path / f'edit-{uuid.uuid4()}.tmp'
    try:
        tmp.write_bytes(encoded)
        os.replace(tmp, path / 'output.md')
    finally:
        tmp.unlink(missing_ok=True)
    return {'saved': True}


@app.get('/api/jobs/{job_id}/download')
def download(job_id: str):
    path = job_dir(job_id)
    job = json.loads((path / 'status.json').read_text(encoding='utf-8'))
    if job['state'] != 'complete':
        raise HTTPException(409, 'Conversion not completed')
    try:
        archive = build_archive(path, job, app.version)
    except ExportIntegrityError as exc:
        raise HTTPException(409, str(exc)) from exc
    return StreamingResponse(
        iter(lambda: archive.read(64 * 1024), b''),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="converted-document.zip"'},
        background=BackgroundTask(archive.close),
    )
