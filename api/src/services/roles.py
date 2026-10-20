"""Printed job titles shared by extraction and name evidence."""
import re


ROLE_TITLES = {
    "代表取締役社長", "代表取締役会長", "代表取締役副社長", "代表取締役",
    "専務取締役", "常務取締役", "取締役", "専務執行役員", "常務執行役員", "執行役員",
    "社長", "副社長", "会長", "副会長", "専務", "常務",
    "部長", "課長", "室長", "係長", "グループ長", "主任", "技師", "編集者", "研究員",
    "所長", "営業所長", "事業所長", "支店長", "支社長", "店長",
    "担当", "営業担当", "デザイナー", "リーダー", "エンジニア", "セールスエンジニア",
}
OCCUPATIONAL_TITLES = {"担当", "営業担当", "デザイナー", "エンジニア", "セールスエンジニア"}


def is_position_title(value: str) -> bool:
    """Recognize local position evidence without making the title list exhaustive."""
    text = re.sub(r'\s+', '', value)
    if text in OCCUPATIONAL_TITLES:
        return False
    if text in ROLE_TITLES:
        return True
    # Unknown titles may still be inferred by the LLM. These suffixes only
    # identify corroborating local positions when it instead selected a job.
    return bool(len(value.split()) == 1 and re.fullmatch(
        r'[一-龯々ァ-ヶーA-Za-z0-9]{1,20}(?:長|責任者|マネージャー|マネジャー)', text))
_ROLE_PREFIX = re.compile(
    r"^(" + "|".join(map(re.escape, sorted(ROLE_TITLES, key=len, reverse=True))) + r")\s+(.+)$"
)


def split_printed_role(value: str) -> tuple[str, str] | None:
    """Require a printed separator; never cut a title from a surname prefix."""
    match = _ROLE_PREFIX.fullmatch(str(value or "").strip())
    return (match[1], match[2].strip()) if match else None
