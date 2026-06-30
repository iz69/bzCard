from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass
class BusinessCardCheck:
    is_likely_card: bool
    reason: str
    score: int


COMPANY_WORDS = (
    "株式会社",
    "有限会社",
    "合同会社",
    "合名会社",
    "合資会社",
    "社団法人",
    "財団法人",
    "医療法人",
    "学校法人",
    "office",
    "corporation",
    "corp",
    "co.,",
    "ltd",
    "inc",
)

TITLE_WORDS = (
    "代表",
    "取締役",
    "社長",
    "部長",
    "課長",
    "係長",
    "営業",
    "担当",
    "manager",
    "president",
    "director",
)

ADDRESS_WORDS = (
    "東京都",
    "北海道",
    "府",
    "県",
    "市",
    "区",
    "町",
    "村",
    "丁目",
    "番地",
)


def check_business_card(raw_text: str) -> BusinessCardCheck:
    text = unicodedata.normalize("NFKC", raw_text or "").strip()
    compact = re.sub(r"\s+", "", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(compact) < 18 or len(lines) < 3:
        return BusinessCardCheck(False, "名刺として読み取れる文字情報が少なすぎます", 0)

    lower = text.lower()
    score = 0
    signals: list[str] = []

    if re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text, flags=re.IGNORECASE):
        score += 3
        signals.append("email")
    if re.search(r"(?:tel|phone|mobile|fax|電話|携帯|直通)", lower) or re.search(r"\b0\d{1,4}-?\d{1,4}-?\d{3,4}\b", text):
        score += 2
        signals.append("phone")
    if re.search(r"(?:https?://|www\.|\.co\.jp|\.com|\.jp)", lower):
        score += 1
        signals.append("website")
    if re.search(r"〒?\d{3}-?\d{4}", text) or any(word in text for word in ADDRESS_WORDS):
        score += 2
        signals.append("address")
    if any(word in text or word in lower for word in COMPANY_WORDS):
        score += 2
        signals.append("company")
    if any(word in text or word in lower for word in TITLE_WORDS):
        score += 1
        signals.append("title")

    if score < 3:
        return BusinessCardCheck(
            False,
            f"名刺らしい連絡先・会社情報が不足しています: {', '.join(signals) or 'no signals'}",
            score,
        )

    return BusinessCardCheck(True, f"business card signals: {', '.join(signals)}", score)
