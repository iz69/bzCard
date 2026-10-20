"""Read-only verification of saved OCR through the current identity pipeline.

Run inside the API environment: python replay_person_identity.py --check
LLM generation is opt-in. No OCR, card updates or feedback writes are performed.
"""
import argparse
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from src.database import DB_PATH
from src.services import feedback, kana_reading
from src.services.extractor import ModelResponse, extract_card_fields


@contextmanager
def readonly_connection():
    # The application connection refreshes contact indexes on read. Audits must
    # never use that connection, including when loading correction examples.
    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        yield conn
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if rescans change current name or reading")
    parser.add_argument("--fresh", action="store_true", help="also report predictions without previous card values")
    parser.add_argument("--with-readings", action="store_true", help="only cards with both a name and a reading")
    parser.add_argument("--latest", action="store_true", help="replay only the latest saved response per card")
    parser.add_argument("--generate", action="store_true", help="call the current LLM using each card's saved OCR, including cards without runs")
    parser.add_argument("--check-fresh", action="store_true", help="also fail on fresh prediction differences (implies --fresh)")
    parser.add_argument("--report", type=Path, help="write detailed JSON to this private local path")
    args = parser.parse_args()
    args.fresh = args.fresh or args.check_fresh
    with readonly_connection() as conn:
        runs = [dict(row) for row in conn.execute("SELECT * FROM extraction_runs ORDER BY created_at, id")]
        cards = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM cards ORDER BY id")
                 if not args.with_readings or (str(row['person_name'] or '').strip() and str(row['person_name_kana'] or '').strip())}
        if not args.generate and not args.with_readings:
            replayable = {run['card_id'] for run in runs}
            cards = {key: card for key, card in cards.items() if key in replayable}
        if args.generate:
            from src.worker import _combined_ocr_text, _combined_ocr_blocks
            images = [dict(row) for row in conn.execute("SELECT * FROM card_images ORDER BY CASE side WHEN 'front' THEN 0 ELSE 1 END, id")]
            runs = [{'id': None, 'card_id': card['id'],
                     'ocr_text': _combined_ocr_text([i for i in images if i['card_id'] == card['id']]),
                     'ocr_blocks_json': json.dumps(_combined_ocr_blocks([i for i in images if i['card_id'] == card['id']]))}
                    for card in cards.values()]
        elif args.latest:
            latest = {run['card_id']: run for run in runs if run['card_id'] in cards}
            by_id = {run['id']: run for run in runs}
            runs = [by_id.get(cards[card_id]['latest_extraction_id'], run) for card_id, run in latest.items()]
        runs = [run for run in runs if run['card_id'] in cards]

    predict = kana_reading._predict
    cache = {}

    def cached_predict(name, family):
        key = (name, family)
        if key not in cache:
            cache[key] = predict(name, family)
        return cache[key]

    failures = 0
    fresh_failures = 0
    reports = []
    from src.services import extractor
    generate = extractor._generate_structured_response
    generated = {}

    def cached_generate(prompt):
        if prompt not in generated:
            generated[prompt] = generate(prompt)
        return generated[prompt]

    with patch.object(feedback, 'connection', readonly_connection), \
         patch.object(kana_reading, '_predict', side_effect=cached_predict):
        for run in runs:
            card = cards[run["card_id"]]
            try:
                if args.generate:
                    generation = patch.object(extractor, '_generate_structured_response', side_effect=cached_generate)
                else:
                    raw = ModelResponse(run["response_text"] or run["raw_json"])
                    raw.model_info = json.loads(run["model_json"])
                    generation = patch.object(extractor, '_generate_structured_response', return_value=raw)
                with generation:
                    if not run['ocr_text'].strip():
                        raise ValueError('No saved OCR for this card')
                    result = extract_card_fields(run["ocr_text"], json.loads(run["ocr_blocks_json"]), card["owner_user_id"], previous=card).data
                    fresh = extract_card_fields(run["ocr_text"], json.loads(run["ocr_blocks_json"]), card["owner_user_id"]).data if args.fresh else None
                changes = {key: {"before": card[key] or "", "after": result[key]}
                           for key in ("person_name", "person_name_kana") if (card[key] or "") != result[key]}
                failures += bool(changes)
                report = {"run": run["id"], "card": card["id"], "name": result["person_name"],
                          "reading": result["person_name_kana"], "changes": changes,
                          "name_decision": result["_model"]["identity"],
                          "reading_source": result["_model"]["kana"].get("source", result["_model"]["kana"]["status"])}
                if fresh:
                    report["fresh"] = {"name": fresh["person_name"], "reading": fresh["person_name_kana"]}
                    fresh_changes = {key: {"before": card[key] or "", "after": fresh[key]}
                                     for key in ("person_name", "person_name_kana") if (card[key] or "") != fresh[key]}
                    report['fresh']['changes'] = fresh_changes
                    fresh_failures += bool(fresh_changes)
                reports.append(report)
                print(json.dumps(report, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures += 1
                fresh_failures += int(args.fresh)
                report = {"run": run["id"], "card": card['id'], "error": str(exc)}
                reports.append(report)
                print(json.dumps(report, ensure_ascii=False), flush=True)
    checked = {run['card_id'] for run in runs}
    summary = {"runs": len(runs), "cards": len(checked), "selected_cards": len(cards),
               "without_inputs": len(set(cards) - checked), "changed_or_failed": failures,
               "model_queries": len(cache), "llm_prompts": len(generated)}
    if args.fresh:
        summary['fresh_changed_or_failed'] = fresh_failures
    if args.report:
        args.report.write_text(json.dumps({'summary': summary, 'results': reports}, ensure_ascii=False, indent=2))
    print(json.dumps(summary), flush=True)
    if (args.check and (failures or summary['without_inputs'])) or (args.check_fresh and (fresh_failures or summary['without_inputs'])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
