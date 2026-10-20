"""The identity audit must never write to the application database."""
import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import replay_person_identity as replay
from src.services import extractor, kana_reading


class ReadonlyReplayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'test.db'
        with sqlite3.connect(self.path) as conn:
            conn.executescript('''
                CREATE TABLE cards(id TEXT, person_name TEXT, person_name_kana TEXT,
                                   owner_user_id TEXT, latest_extraction_id TEXT);
                CREATE TABLE extraction_runs(id TEXT, card_id TEXT, created_at TEXT,
                    response_text TEXT, raw_json TEXT, model_json TEXT,
                    ocr_text TEXT, ocr_blocks_json TEXT);
                INSERT INTO cards VALUES('sample', '雲原 晴輔', 'くもはら はるすけ', NULL, 'run');
                INSERT INTO cards VALUES('blank', '未入力', '', NULL, NULL);
            ''')
        config = patch.object(replay, 'DB_PATH', self.path)
        config.start()
        self.addCleanup(config.stop)

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_connection_rejects_writes_and_never_initializes_application_tables(self):
        before = self.digest()
        with replay.readonly_connection() as conn:
            self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM cards').fetchone()[0], 2)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE cards SET person_name='changed'")
        self.assertEqual(self.digest(), before)

    def test_check_reports_cards_with_readings_but_without_saved_responses(self):
        before = self.digest()
        output = io.StringIO()
        with patch('sys.argv', ['replay', '--with-readings', '--latest', '--check']), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                replay.main()
        self.assertEqual(raised.exception.code, 1)
        summary = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((summary['selected_cards'], summary['without_inputs']), (1, 1))
        self.assertEqual(self.digest(), before)

    def test_latest_replay_checks_fresh_values_and_preserves_database(self):
        with sqlite3.connect(self.path) as conn:
            for run_id, created in [('older', '1'), ('run', '2')]:
                conn.execute('INSERT INTO extraction_runs VALUES(?,?,?,?,?,?,?,?)',
                             (run_id, 'sample', created, '{}', '{}', '{}', '雲原 晴輔', '[]'))
        before = self.digest()
        result = {'person_name': '雲原 晴輔', 'person_name_kana': 'くもはら はるすけ',
                  '_model': {'identity': {'status': 'resolved'}, 'kana': {'status': 'printed'}}}
        output = io.StringIO()
        with patch('sys.argv', ['replay', '--with-readings', '--latest', '--check-fresh']), \
             patch.object(replay, 'extract_card_fields', return_value=SimpleNamespace(data=result)) as extract, \
             contextlib.redirect_stdout(output):
            replay.main()
        summary = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((summary['runs'], summary['changed_or_failed'], summary['fresh_changed_or_failed']), (1, 0, 0))
        self.assertEqual(extract.call_count, 2)
        self.assertIsNone(extract.call_args_list[-1].kwargs.get('previous'))
        self.assertEqual(self.digest(), before)

    def test_generate_uses_each_prompt_once_and_does_not_reuse_another_cards_response(self):
        with sqlite3.connect(self.path) as conn:
            conn.executescript('''
                CREATE TABLE card_images(id TEXT, card_id TEXT, side TEXT, ocr_text TEXT, ocr_blocks_json TEXT);
                INSERT INTO cards VALUES('other', '森丘 詩乃', 'もりおか しの', NULL, NULL);
                INSERT INTO card_images VALUES('a', 'sample', 'front', '雲原 晴輔', '[]');
                INSERT INTO card_images VALUES('b', 'other', 'front', '森丘 詩乃', '[]');
            ''')
        before = self.digest()
        def generate(prompt):
            name, reading = ('雲原 晴輔', 'くもはら はるすけ') if '雲原 晴輔' in prompt else ('森丘 詩乃', 'もりおか しの')
            return json.dumps({'person_name': name, 'person_name_kana': reading})
        output = io.StringIO()
        with patch('sys.argv', ['replay', '--with-readings', '--generate', '--check-fresh']), \
             patch.object(extractor, '_generate_structured_response', side_effect=generate) as llm, \
             patch.object(kana_reading, 'settings', SimpleNamespace(kana_base_url='')), \
             contextlib.redirect_stdout(output):
            replay.main()
        summary = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((summary['cards'], summary['fresh_changed_or_failed']), (2, 0))
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(self.digest(), before)

    def test_generate_caches_auxiliary_role_checks_without_writing_cards(self):
        rows = [{'text': text, 'box': box, '_side': 'front'} for text, box in (
            ('セールスエンジニア', [100, 40, 300, 60]),
            ('所長', [100, 100, 150, 120]),
            ('雲原 晴輔', [100, 130, 300, 170]),
        )]
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE card_images(id TEXT, card_id TEXT, side TEXT, ocr_text TEXT, ocr_blocks_json TEXT)')
            conn.execute('INSERT INTO card_images VALUES(?,?,?,?,?)',
                         ('image', 'sample', 'front', '\n'.join(row['text'] for row in rows), json.dumps(rows)))
        before = self.digest()
        def generate(prompt, schema=None):
            if schema is not None:
                return json.dumps({'occupation': 'セールスエンジニア', 'title': '所長'})
            return json.dumps({'person_name': '雲原 晴輔', 'person_name_kana': 'くもはら はるすけ',
                               'title': 'セールスエンジニア'})
        output = io.StringIO()
        with patch('sys.argv', ['replay', '--with-readings', '--generate', '--check-fresh']), \
             patch.object(extractor, '_generate_structured_response', side_effect=generate) as llm, \
             patch.object(kana_reading, 'settings', SimpleNamespace(kana_base_url='')), \
             contextlib.redirect_stdout(output):
            replay.main()
        summary = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((summary['cards'], summary['fresh_changed_or_failed']), (1, 0))
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(self.digest(), before)


if __name__ == '__main__':
    unittest.main()
