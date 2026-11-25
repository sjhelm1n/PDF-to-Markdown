import hashlib
import json
import os
import shutil
import struct
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from urllib.parse import unquote, urlsplit

from .engines.base import ImageResult


class ExportIntegrityError(Exception):
    """An export failure with a message safe to show in the review UI."""


def document_markdown(markdown: str, pages: list[str]) -> tuple[str, bool]:
    if not pages or not all(page.strip() for page in pages) or '\n\n'.join(pages) != markdown:
        return markdown, False
    return '\n\n'.join(f'<!-- source-page: {index} -->\n\n{page}'
                       for index, page in enumerate(pages, 1)), True


def file_sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _image_path(workspace: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    base = (workspace / 'images').resolve()
    if (not relative.parts or relative.parts[0] != 'images' or relative.suffix.lower() != '.png'
            or any(part == '..' or '\\' in part or '\0' in part for part in relative.parts)):
        raise ExportIntegrityError('Export images must use relative PNG paths under images/.')
    path = (workspace / relative).resolve()
    if not base.is_relative_to(workspace.resolve()) or not path.is_relative_to(base) or not path.is_file():
        raise ExportIntegrityError('An image is missing from the conversion workspace.')
    return path


def _figure_record(workspace: Path, name: str, source_order: int) -> dict:
    path = _image_path(workspace, name)
    with path.open('rb') as stream:
        header = stream.read(24)
        if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
            raise ExportIntegrityError('A conversion image is not a valid PNG.')
        width, height = struct.unpack('>II', header[16:24])
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'id': f'fig-{source_order:06}', 'source_order': source_order, 'label': None,
            'width': width, 'height': height, 'size_bytes': path.stat().st_size, 'sha256': digest}


def figure_index(workspace: Path, images: tuple[ImageResult, ...]) -> dict:
    return {'images': [asdict(image) | _figure_record(workspace, image.path, index)
                       for index, image in enumerate(images, 1)]}


def _local_image_links(markdown: str) -> set[str]:
    # Docling's standard install already includes the Marko Markdown parser.
    from marko import Markdown
    from marko.ext.gfm import GFM
    from marko.inline import Image

    links = set()
    pending = [Markdown(extensions=[GFM]).parse(markdown)]
    while pending:
        node = pending.pop()
        if isinstance(node, Image):
            try:
                uri = urlsplit(node.dest)
                if uri.scheme or uri.netloc:
                    continue
                parts = [unquote(part, errors='strict') for part in uri.path.removeprefix('./').split('/')]
            except (UnicodeError, ValueError) as exc:
                raise ExportIntegrityError('Saved Markdown contains an invalid local image link.') from exc
            if (len(parts) < 2 or parts[0] != 'images' or uri.query or uri.fragment
                    or any(not part or part == '.' or '..' in part or '/' in part or '\\' in part or '\0' in part
                           for part in parts)):
                raise ExportIntegrityError('Saved Markdown contains an invalid local image link.')
            links.add('/'.join(parts))
        children = getattr(node, 'children', None)
        if isinstance(children, list):
            pending.extend(children)
    return links


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=False).encode('utf-8')


def build_archive(workspace: Path, status: dict, app_version: str) -> BinaryIO:
    with (workspace / 'output.md').open('rb') as stream:
        manual = stream.read()
        saved_mtime = os.fstat(stream.fileno()).st_mtime_ns
    references = _local_image_links(manual.decode('utf-8'))
    assets = json.loads((workspace / 'image-index.json').read_text(encoding='utf-8'))['images']
    indexed = {item['path']: item for item in assets}
    if references - indexed.keys():
        raise ExportIntegrityError('Saved Markdown links to a PNG missing from the exported image index.')

    files = {}
    figures = []
    for index, item in enumerate(assets, 1):
        name = item['path']
        if name not in references:
            continue
        files[name] = _image_path(workspace, name)
        facts = _figure_record(workspace, name, index)
        if item.get('sha256') is not None and item['sha256'] != facts['sha256']:
            raise ExportIntegrityError('An extracted PNG failed its checksum check.')
        figures.append(item | facts)

    original = workspace / 'metadata.json'
    conversion = json.loads(original.read_text(encoding='utf-8')) if original.exists() else {}
    original_sha = conversion.get('original_markdown_sha256')
    manual_sha = hashlib.sha256(manual).hexdigest()
    converted_mtime = conversion.get('converted_markdown_mtime_ns')
    saved_at = (datetime.fromtimestamp(saved_mtime / 1_000_000_000, tz=timezone.utc).isoformat()
                if converted_mtime is not None and saved_mtime != converted_mtime else None)
    index_content = _json_bytes({'images': figures})
    pages = json.loads((workspace / 'pages.json').read_text(encoding='utf-8'))
    metadata = {
        'source_filename': status['filename'],
        'source_pdf_sha256': status.get('source_pdf_sha256') or file_sha256(workspace / 'input.pdf'),
        'created_at': status.get('created_at'),
        'converted_at': status.get('updated_at'),
        'saved_at': saved_at,
        'engine': status['engine'],
        'engine_version': status.get('engine_version'),
        'adapter_version': status.get('adapter_version'),
        'engine_options': status.get('engine_options', {}),
        'application_version': app_version,
        'warnings': status.get('warnings', []),
        'page_count': len(pages['pages']),
        'image_count': len(figures),
        'user_edited': manual_sha != original_sha if original_sha is not None else None,
        'page_anchors_verified': bool(pages.get('page_anchors')) and manual_sha == original_sha,
        'artifact_checksums': {'manual.md': manual_sha, 'image-index.json': hashlib.sha256(index_content).hexdigest()}
        | {image['path']: image['sha256'] for image in figures},
    }
    contents = {'manual.md': manual, 'image-index.json': index_content, 'metadata.json': _json_bytes(metadata)}

    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b', dir=workspace)
    try:
        with zipfile.ZipFile(output, 'w') as archive:
            for name in sorted(files.keys() | contents.keys()):
                info = zipfile.ZipInfo(name)
                if name in files:
                    with files[name].open('rb') as source, archive.open(info, 'w') as destination:
                        shutil.copyfileobj(source, destination)
                else:
                    archive.writestr(info, contents[name], compress_type=zipfile.ZIP_DEFLATED)
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
