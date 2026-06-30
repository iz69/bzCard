from __future__ import annotations

import json
import re
import unicodedata
from uuid import uuid4

from ..config import settings
from ..database import connection, get_connection, row_to_dict
from .timeutil import now_iso


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
    "memo",
    "ocr_text",
    "back_ocr_text",
]

LIST_OMITTED_FIELDS = {
    "ocr_text",
    "ocr_blocks_json",
    "back_ocr_text",
    "back_ocr_blocks_json",
    "extracted_json",
}


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


def create_card(card_id: str, original_image_path: str, original_sha256: str, direction: str) -> str:
    now = now_iso()
    job_id = uuid4().hex
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO cards (
                id, status, original_image_path, original_sha256, ocr_direction, created_at, updated_at
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?)
            """,
            (card_id, original_image_path, original_sha256, direction, now, now),
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


def set_card_owner(card_id: str, owner_line_user_id: str) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE cards SET owner_line_user_id = ?, updated_at = ? WHERE id = ?",
            (owner_line_user_id, now, card_id),
        )


def get_line_owned_card_by_original_sha256(original_sha256: str, line_user_id: str) -> dict | None:
    if not original_sha256:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT cards.*
            FROM cards
            JOIN card_images ON card_images.card_id = cards.id
            WHERE card_images.original_sha256 = ?
              AND cards.owner_line_user_id = ?
            ORDER BY cards.created_at ASC
            LIMIT 1
            """,
            (original_sha256, line_user_id),
        ).fetchone()
        return _hydrate_card(conn, row)


def claim_line_event(
    event_id: str,
    event_type: str | None,
    line_user_id: str | None,
    message_id: str | None,
) -> bool:
    now = now_iso()
    with connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO line_events (
                id, event_type, line_user_id, message_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'received', ?, ?)
            """,
            (event_id, event_type, line_user_id, message_id, now, now),
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


def get_line_user(line_user_id: str) -> dict | None:
    with get_connection() as conn:
        return row_to_dict(
            conn.execute(
                "SELECT * FROM line_users WHERE line_user_id = ?",
                (line_user_id,),
            ).fetchone()
        )


def upsert_line_user_profile(
    line_user_id: str,
    display_name: str | None,
    picture_url: str | None,
) -> dict:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO line_users (
                line_user_id, display_name, picture_url, status, role, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', 'user', ?, ?, ?)
            ON CONFLICT(line_user_id) DO UPDATE SET
                display_name = excluded.display_name,
                picture_url = excluded.picture_url,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
            """,
            (line_user_id, display_name, picture_url, now, now, now),
        )
        return dict(
            conn.execute(
                "SELECT * FROM line_users WHERE line_user_id = ?",
                (line_user_id,),
            ).fetchone()
        )


def activate_line_user(line_user_id: str) -> dict:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            """
            UPDATE line_users
            SET status = 'active',
                last_seen_at = ?,
                updated_at = ?
            WHERE line_user_id = ?
            """,
            (now, now, line_user_id),
        )
        return dict(
            conn.execute(
                "SELECT * FROM line_users WHERE line_user_id = ?",
                (line_user_id,),
            ).fetchone()
        )


def create_line_session(token_hash: str, line_user_id: str, expires_at: str) -> None:
    now = now_iso()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO line_sessions (token_hash, line_user_id, expires_at, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_hash, line_user_id, expires_at, now, now),
        )


def get_line_session_user(token_hash: str, now: str) -> dict | None:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT line_users.*
            FROM line_sessions
            JOIN line_users ON line_users.line_user_id = line_sessions.line_user_id
            WHERE line_sessions.token_hash = ?
              AND line_sessions.expires_at > ?
              AND line_users.status = 'active'
            """,
            (token_hash, now),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE line_sessions
            SET last_seen_at = ?
            WHERE token_hash = ?
            """,
            (now, token_hash),
        )
        conn.execute(
            """
            UPDATE line_users
            SET last_seen_at = ?, updated_at = ?
            WHERE line_user_id = ?
            """,
            (now, now, row["line_user_id"]),
        )
        return dict(row)


def delete_line_session(token_hash: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM line_sessions WHERE token_hash = ?", (token_hash,))


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
        variants = _query_variants(q)
        expressions = []
        for field in SEARCH_FIELDS:
            for _variant in variants:
                expressions.append(f"{field} LIKE ?")
                expressions.append(f"REPLACE(REPLACE({field}, ' ', ''), '　', '') LIKE ?")
        where.append(
            "(" + " OR ".join(expressions) + ")"
        )
        for _field in SEARCH_FIELDS:
            for variant in variants:
                like = f"%{variant}%"
                params.extend([like, like])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with get_connection() as conn:
        return [
            card
            for row in conn.execute(sql, params).fetchall()
            if (card := _hydrate_card(conn, row, include_images=False)) is not None
        ]


def get_card(card_id: str) -> dict | None:
    with get_connection() as conn:
        return _hydrate_card(
            conn,
            conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone(),
        )


def list_line_user_cards(line_user_id: str, q: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM cards"
    where = ["owner_line_user_id = ?"]
    params: list[str] = [line_user_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        variants = _query_variants(q)
        expressions = []
        for field in SEARCH_FIELDS:
            for _variant in variants:
                expressions.append(f"{field} LIKE ?")
                expressions.append(f"REPLACE(REPLACE({field}, ' ', ''), '　', '') LIKE ?")
        where.append("(" + " OR ".join(expressions) + ")")
        for _field in SEARCH_FIELDS:
            for variant in variants:
                like = f"%{variant}%"
                params.extend([like, like])
    sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with get_connection() as conn:
        return [
            card
            for row in conn.execute(sql, params).fetchall()
            if (card := _hydrate_card(conn, row, include_images=False)) is not None
        ]


def get_line_user_card(card_id: str, line_user_id: str) -> dict | None:
    with get_connection() as conn:
        return _hydrate_card(
            conn,
            conn.execute(
                "SELECT * FROM cards WHERE id = ? AND owner_line_user_id = ?",
                (card_id, line_user_id),
            ).fetchone(),
        )


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
    return get_card(card_id)


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
        "memo": extracted.get("memo") or "",
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
                memo = ?,
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
                values["memo"],
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


def _normalize_kana_field(value) -> str:
    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())
    if not text:
        return ""
    if not any(_is_kana(char) for char in text):
        return ""
    return _katakana_to_hiragana(text)


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
