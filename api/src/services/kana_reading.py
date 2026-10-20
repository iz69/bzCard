"""Choose a name reading once, with explicit evidence taking precedence over guesses."""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import requests

from ..config import settings
from .normalization import _normalize_kana_field
from .name_evidence import (
    _email_corroborated_roman_pairs, _kanji_text, _prefer_printed_kana,
    _linked_roman_pairs, _roman_ocr_matches, _roman_name_pairs, _model_verified_roman_readings,
    person_name_is_printed, _spatial_name_candidates, _fragmented_ruby_readings, _adjacent_ocr_one,
    _spatial_roman_pairs,
    KANJI_CHARS, _roman_to_hiragana, compact_name, email_name_parts, printed_reading, same_person,
)
from .person_identity import _retain_supported_previous_reading

logger = logging.getLogger("bzcard.kana_reading")


@dataclass
class _PredictionSession:
    results: dict = field(default_factory=dict)
    unavailable: requests.RequestException | None = None


_prediction_session: ContextVar[_PredictionSession | None] = ContextVar('kana_prediction_session', default=None)


@contextmanager
def _prediction_scope():
    token = _prediction_session.set(_PredictionSession())
    try:
        yield
    finally:
        _prediction_session.reset(token)


def _predict(name: str, family: str) -> dict:
    session = _prediction_session.get()
    key = (settings.kana_base_url, name, family)
    if session is not None:
        if session.unavailable is not None:
            raise session.unavailable
        if key in session.results:
            result = session.results[key]
            if isinstance(result, Exception):
                raise result
            return result
    try:
        response = requests.post(f"{settings.kana_base_url}/predict",
                                 json={"name": name, "family": family}, timeout=30)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        if session is not None:
            session.results[key] = exc
            if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
                session.unavailable = exc
        raise
    if session is not None:
        session.results[key] = result
    return result


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


@_prediction_scope()
def apply_specialist_reading(data: dict, source: str, blocks: list[dict],
                             previous: dict | None = None, name_decision: dict | None = None) -> dict:
    name = data.get("person_name") or ""
    if not name:
        data["person_name_kana"] = ""
        return {"status": "uncertain", "reason": "no_person_name"}
    old_kana = _normalize_kana_field((previous or {}).get("person_name_kana"))
    # Retaining the spelling must not skip inference when its reading is blank.
    if ((name_decision or {}).get("retain_previous_reading") and same_person(data, previous)
            and len(old_kana.split()) == 2):
        data["person_name_kana"] = old_kana
        return {"status": "retained", "source": "previous",
                "selected": old_kana,
                "reason": "identity_retained_from_partial_ocr"}
    current = _normalize_kana_field(data.get("person_name_kana"))
    data["person_name_kana"] = current if len(current.split()) == 2 else ""
    if same_person(data, previous) and len(old_kana.split()) == 2:
        data["person_name_kana"] = old_kana
    evidence = printed_reading(data, source, blocks)
    if evidence.get("status") == "uncertain":
        data["person_name_kana"] = ""
        return evidence
    parts = name.split()
    if len(parts) == 2 and evidence.get('status') == 'printed':
        data['person_name_kana'] = evidence['selected']
        return evidence
    repaired = _restore_ocr_one_name(data, source, blocks)
    if repaired:
        return repaired
    spatial_roman = _spatial_roman_pairs(name, blocks)
    if spatial_roman and settings.kana_base_url and not evidence.get('selected'):
        restored = _restore_spatial_roman_boundary(data, spatial_roman)
        if restored:
            return restored
    linked = _linked_roman_pairs(name, source, blocks)
    if len(parts) != 2 or ((name_decision or {}).get("source") == "assembled_ocr_row"
                          and _email_corroborated_roman_pairs(source)):
        restored = _restore_unspaced_name(data, source, blocks)
        if restored and restored.get('segmentation') == 'verified':
            return restored
        if len(parts) != 2:
            if restored:
                if restored.get("status") == "uncertain":
                    data["person_name_kana"] = ""
                return restored
            data["person_name_kana"] = evidence.get("selected", "")
            return {"status": "uncertain", "reason": "surname_boundary_unresolved"}
    if evidence.get("selected"):
        data["person_name_kana"] = evidence["selected"]
    if not settings.kana_base_url or not all(_kanji_text(part) for part in parts):
        if linked and not evidence.get("selected"):
            return _unresolved_roman_reading(data, "reading_model_not_available")
        return evidence or _unavailable_reading(data, previous, "reading_model_not_available")
    try:
        result = _predict("".join(parts), parts[0])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Kana model unavailable for name reading: %s", exc)
        if linked and not evidence.get("selected"):
            return _unresolved_roman_reading(data, type(exc).__name__)
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
    if any(not _roman_to_hiragana(part) for pair in linked for part in pair):
        return _repair_roman_ocr_reading(data, linked, info, chosen_evidence.get("selected"))
    if chosen_evidence.get("selected"):
        selected = validated_evidence.get("selected") or chosen_evidence["selected"]
        initial = evidence.get('selected')
        if initial and initial != selected and _roman_long_vowel_variants(initial, selected):
            # Plain roman letters cannot decide whether an omitted long vowel
            # is present. Do not replace an already compatible reading merely
            # because a lower-ranked surname candidate inserts う.
            selected = initial
            info['long_vowel_variant_retained'] = True
        data["person_name_kana"] = selected
        info.update(status="applied" if selected in choices else "roman", selected=selected,
                    source="roman")
        if validated_evidence.get("verification"):
            info.update(verification=validated_evidence["verification"],
                        roman_pairs=validated_evidence["roman_pairs"])
        return info
    if linked:
        return _unresolved_roman_reading(data, "printed_roman_order_unresolved", info)
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


def _roman_long_vowel_variants(first: str, second: str) -> bool:
    a, b = first.split(), second.split()
    return len(a) == len(b) == 2 and all(
        _prefer_printed_kana(left, right) == left or _prefer_printed_kana(right, left) == right
        for left, right in zip(a, b))


def _unresolved_roman_reading(data: dict, reason: str, info: dict | None = None) -> dict:
    data["person_name_kana"] = ""
    return {**(info or {}), "status": "uncertain", "source": "roman", "selected": "", "reason": reason}


def _restore_ocr_one_name(data: dict, source: str, blocks: list[dict]) -> dict | None:
    """Verify an adjacent 1/一 confusion against both printed roman components."""
    parts = str(data.get("person_name") or "").split()
    if (len(parts) != 2 or not all(_kanji_text(part) for part in parts)
            or not settings.kana_base_url or not _adjacent_ocr_one(data['person_name'], blocks)):
        return None
    if not _linked_roman_pairs(data['person_name'], source, blocks):
        return None
    spelling = f'{parts[0]} {parts[1]}一'
    try:
        result = _predict(compact_name(spelling), parts[0])
    except (requests.RequestException, ValueError):
        return None
    verified = _model_verified_roman_readings(source, result.get('family_candidates') or [], result.get('candidates') or [])
    if len(verified) != 1:
        return None
    reading, pairs = next(iter(verified.items()))
    original = data['person_name']
    data['person_name'], data['person_name_kana'] = spelling, reading
    return {**_model_info(result), 'status': 'roman', 'source': 'roman', 'selected': reading,
            'verification': 'family_and_full_name_model_match', 'roman_pairs': pairs,
            'name_repair': {'method': 'adjacent_ocr_one', 'before': original, 'after': spelling,
                            'observed': '1', 'selected': '一'}}


def _repair_roman_ocr_reading(data: dict, pairs: set[tuple[str, str]], info: dict,
                             selected: str | None) -> dict:
    """Use a supported surname to check an unreadable given-name roman token."""
    families = {_normalize_kana_field(candidate.get("reading"))
                for candidate in info["family_candidates"]}
    families.discard("")
    anchors = []
    for first, second in sorted(pairs):
        a, b = _roman_to_hiragana(first), _roman_to_hiragana(second)
        if a and b:
            continue
        readable, unreadable = (a, second) if a else (b, first)
        supported = {family for family in families if readable and " " not in family
                     and _prefer_printed_kana(family, readable) == family}
        if not supported:
            return _unresolved_roman_reading(data, "printed_roman_surname_unverified", info)
        anchors.append((supported, unreadable))
    try:
        given = _predict(data["person_name"].split()[1], "")
    except (requests.RequestException, ValueError) as exc:
        return _unresolved_roman_reading(data, type(exc).__name__, info)
    info = {**info, "given_candidates": given.get("candidates") or []}
    choices = {selected} if selected else set()
    corrections = []
    for families, token in anchors:
        matches = _roman_ocr_matches(token, info["given_candidates"])
        pair_choices = {f"{family} {match['reading']}" for family in families for match in matches}
        if len(pair_choices) != 1:
            return _unresolved_roman_reading(data, "roman_ocr_correction_unresolved", info)
        choices.update(pair_choices)
        corrections.extend(match for match in matches if match not in corrections)
    if len(choices) != 1:
        return _unresolved_roman_reading(data, "conflicting_roman_readings", info)
    data["person_name_kana"] = choices.pop()
    return {**info, "status": "roman", "source": "roman", "selected": data["person_name_kana"],
            "roman_ocr_corrections": corrections}


def _unavailable_reading(data: dict, previous: dict | None, reason: str) -> dict:
    old = _normalize_kana_field((previous or {}).get("person_name_kana"))
    if same_person(data, previous) and len(old.split()) == 2:
        data["person_name_kana"] = old
        return {"status": "retained", "source": "previous", "selected": old, "reason": reason}
    return {"status": "fallback", "selected": data.get("person_name_kana") or "", "reason": reason}


def _restore_spatial_roman_boundary(data: dict, pairs: set[tuple[str, str]]) -> dict | None:
    """Printed local roman labels can supply a rare given reading absent from the model."""
    name = compact_name(data.get('person_name'))
    if not re.fullmatch(f'[{KANJI_CHARS}]{{3,6}}', name):
        return None
    matches = {}
    for length in range(1, min(4, len(name) - 1) + 1):
        try:
            result = _predict(name, name[:length])
        except (requests.RequestException, ValueError) as exc:
            return _unresolved_roman_reading(data, type(exc).__name__)
        for first, second in pairs:
            for surname, given in ((first, second), (second, first)):
                family_reading, given_reading = _roman_to_hiragana(surname), _roman_to_hiragana(given)
                if not family_reading or not given_reading:
                    continue
                if any(_prefer_printed_kana(_normalize_kana_field(c.get('reading')), family_reading)
                       == _normalize_kana_field(c.get('reading')) for c in result.get('family_candidates') or []):
                    spelling = f'{name[:length]} {name[length:]}'
                    selected = f'{family_reading} {given_reading}'
                    matches[(spelling, selected)] = {
                        **_model_info(result), 'status': 'roman', 'source': 'roman', 'selected': selected,
                        'segmentation': 'verified', 'verification': 'spatial_roman_and_surname_model_match',
                        'roman_pairs': [list(pair) for pair in sorted(pairs)],
                    }
    if len(matches) == 1:
        (spelling, reading), info = next(iter(matches.items()))
        data['person_name'], data['person_name_kana'] = spelling, reading
        return info
    if matches:
        return _unresolved_roman_reading(data, 'conflicting_spatial_roman_boundaries')
    return None


def _restore_unspaced_name(data: dict, source: str, blocks: list[dict]) -> dict | None:
    """Verify a surname length with its reading, rather than the kanji count."""
    name = compact_name(data.get("person_name"))
    if not re.fullmatch(f"[{KANJI_CHARS}]{{3,6}}", name) or not settings.kana_base_url:
        return None
    evidence = printed_reading(data, source, blocks)
    if evidence.get("status") == "uncertain":
        return evidence
    ruby = {reading for spelling, reading in _fragmented_ruby_readings(blocks) if spelling == name}
    if ruby:
        return _restore_component_verified_boundary(data, ruby)
    pairs = _email_corroborated_roman_pairs(source)
    if not evidence.get("selected") and not pairs:
        restored = _restore_model_verified_roman_boundary(data, source, blocks)
        if restored:
            return restored
    current = _normalize_kana_field(data.get("person_name_kana")).split()
    single_emails = [parts[0] for parts in email_name_parts(source) if len(parts) == 1]
    spaced_line = any(compact_name(line) == name and len(line.split()) > 2 for line in source.splitlines())
    fragmented = spaced_line or name in _spatial_name_candidates(blocks)
    if not (evidence.get("selected") or pairs or len(current) == 2 or (fragmented and single_emails)):
        return _restore_component_verified_boundary(data) if fragmented else None
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
        if not targets and fragmented:
            targets.extend((candidate, "applied") for candidate in candidates[:1]
                           if any(_account_supports_reading(account, candidate) for account in single_emails))
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
    if fragmented and not (evidence.get('selected') or pairs):
        restored = _restore_component_verified_boundary(data)
        if restored:
            return restored
    return {"status": "uncertain", "reason": "surname_boundary_not_verified"}


def _restore_component_verified_boundary(data: dict, ruby: set[str] | None = None) -> dict | None:
    """Require independent surname, given-name and full-name model agreement."""
    name = compact_name(data.get("person_name"))
    matches = {}
    preferred = set()
    for length in range(1, min(4, len(name) - 1) + 1):
        try:
            result = _predict(name, name[:length])
            given_result = _predict(name[length:], "")
        except (requests.RequestException, ValueError) as exc:
            return {"status": "uncertain", "reason": type(exc).__name__}
        given_candidates = given_result.get("candidates") or []
        if not given_candidates:
            continue
        givens = {_normalize_kana_field(c.get("reading")) for c in (given_candidates if ruby else given_candidates[:1])}
        full_candidates = result.get("candidates") or []
        model_readings = {_normalize_kana_field(full.get("reading")) for full in full_candidates}
        if ruby:
            readings = set(ruby) | {r for r in model_readings if any(_ruby_matches_model(o, r) for o in ruby)}
        else:
            readings = [_normalize_kana_field(full_candidates[0].get("reading"))] if full_candidates else []
        for reading in readings:
            observed = {r for r in ruby or [] if _ruby_matches_model(r, reading)}
            if not reading or " " in reading or (ruby and not observed):
                continue
            for candidate in result.get("family_candidates") or []:
                family = _normalize_kana_field(candidate.get("reading"))
                if not family or not reading.startswith(family) or reading[len(family):] not in givens:
                    continue
                given = reading[len(family):]
                if not given:
                    continue
                selected = f"{family} {given}"
                info = {**_model_info(result), "status": "printed" if ruby else "applied",
                        "source": "printed" if ruby else "specialist", "selected": selected,
                        "given_candidates": given_candidates, "segmentation": "verified",
                        "verification": "family_given_and_full_name_model_match"}
                if ruby:
                    info["ruby_readings"] = sorted(observed)
                    if reading not in model_readings:
                        info['verification'] = 'family_given_and_printed_ruby_match'
                    if reading not in observed:
                        info["ruby_ocr_corrections"] = {"observed": sorted(observed), "selected": reading}
                matches[(f"{name[:length]} {name[length:]}", selected)] = info
                if (ruby and reading in model_readings
                        and given == _normalize_kana_field(given_candidates[0].get('reading'))):
                    preferred.add((f"{name[:length]} {name[length:]}", selected))
    if preferred:
        matches = {key: value for key, value in matches.items() if key in preferred}
    if len(matches) == 1:
        (spelling, reading), info = next(iter(matches.items()))
        data["person_name"], data["person_name_kana"] = spelling, reading
        return info
    if ruby or matches:
        return {"status": "uncertain", "reason": "fragmented_ruby_not_verified" if not matches else "conflicting_model_boundaries"}
    return None


def _ruby_matches_model(observed: str, predicted: str) -> bool:
    if observed == predicted:
        return True
    # A small っ before a vowel or semivowel cannot form a geminate. Correct
    # only that OCR size confusion, with the full-name model as corroboration.
    corrected = re.sub(r"っ(?=[あいうえおやゆよわん]|$)", "つ", observed, count=1)
    return corrected != observed and corrected == predicted


def _account_supports_reading(account: str, reading: str) -> bool:
    """Check a surname account, with an optional supported given-name initial."""
    family, given = reading.split()
    sound = _roman_to_hiragana(account)
    if sound and _prefer_printed_kana(family, sound) == family:
        return True
    if not re.fullmatch(r"[a-z]{4,}", account):
        return False
    sound = _roman_to_hiragana(account[:-1])
    if not sound or _prefer_printed_kana(family, sound) != family:
        return False
    # The final letter must also agree with the full-name model's given name.
    return any(given.startswith(initial) for suffix in ("a", "i", "u", "e", "o", "ya", "yu", "yo", "hi", "su")
               if (initial := _roman_to_hiragana(account[-1] + suffix)))


def _restore_model_verified_roman_boundary(data: dict, source: str, blocks: list[dict]) -> dict | None:
    """Check all surname lengths against printed roman and independent models."""
    name = compact_name(data.get("person_name"))
    if (not person_name_is_printed(name, source, blocks)
            or not any(all(_roman_to_hiragana(token) for token in pair) for pair in _roman_name_pairs(source))):
        return None
    matches = {}
    for length in (2, 1, 3, 4):
        if length >= len(name):
            continue
        try:
            result = _predict(name, name[:length])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Kana model unavailable for roman name boundary: %s", exc)
            return {"status": "uncertain", "reason": type(exc).__name__}
        info = _model_info(result)
        verified = _model_verified_roman_readings(source, info["family_candidates"], info["candidates"])
        for reading, roman_pairs in verified.items():
            spelling = f"{name[:length]} {name[length:]}"
            matches[(spelling, reading)] = {**info, "status": "roman", "source": "roman", "selected": reading,
                "segmentation": "verified", "verification": "family_and_full_name_model_match",
                "roman_pairs": roman_pairs}
    if len(matches) > 1:
        return {"status": "uncertain", "reason": "conflicting_roman_boundaries"}
    if matches:
        (spelling, reading), info = next(iter(matches.items()))
        data["person_name"], data["person_name_kana"] = spelling, reading
        return info
    return None
