import hashlib
import importlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.engines.base import (
    ConversionEngine, ConversionResult, EngineCapabilities, EngineConfig, EngineError, ImageResult, PageResult,
)
from app.engines.nemotron import NemotronAdapter
from app.engines.registry import EngineRegistry

PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02'
    '0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082'
)


class FixtureAdapter(ConversionEngine):
    id = 'fixture'
    name = 'Fixture converter'
    adapter_version = 'test'
    capabilities = EngineCapabilities(markdown=True, page_markdown=True, png_images=True)

    def probe(self) -> str | None:
        return 'test-runtime'

    def convert(self, pdf_path: Path, workspace: Path, *, source_filename: str | None = None) -> ConversionResult:
        contents = pdf_path.read_bytes()
        if b'typed error' in contents:
            raise EngineError('engine_unavailable', 'Required local model files are missing.')
        if b'unexpected error' in contents:
            raise RuntimeError('/private/runtime-path')
        (workspace / 'images').mkdir()
        (workspace / 'images/figure.png').write_bytes(PNG)
        return ConversionResult(
            '# Fixture\n\n![Figure](images/figure.png)\n',
            pages=(PageResult(1, '# Fixture'),),
            images=(ImageResult('images/figure.png'),),
            engine_version=self.probe(),
        )


class EngineAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        with patch.dict(os.environ, {'APP_DATA': str(data), 'PDF2MD_ENGINES': ''}):
            main = importlib.import_module('app.main')
        cls.main = main
        cls.enterClassContext(patch.object(main, 'ROOT', data))
        cls.enterClassContext(patch.object(main, 'MAX_BYTES', 256))
        cls.enterClassContext(patch.object(main, 'ENGINES', EngineRegistry([
            FixtureAdapter(EngineConfig(options={'profile': 'fixture'})),
            NemotronAdapter(EngineConfig()),
        ])))
        (data / 'interrupted').mkdir()
        interrupted = data / 'interrupted/status.json'
        interrupted.write_text('{"state":"running"}', encoding='utf-8')

        cls.client = cls.enterClassContext(TestClient(main.app))
        cls.recovered = json.loads(interrupted.read_text())['state']

    def request(self, method, path, body=None, headers=None):
        response = self.client.request(method, path, content=body, headers=headers)
        return response.status_code, response.content

    def upload(self, engine='fixture', contents=b'%PDF-1.4\nfixture', filename='manual.pdf'):
        response = self.client.post('/api/jobs', params={'engine': engine}, files={
            'file': (filename, contents, 'application/pdf'),
        })
        return response.status_code, response.content

    def wait_for_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            code, data = self.request('GET', f'/api/jobs/{job_id}')
            self.assertEqual(code, 200)
            job = json.loads(data)
            if job['state'] in {'complete', 'failed', 'interrupted'}:
                return job
            time.sleep(0.01)
        self.fail('Conversion job never reached a terminal state')

    def test_discovery_and_health(self):
        self.assertEqual(self.recovered, 'interrupted')
        code, data = self.request('GET', '/api/engines')
        self.assertEqual(code, 200)
        doc, placeholder = json.loads(data)['engines']
        self.assertEqual(doc['id'], 'fixture')
        self.assertTrue(doc['available'])
        self.assertEqual(doc['engine_version'], 'test-runtime')
        self.assertTrue(doc['capabilities']['png_images'])
        self.assertFalse(doc['capabilities']['figure_provenance'])
        self.assertFalse(placeholder['available'])
        self.assertIn('later phase', placeholder['unavailable_reason'])
        self.assertFalse(json.loads(self.request('GET', '/api/health')[1])['engines'][1]['available'])

    def test_conversion_worker_setting(self):
        with patch.dict(os.environ, clear=True):
            self.assertEqual(self.main.conversion_worker_count(), 1)
        with patch.dict(os.environ, {'PDF2MD_CONVERSION_WORKERS': '2'}):
            self.assertEqual(self.main.conversion_worker_count(), 2)
        for value in ('0', '-1', 'invalid'):
            with self.subTest(value=value), patch.dict(os.environ, {'PDF2MD_CONVERSION_WORKERS': value}):
                with self.assertRaisesRegex(ValueError, 'PDF2MD_CONVERSION_WORKERS'):
                    self.main.conversion_worker_count()

    def test_two_configured_workers_process_separate_jobs(self):
        barrier = threading.Barrier(2, timeout=3)
        convert = FixtureAdapter.convert
        filenames = {}

        def parallel_convert(adapter, pdf_path, workspace, *, source_filename=None):
            filenames[workspace.name] = source_filename
            barrier.wait()
            return convert(adapter, pdf_path, workspace, source_filename=source_filename)

        with (
            patch.dict(os.environ, {'PDF2MD_CONVERSION_WORKERS': '2'}),
            ThreadPoolExecutor(max_workers=self.main.conversion_worker_count()) as pool,
            patch.object(self.main, 'POOL', pool),
            patch.object(FixtureAdapter, 'convert', parallel_convert),
        ):
            jobs = []
            for filename in ('archive/first.pdf', r'archive\second.pdf'):
                code, payload = self.upload(filename=filename)
                self.assertEqual(code, 202)
                jobs.append(json.loads(payload)['id'])
            self.assertNotEqual(*jobs)
            for job_id, filename in zip(jobs, ('first.pdf', 'second.pdf')):
                job = self.wait_for_job(job_id)
                self.assertEqual((job['state'], job['filename']), ('complete', filename))
                self.assertEqual(filenames[job_id], filename)

    def test_unsupported_engines_and_invalid_uploads(self):
        for engine, expected_status, expected_code in [
            ('unknown', 422, 'unknown_engine'), ('nemotron', 503, 'engine_unavailable'),
        ]:
            with self.subTest(engine=engine):
                code, data = self.upload(engine)
                self.assertEqual(code, expected_status)
                self.assertEqual(json.loads(data)['detail']['code'], expected_code)
        self.assertEqual(self.upload(filename='wrong.txt')[0], 422)
        self.assertEqual(self.upload(contents=b'not a PDF')[0], 422)
        self.assertEqual(self.upload(contents=b'%PDF-' + b'x' * 256)[0], 413)

    def test_selected_adapter_converts_and_preserves_edit_and_zip_flow(self):
        code, payload = self.upload()
        self.assertEqual(code, 202)
        job_id = json.loads(payload)['id']
        job = self.wait_for_job(job_id)
        self.assertEqual(job['state'], 'complete')
        self.assertEqual(job['engine'], 'fixture')
        self.assertEqual(job['engine_options'], {'profile': 'fixture'})
        self.assertEqual(job['pages'], ['# Fixture'])
        self.assertIsNone(job['images'][0]['source_page'])
        self.assertEqual(self.request('GET', f'/api/jobs/{job_id}/pdf'), (200, b'%PDF-1.4\nfixture'))
        self.assertEqual(self.request('GET', f'/api/jobs/{job_id}/assets/figure.png'), (200, PNG))
        edited = job['markdown'] + '\nEdited.\n'
        self.assertEqual(self.request(
            'PUT', f'/api/jobs/{job_id}/markdown', json.dumps({'markdown': edited}),
            {'Content-Type': 'application/json'},
        )[0], 200)
        self.assertEqual(self.wait_for_job(job_id)['markdown'], edited)
        code, archive = self.request('GET', f'/api/jobs/{job_id}/download')
        self.assertEqual(code, 200)
        with zipfile.ZipFile(io.BytesIO(archive)) as exported:
            self.assertEqual(exported.namelist(), ['image-index.json', 'images/figure.png', 'manual.md', 'metadata.json'])
            self.assertEqual(exported.read('manual.md').decode(), edited)
            self.assertEqual(exported.read('images/figure.png'), PNG)
            self.assertEqual(json.loads(exported.read('image-index.json'))['images'], job['images'])
            self.assertIsNone(exported.testzip())
            metadata = json.loads(exported.read('metadata.json'))
            self.assertTrue(metadata['user_edited'])
            self.assertFalse(metadata['page_anchors_verified'])
            self.assertIsNotNone(metadata['saved_at'])
            self.assertEqual(metadata['source_pdf_sha256'], hashlib.sha256(b'%PDF-1.4\nfixture').hexdigest())
            self.assertEqual(metadata['engine_options'], {'profile': 'fixture'})
            for name, expected in metadata['artifact_checksums'].items():
                self.assertEqual(hashlib.sha256(exported.read(name)).hexdigest(), expected)

    def test_export_checks_rendered_image_links_and_png_bytes(self):
        _, payload = self.upload()
        job_id = json.loads(payload)['id']
        job = self.wait_for_job(job_id)
        self.assertEqual(job['images'][0]['id'], 'fig-000001')
        with self.subTest('fenced literal and reference-style image'):
            edited = '![Figure][ref]\n\n[ref]: images/figure.png\n\n```\n![literal](images/fake.png)\n```\n'
            self.client.put(f'/api/jobs/{job_id}/markdown', json={'markdown': edited}).raise_for_status()
            self.client.get(f'/api/jobs/{job_id}/download').raise_for_status()
        for unsafe in ('images/missing.png', 'images/%2e%2e/escape.png', 'images/a%2fb.png'):
            with self.subTest(link=unsafe):
                self.client.put(f'/api/jobs/{job_id}/markdown', json={'markdown': f'![Figure]({unsafe})'}).raise_for_status()
                self.assertEqual(self.client.get(f'/api/jobs/{job_id}/download').status_code, 409)
        self.client.put(f'/api/jobs/{job_id}/markdown', json={'markdown': job['markdown']}).raise_for_status()
        (self.main.ROOT / job_id / 'images/figure.png').write_bytes(PNG + b'changed')
        response = self.client.get(f'/api/jobs/{job_id}/download')
        self.assertEqual(response.status_code, 409)
        self.assertIn('checksum', response.json()['detail'])

    def test_worker_reports_safe_typed_and_unexpected_failures(self):
        for failure, expected_code, message in [
            (b'typed error', 'engine_unavailable', 'Required local model files are missing.'),
            (b'unexpected error', 'conversion_failed', 'Conversion failed. See server logs.'),
        ]:
            with self.subTest(failure=failure), self.assertLogs('app.main', level='WARNING'):
                code, payload = self.upload(contents=b'%PDF-1.4\n' + failure)
                self.assertEqual(code, 202)
                job_id = json.loads(payload)['id']
                job = self.wait_for_job(job_id)
                self.assertEqual((job['state'], job['error_code'], job['error']), ('failed', expected_code, message))
                self.assertEqual(self.request('GET', f'/api/jobs/{job_id}/download')[0], 409)
