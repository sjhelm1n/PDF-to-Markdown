import errno
from tempfile import SpooledTemporaryFile

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .jobs import STORAGE_MESSAGE, JobStore, QueueFull
from .settings import MIB, Settings


class RequestLimits:
    def __init__(self, app: ASGIApp, settings: Settings, jobs: JobStore) -> None:
        self.app = app
        self.settings = settings
        self.jobs = jobs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        path = scope['path']
        root = scope.get('root_path', '').rstrip('/')
        if root and path.startswith(root + '/'):
            path = path[len(root):]
        upload = scope['method'] == 'POST' and path == '/api/jobs'
        edit = scope['method'] == 'PUT' and path.startswith('/api/jobs/') and path.endswith('/markdown')
        if not (upload or edit):
            await self.app(scope, receive, send)
            return

        limit = self.settings.upload_request_bytes if upload else self.settings.edit_request_bytes
        too_large = JSONResponse({'detail': 'Request body exceeds the size limit.'}, status_code=413)
        declared_length = Headers(scope=scope).get('content-length', '')
        if declared_length.isascii() and declared_length.isdecimal():
            digits = declared_length.lstrip('0')
            if len(digits) > len(str(limit)) or int(digits or '0') > limit:
                await too_large(scope, receive, send)
                return

        admission = None
        if upload:
            try:
                admission = self.jobs.admit()
            except QueueFull:
                response = JSONResponse({'detail': 'The conversion queue is full. Try again shortly.'},
                                        status_code=429, headers={'Retry-After': '5'})
                await response(scope, receive, send)
                return
            scope.setdefault('state', {})['admission'] = admission

        try:
            # Count the entire body before handing any bytes to a parser. The
            # spool bounds memory and is closed on rejection or disconnect.
            with SpooledTemporaryFile(max_size=MIB) as body:
                size = 0
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    chunk = message.get('body', b'')
                    size += len(chunk)
                    if size > limit:
                        await too_large(scope, receive, send)
                        return
                    if chunk:
                        try:
                            await run_in_threadpool(body.write, chunk)
                        except OSError as exc:
                            if exc.errno != errno.ENOSPC:
                                raise
                            full = JSONResponse({'detail': STORAGE_MESSAGE}, status_code=507)
                            await full(scope, receive, send)
                            return
                    if not message.get('more_body', False):
                        break

                body.seek(0)
                sent_final_chunk = False

                async def replay() -> Message:
                    nonlocal size, sent_final_chunk
                    if sent_final_chunk:
                        return await receive()
                    chunk = await run_in_threadpool(body.read, 64 * 1024)
                    size -= len(chunk)
                    sent_final_chunk = size == 0
                    return {'type': 'http.request', 'body': chunk, 'more_body': not sent_final_chunk}

                await self.app(scope, replay, send)
        finally:
            if admission is not None and not admission.submitted:
                self.jobs.release(admission)
