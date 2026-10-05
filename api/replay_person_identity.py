"""Read-only replay of saved OCR/model responses through the current identity pipeline.

Run inside the API environment: python replay_person_identity.py --check
No LLM generation, OCR, card updates or feedback writes are performed.
"""
import argparse
import json
from unittest.mock import patch

from src.database import connection
from src.services import kana_reading
from src.services.extractor import ModelResponse, extract_card_fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if rescans change current name or reading")
    parser.add_argument("--fresh", action="store_true", help="also report predictions without previous card values")
    args = parser.parse_args()
    with connection() as conn:
        runs = [dict(row) for row in conn.execute("SELECT * FROM extraction_runs ORDER BY created_at, id")]
        cards = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM cards WHERE id IN (SELECT card_id FROM extraction_runs)")}

    post = kana_reading.requests.post
    cache = {}

    def cached_predict(url, **kwargs):
        query = kwargs.get("json") or {}
        key = (url, query.get("name"), query.get("family"))
        if key not in cache:
            cache[key] = post(url, **kwargs)
        return cache[key]

    failures = 0
    with patch("src.services.kana_reading.requests.post", side_effect=cached_predict):
        for run in runs:
            card = cards[run["card_id"]]
            raw = ModelResponse(run["response_text"] or run["raw_json"])
            raw.model_info = json.loads(run["model_json"])
            try:
                with patch("src.services.extractor._generate_structured_response", return_value=raw):
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
                print(json.dumps(report, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures += 1
                print(json.dumps({"run": run["id"], "error": str(exc)}, ensure_ascii=False), flush=True)
    print(json.dumps({"runs": len(runs), "cards": len(cards), "changed_or_failed": failures,
                      "model_queries": len(cache)}), flush=True)
    if args.check and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
