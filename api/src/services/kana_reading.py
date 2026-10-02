"""Select a name reading from the local specialist without losing printed evidence."""
from __future__ import annotations

import logging
import re
import unicodedata

import requests

from ..config import settings
from .normalization import _normalize_kana_field

logger = logging.getLogger("bzcard.kana_reading")


def apply_specialist_reading(data: dict, source: str, blocks: list[dict]) -> dict:
    from .extractor import _explicit_kana_for_name, _ruby_kana_for_name, _printed_identity, _roman_name_pairs, _roman_to_hiragana

    info = {"status": "skipped"}
    if not settings.kana_base_url:
        return info
    name = unicodedata.normalize("NFKC", data.get("person_name") or "").split()
    if len(name) != 2 or not all(re.fullmatch(r"[一-龯々〆ヵヶ]{1,6}", part) for part in name):
        return info
    full_name = "".join(name)
    if _explicit_kana_for_name(source, data["person_name"]) or _ruby_kana_for_name(blocks, data["person_name"]):
        return {"status": "printed"}
    compact_name = "".join(name)
    matched_identity = any("".join(unicodedata.normalize("NFKC", printed).split()) == compact_name
                           for printed, _first, _second in _printed_identity(source))
    current_parts = _normalize_kana_field(data.get("person_name_kana")).split()
    matched_roman = len(current_parts) == 2 and any(
        set(current_parts) == {_roman_to_hiragana(first), _roman_to_hiragana(second)}
        for first, second in _roman_name_pairs(source)
    )
    if data.get("person_name_kana") and (matched_identity or matched_roman):
        return {"status": "roman"}

    try:
        response = requests.post(
            f"{settings.kana_base_url}/predict",
            json={"name": full_name, "family": name[0]},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Kana model unavailable for name reading: %s", exc)
        return {"status": "fallback", "reason": type(exc).__name__}

    candidates = result.get("candidates") or []
    family_candidates = result.get("family_candidates") or []
    info = {"status": "fallback", "model": result.get("model"), "candidates": candidates, "family_candidates": family_candidates}
    if not candidates or not family_candidates:
        return info
    reading = str(candidates[0].get("reading") or "")
    for candidate in family_candidates:
        family = str(candidate.get("reading") or "")
        if family and reading.startswith(family) and len(reading) > len(family):
            selected = _normalize_kana_field(f"{family} {reading[len(family):]}")
            if selected:
                data["person_name_kana"] = selected
                info["status"] = "applied"
                info["selected"] = selected
            break
    return info
