import json
import logging
import os
import shutil
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from uuid import UUID, uuid4

from .settings import Settings

logger = logging.getLogger(__name__)
TERMINAL = {'complete', 'failed', 'interrupted'}
STORAGE_MESSAGE = 'Job storage is full. Try again after expired jobs are cleaned up.'


class QueueFull(Exception):
    pass


class StorageFull(Exception):
    pass


@dataclass
class Admission:
    job_id: str
    submitted: bool = False


def is_job_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def read_status(path: Path) -> dict:
    return read_json(path / 'status.json')


def json_bytes(value: dict) -> bytes:
    return json.dumps(value, indent=2).encode('utf-8')


class JobStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.root
        self.lock = RLock()
        self.admission_lock = Lock()
        self.active: set[str] = set()
        self.readers: Counter[str] = Counter()
        self.reserved_files: dict[Path, int] = {}

    def admit(self) -> Admission:
        with self.admission_lock:
            if len(self.active) >= self.settings.pending_jobs + 1:
                raise QueueFull
            admission = Admission(str(uuid4()))
            self.active.add(admission.job_id)
            return admission

    def release(self, admission: Admission) -> None:
        with self.admission_lock:
            self.active.discard(admission.job_id)

    @contextmanager
    def open_job(self, job_id: str) -> Iterator[Path]:
        path = self.root / job_id
        with self.lock:
            if path.is_symlink() or not (path / 'status.json').is_file():
                raise FileNotFoundError(job_id)
            self.readers[job_id] += 1
        try:
            yield path
        finally:
            with self.lock:
                self.readers[job_id] -= 1
                if self.readers[job_id] == 0:
                    del self.readers[job_id]

    def _usage(self) -> int:
        total = sum(self.reserved_files.values())
        for directory, _, names in os.walk(self.root):
            for name in names:
                path = Path(directory, name)
                if path not in self.reserved_files and not path.is_symlink():
                    try:
                        total += path.stat().st_size
                    except FileNotFoundError:
                        # An adapter may rename a PNG while the walk is in progress.
                        continue
        return total

    def _require_space(self, size: int) -> None:
        if self._usage() + size > self.settings.storage_bytes:
            self.cleanup()
            if self._usage() + size > self.settings.storage_bytes:
                raise StorageFull

    @contextmanager
    def reserve_file(self, path: Path, size: int) -> Iterator[None]:
        with self.lock:
            self._require_space(size)
            self.reserved_files[path] = size
        try:
            yield
        finally:
            with self.lock:
                self.reserved_files.pop(path)

    def write_file(self, path: Path, content: bytes, *, charge: bool = True) -> None:
        temporary = path.with_name(f'write-{uuid4()}.tmp')
        with self.lock:
            if charge:
                self._require_space(len(content))
            try:
                temporary.write_bytes(content)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def status(self, path: Path, state: str, error: str | None = None, **details: object) -> None:
        obj = read_status(path)
        obj.update(state=state, error=error, updated_at=utc_now(), **details)
        # Small operational status records must remain writable when a job
        # fails for lack of quota. Successful publication is quota-checked.
        self.write_file(path / 'status.json', json_bytes(obj), charge=state == 'complete')

    def discard_outputs(self, path: Path) -> None:
        with self.lock:
            for child in path.iterdir():
                if child.name not in ('input.pdf', 'status.json'):
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()

    def _directories(self) -> Iterator[Path]:
        for path in self.root.iterdir():
            if is_job_id(path.name) and not path.is_symlink() and path.is_dir():
                yield path

    def recover(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock:
            for path in self._directories():
                try:
                    state = read_status(path).get('state')
                    if state in ('queued', 'running'):
                        self.discard_outputs(path)
                        self.status(path, 'interrupted', 'Conversion interrupted by service restart')
                    for temporary in path.glob('write-*.tmp'):
                        temporary.unlink()
                except FileNotFoundError:
                    # A crash before publication leaves an uncommitted upload.
                    if not (path / 'status.json').exists():
                        shutil.rmtree(path, ignore_errors=True)
                except (OSError, ValueError, AttributeError):
                    logger.warning('Unable to recover job %s', path.name, exc_info=True)
        self.cleanup()

    def cleanup(self) -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - self.settings.retention_seconds
        with self.lock:
            with self.admission_lock:
                active = self.active.copy()
            for path in self._directories():
                if path.name in active or path.name in self.readers:
                    continue
                try:
                    status_path = path / 'status.json'
                    if status_path.stat().st_mtime <= cutoff and read_status(path).get('state') in TERMINAL:
                        shutil.rmtree(path)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, AttributeError):
                    logger.warning('Unable to clean job %s', path.name, exc_info=True)
