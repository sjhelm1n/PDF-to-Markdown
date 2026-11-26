import asyncio
import errno
import hashlib
import logging
import shutil
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .artifacts import ExportIntegrityError, build_archive, document_markdown, figure_index
from .engines.base import ConversionEngine, EngineError, EngineInfo, ErrorCode
from .engines.registry import EngineRegistry, configured_registry
from .filenames import upload_basename
from .jobs import (
    STORAGE_MESSAGE, Admission, JobStore, StorageFull, is_job_id, json_bytes, read_json, read_status, utc_now,
)
from .pdf_validation import InvalidPdf, validate_pdf
from .request_limits import RequestLimits
from .settings import MIB, Settings

DEFAULT_ENGINE = 'docling'
logger = logging.getLogger(__name__)


def convert(jobs: JobStore, path: Path, engine: ConversionEngine, source_filename: str) -> None:
    try:
        jobs.status(path, 'running')
        result = engine.convert(path / 'input.pdf', path, source_filename=source_filename)
        pages = [''] * max((page.source_page for page in result.pages), default=0)
        for page in result.pages:
            pages[page.source_page - 1] = page.markdown
        markdown, page_anchors = document_markdown(result.markdown, pages)
        encoded = markdown.encode('utf-8')
        output = path / 'output.md'
        jobs.write_file(output, encoded)
        jobs.write_file(path / 'pages.json', json_bytes({'pages': pages, 'page_anchors': page_anchors}))
        jobs.write_file(path / 'image-index.json', json_bytes(figure_index(path, result.images)))
        jobs.write_file(path / 'metadata.json', json_bytes({
            'original_markdown_sha256': hashlib.sha256(encoded).hexdigest(),
            'converted_markdown_mtime_ns': output.stat().st_mtime_ns,
        }))
        jobs.status(path, 'complete', engine_version=result.engine_version, warnings=result.warnings)
        return
    except StorageFull:
        message, code = STORAGE_MESSAGE, 'storage_limit'
    except EngineError as exc:
        logger.warning('Conversion job %s failed (%s)', path.name, exc.code, exc_info=True)
        message, code = str(exc), exc.code
    except Exception as exc:
        logger.exception('Conversion job %s failed', path.name)
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            message, code = STORAGE_MESSAGE, 'storage_limit'
        else:
            message, code = 'Conversion failed. See server logs.', 'conversion_failed'

    try:
        jobs.discard_outputs(path)
    finally:
        jobs.status(path, 'failed', message, error_code=code)


def finish_job(jobs: JobStore, admission: Admission, future: Future[None]) -> None:
    try:
        if future.cancelled() or future.exception() is not None:
            jobs.status(jobs.root / admission.job_id, 'interrupted', 'Conversion interrupted before completion')
    except OSError:
        logger.exception('Unable to record interruption for job %s', admission.job_id)
    finally:
        jobs.release(admission)


class EditRequest(BaseModel):
    markdown: str


class EnginesResponse(BaseModel):
    default_engine: str
    engines: list[EngineInfo]


class EngineErrorDetail(BaseModel):
    code: ErrorCode
    message: str


def create_app(settings: Settings | None = None, engines: EngineRegistry | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engines = engines or configured_registry(pdf_pages=settings.pdf_pages)
    jobs = JobStore(settings)

    async def sweep() -> None:
        while True:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            await run_in_threadpool(jobs.cleanup)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await run_in_threadpool(jobs.recover)
        pool = ThreadPoolExecutor(max_workers=1)
        app.state.pool = pool
        clean_task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            clean_task.cancel()
            try:
                await clean_task
            except asyncio.CancelledError:
                pass
            await run_in_threadpool(pool.shutdown, cancel_futures=True)

    app = FastAPI(title='PDF to Markdown API', version='0.1.0', lifespan=lifespan)
    app.state.jobs = jobs
    app.add_middleware(RequestLimits, settings=settings, jobs=jobs)

    @app.exception_handler(StorageFull)
    async def storage_full(request: Request, exc: StorageFull) -> JSONResponse:
        return JSONResponse({'detail': STORAGE_MESSAGE}, status_code=507)

    def job_path(job_id: str) -> Iterator[Path]:
        if not is_job_id(job_id):
            raise HTTPException(400, 'Invalid job identifier')
        try:
            with jobs.open_job(job_id) as path:
                yield path
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Job not found') from exc

    JobPath = Annotated[Path, Depends(job_path)]

    @app.get('/api/health')
    def health():
        return {'ok': True, 'engines': [
            {'id': engine.id, 'available': engine.available} for engine in engines.describe()
        ]}

    @app.get('/api/engines')
    def list_engines() -> EnginesResponse:
        return EnginesResponse(default_engine=DEFAULT_ENGINE, engines=engines.describe())

    @app.post('/api/jobs', status_code=202)
    def create_job(request: Request, file: UploadFile = File(...), engine: str = DEFAULT_ENGINE):
        filename = upload_basename(file.filename or '')
        if not filename.lower().endswith('.pdf'):
            raise HTTPException(422, 'Upload a PDF file')
        if file.size is None or file.size > settings.pdf_bytes:
            raise HTTPException(413, 'PDF exceeds upload size limit')
        if file.file.read(5) != b'%PDF-':
            raise HTTPException(422, 'File is not a PDF')
        file.file.seek(0)
        try:
            adapter = engines.select(engine)
        except EngineError as exc:
            error = EngineErrorDetail(code=exc.code, message=str(exc))
            raise HTTPException(422 if exc.code == 'unknown_engine' else 503, error.model_dump()) from exc

        admission: Admission = request.state.admission
        path = jobs.root / admission.job_id
        source = path / 'input.pdf'
        try:
            path.mkdir(mode=0o700)
            checksum = hashlib.sha256()
            with jobs.reserve_file(source, file.size), source.open('xb') as dest:
                while chunk := file.file.read(MIB):
                    checksum.update(chunk)
                    dest.write(chunk)
            try:
                page_count = validate_pdf(source, settings.pdf_pages)
            except InvalidPdf as exc:
                raise HTTPException(422, str(exc)) from exc
            jobs.write_file(path / 'status.json', json_bytes({
                'id': admission.job_id, 'filename': filename, 'source_pdf_sha256': checksum.hexdigest(),
                'source_page_count': page_count, 'engine': adapter.id, 'adapter_version': adapter.adapter_version,
                'engine_options': dict(adapter.config.options), 'state': 'queued', 'error': None,
                'created_at': utc_now(),
            }))
            future = request.app.state.pool.submit(convert, jobs, path, adapter, filename)
        except BaseException as exc:
            shutil.rmtree(path, ignore_errors=True)
            if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                raise StorageFull from exc
            raise

        admission.submitted = True
        future.add_done_callback(lambda done: finish_job(jobs, admission, done))
        return {'id': admission.job_id}

    @app.get('/api/jobs/{job_id}')
    def get_job(path: JobPath):
        obj = read_status(path)
        if obj['state'] == 'complete':
            obj['markdown'] = (path / 'output.md').read_text(encoding='utf-8')
            pages = read_json(path / 'pages.json')
            obj['pages'] = pages['pages']
            obj['page_anchors'] = pages.get('page_anchors', False)
            obj['images'] = read_json(path / 'image-index.json')['images']
        return obj

    @app.get('/api/jobs/{job_id}/pdf')
    def pdf(path: JobPath):
        return FileResponse(path / 'input.pdf', media_type='application/pdf')

    @app.get('/api/jobs/{job_id}/assets/{asset_path:path}')
    def asset(path: JobPath, asset_path: str):
        images = (path / 'images').resolve()
        if '\0' in asset_path:
            raise HTTPException(404, 'Asset not found')
        target = (images / asset_path).resolve()
        if (not images.is_relative_to(path.resolve()) or not target.is_relative_to(images)
                or target.suffix.lower() != '.png' or not target.is_file()):
            raise HTTPException(404, 'Asset not found')
        return FileResponse(target, media_type='image/png')

    @app.put('/api/jobs/{job_id}/markdown')
    def edit(path: JobPath, payload: EditRequest):
        if read_status(path)['state'] != 'complete':
            raise HTTPException(409, 'Job not complete')
        try:
            encoded = payload.markdown.encode('utf-8')
        except UnicodeEncodeError as exc:
            raise HTTPException(422, 'Markdown must be valid UTF-8.') from exc
        if len(encoded) > 20 * MIB:
            raise HTTPException(413, 'Markdown too large')
        try:
            jobs.write_file(path / 'output.md', encoded)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise StorageFull from exc
            raise
        return {'saved': True}

    @app.get('/api/jobs/{job_id}/download')
    def download(path: JobPath):
        job = read_status(path)
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

    return app


app = create_app()
