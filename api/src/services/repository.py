from __future__ import annotations

import json
import re
import unicodedata
from .normalization import normalize_fields
from .feedback import PIPELINE_VERSION, record_manual_changes
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

from ..config import settings
from .fields import SCHEMA_KEYS, CARD_FIELDS, EXTRACTED_JSON_FIELDS, SEARCH_FIELDS
from ..database import connection, get_connection, row_to_dict
from .timeutil import now_iso
from .secret_store import encrypt, decrypt
from .image_store import image_metadata, resolve_data_path
from .normalization import (
    _hiragana_to_katakana, _is_kana, _katakana_to_hiragana, _normalize_company_name, _normalize_phone_number
)


LIST_OMITTED_FIELDS = {
    "ocr_text",
    "ocr_blocks_json",
    "back_ocr_text",
    "back_ocr_blocks_json",
    "extracted_json",
}

CONTACT_SUMMARY_FIELDS = (
    "status",
    "thumbnail_path",
    "person_name",
    "company_name",
    "tags",
    "created_at",
    "updated_at",
)


def _upsert_card_image(
    conn,
    card_id: str,
    side: str,
    original_image_path: str,
    original_sha256: str,
    direction: str,
) -> None:
    now = now_iso()
    width, height, file_size = image_metadata(resolve_data_path(original_image_path))
    conn.execute(
        """
        INSERT INTO card_images (
            id, card_id, side, original_sha256, original_image_path, ocr_direction,
            width, height, file_size, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(card_id, side) DO UPDATE SET
            original_sha256 = excluded.original_sha256,
            original_image_path = excluded.original_image_path,
            processed_image_path = NULL,
            thumbnail_path = NULL,
            ocr_direction = excluded.ocr_direction,
            manual_rotation = 0, auto_rotation = 0,
            ocr_text = NULL,
            ocr_blocks_json = NULL,
            ocr_duration_ms = NULL,
            width = excluded.width,
            height = excluded.height,
            file_size = excluded.file_size,
            updated_at = excluded.updated_at
        """,
        (
            f"{card_id}:{side}",
            card_id,
            side,
            original_sha256,
            original_image_path,
            direction,
            width,
            height,
            file_size,
            now,
            now,
        ),
    )


def _hydrate_card(conn, row, include_images: bool = True) -> dict | None:
    card = row_to_dict(row)
    if card is None:
        return None
    if not include_images:
        for field in LIST_OMITTED_FIELDS:
            card.pop(field, None)
        return card
    images = [
        dict(image)
        for image in conn.execute(
            """
            SELECT *
            FROM card_images
            WHERE card_id = ?
            ORDER BY CASE side WHEN 'front' THEN 0 WHEN 'back' THEN 1 ELSE 2 END, created_at
            """,
            (card["id"],),
        ).fetchall()
    ]
    card["images"] = images
    return card


def create_card(card_id: str, original_image_path: str, original_sha256: str,
                direction: str, owner_user_id: str, *, line_event_id: str | None = None) -> str:
    now = now_iso()
    job_id = uuid4().hex
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE id = ? AND status = 'active'", (owner_user_id,)).fetchone() is None:
            raise ValueError("利用者が無効のため名刺を登録できません")
        conn.execute("INSERT INTO cards (id, status, owner_user_id, created_at, updated_at) VALUES (?, 'queued', ?, ?, ?)",
                     (card_id, owner_user_id, now, now))
        _upsert_card_image(conn, card_id, "front", original_image_path, original_sha256, direction)
        conn.execute("INSERT INTO jobs (id, card_id, type, status, created_at) VALUES (?, ?, 'process_card', 'queued', ?)",
                     (job_id, card_id, now))
        if line_event_id:
            event = conn.execute("SELECT message_id FROM line_events WHERE id = ? AND connection_id IN (SELECT id FROM line_connections WHERE owner_user_id = ?)",
                                 (line_event_id, owner_user_id)).fetchone()
            if event is None:
                raise ValueError("LINEイベントの所有者が一致しません")
            conn.execute("UPDATE cards SET source_system = 'line', source_id = ? WHERE id = ?", (event['message_id'], card_id))
            conn.execute("UPDATE line_events SET card_id = ?, status = 'queued', error_message = NULL, updated_at = ? WHERE id = ?",
                         (card_id, now, line_event_id))
    return job_id



def get_user_owned_card_by_original_sha256(original_sha256: str, user_id: str) -> dict | None:
    if not original_sha256:
        return None
    with connection() as conn:
        row = conn.execute(
            """
            SELECT cards.*
            FROM card_records cards
            JOIN card_images ON card_images.card_id = cards.id
            WHERE card_images.original_sha256 = ?
              AND cards.owner_user_id = ?
            ORDER BY cards.created_at ASC
            LIMIT 1
            """,
            (original_sha256, user_id),
        ).fetchone()
        return _hydrate_card(conn, row)



def finish_line_event(event_id: str, status: str, card_id: str | None = None, error_message: str | None = None) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            """
            UPDATE line_events
            SET status = ?,
                card_id = COALESCE(?, card_id),
                error_message = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (status, card_id, error_message, now, event_id),
        )


def has_users() -> bool:
    with connection() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def get_user_by_login_id(login_id: str) -> dict | None:
    with connection() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM users WHERE login_id = ?", (login_id,)).fetchone()
        )


def get_user_by_id(user_id: str) -> dict | None:
    with connection() as conn:
        return row_to_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def list_users() -> list[dict]:
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY created_at")]


def create_user(login_id: str, password_hash: str, role: str = "user") -> dict:
    user_id = uuid4().hex
    now = now_iso()
    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO users (id, login_id, password_hash, status, role, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?)
                """,
                (user_id, login_id, password_hash, role, now, now),
            )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise ValueError("このログインIDはすでに使われています") from exc
        raise
    return get_user_by_login_id(login_id) or {}


def bootstrap_first_user(login_id: str, password_hash: str) -> dict:
    """Create the local administrator and atomically claim every pre-local-auth card.

    The schema cleanup is intentionally delayed until this point: a pre-existing
    installation remains usable for rollback until the administrator explicitly
    completes setup.
    """
    if has_users():
        raise ValueError("Initial setup is already complete")
    user_id = uuid4().hex
    now = now_iso()
    with connection() as conn:
        existing = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        if existing:
            raise ValueError("Initial setup is already complete")
        conn.execute(
            """
            INSERT INTO users (id, login_id, password_hash, status, role, created_at, updated_at)
            VALUES (?, ?, ?, 'active', 'admin', ?, ?)
            """,
            (user_id, login_id, password_hash, now, now),
        )
        missing = conn.execute(
            "SELECT COUNT(*) AS count FROM cards WHERE owner_user_id IS NULL OR owner_user_id = ''"
        ).fetchone()["count"]
        if missing:
            updated = conn.execute(
                "UPDATE cards SET owner_user_id = ?, updated_at = ? WHERE owner_user_id IS NULL OR owner_user_id = ''",
                (user_id, now),
            ).rowcount
            if updated != missing:
                raise RuntimeError("既存名刺の所有者移行を完了できませんでした")
        conn.execute(
            """
            INSERT INTO line_connections (id, owner_user_id, created_at, updated_at)
            VALUES ('default', ?, ?, ?)
            """,
            (user_id, now, now),
        )
        _remove_legacy_line_auth_schema(conn)
    restore_default_line_identity_from_backup()
    return get_user_by_login_id(login_id) or {}


def _remove_legacy_line_auth_schema(conn) -> None:
    """Remove only obsolete LINE-login schema after all cards have local owners."""
    legacy_card_column = any(
        row["name"] == "owner_line_user_id" for row in conn.execute("PRAGMA table_info(cards)")
    )
    if legacy_card_column:
        conn.execute("DROP INDEX IF EXISTS idx_cards_owner_line_user_id")
        conn.execute("ALTER TABLE cards DROP COLUMN owner_line_user_id")
    if any(row["name"] == "line_user_id" for row in conn.execute("PRAGMA table_info(line_events)")):
        conn.execute("DROP INDEX IF EXISTS idx_line_events_line_user_id")
        conn.execute("ALTER TABLE line_events RENAME COLUMN line_user_id TO line_sender_id")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_events_line_sender_id ON line_events(line_sender_id)")
    conn.execute("DROP TABLE IF EXISTS line_sessions")
    conn.execute("DROP TABLE IF EXISTS line_users")


def create_session(token_hash: str, user_id: str, expires_at: str) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE id = ? AND status = 'active'", (user_id,)).fetchone() is None:
            from fastapi import HTTPException
            raise HTTPException(403, "このユーザーは利用できません")
        conn.execute(
            """
            INSERT INTO sessions (token_hash, user_id, expires_at, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_hash, user_id, expires_at, now, now),
        )


def get_session_user(token_hash: str, now: str) -> dict | None:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.status = 'active'
            """,
            (token_hash, now),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))
        conn.execute(
            "UPDATE users SET last_seen_at = ?, updated_at = ? WHERE id = ?",
            (now, now, row["id"]),
        )
        return dict(row)


def delete_session(token_hash: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def change_user_password(user_id: str, password_hash: str) -> None:
    """Replace a password and invalidate every existing session atomically."""
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def set_user_status(user_id: str, user_status: str) -> dict | None:
    """Change a user's availability and revoke sessions when stopping them."""
    if user_status not in {"active", "stopped"}:
        raise ValueError("Invalid user status")
    now = now_iso()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if existing is None:
            return None
        if existing["role"] == "admin":
            raise ValueError("管理者アカウントは停止できません")
        conn.execute(
            "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
            (user_status, now, user_id),
        )
        if user_status == "stopped":
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return get_user_by_id(user_id)


def delete_user_and_owned_data(user_id: str) -> list[str] | None:
    """Caller holds the user's exclusive lock. Fail closed on ambiguous ownership.

    Database changes are atomic. Filesystem failures leave the database/user in
    place for retry; files already removed from this user cannot be rolled back.
    """
    from .user_deletion import owned_image_directories, remove_owned_images
    with connection() as conn:
        # Also excludes job claiming and new card registration during deletion.
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            return None
        if user["role"] == "admin":
            raise ValueError("管理者アカウントは削除できません")
        if conn.execute("SELECT 1 FROM jobs JOIN cards ON cards.id = jobs.card_id WHERE cards.owner_user_id = ? AND jobs.status = 'running'", (user_id,)).fetchone():
            raise ValueError("名刺を処理中です。完了後に削除を再試行してください")
        if conn.execute("SELECT 1 FROM line_events e JOIN line_connections lc ON lc.id = e.connection_id WHERE lc.owner_user_id = ? AND e.status = 'running'", (user_id,)).fetchone():
            raise ValueError("LINE画像を取り込み中です。完了後に再試行してください")
        directories = owned_image_directories(conn, user_id)
        card_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM cards WHERE owner_user_id = ?", (user_id,)
            ).fetchall()
        ]
        # Connector prefixes include ':' and are matched literally, never LIKE.
        # A conflicting card owner means the association is unsafe to delete.
        event_scope = """EXISTS (SELECT 1 FROM line_connections lc
            WHERE lc.owner_user_id = ? AND substr(line_events.id, 1, length(lc.id) + 1) = lc.id || ':')"""
        if conn.execute(f"SELECT 1 FROM line_events JOIN cards ON cards.id = line_events.card_id WHERE {event_scope} AND cards.owner_user_id IS NOT ?", (user_id, user_id)).fetchone():
            raise ValueError("LINE履歴の所有者が一致しないため削除を中止しました")
        if conn.execute("""SELECT 1 FROM line_events JOIN cards ON cards.id = line_events.card_id
            JOIN line_connections lc ON substr(line_events.id, 1, length(lc.id) + 1) = lc.id || ':'
            WHERE cards.owner_user_id = ? AND lc.owner_user_id != ?""", (user_id, user_id)).fetchone():
            raise ValueError("LINE履歴の所有者が一致しないため削除を中止しました")
        conn.execute(f"DELETE FROM line_events WHERE {event_scope} OR card_id IN (SELECT id FROM cards WHERE owner_user_id = ?)", (user_id, user_id))
        batch_ids = [row[0] for row in conn.execute("SELECT DISTINCT import_batch_id FROM cards WHERE owner_user_id = ? AND import_batch_id IS NOT NULL", (user_id,))]
        if card_ids:
            deleted_cards = conn.execute(
                "DELETE FROM cards WHERE owner_user_id = ?", (user_id,)
            ).rowcount
            if deleted_cards != len(card_ids):
                raise RuntimeError("対象ユーザーの名刺削除を完了できませんでした")

        # Removing a connection cascades only to its own link requests.
        conn.execute("DELETE FROM line_connections WHERE owner_user_id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        deleted_users = conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount
        if deleted_users != 1:
            raise RuntimeError("対象ユーザーの削除を完了できませんでした")
        for batch_id in batch_ids:
            conn.execute("DELETE FROM import_batches WHERE id = ? AND NOT EXISTS (SELECT 1 FROM cards WHERE import_batch_id = ?)", (batch_id, batch_id))
        # Validate/execute every DB constraint before irreversible file removal.
        # An I/O error rolls back DB changes so this operation can be retried.
        remove_owned_images(directories)
    return card_ids


def restore_default_line_identity_from_backup() -> bool:
    """Attempt legacy identity restoration once, never after explicit setup."""
    backup_path = settings.data_dir / "bzcard-before-local-auth.db"
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM line_connections WHERE id = 'default'").fetchone()
        if current is None or current["legacy_identity_checked"]:
            return False
        conn.execute("UPDATE line_connections SET legacy_identity_checked = 1 WHERE id = 'default'")
        if current["line_user_id"] or current["line_login_channel_id"] or not backup_path.exists():
            return False
        import sqlite3

        backup = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        try:
            rows = backup.execute(
                "SELECT line_user_id FROM line_users WHERE status = 'active' ORDER BY created_at"
            ).fetchall()
        except sqlite3.Error:
            return False
        finally:
            backup.close()
        if len(rows) != 1 or not rows[0][0]:
            return False
        conn.execute(
            "UPDATE line_connections SET line_user_id = ?, updated_at = ? WHERE id = 'default'",
            (rows[0][0], now_iso()),
        )
        return True


def get_user_line_connection(user_id: str) -> dict | None:
    with connection() as conn:
        return row_to_dict(conn.execute("SELECT * FROM line_connections WHERE owner_user_id = ?", (user_id,)).fetchone())


def list_line_connections() -> list[dict]:
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM line_connections").fetchall()]


def get_line_connection(connection_id: str) -> dict | None:
    with connection() as conn:
        return row_to_dict(conn.execute("SELECT * FROM line_connections WHERE id = ?", (connection_id,)).fetchone())


def public_line_connection(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "line_user_id_linked": bool(row.get("line_user_id")),
        "line_login_channel_id": row.get("line_login_channel_id") or "",
        "liff_url": row.get("liff_url") or "",
        "configured": bool(row.get("channel_secret_encrypted") and row.get("access_token_encrypted")),
    }


def public_liff_configuration(connection_id: str) -> dict | None:
    """Return only the LIFF application identifier needed by its public web client."""
    row = get_line_connection(connection_id)
    if row is None or not row.get("liff_id") or not row.get("line_login_channel_id"):
        return None
    return {"connection_id": row["id"], "liff_id": row["liff_id"]}


def _liff_id_from_url(value: str) -> str:
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.hostname != "liff.line.me":
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or not re.fullmatch(r"[0-9A-Za-z-]{1,128}", parts[0]):
        return ""
    return parts[0]


def save_user_line_connection(user_id: str, payload: dict) -> dict:
    current = get_user_line_connection(user_id)
    now = now_iso()
    channel_secret = str(payload.get("channel_secret") or "").strip()
    access_token = str(payload.get("access_token") or "").strip()
    login_channel_id = str(payload.get("line_login_channel_id") or "").strip()
    liff_url = str(payload.get("liff_url") or "").strip()
    liff_id = _liff_id_from_url(liff_url)
    if not login_channel_id or not liff_url:
        raise ValueError("LINE LoginチャネルIDとLIFF URLを入力してください")
    if not liff_id:
        raise ValueError("LIFF URLには https://liff.line.me/ で始まるLIFF URLを入力してください")
    if current is None:
        if not channel_secret or not access_token:
            raise ValueError("チャネルシークレットとアクセストークンを入力してください")
        connection_id = secrets.token_hex(16)
        with connection() as conn:
            conn.execute("INSERT INTO line_connections (id, owner_user_id, channel_secret_encrypted, access_token_encrypted, line_login_channel_id, liff_url, liff_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (connection_id, user_id, encrypt(channel_secret), encrypt(access_token), login_channel_id, liff_url, liff_id, now, now))
    else:
        with connection() as conn:
            identity_changed = current.get("line_login_channel_id") != login_channel_id
            conn.execute(
                "UPDATE line_connections SET legacy_identity_checked = 1, channel_secret_encrypted = COALESCE(?, channel_secret_encrypted), access_token_encrypted = COALESCE(?, access_token_encrypted), line_login_channel_id = ?, liff_url = ?, liff_id = ?, line_user_id = CASE WHEN ? THEN NULL ELSE line_user_id END, updated_at = ? WHERE id = ?",
                (encrypt(channel_secret) if channel_secret else None, encrypt(access_token) if access_token else None, login_channel_id, liff_url, liff_id, identity_changed, now, current["id"]),
            )
            if identity_changed:
                conn.execute("DELETE FROM line_link_requests WHERE connection_id = ?", (current["id"],))
    return get_user_line_connection(user_id) or {}


def connection_credentials(connection: dict) -> tuple[str, str]:
    return decrypt(connection.get("channel_secret_encrypted")), decrypt(connection.get("access_token_encrypted"))


def create_line_link_request(connection_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    now = now_iso(); expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="seconds")
    with connection() as conn:
        conn.execute("INSERT INTO line_link_requests (token_hash, connection_id, expires_at, created_at) VALUES (?, ?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), connection_id, expires, now))
    return token, expires


def consume_line_link_request(token: str, connection_id: str) -> bool:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connection() as conn:
        row = conn.execute("SELECT * FROM line_link_requests WHERE token_hash = ? AND connection_id = ? AND expires_at > ?", (digest, connection_id, now_iso())).fetchone()
        if row is None: return False
        conn.execute("DELETE FROM line_link_requests WHERE token_hash = ?", (digest,))
        return True


def bind_line_identity(connection_id: str, line_user_id: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE line_connections SET line_user_id = ?, legacy_identity_checked = 1, updated_at = ? WHERE id = ?", (line_user_id, now_iso(), connection_id))


def backfill_line_connection_liff_ids() -> int:
    """Populate the explicit LIFF ID for connectors saved before multi-user LIFF support."""
    updated = 0
    with connection() as conn:
        rows = conn.execute("SELECT id, liff_url FROM line_connections WHERE (liff_id IS NULL OR liff_id = '') AND liff_url IS NOT NULL").fetchall()
        for row in rows:
            liff_id = _liff_id_from_url(row["liff_url"])
            if liff_id:
                conn.execute("UPDATE line_connections SET liff_id = ?, updated_at = ? WHERE id = ?", (liff_id, now_iso(), row["id"]))
                updated += 1
    return updated


def set_back_image(card_id: str, original_image_path: str, original_sha256: str, direction: str) -> str:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _upsert_card_image(conn, card_id, "back", original_image_path, original_sha256, direction)
        return _enqueue_job(conn, card_id, "process_card")


def _enqueue_job(conn, card_id: str, job_type: str) -> str:
    active = conn.execute("SELECT id FROM jobs WHERE card_id = ? AND status IN ('queued', 'running') ORDER BY created_at LIMIT 1", (card_id,)).fetchone()
    if active:
        return active['id']
    job_id = uuid4().hex
    now = now_iso()
    conn.execute("INSERT INTO jobs (id, card_id, type, status, created_at) VALUES (?, ?, ?, 'queued', ?)", (job_id, card_id, job_type, now))
    conn.execute("UPDATE cards SET status = 'queued', error_message = NULL, updated_at = ? WHERE id = ?", (now, card_id))
    return job_id


def enqueue_job(card_id: str, job_type: str) -> str:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _enqueue_job(conn, card_id, job_type)


def get_active_job(card_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE card_id = ? AND status IN ('queued', 'running')
            ORDER BY jobs.created_at ASC
            LIMIT 1
            """,
            (card_id,),
        ).fetchone()
        return row_to_dict(row)


def get_card(card_id: str) -> dict | None:
    with connection() as conn:
        return _hydrate_card(
            conn,
            conn.execute("SELECT * FROM card_records WHERE id = ?", (card_id,)).fetchone(),
        )


def list_user_cards(user_id: str, q: str | None = None, status: str | None = None) -> list[dict]:
    where = "owner_user_id = ?" + (" AND status = ?" if status else "")
    params = [user_id, status] if status else [user_id]
    tokens = _prepare_contact_search(q)
    with connection() as conn:
        rows = conn.execute("SELECT * FROM card_records WHERE " + where + " ORDER BY created_at DESC", params).fetchall()
        scored = []
        for row in rows:
            card = dict(row)
            parts = _token_search_scores(card, tokens)
            if tokens and not all(parts):
                continue
            scored.append((sum(parts), card['created_at'], _hydrate_card(conn, row, include_images=False)))
    if tokens:
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [card for _score, _created, card in scored]


def list_user_contacts(
    user_id: str,
    q: str | None = None,
    status: str | None = None,
    include_cards: bool = False,
) -> list[dict]:
    """List a user's cards grouped into automatically detected people.

    The underlying cards remain independent records.  This presentation-level
    grouping is deliberately computed on the server so every client applies the
    same matching and search rules.
    """
    where = ["owner_user_id = ?"]
    params: list[str] = [user_id]
    if status:
        where.append("status = ?")
        params.append(status)
    # List responses need no OCR blocks/extraction JSON or image metadata. Search
    # adds OCR text only when needed; keep all cards for cross-card matching.
    columns = set(CONTACT_SUMMARY_FIELDS) | {"id", "email", "mobile", "revision"}
    if q:
        columns.update(SEARCH_FIELDS)
    if include_cards:
        columns.update(CARD_FIELDS)
    projection = "*" if include_cards else ", ".join(sorted(columns))
    sql = "SELECT " + projection + " FROM card_records WHERE " + " AND ".join(where)
    search = _prepare_contact_search(q)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        entries = []
        for row in rows:
            card = dict(row)
            parts = _token_search_scores(card, search)
            card["_token_scores"] = parts
            score = sum(parts)
            for field in LIST_OMITTED_FIELDS:
                card.pop(field, None)
            entries.append((card, score))
    matched = set()
    if search:
        for group in _contact_groups([card for card, _score in entries]):
            if all(any(card["_token_scores"][i] for card in group) for i in range(len(search))):
                matched.update(card["id"] for card in group)
    for card, _score in entries:
        card.pop("_token_scores", None)
    return _contacts_from_entries(entries, q if search else None, include_cards=include_cards, matched_groups=matched)


def get_user_contact(user_id: str, contact_id: str) -> dict | None:
    """Resolve a contact by its representative card ID or any member card ID."""
    with connection() as conn:
        # Only load identifiers to discover the connected group, then retrieve
        # full records for that group. Never materialize every person's details.
        identifiers = [dict(row) for row in conn.execute(
            "SELECT id, email, mobile FROM cards WHERE owner_user_id = ?", (user_id,)
        )]
        members = next((group for group in _contact_groups(identifiers)
                        if any(card["id"] == contact_id for card in group)), None)
        if members is None:
            return None
        ids = [card["id"] for card in members]
        cards = []
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            cards.extend(dict(row) for row in conn.execute(
                f"SELECT * FROM card_records WHERE owner_user_id = ? AND id IN ({placeholders})",
                [user_id, *batch],
            ))
        if not cards:
            return None
        for card in cards:
            for field in LIST_OMITTED_FIELDS:
                card.pop(field, None)
        # Build only the requested person's response, with deterministic ordering.
        contacts = _contacts_from_entries([(card, 0) for card in cards], None, include_cards=True)
        return next((contact for contact in contacts if any(card["id"] == contact_id for card in contact["cards"])), None)


def get_user_card(card_id: str, user_id: str) -> dict | None:
    with connection() as conn:
        return _hydrate_card(
            conn,
            conn.execute(
                "SELECT * FROM card_records WHERE id = ? AND owner_user_id = ?",
                (card_id, user_id),
            ).fetchone(),
        )


def _contact_groups(cards: list[dict]) -> list[list[dict]]:
    """Union each identifier with its first card, avoiding all-pairs comparison."""
    parents = list(range(len(cards)))
    sizes = [1] * len(cards)

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            if sizes[left_root] < sizes[right_root]:
                left_root, right_root = right_root, left_root
            parents[right_root] = left_root
            sizes[left_root] += sizes[right_root]

    by_identifier: dict[tuple[str, str], int] = {}
    for index, card in enumerate(cards):
        for identifier in _contact_identifiers(card):
            join(index, by_identifier.setdefault(identifier, index))

    grouped: dict[int, list[dict]] = {}
    for index, card in enumerate(cards):
        grouped.setdefault(find(index), []).append(card)
    return list(grouped.values())


def _contacts_from_entries(
    entries: list[tuple[dict, int]],
    query: str | None,
    include_cards: bool = False,
    matched_groups: set[str] | None = None,
) -> list[dict]:
    """Group by equal email/mobile, retaining transitive matching and ranking."""
    scores = {card["id"]: score for card, score in entries}

    contacts = []
    for cards in _contact_groups([card for card, _score in entries]):
        if query and not any((card["id"] in matched_groups if matched_groups is not None else scores[card["id"]] > 0) for card in cards):
            continue
        cards.sort(key=lambda card: (card.get("created_at") or "", card["id"]), reverse=True)
        representative = cards[0]
        revision = hashlib.sha256(json.dumps([
            [card["id"], card.get("revision"), card.get("updated_at"), card.get("status")]
            for card in cards
        ]).encode()).hexdigest()[:20]
        contact = {
            **(representative if include_cards else _contact_summary(representative)),
            "id": representative["id"],
            "representative_card_id": representative["id"],
            "card_count": len(cards),
            "revision": revision,
            "has_in_progress": any(card.get("status") not in {"ready", "not_card", "error"} for card in cards),
            "_search_score": max(scores[card["id"]] for card in cards),
        }
        if include_cards:
            contact["cards"] = cards
        contacts.append(contact)
    contacts.sort(
        key=lambda contact: (
            contact.pop("_search_score"),
            contact.get("created_at") or "",
            contact["id"],
        ),
        reverse=True,
    )
    return contacts


def _contact_summary(card: dict) -> dict:
    return {field: card.get(field) for field in CONTACT_SUMMARY_FIELDS}


def _contact_identifiers(card: dict) -> set[tuple[str, str]]:
    identifiers = set()
    email = _contact_email(card.get("email"))
    mobile = _contact_mobile(card.get("mobile"))
    if email:
        identifiers.add(("email", email))
    if mobile:
        identifiers.add(("mobile", mobile))
    return identifiers


def _contact_email(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _contact_mobile(value) -> str:
    return re.sub(r"\D", "", _normalize_phone_number(str(value or "")))


def get_card_images(card_id: str) -> list[dict]:
    with connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM card_images
                WHERE card_id = ?
                ORDER BY CASE side WHEN 'front' THEN 0 WHEN 'back' THEN 1 ELSE 2 END, created_at
                """,
                (card_id,),
            ).fetchall()
        ]


def get_user_job(job_id: str, user_id: str) -> dict | None:
    """Return a job only when its card belongs to the requesting user."""
    with connection() as conn:
        return row_to_dict(
            conn.execute(
                """
                SELECT jobs.*
                FROM jobs
                JOIN cards ON cards.id = jobs.card_id
                WHERE jobs.id = ? AND cards.owner_user_id = ?
                """,
                (job_id, user_id),
            ).fetchone()
        )


def update_card_fields(card_id: str, data: dict) -> dict | None:
    fields = normalize_fields({k: v for k, v in data.items() if k in CARD_FIELDS})
    if not fields:
        return get_card(card_id)
    now = now_iso()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if card is None:
            return None
        record_manual_changes(conn, card, fields)
        assignments = ", ".join(f"{field} = ?" for field in fields)
        conn.execute(f"UPDATE cards SET {assignments}, updated_at = ? WHERE id = ?", list(fields.values()) + [now, card_id])
        _sync_extracted_json(conn, card_id, fields, now)
    return get_card(card_id)


def _sync_extracted_json(conn, card_id: str, fields: dict, now: str) -> None:
    updates = {key: value for key, value in fields.items() if key in EXTRACTED_JSON_FIELDS}
    if not updates:
        return

    row = conn.execute("SELECT extracted_json FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None or not row["extracted_json"]:
        return

    try:
        extracted = json.loads(row["extracted_json"])
    except json.JSONDecodeError:
        return
    if not isinstance(extracted, dict):
        return

    for key, value in updates.items():
        extracted[key] = value or ""

    conn.execute(
        "UPDATE cards SET extracted_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(extracted, ensure_ascii=False), now, card_id),
    )


def set_image_ocr_direction(card_id: str, direction: str, side: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE card_images SET ocr_direction = ?, updated_at = ? WHERE card_id = ? AND side = ?", (direction, now_iso(), card_id, side))


def set_ocr_direction(card_id: str, direction: str) -> None:
    set_image_ocr_direction(card_id, direction, "front")


def set_back_ocr_direction(card_id: str, direction: str) -> None:
    set_image_ocr_direction(card_id, direction, "back")


def save_detected_ocr_direction(card_id: str, direction: str, side: str = "front") -> None:
    set_image_ocr_direction(card_id, direction, side)


def delete_card(card_id: str) -> bool:
    with connection() as conn:
        cur = conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        return cur.rowcount > 0


def normalize_existing_company_names() -> int:
    now = now_iso()
    changed = 0
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, company_name
            FROM cards
            WHERE company_name IS NOT NULL AND company_name != ''
            """
        ).fetchall()
        for row in rows:
            normalized = _normalize_company_name(row["company_name"])
            if normalized == row["company_name"]:
                continue
            conn.execute(
                "UPDATE cards SET company_name = ?, updated_at = ? WHERE id = ?",
                (normalized, now, row["id"]),
            )
            changed += 1
    return changed


def set_card_processing_artifacts(card_id: str, processed_path: str, thumbnail_path: str, side: str = "front") -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute("UPDATE card_images SET processed_image_path = ?, thumbnail_path = ?, updated_at = ? WHERE card_id = ? AND side = ?",
                     (processed_path, thumbnail_path, now, card_id, side))
        conn.execute("UPDATE cards SET status = 'scanning', error_message = NULL, updated_at = ? WHERE id = ?", (now, card_id))


def set_card_status(card_id: str, status: str, error_message: str | None = None) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE cards SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, error_message, now, card_id),
        )


def update_image_orientation_metadata(card_id: str, side: str, degrees: int, thumbnail_path: str) -> dict | None:
    with connection() as conn:
        conn.execute("UPDATE card_images SET manual_rotation = (manual_rotation + ? + 360) % 360, thumbnail_path = ?, updated_at = ? WHERE card_id = ? AND side = ?",
                     (degrees, thumbnail_path, now_iso(), card_id, side))
    return get_card(card_id)


def save_auto_rotation(card_id: str, side: str, degrees: int) -> None:
    with connection() as conn:
        conn.execute("UPDATE card_images SET auto_rotation = (auto_rotation + ? + 360) % 360, updated_at = ? WHERE card_id = ? AND side = ?",
                     (degrees, now_iso(), card_id, side))


def save_ocr_result(card_id: str, raw_text: str, blocks: list[dict], duration_ms: int, side: str = "front") -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute("UPDATE card_images SET ocr_text = ?, ocr_blocks_json = ?, ocr_duration_ms = ?, updated_at = ? WHERE card_id = ? AND side = ?",
                     (raw_text, json.dumps(blocks, ensure_ascii=False), duration_ms, now, card_id, side))
        conn.execute("UPDATE cards SET status = 'extracting', updated_at = ? WHERE id = ?", (now, card_id))


def save_extraction_result(card_id: str, extracted: dict, duration_ms: int,
                           ocr_text: str = "", blocks: list[dict] | None = None) -> None:
    from ..config import settings
    values = normalize_fields({key: extracted.get(key) or "" for key in SCHEMA_KEYS})
    raw = extracted.get("_raw") or {}
    automatic = normalize_fields(extracted.get("_automatic") or values)
    final = {**extracted, **values}
    now, run_id = now_iso(), uuid4().hex
    model = {"provider": settings.llm_provider, "model": settings.llm_model if settings.llm_provider == "ollama" else settings.gemini_model,
             "ocr": "yomitoku", "ocr_version": "0.14.0", "ocr_recognizer_model": settings.ocr_recognizer_model, **(extracted.get("_model") or {})}
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        card = conn.execute("SELECT owner_user_id FROM cards WHERE id = ?", (card_id,)).fetchone()
        if card is None:
            return
        dump = lambda data: json.dumps(data, ensure_ascii=False)
        conn.execute("""INSERT INTO extraction_runs (id, card_id, owner_user_id, ocr_text, ocr_blocks_json,
            model_json, pipeline_version, raw_json, automatic_json, result_json, feedback_json, created_at, response_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, card_id, card["owner_user_id"], ocr_text, dump(blocks or []), dump(model), PIPELINE_VERSION,
             dump(raw), dump(automatic), dump(values), dump(extracted.get("_feedback") or {}), now, extracted.get("_response_text") or ""))
        assignments = ", ".join(f"{key} = ?" for key in values)
        # Preserve the approved behavior: completion replaces current manual fields.
        conn.execute(f"UPDATE cards SET {assignments}, status = 'ready', extracted_json = ?, latest_extraction_id = ?, extraction_duration_ms = ?, error_message = NULL, updated_at = ? WHERE id = ?",
                     list(values.values()) + [dump(final), run_id, duration_ms, now, card_id])


def _query_variants(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).strip()
    bases = [value.strip(), normalized, _remove_spaces(normalized)]
    variants = []
    for base in bases:
        variants.extend([base, _katakana_to_hiragana(base), _hiragana_to_katakana(base)])
    result: list[str] = []
    for variant in variants:
        if variant and variant not in result:
            result.append(variant)
    return result


def _search_score(row, query: str | None) -> int:
    return _contact_search_score(dict(row), _prepare_contact_search(query))


def _prepare_contact_search(query: str | None) -> list[list[tuple[str, str]]]:
    if not query:
        return []
    return [list({(normalized, _remove_search_separators(normalized))
                  for variant in _query_variants(token)
                  if (normalized := _search_normalize(variant))})
            for token in _query_tokens(query)]


def _token_search_scores(card: dict, tokens: list[list[tuple[str, str]]]) -> list[int]:
    fields = []
    for field in SEARCH_FIELDS:
        value = _search_normalize(card.get(field))
        if value:
            fields.append((value, _remove_search_separators(value), _search_field_weight(field)))
    scores = []
    for variants in tokens:
        best = 0
        for token, compact_token in variants:
            for value, compact_value, weight in fields:
                compact_match = bool(compact_token)
                if value == token or (compact_match and compact_value == compact_token):
                    match = weight + 1000
                elif value.startswith(token) or (compact_match and compact_value.startswith(compact_token)):
                    match = weight + 600
                elif token in value or (compact_match and compact_token in compact_value):
                    match = weight + 100
                else:
                    match = 0
                best = max(best, match)
        scores.append(best)
    return scores


def _contact_search_score(card: dict, tokens: list[list[tuple[str, str]]]) -> int:
    return sum(_token_search_scores(card, tokens))


def _search_field_weight(field: str) -> int:
    if field == "person_name":
        return 500
    if field == "person_name_kana":
        return 450
    if field == "company_name":
        return 350
    if field in {"department", "title", "tags"}:
        return 250
    if field in {"email", "tel", "mobile", "fax", "website"}:
        return 200
    if field in {"ocr_text", "back_ocr_text"}:
        return 50
    return 100


def _search_normalize(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _query_tokens(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", query).strip()
    tokens = []
    for token in re.split(r"\s+", normalized):
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _remove_spaces(value: str) -> str:
    return "".join(value.split())


SEARCH_SEPARATOR_CHARS = (" ", "　", "-", "‐", "‑", "‒", "–", "—", "―", "−", "ー", "ｰ", "－")


def _remove_search_separators(value: str) -> str:
    text = _remove_spaces(value)
    for char in SEARCH_SEPARATOR_CHARS:
        if char.strip():
            text = text.replace(char, "")
    return text


def claim_next_job() -> dict | None:
    now = now_iso()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT jobs.* FROM jobs JOIN cards ON cards.id = jobs.card_id JOIN users ON users.id = cards.owner_user_id
            WHERE jobs.status = 'queued'
            ORDER BY jobs.created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            """
            UPDATE jobs
            SET status = 'running', attempts = attempts + 1, started_at = ?, error_message = NULL
            WHERE id = ?
            """,
            (now, row["id"]),
        )
        conn.commit()
        job = dict(row)
        job["status"] = "running"
        job["attempts"] = row["attempts"] + 1
        return job
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_job(job_id: str) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'done', finished_at = ?, error_message = NULL WHERE id = ?",
            (now, job_id),
        )


def fail_job(job_id: str, card_id: str, message: str) -> None:
    now = now_iso()
    with connection() as conn:
        row = conn.execute("SELECT attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
        retry = row is not None and row["attempts"] < 3
        conn.execute("UPDATE jobs SET status = ?, finished_at = ?, error_message = ? WHERE id = ?",
                     ("queued" if retry else "error", None if retry else now, message, job_id))
        conn.execute("UPDATE cards SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                     ("queued" if retry else "error", message, now, card_id))


def recover_interrupted_work() -> None:
    """Only call while holding the worker leader lock; no other worker is alive."""
    now = now_iso()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for job in conn.execute("SELECT * FROM jobs WHERE status = 'running'").fetchall():
            status = "queued" if job["attempts"] < 3 else "error"
            message = "前回の処理が中断されました" if status == "queued" else "処理が繰り返し中断されました。再処理してください"
            conn.execute("UPDATE jobs SET status = ?, error_message = ?, finished_at = ? WHERE id = ?", (status, message, None if status == "queued" else now, job["id"]))
            conn.execute("UPDATE cards SET status = ?, error_message = ?, updated_at = ? WHERE id = ?", (status, message, now, job["card_id"]))
        conn.execute("UPDATE line_events SET status = CASE WHEN attempts < 3 THEN 'pending' ELSE 'error' END, available_at = ?, updated_at = ? WHERE status = 'running'", (now, now))


def queue_line_events(events: list[dict], connector: dict) -> None:
    """Acknowledge only after all events are durably registered."""
    now = now_iso()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for event in events:
            message = event.get("message") or {}
            raw_id = event.get("webhookEventId") or message.get("id")
            if not raw_id:
                continue
            event_id = f"{connector['id']}:{raw_id}"
            payload = json.dumps(event, ensure_ascii=False)
            conn.execute("""INSERT OR IGNORE INTO line_events (id, event_type, line_sender_id, message_id,
                connection_id, payload_json, status, available_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (event_id, event.get("type"), (event.get("source") or {}).get("userId"), message.get("id"),
                 connector["id"], payload, now, now, now))
            # Legacy failed events had no recoverable payload. Redelivery repairs
            # them once; exhausted new events remain terminal until a new upload.
            conn.execute("""UPDATE line_events SET connection_id = ?, payload_json = ?, status = 'pending',
                available_at = ?, updated_at = ? WHERE id = ? AND status IN ('error', 'received')
                AND payload_json IS NULL AND card_id IS NULL""", (connector["id"], payload, now, now, event_id))


def claim_next_line_event() -> dict | None:
    now = now_iso()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM line_events WHERE status = 'pending' AND available_at <= ? ORDER BY created_at, rowid LIMIT 1", (now,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE line_events SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?", (now, row["id"]))
        return {**dict(row), "status": "running", "attempts": row["attempts"] + 1}


def reserve_line_card(event_id: str) -> str:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT pending_card_id FROM line_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise ValueError("LINEイベントが削除されました")
        card_id = row["pending_card_id"] or uuid4().hex
        conn.execute("UPDATE line_events SET pending_card_id = ? WHERE id = ?", (card_id, event_id))
        return card_id


def retry_line_event(event: dict, error: str) -> None:
    from datetime import datetime, timedelta, timezone
    terminal = event["attempts"] >= 3
    available = (datetime.now(timezone.utc) + timedelta(seconds=2 ** event["attempts"])).isoformat(timespec="seconds")
    with connection() as conn:
        conn.execute("UPDATE line_events SET status = ?, error_message = ?, available_at = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                     ("error" if terminal else "pending", error[:2000], available, now_iso(), event["id"]))
