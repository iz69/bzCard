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
    prompt = _build_prompt(raw_text, blocks)
    payload = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
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
    raw = body.get("response") or "{}"
    data = _parse_json_object(raw)
    normalized = {key: _string_or_empty(data.get(key)) for key in SCHEMA_KEYS}
    normalized["_raw"] = data
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ExtractionResult(data=normalized, duration_ms=duration_ms)


def _build_prompt(raw_text: str, blocks: list[dict]) -> str:
    marked_text = _marked_text(blocks) or raw_text
    keys = ", ".join(f'"{key}"' for key in SCHEMA_KEYS)
    return f"""
以下は日本の名刺1枚からOCRで読み取ったテキストです。
【大文字】は大きなフォントサイズのテキストで、氏名や会社名の候補です。

{marked_text}

次のキーを持つJSONオブジェクトだけを返してください。
不明な項目は空文字にしてください。説明文、Markdown、コードブロックは不要です。
person_name_kana は氏名の読みをひらがなで入れてください。姓名の間には半角スペースを1つ入れてください。
OCRテキスト内にふりがな・フリガナがある場合はそれを優先してください。
ふりがながない場合でも、日本人名として自然で一般的な読みを推測してください。
ローマ字表記がある場合は読み推測の強い手がかりとして使い、person_name_kana にはローマ字ではなくひらがなを入れてください。
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
