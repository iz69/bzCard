"""Address evidence; missing prefecture characters require printed text."""
import re

from .normalization import _normalize_address


PREFECTURES = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 "
    "東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 "
    "滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 "
    "徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県"
).split()


def prefer_complete_printed_address(data: dict, source: str) -> None:
    """Restore an omitted prefecture prefix only from the same printed address."""
    current = _normalize_address(data.get("address"))
    if not current or any(current.startswith(name) for name in PREFECTURES):
        return
    # Postal codes can precede the address, and building names can wrap to the
    # next OCR line. Match the whole address, ignoring only printed whitespace.
    compact_source = compact_address_for_evidence(source)
    compact_current = compact_address_for_evidence(current)
    matching = set()
    for prefecture in PREFECTURES:
        for missing in range(1, len(prefecture) + 1):
            if not compact_current.startswith(prefecture[missing:]):
                continue
            prefix = prefecture[:missing]
            # Use exactly the same check as the downstream address guard. A
            # failed completion must leave the original extracted address intact.
            if compact_address_for_evidence(prefix + current) in compact_source:
                matching.add(prefix + current)
    if len(matching) == 1:
        data["address"] = matching.pop()


def compact_address_for_evidence(value: str) -> str:
    # Normalize only street numbers, preserving both place names and digits.
    normalized = _normalize_address(value)
    normalized = re.sub(r"(\d+)(?:丁目|番地?|号)", r"\1-", normalized)
    normalized = re.sub(r"[ー－−]", "-", normalized)
    return re.sub(r"[\s()（）./:：]", "", normalized).strip("-").casefold()
