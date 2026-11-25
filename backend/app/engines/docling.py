from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import local
from typing import TYPE_CHECKING

from ..filenames import source_image_prefix
from .base import (
    ConversionEngine, ConversionResult, EngineCapabilities, EngineConfig, EngineError, ImageResult, PageResult,
)

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument
    from docling_core.types.doc.labels import DocItemLabel


def _load_runtime():
    # Keep heavyweight imports out of API startup. Import the full runtime so
    # a missing native or transitive dependency also disables the option.
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import CaptionPlacement, ImageRefMode
    except ImportError as exc:
        raise EngineError('engine_unavailable', 'Docling and its runtime dependencies must be installed.') from exc
    return InputFormat, PdfPipelineOptions, DocumentConverter, PdfFormatOption, ImageRefMode, CaptionPlacement


def _runtime_version() -> str | None:
    try:
        return version('docling')
    except PackageNotFoundError:
        return None


def _exclude_running_page_text(document: 'DoclingDocument') -> set['DocItemLabel']:
    from docling_core.types.doc import ContentLayer
    from docling_core.types.doc.document import DOCUMENT_TOKENS_EXPORT_LABELS
    from docling_core.types.doc.labels import DocItemLabel

    margin_roles = {DocItemLabel.PAGE_HEADER: 'top', DocItemLabel.PAGE_FOOTER: 'bottom'}
    plain_labels = {DocItemLabel.TEXT, DocItemLabel.PARAGRAPH}
    labeled_positions: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    candidates = []

    for item in document.texts:
        if len(item.prov) != 1 or (item.label not in margin_roles and item.label not in plain_labels):
            continue
        provenance = item.prov[0]
        page = document.pages.get(provenance.page_no)
        if page is None or page.size.height <= 0:
            continue
        height = page.size.height
        box = provenance.bbox.to_top_left_origin(height)
        if 0 <= box.t < box.b <= 0.08 * height:
            margin = 'top'
        elif 0.92 * height <= box.t < box.b <= height:
            margin = 'bottom'
        else:
            continue

        text = ' '.join(item.text.split())
        if not text:
            continue
        key = (margin, text)
        position = (box.t + box.b) / (2 * height)
        if margin_roles.get(item.label) == margin:
            labeled_positions[key].append((provenance.page_no, position))
        elif (item.label in plain_labels and item.content_layer == ContentLayer.BODY
              and not item.children and item.hyperlink is None
              and item.parent is not None and item.parent.cref == document.body.self_ref):
            candidates.append((item, key, provenance.page_no, position))

    for item, key, page_no, position in candidates:
        counter = f'page {page_no} of {len(document.pages)}'
        matches_labeled = any(
            other_page != page_no and abs(other_position - position) <= 0.02
            for other_page, other_position in labeled_positions.get(key, ())
        )
        if key[1].casefold() == counter or matches_labeled:
            item.content_layer = ContentLayer.FURNITURE

    return DOCUMENT_TOKENS_EXPORT_LABELS - margin_roles.keys()


def _figure_result(path: Path, item, document: 'DoclingDocument') -> ImageResult:
    provenance = getattr(item, 'prov', ())
    page_numbers = {entry.page_no for entry in provenance}
    if len(page_numbers) != 1:
        return ImageResult(path.as_posix())

    page_no = next(iter(page_numbers))
    page = document.pages.get(page_no)
    if page is None:
        return ImageResult(path.as_posix())

    bbox = None
    if len(provenance) == 1:
        box = provenance[0].bbox.to_top_left_origin(page.size.height)
        if 0 <= box.l < box.r <= page.size.width and 0 <= box.t < box.b <= page.size.height:
            bbox = (box.l, box.t, box.r, box.b)

    captions = []
    for reference in getattr(item, 'captions', ()):
        caption = reference.resolve(document)
        caption_provenance = getattr(caption, 'prov', ())
        text = getattr(caption, 'text', '')
        if text.strip() and {entry.page_no for entry in caption_provenance} == {page_no}:
            captions.append(text)

    return ImageResult(path.as_posix(), page_no, '\n'.join(captions) or None, bbox)


def _normalize_images(document: 'DoclingDocument', workspace: Path, prefix: str) -> tuple[ImageResult, ...]:
    image_dir = Path('images')
    exported = {path.relative_to(workspace) for path in (workspace / image_dir).glob('*.png')}
    renamed: dict[Path, Path] = {}
    figures: dict[Path, ImageResult] = {}

    # Update picture references before serializing either Markdown view, so
    # figure links follow the assets without rewriting source text.
    for item, _ in document.iterate_items():
        image = getattr(item, 'image', None)
        if image is None or not isinstance(image.uri, Path) or image.uri not in exported:
            continue
        source = image.uri
        if source not in renamed:
            renamed[source] = image_dir / f'{prefix}-fig-{len(renamed) + 1:06}.png'
            figures[source] = _figure_result(renamed[source], item, document)
        elif figures[source] != _figure_result(renamed[source], item, document):
            figures[source] = ImageResult(renamed[source].as_posix())
        image.uri = renamed[source]

    if len(renamed) != len(exported):
        raise EngineError('conversion_failed', 'Docling exported a PNG without a matching figure reference.')
    for source, destination in renamed.items():
        (workspace / source).rename(workspace / destination)

    return tuple(figures.values())


class DoclingAdapter(ConversionEngine):
    id = 'docling'
    name = 'Docling'
    adapter_version = '7'
    capabilities = EngineCapabilities(markdown=True, page_markdown=True, png_images=True, figure_provenance=True)

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._converters = local()

    def probe(self) -> str | None:
        _load_runtime()
        return _runtime_version()

    def convert(self, pdf_path: Path, workspace: Path, *, source_filename: str | None = None) -> ConversionResult:
        input_format, pipeline_options, converter_class, pdf_option, image_ref_mode, caption_placement = _load_runtime()
        converter = getattr(self._converters, 'converter', None)
        if converter is None:
            options = pipeline_options(
                generate_picture_images=True,
                images_scale=float(self.config.options.get('images_scale', 3.0)),
            )
            # Prefer original pixels when Docling can safely match an embedded image.
            if hasattr(options, 'use_embedded_picture_images'):
                options.use_embedded_picture_images = True
            options.enable_remote_services = False
            converter = converter_class(format_options={input_format.PDF: pdf_option(pipeline_options=options)})
        try:
            document = converter.convert(str(pdf_path)).document
        except Exception as exc:
            self._converters.converter = None
            if isinstance(exc, (ImportError, FileNotFoundError)):
                raise EngineError(
                    'engine_unavailable', 'Docling could not load required local conversion resources.',
                ) from exc
            raise

        page_count = len(document.pages)
        if not page_count:
            raise EngineError('conversion_failed', 'Docling returned a document without pages.')
        self._converters.converter = converter
        image_mode = image_ref_mode.REFERENCED
        # Prepare references once, as Docling's save_as_markdown does. Preparing
        # each page separately would restart Docling's image numbering.
        referenced_document = document._make_copy_with_refmode(
            artifacts_dir=workspace / 'images', image_mode=image_mode,
            page_no=None, reference_path=workspace,
        )
        prefix = source_image_prefix(source_filename if source_filename is not None else pdf_path.name)
        images = _normalize_images(referenced_document, workspace, prefix)
        markdown_options = {
            'image_mode': image_mode,
            'caption_placement': caption_placement.LAYOUT,
            'labels': _exclude_running_page_text(referenced_document),
        }
        return ConversionResult(
            markdown=referenced_document.export_to_markdown(**markdown_options),
            pages=tuple(
                PageResult(page, referenced_document.export_to_markdown(page_no=page, **markdown_options))
                for page in sorted(document.pages)
            ),
            images=images,
            engine_version=_runtime_version(),
        )
