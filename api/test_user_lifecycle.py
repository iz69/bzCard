import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from src import database
from src.auth import hash_password, issue_session
from src.config import settings
from src.routers.auth import router as auth_router
from src.routers.cards import router as cards_router
from src.routers.line import _handle, router as line_router
from src.services import repository
from src.services.user_data_lock import user_data_lock


class UserLifecycleRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_data_dir = settings.data_dir
        self.original_multi_user = settings.multi_user_enabled
        object.__setattr__(settings, "data_dir", Path(self.temp_dir.name))
        object.__setattr__(settings, "multi_user_enabled", True)
        database.DB_PATH = settings.data_dir / "bzcard.db"
        database.init_db()
        self.admin = repository.create_user("admin", "hash", role="admin")
        self.target = repository.create_user("remove-me", "hash")
        self.other = repository.create_user("keep-me", "hash")
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(cards_router)
        app.include_router(line_router)
        self.client = TestClient(app)
        self.client.headers["Authorization"] = f"Bearer {issue_session(self.admin['id'])[0]}"

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        object.__setattr__(settings, "data_dir", self.original_data_dir)
        object.__setattr__(settings, "multi_user_enabled", self.original_multi_user)
        self.client.close()
        self.temp_dir.cleanup()

    def _insert_card(self, card_id, owner_id):
        directory = settings.data_dir / "cards" / card_id
        directory.mkdir()
        (directory / "original.jpg").write_bytes(b"unique image bytes:" + card_id.encode())
        with database.connection() as conn:
            conn.execute(
                """
                INSERT INTO cards (id, status, owner_user_id, created_at, updated_at)
                VALUES (?, 'ready', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
                """,
                (card_id, owner_id),
            )
            conn.execute("INSERT INTO card_images (id, card_id, side, original_image_path, created_at, updated_at) VALUES (?, ?, 'front', ?, 'now', 'now')", (card_id + "-front", card_id, f"cards/{card_id}/original.jpg"))
            conn.execute("INSERT INTO jobs (id, card_id, type, status, created_at) VALUES (?, ?, 'process_card', 'queued', 'now')", (card_id, card_id))
            conn.execute("INSERT INTO card_images (id, card_id, side, original_image_path, created_at, updated_at) VALUES (?, ?, 'back', ?, 'now', 'now')", (card_id, card_id, f"cards/{card_id}/original.jpg"))

    def _delete(self, user=None, confirmation=None):
        user = user or self.target
        return self.client.request("DELETE", f"/api/auth/users/{user['id']}", json={"confirm_login_id": user["login_id"] if confirmation is None else confirmation})

    def _snapshot_other_data(self):
        """All other-user rows and bytes must survive byte-for-byte."""
        with database.connection() as conn:
            result = {}
            for table in ("users", "sessions", "cards", "card_images", "jobs", "line_connections", "line_link_requests", "line_events", "import_batches"):
                result[table] = [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            # Only the requesting administrator's authentication timestamps
            # legitimately change on every API call. Keep all other data exact.
            result["users"] = [row[:6] + (None, None) if row[0] == self.admin["id"] else row for row in result["users"]]
            result["sessions"] = [row[:4] + (None,) if row[1] == self.admin["id"] else row for row in result["sessions"]]
        result["files"] = {str(p.relative_to(settings.data_dir)): p.read_bytes() for p in (settings.data_dir / "cards").rglob("*") if p.is_file()}
        return result

    def test_stopping_user_revokes_only_their_sessions(self):
        repository.create_session("target-session", self.target["id"], "2099-01-01T00:00:00+00:00")
        repository.create_session("other-session", self.other["id"], "2099-01-01T00:00:00+00:00")

        stopped = repository.set_user_status(self.target["id"], "stopped")

        self.assertEqual(stopped["status"], "stopped")
        with database.connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sessions WHERE user_id = ?", (self.target["id"],)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sessions WHERE user_id = ?", (self.other["id"],)).fetchone())

    def test_delete_removes_only_the_target_users_owned_data(self):
        self._insert_card("target-card", self.target["id"])
        self._insert_card("other-card", self.other["id"])
        repository.create_session("target-session", self.target["id"], "2099-01-01T00:00:00+00:00")
        repository.create_session("other-session", self.other["id"], "2099-01-01T00:00:00+00:00")
        with database.connection() as conn:
            conn.execute(
                "INSERT INTO line_connections (id, owner_user_id, created_at, updated_at) VALUES ('target-line', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (self.target["id"],),
            )
            conn.execute(
                "INSERT INTO line_connections (id, owner_user_id, created_at, updated_at) VALUES ('other-line', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (self.other["id"],),
            )
            conn.execute(
                "INSERT INTO line_link_requests (token_hash, connection_id, expires_at, created_at) VALUES ('target-link', 'target-line', '2099-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO line_events (id, status, card_id, created_at, updated_at) VALUES ('target-event', 'queued', 'target-card', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO line_events (id, status, card_id, created_at, updated_at) VALUES ('other-event', 'queued', 'other-card', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )

        deleted_card_ids = repository.delete_user_and_owned_data(self.target["id"])

        self.assertEqual(deleted_card_ids, ["target-card"])
        with database.connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE id = ?", (self.target["id"],)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE id = ?", (self.other["id"],)).fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM cards WHERE id = 'target-card'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM cards WHERE id = 'other-card'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM sessions WHERE token_hash = 'target-session'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sessions WHERE token_hash = 'other-session'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM line_connections WHERE id = 'target-line'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM line_connections WHERE id = 'other-line'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM line_link_requests WHERE token_hash = 'target-link'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM line_events WHERE id = 'target-event'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM line_events WHERE id = 'other-event'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM jobs WHERE card_id = 'target-card'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM card_images WHERE card_id = 'target-card'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM jobs WHERE card_id = 'other-card'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM card_images WHERE card_id = 'other-card'").fetchone())
        self.assertFalse((settings.data_dir / "cards/target-card").exists())
        self.assertEqual((settings.data_dir / "cards/other-card/original.jpg").read_bytes(), b"unique image bytes:other-card")

    def test_stop_blocks_password_login_sessions_and_line_then_resume_requires_new_login(self):
        password = "test-password-1234"
        repository.change_user_password(self.target["id"], hash_password(password))
        token, _ = issue_session(self.target["id"])
        url = f"/api/auth/users/{self.target['id']}"
        self.assertEqual(self.client.post(url + "/stop").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"login_id": self.target["login_id"], "password": password}).status_code, 403)
        with patch("src.routers.line._handle_owned_event") as handle:
            _handle({}, {"owner_user_id": self.target["id"]})
            handle.assert_not_called()
        self.assertEqual(self.client.post(url + "/activate").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"login_id": self.target["login_id"], "password": password}).status_code, 200)

    def test_management_requires_admin_and_multi_user_mode(self):
        for token in ("", issue_session(self.other["id"])[0]):
            self.client.headers["Authorization"] = f"Bearer {token}"
            for action in ("stop", "activate"):
                self.assertIn(self.client.post(f"/api/auth/users/{self.target['id']}/{action}").status_code, (401, 403))
            self.assertIn(self._delete().status_code, (401, 403))
        self.client.headers["Authorization"] = f"Bearer {issue_session(self.admin['id'])[0]}"
        object.__setattr__(settings, "multi_user_enabled", False)
        self.assertEqual(self._delete().status_code, 403)

    def test_protected_admin_and_confirmation_and_missing_user(self):
        self.assertEqual(self._delete(self.admin).status_code, 400)
        self.assertEqual(self.client.post(f"/api/auth/users/{self.admin['id']}/stop").status_code, 400)
        self.assertEqual(self._delete(confirmation="wrong").status_code, 400)
        self.assertEqual(self._delete({"id": "missing", "login_id": "missing"}).status_code, 404)
        self.assertIsNotNone(repository.get_user_by_id(self.target["id"]))

    def test_inflight_request_blocks_only_same_user_deletion(self):
        with user_data_lock(self.target["id"]):
            self.assertEqual(self._delete().status_code, 409)
        with user_data_lock(self.other["id"]):
            self.assertEqual(self._delete().status_code, 200)
        with user_data_lock(self.other["id"], exclusive=True):
            token, _ = issue_session(self.other["id"])
            response = self.client.get("/api/cards", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(response.status_code, 409)

    def test_running_job_blocks_deletion_without_mutation(self):
        self._insert_card("target-card", self.target["id"])
        with database.connection() as conn:
            conn.execute("UPDATE jobs SET status = 'running'")
        before = self._snapshot_other_data()
        self.assertEqual(self._delete().status_code, 409)
        self.assertEqual(before, self._snapshot_other_data())

    def test_other_users_full_rows_and_file_bytes_are_unchanged(self):
        self._insert_card("other-card", self.other["id"])
        self._insert_card("admin-card", self.admin["id"])
        before = self._snapshot_other_data()
        self._insert_card("target-card", self.target["id"])
        self.assertEqual(self._delete().status_code, 200)
        after = self._snapshot_other_data()
        before["users"] = [row for row in before["users"] if row[0] != self.target["id"]]
        self.assertEqual(before, after)
        self.assertEqual(self._delete().status_code, 404)

    def test_shared_image_reference_aborts_before_deleting_anything(self):
        self._insert_card("target-card", self.target["id"])
        self._insert_card("other-card", self.other["id"])
        for image_id in ("other-card-front", "other-card"):
            with self.subTest(image_id=image_id):
                with database.connection() as conn:
                    conn.execute("UPDATE card_images SET original_image_path = 'cards/target-card/original.jpg' WHERE id = ?", (image_id,))
                before = self._snapshot_other_data()
                self.assertEqual(self._delete().status_code, 409)
                self.assertEqual(before, self._snapshot_other_data())
                with database.connection() as conn:
                    conn.execute("UPDATE card_images SET original_image_path = 'cards/other-card/original.jpg' WHERE id = ?", (image_id,))

    def test_symlink_and_external_path_abort(self):
        self._insert_card("other-card", self.other["id"])
        with database.connection() as conn:
            conn.execute("INSERT INTO cards (id, status, owner_user_id, created_at, updated_at) VALUES ('target-card', 'ready', ?, 'now', 'now')", (self.target["id"],))
            conn.execute("INSERT INTO card_images (id, card_id, side, original_image_path, created_at, updated_at) VALUES ('target-card', 'target-card', 'front', 'cards/target-card/original.jpg', 'now', 'now')")
        (settings.data_dir / "cards/target-card").symlink_to(settings.data_dir / "cards/other-card", target_is_directory=True)
        self.assertEqual(self._delete().status_code, 409)
        self.assertTrue((settings.data_dir / "cards/other-card/original.jpg").exists())
        (settings.data_dir / "cards/target-card").unlink()
        with database.connection() as conn:
            conn.execute("UPDATE card_images SET original_image_path = '../outside.jpg' WHERE id = 'target-card'")
        self.assertEqual(self._delete().status_code, 409)

    def test_file_failure_preserves_user_for_retry_and_never_reports_success(self):
        self._insert_card("target-card", self.target["id"])
        with patch("src.services.user_deletion.shutil.rmtree", side_effect=PermissionError("denied")):
            self.assertEqual(self._delete().status_code, 500)
        self.assertIsNotNone(repository.get_user_by_id(self.target["id"]))
        self.assertIsNotNone(repository.get_card("target-card"))
        self.assertEqual(self._delete().status_code, 200)

    def test_connector_events_without_cards_are_deleted_by_exact_prefix(self):
        with database.connection() as conn:
            for conn_id, owner in (("conn_1", self.target), ("conn_10", self.other)):
                conn.execute("INSERT INTO line_connections (id, owner_user_id, created_at, updated_at) VALUES (?, ?, 'now', 'now')", (conn_id, owner["id"]))
                conn.execute("INSERT INTO line_events (id, status, created_at, updated_at) VALUES (?, 'searched', 'now', 'now')", (conn_id + ":event",))
        self.assertEqual(self._delete().status_code, 200)
        with database.connection() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT id FROM line_events")], ["conn_10:event"])

    def test_shared_import_batch_is_preserved_exclusive_batch_removed(self):
        self._insert_card("target-card", self.target["id"])
        self._insert_card("target-only", self.target["id"])
        self._insert_card("other-card", self.other["id"])
        with database.connection() as conn:
            for batch in ("shared", "exclusive"):
                conn.execute("INSERT INTO import_batches (id, status, created_at) VALUES (?, 'done', 'now')", (batch,))
            conn.execute("UPDATE cards SET import_batch_id = CASE WHEN id = 'target-only' THEN 'exclusive' ELSE 'shared' END")
        self.assertEqual(self._delete().status_code, 200)
        with database.connection() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT id FROM import_batches")], ["shared"])

    def test_stale_registration_and_session_cannot_recreate_deleted_user_data(self):
        self.assertEqual(self._delete().status_code, 200)
        with self.assertRaises(ValueError):
            repository.create_card("late", "cards/late/original.jpg", "hash", "auto", self.target["id"])
        with self.assertRaises(HTTPException):
            issue_session(self.target["id"])

    def test_conflicting_event_ownership_prevents_deletion(self):
        self._insert_card("other-card", self.other["id"])
        with database.connection() as conn:
            conn.execute("INSERT INTO line_connections (id, owner_user_id, created_at, updated_at) VALUES ('target-line', ?, 'now', 'now')", (self.target["id"],))
            conn.execute("INSERT INTO line_events (id, card_id, status, created_at, updated_at) VALUES ('target-line:event', 'other-card', 'queued', 'now', 'now')")
        before = self._snapshot_other_data()
        self.assertEqual(self._delete().status_code, 409)
        self.assertEqual(before, self._snapshot_other_data())

    def test_other_user_credentials_and_deleted_user_sessions(self):
        target_token = issue_session(self.target["id"])[0]
        other_token = issue_session(self.other["id"])[0]
        self.assertEqual(self._delete().status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {target_token}"}).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {other_token}"}).status_code, 200)

    def test_stopping_during_upload_does_not_leave_orphan_images(self):
        original_create = repository.create_card
        def stop_then_create(*args):
            repository.set_user_status(self.target["id"], "stopped")
            return original_create(*args)
        data = BytesIO()
        Image.new("RGB", (2, 2)).save(data, "PNG")
        token = issue_session(self.target["id"])[0]
        with patch("src.routers.cards.repository.create_card", side_effect=stop_then_create):
            response = self.client.post("/api/cards/upload", files={"file": ("card.png", data.getvalue(), "image/png")}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list((settings.data_dir / "cards").iterdir()), [])
        self.assertEqual(repository.list_user_cards(self.target["id"]), [])


if __name__ == "__main__":
    unittest.main()
