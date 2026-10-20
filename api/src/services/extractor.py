from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import requests

from ..config import settings
from .fields import SCHEMA_KEYS
from .normalization import normalize_fields, _normalize_address
from .feedback import relevant_corrections, prompt_examples, apply_known_reading
from .kana_reading import apply_specialist_reading
from .name_evidence import (
    _compact_for_evidence,
    _is_organization_name,
    _spatial_name_candidates,
    _with_spatial_name_candidates,
    compact_name, person_name_is_printed, name_evidence_scope,
    _box_coordinates, _name_layout_blocks,
    _roman_to_hiragana,
)
from .person_identity import resolve_person_name
from .roles import ROLE_TITLES as _ROLE_TITLES, OCCUPATIONAL_TITLES, is_position_title, split_printed_role
from .address_evidence import prefer_complete_printed_address


class ModelResponse(str):
    model_info: dict


@dataclass
class ExtractionResult:
    data: dict
    duration_ms: int


@name_evidence_scope()
def extract_card_fields(raw_text: str, blocks: list[dict], owner_user_id: str | None = None,
                        previous: dict | None = None) -> ExtractionResult:
    started = time.perf_counter()
    # OCR sometimes reads a visually continuous name as separate blocks.  Keep
    # the original OCR text, but add only geometry-backed candidates so both the
    # extractor and the evidence check can use the reconstructed spelling.
    source_text = _with_spatial_name_candidates(raw_text, blocks)
    corrections = relevant_corrections(owner_user_id, source_text)
    base_prompt = _build_prompt(source_text, blocks)
    prompt = base_prompt + prompt_examples(corrections)
    raw = _generate_structured_response(prompt)
    data, normalized, identity_info, position_info = _normalize_response(raw, source_text, blocks, previous)
    fallback = {}
    company_confusion = (_is_organization_name(data.get("person_name") or "")
                         and bool(_spatial_name_candidates(blocks)))
    if not normalized.get("person_name") and (company_confusion or any(c.get("reading_rules") for c in corrections)):
        # Small models can confuse a component's reading with its kanji name.
        # Retry the original task once, then apply only grounded reading rules.
        fallback = {"rejected_response_text": str(raw), "rejected_model": getattr(raw, "model_info", {})}
        retry_prompt = base_prompt
        if company_confusion:
            retry_prompt += "\n前の応答では会社名が person_name に入っていました。会社名を氏名に使わず、OCRの氏名候補と座標を確認して人物名を抽出してください。確定できなければ空文字にしてください。\n"
        raw = _generate_structured_response(retry_prompt)
        data, normalized, identity_info, position_info = _normalize_response(raw, source_text, blocks, previous)
    kana_info = apply_specialist_reading(normalized, source_text, blocks,
                                         previous=previous, name_decision=identity_info)
    if kana_info.get("segmentation") == "verified":
        identity_info = {"status": "resolved", "source": "reading_verified_boundary"}
    if kana_info.get("name_repair"):
        identity_info = {"status": "resolved", "source": "reading_verified_ocr_character",
                         "repair": kana_info["name_repair"]}
    identity_info["selected"] = normalized.get("person_name") or ""
    automatic = dict(normalized)
    applied = apply_known_reading(
        normalized, source_text, blocks, corrections,
        allow_general=kana_info.get("source") != "previous", reading_decision=kana_info)
    if applied:
        kana_info.update(status="corrected", selected=normalized["person_name_kana"], source="confirmed_correction")
    kana_info['selected'] = normalized.get('person_name_kana') or ''
    normalized["_automatic"] = automatic
    normalized["_feedback"] = {"example_ids": [c["id"] for c in corrections], "applied_ids": applied}
    if fallback:
        normalized["_feedback"]["fallback"] = fallback
    normalized["_raw"] = data
    normalized["_response_text"] = str(raw)
    normalized["_model"] = {**getattr(raw, "model_info", {}), "identity": identity_info, "kana": kana_info}
    if position_info:
        normalized["_model"]["position"] = position_info
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ExtractionResult(data=normalized, duration_ms=duration_ms)


def _normalize_response(raw: str, source_text: str, blocks: list[dict], previous: dict | None = None) -> tuple[dict, dict, dict, dict]:
    data = _parse_json_object(raw)
    normalized = {key: _string_or_empty(data.get(key)) for key in SCHEMA_KEYS}
    _separate_department_and_title(normalized, source_text)
    _recover_printed_department(normalized, source_text)
    _prefer_labeled_phone_numbers(normalized, source_text)
    prefer_complete_printed_address(normalized, source_text)
    _remove_ungrounded_values(normalized, source_text, validate_person_name=False)
    normalized = normalize_fields(normalized)
    identity_info = resolve_person_name(normalized, source_text, blocks, previous)
    _remove_person_from_title(normalized, source_text, blocks)
    position_info = _recover_printed_title(normalized, source_text, blocks)
    return data, normalize_fields(normalized), identity_info, position_info


def _generate_structured_response(prompt: str, schema: dict | None = None) -> str:
    if settings.llm_provider == "ollama":
        return _generate_with_ollama(prompt, schema)
    if settings.llm_provider == "gemini":
        return _generate_with_gemini(prompt, schema)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")


def _generate_with_ollama(prompt: str, schema: dict | None = None) -> str:
    payload = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "format": schema or _extraction_schema(),
        "options": {
            "temperature": 0,
        },
    }
    response = requests.post(
        f"{settings.llm_base_url}/api/generate",
        json=payload,
        timeout=30 if schema else 180,
    )
    response.raise_for_status()
    body = response.json()
    from .model_info import _llm_version_info
    response_text = ModelResponse(body.get("response") or "{}")
    response_text.model_info = _llm_version_info()
    return response_text


def _generate_with_gemini(prompt: str, schema: dict | None = None) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")

    payload = {
        "model": settings.gemini_model,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": schema or _extraction_schema(),
        },
    }
    response = requests.post(
        f"{settings.gemini_base_url}/interactions",
        headers={"x-goog-api-key": settings.gemini_api_key},
        json=payload,
        timeout=30 if schema else 180,
    )
    response.raise_for_status()
    body = response.json()
    result = ModelResponse(_extract_gemini_text(body))
    result.model_info = {"provider": "gemini", "model": settings.gemini_model, "model_version": body.get("modelVersion")}
    return result


def _extraction_schema(keys=SCHEMA_KEYS) -> dict:
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "string",
            }
            for key in keys
        },
        "required": keys,
        "additionalProperties": False,
    }


def _extract_gemini_text(body: dict) -> str:
    if any(key in body for key in SCHEMA_KEYS):
        return json.dumps(body, ensure_ascii=False)

    for key in ("output_text", "text", "response", "output", "content"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value

    chunks = _collect_text_chunks(body)
    if chunks:
        return "\n".join(chunks)
    raise ValueError("Gemini response did not contain text output")


def _collect_text_chunks(value) -> list[str]:
    if isinstance(value, str):
        return []
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_collect_text_chunks(item))
        return chunks
    if not isinstance(value, dict):
        return []

    chunks = []
    text = value.get("text")
    if isinstance(text, str) and text.strip():
        chunks.append(text)

    for key in ("steps", "output", "content", "parts", "candidates", "message"):
        child = value.get(key)
        if isinstance(child, str) and child.strip():
            chunks.append(child)
        elif child is not None:
            chunks.extend(_collect_text_chunks(child))
    return chunks


def _build_prompt(raw_text: str, blocks: list[dict]) -> str:
    marked_text = _marked_text(blocks)
    keys = ", ".join(f'"{key}"' for key in SCHEMA_KEYS)
    large_text_hint = ""
    if marked_text:
        large_text_hint = f"""

以下はOCRブロックのうち大きな文字です。氏名・会社名の候補として優先的に参照してください。
{marked_text}
"""
    spatial_name_hint = ""
    if "【座標補正による氏名候補】" in raw_text:
        spatial_name_hint = """

「座標補正による氏名候補」は、近接した大きな漢字のOCRブロックを名刺上の左から右の位置で結合した補助情報です。
氏名として自然な候補だけを person_name に使ってください。役職・部署に見える候補は person_name に使わないでください。
"""
    return f"""
以下は日本の名刺1枚からOCRで読み取ったテキストです。

{raw_text}
{large_text_hint}
{spatial_name_hint}

次のキーを持つJSONオブジェクトだけを返してください。
不明な項目は空文字にしてください。説明文、Markdown、コードブロックは不要です。
OCRテキストにない値を補完・創作してはいけません。ただし person_name_kana の読みの推測だけは、下記の規則に従って許可します。
株式会社などの法人格を含む行は会社名です。person_name に会社名や法人格を入れないでください。
「店」「支店」「営業所」などの店舗名・拠点名は department に入れ、person_name に入れないでください。
日本語の氏名の直後にローマ字の姓名が印刷されている場合、その日本語行を氏名として優先してください。
email は @ を含むOCR上のメールアドレスだけを入れてください。email を mobile、fax、tel に入れてはいけません。
tel と mobile には電話番号だけを入れてください。fax にはOCR上で FAX と明示された電話番号だけを入れてください。
department には部署名だけを入れ、部・課・グループなどが複数行に分かれている場合は上位から順にすべて含めてください。title には役職名だけを入れてください。OCR上にない項目は空文字にしてください。
「専務取締役」「常務取締役」「代表取締役」「部長」などは役職です。役職だけが印刷され、部署名がない場合は department を空文字にし、役職を title だけに入れてください。
「役職＋姓の一部」のように役職と氏名の一部が同じOCR領域にあっても、役職は title、氏名は person_name に分離してください。title に氏名を含めないでください。
会社名の単語間の空白はOCRに従い、OCRで続けて読めている日本語の社名に空白を追加しないでください。
person_name_kana は氏名の読みをひらがなだけで入れてください。漢字や別人の名前を混ぜないでください。姓名の間には半角スペースを1つ入れてください。
OCRテキスト内にふりがな・フリガナがある場合はそれを優先してください。
ふりがながない場合でも、日本人名として自然で一般的な読みを推測してください。
ローマ字表記がある場合は読み推測の強い手がかりとして使い、person_name_kana にはローマ字ではなくひらがなを入れてください。
ローマ字の氏名表記がある場合は、漢字からの推測よりローマ字を優先して読みを検証してください。ローマ字が名→姓の順でも、person_name_kana は person_name と同じ姓→名の順に並べてください。
例示した別人の氏名や読みを、OCRで確認できない名刺に流用してはいけません。
氏名がOCRで姓・名に分割され、行順が崩れることがあります。大きな日本語名らしい断片が複数ある場合は、会社名・部署名・住所ではないかを確認し、自然な日本人名の姓名順に並べ直してください。
ふりがなはOCRテキストやローマ字表記に存在しない限り、珍しい読みを無理に作らず、判断できない場合は空文字にしてください。
読みがどうしても判断できない場合だけ空文字にしてください。
電話番号、携帯番号、FAX、郵便番号は可能なら半角数字とハイフンに正規化してください。
電話番号、携帯番号、FAXに括弧は使わず、例: 03-0000-0000 の形式にしてください。
住所の番地・丁目・号に使われている漢数字は数字に正規化してください。例: 朝日町一丁目五三番一号 -> 朝日町1丁目53番1号。
ただし地名に含まれる漢数字は変換しないでください。例: 三田、四谷、一番町 はそのままにしてください。

キー: {keys}
""".strip()


def _marked_text(blocks: list[dict]) -> str:
    sizes = sorted(float(block.get("font_size") or 0) for block in blocks)
    threshold = 0
    if sizes:
        threshold = sizes[int(len(sizes) * 0.8)]

    lines = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if threshold > 0 and float(block.get("font_size") or 0) >= threshold:
            lines.append(f"【大文字】{text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM did not return a JSON object")
    return value


def _string_or_empty(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


_DEPARTMENT_END = re.compile(r"(?:本部|事業部|部|課|室|局|支店|営業所|出張所|センター|グループ|チーム)$")


def _classify_position(name: str, source: str, occupation: str, candidate: str) -> dict:
    """Review only the position when a nearby label conflicts with a job category."""
    roman_parts = candidate.split()
    if (len(roman_parts) == 2 and all(re.fullmatch('[A-Za-z]+', part)
                                    and _roman_to_hiragana(part) for part in roman_parts)):
        return {'title': '', 'reason': 'roman_name_candidate'}
    prompt = f'''以下は日本の名刺のOCR全文です。
{source}

対象者: {name}
職種の候補: {occupation}
対象者の氏名のすぐ近くに印刷された、役職かもしれない候補: {candidate}

職種と役職を区別してください。セールスエンジニアなどの職種は occupation に、所長などの職位は title に入れてください。
役職は例示した語や既知の一覧に限定せず、名刺上の本人との対応と意味から判断してください。
title は近くに印刷された別の表現が本人の役職と判断できる場合だけ、その表現をそのまま返してください。
氏名・会社・部署・職種だった場合や不明な場合は title を空文字にしてください。
occupation と title だけを持つJSONを返してください。その他の抽出項目を返してはいけません。'''
    try:
        schema = _extraction_schema(['occupation', 'title'])
        schema['properties']['occupation']['enum'] = [occupation, '']
        schema['properties']['title']['enum'] = [candidate, '']
        raw = _generate_structured_response(prompt, schema)
        result = _parse_json_object(raw)
        return {'title': _string_or_empty(result.get('title')),
                'occupation': _string_or_empty(result.get('occupation')), 'response_text': str(raw)}
    except (requests.RequestException, ValueError) as exc:
        return {'title': '', 'reason': type(exc).__name__}


def _recover_printed_title(data: dict, raw_text: str, blocks: list[dict] | None = None) -> dict:
    """Prefer a standalone role to a fragment copied from professional licenses."""
    decision = {}
    title = compact_name(data.get('title'))
    if title in OCCUPATIONAL_TITLES:
        local = _nearby_title_labels(data.get('person_name') or '', raw_text, blocks or [])
        excluded = {title, compact_name(data.get('department')), compact_name(data.get('company_name'))}
        local = {label for label in local if compact_name(label) not in excluded}
        if len(local) == 1:
            candidate = next(iter(local))
            checked = _classify_position(data.get('person_name') or '', raw_text, title, candidate)
            if compact_name(checked.get('title')) == compact_name(candidate):
                data['title'] = candidate
                decision = {**checked, 'source': 'llm_local_context', 'selected': candidate}
            elif is_position_title(candidate):
                data['title'] = candidate
                decision = {**checked, 'source': 'printed_position_fallback', 'selected': candidate}
    roles = {compact_name(line) for line in raw_text.splitlines()
             if compact_name(line) in _ROLE_TITLES}
    if len(roles) != 1:
        return decision
    qualifications = re.findall(r'[一-龯々]{2,}士', raw_text)
    title = compact_name(data.get('title'))
    if not title or any(title in qualification for qualification in qualifications):
        data['title'] = roles.pop()
    department = compact_name(data.get('department'))
    if (department and not _DEPARTMENT_END.search(department)
            and department not in {compact_name(line) for line in raw_text.splitlines()}
            and any(department in qualification for qualification in qualifications)):
        data['department'] = ''
    return decision


def _nearby_title_labels(name: str, source: str, blocks: list[dict]) -> set[str]:
    if not person_name_is_printed(name, source, blocks):
        return set()
    rows = _name_layout_blocks(blocks)
    names = [b for b in rows if compact_name(b.get('text')) == compact_name(name)]
    positions = set()
    for person in names:
        box = _box_coordinates(person.get('box'))
        if not box:
            continue
        nx1, ny1, nx2, ny2 = box
        for row in rows:
            text = str(row.get('text') or '').strip()
            role = _box_coordinates(row.get('box'))
            if not role or row.get('_side') != person.get('_side') or not text or len(text) > 32:
                continue
            if (compact_name(text) == compact_name(name) or _DEPARTMENT_END.search(compact_name(text))
                    or re.search(r'[@:：/0-9]', text)):
                continue
            x1, y1, x2, y2 = role
            height = max(ny2 - ny1, y2 - y1)
            above = -.25 * height <= ny1 - y2 <= height and min(nx2, x2) > max(nx1, x1)
            beside = (0 <= nx1 - x2 <= 2 * height
                      and min(ny2, y2) - max(ny1, y1) >= min(ny2 - ny1, y2 - y1) * .5)
            if above or beside:
                positions.add(text)
    return positions


def _remove_person_from_title(data: dict, source: str, blocks: list[dict]) -> None:
    name = compact_name(data.get("person_name"))
    title = compact_name(data.get("title"))
    if not name or not title or not person_name_is_printed(name, source, blocks):
        return
    for role in _ROLE_TITLES:
        if title in (role + name, name + role):
            data["title"] = role
            return
    # The model may copy only the first OCR fragment (a title plus surname fragment) to title.
    # Strip it only when that exact combined row is printed and its name
    # fragment starts the independently verified complete person name.
    combined = split_printed_role(data.get("title"))
    rows = [*source.splitlines(), *(str(block.get("text") or "") for block in blocks)]
    if (combined and name.startswith(compact_name(combined[1]))
            and any(compact_name(row) == title for row in rows)):
        data["title"] = combined[0]


def _separate_department_and_title(data: dict, raw_text: str) -> None:
    """Keep printed roles out of departments and split combined department/role lines."""
    department = re.sub(r"\s+", "", data.get("department") or "")
    if department in _ROLE_TITLES and _compact_for_evidence(department) in _compact_for_evidence(raw_text):
        data["department"] = ""
        if not data.get("title"):
            data["title"] = department

    candidates = set()
    for line in raw_text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2 or parts[-1] not in _ROLE_TITLES:
            continue
        department = " ".join(parts[:-1])
        if _DEPARTMENT_END.search(department.replace(" ", "")):
            candidates.add((department, parts[-1]))
    if len(candidates) != 1:
        return

    department, title = candidates.pop()
    current_department = _compact_for_evidence(data.get("department") or "")
    current_title = _compact_for_evidence(data.get("title") or "")
    if current_department or current_title:
        printed_department = _compact_for_evidence(department)
        printed_title = _compact_for_evidence(title)
        if not (
            printed_department in current_department
            or printed_title in current_department
            or printed_department in current_title
            or printed_title in current_title
        ):
            return
    data["department"] = department
    data["title"] = title


def _recover_printed_department(data: dict, raw_text: str) -> None:
    """Restore a full printed hierarchy when the model skipped middle units."""
    department = (data.get("department") or "").strip()
    if not department or _compact_for_evidence(department) in _compact_for_evidence(raw_text):
        return
    parts = department.split()
    if len(parts) < 2:
        return
    candidates = []
    for line in raw_text.splitlines():
        tokens = line.strip().split()
        if len(tokens) < 2 or not all(_DEPARTMENT_END.search(token) for token in tokens):
            continue
        compact_line = _compact_for_evidence(line)
        position = 0
        for part in parts:
            match_at = compact_line.find(_compact_for_evidence(part), position)
            if match_at < 0:
                break
            position = match_at + len(_compact_for_evidence(part))
        else:
            candidates.append(" ".join(tokens))
    if len(set(candidates)) == 1:
        data["department"] = candidates[0]


def _remove_ungrounded_values(data: dict, raw_text: str, validate_person_name: bool = True) -> None:
    """Do not persist contact details or labels invented by the extraction model.

    OCR can be wrong, but a local model must not manufacture an address or a phone
    number that has no basis in the OCR input. Japanese-name kana is deliberately
    excluded because the prompt explicitly permits a reading to be inferred.
    """
    compact_source = _compact_for_evidence(raw_text)
    numeric_values = _number_candidates(raw_text)

    for key in ("person_name", "company_name", "department", "title", "email", "website"):
        if key == "person_name" and not validate_person_name:
            continue
        value = data.get(key, "")
        if value and _compact_for_evidence(value) not in compact_source:
            data[key] = ""

    for key in ("postal_code", "tel", "mobile", "fax"):
        value = data.get(key, "")
        if value and not _number_has_evidence(value, numeric_values):
            data[key] = ""

    fax_values = _numbers_on_labeled_lines(raw_text, r"fax|ファックス")
    if data.get("fax") and not _number_has_evidence(data["fax"], fax_values):
        data["fax"] = ""

    mobile = data.get("mobile", "")
    mobile_digits = _normalized_digits(mobile)
    mobile_values = _numbers_on_labeled_lines(raw_text, r"mobile|cell|携帯|直通")
    if mobile and not (_number_has_evidence(mobile, mobile_values) or re.fullmatch(r"0[789]0\d{8}", mobile_digits)):
        data["mobile"] = ""

    address = data.get("address", "")
    if address and _compact_address_for_evidence(address) not in _compact_address_for_evidence(raw_text):
        data["address"] = ""

    if not data.get("person_name"):
        data["person_name_kana"] = ""


def _normalized_digits(value: str) -> str:
    return re.sub(r"\D", "", value.translate(str.maketrans("０１２３４５６７８９", "0123456789")))


def _number_candidates(raw_text: str) -> set[str]:
    # Keep each printed number separate even when several appear on one line.
    pattern = r"(?<![0-9０-９])(?:\+?[0-9０-９]{1,4}[-ー－−()（）]{1,2}){1,4}[0-9０-９]{2,4}(?![0-9０-９])|(?<![0-9０-９])[0-9０-９]{7,11}(?![0-9０-９])"
    return {_normalized_digits(match) for match in re.findall(pattern, raw_text)}


def _number_has_evidence(value: str, candidates: set[str]) -> bool:
    digits = _normalized_digits(value)
    return digits in candidates or (digits.startswith("0") and "81" + digits[1:] in candidates)


def _prefer_labeled_phone_numbers(data: dict, raw_text: str) -> None:
    for key, label in (("tel", r"\btel(?=\d|[^A-Za-z])|(?<!携帯)電話"),
                       ("fax", r"\bfax(?=\d|[^A-Za-z])|ファックス")):
        numbers = {_local_japanese_number(value) for value in _numbers_on_labeled_lines(raw_text, label)}
        if len(numbers) == 1:
            digits = numbers.pop()
            current = data.get(key) or ""
            if current and _normalized_digits(current) == digits:
                continue
            data[key] = _format_japanese_phone(digits)


def _local_japanese_number(digits: str) -> str:
    return "0" + digits[2:] if digits.startswith("81") else digits


def _format_japanese_phone(digits: str) -> str:
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10 and digits.startswith(("03", "06")):
        return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits


def _numbers_on_labeled_lines(raw_text: str, label_pattern: str) -> set[str]:
    values = set()
    for line in raw_text.splitlines():
        for label in re.finditer(label_pattern, line, flags=re.IGNORECASE):
            following = line[label.end():]
            number = re.match(r"[^0-9０-９+＋\n]{0,12}([+＋]?[0-9０-９][0-9０-９()（）\-ー－−]{5,})", following)
            if number:
                values.add(_normalized_digits(number.group(1)))
    return values


def _compact_address_for_evidence(value: str) -> str:
    # Normalize only street numbers, preserving both place names and digits.
    normalized = _normalize_address(value)
    normalized = re.sub(r"(\d+)(?:丁目|番地?|号)", r"\1-", normalized)
    normalized = re.sub(r"[ー－−]", "-", normalized)
    return re.sub(r"[\s()（）./:：]", "", normalized).strip("-").casefold()
