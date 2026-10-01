from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import requests

from ..config import settings
from .fields import SCHEMA_KEYS
from .normalization import normalize_fields, _normalize_address, _normalize_kana_field
from .feedback import relevant_corrections, prompt_examples, apply_known_reading


class ModelResponse(str):
    model_info: dict


@dataclass
class ExtractionResult:
    data: dict
    duration_ms: int




def extract_card_fields(raw_text: str, blocks: list[dict], owner_user_id: str | None = None) -> ExtractionResult:
    started = time.perf_counter()
    # OCR sometimes reads a visually continuous name as separate blocks.  Keep
    # the original OCR text, but add only geometry-backed candidates so both the
    # extractor and the evidence check can use the reconstructed spelling.
    source_text = _with_spatial_name_candidates(raw_text, blocks)
    corrections = relevant_corrections(owner_user_id, source_text)
    base_prompt = _build_prompt(source_text, blocks)
    prompt = base_prompt + prompt_examples(corrections)
    raw = _generate_structured_response(prompt)
    data, normalized = _normalize_response(raw, source_text, blocks)
    fallback = {}
    if any(c.get("reading_rules") for c in corrections) and not normalized.get("person_name"):
        # Small models can confuse a component's reading with its kanji name.
        # Retry the original task once, then apply only grounded reading rules.
        fallback = {"rejected_response_text": str(raw), "rejected_model": getattr(raw, "model_info", {})}
        raw = _generate_structured_response(base_prompt)
        data, normalized = _normalize_response(raw, source_text, blocks)
    automatic = dict(normalized)
    applied = apply_known_reading(normalized, source_text, blocks, corrections)
    normalized["_automatic"] = automatic
    normalized["_feedback"] = {"example_ids": [c["id"] for c in corrections], "applied_ids": applied}
    if fallback:
        normalized["_feedback"]["fallback"] = fallback
    normalized["_raw"] = data
    normalized["_response_text"] = str(raw)
    normalized["_model"] = getattr(raw, "model_info", {})
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ExtractionResult(data=normalized, duration_ms=duration_ms)


def _normalize_response(raw: str, source_text: str, blocks: list[dict]) -> tuple[dict, dict]:
    data = _parse_json_object(raw)
    normalized = {key: _string_or_empty(data.get(key)) for key in SCHEMA_KEYS}
    _recover_printed_identity(normalized, source_text)
    _correct_person_name_order(normalized, blocks)
    _refine_person_name_kana(normalized, source_text, blocks)
    _separate_department_and_title(normalized, source_text)
    _prefer_labeled_phone_numbers(normalized, source_text)
    _remove_ungrounded_values(normalized, source_text)
    return data, normalize_fields(normalized)


def _generate_structured_response(prompt: str) -> str:
    if settings.llm_provider == "ollama":
        return _generate_with_ollama(prompt)
    if settings.llm_provider == "gemini":
        return _generate_with_gemini(prompt)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")


def _generate_with_ollama(prompt: str) -> str:
    payload = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "format": _extraction_schema(),
        "options": {
            "temperature": 0,
        },
    }
    response = requests.post(
        f"{settings.llm_base_url}/api/generate",
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    body = response.json()
    from .model_info import _llm_version_info
    response_text = ModelResponse(body.get("response") or "{}")
    response_text.model_info = _llm_version_info()
    return response_text


def _generate_with_gemini(prompt: str) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")

    payload = {
        "model": settings.gemini_model,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": _extraction_schema(),
        },
    }
    response = requests.post(
        f"{settings.gemini_base_url}/interactions",
        headers={"x-goog-api-key": settings.gemini_api_key},
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    body = response.json()
    result = ModelResponse(_extract_gemini_text(body))
    result.model_info = {"provider": "gemini", "model": settings.gemini_model, "model_version": body.get("modelVersion")}
    return result


def _extraction_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "string",
            }
            for key in SCHEMA_KEYS
        },
        "required": SCHEMA_KEYS,
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
日本語の氏名の直後にローマ字の姓名が印刷されている場合、その日本語行を氏名として優先してください。
email は @ を含むOCR上のメールアドレスだけを入れてください。email を mobile、fax、tel に入れてはいけません。
tel と mobile には電話番号だけを入れてください。fax にはOCR上で FAX と明示された電話番号だけを入れてください。
department には部署名だけを入れ、部・課・グループなどが複数行に分かれている場合は上位から順にすべて含めてください。title には役職名だけを入れてください。同じ行に「営業部 課長」と印刷されている場合、department は「営業部」、title は「課長」です。OCR上にない項目は空文字にしてください。
person_name_kana は氏名の読みをひらがなだけで入れてください。漢字や別人の名前を混ぜないでください。姓名の間には半角スペースを1つ入れてください。
OCRテキスト内にふりがな・フリガナがある場合はそれを優先してください。
ふりがながない場合でも、日本人名として自然で一般的な読みを推測してください。
ローマ字表記がある場合は読み推測の強い手がかりとして使い、person_name_kana にはローマ字ではなくひらがなを入れてください。
ローマ字の氏名表記がある場合は、漢字からの推測よりローマ字を優先して読みを検証してください。ローマ字が名→姓の順でも、person_name_kana は person_name と同じ姓→名の順に並べてください。
例示した別人の氏名や読みを、OCRで確認できない名刺に流用してはいけません。
氏名がOCRで姓・名に分割され、行順が崩れることがあります。大きな日本語名らしい断片が複数ある場合は、会社名・部署名・住所ではないかを確認し、自然な日本人名の姓名順に並べ直してください。
ふりがなはOCRテキストやローマ字表記に存在しない限り、珍しい読みを無理に作らず、判断できない場合は空文字にしてください。
氏名や社名に「峠」が含まれる場合、姓としての読みは「とうげ」を優先してください。
読みがどうしても判断できない場合だけ空文字にしてください。
電話番号、携帯番号、FAX、郵便番号は可能なら半角数字とハイフンに正規化してください。
電話番号、携帯番号、FAXに括弧は使わず、例: 0465-81-5877 の形式にしてください。
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


def _with_spatial_name_candidates(raw_text: str, blocks: list[dict]) -> str:
    """Append names reconstructed from adjacent, large kanji OCR blocks.

    A frequent example is a surname split into two boxes while the given name is
    in a third box.  Their top coordinates can differ by a few pixels, so OCR's
    normal top-to-bottom sort produces ``橋 / 秀 明 / 口``.  The boxes themselves
    still retain enough layout information to safely reconstruct ``橋口秀明``.
    """
    candidates = _spatial_name_candidates(blocks)
    if not candidates:
        return raw_text
    lines = "\n".join(f"- {candidate}" for candidate in candidates)
    return f"{raw_text}\n\n【座標補正による氏名候補】\n{lines}"


def _spatial_name_candidates(blocks: list[dict]) -> list[str]:
    """Return likely Japanese names formed by horizontally adjacent OCR boxes."""
    by_side: dict[str, list[dict]] = {}
    for block in blocks:
        text = _kanji_text(block.get("text"))
        box = _box_coordinates(block.get("box"))
        if not text or box is None:
            continue
        x1, y1, x2, y2 = box
        height = y2 - y1
        # Small address/company text creates many accidental neighbours.  The
        # name is normally among the prominent text on a business card.
        if height < 40:
            continue
        by_side.setdefault(str(block.get("_side") or "unknown"), []).append(
            {"text": text, "box": box}
        )

    candidates: list[str] = []
    for side_blocks in by_side.values():
        lines: list[list[dict]] = []
        for block in sorted(side_blocks, key=lambda item: (item["box"][1] + item["box"][3]) / 2):
            line = next((line for line in lines if _same_text_line(line[0], block)), None)
            if line is None:
                lines.append([block])
            else:
                line.append(block)
        for line in lines:
            line.sort(key=lambda item: item["box"][0])
            run: list[dict] = []
            for block in line:
                if run and _is_horizontal_neighbour(run[-1], block):
                    run.append(block)
                    continue
                _append_name_candidate(candidates, run)
                run = [block]
            _append_name_candidate(candidates, run)
    return candidates


def _same_text_line(left: dict, right: dict) -> bool:
    left_y1, left_y2 = left["box"][1], left["box"][3]
    right_y1, right_y2 = right["box"][1], right["box"][3]
    return abs((left_y1 + left_y2) - (right_y1 + right_y2)) / 2 <= min(left_y2 - left_y1, right_y2 - right_y1) * 0.4


def _correct_person_name_order(data: dict, blocks: list[dict]) -> None:
    """Use the printed left-to-right order when the model reverses two name blocks."""
    parts = (data.get("person_name") or "").split()
    if len(parts) != 2:
        return
    candidates = _spatial_name_candidates(blocks)
    if parts[1] + parts[0] in candidates and parts[0] + parts[1] not in candidates:
        data["person_name"] = f"{parts[1]} {parts[0]}"


def _append_name_candidate(candidates: list[str], blocks: list[dict]) -> None:
    if len(blocks) < 2:
        return
    candidate = "".join(block["text"] for block in blocks)
    # Two to six kanji covers ordinary Japanese full names while excluding most
    # split department labels and sentences.
    if 2 <= len(candidate) <= 6 and candidate not in candidates:
        candidates.append(candidate)


def _is_horizontal_neighbour(left: dict, right: dict) -> bool:
    left_x1, left_y1, left_x2, left_y2 = left["box"]
    right_x1, right_y1, right_x2, right_y2 = right["box"]
    left_height = left_y2 - left_y1
    right_height = right_y2 - right_y1
    vertical_overlap = min(left_y2, right_y2) - max(left_y1, right_y1)
    if vertical_overlap < min(left_height, right_height) * 0.4:
        return False
    center_difference = abs((left_y1 + left_y2) - (right_y1 + right_y2)) / 2
    if center_difference > min(left_height, right_height) * 0.4:
        return False
    gap = right_x1 - left_x2
    return -min(left_height, right_height) * 0.3 <= gap <= (left_height + right_height) * 0.7


def _kanji_text(value) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    if re.fullmatch(r"[一-龯々〆ヵヶ]+", text):
        return text
    return ""


def _box_coordinates(value) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        x1, y1, x2, y2 = (float(value[index]) for index in range(4))
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


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


_CORPORATE_MARKER = re.compile(
    r"株式会社|有限会社|合同会社|合名会社|合資会社|医療法人|学校法人|社会福祉法人|社団法人|財団法人"
)


def _printed_identity(raw_text: str) -> list[tuple[str, str, str]]:
    """Find a Japanese name followed by its roman spelling, corroborated by email."""
    lines = [line.strip() for line in raw_text.splitlines()]
    email_pairs = {
        tuple(part.casefold() for part in re.split(r"[._-]", local))
        for local in re.findall(r"\b([A-Za-z][A-Za-z0-9._-]*)@", raw_text)
    }
    found = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"([A-Za-z]{3,})\s+([A-Za-z]{3,})", line)
        if not match or tuple(part.casefold() for part in match.groups()) not in email_pairs:
            continue
        for previous in (index - 1, index - 2):
            if previous < 0:
                continue
            candidate = lines[previous]
            if previous == index - 2 and not re.fullmatch(r"\d+", lines[index - 1]):
                continue
            if _CORPORATE_MARKER.search(candidate):
                continue
            if re.fullmatch(r"[一-龯々〆ヵヶ]{1,4}[\s　]+[一-龯々〆ヵヶ]{1,4}", candidate):
                found.append((" ".join(candidate.split()), *[part.casefold() for part in match.groups()]))
                break
    return found


def _recover_printed_identity(data: dict, raw_text: str) -> None:
    """Use unambiguous OCR labels when the model confuses a person and company."""
    companies = [line.strip() for line in raw_text.splitlines()
                 if _CORPORATE_MARKER.search(line) and len(line.strip()) <= 80]
    companies = list(dict.fromkeys(companies))
    if len(companies) == 1:
        data["company_name"] = companies[0]

    name = data.get("person_name") or ""
    if not name or _CORPORATE_MARKER.search(name) or (
        data.get("company_name") and _compact_for_evidence(name) == _compact_for_evidence(data["company_name"])
    ):
        identities = _printed_identity(raw_text)
        if len(identities) == 1:
            data["person_name"] = identities[0][0]


_ROLE_TITLES = {
    "代表取締役社長", "代表取締役", "取締役", "執行役員", "社長", "副社長",
    "部長", "課長", "室長", "係長", "主任", "技師", "編集者", "研究員",
    "担当", "デザイナー", "リーダー", "エンジニア",
}
_DEPARTMENT_END = re.compile(r"(?:本部|事業部|部|課|室|局|支店|営業所|センター|グループ|チーム)$")


def _separate_department_and_title(data: dict, raw_text: str) -> None:
    """Use a printed department/role line when the model combines both fields."""
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


def _refine_person_name_kana(data: dict, raw_text: str, blocks: list[dict] | None = None) -> None:
    """Prefer printed phonetic evidence over an LLM's kanji-only guess."""
    name = data.get("person_name") or ""
    kana = data.get("person_name_kana") or ""
    if not name:
        return

    explicit_kana = _explicit_kana_for_name(raw_text, name) or _ruby_kana_for_name(blocks or [], name)
    if explicit_kana:
        data["person_name_kana"] = explicit_kana
        return

    current_parts = _kana_parts(kana)
    valid_kana = len(current_parts) == 2 and all(re.fullmatch(r"[ぁ-ゖー]+", part) for part in current_parts)
    for printed_name, first_roman, second_roman in _printed_identity(raw_text):
        if _compact_for_evidence(printed_name) != _compact_for_evidence(name):
            continue
        ordered = _ordered_roman_reading(first_roman, second_roman, current_parts)
        if ordered is None:
            continue
        family, given = ordered
        if given and family:
            data["person_name_kana"] = f"{_prefer_printed_kana(current_parts[0], family) if valid_kana else family} {_prefer_printed_kana(current_parts[1], given) if valid_kana else given}"
            return
    for given_roman, family_roman in _roman_name_pairs(raw_text):
        given = _roman_to_hiragana(given_roman)
        family = _roman_to_hiragana(family_roman)
        if not given or not family:
            continue
        # The common printed order is given-name first (HANAKO AOBA), while the
        # Japanese field is family-name first.  Require one exact matching part
        # so a generic email address cannot overwrite an unrelated name.
        if valid_kana and (current_parts[0] == family or current_parts[1] == given):
            data["person_name_kana"] = f"{_prefer_printed_kana(current_parts[0], family)} {_prefer_printed_kana(current_parts[1], given)}"
            return
        if valid_kana and (current_parts[0] == given or current_parts[1] == family):
            data["person_name_kana"] = f"{_prefer_printed_kana(current_parts[0], given)} {_prefer_printed_kana(current_parts[1], family)}"
            return
        if not valid_kana and _surname_roman_hint(raw_text) == family_roman:
            data["person_name_kana"] = f"{family} {given}"
            return
    if not valid_kana:
        data["person_name_kana"] = ""


def _ordered_roman_reading(first: str, second: str, current: list[str]) -> tuple[str, str] | None:
    a, b = _roman_to_hiragana(first), _roman_to_hiragana(second)
    if not a or not b:
        return None
    if len(current) == 2:
        same = current[0] == a or current[1] == b
        reversed_order = current[0] == b or current[1] == a
        if same != reversed_order:
            return (a, b) if same else (b, a)
    # An ambiguous pair is left to the model, never blindly reversed.
    given_names = {"taro", "tarou", "hanako", "ayaka", "tomoko", "yumi", "takuya", "mayu", "daisuke",
                   "shota", "shouta", "makoto", "ryo", "ryou", "hiroshi", "kenichi", "yoko", "yuko", "yosuke"}
    if (first in given_names) != (second in given_names):
        return (b, a) if first in given_names else (a, b)
    return None


def _prefer_printed_kana(current: str, roman: str) -> str:
    """Keep a plausible printed reading when roman letters omit one long vowel."""
    if current == roman or (
        current.count("う") == roman.count("う") + 1
        and current.replace("う", "") == roman.replace("う", "")
    ):
        return current
    return roman


def _surname_roman_hint(raw_text: str) -> str:
    """The final component of an email local part is often the family name."""
    for local_part in re.findall(r"\b([A-Za-z][A-Za-z0-9._-]*)@", raw_text):
        parts = re.split(r"[._-]+", local_part.casefold())
        if len(parts) >= 2 and len(parts[-1]) >= 3 and parts[-1].isalpha():
            return parts[-1]
    return ""


def _explicit_kana_for_name(raw_text: str, name: str) -> str:
    compact_name = re.sub(r"\s+", "", name)
    for line in raw_text.splitlines():
        # Remove the entire name first so a kana given name cannot become ruby.
        compact_line = re.sub(r"\s+", "", line)
        if compact_name not in compact_line:
            continue
        match = re.search(r"[（(]([ぁ-ゖァ-ヶー]+[\s　]+[ぁ-ゖァ-ヶー]+)[）)]", line)
        if match and _normalize_kana_field(match.group(1)):
            return _normalize_kana_field(match.group(1))
        # Unlabelled fragments such as 'カナ' are not a full-name reading.
        suffix = re.sub(r"\s+", "", compact_line.split(compact_name, 1)[1])
        labelled = re.match(r"(?:ふりがな|フリガナ|かな|カナ)[:：]?([ぁ-ゖァ-ヶー]+)", suffix)
        if labelled:
            return _normalize_kana_field(labelled.group(1))
    return ""


def _ruby_kana_for_name(blocks: list[dict], name: str) -> str:
    compact_name = re.sub(r"\s+", "", name)
    results = set()
    for name_block in blocks:
        if re.sub(r"\s+", "", str(name_block.get("text") or "")) != compact_name:
            continue
        name_box = _box_coordinates(name_block.get("box"))
        if name_box is None:
            continue
        nx1, ny1, nx2, ny2 = name_box
        height = ny2 - ny1
        nearby = []
        for block in blocks:
            if block is name_block or block.get("_side") != name_block.get("_side"):
                continue
            text = str(block.get("text") or "").strip()
            box = _box_coordinates(block.get("box"))
            if not box or not _normalize_kana_field(text):
                continue
            x1, y1, x2, y2 = box
            # Ruby is smaller, on a distinct row, and overlaps the name.
            if y2 - y1 >= height * .8 or min(nx2, x2) <= max(nx1, x1):
                continue
            if not (y2 <= ny1 or y1 >= ny2):
                continue
            gap = min(abs(ny1 - y2), abs(y1 - ny2))
            if gap <= height * 1.25:
                nearby.append((x1, y1, text))
        if len(nearby) == 2 and abs(nearby[0][1] - nearby[1][1]) <= height * .4:
            nearby.sort()
            results.add(" ".join(_normalize_kana_field(text) for _x, _y, text in nearby))
        elif len(nearby) == 1 and len(nearby[0][2].split()) == 2:
            results.add(_normalize_kana_field(nearby[0][2]))
    return results.pop() if len(results) == 1 else ""


def _roman_name_pairs(raw_text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    def add_pair(first: str, second: str) -> None:
        pair = (first.casefold(), second.casefold())
        if pair not in pairs and all(len(part) >= 3 for part in pair):
            pairs.append(pair)

    for line in raw_text.splitlines():
        words = re.findall(r"[A-Za-z]+", line)
        if len(words) == 2 and re.fullmatch(r"[A-Za-z\s.'’-]+", line.strip()):
            add_pair(words[0], words[1])

    for local_part in re.findall(r"\b([A-Za-z][A-Za-z0-9._-]*)@", raw_text):
        words = [word for word in re.split(r"[._-]+", local_part) if word.isalpha()]
        if len(words) == 2:
            add_pair(words[0], words[1])
    return pairs


def _kana_parts(value: str) -> list[str]:
    normalized = _normalize_kana_evidence(value)
    return normalized.split() if normalized else []


def _normalize_kana_evidence(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    chars = []
    for char in text:
        code = ord(char)
        chars.append(chr(code - 0x60) if 0x30A1 <= code <= 0x30F6 else char)
    return "".join(chars)


def _roman_to_hiragana(value: str) -> str:
    text = re.sub(r"[^a-z]", "", value.casefold())
    if not text:
        return ""
    # Hepburn spellings often omit long vowels in common given names.  Keep
    # these readings explicit rather than turning e.g. SHOTA into しょた.
    common_given_names = {
        "yosuke": "ようすけ", "yousuke": "ようすけ",
        "shota": "しょうた", "shouta": "しょうた",
        "ryo": "りょう", "ryou": "りょう",
        "koji": "こうじ", "kouji": "こうじ",
        "taro": "たろう", "tarou": "たろう",
        "yuko": "ゆうこ", "yuuko": "ゆうこ",
        "kyoko": "きょうこ", "kyouko": "きょうこ",
        "kenichi": "けんいち",
    }
    if text in common_given_names:
        return common_given_names[text]
    syllables = {
        "kya": "きゃ", "kyu": "きゅ", "kyo": "きょ", "sha": "しゃ", "shu": "しゅ", "sho": "しょ",
        "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ", "nya": "にゃ", "nyu": "にゅ", "nyo": "にょ",
        "hya": "ひゃ", "hyu": "ひゅ", "hyo": "ひょ", "mya": "みゃ", "myu": "みゅ", "myo": "みょ",
        "rya": "りゃ", "ryu": "りゅ", "ryo": "りょ", "gya": "ぎゃ", "gyu": "ぎゅ", "gyo": "ぎょ",
        "bya": "びゃ", "byu": "びゅ", "byo": "びょ", "pya": "ぴゃ", "pyu": "ぴゅ", "pyo": "ぴょ",
        "shi": "し", "chi": "ち", "tsu": "つ", "fu": "ふ", "ji": "じ",
        "ka": "か", "ki": "き", "ku": "く", "ke": "け", "ko": "こ",
        "sa": "さ", "su": "す", "se": "せ", "so": "そ",
        "ta": "た", "te": "て", "to": "と", "na": "な", "ni": "に", "nu": "ぬ", "ne": "ね", "no": "の",
        "ha": "は", "hi": "ひ", "he": "へ", "ho": "ほ", "ma": "ま", "mi": "み", "mu": "む", "me": "め", "mo": "も",
        "ya": "や", "yu": "ゆ", "yo": "よ", "ra": "ら", "ri": "り", "ru": "る", "re": "れ", "ro": "ろ",
        "wa": "わ", "wo": "を", "ga": "が", "gi": "ぎ", "gu": "ぐ", "ge": "げ", "go": "ご",
        "za": "ざ", "zu": "ず", "ze": "ぜ", "zo": "ぞ", "da": "だ", "de": "で", "do": "ど",
        "ba": "ば", "bi": "び", "bu": "ぶ", "be": "べ", "bo": "ぼ", "pa": "ぱ", "pi": "ぴ", "pu": "ぷ", "pe": "ぺ", "po": "ぽ",
        "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
    }
    result = []
    while text:
        if len(text) >= 2 and text[0] == text[1] and text[0] not in "aeioun":
            result.append("っ")
            text = text[1:]
            continue
        if text[0] == "n" and (len(text) == 1 or text[1] not in "aiueoy"):
            result.append("ん")
            text = text[1:]
            continue
        match = next((token for token in sorted(syllables, key=len, reverse=True) if text.startswith(token)), None)
        if match is None:
            return ""
        result.append(syllables[match])
        text = text[len(match):]
    return "".join(result)


def _remove_ungrounded_values(data: dict, raw_text: str) -> None:
    """Do not persist contact details or labels invented by the extraction model.

    OCR can be wrong, but a local model must not manufacture an address or a phone
    number that has no basis in the OCR input. Japanese-name kana is deliberately
    excluded because the prompt explicitly permits a reading to be inferred.
    """
    compact_source = _compact_for_evidence(raw_text)
    numeric_values = _number_candidates(raw_text)

    for key in ("person_name", "company_name", "department", "title", "email", "website"):
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


def _compact_for_evidence(value: str) -> str:
    return re.sub(r"[\s()（）\-ー－−./:：・·･]", "", value).casefold()


def _normalized_digits(value: str) -> str:
    return re.sub(r"\D", "", value.translate(str.maketrans("０１２３４５６７８９", "0123456789")))


def _number_candidates(raw_text: str) -> set[str]:
    # Keep each printed number separate even when several appear on one line.
    pattern = r"(?<![0-9０-９])(?:\+?[0-9０-９]{1,4}[-ー－−()（）]){1,4}[0-9０-９]{2,4}(?![0-9０-９])|(?<![0-9０-９])[0-9０-９]{7,11}(?![0-9０-９])"
    return {_normalized_digits(match) for match in re.findall(pattern, raw_text)}


def _number_has_evidence(value: str, candidates: set[str]) -> bool:
    digits = _normalized_digits(value)
    return digits in candidates or (digits.startswith("0") and "81" + digits[1:] in candidates)


def _prefer_labeled_phone_numbers(data: dict, raw_text: str) -> None:
    for key, label in (("tel", r"\btel\b|電話"), ("fax", r"\bfax\b|ファックス")):
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
