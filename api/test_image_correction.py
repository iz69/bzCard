"""Image deletion, original-only rescans and ownership, using synthetic images."""
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src import database
from src.config import settings
from src.routers.cards import require_user, router
from src.services import repository
from src.services.image_store import create_processed_images, relative_path, save_original_bytes
from src.services.ocr import OcrResult
from src.worker import _process_image


def picture(color):
    image = Image.new('RGB', (960, 580), color)
    image.paste('black', (100, 200, 700, 220))
    stream = BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


class ImageCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # Settings is a frozen dataclass; match the application's existing tests.
        old_directory = settings.data_dir
        object.__setattr__(settings, 'data_dir', Path(self.directory.name))
        self.addCleanup(object.__setattr__, settings, 'data_dir', old_directory)
        self.db_patch = patch.object(database, 'DB_PATH', Path(self.directory.name) / 'bzcard.db')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.owner = repository.create_user('owner', 'hash')['id']
        self.other = repository.create_user('other', 'hash')['id']
        self.originals = {}
        self.processed = {}
        for side, color in (('front', 'white'), ('back', 'lightblue')):
            original = save_original_bytes(picture(color), 'image/png', 'card', side)
            self.originals[side] = (original, original.read_bytes())
            if side == 'front':
                repository.create_card('card', relative_path(original), 'front-hash', 'horizontal', self.owner)
            else:
                repository.set_back_image('card', relative_path(original), 'back-hash', 'horizontal')
            processed, thumb = create_processed_images(original, 'card', side)
            self.processed[side] = processed
            repository.set_card_processing_artifacts('card', relative_path(processed), relative_path(thumb), side)
            repository.save_ocr_result('card', 'SYNTHETIC OCR ' + side, [], 1, side)
        with database.connection() as conn:
            conn.execute("UPDATE jobs SET status = 'done'")
        repository.update_card_fields('card', {'person_name': '試験 太郎', 'memo': '保存する内容'})
        repository.set_card_status('card', 'not_card', 'sample failure')
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[require_user] = lambda: {'id': self.owner}
        self.client = TestClient(self.app)

    def delete(self, side='front'):
        return self.client.delete('/api/cards/card/processed-image', params={'side': side})

    def test_deletion_is_side_specific_and_preserves_originals_ocr_and_fields(self):
        before = repository.get_card('card')
        for side in ('front', 'back'):
            with self.subTest(side=side):
                result = self.delete(side)
                self.assertEqual(result.status_code, 200, result.text)
                card = result.json()['card']
                image = next(image for image in card['images'] if image['side'] == side)
                self.assertTrue(image['correction_disabled'])
                self.assertFalse(self.processed[side].exists())
                with Image.open(settings.data_dir / image['processed_image_path']) as preview:
                    self.assertEqual(preview.size, (960, 580))
                for field in ('person_name', 'memo', 'status', 'error_message', 'ocr_text', 'back_ocr_text'):
                    self.assertEqual(card[field], before[field])
                self.assertGreater(card['revision'], before['revision'])
                prefix = 'back-' if side == 'back' else ''
                for endpoint in ('processed-image', 'original-image', 'thumbnail'):
                    self.assertEqual(self.client.get('/api/cards/card/' + prefix + endpoint).status_code, 200)
                self.assertEqual(self.delete(side).status_code, 200)
                if side == 'front':
                    self.assertTrue(self.processed['back'].exists())
        for original, contents in self.originals.values():
            self.assertEqual(original.read_bytes(), contents)

    def test_rescan_and_rotation_do_not_restore_correction_or_change_original(self):
        self.assertEqual(self.delete().status_code, 200)
        self.assertEqual(self.client.post('/api/cards/card/rotate?degrees=90').status_code, 200)
        image = repository.get_card_images('card')[0]
        ocr = OcrResult('SYNTHETIC OCR', [], 1, 'horizontal', 5, 0)
        with patch('src.services.image_store._autocrop_and_correct') as crop, \
             patch('src.services.image_store._enhance_processed_image') as enhance, \
             patch('src.worker.run_yomitoku', return_value=ocr) as recognize:
            _process_image('card', image)
        crop.assert_not_called()
        enhance.assert_not_called()
        with Image.open(recognize.call_args.args[0]) as preview:
            self.assertEqual(preview.size, (580, 960))
        self.assertTrue(repository.get_card_images('card')[0]['correction_disabled'])
        self.assertFalse(self.processed['front'].exists())
        self.assertEqual(self.originals['front'][0].read_bytes(), self.originals['front'][1])

    def test_active_processing_rejects_deletion_without_changing_files(self):
        repository.enqueue_job('card', 'process_card')
        self.assertEqual(self.delete().status_code, 409)
        self.assertTrue(self.processed['front'].exists())
        self.assertFalse(repository.get_card_images('card')[0]['correction_disabled'])

    def test_ownership_side_and_missing_card_are_checked(self):
        self.assertEqual(self.delete('invalid').status_code, 422)
        self.assertEqual(self.client.delete('/api/cards/missing/processed-image').status_code, 404)
        self.app.dependency_overrides[require_user] = lambda: {'id': self.other}
        self.assertEqual(self.delete().status_code, 404)
        self.assertTrue(self.processed['front'].exists())

    def test_missing_corrected_file_can_still_return_to_original(self):
        self.processed['front'].unlink()
        self.assertEqual(self.delete().status_code, 200)
        self.assertEqual(self.client.get('/api/cards/card/processed-image').status_code, 200)

    def test_original_referenced_as_processed_is_never_deleted(self):
        path = self.originals['front'][0]
        repository.set_card_processing_artifacts('card', relative_path(path), 'cards/card/thumbnail.jpg')
        self.assertEqual(self.delete().status_code, 200)
        self.assertEqual(path.read_bytes(), self.originals['front'][1])

    def test_new_back_upload_resets_only_that_sides_original_preference(self):
        self.delete('front')
        self.delete('back')
        result = self.client.post('/api/cards/card/back/upload', files={'file': ('new.png', picture('yellow'), 'image/png')})
        self.assertEqual(result.status_code, 200, result.text)
        images = repository.get_card_images('card')
        self.assertTrue(images[0]['correction_disabled'])
        self.assertFalse(images[1]['correction_disabled'])

    def test_schema_upgrade_defaults_to_correction_and_keeps_saved_preference(self):
        self.delete('front')
        database.init_db()
        self.assertTrue(repository.get_card_images('card')[0]['correction_disabled'])
        with database.connection() as conn:
            conn.execute('ALTER TABLE card_images DROP COLUMN correction_disabled')
        database.init_db()
        self.assertEqual([row['correction_disabled'] for row in repository.get_card_images('card')], [0, 0])


if __name__ == '__main__':
    unittest.main()
