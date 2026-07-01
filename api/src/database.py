from __future__ import annotations

import sqlite3
import hashlib
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
    (settings.data_dir / "cards").mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                original_sha256 TEXT,
                original_image_path TEXT NOT NULL,
                processed_image_path TEXT,
                thumbnail_path TEXT,
                back_original_sha256 TEXT,
                back_original_image_path TEXT,
                back_processed_image_path TEXT,
                back_thumbnail_path TEXT,
                back_ocr_direction TEXT NOT NULL DEFAULT 'horizontal',
                back_ocr_text TEXT,
                back_ocr_blocks_json TEXT,
                back_ocr_duration_ms INTEGER,
                ocr_direction TEXT NOT NULL DEFAULT 'horizontal',
                ocr_text TEXT,
                ocr_blocks_json TEXT,
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
                ocr_duration_ms INTEGER,
                extraction_duration_ms INTEGER,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "cards", "original_sha256", "TEXT")
        _ensure_column(conn, "cards", "back_original_sha256", "TEXT")
        _ensure_column(conn, "cards", "back_original_image_path", "TEXT")
        _ensure_column(conn, "cards", "back_processed_image_path", "TEXT")
        _ensure_column(conn, "cards", "back_thumbnail_path", "TEXT")
        _ensure_column(conn, "cards", "back_ocr_direction", "TEXT NOT NULL DEFAULT 'horizontal'")
        _ensure_column(conn, "cards", "back_ocr_text", "TEXT")
        _ensure_column(conn, "cards", "back_ocr_blocks_json", "TEXT")
        _ensure_column(conn, "cards", "back_ocr_duration_ms", "INTEGER")
        _ensure_column(conn, "cards", "source_system", "TEXT")
        _ensure_column(conn, "cards", "source_id", "TEXT")
        _ensure_column(conn, "cards", "source_filename", "TEXT")
        _ensure_column(conn, "cards", "import_batch_id", "TEXT")
        _ensure_column(conn, "cards", "owner_line_user_id", "TEXT")
        _ensure_column(conn, "cards", "tags", "TEXT")
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS line_events (
                id TEXT PRIMARY KEY,
                event_type TEXT,
                line_user_id TEXT,
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
            CREATE TABLE IF NOT EXISTS line_users (
                line_user_id TEXT PRIMARY KEY,
                display_name TEXT,
                picture_url TEXT,
                status TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS line_sessions (
                token_hash TEXT PRIMARY KEY,
                line_user_id TEXT NOT NULL REFERENCES line_users(line_user_id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_original_sha256 ON cards(original_sha256)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_back_original_sha256 ON cards(back_original_sha256)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_owner_line_user_id ON cards(owner_line_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_images_card_id ON card_images(card_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_images_sha256 ON card_images(original_sha256)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_images_side ON card_images(side)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_import_batch_id ON cards(import_batch_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_events_message_id ON line_events(message_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_events_line_user_id ON line_events(line_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_users_status ON line_users(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_sessions_line_user_id ON line_sessions(line_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_line_sessions_expires_at ON line_sessions(expires_at)")
        _backfill_original_hashes(conn)
        _backfill_card_images(conn)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(row["name"] == column for row in rows):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _backfill_original_hashes(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, original_image_path
        FROM cards
        WHERE original_sha256 IS NULL OR original_sha256 = ''
        """
    ).fetchall()
    for row in rows:
        path = settings.data_dir / row["original_image_path"]
        if not path.exists():
            continue
        conn.execute(
            "UPDATE cards SET original_sha256 = ? WHERE id = ?",
            (_sha256_file(path), row["id"]),
        )

    rows = conn.execute(
        """
        SELECT id, back_original_image_path
        FROM cards
        WHERE back_original_image_path IS NOT NULL
          AND back_original_image_path != ''
          AND (back_original_sha256 IS NULL OR back_original_sha256 = '')
        """
    ).fetchall()
    for row in rows:
        path = settings.data_dir / row["back_original_image_path"]
        if not path.exists():
            continue
        conn.execute(
            "UPDATE cards SET back_original_sha256 = ? WHERE id = ?",
            (_sha256_file(path), row["id"]),
        )


def _backfill_card_images(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            id, original_sha256, original_image_path, processed_image_path, thumbnail_path,
            ocr_direction, ocr_text, ocr_blocks_json, ocr_duration_ms,
            back_original_sha256, back_original_image_path, back_processed_image_path,
            back_thumbnail_path, back_ocr_direction, back_ocr_text, back_ocr_blocks_json,
            back_ocr_duration_ms, created_at, updated_at
        FROM cards
        """
    ).fetchall()
    for row in rows:
        if row["original_image_path"]:
            _insert_card_image_from_legacy(
                conn=conn,
                card_id=row["id"],
                side="front",
                original_sha256=row["original_sha256"],
                original_image_path=row["original_image_path"],
                processed_image_path=row["processed_image_path"],
                thumbnail_path=row["thumbnail_path"],
                ocr_direction=row["ocr_direction"],
                ocr_text=row["ocr_text"],
                ocr_blocks_json=row["ocr_blocks_json"],
                ocr_duration_ms=row["ocr_duration_ms"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        if row["back_original_image_path"]:
            _insert_card_image_from_legacy(
                conn=conn,
                card_id=row["id"],
                side="back",
                original_sha256=row["back_original_sha256"],
                original_image_path=row["back_original_image_path"],
                processed_image_path=row["back_processed_image_path"],
                thumbnail_path=row["back_thumbnail_path"],
                ocr_direction=row["back_ocr_direction"],
                ocr_text=row["back_ocr_text"],
                ocr_blocks_json=row["back_ocr_blocks_json"],
                ocr_duration_ms=row["back_ocr_duration_ms"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )


def _insert_card_image_from_legacy(
    conn: sqlite3.Connection,
    card_id: str,
    side: str,
    original_sha256: str | None,
    original_image_path: str,
    processed_image_path: str | None,
    thumbnail_path: str | None,
    ocr_direction: str | None,
    ocr_text: str | None,
    ocr_blocks_json: str | None,
    ocr_duration_ms: int | None,
    created_at: str,
    updated_at: str,
) -> None:
    width, height, file_size = _image_metadata(settings.data_dir / original_image_path)
    conn.execute(
        """
        INSERT OR IGNORE INTO card_images (
            id, card_id, side, original_sha256, original_image_path, processed_image_path,
            thumbnail_path, ocr_direction, ocr_text, ocr_blocks_json, ocr_duration_ms,
            width, height, file_size, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{card_id}:{side}",
            card_id,
            side,
            original_sha256,
            original_image_path,
            processed_image_path,
            thumbnail_path,
            ocr_direction or "horizontal",
            ocr_text,
            ocr_blocks_json,
            ocr_duration_ms,
            width,
            height,
            file_size,
            created_at,
            updated_at,
        ),
    )


def _image_metadata(path: Path) -> tuple[int | None, int | None, int | None]:
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
