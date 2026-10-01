from __future__ import annotations

import sqlite3
import fcntl
import os
from contextlib import closing
from uuid import uuid4
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings


DB_PATH = settings.data_dir / "bzcard.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def backup_before_local_auth() -> Path:
    """Create one recoverable pre-migration snapshot without overwriting an existing one."""
    backup_path = settings.data_dir / "bzcard-before-local-auth.db"
    if backup_path.exists():
        return backup_path
    source = get_connection()
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup_path


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / ".database-migration.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _init_db_locked()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _init_db_locked() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "cards").mkdir(parents=True, exist_ok=True)
    _backup_before_image_migration()

    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                extracted_json TEXT,
                person_name TEXT,
                person_name_kana TEXT,
                company_name TEXT,
                department TEXT,
                title TEXT,
                postal_code TEXT,
                address TEXT,
                tel TEXT,
                mobile TEXT,
                fax TEXT,
                email TEXT,
                website TEXT,
                tags TEXT,
                memo TEXT,
                extraction_duration_ms INTEGER,
                error_message TEXT,
                owner_user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "cards", "source_system", "TEXT")
        _ensure_column(conn, "cards", "source_id", "TEXT")
        _ensure_column(conn, "cards", "source_filename", "TEXT")
        _ensure_column(conn, "cards", "import_batch_id", "TEXT")
        _ensure_column(conn, "cards", "owner_user_id", "TEXT")
        _ensure_column(conn, "cards", "tags", "TEXT")
        _ensure_column(conn, "cards", "revision", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "cards", "latest_extraction_id", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS import_batches (
                id TEXT PRIMARY KEY,
                source_name TEXT,
                status TEXT NOT NULL,
                total_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                memo TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS card_images (
                id TEXT PRIMARY KEY,
                card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                side TEXT NOT NULL,
                original_sha256 TEXT,
                original_image_path TEXT NOT NULL,
                processed_image_path TEXT,
                thumbnail_path TEXT,
                ocr_direction TEXT NOT NULL DEFAULT 'horizontal',
                ocr_text TEXT,
                ocr_blocks_json TEXT,
                ocr_duration_ms INTEGER,
                width INTEGER,
                height INTEGER,
                file_size INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(card_id, side)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        _ensure_column(conn, "card_images", "manual_rotation", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "card_images", "auto_rotation", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS line_events (
                id TEXT PRIMARY KEY,
                event_type TEXT,
                line_sender_id TEXT,
                message_id TEXT,
                card_id TEXT REFERENCES cards(id) ON DELETE SET NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                login_id TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS line_connections (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                line_user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "line_connections", "line_user_id", "TEXT")
        _ensure_column(conn, "line_connections", "channel_secret_encrypted", "TEXT")
        _ensure_column(conn, "line_connections", "access_token_encrypted", "TEXT")
        _ensure_column(conn, "line_connections", "line_login_channel_id", "TEXT")
        _ensure_column(conn, "line_connections", "liff_url", "TEXT")
        _ensure_column(conn, "line_connections", "liff_id", "TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS line_link_requests (
                token_hash TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL REFERENCES line_connections(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL, created_at TEXT NOT NULL
            )"""
        )
        _ensure_column(conn, "line_events", "connection_id", "TEXT REFERENCES line_connections(id) ON DELETE CASCADE")
        _ensure_column(conn, "line_events", "payload_json", "TEXT")
        _ensure_column(conn, "line_events", "attempts", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "line_events", "available_at", "TEXT")
        _ensure_column(conn, "line_events", "pending_card_id", "TEXT")
        _ensure_column(conn, "line_connections", "legacy_identity_checked", "INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS extraction_runs (
            id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ocr_text TEXT NOT NULL, ocr_blocks_json TEXT NOT NULL,
            model_json TEXT NOT NULL, pipeline_version TEXT NOT NULL,
            raw_json TEXT NOT NULL, automatic_json TEXT NOT NULL, result_json TEXT NOT NULL,
            feedback_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS corrections (
            id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            extraction_id TEXT REFERENCES extraction_runs(id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            field TEXT NOT NULL, model_value TEXT NOT NULL, automatic_value TEXT NOT NULL,
            before_value TEXT NOT NULL, corrected_value TEXT NOT NULL,
            context_json TEXT NOT NULL, cause TEXT NOT NULL,
            eligible INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 0,
            superseded INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        )""")
        _ensure_column(conn, "extraction_runs", "response_text", "TEXT NOT NULL DEFAULT ''")
        _migrate_image_storage(conn)
        _create_card_view(conn)
        _create_revision_triggers(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_owner_user_id ON cards(owner_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_images_card_id ON card_images(card_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_images_sha256 ON card_images(original_sha256)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_images_side ON card_images(side)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_import_batch_id ON cards(import_batch_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_events_message_id ON line_events(message_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_events_queue ON line_events(status, available_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_extraction_runs_owner_card ON extraction_runs(owner_user_id, card_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_corrections_owner ON corrections(owner_user_id, active, field)")
        event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(line_events)")}
        if "line_sender_id" not in event_columns and "line_user_id" in event_columns:
            conn.execute("DROP INDEX IF EXISTS idx_line_events_line_user_id")
            conn.execute("ALTER TABLE line_events RENAME COLUMN line_user_id TO line_sender_id")
            event_columns.add("line_sender_id")
        if "line_sender_id" in event_columns:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_line_events_line_sender_id ON line_events(line_sender_id)")
        else:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_line_events_line_user_id ON line_events(line_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_connections_owner_user_id ON line_connections(owner_user_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_line_connections_line_user_id ON line_connections(line_user_id) WHERE line_user_id IS NOT NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_line_connections_owner_user_id_unique ON line_connections(owner_user_id)")
        conn.execute(
            """UPDATE cards
            SET status = CASE status
                WHEN 'preprocessing' THEN 'preparing'
                WHEN 'ocr_processing' THEN 'scanning'
            END
            WHERE status IN ('preprocessing', 'ocr_processing')"""
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(row["name"] == column for row in rows):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


IMAGE_FIELDS = (
    "original_sha256", "original_image_path", "processed_image_path", "thumbnail_path",
    "ocr_direction", "ocr_text", "ocr_blocks_json", "ocr_duration_ms",
)


def _migrate_image_storage(conn: sqlite3.Connection) -> None:
    """Import legacy image rows once before removing duplicate storage columns."""
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = 1").fetchone():
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(cards)")}
    for prefix, side in (("", "front"), ("back_", "back")):
        original = prefix + "original_image_path"
        if original not in columns:
            continue
        source = [prefix + field if prefix + field in columns else
                  "'horizontal'" if field == "ocr_direction" else "NULL" for field in IMAGE_FIELDS]
        conn.execute(f"""INSERT OR IGNORE INTO card_images
            (id, card_id, side, {', '.join(IMAGE_FIELDS)}, created_at, updated_at)
            SELECT id || ':{side}', id, '{side}', {', '.join(source)}, created_at, updated_at
            FROM cards WHERE {original} IS NOT NULL AND {original} != ''""")
    for index in ("idx_cards_original_sha256", "idx_cards_back_original_sha256"):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    for field in IMAGE_FIELDS:
        for prefix in ("", "back_"):
            if prefix + field in columns:
                conn.execute(f"ALTER TABLE cards DROP COLUMN {prefix}{field}")
    # Hashes can be missing in old imports. Compute only during this migration.
    from .services.image_store import image_metadata, resolve_data_path, sha256_file
    for row in conn.execute("SELECT id, original_image_path FROM card_images WHERE original_sha256 IS NULL OR original_sha256 = ''").fetchall():
        path = resolve_data_path(row["original_image_path"])
        if path.is_file():
            width, height, size = image_metadata(path)
            conn.execute("UPDATE card_images SET original_sha256 = ?, width = ?, height = ?, file_size = ? WHERE id = ?",
                         (sha256_file(path), width, height, size, row["id"]))
    from .services.timeutil import now_iso
    conn.execute("INSERT INTO schema_migrations VALUES (1, ?)", (now_iso(),))


def _create_card_view(conn: sqlite3.Connection) -> None:
    aliases = [f"COALESCE({alias}.{field}, 'horizontal') AS {prefix}{field}" if field == "ocr_direction"
               else f"{alias}.{field} AS {prefix}{field}"
               for alias, prefix in (("front", ""), ("back", "back_")) for field in IMAGE_FIELDS]
    conn.execute("""CREATE VIEW IF NOT EXISTS card_records AS SELECT cards.*, """ + ", ".join(aliases) + """
        FROM cards LEFT JOIN card_images front ON front.card_id = cards.id AND front.side = 'front'
        LEFT JOIN card_images back ON back.card_id = cards.id AND back.side = 'back'""")


def _create_revision_triggers(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TRIGGER IF NOT EXISTS cards_revision AFTER UPDATE ON cards
        WHEN NEW.revision = OLD.revision
        BEGIN UPDATE cards SET revision = OLD.revision + 1 WHERE id = NEW.id; END""")
    for operation, reference in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS images_revision_{operation.lower()}
            AFTER {operation} ON card_images BEGIN
            UPDATE cards SET revision = revision + 1, updated_at = {reference}.updated_at
            WHERE id = {reference}.card_id; END""")


def _backup_before_image_migration() -> None:
    if not DB_PATH.exists():
        return
    backup = settings.data_dir / "bzcard-before-image-storage-v1.db"
    if backup.exists():
        return
    with closing(get_connection()) as source:
        columns = {row["name"] for row in source.execute("PRAGMA table_info(cards)")}
        if "original_image_path" not in columns:
            return
        temporary = backup.with_name(f".{backup.name}.{uuid4().hex}.tmp")
        try:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
            os.replace(temporary, backup)
        finally:
            temporary.unlink(missing_ok=True)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
