from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import requests

from ..config import settings


@dataclass
class ExtractionResult:
    data: dict
    duration_ms: int


SCHEMA_KEYS = [
    "person_name",
    "person_name_kana",
    "company_name",
    "department",
    "title",
    "postal_code",
    "address",
    "tel",
    "mobile",
    "fax",
    "email",
    "website",
]


def extract_card_fields(raw_text: str, blocks: list[dict]) -> ExtractionResult:
    started = time.perf_counter()
    # OCR sometimes reads a visually continuous name as separate blocks.  Keep
    # the original OCR text, but add only geometry-backed candidates so both the
    # extractor and the evidence check can use the reconstructed spelling.
    source_text = _with_spatial_name_candidates(raw_text, blocks)
    prompt = _build_prompt(source_text, blocks)
    raw = _generate_structured_response(prompt)
    data = _parse_json_object(raw)
    normalized = {key: _string_or_empty(data.get(key)) for key in SCHEMA_KEYS}
    _refine_person_name_kana(normalized, source_text, blocks)
    _remove_ungrounded_values(normalized, source_text)
    normalized["_raw"] = data
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ExtractionResult(data=normalized, duration_ms=duration_ms)


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
    return body.get("response") or "{}"


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
    return _extract_gemini_text(response.json())


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
email は @ を含むOCR上のメールアドレスだけを入れてください。email を mobile、fax、tel に入れてはいけません。
tel と mobile には電話番号だけを入れてください。fax にはOCR上で FAX と明示された電話番号だけを入れてください。
department には部署名だけを、title には役職名だけを入れてください。OCR上にない項目は空文字にしてください。
person_name_kana は氏名の読みをひらがなで入れてください。姓名の間には半角スペースを1つ入れてください。
OCRテキスト内にふりがな・フリガナがある場合はそれを優先してください。
ふりがながない場合でも、日本人名として自然で一般的な読みを推測してください。
ローマ字表記がある場合は読み推測の強い手がかりとして使い、person_name_kana にはローマ字ではなくひらがなを入れてください。
ローマ字の氏名表記がある場合は、漢字からの推測よりローマ字を優先して読みを検証してください。ローマ字が名→姓の順でも、person_name_kana は person_name と同じ姓→名の順に並べてください。
例えば person_name が「青葉 花子」でローマ字表記が「HANAKO AOBA」なら、person_name_kana は「あおば はなこ」です。「花子」を「あおば はこ」のように短くしてはいけません。
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
    for line_blocks in by_side.values():
        line_blocks.sort(key=lambda item: (item["box"][0], item["box"][1]))
        run: list[dict] = []
        for block in line_blocks:
            if run and _is_horizontal_neighbour(run[-1], block):
                run.append(block)
                continue
            _append_name_candidate(candidates, run)
            run = [block]
        _append_name_candidate(candidates, run)
    return candidates


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
    return -min(left_height, right_height) * 0.15 <= gap <= (left_height + right_height) * 0.7


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


def _refine_person_name_kana(data: dict, raw_text: str, blocks: list[dict] | None = None) -> None:
    """Prefer printed phonetic evidence over an LLM's kanji-only guess."""
    name = data.get("person_name") or ""
    kana = data.get("person_name_kana") or ""
    if not name or not kana:
        return

    explicit_kana = _explicit_kana_for_name(raw_text, name) or _ruby_kana_for_name(blocks or [], name)
    if explicit_kana:
        data["person_name_kana"] = explicit_kana
        return

    current_parts = _kana_parts(kana)
    if len(current_parts) != 2:
        return
    for given_roman, family_roman in _roman_name_pairs(raw_text):
        given = _roman_to_hiragana(given_roman)
        family = _roman_to_hiragana(family_roman)
        if not given or not family:
            continue
        # The common printed order is given-name first (HANAKO AOBA), while the
        # Japanese field is family-name first.  Require one exact matching part
        # so a generic email address cannot overwrite an unrelated name.
        if current_parts[0] == family or current_parts[1] == given:
            data["person_name_kana"] = f"{family} {given}"
            return
        if current_parts[0] == given or current_parts[1] == family:
            data["person_name_kana"] = f"{given} {family}"
            return


def _explicit_kana_for_name(raw_text: str, name: str) -> str:
    compact_name = re.sub(r"\s+", "", name)
    for line in raw_text.splitlines():
        if compact_name not in re.sub(r"\s+", "", line):
            continue
        readings = re.findall(r"[ぁ-ゖァ-ヶー]+(?:[\s　]+[ぁ-ゖァ-ヶー]+)?", line)
        if readings:
            return _normalize_kana_evidence(readings[-1])
    return ""


def _ruby_kana_for_name(blocks: list[dict], name: str) -> str:
    """Read kana blocks positioned directly above or below the printed name."""
    compact_name = re.sub(r"\s+", "", name)
    name_boxes = []
    kana_blocks = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        box = _box_coordinates(block.get("box"))
        if not text or box is None:
            continue
        if re.sub(r"\s+", "", text) == compact_name:
            name_boxes.append(box)
        elif re.fullmatch(r"[ぁ-ゖァ-ヶー]+", re.sub(r"\s+", "", text)):
            kana_blocks.append((text, box))
    if not name_boxes:
        return ""

    name_x1 = min(box[0] for box in name_boxes)
    name_y1 = min(box[1] for box in name_boxes)
    name_x2 = max(box[2] for box in name_boxes)
    name_y2 = max(box[3] for box in name_boxes)
    name_height = name_y2 - name_y1
    nearby = []
    for text, (x1, y1, x2, y2) in kana_blocks:
        overlap = min(name_x2, x2) - max(name_x1, x1)
        if overlap <= 0:
            continue
        vertical_gap = min(abs(name_y1 - y2), abs(y1 - name_y2))
        if vertical_gap <= name_height * 1.25:
            nearby.append((x1, text))
    if len(nearby) != 2:
        return ""
    nearby.sort(key=lambda item: item[0])
    return " ".join(_normalize_kana_evidence(text) for _x, text in nearby)


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
    numeric_values = {
        _normalized_digits(value)
        for value in re.findall(r"[0-9０-９][0-9０-９()（）\-ー－−\s]{4,}", raw_text)
    }

    for key in ("person_name", "company_name", "department", "title", "email", "website"):
        value = data.get(key, "")
        if value and _compact_for_evidence(value) not in compact_source:
            data[key] = ""

    for key in ("postal_code", "tel", "mobile", "fax"):
        value = data.get(key, "")
        if value and _normalized_digits(value) not in numeric_values:
            data[key] = ""

    fax_values = _numbers_on_labeled_lines(raw_text, r"fax|ファックス")
    if data.get("fax") and _normalized_digits(data["fax"]) not in fax_values:
        data["fax"] = ""

    mobile = data.get("mobile", "")
    mobile_digits = _normalized_digits(mobile)
    mobile_values = _numbers_on_labeled_lines(raw_text, r"mobile|cell|携帯|直通")
    if mobile and not (mobile_digits in mobile_values or re.fullmatch(r"0[789]0\d{8}", mobile_digits)):
        data["mobile"] = ""

    address = data.get("address", "")
    if address and _compact_address_for_evidence(address) not in _compact_address_for_evidence(raw_text):
        data["address"] = ""

    if not data.get("person_name"):
        data["person_name_kana"] = ""


def _compact_for_evidence(value: str) -> str:
    return re.sub(r"[\s()（）\-ー－−./:：]", "", value).casefold()


def _normalized_digits(value: str) -> str:
    return re.sub(r"\D", "", value.translate(str.maketrans("０１２３４５６７８９", "0123456789")))


def _numbers_on_labeled_lines(raw_text: str, label_pattern: str) -> set[str]:
    return {
        _normalized_digits(line)
        for line in raw_text.splitlines()
        if re.search(label_pattern, line, flags=re.IGNORECASE) and _normalized_digits(line)
    }


def _compact_address_for_evidence(value: str) -> str:
    return re.sub(r"[\s()（）\-ー－−./:：0-9０-９〇一二三四五六七八九十百千万億兆]", "", value).casefold()
