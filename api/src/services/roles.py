"""Printed job titles shared by extraction and name evidence."""
import re


ROLE_TITLES = {
    "代表取締役社長", "代表取締役会長", "代表取締役副社長", "代表取締役",
    "専務取締役", "常務取締役", "取締役", "専務執行役員", "常務執行役員", "執行役員",
    "社長", "副社長", "会長", "副会長", "専務", "常務",
    "部長", "課長", "室長", "係長", "グループ長", "主任", "技師", "編集者", "研究員",
    "担当", "営業担当", "デザイナー", "リーダー", "エンジニア",
}
_ROLE_PREFIX = re.compile(
    r"^(" + "|".join(map(re.escape, sorted(ROLE_TITLES, key=len, reverse=True))) + r")\s+(.+)$"
)


def split_printed_role(value: str) -> tuple[str, str] | None:
    """Require a printed separator; never cut a title from a surname prefix."""
    match = _ROLE_PREFIX.fullmatch(str(value or "").strip())
    return (match[1], match[2].strip()) if match else None
