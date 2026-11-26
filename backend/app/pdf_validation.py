from pathlib import Path
from threading import Lock


class InvalidPdf(ValueError):
    pass


_pdfium_lock = Lock()


def validate_pdf(path: Path, max_pages: int) -> int:
    from pypdfium2 import PdfDocument, PdfiumError
    from pypdfium2.raw import FPDF_ERR_PASSWORD, FPDF_ERR_SECURITY, FPDF_GetSecurityHandlerRevision

    try:
        # PDFium calls are serialized. This preflight opens the document without
        # rendering pages or loading any inference models.
        with _pdfium_lock, PdfDocument(path) as document:
            if FPDF_GetSecurityHandlerRevision(document) != -1:
                raise InvalidPdf('Encrypted PDFs are not supported.')
            pages = len(document)
    except PdfiumError as exc:
        if exc.err_code in (FPDF_ERR_PASSWORD, FPDF_ERR_SECURITY):
            raise InvalidPdf('Encrypted PDFs or unsupported PDF security settings are not supported.') from exc
        raise InvalidPdf('The uploaded PDF is malformed or unsupported.') from exc

    if pages <= 0:
        raise InvalidPdf('The uploaded PDF has no readable pages.')
    if pages > max_pages:
        raise InvalidPdf(f'PDFs may contain at most {max_pages} pages.')
    return pages
