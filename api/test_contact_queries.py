import tempfile
import unittest
import json
from pathlib import Path

from src import database
from src.config import settings
from src.services import repository


class ContactQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path, self.old_dir = database.DB_PATH, settings.data_dir
        object.__setattr__(settings, "data_dir", Path(self.temp.name))
        database.DB_PATH = settings.data_dir / "test.db"
        database.init_db()
        self.user = repository.create_user("one", "hash")["id"]
        self.other = repository.create_user("two", "hash")["id"]
        with database.connection() as conn:
            for card_id, owner, email, mobile, created, ocr in (
                ("a", self.user, "a@example.jp", "", "1", "古い名刺の検索語"),
                ("b", self.user, "a@example.jp", "09011112222", "2", ""),
                ("c", self.user, "", "090-1111-2222", "3", ""),
                ("private", self.other, "a@example.jp", "09011112222", "4", "秘密"),
            ):
                conn.execute("""INSERT INTO cards
                    (id, owner_user_id, status, email, mobile, created_at, updated_at)
                    VALUES (?, ?, 'ready', ?, ?, ?, ?)""",
                    (card_id, owner, email, mobile, created, created))
                conn.execute("INSERT INTO card_images (id, card_id, side, original_image_path, ocr_text, ocr_blocks_json, created_at, updated_at) VALUES (?, ?, 'front', ?, ?, ?, ?, ?)", (card_id, card_id, f"cards/{card_id}/original.jpg", ocr, "large OCR blocks", created, created))

    def tearDown(self):
        database.DB_PATH = self.old_path
        object.__setattr__(settings, "data_dir", self.old_dir)
        self.temp.cleanup()

    def test_searching_old_card_keeps_full_person_without_ocr_payload(self):
        contacts = repository.list_user_contacts(self.user, q="検索語")
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["id"], "c")
        self.assertEqual(contacts[0]["card_count"], 3)
        self.assertNotIn("ocr_blocks_json", contacts[0])

    def test_detail_resolves_any_member_and_never_includes_other_users(self):
        for member in "abc":
            detail = repository.get_user_contact(self.user, member)
            self.assertEqual([c["id"] for c in detail["cards"]], ["c", "b", "a"])
            self.assertEqual(detail["id"], "c")
            self.assertNotIn("ocr_blocks_json", detail["cards"][0])
        self.assertIsNone(repository.get_user_contact(self.user, "private"))
        self.assertIsNone(repository.get_user_contact(self.user, "missing"))

    def test_summary_revision_matches_detail_and_updates_when_old_member_changes(self):
        before = repository.list_user_contacts(self.user)[0]
        self.assertEqual(before["revision"], repository.get_user_contact(self.user, "a")["revision"])
        with database.connection() as conn:
            conn.execute("UPDATE cards SET status = 'queued' WHERE id = 'a'")
        after = repository.list_user_contacts(self.user)[0]
        self.assertTrue(after["has_in_progress"])
        self.assertNotEqual(before["revision"], after["revision"])

    def test_status_filter_still_applies(self):
        with database.connection() as conn:
            conn.execute("UPDATE cards SET status = 'queued' WHERE id = 'a'")
        rows = repository.list_user_contacts(self.user, status="queued")
        self.assertEqual([row["id"] for row in rows], ["a"])

    def test_postal_code_format_is_shared_by_manual_and_ocr_saves(self):
        repository.update_card_fields("a", {"postal_code": "〒２５００１９３"})
        self.assertEqual(repository.get_card("a")["postal_code"], "250-0193")

        repository.save_extraction_result("a", {"postal_code": "2430018", "_raw": {}}, 0)
        saved = repository.get_card("a")
        self.assertEqual(saved["postal_code"], "243-0018")
        self.assertEqual(json.loads(saved["extracted_json"])["postal_code"], "243-0018")

        repository.update_card_fields("a", {"postal_code": "135-0091, 106-0032"})
        self.assertEqual(repository.get_card("a")["postal_code"], "135-0091, 106-0032")
