import hashlib
import importlib
import io
import json
import os
import re
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from fastapi.testclient import TestClient

from app.engines.base import EngineConfig
from app.engines.docling import DoclingAdapter
from app.engines.registry import configured_registry


def image_references(markdown):
    return {
        unquote(name).removeprefix('./')
        for name in re.findall(r'!\[[^\]]*\]\(<?([^\s)>]+)>?\)', markdown)
    }


@unittest.skipUnless(os.getenv('PDF2MD_TEST_PDF'), 'Set PDF2MD_TEST_PDF to run real Docling integration')
class DoclingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.enterClassContext(patch.dict(os.environ, {
            'APP_DATA': str(data),
            'PDF2MD_ENGINES': 'docling',
            'DOCLING_DEVICE': 'cpu',
            'PDF2MD_CONVERSION_WORKERS': '1',
        }))
        main = importlib.import_module('app.main')
        cls.enterClassContext(patch.object(main, 'ROOT', data))
        cls.enterClassContext(patch.object(main, 'ENGINES', configured_registry()))
        cls.client = cls.enterClassContext(TestClient(main.app))

    def submit(self, name, content):
        response = self.client.post('/api/jobs', params={'engine': 'docling'}, files={
            'file': (name, content, 'application/pdf'),
        })
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['id']

    def wait_for_job(self, job_id):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            response = self.client.get(f'/api/jobs/{job_id}')
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job['state'] in {'complete', 'failed', 'interrupted'}:
                return job
            time.sleep(1)
        self.fail('Docling job exceeded the five-minute test timeout')

    def serialize_document(self, document):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch('docling.document_converter.DocumentConverter') as converter,
        ):
            converter.return_value.convert.return_value = SimpleNamespace(document=document)
            workspace = Path(directory)
            return DoclingAdapter(EngineConfig()).convert(workspace / 'synthetic.pdf', workspace)

    def test_real_pdf_cpu_conversion_edit_and_export(self):
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from PIL import Image

        device = PdfPipelineOptions().accelerator_options.device
        self.assertEqual(getattr(device, 'value', device), 'cpu')
        source = Path(os.environ['PDF2MD_TEST_PDF'])
        job_id = self.submit('Café 泵 Manual.PDF', source.read_bytes())
        job = self.wait_for_job(job_id)
        self.assertEqual(job['state'], 'complete', job.get('error'))
        self.assertEqual(job['engine'], 'docling')
        self.assertTrue(job['engine_version'])
        self.assertGreaterEqual(len(job['pages']), 2, 'Use a multipage PDF for this test')
        self.assertTrue(all(page.strip() for page in job['pages']))
        if reference := os.getenv('PDF2MD_TEST_FIGURE_REFERENCE'):
            self.assertIn(reference.casefold(), job['markdown'].casefold())

        references = image_references(job['markdown'])
        self.assertTrue(references, 'Expected real Docling PNG references')
        page_references = set().union(*(image_references(page) for page in job['pages']))
        self.assertTrue(page_references, 'Expected PNG references in the linked page Markdown')
        self.assertLessEqual(page_references, references)
        expected_paths = [f'images/Café-泵-Manual-fig-{index:06}.png' for index in range(1, len(references) + 1)]
        self.assertEqual(references, set(expected_paths))
        self.assertEqual([image['path'] for image in job['images']], expected_paths)
        for image in job['images']:
            page = image['source_page']
            self.assertTrue(page is None or 1 <= page <= len(job['pages']))

        edited = job['markdown'] + '\n<!-- local validation edit -->\n'
        self.assertEqual(self.client.put(f'/api/jobs/{job_id}/markdown', json={'markdown': edited}).status_code, 200)
        response = self.client.get(f'/api/jobs/{job_id}/download')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('manual.md').decode(), edited)
            self.assertEqual(json.loads(archive.read('image-index.json'))['images'], job['images'])
            self.assertEqual(set(archive.namelist()), {'manual.md', 'image-index.json', 'metadata.json', *references})
            manifest = json.loads(archive.read('metadata.json'))
            self.assertTrue(manifest['user_edited'])
            self.assertEqual(manifest['source_pdf_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(manifest['image_count'], len(references))
            for name in sorted(references):
                payload = archive.read(name)
                with Image.open(io.BytesIO(payload)) as picture:
                    self.assertEqual(picture.format, 'PNG')
                    picture.verify()
                asset = self.client.get(f'/api/jobs/{job_id}/assets/{name.removeprefix("images/")}')
                self.assertEqual(asset.status_code, 200)
                self.assertEqual(hashlib.sha256(payload).digest(), hashlib.sha256(asset.content).digest())
                self.assertEqual(manifest['artifact_checksums'][name], hashlib.sha256(payload).hexdigest())
                print(f'{name} SHA-256 {hashlib.sha256(payload).hexdigest()}')
        print(f'Docling {job["engine_version"]} CPU: {len(job["pages"])} pages, {len(references)} PNGs')

    def test_docling_places_associated_captions_above_and_below_figures(self):
        from docling_core.types.doc import (
            BoundingBox, CoordOrigin, DoclingDocument, ImageRef, ProvenanceItem, Size,
        )
        from docling_core.types.doc.labels import DocItemLabel
        from PIL import Image

        document = DoclingDocument(name='figure captions')
        for number, caption_top, text in [(1, 30, 'Caption above'), (2, 160, 'Caption below')]:
            document.add_page(page_no=number, size=Size(width=200, height=200))
            image = ImageRef.from_pil(Image.new('RGB', (25, 25), 'blue'), dpi=72)
            picture = document.add_picture(image=image, prov=ProvenanceItem(
                page_no=number, charspan=(0, 0),
                bbox=BoundingBox(l=30, t=60, r=130, b=150, coord_origin=CoordOrigin.TOPLEFT),
            ))
            caption = document.add_text(DocItemLabel.CAPTION, text, parent=picture, prov=ProvenanceItem(
                page_no=number, charspan=(0, len(text)),
                bbox=BoundingBox(l=30, t=caption_top, r=130, b=caption_top + 10, coord_origin=CoordOrigin.TOPLEFT),
            ))
            picture.captions.append(caption.get_ref())

        result = self.serialize_document(document)
        first, second = result.pages
        self.assertEqual([(image.source_page, image.caption) for image in result.images],
                         [(1, 'Caption above'), (2, 'Caption below')])
        self.assertEqual(result.images[0].source_bbox, (30, 60, 130, 150))
        self.assertEqual(image_references(first.markdown), {'images/synthetic-fig-000001.png'})
        self.assertEqual(image_references(second.markdown), {'images/synthetic-fig-000002.png'})
        self.assertLess(first.markdown.index('Caption above'), first.markdown.index('![Image]'))
        self.assertLess(second.markdown.index('![Image]'), second.markdown.index('Caption below'))
        self.assertEqual(result.markdown, first.markdown + '\n\n' + second.markdown)

    def test_ignores_running_page_margins_but_preserves_technical_text(self):
        from docling_core.types.doc import (
            BoundingBox, ContentLayer, CoordOrigin, DoclingDocument, ProvenanceItem, Size,
        )
        from docling_core.types.doc.labels import DocItemLabel

        document = DoclingDocument(name='page furniture')
        for page in range(1, 4):
            document.add_page(page_no=page, size=Size(width=600, height=800))

        def add_text(page, top, text, label=DocItemLabel.TEXT, layer=ContentLayer.BODY):
            document.add_text(label, text, content_layer=layer, prov=ProvenanceItem(
                page_no=page, charspan=(0, len(text)),
                bbox=BoundingBox(l=24, t=top, r=475, b=top + 12, coord_origin=CoordOrigin.TOPLEFT),
            ))

        add_text(1, 24, 'Service department copy', DocItemLabel.PAGE_HEADER, ContentLayer.FURNITURE)
        add_text(1, 22, 'Compressor service manual', DocItemLabel.TITLE)
        add_text(1, 753, 'WARNING: Isolate the supply before maintenance.')
        add_text(1, 768, 'Page 1 of 3')
        add_text(2, 25, 'Service department copy')
        add_text(2, 754, 'WARNING: Isolate the supply before maintenance.')
        add_text(2, 772, 'Document control footer', DocItemLabel.PAGE_FOOTER)
        add_text(2, 764, 'Check the gasket on a cold system.', DocItemLabel.FOOTNOTE)
        add_text(3, 180, 'Service department copy')
        add_text(3, 275, 'Proceed as described on Page 3 of 3.')
        add_text(3, 765, 'Page 1 of 8')

        result = self.serialize_document(document)
        first, second, third = (page.markdown for page in result.pages)
        self.assertIn('Compressor service manual', first)
        self.assertIn('Isolate the supply', first)
        self.assertIn('Isolate the supply', second)
        self.assertIn('Check the gasket', second)
        self.assertIn('Service department copy', third)
        self.assertIn('Page 1 of 8', third)
        self.assertIn('Proceed as described on Page 3 of 3.', third)
        self.assertNotIn('Service department copy', first + second)
        self.assertNotIn('Page 1 of 3', result.markdown)
        self.assertNotIn('Document control footer', result.markdown)
        self.assertEqual(result.markdown, '\n\n'.join((first, second, third)))

    def test_corrupt_pdf_reaches_failed_state_and_cannot_export(self):
        job_id = self.submit('corrupt.pdf', b'%PDF-1.4\ncorrupt document\n%%EOF\n')
        job = self.wait_for_job(job_id)
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['error_code'], 'conversion_failed')
        self.assertTrue(job['error'])
        self.assertEqual(self.client.get(f'/api/jobs/{job_id}/download').status_code, 409)
        self.assertEqual(self.client.put(f'/api/jobs/{job_id}/markdown', json={'markdown': 'edit'}).status_code, 409)
