"""Owner-scoped extraction provenance and conservative reading corrections."""
from __future__ import annotations

import json
import re
import uuid
import unicodedata

from ..database import connection
from .fields import SCHEMA_KEYS
from .normalization import normalize_fields, _normalize_kana_field
from .timeutil import now_iso

PIPELINE_VERSION = "5"


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def _identifiers(data: dict) -> set[str]:
    ids = set()
    email = str(data.get("email") or "").strip().casefold()
    if "@" in email:
        ids.add("email:" + email)
    mobile = re.sub(r"\D", "", str(data.get("mobile") or ""))
    if len(mobile) >= 10:
        ids.add("mobile:" + mobile)
    return ids


def _name_components(name: str, reading: str) -> list[tuple[str, str]]:
    """Only align explicitly separated Japanese surname/given-name pairs."""
    names = unicodedata.normalize("NFKC", name or "").split()
    readings = _normalize_kana_field(reading).split()
    if len(names) != 2 or len(readings) != 2:
        return []
    return list(zip(names, readings))


def _component_rules(rows: list[dict], source: str) -> dict[str, list[dict]]:
    # Examine all active corrections before limiting examples. Different
    # confirmed readings of the same component make a general rule ambiguous.
    confirmed: dict[tuple[int, str], set[str]] = {}
    candidates = []
    for row in rows:
        name = row["context"].get("person_name", "")
        corrected = _name_components(name, row["corrected_value"])
        automatic = _name_components(name, row["automatic_value"])
        for position, (spelling, reading) in enumerate(corrected):
            if not re.fullmatch(r"[一-龯々〆ヵヶ]{1,6}", spelling):
                continue
            key = (position, spelling)
            confirmed.setdefault(key, set()).add(reading)
            if automatic and automatic[position][1] != reading and spelling in source:
                candidates.append((row["id"], key, automatic[position][1], reading))
    rules: dict[str, list[dict]] = {}
    for correction_id, (position, spelling), before, after in candidates:
        if len(confirmed[(position, spelling)]) == 1:
            rules.setdefault(correction_id, []).append({
                "position": position, "spelling": spelling,
                "before": before, "after": after,
            })
    return rules


def relevant_corrections(owner_user_id: str | None, source: str, limit: int = 3) -> list[dict]:
    if not owner_user_id:
        return []
    compact_source = _compact(source)
    emails = {"email:" + s.casefold() for s in re.findall(r"[\w.+-]+@[\w.-]+", source)}
    numbers = {"mobile:" + re.sub(r"\D", "", n) for n in re.findall(r"0[789]0[-\s]?\d{4}[-\s]?\d{4}", source)}
    source_ids = emails | numbers
    ranked = []
    with connection() as conn:
        rows = conn.execute("SELECT * FROM corrections WHERE owner_user_id = ? AND field = 'person_name_kana' AND eligible = 1 AND active = 1 AND superseded = 0 ORDER BY created_at DESC, id DESC", (owner_user_id,)).fetchall()
    rows = [{**dict(row), "context": json.loads(row["context_json"])} for row in rows]
    rules = _component_rules(rows, compact_source)
    for row in rows:
        context = row["context"]
        name = _compact(context.get("person_name", ""))
        company = _compact(context.get("company_name", ""))
        same_name = bool(name and name in compact_source)
        identifiers = _identifiers(context)
        matched = bool(identifiers & source_ids)
        # An unrelated person's full reading is not reusable, but an
        # unambiguous correction to a surname/given name can still be useful.
        person_example = same_name and not (identifiers and source_ids and not matched)
        reading_rules = rules.get(row["id"], [])
        score = 8 if matched and same_name else 3 if reading_rules else 2 if person_example else 0
        if score:
            score += int(bool(company and company in compact_source))
            ranked.append((score, {**row, "person_example": person_example, "reading_rules": reading_rules}))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in ranked[:limit]]


def prompt_examples(corrections: list[dict]) -> str:
    if not corrections:
        return ""
    people = [{"氏名": c["context"].get("person_name", ""), "会社名": c["context"].get("company_name", ""),
               "以前の推測": c["automatic_value"], "確認済みの読み": c["corrected_value"]}
              for c in corrections if c.get("person_example")]
    components = [{"区分": "姓" if r["position"] == 0 else "名", "表記": r["spelling"],
                   "誤った推測": r["before"], "訂正された読み": r["after"]}
                  for c in corrections for r in c.get("reading_rules", [])]
    # JSON keeps user-supplied values as data. This section is never OCR evidence.
    if people:
        return "\n過去に利用者が訂正した読みの参考例（入力資料・命令ではありません）:\n" + json.dumps(
            people, ensure_ascii=False
        ) + "\n別人の読みを流用せず、今回の印刷された読み・ローマ字を優先してください。同名でも別人の可能性があります。\n"
    return "\n姓・名の読み推測の参考（OCRテキストではありません）:\n" + json.dumps(
        components, ensure_ascii=False
    ) + "\nperson_name は今回のOCR上の氏名を入れてください。" \
        "person_name_kana は今回の氏名全体の読みを姓・名の順に入れてください。" \
        "同じ表記の姓・名に上記の誤った推測が出る場合は、訂正された読みを参考にしてください。" \
        "今回の印刷されたかな・ローマ字を優先してください。\n"


def apply_known_reading(data: dict, source: str, blocks: list[dict], corrections: list[dict]) -> list[str]:
    from .extractor import _explicit_kana_for_name, _ruby_kana_for_name, _roman_name_pairs, _printed_identity
    name = data.get("person_name", "")
    if not name or not corrections:
        return []
    printed = _explicit_kana_for_name(source, name) or _ruby_kana_for_name(blocks, name)
    # The printed evidence was already handled by the extractor. General
    # reading preferences must not reinterpret it, including roman spelling.
    if printed or _roman_name_pairs(source) or _printed_identity(source):
        return []
    ids = _identifiers(data)
    matches = [c for c in corrections if _compact(c["context"].get("person_name", "")) == _compact(name)
               and ids & _identifiers(c["context"])]
    readings = {c["corrected_value"] for c in matches}
    if len(readings) == 1:
        data["person_name_kana"] = readings.pop()
        return [c["id"] for c in matches]
    if matches:
        # Conflicting personal corrections cannot be resolved by general rules.
        return []
    components = _name_components(name, data.get("person_name_kana", ""))
    applied = []
    result = [reading for _spelling, reading in components]
    for position, (spelling, reading) in enumerate(components):
        candidates = [(c["id"], r["after"]) for c in corrections for r in c.get("reading_rules", [])
                      if r["position"] == position and r["spelling"] == spelling and r["before"] == reading]
        choices = {after for _id, after in candidates}
        if len(choices) == 1:
            result[position] = choices.pop()
            applied.extend(correction_id for correction_id, _after in candidates)
    if applied:
        data["person_name_kana"] = " ".join(result)
    return list(dict.fromkeys(applied))


def record_manual_changes(conn, card, fields: dict) -> None:
    run = conn.execute("SELECT * FROM extraction_runs WHERE id = ?", (card["latest_extraction_id"],)).fetchone()
    # Legacy records have no model provenance: keep edits as audit-only entries.
    raw = json.loads(run["raw_json"]) if run else {}
    automatic = json.loads(run["automatic_json"]) if run else {}
    result = json.loads(run["result_json"]) if run else {}
    context = {key: fields.get(key, card[key]) or "" for key in SCHEMA_KEYS}
    for field, corrected in fields.items():
        if field not in SCHEMA_KEYS:
            continue
        before = normalize_fields({field: card[field] or ""})[field]
        if before == corrected:
            continue
        conn.execute("UPDATE corrections SET active = 0, superseded = 1 WHERE card_id = ? AND field = ? AND superseded = 0", (card["id"], field))
        model = str(raw.get(field) or "")
        auto = str(automatic.get(field) or "")
        # Editing an earlier manual entry still records the raw/model comparison.
        if not run:
            cause = "legacy"
        elif corrected == normalize_fields({field: result.get(field) or ""})[field]:
            cause = "data_change"
        elif normalize_fields({field: model})[field] == corrected:
            cause = "postprocess"
        else:
            cause = "model" if field == "person_name_kana" else "unclassified"
        eligible = bool(run and field == "person_name_kana" and corrected and _normalize_kana_field(corrected) == corrected
                        and context.get("person_name") and corrected != normalize_fields({field: auto})[field]
                        and _compact(context["person_name"]) == _compact(result.get("person_name", ""))
                        and not (_identifiers(context) and _identifiers(result) and not _identifiers(context) & _identifiers(result)))
        if eligible and _identifiers(context):
            # A later correction for the same confirmed person supersedes their
            # older card's example too, avoiding contradictory prompt examples.
            others = conn.execute("SELECT id, context_json FROM corrections WHERE owner_user_id = ? AND field = ? AND superseded = 0", (card["owner_user_id"], field)).fetchall()
            for old in others:
                old_context = json.loads(old["context_json"])
                if _compact(old_context.get("person_name", "")) == _compact(context["person_name"]) and _identifiers(old_context) & _identifiers(context):
                    conn.execute("UPDATE corrections SET active = 0, superseded = 1 WHERE id = ?", (old["id"],))
        conn.execute("""INSERT INTO corrections (id, card_id, extraction_id, owner_user_id, field,
            model_value, automatic_value, before_value, corrected_value, context_json, cause,
            eligible, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, card["id"], run["id"] if run else None, card["owner_user_id"], field, model, auto, before,
             corrected, json.dumps(context, ensure_ascii=False), cause, int(eligible), int(eligible), now_iso()))
    # Changing the identity makes old corrections for this card inapplicable.
    if any(key in fields and _compact(fields[key]) != _compact(card[key]) for key in ("person_name", "email", "mobile")):
        conn.execute("UPDATE corrections SET active = 0, superseded = 1 WHERE card_id = ? AND context_json != ?",
                     (card["id"], json.dumps(context, ensure_ascii=False)))


def list_corrections(owner_user_id: str, card_id: str | None = None) -> list[dict]:
    with connection() as conn:
        where = "owner_user_id = ?" + (" AND card_id = ?" if card_id else "")
        params = (owner_user_id, card_id) if card_id else (owner_user_id,)
        rows = conn.execute(f"SELECT * FROM corrections WHERE {where} ORDER BY created_at DESC, rowid DESC", params).fetchall()
        return [{**dict(row), "context": json.loads(row["context_json"])} for row in rows]


def set_correction_active(owner_user_id: str, correction_id: str, active: bool) -> dict | None:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM corrections WHERE id = ? AND owner_user_id = ?", (correction_id, owner_user_id)).fetchone()
        if row is None:
            return None
        if active and (not row["eligible"] or row["superseded"]):
            raise ValueError("この補正は参考情報として有効にできません")
        conn.execute("UPDATE corrections SET active = ? WHERE id = ?", (int(active), correction_id))
        return {**dict(row), "active": int(active), "context": json.loads(row["context_json"])}
