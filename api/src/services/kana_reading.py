"""Choose a name reading once, with explicit evidence taking precedence over guesses."""
from __future__ import annotations

import logging
import re

import requests

from ..config import settings
from .normalization import _normalize_kana_field
from .name_evidence import (
    _email_corroborated_roman_pairs, _kanji_text, _prefer_printed_kana,
    KANJI_CHARS, _roman_to_hiragana, compact_name, email_name_parts, printed_reading, same_person,
)
from .person_identity import _retain_supported_previous_reading

logger = logging.getLogger("bzcard.kana_reading")


def _predict(name: str, family: str) -> dict:
    response = requests.post(f"{settings.kana_base_url}/predict",
                             json={"name": name, "family": family}, timeout=30)
    response.raise_for_status()
    return response.json()


def _model_info(result: dict) -> dict:
    return {"status": "fallback", "model": result.get("model"),
            "candidates": result.get("candidates") or [],
            "family_candidates": result.get("family_candidates") or []}


def _candidate_readings(result: dict) -> list[str]:
    readings = []
    for candidate in result.get("candidates") or []:
        reading = _normalize_kana_field(candidate.get("reading"))
        if not reading or " " in reading:
            continue
        for family_candidate in result.get("family_candidates") or []:
            family = _normalize_kana_field(family_candidate.get("reading"))
            if family and " " not in family and reading.startswith(family) and len(reading) > len(family):
                selected = f"{family} {reading[len(family):]}"
                if selected not in readings:
                    readings.append(selected)
    return readings


def apply_specialist_reading(data: dict, source: str, blocks: list[dict],
                             previous: dict | None = None, name_decision: dict | None = None) -> dict:
    name = data.get("person_name") or ""
    if not name:
        data["person_name_kana"] = ""
        return {"status": "uncertain", "reason": "no_person_name"}
    if (name_decision or {}).get("retain_previous_reading") and same_person(data, previous):
        return {"status": "retained", "source": "previous",
                "selected": data.get("person_name_kana") or "",
                "reason": "identity_retained_from_partial_ocr"}
    current = _normalize_kana_field(data.get("person_name_kana"))
    data["person_name_kana"] = current if len(current.split()) == 2 else ""
    old_kana = _normalize_kana_field((previous or {}).get("person_name_kana"))
    if same_person(data, previous) and len(old_kana.split()) == 2:
        data["person_name_kana"] = old_kana
    evidence = printed_reading(data, source, blocks)
    if evidence.get("status") == "uncertain":
        data["person_name_kana"] = ""
        return evidence
    parts = name.split()
    if ((name_decision or {}).get("source") == "assembled_ocr_row"
            and _email_corroborated_roman_pairs(source)):
        restored = _restore_unspaced_name(data, source, blocks)
        if restored and restored.get("segmentation") == "verified":
            return restored
    if len(parts) != 2:
        restored = _restore_unspaced_name(data, source, blocks)
        if restored:
            if restored.get("status") == "uncertain":
                data["person_name_kana"] = ""
            return restored
        data["person_name_kana"] = evidence.get("selected", "")
        return {"status": "uncertain", "reason": "surname_boundary_unresolved"}
    if evidence.get("selected"):
        data["person_name_kana"] = evidence["selected"]
        if evidence["status"] == "printed":
            return evidence
    if not settings.kana_base_url or not all(_kanji_text(part) for part in parts):
        return evidence or _unavailable_reading(data, previous, "reading_model_not_available")
    try:
        result = _predict("".join(parts), parts[0])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Kana model unavailable for name reading: %s", exc)
        return evidence or _unavailable_reading(data, previous, type(exc).__name__)
    info = _model_info(result)
    choices = _candidate_readings(result)
    if evidence.get("status") == "roman" and info["family_candidates"]:
        expected_family = evidence["selected"].split()[0]
        predicted_family = _normalize_kana_field(info["family_candidates"][0].get("reading"))
        if _prefer_printed_kana(predicted_family, expected_family) != predicted_family:
            # A correct full reading cannot validate the wrong kanji boundary.
            # Verify another surname length instead of retaining an OCR split.
            restored = _restore_unspaced_name(data, source, blocks)
            if restored and restored.get("segmentation") == "verified":
                return restored
    validated_evidence = printed_reading(data, source, blocks, info["family_candidates"], info["candidates"])
    if validated_evidence.get("status") == "uncertain":
        data["person_name_kana"] = ""
        return {**info, **validated_evidence}
    # An email/roman reading also constrains the model; a different given-name
    # guess must not replace it. Compatible long vowels may come from the model.
    chosen_evidence = evidence or validated_evidence
    if chosen_evidence.get("selected"):
        selected = validated_evidence.get("selected") or chosen_evidence["selected"]
        data["person_name_kana"] = selected
        info.update(status="applied" if selected in choices else "roman", selected=selected,
                    source="roman")
        return info
    if choices:
        data["person_name_kana"] = choices[0]
        info.update(status="applied", selected=choices[0], source="specialist")
        if _retain_supported_previous_reading(data, previous, info):
            info.update(previous_reading_retained=True, selected=data["person_name_kana"], source="previous")
    else:
        restored = _restore_unspaced_name(data, source, blocks)
        if restored and restored.get("segmentation") == "verified":
            return restored
        info.update(_unavailable_reading(data, previous, "model_reading_cannot_be_segmented"))
    return info


def _unavailable_reading(data: dict, previous: dict | None, reason: str) -> dict:
    old = _normalize_kana_field((previous or {}).get("person_name_kana"))
    if same_person(data, previous) and len(old.split()) == 2:
        data["person_name_kana"] = old
        return {"status": "retained", "source": "previous", "selected": old, "reason": reason}
    return {"status": "fallback", "selected": data.get("person_name_kana") or "", "reason": reason}


def _restore_unspaced_name(data: dict, source: str, blocks: list[dict]) -> dict | None:
    """Verify a surname length with its reading, rather than the kanji count."""
    name = compact_name(data.get("person_name"))
    if not re.fullmatch(f"[{KANJI_CHARS}]{{3,6}}", name) or not settings.kana_base_url:
        return None
    evidence = printed_reading(data, source, blocks)
    if evidence.get("status") == "uncertain":
        return evidence
    pairs = _email_corroborated_roman_pairs(source)
    current = _normalize_kana_field(data.get("person_name_kana")).split()
    single_emails = [parts[0] for parts in email_name_parts(source) if len(parts) == 1]
    spaced_line = any(compact_name(line) == name and len(line.split()) > 2 for line in source.splitlines())
    if not (evidence.get("selected") or pairs or len(current) == 2 or (spaced_line and single_emails)):
        return None
    if len(pairs) > 1:
        # Different full accounts may have opposite orders. Compare the actual
        # phonetic parts rather than selecting whichever appeared first.
        sounds = {frozenset(_roman_to_hiragana(p) for p in pair) for pair in pairs}
        if len(sounds) > 1:
            return {"status": "uncertain", "reason": "conflicting_roman_readings"}
    for length in (2, 1, 3, 4):
        if length >= len(name):
            continue
        try:
            result = _predict(name, name[:length])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Kana model unavailable for name boundary: %s", exc)
            return {"status": "fallback", "reason": type(exc).__name__}
        info = _model_info(result)
        families = info["family_candidates"]
        if not families:
            continue
        family = _normalize_kana_field(families[0].get("reading"))
        if not family or " " in family:
            continue
        candidates = _candidate_readings(result)
        targets = []
        if evidence.get("status") == "printed":
            targets.append((evidence["selected"], "printed"))
        for a, b in pairs:
            first, second = _roman_to_hiragana(a), _roman_to_hiragana(b)
            if first and second:
                if _prefer_printed_kana(family, first) == family:
                    targets.append((f"{family} {second}", "roman"))
                if _prefer_printed_kana(family, second) == family:
                    targets.append((f"{family} {first}", "roman"))
        if not targets and len(current) == 2:
            targets.append((" ".join(current), "applied"))
        if not targets and spaced_line and any(_roman_to_hiragana(s) == family for s in single_emails):
            targets.extend((candidate, "applied") for candidate in candidates[:1])
        verified = []
        for reading, status in targets:
            target_parts = reading.split()
            if len(target_parts) != 2 or target_parts[0] != family:
                continue
            if status == "printed":
                verified.append((reading, status))
                continue
            for candidate in candidates:
                candidate_parts = candidate.split()
                if all(_prefer_printed_kana(c, r) == c for c, r in zip(candidate_parts, target_parts)):
                    verified.append((candidate, status))
                    break
        if not verified:
            continue
        readings = {reading for reading, _status in verified}
        if len(readings) != 1:
            return {"status": "uncertain", "reason": "conflicting_roman_readings"}
        selected, status = verified[0]
        data["person_name"] = f"{name[:length]} {name[length:]}"
        data["person_name_kana"] = selected
        info.update(status=status, selected=selected, segmentation="verified",
                    source="printed" if status == "printed" else "roman" if status == "roman" else "specialist")
        return info
    return {"status": "uncertain", "reason": "surname_boundary_not_verified"}
