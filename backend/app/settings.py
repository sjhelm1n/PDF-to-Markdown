import os
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024


def _integer(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f'{name} must be an integer of at least {minimum}') from exc
    if value < minimum:
        raise ValueError(f'{name} must be an integer of at least {minimum}')
    return value


@dataclass(frozen=True)
class Settings:
    root: Path
    pdf_bytes: int = 100 * MIB
    pdf_pages: int = 25
    upload_request_bytes: int = 101 * MIB
    edit_request_bytes: int = 24 * MIB
    pending_jobs: int = 2
    storage_bytes: int = 2 * 1024 * MIB
    retention_seconds: int = 7 * 24 * 60 * 60
    cleanup_interval_seconds: int = 60

    @classmethod
    def from_env(cls) -> 'Settings':
        if _integer('PDF2MD_CONVERSION_WORKERS', 1) != 1:
            raise ValueError('PDF2MD_CONVERSION_WORKERS must be 1')
        pdf_bytes = _integer('MAX_PDF_BYTES', 100 * MIB)
        return cls(
            root=Path(os.getenv('APP_DATA', '/data')).resolve(),
            pdf_bytes=pdf_bytes,
            pdf_pages=_integer('PDF2MD_MAX_PDF_PAGES', 25),
            upload_request_bytes=_integer('PDF2MD_MAX_UPLOAD_REQUEST_BYTES', pdf_bytes + MIB),
            edit_request_bytes=_integer('PDF2MD_MAX_EDIT_REQUEST_BYTES', 24 * MIB),
            pending_jobs=_integer('PDF2MD_MAX_PENDING_JOBS', 2, 0),
            storage_bytes=_integer('PDF2MD_MAX_STORAGE_BYTES', 2 * 1024 * MIB),
            retention_seconds=_integer('PDF2MD_RETENTION_SECONDS', 7 * 24 * 60 * 60),
            cleanup_interval_seconds=_integer('PDF2MD_CLEANUP_INTERVAL_SECONDS', 60),
        )
