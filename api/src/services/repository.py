from __future__ import annotations

import json
import re
import unicodedata
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

from ..config import settings
from ..database import connection, get_connection, row_to_dict
from .timeutil import now_iso
from .secret_store import encrypt, decrypt


CARD_FIELDS = {
    "person_name",
    "person_name_kana",
    "company_name",
    "department",
    "title",
    "postal_code",
    "address",
    "tel",
    "mobile",
    "fax",
    "email",
    "website",
    "tags",
    "memo",
}

SEARCH_FIELDS = [
    "person_name",
    "person_name_kana",
    "company_name",
    "department",
    "title",
    "postal_code",
    "address",
    "tel",
    "mobile",
    "fax",
    "email",
    "website",
    "tags",
    "memo",
    "ocr_text",
    "back_ocr_text",
]

EXTRACTED_JSON_FIELDS = {
    "person_name",
    "person_name_kana",
    "company_name",
    "department",
    "title",
    "postal_code",
    "address",
    "tel",
    "mobile",
    "fax",
    "email",
    "website",
}

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


def _image_metadata(relative: str) -> tuple[int | None, int | None, int | None]:
    path = settings.data_dir / relative
    if not path.exists():
        return None, None, None
    file_size = path.stat().st_size
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        return width, height, file_size
    except Exception:
        return None, None, file_size


def _upsert_card_image(
    conn,
    card_id: str,
    side: str,
    original_image_path: str,
    original_sha256: str,
    direction: str,
) -> None:
    now = now_iso()
    width, height, file_size = _image_metadata(original_image_path)
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
    for image in images:
        _apply_image_to_card(card, image)
    return card


def _apply_image_to_card(card: dict, image: dict) -> None:
    if image["side"] == "back":
        prefix = "back_"
    elif image["side"] == "front":
        prefix = ""
    else:
        return
    for field in (
        "original_sha256",
        "original_image_path",
        "processed_image_path",
        "thumbnail_path",
        "ocr_direction",
        "ocr_text",
        "ocr_blocks_json",
        "ocr_duration_ms",
    ):
        card[f"{prefix}{field}"] = image.get(field)


def create_card(
    card_id: str,
    original_image_path: str,
    original_sha256: str,
    direction: str,
    owner_user_id: str,
) -> str:
    now = now_iso()
    job_id = uuid4().hex
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO cards (
                id, status, original_image_path, original_sha256, ocr_direction,
                owner_user_id, created_at, updated_at
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (card_id, original_image_path, original_sha256, direction, owner_user_id, now, now),
        )
        _upsert_card_image(conn, card_id, "front", original_image_path, original_sha256, direction)
        conn.execute(
            """
            INSERT INTO jobs (id, card_id, type, status, created_at)
            VALUES (?, ?, 'process_card', 'queued', ?)
            """,
            (job_id, card_id, now),
        )
    return job_id


def set_card_source(
    card_id: str,
    source_system: str,
    source_id: str,
    source_filename: str | None = None,
) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            """
            UPDATE cards
            SET source_system = ?,
                source_id = ?,
                source_filename = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (source_system, source_id, source_filename, now, card_id),
        )


def get_user_owned_card_by_original_sha256(original_sha256: str, user_id: str) -> dict | None:
    if not original_sha256:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT cards.*
            FROM cards
            JOIN card_images ON card_images.card_id = cards.id
            WHERE card_images.original_sha256 = ?
              AND cards.owner_user_id = ?
            ORDER BY cards.created_at ASC
            LIMIT 1
            """,
            (original_sha256, user_id),
        ).fetchone()
        return _hydrate_card(conn, row)


def claim_line_event(
    event_id: str,
    event_type: str | None,
    line_sender_id: str | None,
    message_id: str | None,
) -> bool:
    now = now_iso()
    with connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(line_events)")}
        sender_column = "line_sender_id" if "line_sender_id" in columns else "line_user_id"
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO line_events (
                id, event_type, {sender_column}, message_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'received', ?, ?)
            """,
            (event_id, event_type, line_sender_id, message_id, now, now),
        )
        return cursor.rowcount > 0


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
    with get_connection() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def get_user_by_login_id(login_id: str) -> dict | None:
    with get_connection() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM users WHERE login_id = ?", (login_id,)).fetchone()
        )


def get_user_by_id(user_id: str) -> dict | None:
    with get_connection() as conn:
        return row_to_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def list_users() -> list[dict]:
    with get_connection() as conn:
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


def get_default_line_connection() -> dict | None:
    with get_connection() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM line_connections WHERE id = 'default'").fetchone()
        )


def restore_default_line_identity_from_backup() -> bool:
    """Recover the one previous LINE identity only when migration backup is unambiguous."""
    backup_path = settings.data_dir / "bzcard-before-local-auth.db"
    if not backup_path.exists():
        return False
    with connection() as conn:
        current = conn.execute("SELECT * FROM line_connections WHERE id = 'default'").fetchone()
        if current is None or current["line_user_id"]:
            return False
        import sqlite3

        backup = sqlite3.connect(backup_path)
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


def set_default_line_connection_owner(user_id: str) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE line_connections SET owner_user_id = ?, updated_at = ? WHERE id = 'default'",
            (user_id, now),
        )


def get_user_line_connection(user_id: str) -> dict | None:
    with get_connection() as conn:
        return row_to_dict(conn.execute("SELECT * FROM line_connections WHERE owner_user_id = ?", (user_id,)).fetchone())


def list_line_connections() -> list[dict]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM line_connections").fetchall()]


def get_line_connection(connection_id: str) -> dict | None:
    with get_connection() as conn:
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
                "UPDATE line_connections SET channel_secret_encrypted = COALESCE(?, channel_secret_encrypted), access_token_encrypted = COALESCE(?, access_token_encrypted), line_login_channel_id = ?, liff_url = ?, liff_id = ?, line_user_id = CASE WHEN ? THEN NULL ELSE line_user_id END, updated_at = ? WHERE id = ?",
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
        conn.execute("UPDATE line_connections SET line_user_id = ?, updated_at = ? WHERE id = ?", (line_user_id, now_iso(), connection_id))


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


def get_card_by_original_sha256(original_sha256: str) -> dict | None:
    if not original_sha256:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT cards.*
            FROM cards
            JOIN card_images ON card_images.card_id = cards.id
            WHERE card_images.original_sha256 = ?
            ORDER BY cards.created_at ASC
            LIMIT 1
            """,
            (original_sha256,),
        ).fetchone()
        if row is not None:
            return _hydrate_card(conn, row)
        row = conn.execute(
            """
            SELECT * FROM cards
            WHERE original_sha256 = ? OR back_original_sha256 = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (original_sha256, original_sha256),
        ).fetchone()
        return _hydrate_card(conn, row)


def set_back_image(card_id: str, original_image_path: str, original_sha256: str, direction: str) -> str:
    now = now_iso()
    job_id = uuid4().hex
    with connection() as conn:
        _upsert_card_image(conn, card_id, "back", original_image_path, original_sha256, direction)
        conn.execute(
            """
            UPDATE cards
            SET status = 'queued',
                back_original_image_path = ?,
                back_original_sha256 = ?,
                back_processed_image_path = NULL,
                back_thumbnail_path = NULL,
                back_ocr_direction = ?,
                back_ocr_text = NULL,
                back_ocr_blocks_json = NULL,
                back_ocr_duration_ms = NULL,
                error_message = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (original_image_path, original_sha256, direction, now, card_id),
        )
        conn.execute(
            """
            INSERT INTO jobs (id, card_id, type, status, created_at)
            VALUES (?, ?, 'process_card', 'queued', ?)
            """,
            (job_id, card_id, now),
        )
    return job_id


def enqueue_job(card_id: str, job_type: str) -> str:
    job_id = uuid4().hex
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "INSERT INTO jobs (id, card_id, type, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            (job_id, card_id, job_type, now),
        )
        conn.execute(
            "UPDATE cards SET status = 'queued', error_message = NULL, updated_at = ? WHERE id = ?",
            (now, card_id),
        )
    return job_id


def get_active_job(card_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE card_id = ? AND status IN ('queued', 'running')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (card_id,),
        ).fetchone()
        return row_to_dict(row)


def list_cards(q: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM cards"
    where = []
    params: list[str] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        search_clause, search_params = _search_filter(q)
        if search_clause:
            where.append(search_clause)
            params.extend(search_params)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return _cards_from_rows(conn, rows, q)


def get_card(card_id: str) -> dict | None:
    with get_connection() as conn:
        return _hydrate_card(
            conn,
            conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone(),
        )


def list_user_cards(user_id: str, q: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM cards"
    where = ["owner_user_id = ?"]
    params: list[str] = [user_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        search_clause, search_params = _search_filter(q)
        if search_clause:
            where.append(search_clause)
            params.extend(search_params)
    sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return _cards_from_rows(conn, rows, q)


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
    sql = "SELECT * FROM cards WHERE " + " AND ".join(where) + " ORDER BY created_at DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        entries = []
        for row in rows:
            card = _hydrate_card(conn, row, include_images=False)
            if card is not None:
                entries.append((card, _search_score(row, q) if q else 0))
    return _contacts_from_entries(entries, q, include_cards=include_cards)


def get_user_contact(user_id: str, contact_id: str) -> dict | None:
    """Resolve a contact by its representative card ID or any member card ID."""
    for contact in list_user_contacts(user_id, include_cards=True):
        if contact["id"] == contact_id or any(card["id"] == contact_id for card in contact["cards"]):
            return contact
    return None


def get_user_card(card_id: str, user_id: str) -> dict | None:
    with get_connection() as conn:
        return _hydrate_card(
            conn,
            conn.execute(
                "SELECT * FROM cards WHERE id = ? AND owner_user_id = ?",
                (card_id, user_id),
            ).fetchone(),
        )


def _contacts_from_entries(
    entries: list[tuple[dict, int]],
    query: str | None,
    include_cards: bool = False,
) -> list[dict]:
    """Build connected groups from exact contact identifiers.

    An equal email address or mobile number is enough to identify a person.
    A name and company alone are deliberately not enough: people with the same
    name can work at the same company, so those cases stay separate until a
    future explicit merge workflow is added.
    """
    parents = list(range(len(entries)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    by_identifier: dict[tuple[str, str], list[int]] = {}
    for index, (card, _score) in enumerate(entries):
        for identifier in _contact_identifiers(card):
            by_identifier.setdefault(identifier, []).append(index)

    for indices in by_identifier.values():
        for left_offset, left in enumerate(indices):
            for right in indices[left_offset + 1:]:
                if _cards_are_same_contact(entries[left][0], entries[right][0]):
                    join(left, right)

    grouped: dict[int, list[tuple[dict, int]]] = {}
    for index, entry in enumerate(entries):
        grouped.setdefault(find(index), []).append(entry)

    contacts = []
    for members in grouped.values():
        if query and not any(score > 0 for _card, score in members):
            continue
        members.sort(key=lambda item: (item[0].get("created_at") or "", item[0]["id"]), reverse=True)
        representative = members[0][0]
        cards = [card for card, _score in members]
        contact = {
            **(representative if include_cards else _contact_summary(representative)),
            "id": representative["id"],
            "representative_card_id": representative["id"],
            "card_count": len(cards),
            "_search_score": max(score for _card, score in members),
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


def _cards_are_same_contact(left: dict, right: dict) -> bool:
    left_email, right_email = _contact_email(left.get("email")), _contact_email(right.get("email"))
    left_mobile, right_mobile = _contact_mobile(left.get("mobile")), _contact_mobile(right.get("mobile"))
    if left_email and left_email == right_email:
        return True
    if left_mobile and left_mobile == right_mobile:
        return True
    return False


def _contact_email(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _contact_mobile(value) -> str:
    return re.sub(r"\D", "", _normalize_phone_number(str(value or "")))


def get_card_images(card_id: str) -> list[dict]:
    with get_connection() as conn:
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


def get_card_image(card_id: str, side: str) -> dict | None:
    with get_connection() as conn:
        return row_to_dict(
            conn.execute(
                "SELECT * FROM card_images WHERE card_id = ? AND side = ?",
                (card_id, side),
            ).fetchone()
        )


def get_job(job_id: str) -> dict | None:
    with get_connection() as conn:
        return row_to_dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def get_user_job(job_id: str, user_id: str) -> dict | None:
    """Return a job only when its card belongs to the requesting user."""
    with get_connection() as conn:
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
    fields = {k: v for k, v in data.items() if k in CARD_FIELDS}
    if not fields:
        return get_card(card_id)
    if "person_name_kana" in fields:
        fields["person_name_kana"] = _normalize_kana_field(fields["person_name_kana"])
    if "address" in fields:
        fields["address"] = _normalize_address(fields["address"])
    if "company_name" in fields:
        fields["company_name"] = _normalize_company_name(fields["company_name"])
    if "tags" in fields:
        fields["tags"] = _normalize_tags(fields["tags"])
    for phone_field in ("tel", "mobile", "fax"):
        if phone_field in fields:
            fields[phone_field] = _normalize_phone_number(fields[phone_field])

    now = now_iso()
    assignments = ", ".join(f"{field} = ?" for field in fields)
    params = list(fields.values()) + [now, card_id]
    with connection() as conn:
        conn.execute(
            f"UPDATE cards SET {assignments}, updated_at = ? WHERE id = ?",
            params,
        )
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
    raw = extracted.get("_raw")
    if isinstance(raw, dict):
        for key, value in updates.items():
            raw[key] = value or ""

    conn.execute(
        "UPDATE cards SET extracted_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(extracted, ensure_ascii=False), now, card_id),
    )


def set_ocr_direction(card_id: str, direction: str) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE cards SET ocr_direction = ?, updated_at = ? WHERE id = ?",
            (direction, now, card_id),
        )
        conn.execute(
            "UPDATE card_images SET ocr_direction = ?, updated_at = ? WHERE card_id = ? AND side = 'front'",
            (direction, now, card_id),
        )


def set_back_ocr_direction(card_id: str, direction: str) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE cards SET back_ocr_direction = ?, updated_at = ? WHERE id = ?",
            (direction, now, card_id),
        )
        conn.execute(
            "UPDATE card_images SET ocr_direction = ?, updated_at = ? WHERE card_id = ? AND side = 'back'",
            (direction, now, card_id),
        )


def save_detected_ocr_direction(card_id: str, direction: str, side: str = "front") -> None:
    if side == "back":
        set_back_ocr_direction(card_id, direction)
        return
    set_ocr_direction(card_id, direction)


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


def set_card_processing_artifacts(
    card_id: str,
    processed_path: str,
    thumbnail_path: str,
    side: str = "front",
) -> None:
    now = now_iso()
    if side == "back":
        with connection() as conn:
            conn.execute(
                """
                UPDATE card_images
                SET processed_image_path = ?,
                    thumbnail_path = ?,
                    updated_at = ?
                WHERE card_id = ? AND side = 'back'
                """,
                (processed_path, thumbnail_path, now, card_id),
            )
            conn.execute(
                """
                UPDATE cards
                SET status = 'ocr_processing',
                    back_processed_image_path = ?,
                    back_thumbnail_path = ?,
                    error_message = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (processed_path, thumbnail_path, now, card_id),
            )
        return
    with connection() as conn:
        conn.execute(
            """
            UPDATE card_images
            SET processed_image_path = ?,
                thumbnail_path = ?,
                updated_at = ?
            WHERE card_id = ? AND side = 'front'
            """,
            (processed_path, thumbnail_path, now, card_id),
        )
        conn.execute(
            """
            UPDATE cards
            SET status = 'ocr_processing',
                processed_image_path = ?,
                thumbnail_path = ?,
                error_message = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (processed_path, thumbnail_path, now, card_id),
        )


def set_card_status(card_id: str, status: str, error_message: str | None = None) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE cards SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, error_message, now, card_id),
        )


def update_image_orientation_metadata(
    card_id: str,
    side: str,
    original_sha256: str | None,
    thumbnail_path: str,
    width: int | None,
    height: int | None,
    file_size: int | None,
    original_changed: bool,
) -> dict | None:
    now = now_iso()
    with connection() as conn:
        if side == "back":
            conn.execute(
                """
                UPDATE card_images
                SET thumbnail_path = ?,
                    original_sha256 = CASE WHEN ? THEN ? ELSE original_sha256 END,
                    width = CASE WHEN ? THEN ? ELSE width END,
                    height = CASE WHEN ? THEN ? ELSE height END,
                    file_size = CASE WHEN ? THEN ? ELSE file_size END,
                    updated_at = ?
                WHERE card_id = ? AND side = 'back'
                """,
                (
                    thumbnail_path,
                    original_changed,
                    original_sha256,
                    original_changed,
                    width,
                    original_changed,
                    height,
                    original_changed,
                    file_size,
                    now,
                    card_id,
                ),
            )
            conn.execute(
                """
                UPDATE cards
                SET back_thumbnail_path = ?,
                    back_original_sha256 = CASE WHEN ? THEN ? ELSE back_original_sha256 END,
                    updated_at = ?
                WHERE id = ?
                """,
                (thumbnail_path, original_changed, original_sha256, now, card_id),
            )
        else:
            conn.execute(
                """
                UPDATE card_images
                SET thumbnail_path = ?,
                    original_sha256 = CASE WHEN ? THEN ? ELSE original_sha256 END,
                    width = CASE WHEN ? THEN ? ELSE width END,
                    height = CASE WHEN ? THEN ? ELSE height END,
                    file_size = CASE WHEN ? THEN ? ELSE file_size END,
                    updated_at = ?
                WHERE card_id = ? AND side = 'front'
                """,
                (
                    thumbnail_path,
                    original_changed,
                    original_sha256,
                    original_changed,
                    width,
                    original_changed,
                    height,
                    original_changed,
                    file_size,
                    now,
                    card_id,
                ),
            )
            conn.execute(
                """
                UPDATE cards
                SET thumbnail_path = ?,
                    original_sha256 = CASE WHEN ? THEN ? ELSE original_sha256 END,
                    updated_at = ?
                WHERE id = ?
                """,
                (thumbnail_path, original_changed, original_sha256, now, card_id),
            )
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    with get_connection() as conn:
        return _hydrate_card(conn, row)


def save_ocr_result(
    card_id: str,
    raw_text: str,
    blocks: list[dict],
    duration_ms: int,
    side: str = "front",
) -> None:
    now = now_iso()
    if side == "back":
        with connection() as conn:
            conn.execute(
                """
                UPDATE card_images
                SET ocr_text = ?,
                    ocr_blocks_json = ?,
                    ocr_duration_ms = ?,
                    updated_at = ?
                WHERE card_id = ? AND side = 'back'
                """,
                (raw_text, json.dumps(blocks, ensure_ascii=False), duration_ms, now, card_id),
            )
            conn.execute(
                """
                UPDATE cards
                SET status = 'extracting',
                    back_ocr_text = ?,
                    back_ocr_blocks_json = ?,
                    back_ocr_duration_ms = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (raw_text, json.dumps(blocks, ensure_ascii=False), duration_ms, now, card_id),
            )
        return
    with connection() as conn:
        conn.execute(
            """
            UPDATE card_images
            SET ocr_text = ?,
                ocr_blocks_json = ?,
                ocr_duration_ms = ?,
                updated_at = ?
            WHERE card_id = ? AND side = 'front'
            """,
            (raw_text, json.dumps(blocks, ensure_ascii=False), duration_ms, now, card_id),
        )
        conn.execute(
            """
            UPDATE cards
            SET status = 'extracting',
                ocr_text = ?,
                ocr_blocks_json = ?,
                ocr_duration_ms = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (raw_text, json.dumps(blocks, ensure_ascii=False), duration_ms, now, card_id),
        )


def save_extraction_result(
    card_id: str,
    extracted: dict,
    duration_ms: int,
) -> None:
    now = now_iso()
    values = {
        "person_name": extracted.get("person_name") or extracted.get("name") or "",
        "person_name_kana": _normalize_kana_field(extracted.get("person_name_kana") or ""),
        "company_name": _normalize_company_name(extracted.get("company_name") or extracted.get("company") or ""),
        "department": extracted.get("department") or "",
        "title": extracted.get("title") or extracted.get("position") or "",
        "postal_code": extracted.get("postal_code") or "",
        "address": _normalize_address(extracted.get("address") or ""),
        "tel": _normalize_phone_number(extracted.get("tel") or extracted.get("phone") or ""),
        "mobile": _normalize_phone_number(extracted.get("mobile") or ""),
        "fax": _normalize_phone_number(extracted.get("fax") or ""),
        "email": extracted.get("email") or "",
        "website": extracted.get("website") or extracted.get("url") or "",
    }
    with connection() as conn:
        conn.execute(
            """
            UPDATE cards
            SET status = 'ready',
                extracted_json = ?,
                person_name = ?,
                person_name_kana = ?,
                company_name = ?,
                department = ?,
                title = ?,
                postal_code = ?,
                address = ?,
                tel = ?,
                mobile = ?,
                fax = ?,
                email = ?,
                website = ?,
                extraction_duration_ms = ?,
                error_message = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(extracted, ensure_ascii=False),
                values["person_name"],
                values["person_name_kana"],
                values["company_name"],
                values["department"],
                values["title"],
                values["postal_code"],
                values["address"],
                values["tel"],
                values["mobile"],
                values["fax"],
                values["email"],
                values["website"],
                duration_ms,
                now,
                card_id,
            ),
        )


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


def _cards_from_rows(conn, rows, query: str | None) -> list[dict]:
    scored_cards = []
    for row in rows:
        card = _hydrate_card(conn, row, include_images=False)
        if card is None:
            continue
        score = _search_score(row, query) if query else 0
        scored_cards.append((score, row["created_at"] or "", card))

    if query:
        scored_cards.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [card for _score, _created_at, card in scored_cards]


def _search_score(row, query: str | None) -> int:
    if not query:
        return 0

    score = 0
    for token in _query_tokens(query):
        token_score = 0
        for variant in _query_variants(token):
            for field in SEARCH_FIELDS:
                token_score = max(
                    token_score,
                    _field_match_score(row[field], variant, _search_field_weight(field)),
                )
        score += token_score
    return score


def _field_match_score(value, variant: str, weight: int) -> int:
    field = _search_normalize(value)
    token = _search_normalize(variant)
    if not field or not token:
        return 0

    compact_field = _remove_search_separators(field)
    compact_token = _remove_search_separators(token)
    if field == token or compact_field == compact_token:
        return weight + 1000
    if field.startswith(token) or compact_field.startswith(compact_token):
        return weight + 600
    if token in field or compact_token in compact_field:
        return weight + 100
    return 0


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


def _search_filter(query: str) -> tuple[str, list[str]]:
    token_clauses = []
    params: list[str] = []

    for token in _query_tokens(query):
        variants = _query_variants(token)
        if not variants:
            continue

        expressions = []
        for field in SEARCH_FIELDS:
            compact_field = _sql_remove_search_separators(field)
            for variant in variants:
                expressions.append(f"{field} LIKE ?")
                params.append(f"%{variant}%")
                expressions.append(f"{compact_field} LIKE ?")
                params.append(f"%{_remove_search_separators(variant)}%")
        token_clauses.append("(" + " OR ".join(expressions) + ")")

    if not token_clauses:
        return "", []
    return "(" + " AND ".join(token_clauses) + ")", params


def _query_tokens(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", query).strip()
    tokens = []
    for token in re.split(r"\s+", normalized):
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _normalize_kana_field(value) -> str:
    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())
    if not text:
        return ""
    if not any(_is_kana(char) for char in text):
        return ""
    return _katakana_to_hiragana(text)


def _normalize_tags(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    raw_tags = re.split(r"[,、\n\r]+", text)
    tags: list[str] = []
    for raw_tag in raw_tags:
        tag = re.sub(r"\s+", " ", raw_tag.strip().lstrip("#")).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return ", ".join(tags)


def _normalize_company_name(value) -> str:
    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())
    if not text:
        return ""
    corporate_types = (
        "株式会社",
        "有限会社",
        "合同会社",
        "合名会社",
        "合資会社",
        "医療法人",
        "学校法人",
        "社会福祉法人",
        "一般社団法人",
        "公益社団法人",
        "一般財団法人",
        "公益財団法人",
        "特定非営利活動法人",
    )
    types_pattern = "|".join(map(re.escape, sorted(corporate_types, key=len, reverse=True)))
    text = re.sub(rf"^({types_pattern})\s*(?=\S)", r"\1 ", text)
    text = re.sub(rf"(?<=\S)\s*({types_pattern})$", r" \1", text)
    return text


def _normalize_phone_number(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    text = text.replace("(", "-").replace(")", "-")
    text = text.replace("[", "-").replace("]", "-")
    text = text.replace("（", "-").replace("）", "-")
    text = text.replace("ー", "-").replace("－", "-").replace("―", "-")
    text = re.sub(r"[^0-9+\-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    text = re.sub(r"^\+?81-?0?", "0", text)
    return text


def _normalize_address(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""

    kanji_digits = "〇零一二三四五六七八九十百千万壱弐参"

    def replace_match(match: re.Match) -> str:
        number = _kanji_number_to_int(match.group("number"))
        if number is None:
            return match.group(0)
        return f"{number}{match.group('suffix')}"

    return re.sub(
        rf"(?P<number>[{kanji_digits}]+)(?P<suffix>丁目|番地|番(?!町)|号)",
        replace_match,
        text,
    )


def _kanji_number_to_int(value: str) -> int | None:
    digits = {
        "〇": 0,
        "零": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "壱": 1,
        "弐": 2,
        "参": 3,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}

    if not value:
        return None
    if not any(char in units for char in value):
        numbers = [digits.get(char) for char in value]
        if any(number is None for number in numbers):
            return None
        return int("".join(str(number) for number in numbers))

    total = 0
    section = 0
    current = 0
    for char in value:
        if char in digits:
            current = digits[char]
            continue
        unit = units.get(char)
        if unit is None:
            return None
        if unit == 10000:
            section = (section + (current or 1)) * unit
            total += section
            section = 0
        else:
            section += (current or 1) * unit
        current = 0
    return total + section + current


def _remove_spaces(value: str) -> str:
    return "".join(value.split())


SEARCH_SEPARATOR_CHARS = (" ", "　", "-", "‐", "‑", "‒", "–", "—", "―", "−", "ー", "ｰ", "－")


def _remove_search_separators(value: str) -> str:
    text = _remove_spaces(value)
    for char in SEARCH_SEPARATOR_CHARS:
        if char.strip():
            text = text.replace(char, "")
    return text


def _sql_remove_search_separators(field: str) -> str:
    expression = field
    for char in SEARCH_SEPARATOR_CHARS:
        expression = f"REPLACE({expression}, '{char}', '')"
    return expression


def _katakana_to_hiragana(value: str) -> str:
    chars = []
    for char in value:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def _hiragana_to_katakana(value: str) -> str:
    chars = []
    for char in value:
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            chars.append(chr(code + 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def _is_kana(char: str) -> bool:
    code = ord(char)
    return 0x3041 <= code <= 0x3096 or 0x30A1 <= code <= 0x30F6


def claim_next_job() -> dict | None:
    now = now_iso()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
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
        conn.execute(
            "UPDATE jobs SET status = 'error', finished_at = ?, error_message = ? WHERE id = ?",
            (now, message, job_id),
        )
        conn.execute(
            "UPDATE cards SET status = 'error', error_message = ?, updated_at = ? WHERE id = ?",
            (message, now, card_id),
        )
