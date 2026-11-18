"""Offline regression tests; use generated documents and a temporary storage directory."""
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi import UploadFile
from pptx import Presentation
from pptx.util import Inches
import main
import services_ai
from models_patch import SlidePatch
from services.parse import extract_text_objects


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        for name in ('uploads', 'outputs', 'objects', 'outputs/_history'):
            (self.storage / name).mkdir(parents=True, exist_ok=True)
        self.file_id = 'fixture'
        self.source = self.storage / 'uploads/fixture.pptx'
        prs = Presentation()
        for text in ('Original first slide 2024', 'Original second slide'):
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = text
        prs.save(self.source)
        self.original_bytes = self.source.read_bytes()
        objects, _ = extract_text_objects(self.source)
        self.meta_path = self.storage / 'objects/fixture.json'
        self.meta_path.write_text(json.dumps({
            'file_id': self.file_id, 'file_name': 'fixture.pptx',
            'slide_count': 2, 'slides': [{'slide_index': 0}, {'slide_index': 1}],
            'objects': objects,
        }))
        for name, value in {
            'STORAGE_DIR': self.storage, 'UPLOAD_DIR': self.storage / 'uploads',
            'OUTPUTS_DIR': self.storage / 'outputs', 'OBJECTS_DIR': self.storage / 'objects',
            'HISTORY_DIR': self.storage / 'outputs/_history',
        }.items():
            p = patch.object(main, name, value); p.start(); self.addCleanup(p.stop)
        for name, value in {
            '_get_storage_dir': lambda: self.storage,
            '_get_objects_path': lambda fid: self.storage / 'objects' / f'{fid}.json',
        }.items():
            p = patch.object(services_ai, name, value); p.start(); self.addCleanup(p.stop)

    def apply(self, action, **kwargs):
        result = services_ai.apply_patches_to_onlyoffice(self.file_id, [SlidePatch(
            id='test', slide_index=0, shape_index=0, action=action, after=kwargs.pop('after', ''), **kwargs
        )])
        self.assertEqual(result, self.file_id)
        self.assertEqual(self.source.read_bytes(), self.original_bytes)
        return Presentation(self.storage / 'outputs/fixture.pptx')

    def test_edit_and_download(self):
        prs = self.apply('edit', before='Original first slide 2024', after='Revised text')
        self.assertEqual(prs.slides[0].shapes[0].text, 'Revised text')
        self.assertEqual(prs.slides[1].shapes[0].text, 'Original second slide')
        self.assertEqual(json.loads(self.meta_path.read_text())['objects'][0]['text'], 'Revised text')
        response = main.download_ppt(self.file_id)
        self.assertEqual(Presentation(io.BytesIO(response.body)).slides[0].shapes[0].text, 'Revised text')

    def test_download_chinese_filename(self):
        meta = json.loads(self.meta_path.read_text())
        meta['file_name'] = '企业演示.pptx'
        self.meta_path.write_text(json.dumps(meta))
        response = main.download_ppt(self.file_id)
        self.assertIn("filename*=UTF-8''%E4%BC%81", response.headers['content-disposition'])
        self.assertEqual(response.body, self.original_bytes)

    def test_add_textbox(self):
        prs = self.apply('add_textbox', after='Added paragraph')
        self.assertEqual(len(prs.slides[0].shapes), 2)
        self.assertEqual(prs.slides[0].shapes[1].text, 'Added paragraph')
        self.assertEqual(len(json.loads(self.meta_path.read_text())['objects']), 3)

    def test_delete_textbox(self):
        prs = self.apply('delete_textbox')
        self.assertEqual(len(prs.slides[0].shapes), 0)
        self.assertEqual(len(json.loads(self.meta_path.read_text())['objects']), 1)

    def test_insert_slide_preserves_following_content(self):
        prs = self.apply('add_slide', payload={'title': 'New title', 'bullets': ['One', 'Two'], 'insert_index': 1})
        self.assertEqual(len(prs.slides), 3)
        self.assertEqual(prs.slides[1].shapes[0].text, 'New title')
        self.assertIn('Two', prs.slides[1].shapes[1].text)
        self.assertEqual(prs.slides[2].shapes[0].text, 'Original second slide')
        meta = json.loads(self.meta_path.read_text())
        self.assertEqual(len(meta['slides']), 3)
        shifted = next(o for o in meta['objects'] if o['text'] == 'Original second slide')
        self.assertEqual(shifted['slide_index'], 2)
        self.assertTrue(shifted['shape_id'].startswith('s2_'))

    def test_upload_and_mock_ai_without_network(self):
        response = asyncio.run(main.upload_ppt(UploadFile(filename='example.pptx', file=io.BytesIO(self.original_bytes))))
        fid = response['file_id']
        self.assertEqual(response['page_count'], 2)
        self.assertEqual(len(main.get_objects(fid)['objects']), 2)
        with patch.dict(os.environ, {'USE_DEEPSEEK': 'false', 'FORCE_DEEPSEEK': 'false'}):
            result = asyncio.run(main.ai_scan_deck(main.ScanDeckRequest(file_id=fid, instruction='将 2024 更新为 2026')))
        self.assertEqual(len(result.patches), 1)
        self.assertIn('2026', result.patches[0].after)
        applied = asyncio.run(main.ai_apply_patches(main.PatchesRequest(file_id=fid, patches=result.patches)))
        self.assertEqual(applied.version_id, fid)
        self.assertEqual(main.health()['status'], 'ok')

    def test_active_routes_retained_legacy_routes_removed(self):
        paths = {r.path for r in main.app.routes}
        self.assertTrue({'/upload', '/download', '/ai/scan-deck', '/ai/apply-patches', '/onlyoffice/callback/{file_id}', '/rollback'} <= paths)
        self.assertFalse({'/manual-edit', '/manual-edit/batch', '/__debug/routes', '/__debug/oo-plugins'} & paths)


if __name__ == '__main__':
    unittest.main()
