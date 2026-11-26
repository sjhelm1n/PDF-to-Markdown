import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.engines.base import (
    ConversionEngine, ConversionResult, EngineCapabilities, EngineConfig, EngineError, ImageResult, PageResult,
)
from app.engines.nemotron import NemotronAdapter
from app.engines.registry import EngineRegistry
from app.main import create_app
from app.settings import Settings

PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02'
    '0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082'
)


def pdf_bytes(pages=1, comment=b''):
    kids = b' '.join(f'{index + 3} 0 R'.encode() for index in range(pages))
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Count %d /Kids [%s] >>' % (pages, kids)]
    objects += [b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>'] * pages
    contents = b'%PDF-1.4\n%' + comment + b'\n'
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(contents))
        contents += b'%d 0 obj\n%s\nendobj\n' % (number, obj)
    xref = len(contents)
    contents += b'xref\n0 %d\n0000000000 65535 f \n' % len(offsets)
    contents += b''.join(b'%010d 00000 n \n' % offset for offset in offsets[1:])
    return contents + b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(offsets), xref)


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


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(Path(self.enterContext(tempfile.TemporaryDirectory())), pdf_bytes=16 * 1024)

    def start(self, **limits):
        self.app = create_app(replace(self.settings, **limits), EngineRegistry([
            FixtureAdapter(EngineConfig(options={'profile': 'fixture'})),
            NemotronAdapter(EngineConfig()),
        ]))
        self.client = self.enterContext(TestClient(self.app))

    def request(self, method, path, body=None, headers=None):
        response = self.client.request(method, path, content=body, headers=headers)
        return response.status_code, response.content

    def upload(self, engine='fixture', contents=None, filename='manual.pdf'):
        response = self.client.post('/api/jobs', params={'engine': engine}, files={
            'file': (filename, pdf_bytes() if contents is None else contents, 'application/pdf'),
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


class EngineAPITests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.start()

    def test_discovery_and_health(self):
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
            self.assertEqual(Settings.from_env().pdf_pages, 25)
        for value in ('0', '-1', '2', 'invalid'):
            with self.subTest(value=value), patch.dict(os.environ, {'PDF2MD_CONVERSION_WORKERS': value}):
                with self.assertRaisesRegex(ValueError, 'PDF2MD_CONVERSION_WORKERS'):
                    Settings.from_env()

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
        self.assertEqual(self.upload(contents=b'%PDF-' + b'x' * self.settings.pdf_bytes)[0], 413)

    def test_selected_adapter_converts_and_preserves_edit_and_zip_flow(self):
        code, payload = self.upload(filename=r'archive\manual.pdf')
        self.assertEqual(code, 202)
        job_id = json.loads(payload)['id']
        job = self.wait_for_job(job_id)
        self.assertEqual(job['state'], 'complete')
        self.assertEqual(job['engine'], 'fixture')
        self.assertEqual(job['engine_options'], {'profile': 'fixture'})
        self.assertEqual(job['pages'], ['# Fixture'])
        self.assertIsNone(job['images'][0]['source_page'])
        self.assertEqual(job['filename'], 'manual.pdf')
        self.assertEqual(self.request('GET', f'/api/jobs/{job_id}/pdf'), (200, pdf_bytes()))
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
            self.assertEqual(metadata['source_pdf_sha256'], hashlib.sha256(pdf_bytes()).hexdigest())
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
        (self.settings.root / job_id / 'images/figure.png').write_bytes(PNG + b'changed')
        response = self.client.get(f'/api/jobs/{job_id}/download')
        self.assertEqual(response.status_code, 409)
        self.assertIn('checksum', response.json()['detail'])

    def test_worker_reports_safe_typed_and_unexpected_failures(self):
        for failure, expected_code, message in [
            (b'typed error', 'engine_unavailable', 'Required local model files are missing.'),
            (b'unexpected error', 'conversion_failed', 'Conversion failed. See server logs.'),
        ]:
            with self.subTest(failure=failure), self.assertLogs('app.main', level='WARNING'):
                code, payload = self.upload(contents=pdf_bytes(comment=failure))
                self.assertEqual(code, 202)
                job_id = json.loads(payload)['id']
                job = self.wait_for_job(job_id)
                self.assertEqual((job['state'], job['error_code'], job['error']), ('failed', expected_code, message))
                self.assertEqual(self.request('GET', f'/api/jobs/{job_id}/download')[0], 409)
