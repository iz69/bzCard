"""Persistent person summaries, maintained in the card writer's transaction."""
from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict


class InvalidCursor(ValueError):
    pass


class ChangedList(ValueError):
    pass


def initialize(conn):
    statements = (
        """CREATE TABLE IF NOT EXISTS contact_summaries (
            owner_user_id TEXT NOT NULL, scope TEXT NOT NULL, id TEXT NOT NULL,
            group_id TEXT NOT NULL, created_at TEXT NOT NULL,
            has_in_progress INTEGER NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(owner_user_id, scope, id))""",
        """CREATE TABLE IF NOT EXISTS contact_members (
            owner_user_id TEXT NOT NULL, scope TEXT NOT NULL, card_id TEXT NOT NULL,
            contact_id TEXT NOT NULL, PRIMARY KEY(owner_user_id, scope, card_id))""",
        """CREATE TABLE IF NOT EXISTS contact_identifiers (
            owner_user_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
            card_id TEXT NOT NULL, PRIMARY KEY(owner_user_id, kind, value, card_id))""",
        """CREATE TABLE IF NOT EXISTS contact_search (
            owner_user_id TEXT NOT NULL, card_id TEXT NOT NULL, fields TEXT NOT NULL,
            PRIMARY KEY(owner_user_id, card_id))""",
        """CREATE TABLE IF NOT EXISTS contact_dirty (
            owner_user_id TEXT NOT NULL, card_id TEXT NOT NULL, old_contact_id TEXT,
            PRIMARY KEY(owner_user_id, card_id))""",
        "CREATE TABLE IF NOT EXISTS contact_versions (owner_user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_contact_order ON contact_summaries(owner_user_id, scope, created_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_contact_group ON contact_summaries(owner_user_id, group_id)",
        "CREATE INDEX IF NOT EXISTS idx_contact_progress ON contact_summaries(owner_user_id, scope, has_in_progress)",
        "CREATE INDEX IF NOT EXISTS idx_contact_members ON contact_members(owner_user_id, scope, contact_id)",
        "CREATE INDEX IF NOT EXISTS idx_contact_identifier_card ON contact_identifiers(owner_user_id, card_id)",
    )
    for sql in statements:
        conn.execute(sql)
    for operation, references in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",))):
        body = " ".join(
            f"""INSERT INTO contact_dirty(owner_user_id, card_id, old_contact_id)
                SELECT {ref}.owner_user_id, {ref}.id,
                    (SELECT contact_id FROM contact_members WHERE owner_user_id = {ref}.owner_user_id
                     AND scope = '' AND card_id = {ref}.id)
                WHERE {ref}.owner_user_id IS NOT NULL AND {ref}.owner_user_id != ''
                ON CONFLICT(owner_user_id, card_id) DO NOTHING;"""
            for ref in references)
        conn.execute(f"DROP TRIGGER IF EXISTS contacts_{operation.lower()}")
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS contacts_{operation.lower()} AFTER {operation} ON cards BEGIN {body} END")
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = 2").fetchone():
        return
    for row in conn.execute("SELECT DISTINCT owner_user_id FROM cards WHERE owner_user_id IS NOT NULL AND owner_user_id != ''").fetchall():
        owner = row[0]
        cards = _cards(conn, owner)
        _update_identifiers(conn, owner, cards)
        _write_groups(conn, owner, cards)
        conn.execute("INSERT OR REPLACE INTO contact_versions VALUES (?, 1)", (owner,))
    conn.execute("DELETE FROM contact_dirty")
    from .timeutil import now_iso
    conn.execute("INSERT INTO schema_migrations VALUES (2, ?)", (now_iso(),))


def _batches(values):
    values = list(values)
    for start in range(0, len(values), 400):
        yield values[start:start + 400]


def _cards(conn, owner, ids=None, search=False):
    from . import repository as repo
    columns = set(repo.CONTACT_SUMMARY_FIELDS) | {"id", "email", "mobile", "revision"}
    if search:
        columns.update(repo.SEARCH_FIELDS)
    projection = ", ".join(sorted(columns))
    if ids is None:
        return [dict(row) for row in conn.execute(f"SELECT {projection} FROM card_records WHERE owner_user_id = ?", (owner,))]
    rows = []
    for batch in _batches(ids):
        placeholders = ','.join('?' for _ in batch)
        rows.extend(dict(row) for row in conn.execute(
            f"SELECT {projection} FROM card_records WHERE owner_user_id = ? AND id IN ({placeholders})", [owner, *batch]))
    return rows


def _update_identifiers(conn, owner, cards):
    from . import repository as repo
    identifiers = []
    for card in cards:
        for kind, value in repo._contact_identifiers(card):
            conn.execute("INSERT OR IGNORE INTO contact_identifiers VALUES (?, ?, ?, ?)", (owner, kind, value, card['id']))
            identifiers.append((kind, value))
    # Search text is normalized once on mutation, including historical OCR text.
    for card in _cards(conn, owner, [card['id'] for card in cards], search=True):
        conn.execute("INSERT OR REPLACE INTO contact_search VALUES (?, ?, ?)",
                     (owner, card['id'], json.dumps(repo._normalized_search_fields(card), ensure_ascii=False)))
    return set(identifiers)


def _write_groups(conn, owner, cards):
    from . import repository as repo
    for group in repo._contact_groups(cards):
        group_id = max(group, key=lambda card: (card.get('created_at') or '', card['id']))['id']
        scopes = {'': group}
        for card in group:
            scopes.setdefault(card['status'], []).append(card)
        for scope, members in scopes.items():
            # A status filter can disconnect a person whose bridge card is hidden.
            for contact in repo._contacts_from_entries([(card, 0) for card in members], None, include_cards=True):
                payload = {field: contact.get(field) for field in repo.CONTACT_SUMMARY_FIELDS}
                payload.update({field: contact[field] for field in ('id', 'representative_card_id', 'card_count', 'revision', 'has_in_progress')})
                conn.execute("INSERT OR REPLACE INTO contact_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (owner, scope, contact['id'], group_id, contact.get('created_at') or '', int(contact['has_in_progress']), json.dumps(payload, ensure_ascii=False)))
                conn.executemany("INSERT OR REPLACE INTO contact_members VALUES (?, ?, ?, ?)",
                    [(owner, scope, card['id'], contact['id']) for card in contact['cards']])


def refresh(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'contact_dirty'").fetchone():
        return False
    if not conn.execute("SELECT 1 FROM contact_dirty LIMIT 1").fetchone():
        return False
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    dirty = defaultdict(list)
    for row in conn.execute("SELECT * FROM contact_dirty"):
        dirty[row['owner_user_id']].append(dict(row))
    for owner, changes in dirty.items():
        ids = {row['card_id'] for row in changes}
        groups = {row['old_contact_id'] for row in changes if row['old_contact_id']}
        for batch in _batches(ids):
            marks = ','.join('?' for _ in batch)
            groups.update(row[0] for row in conn.execute(
                f"SELECT DISTINCT contact_id FROM contact_members WHERE owner_user_id = ? AND scope = '' AND card_id IN ({marks})", [owner, *batch]))
            for table in ('contact_identifiers', 'contact_search'):
                conn.execute(f"DELETE FROM {table} WHERE owner_user_id = ? AND card_id IN ({marks})", [owner, *batch])
        cards = _cards(conn, owner, ids)
        for kind, value in _update_identifiers(conn, owner, cards):
            groups.update(row[0] for row in conn.execute(
                """SELECT DISTINCT m.contact_id FROM contact_identifiers i JOIN contact_members m
                    ON m.owner_user_id = i.owner_user_id AND m.card_id = i.card_id AND m.scope = ''
                    WHERE i.owner_user_id = ? AND i.kind = ? AND i.value = ?""", (owner, kind, value)))
        affected = set(ids)
        for batch in _batches(groups):
            marks = ','.join('?' for _ in batch)
            affected.update(row[0] for row in conn.execute(
                f"SELECT card_id FROM contact_members WHERE owner_user_id = ? AND scope = '' AND contact_id IN ({marks})", [owner, *batch]))
            conn.execute(f"DELETE FROM contact_summaries WHERE owner_user_id = ? AND group_id IN ({marks})", [owner, *batch])
        for batch in _batches(affected):
            marks = ','.join('?' for _ in batch)
            conn.execute(f"DELETE FROM contact_members WHERE owner_user_id = ? AND card_id IN ({marks})", [owner, *batch])
        _write_groups(conn, owner, _cards(conn, owner, affected))
        conn.execute("DELETE FROM contact_dirty WHERE owner_user_id = ?", (owner,))
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (owner,)).fetchone():
            conn.execute("INSERT INTO contact_versions VALUES (?, 1) ON CONFLICT(owner_user_id) DO UPDATE SET revision = revision + 1", (owner,))
        else:
            conn.execute("DELETE FROM contact_versions WHERE owner_user_id = ?", (owner,))
    return True


def _search_matches(conn, owner, scope, query):
    from . import repository as repo
    tokens = repo._prepare_contact_search(query)
    matches = {}
    for row in conn.execute("""SELECT m.contact_id, s.fields FROM contact_members m JOIN contact_search s
            ON s.owner_user_id = m.owner_user_id AND s.card_id = m.card_id
            WHERE m.owner_user_id = ? AND m.scope = ?""", (owner, scope)):
        parts = repo._search_field_scores(json.loads(row['fields']), tokens)
        entry = matches.setdefault(row['contact_id'], [0, [False] * len(tokens)])
        entry[0] = max(entry[0], sum(parts))
        entry[1] = [old or bool(new) for old, new in zip(entry[1], parts)]
    return {key: value[0] for key, value in matches.items() if all(value[1])}


def page(conn, owner, query=None, status=None, limit=None, cursor=None, known_revision=None):
    if not conn.in_transaction:
        conn.execute('BEGIN')
    scope = status or ''
    row = conn.execute("SELECT revision FROM contact_versions WHERE owner_user_id = ?", (owner,)).fetchone()
    revision = str(row[0] if row else 0)
    progress = bool(conn.execute("SELECT 1 FROM contact_summaries WHERE owner_user_id = ? AND scope = '' AND has_in_progress = 1 LIMIT 1", (owner,)).fetchone())
    result = {'items': [], 'next_cursor': None, 'revision': revision, 'has_in_progress': progress}
    if cursor is None and known_revision == revision:
        return {**result, 'unchanged': True}
    binding = hashlib.sha256(json.dumps([owner, query or '', scope]).encode()).hexdigest()
    after = None
    if cursor:
        try:
            if len(cursor) > 4096:
                raise ValueError()
            saved = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if saved['binding'] != binding or not isinstance(saved['after'], list) or len(saved['after']) != 3:
                raise ValueError()
            after = saved['after']
            if not isinstance(after[0], int) or not all(isinstance(v, str) for v in after[1:]):
                raise ValueError()
        except (ValueError, KeyError, TypeError) as error:
            raise InvalidCursor('Invalid contacts cursor') from error
        if saved.get('revision') != revision:
            raise ChangedList('人物一覧が更新されました。先頭から再取得してください。')
    from . import repository as repo
    search = bool(repo._prepare_contact_search(query))
    if search:
        scores = _search_matches(conn, owner, scope, query)
        ordering = [(scores[row['id']], row['created_at'], row['id']) for row in conn.execute(
            "SELECT id, created_at FROM contact_summaries WHERE owner_user_id = ? AND scope = ?", (owner, scope)) if row['id'] in scores]
        ordering.sort(reverse=True)
        if after:
            ordering = [key for key in ordering if key < tuple(after)]
        chosen = ordering if limit is None else ordering[:limit + 1]
        more = limit is not None and len(chosen) > limit
        chosen = chosen if limit is None else chosen[:limit]
        for score, created, contact_id in chosen:
            payload = conn.execute("SELECT payload FROM contact_summaries WHERE owner_user_id = ? AND scope = ? AND id = ?", (owner, scope, contact_id)).fetchone()[0]
            result['items'].append(json.loads(payload))
        last = chosen[-1] if chosen else None
    else:
        sql = "SELECT id, created_at, payload FROM contact_summaries WHERE owner_user_id = ? AND scope = ?"
        params = [owner, scope]
        if after:
            sql += " AND (created_at, id) < (?, ?)"
            params.extend(after[1:])
        sql += " ORDER BY created_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit + 1)
        rows = conn.execute(sql, params).fetchall()
        more = limit is not None and len(rows) > limit
        rows = rows if limit is None else rows[:limit]
        result['items'] = [json.loads(row['payload']) for row in rows]
        last = (0, rows[-1]['created_at'], rows[-1]['id']) if rows else None
    if more and last:
        result['next_cursor'] = base64.urlsafe_b64encode(json.dumps({'binding':binding,'revision':revision,'after':last}).encode()).decode().rstrip('=')
    return result
