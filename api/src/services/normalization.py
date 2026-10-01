"""Shared normalization for extraction, edits and search."""
import re
import unicodedata


def _normalize_kana_field(value) -> str:
    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())
    if not text:
        return ""
    text = _katakana_to_hiragana(text)
    if not re.fullmatch(r"[ぁ-ゖー]+(?: [ぁ-ゖー]+)*", text):
        return ""
    return text


def _normalize_postal_code(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    digits = re.sub(r"[\s\-ー−]", "", text.removeprefix("〒").strip())
    if re.fullmatch(r"[0-9]{7}", digits):
        return f"{digits[:3]}-{digits[3:]}"
    return text


def _normalize_tags(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    raw_tags = re.split(r"[,、\n\r]+", text)
    tags: list[str] = []
    for raw_tag in raw_tags:
        tag = re.sub(r"\s+", " ", raw_tag.strip().lstrip("#")).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return ", ".join(tags)


def _normalize_company_name(value) -> str:
    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())
    if not text:
        return ""
    corporate_types = (
        "株式会社",
        "有限会社",
        "合同会社",
        "合名会社",
        "合資会社",
        "医療法人",
        "学校法人",
        "社会福祉法人",
        "一般社団法人",
        "公益社団法人",
        "一般財団法人",
        "公益財団法人",
        "特定非営利活動法人",
    )
    types_pattern = "|".join(map(re.escape, sorted(corporate_types, key=len, reverse=True)))
    text = re.sub(rf"^({types_pattern})\s*(?=\S)", r"\1 ", text)
    text = re.sub(rf"(?<=\S)\s*({types_pattern})$", r" \1", text)
    return text


def _normalize_phone_number(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    text = text.replace("(", "-").replace(")", "-")
    text = text.replace("[", "-").replace("]", "-")
    text = text.replace("（", "-").replace("）", "-")
    text = text.replace("ー", "-").replace("－", "-").replace("―", "-")
    text = re.sub(r"[^0-9+\-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    text = re.sub(r"^\+?81-?0?", "0", text)
    return text


def _normalize_address(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""

    kanji_digits = "〇零一二三四五六七八九十百千万壱弐参"

    def replace_match(match: re.Match) -> str:
        number = _kanji_number_to_int(match.group("number"))
        if number is None:
            return match.group(0)
        return f"{number}{match.group('suffix')}"

    return re.sub(
        rf"(?P<number>[{kanji_digits}]+)(?P<suffix>丁目|番地|番(?!町)|号)",
        replace_match,
        text,
    )


def _kanji_number_to_int(value: str) -> int | None:
    digits = {
        "〇": 0,
        "零": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "壱": 1,
        "弐": 2,
        "参": 3,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}

    if not value:
        return None
    if not any(char in units for char in value):
        numbers = [digits.get(char) for char in value]
        if any(number is None for number in numbers):
            return None
        return int("".join(str(number) for number in numbers))

    total = 0
    section = 0
    current = 0
    for char in value:
        if char in digits:
            current = digits[char]
            continue
        unit = units.get(char)
        if unit is None:
            return None
        if unit == 10000:
            section = (section + (current or 1)) * unit
            total += section
            section = 0
        else:
            section += (current or 1) * unit
        current = 0
    return total + section + current


def _katakana_to_hiragana(value: str) -> str:
    chars = []
    for char in value:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def _hiragana_to_katakana(value: str) -> str:
    chars = []
    for char in value:
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            chars.append(chr(code + 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def _is_kana(char: str) -> bool:
    code = ord(char)
    return 0x3041 <= code <= 0x3096 or 0x30A1 <= code <= 0x30F6


NORMALIZERS = {
    "person_name_kana": _normalize_kana_field,
    "postal_code": _normalize_postal_code,
    "company_name": _normalize_company_name,
    "address": _normalize_address,
    "tags": _normalize_tags,
    "tel": _normalize_phone_number,
    "mobile": _normalize_phone_number,
    "fax": _normalize_phone_number,
}


def normalize_fields(fields: dict) -> dict:
    return {key: NORMALIZERS.get(key, lambda value: str(value or "").strip())(value)
            for key, value in fields.items()}
