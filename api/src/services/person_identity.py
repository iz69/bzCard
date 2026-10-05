"""Resolve a person's spelling and surname boundary from OCR evidence."""
from __future__ import annotations

import re

from .normalization import _normalize_kana_field
from .name_evidence import (
    _CORPORATE_MARKER, _compact_for_evidence,
    _is_horizontal_neighbour, _is_organization_name, _kanji_text,
    _same_text_line,
    _spatial_name_blocks, _spatial_name_candidates, _spatial_ruby_identities,
    compact_name, person_name_is_printed, printed_reading, same_person,
    PERSON_CHARS, unique_name_candidate,
)


def resolve_person_name(data: dict, source: str, blocks: list[dict], previous: dict | None = None) -> dict:
    """Use printed evidence once; never assume that four kanji mean a 2+2 name."""
    _correct_person_name_order(data, blocks, source)
    name = " ".join(str(data.get("person_name") or "").split())
    is_company = (_is_organization_name(name) or bool(data.get("company_name")
                  and _compact_for_evidence(name) == _compact_for_evidence(data["company_name"])))
    _recover_printed_company(data, source)
    valid = person_name_is_printed(name, source, blocks) and not is_company
    previous_name = str((previous or {}).get("person_name") or "")
    previous_candidate = {**data, "person_name": previous_name}
    if previous and (not valid or compact_name(name) == compact_name(previous_name)):
        if same_person(previous_candidate, previous) and person_name_is_printed(previous_name, source, blocks):
            if compact_name(name) != compact_name(previous_name):
                data["person_name_kana"] = ""
            data["person_name"] = " ".join(previous_name.split())
            return {"status": "resolved", "source": "same_person_previous_boundary"}
    recovered = _recover_name_candidate(data, source, blocks, is_company)
    name = " ".join(str(data.get("person_name") or "").split())
    compact = compact_name(name)
    valid = person_name_is_printed(name, source, blocks)
    if not valid or re.fullmatch(r"[A-Za-z0-9&.-]+", compact) and len(name.split()) < 2:
        data["person_name"] = ""
        data["person_name_kana"] = ""
        retained = retain_previous_identity_from_partial_ocr(data, previous, source, blocks)
        if retained:
            return retained
        return {"status": "uncertain", "reason": "no_grounded_person_name"}
    if same_person(data, previous) and len(str(previous.get("person_name") or "").split()) == 2:
        data["person_name"] = " ".join(previous["person_name"].split())
        return {"status": "resolved", "source": "same_person_previous_boundary"}
    ruby_names = {spelling for spelling, _reading in _spatial_ruby_identities(blocks)
                  if compact_name(spelling) == compact}
    if len(ruby_names) == 1:
        data["person_name"] = ruby_names.pop()
        return {"status": "resolved", "source": "printed_ruby_boundary"}
    data["person_name"] = name
    _correct_person_name_order(data, blocks, source)
    _join_spaced_person_name(data, source, blocks)
    parts = data["person_name"].split()
    if len(parts) == 2:
        return {"status": "resolved", "source": recovered or "grounded_model_name"}
    return {"status": "uncertain", "reason": "surname_boundary_unresolved"}


def _correct_person_name_order(data: dict, blocks: list[dict], source: str = "") -> None:
    parts = (data.get("person_name") or "").split()
    if len(parts) != 2:
        return
    candidates = _spatial_name_candidates(blocks)
    reverse = f"{parts[1]} {parts[0]}"
    if (parts[1] + parts[0] in candidates and parts[0] + parts[1] not in candidates) or (
        source and person_name_is_printed(reverse, source, blocks)
        and not person_name_is_printed(data["person_name"], source, blocks)
    ):
        data["person_name"] = f"{parts[1]} {parts[0]}"


def _join_spaced_person_name(data: dict, raw_text: str, blocks: list[dict]) -> None:
    parts = (data.get("person_name") or "").split()
    if len(parts) == 2:
        # OCR boxes are recognition fragments, not necessarily surname/given
        # boundaries (e.g. "佐藤 正" / "宗"). Do not replace a complete pair.
        return
    compact = compact_name(data.get("person_name"))
    printed = {" ".join(line.split()) for line in raw_text.splitlines()
               if compact_name(line) == compact and len(line.split()) == 2}
    if len(printed) == 1:
        data["person_name"] = printed.pop()
        return
    if not _kanji_text(compact):
        return
    name_blocks = [block for block in _spatial_name_blocks(blocks) if block["text"] in compact]
    candidates = {block["spelling"] for block in name_blocks
                  if block["text"] == compact and len(block["spelling"].split()) == 2}
    for left in name_blocks:
        for right in name_blocks:
            if (left is right or left["side"] != right["side"]
                    or left["box"][0] >= right["box"][0]):
                continue
            if (left["text"] + right["text"] == compact
                    and _same_text_line(left, right)
                    and _is_horizontal_neighbour(left, right, max_gap_ratio=2)):
                candidates.add(f"{left['text']} {right['text']}")
    if len(candidates) == 1:
        data["person_name"] = candidates.pop()
    elif len(parts) > 2:
        # Collapse OCR character spacing; a later verified model/reading match
        # may establish the surname boundary. Do not invent a 2+2 partition.
        data["person_name"] = compact


def _recover_printed_company(data: dict, raw_text: str) -> None:
    companies = [line.strip() for line in raw_text.splitlines()
                 if _CORPORATE_MARKER.search(line) and len(line.strip()) <= 80]
    companies = list(dict.fromkeys(companies))
    if len(companies) == 1:
        data["company_name"] = companies[0]


def _recover_printed_identity(data: dict, raw_text: str, blocks: list[dict] | None = None) -> str | None:
    """Compatibility entry point for isolated recovery tests."""
    name = data.get("person_name") or ""
    model_company = bool(name and data.get("company_name")
                         and _compact_for_evidence(name) == _compact_for_evidence(data["company_name"]))
    _recover_printed_company(data, raw_text)
    return _recover_name_candidate(data, raw_text, blocks or [], model_company)


def _recover_name_candidate(data: dict, raw_text: str, blocks: list[dict], model_company: bool) -> str | None:
    """Select once from the shared candidate catalogue after rejecting the model."""
    name = data.get("person_name") or ""
    if model_company or not name or _is_organization_name(name) or not person_name_is_printed(name, raw_text, blocks or []) or (
        not re.search(f"[{PERSON_CHARS}]", name) and len(name.split()) < 2
    ) or (
        data.get("company_name") and _compact_for_evidence(name) == _compact_for_evidence(data["company_name"])
    ):
        candidate, _candidates = unique_name_candidate(raw_text, blocks)
        if candidate:
            data["person_name"] = candidate.spelling
            if _compact_for_evidence(name) != _compact_for_evidence(data["person_name"]):
                data["person_name_kana"] = ""
            department = data.get("department") or ""
            if re.search(r"(?:店|営業所|事業所)$", name.strip()) and (
                not department or _compact_for_evidence(department) not in _compact_for_evidence(raw_text)
            ):
                data["department"] = name
            return candidate.source
        if data.get("person_name") == name and (
            _is_organization_name(name) or (
                data.get("company_name")
                and _compact_for_evidence(name) == _compact_for_evidence(data["company_name"])
            )
        ):
            data["person_name"] = ""
            data["person_name_kana"] = ""



def _retain_supported_previous_reading(data: dict, previous: dict | None, kana_info: dict) -> bool:
    """Keep a prior reading only when the same person's specialist candidates support it."""
    if not previous or kana_info.get("status") != "applied":
        return False
    if not same_person(data, previous):
        return False
    old_kana = _normalize_kana_field(previous.get("person_name_kana"))
    parts = old_kana.split()
    if len(parts) != 2 or old_kana == data.get("person_name_kana"):
        return False
    family_readings = {_normalize_kana_field(item.get("reading")) for item in kana_info.get("family_candidates") or []}
    full_readings = {_normalize_kana_field(item.get("reading")) for item in kana_info.get("candidates") or []}
    if parts[0] not in family_readings or "".join(parts) not in full_readings:
        return False
    data["person_name_kana"] = old_kana
    return True


def retain_previous_identity_from_partial_ocr(data: dict, previous: dict | None,
                                              source: str, blocks: list[dict]) -> dict | None:
    """Keep existing identity when OCR loses the given name of the same contact.

    Called after invented contact values have been removed. Require BOTH the
    personal email and mobile plus an independently printed surname; this never
    manufactures a new identity from an email account or source filename.
    """
    if data.get("person_name") or not previous:
        return None
    name = " ".join(str(previous.get("person_name") or "").split())
    parts = name.split()
    if len(parts) != 2 or not all(_kanji_text(part) for part in parts) or _is_organization_name(name):
        return None
    email = lambda value: re.sub(r"\s+", "", str(value or "")).casefold()
    mobile = lambda value: re.sub(r"\D", "", str(value or ""))
    if not (email(data.get("email")) and email(data.get("email")) == email(previous.get("email"))
            and len(mobile(data.get("mobile"))) >= 10
            and mobile(data.get("mobile")) == mobile(previous.get("mobile"))):
        return None
    family = compact_name(parts[0])
    if not (any(compact_name(line) == family for line in source.splitlines())
            or any(compact_name(block.get("text")) == family for block in blocks)):
        return None
    kana = _normalize_kana_field(previous.get("person_name_kana"))
    data["person_name"] = name
    data["person_name_kana"] = kana
    return {"status": "retained", "source": "previous_identity_partial_ocr",
            "retain_previous_reading": True,
            "reason": "surname_email_and_mobile_match_given_name_unreadable"}



def _refine_person_name_kana(data: dict, raw_text: str, blocks: list[dict] | None = None) -> None:
    """Apply the shared evidence decision without invoking a model."""
    evidence = printed_reading(data, raw_text, blocks or [])
    if evidence.get("selected"):
        data["person_name_kana"] = evidence["selected"]
    elif evidence.get("status") == "uncertain":
        data["person_name_kana"] = ""
    else:
        kana = _normalize_kana_field(data.get("person_name_kana"))
        data["person_name_kana"] = kana if len(kana.split()) == 2 else ""
