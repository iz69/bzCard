import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO

import numpy as np
from PIL import Image
from unittest.mock import patch

from src import database
from src.config import settings
from src.services import repository
from src.services.fields import SCHEMA_KEYS
from src.services.image_store import save_original_bytes, relative_path
from src.services.ocr import _extract_blocks, _get_engine
from src.worker import OcrResult, _process_image, _run_ocr_with_auto_rotation
from test_repairs import image_bytes


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_dir, self.old_db = settings.data_dir, database.DB_PATH
        object.__setattr__(settings, 'data_dir', Path(self.temp.name))
        database.DB_PATH = settings.data_dir / 'bzcard.db'

    def tearDown(self):
        object.__setattr__(settings, 'data_dir', self.old_dir)
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def test_cards_only_legacy_database_is_backfilled_and_snapshot_is_preserved(self):
        path = save_original_bytes(image_bytes(), 'image/png', 'legacy')
        logical = ', '.join(f'{field} TEXT' for field in SCHEMA_KEYS)
        image_fields = ', '.join(f'{prefix}{field} TEXT' for prefix in ('','back_') for field in database.IMAGE_FIELDS)
        with sqlite3.connect(database.DB_PATH) as conn:
            conn.execute(f'CREATE TABLE cards (id TEXT PRIMARY KEY, status TEXT, {logical}, {image_fields}, created_at TEXT, updated_at TEXT, extracted_json TEXT, extraction_duration_ms INTEGER, error_message TEXT)')
            conn.execute("INSERT INTO cards (id,status,person_name,person_name_kana,original_image_path,ocr_text,ocr_blocks_json,ocr_direction,created_at,updated_at) VALUES ('legacy','ready','山田 太郎','やまだ たろう',?,'元のOCR','[]','auto','old','old')", (relative_path(path),))
        database.init_db()
        backup = settings.data_dir / 'bzcard-before-image-storage-v1.db'
        original_backup = backup.read_bytes()
        card = repository.get_card('legacy')
        self.assertEqual(card['original_image_path'], relative_path(path))
        self.assertEqual(card['ocr_text'], '元のOCR')
        self.assertEqual(card['person_name_kana'], 'やまだ たろう')
        self.assertEqual(len(card['images']), 1)
        self.assertTrue(card['images'][0]['original_sha256'])
        with database.connection() as conn:
            self.assertNotIn('original_image_path', {r['name'] for r in conn.execute('PRAGMA table_info(cards)')})
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        database.init_db()
        self.assertEqual(backup.read_bytes(), original_backup)
        self.assertEqual(repository.get_card('legacy')['images'], card['images'])

    def test_fixed_ocr_schema_uses_content_and_polygon_geometry(self):
        word = SimpleNamespace(content=' 山田 太郎 ', points=[[100,90],[270,90],[270,140],[100,140]])
        self.assertEqual(_extract_blocks(SimpleNamespace(words=[word])), [{'text':'山田 太郎','box':[100,90,270,140],'font_size':50}])
        import src.services.ocr as ocr
        old = ocr._engine
        ocr._engine = None
        try:
            with patch.dict('sys.modules', {'yomitoku': SimpleNamespace(OCR=lambda **kw: kw)}):
                engine = _get_engine()
            self.assertEqual(engine['configs']['text_recognizer']['model_name'], 'parseq-large-v4_1')
        finally:
            ocr._engine = old

    def test_automatic_rotation_sign_is_preserved_on_rebuild(self):
        database.init_db()
        owner = repository.create_user('owner','hash')['id']
        original = save_original_bytes(image_bytes(), 'image/png', 'card')
        repository.create_card('card', relative_path(original), 'digest', 'auto', owner)
        from src.services.image_store import create_processed_images
        processed, thumb = create_processed_images(original, 'card')
        vertical = OcrResult('名刺の文字列を十分な長さにします' * 2, [], 1, 'vertical', 0, 4)
        horizontal = OcrResult(vertical.raw_text, [], 1, 'horizontal', 6, 0)
        with patch('src.worker.run_yomitoku', side_effect=[vertical,horizontal,vertical]), patch('src.worker._is_portrait_image', return_value=True):
            _run_ocr_with_auto_rotation(processed, thumb, 'auto', 'card')
        rotated = processed.read_bytes()
        image = repository.get_card_images('card')[0]
        self.assertEqual(image['auto_rotation'], 270)
        with patch('src.worker.run_yomitoku', return_value=horizontal):
            _process_image('card', image)
        with Image.open(BytesIO(rotated)) as before, Image.open(processed) as after:
            self.assertEqual(before.size, after.size)
            # JPEG re-encoding changes bytes; geometry and pixels stay aligned.
            difference = np.abs(np.asarray(before, dtype=float) - np.asarray(after, dtype=float))
            self.assertLess(float(difference.mean()), 3)
