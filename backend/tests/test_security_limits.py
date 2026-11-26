import asyncio
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.request_limits import RequestLimits

from test_engine_api import ApiTestCase, FixtureAdapter, PNG, pdf_bytes


class SecurityLimitTests(ApiTestCase):
    def submit(self, **values):
        status, body = self.upload(**values)
        self.assertEqual(status, 202, body.decode())
        return json.loads(body)['id']

    def seed_job(self, state):
        path = self.settings.root / str(uuid4())
        path.mkdir()
        status = path / 'status.json'
        status.write_text(json.dumps({'state': state}), encoding='utf-8')
        expired = time.time() - 3600
        os.utime(status, (expired, expired))
        return path

    def test_page_boundary_uses_real_pdf_preflight_before_conversion(self):
        self.start()
        with patch.object(FixtureAdapter, 'convert', autospec=True, return_value=None) as convert:
            status, body = self.upload(contents=pdf_bytes(26))
            self.assertEqual(status, 422)
            self.assertIn('at most 25 pages', json.loads(body)['detail'])
            convert.assert_not_called()
        job = self.wait_for_job(self.submit(contents=pdf_bytes(25)))
        self.assertEqual((job['state'], job['source_page_count']), ('complete', 25))

    def test_custom_page_limit_and_invalid_pdfs(self):
        self.start(pdf_pages=1)
        status, body = self.upload(contents=pdf_bytes(2))
        self.assertEqual(status, 422)
        self.assertIn('at most 1 pages', json.loads(body)['detail'])
        inputs = [
            (b'%PDF-1.4\ninvalid document', 'malformed or unsupported'),
            (b'Not a PDF', 'not a PDF'),
            ((Path(__file__).parent / 'fixtures/encrypted.pdf').read_bytes(), 'Encrypted PDFs'),
        ]
        with patch.object(FixtureAdapter, 'convert') as convert:
            for content, message in inputs:
                with self.subTest(message=message):
                    status, body = self.upload(contents=content)
                    self.assertEqual(status, 422)
                    self.assertIn(message, json.loads(body)['detail'])
            convert.assert_not_called()
        self.assertEqual(list(self.settings.root.iterdir()), [])

    def test_oversized_requests_return_413_before_fastapi_parsing(self):
        self.start(upload_request_bytes=128, edit_request_bytes=64)
        with (
            patch('starlette.requests.Request.form', side_effect=AssertionError('multipart parser called')),
            patch('starlette.requests.Request.body', side_effect=AssertionError('JSON parser called')),
        ):
            self.assertEqual(self.upload()[0], 413)
            self.assertEqual(self.client.put(f'/api/jobs/{uuid4()}/markdown', json={'markdown': 'x' * 64}).status_code,
                             413)
        self.assertEqual(list(self.settings.root.iterdir()), [])

    def test_stream_size_is_checked_with_no_content_length_or_smaller_length(self):
        self.start(upload_request_bytes=12, edit_request_bytes=12, pending_jobs=0)
        jobs = self.app.state.jobs

        async def check(method, path, headers, *, size, expected):
            responses = []
            chunks = iter((b'a' * 6, b'b' * (size - 6)))

            async def receive():
                chunk = next(chunks)
                return {'type': 'http.request', 'body': chunk, 'more_body': chunk.startswith(b'a')}

            async def send(message):
                responses.append(message)

            async def downstream(scope, receive, send):
                self.assertEqual(size, 12, 'oversized body reached the downstream parser')
                received = await receive()
                self.assertEqual(received['body'], b'aaaaaabbbbbb')
                self.assertFalse(received['more_body'])
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})

            scope = {'type': 'http', 'method': method, 'path': path, 'headers': headers}
            await RequestLimits(downstream, jobs.settings, jobs)(scope, receive, send)
            self.assertEqual(responses[0]['status'], expected)

        for method, path in [('POST', '/api/jobs'), ('PUT', f'/api/jobs/{uuid4()}/markdown')]:
            for headers in ([], [(b'content-length', b'1')]):
                asyncio.run(check(method, path, headers, size=13, expected=413))
                asyncio.run(check(method, path, headers, size=12, expected=200))
        self.assertEqual(jobs.active, set())

    def test_upload_disconnect_releases_admission(self):
        self.start(pending_jobs=0)
        jobs = self.app.state.jobs

        async def disconnect():
            return {'type': 'http.disconnect'}

        async def unexpected(*args):
            self.fail('A disconnected upload should not reach the application or send a response')

        scope = {'type': 'http', 'method': 'POST', 'path': '/api/jobs', 'headers': []}
        asyncio.run(RequestLimits(unexpected, jobs.settings, jobs)(scope, disconnect, unexpected))
        self.assertEqual(jobs.active, set())
        self.assertEqual(self.upload()[0], 202)

    def test_one_running_one_pending_and_cancelled_job_releases_slot(self):
        self.start(pending_jobs=1)
        entered = threading.Event()
        release = threading.Event()
        native_convert = FixtureAdapter.convert
        original_submit = self.app.state.pool.submit
        futures = []

        def blocked(adapter, pdf, workspace, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test did not release the worker')
            return native_convert(adapter, pdf, workspace, **kwargs)

        def capture_submit(*args):
            future = original_submit(*args)
            futures.append(future)
            return future

        with (
            patch.object(FixtureAdapter, 'convert', blocked),
            patch.object(self.app.state.pool, 'submit', side_effect=capture_submit),
        ):
            try:
                first = self.submit()
                self.assertTrue(entered.wait(2))
                second = self.submit()
                self.assertEqual(self.client.get(f'/api/jobs/{first}').json()['state'], 'running')
                self.assertEqual(self.client.get(f'/api/jobs/{second}').json()['state'], 'queued')
                with patch('starlette.requests.Request.form', side_effect=AssertionError('queue parsed body')):
                    self.assertEqual(self.upload()[0], 429)
                self.assertTrue(futures[1].cancel())
                self.assertEqual(self.wait_for_job(second)['state'], 'interrupted')
                third = self.submit()
                self.assertEqual(self.client.get(f'/api/jobs/{third}').json()['state'], 'queued')
            finally:
                release.set()
            self.assertEqual(self.wait_for_job(first)['state'], 'complete')
            self.assertEqual(self.wait_for_job(third)['state'], 'complete')

    def test_failed_conversion_releases_the_only_slot(self):
        self.start(pending_jobs=0)
        with self.assertLogs('app.main', level='ERROR'):
            failing = self.submit(contents=pdf_bytes(comment=b'unexpected error'))
            self.assertEqual(self.wait_for_job(failing)['state'], 'failed')
        self.assertEqual(self.wait_for_job(self.submit())['state'], 'complete')

    def test_retention_skips_active_and_open_jobs(self):
        self.start(retention_seconds=60)
        completed, failed, interrupted, reading = (
            self.seed_job(state) for state in ('complete', 'failed', 'interrupted', 'complete')
        )
        jobs = self.app.state.jobs
        admission = jobs.admit()
        active = self.settings.root / admission.job_id
        active.mkdir()
        status = active / 'status.json'
        status.write_text('{"state":"running"}', encoding='utf-8')
        expired = time.time() - 3600
        os.utime(status, (expired, expired))
        try:
            with jobs.open_job(reading.name):
                jobs.cleanup()
                self.assertTrue(reading.exists())
                self.assertTrue(active.exists())
                self.assertTrue(all(not path.exists() for path in (completed, failed, interrupted)))
            jobs.cleanup()
            self.assertFalse(reading.exists())
            self.assertTrue(active.exists())
        finally:
            jobs.release(admission)

    def test_recovery_reconciles_old_jobs_and_removes_uncommitted_uploads(self):
        running = self.seed_job('running')
        (running / 'output.md').write_text('Incomplete.', encoding='utf-8')
        queued = self.seed_job('queued')
        orphan = self.settings.root / str(uuid4())
        orphan.mkdir()
        (orphan / 'input.pdf').write_bytes(pdf_bytes())
        self.start(pending_jobs=0, retention_seconds=60)
        self.assertEqual(self.wait_for_job(running.name)['state'], 'interrupted')
        self.assertEqual(self.wait_for_job(queued.name)['state'], 'interrupted')
        self.assertFalse((running / 'output.md').exists())
        self.assertFalse(orphan.exists())
        self.assertEqual(self.upload()[0], 202)

    def test_periodic_cleanup_expires_idle_terminal_job(self):
        self.start(retention_seconds=60, cleanup_interval_seconds=1)
        expired = self.seed_job('complete')
        deadline = time.monotonic() + 4
        while expired.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(expired.exists())

    def test_storage_limit_rejects_uploads_and_edits_and_discards_over_budget_output(self):
        self.start(storage_bytes=4096)
        self.assertEqual(self.upload(contents=pdf_bytes(comment=b'x' * 4096))[0], 507)
        self.assertEqual(list(self.settings.root.iterdir()), [])
        normal = FixtureAdapter.convert

        def oversized_conversion(adapter, pdf, workspace, **kwargs):
            result = normal(adapter, pdf, workspace, **kwargs)
            (workspace / 'images/figure.png').write_bytes(PNG + b'x' * 4096)
            return result

        with patch.object(FixtureAdapter, 'convert', oversized_conversion):
            failed = self.wait_for_job(self.submit())
        self.assertEqual((failed['state'], failed['error_code']), ('failed', 'storage_limit'))
        self.assertFalse((self.settings.root / failed['id'] / 'images').exists())

        job = self.wait_for_job(self.submit())
        url = f'/api/jobs/{job["id"]}'
        self.assertEqual(self.client.put(url + '/markdown', json={'markdown': 'x' * 4096}).status_code, 507)
        self.assertEqual(self.client.get(url).json()['markdown'], job['markdown'])
        self.assertEqual(self.client.put(url + '/markdown', json={'markdown': job['markdown'] + '\nEdit'}).status_code,
                         200)
        self.assertEqual(self.client.get(url + '/download').status_code, 200)
