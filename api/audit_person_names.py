"""Offline checks of name decisions against saved OCR; no models or DB writes.

The corpus JSON contains cards, images and optional extraction runs. Imported
text without coordinates is included; this is a postprocessing audit, not an
OCR accuracy measurement or a rerun of the extraction LLM.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from src.services.extractor import _normalize_response
from src.services.name_evidence import compact_name, person_name_is_printed, _with_spatial_name_candidates


def audit(corpus, fresh=False):
    images = defaultdict(list)
    for image in corpus["images"]:
        images[image["card_id"]].append(image)
    failures, unavailable, counts = [], [], Counter()
    for card in corpus["cards"]:
        sides = images[card["id"]]
        source = "\n".join(image["ocr_text"] or "" for image in sides)
        blocks = [{**block, "_side": image["side"]} for image in sides
                  for block in json.loads(image["ocr_blocks_json"] or "[]")]
        source = _with_spatial_name_candidates(source, blocks)
        expected = card.get("person_name") or ""
        if not expected or not person_name_is_printed(expected, source, blocks):
            unavailable.append({"card": card["id"], "name": expected, "reason": "full_registered_name_not_in_ocr"})
            continue
        parts = expected.split()
        variants = {"unchanged": expected, "company_as_name": card.get("company_name") or "株式会社例",
                    "unspaced": compact_name(expected), "glyph_spacing": " ".join(compact_name(expected))}
        if len(parts) == 2:
            variants.update(reversed_name=" ".join(parts[::-1]), partial_family_spacing=f"{' '.join(parts[0])} {parts[1]}")
        for scenario, proposed in variants.items():
            raw = {**{key: card.get(key) or "" for key in ("company_name", "email", "mobile", "person_name_kana")},
                   "person_name": proposed}
            _raw, result, decision = _normalize_response(json.dumps(raw, ensure_ascii=False), source, blocks,
                                                        None if fresh else card)
            counts[scenario] += 1
            if result["person_name"] != expected:
                failures.append({"card": card["id"], "name": expected, "scenario": scenario,
                                 "proposed": proposed, "actual": result["person_name"], "decision": decision})
    return {"summary": {"mode": "fresh" if fresh else "rescan", "cards": len(corpus["cards"]), "eligible_cards": len(corpus["cards"]) - len(unavailable),
                         "checks": sum(counts.values()), "scenarios": dict(counts),
                         "failures": len(failures), "unavailable": len(unavailable)},
            "failures": failures, "unavailable": unavailable}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="do not use previously registered identity")
    args = parser.parse_args()
    report = audit(json.loads(args.corpus.read_text()), fresh=args.fresh)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["summary"], ensure_ascii=False))
    if args.check and report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
