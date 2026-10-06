import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import database
from src.routers import cards
from src.services import repository as repo
from src.services.contact_index import ChangedList, InvalidCursor
import test_contact_queries as fixtures


class ContactIndexTests(unittest.TestCase):
    setUp = fixtures.ContactQueryTests.setUp
    tearDown = fixtures.ContactQueryTests.tearDown

    def add_people(self, count=125):
        owner = repo.create_user('many', 'hash')['id']
        with database.connection() as conn:
            conn.executemany("""INSERT INTO cards(id,owner_user_id,status,person_name,created_at,updated_at)
                VALUES (?, ?, 'ready', ?, ?, ?)""", [(f'p{i:04}', owner, f'人物{i}', f'{i:04}', f'{i:04}') for i in range(count)])
        return owner

    def test_pages_cover_every_person_once_in_stable_order(self):
        owner = self.add_people()
        seen, cursor = [], None
        while True:
            result = repo.list_user_contacts_page(owner, limit=50, cursor=cursor)
            self.assertLessEqual(len(result['items']), 50)
            seen.extend(person['id'] for person in result['items'])
            cursor = result['next_cursor']
            if cursor is None:
                break
        self.assertEqual(seen, [f'p{i:04}' for i in reversed(range(125))])

    def test_first_page_never_groups_or_normalizes_all_cards(self):
        owner = self.add_people(1000)
        with patch.object(repo, '_contact_groups', side_effect=AssertionError('regrouped on read')), \
             patch.object(repo, '_normalized_search_fields', side_effect=AssertionError('normalized on read')):
            result = repo.list_user_contacts_page(owner)
        self.assertEqual(len(result['items']), 50)
        with database.connection() as conn:
            plan = conn.execute("EXPLAIN QUERY PLAN SELECT payload FROM contact_summaries WHERE owner_user_id = ? AND scope = '' ORDER BY created_at DESC,id DESC LIMIT 51", (owner,)).fetchall()
        self.assertTrue(any('idx_contact_order' in row['detail'] for row in plan))

    def test_update_rebuilds_only_related_people_and_changes_revision(self):
        owner = self.add_people(100)
        with patch.object(repo, '_contact_groups', wraps=repo._contact_groups) as groups:
            repo.update_card_fields('p0001', {'company_name': '新しい会社'})
        self.assertTrue(groups.called)
        self.assertTrue(all(len(call.args[0]) == 1 for call in groups.call_args_list))
        self.assertEqual(repo.get_user_contact(owner, 'p0001')['company_name'], '新しい会社')

    def test_bridge_deletion_splits_the_person(self):
        self.assertEqual(repo.list_user_contacts(self.user)[0]['card_count'], 3)
        repo.delete_card('b')
        self.assertEqual({p['id'] for p in repo.list_user_contacts(self.user)}, {'a', 'c'})
        self.assertEqual(repo.get_user_contact(self.user, 'a')['card_count'], 1)

    def test_identifier_change_merges_and_then_splits_groups(self):
        repo.update_card_fields('c', {'mobile': '', 'email': 'new@example.jp'})
        self.assertEqual(len(repo.list_user_contacts(self.user)), 2)
        repo.update_card_fields('c', {'email': 'A@EXAMPLE.JP'})
        self.assertEqual(repo.list_user_contacts(self.user)[0]['card_count'], 3)
        self.assertEqual(repo.list_user_contacts(self.other)[0]['card_count'], 1)

    def test_status_filter_splits_a_hidden_bridge_and_searches_only_that_scope(self):
        repo.set_card_status('b', 'queued')
        result = repo.list_user_contacts_page(self.user, status='ready')
        self.assertEqual({p['id'] for p in result['items']}, {'a', 'c'})
        self.assertTrue(result['has_in_progress'])
        self.assertEqual(repo.list_user_contacts_page(self.user, status='ready', q='検索語')['items'][0]['id'], 'a')

    def test_historical_and_cross_card_search_preserve_ranking(self):
        repo.update_card_fields('a', {'company_name': '古い会社'})
        repo.update_card_fields('c', {'person_name': '青葉 太郎'})
        result = repo.list_user_contacts_page(self.user, q='青葉 古い会社')
        self.assertEqual([p['id'] for p in result['items']], ['c'])
        self.assertEqual(result['items'][0]['card_count'], 3)
        self.assertEqual(repo.list_user_contacts_page(self.user, q='青葉 不存在')['items'], [])
        repo.save_ocr_result('a', '新しいOCR検索語', [], 0)
        self.assertEqual(repo.list_user_contacts(self.user, q='新しいOCR')[0]['id'], 'c')

    def test_cursor_is_bound_to_owner_query_status_and_revision(self):
        owner = self.add_people()
        first = repo.list_user_contacts_page(owner)
        cursor = first['next_cursor']
        for params in ({'user_id':self.user}, {'user_id':owner,'q':'人物'}, {'user_id':owner,'status':'ready'}):
            with self.assertRaises(InvalidCursor):
                repo.list_user_contacts_page(cursor=cursor, **params)
        repo.update_card_fields('p0001', {'person_name':'変更'})
        with self.assertRaises(ChangedList):
            repo.list_user_contacts_page(owner, cursor=cursor)

    def test_unchanged_response_and_offscreen_processing(self):
        owner = self.add_people()
        repo.set_card_status('p0000', 'queued')
        first = repo.list_user_contacts_page(owner)
        self.assertTrue(first['has_in_progress'])
        self.assertTrue(all(p['status'] == 'ready' for p in first['items']))
        unchanged = repo.list_user_contacts_page(owner, known_revision=first['revision'])
        self.assertTrue(unchanged['unchanged'])
        repo.set_card_status('p0000', 'ready')
        changed = repo.list_user_contacts_page(owner, known_revision=first['revision'])
        self.assertFalse(changed.get('unchanged', False))
        self.assertFalse(changed['has_in_progress'])

    def test_raw_sql_changes_are_repaired_and_rollback_is_atomic(self):
        before = repo.list_user_contacts(self.user)
        with self.assertRaises(RuntimeError):
            with database.connection() as conn:
                conn.execute("UPDATE cards SET email='',mobile='' WHERE id='b'")
                raise RuntimeError('abort')
        self.assertEqual(repo.list_user_contacts(self.user), before)
        with sqlite3.connect(database.DB_PATH) as conn:
            conn.execute("DELETE FROM cards WHERE id='b'")
        self.assertEqual(len(repo.list_user_contacts(self.user)), 2)

    def test_owner_change_updates_both_users_without_leaking_members(self):
        with database.connection() as conn:
            conn.execute('UPDATE cards SET owner_user_id = ? WHERE id = ?', (self.other, 'b'))
        self.assertEqual({p['id'] for p in repo.list_user_contacts(self.user)}, {'a','c'})
        moved = repo.get_user_contact(self.other, 'b')
        self.assertEqual({p['id'] for p in moved['cards']}, {'private','b'})
        self.assertIsNone(repo.get_user_contact(self.user, 'b'))

    def test_detail_summary_and_members_share_a_snapshot_during_a_write(self):
        original = repo._contact_detail

        def delete_between_reads(conn, owner, summary, scope):
            repo.delete_card('b')
            return original(conn, owner, summary, scope)

        with patch.object(repo, '_contact_detail', side_effect=delete_between_reads):
            detail = repo.get_user_contact(self.user, 'a')
        self.assertEqual(detail['card_count'], 3)
        self.assertEqual({card['id'] for card in detail['cards']}, {'a', 'b', 'c'})
        self.assertEqual(len(repo.list_user_contacts(self.user)), 2)

    def test_initial_backfill_is_persistent_and_not_repeated_on_restart(self):
        expected = repo.list_user_contacts(self.user)
        with database.connection() as conn:
            conn.execute('DELETE FROM schema_migrations WHERE version=2')
            for table in ('contact_summaries','contact_members','contact_identifiers','contact_search','contact_versions'):
                conn.execute('DELETE FROM '+table)
        database.init_db()
        self.assertEqual(repo.list_user_contacts(self.user), expected)
        with patch.object(repo, '_contact_groups', side_effect=AssertionError('rebuilt on restart')):
            database.init_db()
        with database.connection() as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_http_paging_validation_and_stale_cursor(self):
        owner = self.add_people()
        app = FastAPI()
        app.include_router(cards.router)
        app.dependency_overrides[cards.require_user] = lambda: {'id':owner}
        with TestClient(app) as client:
            first = client.get('/api/contacts?limit=50')
            self.assertEqual(first.status_code, 200)
            self.assertEqual(len(first.json()['items']), 50)
            for value in ('0','201','invalid'):
                self.assertEqual(client.get('/api/contacts',params={'limit':value}).status_code, 422)
            self.assertEqual(client.get('/api/contacts',params={'cursor':'broken'}).status_code, 400)
            repo.update_card_fields('p0001', {'person_name':'変更'})
            self.assertEqual(client.get('/api/contacts',params={'limit':50,'cursor':first.json()['next_cursor']}).status_code, 409)
            self.assertEqual(len(client.get('/api/contacts').json()['items']), 125)


if __name__ == '__main__':
    unittest.main()
