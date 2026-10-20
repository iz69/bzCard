"""Shared-contact history and index migration, using fictional people only."""
import unittest
from unittest.mock import patch

from src import database
from src.services import contact_index, repository as repo
import test_contact_queries as fixtures


class ContactNameMatchingTests(unittest.TestCase):
    setUp = fixtures.ContactQueryTests.setUp
    tearDown = fixtures.ContactQueryTests.tearDown

    def test_name_edits_split_and_merge_only_the_matching_person(self):
        self.assertEqual(repo.get_user_contact(self.user, 'a')['card_count'], 3)
        repo.update_card_fields('b', {'person_name': '架空 花子'})
        for member in 'abc':
            self.assertEqual(repo.get_user_contact(self.user, member)['card_count'], 1)
        repo.update_card_fields('b', {'person_name': '青 葉　太 郎'})
        self.assertEqual(repo.get_user_contact(self.user, 'a')['card_count'], 3)
        repo.update_card_fields('b', {'person_name': ''})
        self.assertEqual(len(repo.list_user_contacts(self.user)), 3)
        repo.update_card_fields('b', {'person_name': '青葉 太郎'})
        self.assertEqual(len(repo.list_user_contacts(self.user)), 1)

    def test_shared_email_history_and_search_do_not_mix_other_people(self):
        repo.update_card_fields('b', {'person_name': '架空 花子', 'company_name': '株式会社サンプル'})
        repo.update_card_fields('a', {'company_name': '株式会社サンプル'})
        repo.update_card_fields('c', {'email': 'A@example.jp'})
        self.assertEqual({card['id'] for card in repo.get_user_contact(self.user, 'a')['cards']}, {'a', 'c'})
        self.assertEqual([card['id'] for card in repo.get_user_contact(self.user, 'b')['cards']], ['b'])
        self.assertEqual(repo.list_user_contacts(self.user, q='架空 検索語'), [])
        found = repo.list_user_contacts(self.user, q='a@example.jp')
        self.assertEqual({person['id'] for person in found}, {'b', 'c'})
        self.assertEqual(repo.list_user_contacts(self.other)[0]['card_count'], 1)

    def test_raw_sql_name_changes_are_reflected_in_the_next_read(self):
        with database.get_connection() as conn:
            conn.execute("UPDATE cards SET person_name='架空 花子' WHERE id='b'")
        self.assertEqual(len(repo.list_user_contacts(self.user)), 3)
        self.assertEqual([card['id'] for card in repo.get_user_contact(self.user, 'b')['cards']], ['b'])

    def test_v2_cache_is_rebuilt_once_without_updating_source_records(self):
        repo.update_card_fields('b', {'person_name': '架空 花子'})
        with database.connection() as conn:
            conn.execute("INSERT INTO cards(id,owner_user_id,status,person_name,email,created_at,updated_at) VALUES ('d',?,'ready','別人 次郎','d@example.test','5','5')", (self.user,))
        def legacy(card):
            return {('email', repo._contact_email(card.get('email')))} if card.get('email') else set()
        # Emulate the installed v2 cache, including a shared email and mobile
        # bridge, then remove only the new migration marker.
        def old_keys(card):
            keys = legacy(card)
            if card.get('mobile'):
                keys.add(('mobile', repo._contact_mobile(card['mobile'])))
            return keys
        with patch.object(repo, '_contact_identifiers', side_effect=old_keys):
            with database.connection() as conn:
                conn.execute('DELETE FROM schema_migrations WHERE version=3')
                contact_index.initialize(conn)
                conn.execute('DELETE FROM schema_migrations WHERE version=3')
        self.assertEqual(repo.get_user_contact(self.user, 'a')['card_count'], 3)
        first = repo.list_user_contacts_page(self.user, limit=1)
        self.assertIsNotNone(first['next_cursor'])
        with database.connection() as conn:
            source = {table:[dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
                      for table in ('cards', 'card_images')}
        database.init_db()
        self.assertEqual(len(repo.list_user_contacts(self.user)), 4)
        self.assertEqual(repo.get_user_contact(self.user, 'b')['card_count'], 1)
        changed = repo.list_user_contacts_page(self.user, known_revision=first['revision'])
        self.assertFalse(changed.get('unchanged', False))
        with self.assertRaises(contact_index.ChangedList):
            repo.list_user_contacts_page(self.user, limit=1, cursor=first['next_cursor'])
        with database.connection() as conn:
            for table, rows in source.items():
                self.assertEqual([dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')], rows)
            revision = [tuple(row) for row in conn.execute('SELECT * FROM contact_versions ORDER BY owner_user_id')]
            self.assertIsNotNone(conn.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone())
        with patch.object(contact_index, '_write_groups', side_effect=AssertionError('rebuilt on restart')):
            database.init_db()
        with database.connection() as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM contact_versions ORDER BY owner_user_id')], revision)

    def test_failed_rebuild_rolls_back_and_can_be_retried(self):
        with database.connection() as conn:
            conn.execute('DELETE FROM schema_migrations WHERE version=3')
            tables = ('contact_summaries', 'contact_members', 'contact_identifiers', 'contact_search', 'contact_versions')
            before = {table:sorted(tuple(row) for row in conn.execute(f'SELECT * FROM {table}')) for table in tables}
        with patch.object(contact_index, '_write_groups', side_effect=RuntimeError('synthetic migration failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic migration failure'):
                database.init_db()
        with database.connection() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone())
            for table, rows in before.items():
                self.assertEqual(sorted(tuple(row) for row in conn.execute(f'SELECT * FROM {table}')), rows)
        database.init_db()
        self.assertEqual(repo.get_user_contact(self.user, 'a')['card_count'], 3)


if __name__ == '__main__':
    unittest.main()
