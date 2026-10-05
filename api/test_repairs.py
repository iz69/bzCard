"""Regression cases for the audited failures; all storage and LINE calls isolated."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src import database, worker
from src.auth import issue_session
from src.config import settings
from src.routers.cards import router as cards_router
from src.routers.line import process_line_event, router as line_router
from src.services import feedback, repository
from src.services.card_data_lock import card_data_lock
from src.services.extractor import extract_card_fields, _remove_ungrounded_values
from src.services.person_identity import _refine_person_name_kana
from src.services.image_store import save_original_bytes, relative_path, sha256_file, resolve_data_path
from src.services.secret_store import encrypt, decrypt


def encrypt_in_process(value):
    return encrypt(value)


def image_bytes(color='white'):
    image = Image.new('RGB', (120, 60), color)
    image.putpixel((2, 3), (255, 0, 0))
    data = BytesIO()
    image.save(data, 'PNG')
    return data.getvalue()


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_dir, self.old_db, self.old_multi = settings.data_dir, database.DB_PATH, settings.multi_user_enabled
        object.__setattr__(settings, 'data_dir', Path(self.temp.name))
        object.__setattr__(settings, 'multi_user_enabled', True)
        database.DB_PATH = settings.data_dir / 'bzcard.db'
        database.init_db()
        self.owner = repository.create_user('owner', 'hash', role='admin')['id']
        self.other = repository.create_user('other', 'hash')['id']
        app = FastAPI()
        app.include_router(cards_router)
        app.include_router(line_router)
        self.client = TestClient(app)
        self.client.headers['Authorization'] = 'Bearer ' + issue_session(self.owner)[0]

    def tearDown(self):
        self.client.close()
        object.__setattr__(settings, 'data_dir', self.old_dir)
        object.__setattr__(settings, 'multi_user_enabled', self.old_multi)
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def card(self, card_id='a', owner=None, color='white'):
        original = save_original_bytes(image_bytes(color), 'image/png', card_id)
        job = repository.create_card(card_id, relative_path(original), sha256_file(original), 'auto', owner or self.owner)
        repository.finish_job(job)
        repository.set_card_status(card_id, 'ready')
        return repository.get_card(card_id)

    def extraction(self, card_id='a', name='角田 太郎', kana='つのだ たろう', email='a@example.jp'):
        output = dict(person_name=name, person_name_kana=kana, email=email, company_name='株式会社例')
        output['_raw'] = {k: v for k, v in output.items() if not k.startswith('_')}
        repository.save_extraction_result(card_id, output, 1, name+'\n'+email, [])

    def test_failed_duplicate_and_replacement_back_upload_preserve_old_images(self):
        self.card('a')
        old = save_original_bytes(image_bytes('blue'), 'image/png', 'a', 'back')
        job = repository.set_back_image('a', relative_path(old), sha256_file(old), 'auto')
        repository.finish_job(job)
        before = old.read_bytes()
        bad = self.client.post('/api/cards/a/back/upload', files={'file': ('bad.png', b'broken image', 'image/png')})
        self.assertEqual(bad.status_code, 415)
        self.assertEqual(old.read_bytes(), before)
        self.card('b', color='green')
        duplicate = self.client.post('/api/cards/a/back/upload', files={'file': ('new.png', image_bytes('green'), 'image/png')})
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(repository.get_card('a')['back_original_image_path'], relative_path(old))
        replacement = self.client.post('/api/cards/a/back/upload', files={'file': ('new.png', image_bytes('red'), 'image/png')})
        self.assertEqual(replacement.status_code, 200)
        self.assertNotEqual(repository.get_card('a')['back_original_image_path'], relative_path(old))
        self.assertFalse(old.exists())
        self.assertTrue(resolve_data_path(repository.get_card('a')['original_image_path']).exists())

    def test_rotation_persists_when_full_processing_rebuilds_image(self):
        card = self.card()
        original = resolve_data_path(card['original_image_path'])
        before = original.read_bytes()
        self.assertEqual(self.client.post('/api/cards/a/rotate?degrees=90').status_code, 200)
        image = repository.get_card_images('a')[0]
        self.assertEqual(image['manual_rotation'], 90)
        with patch('src.worker.run_yomitoku', return_value=worker.OcrResult('名刺', [], 1, 'horizontal')):
            worker._process_image('a', image)
        with Image.open(resolve_data_path(repository.get_card('a')['processed_image_path'])) as result:
            self.assertGreater(result.height, result.width)
        self.assertEqual(original.read_bytes(), before)

    def test_recovery_and_retries_have_a_limit(self):
        self.card()
        repository.enqueue_job('a', 'process_card')
        for attempt in range(1, 4):
            job = repository.claim_next_job()
            self.assertEqual(job['attempts'], attempt)
            repository.recover_interrupted_work()
        self.assertIsNone(repository.claim_next_job())
        self.assertEqual(repository.get_card('a')['status'], 'error')
        repository.enqueue_job('a', 'process_card')
        job = repository.claim_next_job()
        repository.fail_job(job['id'], 'a', 'temporary')
        self.assertEqual(repository.get_active_job('a')['status'], 'queued')

    def test_active_job_response_is_compatible_while_image_operations_are_locked(self):
        self.card()
        job_id = repository.enqueue_job('a', 'process_card')
        with database.connection() as conn:
            conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
        with card_data_lock('a'):
            for action in ('reprocess', 'reextract'):
                response = self.client.post('/api/cards/a/'+action)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['job_id'], job_id)
            self.assertEqual(self.client.post('/api/cards/a/rotate?degrees=90').status_code, 409)
            self.assertEqual(self.client.patch('/api/cards/a', json={'person_name':'手動入力'}).status_code, 200)

    def test_contact_and_card_search_require_every_token_with_cross_card_match(self):
        self.card('a')
        self.card('b', color='blue')
        repository.update_card_fields('a', {'person_name': '青葉 花子', 'email': 'a@ex.jp'})
        repository.update_card_fields('b', {'company_name': '古い会社', 'email': 'a@ex.jp'})
        self.assertEqual(repository.list_user_cards(self.owner, q='青葉 古い会社'), [])
        self.assertEqual(len(repository.list_user_contacts(self.owner, q='青葉 古い会社')), 1)
        self.assertEqual(repository.list_user_contacts(self.owner, q='青葉 存在しない'), [])
        self.assertEqual(repository.list_user_cards(self.owner, q='-'), [])

    def test_same_second_updates_change_contact_revision(self):
        self.card()
        revision = repository.list_user_contacts(self.owner)[0]['revision']
        with patch('src.services.repository.now_iso', return_value='2026-01-01T00:00:00+00:00'):
            repository.update_card_fields('a', {'title': '主任'})
            revision2 = repository.list_user_contacts(self.owner)[0]['revision']
            repository.update_card_fields('a', {'title': '課長'})
        self.assertNotEqual(revision, revision2)
        self.assertNotEqual(revision2, repository.list_user_contacts(self.owner)[0]['revision'])

    def test_corrections_preserve_raw_ignore_formatting_and_supersede_earlier_entries(self):
        self.card()
        self.extraction()
        repository.update_card_fields('a', {'person_name_kana': 'ツノダ　タロウ'})
        self.assertEqual(feedback.list_corrections(self.owner), [])
        repository.update_card_fields('a', {'person_name_kana': 'かくた たろう'})
        first = feedback.list_corrections(self.owner)[0]
        self.assertEqual(first['model_value'], 'つのだ たろう')
        self.assertTrue(first['active'])
        self.assertEqual(json.loads(repository.get_card('a')['extracted_json'])['_raw']['person_name_kana'], 'つのだ たろう')
        repository.update_card_fields('a', {'person_name_kana': 'すみだ たろう'})
        old = next(c for c in feedback.list_corrections(self.owner) if c['id'] == first['id'])
        self.assertTrue(old['superseded'])
        self.assertFalse(old['active'])
        self.assertEqual(self.client.patch('/api/corrections/'+first['id'], json={'active': True}).status_code, 400)
        self.assertEqual(feedback.list_corrections(self.other), [])
        token = issue_session(self.other)[0]
        self.assertEqual(self.client.get('/api/cards/a/corrections', headers={'Authorization': 'Bearer '+token}).status_code, 404)
        self.assertEqual(self.client.patch('/api/corrections/'+first['id'], json={'active':False}, headers={'Authorization':'Bearer '+token}).status_code, 404)

    def test_feedback_evaluation_same_person_new_card_and_held_out_users(self):
        self.card()
        self.extraction()
        repository.update_card_fields('a', {'person_name_kana': 'かくた たろう'})
        output = {'person_name': '角田 太郎', 'person_name_kana':'つのだ たろう', 'email':'a@example.jp'}
        cases = [('same', self.owner, '角田 太郎\na@example.jp', output, 'かくた たろう'),
                 ('other-user', self.other, '角田 太郎\na@example.jp', output, 'つのだ たろう'),
                 ('same-name-different-person', self.owner, '角田 太郎\nb@example.jp', {**output,'email':'b@example.jp'}, 'かくた たろう'),
                 ('first-seen', self.owner, '山田 太郎\ny@example.jp', {'person_name':'山田 太郎','person_name_kana':'やまだ たろう','email':'y@example.jp'}, 'やまだ たろう'),
                 ('printed-conflict', self.owner, '角田 太郎（ツノダ タロウ）\na@example.jp', output, 'つのだ たろう')]
        for label, owner, source, raw, expected in cases:
            with self.subTest(label=label), patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)):
                baseline = extract_card_fields(source, []).data
                learned = extract_card_fields(source, [], owner_user_id=owner).data
                self.assertEqual(learned['person_name_kana'], expected)
                if label not in ('same', 'same-name-different-person'):
                    self.assertEqual(learned['person_name_kana'], baseline['person_name_kana'])
                    self.assertEqual(learned['_feedback']['applied_ids'], [])
        correction = feedback.list_corrections(self.owner)[0]
        feedback.set_correction_active(self.owner, correction['id'], False)
        self.assertEqual(feedback.relevant_corrections(self.owner, '角田 太郎 a@example.jp'), [])
        # Approved overwrite behavior is preserved; correction history survives.
        self.extraction()
        self.assertEqual(repository.get_card('a')['person_name_kana'], 'つのだ たろう')
        self.assertEqual(len(feedback.list_corrections(self.owner)), 1)
        repository.delete_card('a')
        self.assertEqual(feedback.list_corrections(self.owner), [])
        with database.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM extraction_runs').fetchone()[0], 0)

    def test_unrelated_english_lines_do_not_disable_confirmed_personal_reading(self):
        self.card()
        self.extraction()
        repository.update_card_fields('a', {'person_name_kana': 'かくた たろう'})
        raw = {'person_name': '角田 太郎', 'person_name_kana': 'つのだ たろう', 'email': 'a@example.jp'}
        source = '角田 太郎\nBUREAU VERITAS\na@example.jp'
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)):
            result = extract_card_fields(source, [], self.owner).data
        self.assertEqual(result['person_name_kana'], 'かくた たろう')
        self.assertTrue(result['_feedback']['applied_ids'])
        self.assertEqual(result['_model']['kana']['selected'], 'かくた たろう')
        self.assertEqual(result['_model']['kana']['source'], 'confirmed_correction')

    def test_surname_correction_transfers_to_another_person_and_enters_prompt(self):
        self.card()
        self.extraction(name='須藤 太郎', kana='すど たろう')
        repository.update_card_fields('a', {'person_name_kana': 'すどう たろう'})
        output = {'person_name': '須藤 花子', 'person_name_kana': 'すど はなこ', 'email': 'b@example.jp'}
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(output)) as generate:
            result = extract_card_fields('須藤 花子\nb@example.jp', [], self.owner).data
        self.assertEqual(result['person_name_kana'], 'すどう はなこ')
        self.assertEqual(result['_raw']['person_name_kana'], 'すど はなこ')
        self.assertEqual(result['_automatic']['person_name_kana'], 'すど はなこ')
        self.assertEqual(len(result['_feedback']['applied_ids']), 1)
        prompt = generate.call_args.args[0]
        self.assertIn('"表記": "須藤"', prompt)
        self.assertIn('"誤った推測": "すど"', prompt)
        self.assertIn('"訂正された読み": "すどう"', prompt)
        self.assertIn('姓・名の読み推測の参考', prompt)
        self.assertNotIn('すどう たろう', prompt)
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(output)):
            other = extract_card_fields('須藤 花子\nb@example.jp', [], self.other).data
        self.assertEqual(other['person_name_kana'], 'すど はなこ')
        self.assertEqual(other['_feedback']['example_ids'], [])

    def test_given_name_correction_keeps_the_new_surname(self):
        self.card()
        self.extraction(name='山田 翔太', kana='やまだ しょた')
        repository.update_card_fields('a', {'person_name_kana': 'やまだ しょうた'})
        output = {'person_name': '佐藤 翔太', 'person_name_kana': 'さとう しょた'}
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(output)):
            result = extract_card_fields('佐藤 翔太', [], self.owner).data
        self.assertEqual(result['person_name_kana'], 'さとう しょうた')

    def test_general_examples_that_confuse_the_name_retry_once_and_preserve_provenance(self):
        self.card()
        self.extraction(name='須藤 太郎', kana='すど たろう')
        repository.update_card_fields('a', {'person_name_kana': 'すどう たろう'})
        wrong = json.dumps({'person_name': 'すどう 花子', 'person_name_kana': 'すどう はなこ'})
        good = json.dumps({'person_name': '須藤 花子', 'person_name_kana': 'すど はなこ'})
        with patch('src.services.extractor._generate_structured_response', side_effect=[wrong, good]) as generate:
            result = extract_card_fields('須藤 花子\n山田 太郎\nb@example.jp', [], self.owner).data
        self.assertEqual(generate.call_count, 2)
        self.assertNotIn('姓・名の読み推測の参考', generate.call_args_list[1].args[0])
        self.assertEqual(result['person_name'], '須藤 花子')
        self.assertEqual(result['person_name_kana'], 'すどう はなこ')
        self.assertEqual(result['_raw']['person_name_kana'], 'すど はなこ')
        self.assertEqual(result['_feedback']['fallback']['rejected_response_text'], wrong)
        repository.save_extraction_result('a', result, 1, '須藤 花子\nb@example.jp', [])
        with database.connection() as conn:
            saved = json.loads(conn.execute('SELECT feedback_json FROM extraction_runs WHERE id = ?',
                               (repository.get_card('a')['latest_extraction_id'],)).fetchone()[0])
        self.assertEqual(saved['fallback']['rejected_response_text'], wrong)

    def test_general_reading_preserves_printed_kana_ruby_and_roman(self):
        self.card()
        self.extraction(name='須藤 太郎', kana='すど たろう')
        repository.update_card_fields('a', {'person_name_kana': 'すどう たろう'})
        output = {'person_name': '須藤 花子', 'person_name_kana': 'すど はなこ', 'email': 'hanako.sudo@example.jp'}
        cases = [
            ('須藤 花子（スド ハナコ）', []),
            ('須藤 花子\nHANAKO SUDO\nhanako.sudo@example.jp', []),
            ('須藤 花子\nスド ハナコ', [
                {'text': 'スド ハナコ', 'box': [0, 0, 140, 20], '_side': 'front'},
                {'text': '須藤 花子', 'box': [0, 25, 140, 60], '_side': 'front'},
            ]),
        ]
        for source, blocks in cases:
            with self.subTest(source=source), patch('src.services.extractor._generate_structured_response', return_value=json.dumps(output)):
                result = extract_card_fields(source, blocks, self.owner).data
            self.assertEqual(result['person_name_kana'], 'すど はなこ')
            self.assertEqual(result['_feedback']['applied_ids'], [])

    def test_general_reading_only_replaces_the_known_error_in_the_exact_component(self):
        self.card()
        self.extraction(name='須藤 太郎', kana='すど たろう')
        repository.update_card_fields('a', {'person_name_kana': 'すどう たろう'})
        cases = [('須藤 花子', 'すとう はなこ'), ('小須藤 花子', 'すど はなこ'),
                 ('山田 須藤', 'やまだ すど')]
        for name, kana in cases:
            with self.subTest(name=name, kana=kana), patch('src.services.extractor._generate_structured_response', return_value=json.dumps({'person_name':name, 'person_name_kana':kana})):
                result = extract_card_fields(name, [], self.owner).data
            self.assertEqual(result['person_name_kana'], kana)
            self.assertEqual(result['_feedback']['applied_ids'], [])
        data = {'person_name': '須藤花子', 'person_name_kana': 'すどはなこ'}
        corrections = feedback.relevant_corrections(self.owner, '須藤花子')
        self.assertEqual(feedback.apply_known_reading(data, '須藤花子', [], corrections), [])
        self.assertEqual(data['person_name_kana'], 'すどはなこ')

    def test_conflicting_readings_are_checked_before_example_limit_and_person_wins(self):
        for index, (name, corrected) in enumerate([
            ('須藤 一郎', 'すとう いちろう'), ('須藤 太郎', 'すどう たろう'),
            ('須藤 二郎', 'すどう じろう'), ('須藤 三郎', 'すどう さぶろう'),
        ]):
            card_id = f'c{index}'
            self.card(card_id)
            self.extraction(card_id, name, 'すど '+corrected.split()[1], f'{index}@example.jp')
            repository.update_card_fields(card_id, {'person_name_kana': corrected})
        # The conflicting oldest entry must not be hidden by the three-example limit.
        self.assertEqual(feedback.relevant_corrections(self.owner, '須藤 花子\nnew@example.jp'), [])
        output = {'person_name':'須藤 一郎', 'person_name_kana':'すど いちろう', 'email':'0@example.jp'}
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(output)):
            result = extract_card_fields('須藤 一郎\n0@example.jp', [], self.owner).data
        self.assertEqual(result['person_name_kana'], 'すとう いちろう')
        # With the exceptional example disabled, the general rule becomes usable.
        conflicting = next(c for c in feedback.list_corrections(self.owner) if c['card_id'] == 'c0')
        feedback.set_correction_active(self.owner, conflicting['id'], False)
        self.assertTrue(feedback.relevant_corrections(self.owner, '須藤 花子\nnew@example.jp'))

    def test_rule_changes_follow_recorrection_disabling_and_card_deletion(self):
        self.card()
        self.extraction(name='須藤 太郎', kana='すど たろう')
        repository.update_card_fields('a', {'person_name_kana': 'すどう たろう'})
        source = '須藤 花子\nb@example.jp'
        first = feedback.list_corrections(self.owner)[0]
        feedback.set_correction_active(self.owner, first['id'], False)
        self.assertEqual(feedback.relevant_corrections(self.owner, source), [])
        feedback.set_correction_active(self.owner, first['id'], True)
        self.assertTrue(feedback.relevant_corrections(self.owner, source))
        repository.update_card_fields('a', {'person_name_kana': 'すとう たろう'})
        rules = feedback.relevant_corrections(self.owner, source)
        self.assertEqual(rules[0]['reading_rules'][0]['after'], 'すとう')
        self.assertNotEqual(rules[0]['id'], first['id'])
        repository.delete_card('a')
        self.assertEqual(feedback.relevant_corrections(self.owner, source), [])

    def test_unseparated_training_name_is_not_split_by_guessing(self):
        self.card()
        self.extraction(name='須藤太郎', kana='すど たろう')
        repository.update_card_fields('a', {'person_name_kana': 'すどう たろう'})
        self.assertEqual(feedback.relevant_corrections(self.owner, '須藤 花子\nb@example.jp'), [])

    def test_recorrection_on_another_card_supersedes_the_same_person_example(self):
        self.card('a')
        self.extraction('a')
        repository.update_card_fields('a', {'person_name_kana': 'かくた たろう'})
        first = feedback.list_corrections(self.owner)[0]['id']
        self.card('b', color='blue')
        self.extraction('b')
        repository.update_card_fields('b', {'person_name_kana': 'すみだ たろう'})
        entries = feedback.list_corrections(self.owner)
        self.assertEqual([c['corrected_value'] for c in entries if c['active']], ['すみだ たろう'])
        self.assertTrue(next(c for c in entries if c['id'] == first)['superseded'])

    def test_user_deletion_cascades_only_their_provenance(self):
        self.card('other', self.other)
        self.extraction('other')
        repository.update_card_fields('other', {'person_name_kana': 'かくた たろう'})
        self.card('a')
        self.extraction('a')
        repository.update_card_fields('a', {'person_name_kana': 'すみだ たろう'})
        before = feedback.list_corrections(self.owner)
        repository.delete_user_and_owned_data(self.other)
        self.assertEqual(feedback.list_corrections(self.other), [])
        self.assertEqual(feedback.list_corrections(self.owner), before)
        with database.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM extraction_runs WHERE owner_user_id = ?', (self.other,)).fetchone()[0], 0)

    def test_inline_kana_side_separation_roman_order_and_address_evidence(self):
        data = {'person_name':'田中 さくら', 'person_name_kana':'たなか さくら'}
        _refine_person_name_kana(data, '田中 さくら')
        self.assertEqual(data['person_name_kana'], 'たなか さくら')
        data = {'person_name':'山田 太郎', 'person_name_kana':'やまだ たろう'}
        blocks = [{'text':'山田 太郎','box':[100,90,270,140], '_side':'front'},
                  {'text':'テスト','box':[100,50,180,70], '_side':'back'},
                  {'text':'サービス','box':[190,50,270,70], '_side':'back'}]
        _refine_person_name_kana(data, '山田 太郎 カナ', blocks)
        self.assertEqual(data['person_name_kana'], 'やまだ たろう')
        data['person_name_kana'] = 'あおば はなこ'
        _refine_person_name_kana(data, '山田 太郎\nYAMADA TARO\nyamada.taro@example.jp')
        self.assertEqual(data['person_name_kana'], 'やまだ たろう')
        for address, expected in [('東京都朝日町9-8-7',''), ('東京都朝日町1-2-3','東京都朝日町1-2-3')]:
            data = {'address': address}
            _remove_ungrounded_values(data, '東京都朝日町一丁目二番三号')
            self.assertEqual(data['address'], expected)

    def connector(self):
        with database.connection() as conn:
            conn.execute("INSERT INTO line_connections (id,owner_user_id,line_user_id,created_at,updated_at) VALUES ('line',?,'sender','now','now')", (self.owner,))
        return repository.get_line_connection('line')

    def test_line_download_retry_redelivery_and_restart_create_exactly_one_card(self):
        connector = self.connector()
        event = {'webhookEventId':'event', 'type':'message', 'source':{'userId':'sender'}, 'message':{'id':'message','type':'image'}}
        repository.queue_line_events([event], connector)
        repository.queue_line_events([event], connector)
        queued = repository.claim_next_line_event()
        with patch('src.routers.line._download', side_effect=RuntimeError('temporary')):
            process_line_event(queued)
        with database.connection() as conn:
            conn.execute("UPDATE line_events SET available_at = '0'")
        queued = repository.claim_next_line_event()
        repository.recover_interrupted_work()
        queued = repository.claim_next_line_event()
        with patch('src.routers.line._download', return_value=(image_bytes(), 'image/png')), patch('src.routers.line._reply'):
            process_line_event(queued)
        repository.queue_line_events([event], connector)
        self.assertIsNone(repository.claim_next_line_event())
        self.assertEqual(len(repository.list_user_cards(self.owner)), 1)
        with database.connection() as conn:
            row = conn.execute('SELECT * FROM line_events').fetchone()
            self.assertEqual(row['status'], 'queued')
            self.assertEqual(conn.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_webhook_persists_before_reply_and_does_not_download_inline(self):
        self.connector()
        event = {'webhookEventId':'x','type':'message','source':{'userId':'sender'},'message':{'id':'m','type':'image'}}
        with patch('src.routers.line._connection_for_signature', return_value=repository.get_line_connection('line')), patch('src.routers.line._download') as download:
            response = self.client.post('/line/webhook', json={'events':[event]})
            self.assertEqual(response.status_code, 200)
            download.assert_not_called()
        self.assertIsNotNone(repository.claim_next_line_event())
        with patch('src.routers.line._connection_for_signature', return_value=repository.get_line_connection('line')), patch('src.routers.line.repository.queue_line_events', side_effect=sqlite3.OperationalError('busy')):
            with self.assertRaises(sqlite3.OperationalError):
                self.client.post('/line/webhook', json={'events':[event]})

    def test_first_key_creation_is_exclusive_across_processes(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            tokens = list(pool.map(encrypt_in_process, ['one','two','three','four']))
        self.assertEqual([decrypt(token) for token in tokens], ['one','two','three','four'])

    def test_default_line_restore_is_a_one_time_migration(self):
        backup = settings.data_dir / 'bzcard-before-local-auth.db'
        with sqlite3.connect(backup) as conn:
            conn.execute('CREATE TABLE line_users (line_user_id TEXT, status TEXT, created_at TEXT)')
            conn.execute("INSERT INTO line_users VALUES ('legacy', 'active', 'now')")
        with database.connection() as conn:
            conn.execute("INSERT INTO line_connections (id,owner_user_id,created_at,updated_at) VALUES ('default',?,'now','now')", (self.owner,))
        self.assertTrue(repository.restore_default_line_identity_from_backup())
        with database.connection() as conn:
            conn.execute("UPDATE line_connections SET line_user_id = NULL WHERE id = 'default'")
        self.assertFalse(repository.restore_default_line_identity_from_backup())
        self.assertIsNone(repository.get_line_connection('default')['line_user_id'])
