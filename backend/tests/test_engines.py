import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from app.engines.base import EngineConfig, EngineError, ImageResult, PageResult
from app.engines.docling import DoclingAdapter
from app.engines.nemotron import NemotronAdapter
from app.engines.registry import EngineRegistry, configured_registry
from app.filenames import source_image_prefix, upload_basename


class RegistryTests(unittest.TestCase):
    def test_registered_engines_report_availability_and_capabilities(self):
        with patch.dict(os.environ, clear=True), patch.object(DoclingAdapter, 'probe', return_value='test'):
            registry = configured_registry()
            docling, nemotron = registry.describe()
            self.assertEqual((docling.id, docling.engine_version, docling.available), ('docling', 'test', True))
            self.assertTrue(docling.capabilities.markdown)
            self.assertTrue(docling.capabilities.page_markdown)
            self.assertTrue(docling.capabilities.png_images)
            self.assertTrue(docling.capabilities.figure_provenance)
            self.assertEqual(nemotron.id, 'nemotron')
            self.assertFalse(nemotron.available)
            self.assertFalse(nemotron.capabilities.markdown)
            self.assertIn('later phase', nemotron.unavailable_reason)
            self.assertIsInstance(registry.select('docling'), DoclingAdapter)

    def test_config_disabled_engine_is_not_probed(self):
        with patch.dict(os.environ, {'PDF2MD_ENGINES': 'nemotron'}):
            registry = configured_registry()
        with patch.object(DoclingAdapter, 'probe') as probe:
            docling, _ = registry.describe()
            self.assertFalse(docling.enabled)
            self.assertFalse(docling.available)
            with self.assertRaises(EngineError) as caught:
                registry.select(docling.id)
            self.assertEqual(caught.exception.code, 'engine_unavailable')
            probe.assert_not_called()

    def test_unknown_and_unavailable_have_distinct_errors(self):
        registry = EngineRegistry([NemotronAdapter(EngineConfig())])
        for engine_id, code in [('nemotron', 'engine_unavailable'), ('invalid', 'unknown_engine')]:
            with self.subTest(engine=engine_id), self.assertRaises(EngineError) as caught:
                registry.select(engine_id)
            self.assertEqual(caught.exception.code, code)

    def test_duplicate_ids_and_unknown_config_are_rejected(self):
        adapter = NemotronAdapter(EngineConfig())
        with self.assertRaisesRegex(ValueError, 'Duplicate conversion engine ID'):
            EngineRegistry([adapter, adapter])
        with patch.dict(os.environ, {'PDF2MD_ENGINES': 'invalid'}):
            with self.assertRaisesRegex(ValueError, 'Unknown IDs'):
                configured_registry()

    def test_probe_failure_is_isolated_and_does_not_leak_details(self):
        registry = EngineRegistry([DoclingAdapter(EngineConfig()), NemotronAdapter(EngineConfig())])
        with (
            patch.object(DoclingAdapter, 'probe', side_effect=RuntimeError('/private/runtime-path')),
            self.assertLogs('app.engines.registry', level='ERROR'),
        ):
            docling, nemotron = registry.describe()
        self.assertFalse(docling.available)
        self.assertNotIn('/private/runtime-path', docling.unavailable_reason)
        self.assertEqual(nemotron.id, 'nemotron')

    def test_placeholder_conversion_returns_typed_error(self):
        with self.assertRaises(EngineError) as caught:
            NemotronAdapter(EngineConfig()).convert(Path('input.pdf'), Path('.'))
        self.assertEqual(caught.exception.code, 'engine_unavailable')


class FigureFilenameTests(unittest.TestCase):
    def test_source_prefix_is_readable_and_safe(self):
        for filename, prefix in [
            ('Pump Service Manual.pdf', 'Pump-Service-Manual'),
            ('../Parts/Service (rev. A) #2.PDF', 'Service-rev-A-2'),
            (r'C:\archive\Café 泵.pdf', 'Café-泵'),
            ('Cafe\u0301.pdf', 'Café'),
            ('.pdf', 'document'),
            ('#%_.pdf', 'document'),
            ('a' * 99 + '泵.pdf', 'a' * 99),
        ]:
            with self.subTest(filename=filename):
                self.assertEqual(source_image_prefix(filename), prefix)
                self.assertLessEqual(len(prefix.encode('utf-8')), 100)
        self.assertEqual(upload_basename(r'C:\archive\Café 泵.pdf'), 'Café 泵.pdf')


class DoclingAdapterTests(unittest.TestCase):
    def test_worker_reuses_only_its_own_successful_converter(self):
        document = Mock(pages={1: None})
        document._make_copy_with_refmode.return_value = document
        document.iterate_items.return_value = []
        document.export_to_markdown.return_value = '# Document'
        response = SimpleNamespace(document=document)
        first = Mock()
        first.convert.side_effect = [response, response, RuntimeError('conversion failed')]
        replacement = Mock()
        replacement.convert.return_value = response
        other_worker = Mock()
        other_worker.convert.return_value = response
        factory = Mock(side_effect=[first, replacement, other_worker])
        runtime = (SimpleNamespace(PDF='pdf'), Mock(), factory, Mock(),
                   SimpleNamespace(REFERENCED='ref'), SimpleNamespace(LAYOUT='layout'))
        adapter = DoclingAdapter(EngineConfig())

        with (
            tempfile.TemporaryDirectory() as directory,
            ThreadPoolExecutor(max_workers=1) as pool,
            patch('app.engines.docling._load_runtime', return_value=runtime),
            patch('app.engines.docling._exclude_running_page_text', return_value={'body'}),
        ):
            workspace = Path(directory)
            adapter.convert(workspace / 'one.pdf', workspace)
            adapter.convert(workspace / 'two.pdf', workspace)
            self.assertEqual(factory.call_count, 1)
            with self.assertRaisesRegex(RuntimeError, 'conversion failed'):
                adapter.convert(workspace / 'failed.pdf', workspace)
            adapter.convert(workspace / 'three.pdf', workspace)
            pool.submit(adapter.convert, workspace / 'four.pdf', workspace).result()

        self.assertEqual(factory.call_count, 3)
        first.convert.assert_any_call(str(workspace / 'two.pdf'))
        replacement.convert.assert_called_once_with(str(workspace / 'three.pdf'))
        other_worker.convert.assert_called_once_with(str(workspace / 'four.pdf'))

    def test_docling_retains_pipeline_options_and_artifact_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            document = Mock(pages={1: None, 2: None})
            referenced_document = Mock()
            first = SimpleNamespace(uri=Path('images/b.png'))
            second = SimpleNamespace(uri=Path('images/a.png'))
            referenced_document.iterate_items.return_value = [
                (SimpleNamespace(image=first), 1), (SimpleNamespace(image=second), 1),
            ]

            def export_markdown(*, page_no=None, **options):
                pages = [f'Page 1\n\n![Figure 1]({first.uri.as_posix()})', f'Page 2\n\n![Figure 2]({second.uri.as_posix()})']
                return '\n\n'.join(pages) if page_no is None else pages[page_no - 1]

            referenced_document.export_to_markdown.side_effect = export_markdown

            def prepare_references(*, artifacts_dir, image_mode, page_no, reference_path):
                artifacts_dir.mkdir()
                (artifacts_dir / 'a.png').write_bytes(b'second figure')
                (artifacts_dir / 'b.png').write_bytes(b'first figure')
                return referenced_document

            document._make_copy_with_refmode.side_effect = prepare_references
            options = SimpleNamespace(use_embedded_picture_images=False, enable_remote_services=True)
            pipeline, pdf_option = Mock(return_value=options), Mock()
            converter = Mock(return_value=Mock(convert=Mock(return_value=SimpleNamespace(document=document))))
            runtime = (
                SimpleNamespace(PDF='pdf'), pipeline, converter, pdf_option,
                SimpleNamespace(REFERENCED='ref'), SimpleNamespace(LAYOUT='layout'),
            )
            with (
                patch('app.engines.docling._load_runtime', return_value=runtime),
                patch('app.engines.docling._runtime_version', return_value='test'),
                patch('app.engines.docling._exclude_running_page_text', return_value={'body'}),
            ):
                result = DoclingAdapter(EngineConfig()).convert(
                    workspace / 'input.pdf', workspace, source_filename='Pump Service Manual.pdf',
                )

            pipeline.assert_called_once_with(generate_picture_images=True, images_scale=3.0)
            self.assertTrue(options.use_embedded_picture_images)
            self.assertFalse(options.enable_remote_services)
            converter.return_value.convert.assert_called_once_with(str(workspace / 'input.pdf'))
            document._make_copy_with_refmode.assert_called_once_with(
                artifacts_dir=workspace / 'images', image_mode='ref', page_no=None, reference_path=workspace,
            )
            self.assertEqual(referenced_document.export_to_markdown.call_args_list, [
                call(image_mode='ref', caption_placement='layout', labels={'body'}),
                call(page_no=1, image_mode='ref', caption_placement='layout', labels={'body'}),
                call(page_no=2, image_mode='ref', caption_placement='layout', labels={'body'}),
            ])
            first_name = 'Pump-Service-Manual-fig-000001.png'
            second_name = 'Pump-Service-Manual-fig-000002.png'
            page_one = f'Page 1\n\n![Figure 1](images/{first_name})'
            page_two = f'Page 2\n\n![Figure 2](images/{second_name})'
            self.assertEqual(result.markdown, page_one + '\n\n' + page_two)
            self.assertEqual(result.pages, (PageResult(1, page_one), PageResult(2, page_two)))
            self.assertEqual(result.images, (ImageResult(f'images/{first_name}'), ImageResult(f'images/{second_name}')))
            images = workspace / 'images'
            self.assertEqual(sorted(path.name for path in images.iterdir()), [first_name, second_name])
            self.assertEqual((images / first_name).read_bytes(), b'first figure')
            self.assertEqual((images / second_name).read_bytes(), b'second figure')
            self.assertEqual(result.engine_version, 'test')
