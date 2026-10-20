"""Address evidence; missing prefecture characters require printed text."""
from .normalization import _normalize_address


PREFECTURES = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 "
    "東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 "
    "滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 "
    "徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県"
).split()


def prefer_complete_printed_address(data: dict, source: str) -> None:
    """Use an OCR address when the model omitted its first prefecture character."""
    current = _normalize_address(data.get("address"))
    if not current or any(current.startswith(name) for name in PREFECTURES):
        return
    candidates = {_normalize_address(line) for line in source.splitlines()
                  if any(_normalize_address(line).startswith(name) for name in PREFECTURES)}
    matching = {line for line in candidates if line[1:] == current}
    if len(matching) == 1:
        data["address"] = matching.pop()
