"""OCR evidence shared by name selection, reading selection and feedback."""
from __future__ import annotations

import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from string import ascii_lowercase

from .normalization import _normalize_kana_field
from .roles import ROLE_TITLES, split_printed_role

KANJI_CHARS = "㐀-䶿一-鿿豈-﫿\U00020000-\U0002ebef々〆ヵヶ"
PERSON_CHARS = KANJI_CHARS + "ぁ-ゖァ-ヶー"

_geometry_cache: ContextVar[dict | None] = ContextVar('name_geometry_cache', default=None)


@contextmanager
def name_evidence_scope():
    """Reuse geometry only while one extraction reads unchanged OCR blocks."""
    token = _geometry_cache.set({})
    try:
        yield
    finally:
        _geometry_cache.reset(token)


def _cached_geometry(function):
    @wraps(function)
    def cached(blocks):
        cache = _geometry_cache.get()
        if cache is None:
            return function(blocks)
        key = (function, id(blocks))
        if key not in cache:
            # Retain the input as well as the result so temporary aligned lists
            # cannot be freed and have their ids reused within this extraction.
            cache[key] = (blocks, function(blocks))
        return cache[key][1]
    return cached


def _name_evidence_lines(source: str) -> list[str]:
    """Separate a complete Japanese/roman name row without changing saved OCR.

    Match the entire row so organization labels and arbitrary substrings never
    become person evidence. Reading selection still requires its own evidence.
    """
    lines = []
    japanese = rf"[{PERSON_CHARS}]{{1,8}}(?:\s+[{PERSON_CHARS}]{{1,8}})?"
    for line in source.splitlines():
        line = line.strip()
        role_name = split_printed_role(line)
        if role_name and re.fullmatch(rf"[{PERSON_CHARS}\s]+", role_name[1]):
            lines.append(role_name[0])
            line = role_name[1]
        match = re.fullmatch(rf"({japanese})\s+([A-Za-z]{{3,}}\s+[A-Za-z]{{3,}})", line)
        if match and not _is_organization_name(match[1]):
            lines.extend(match.groups())
        else:
            lines.append(line)
    return lines


def compact_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def email_name_parts(source: str) -> list[tuple[str, ...]]:
    """Read account components, including OCR spaces around dot separators."""
    found = []
    for line in source.splitlines():
        line = re.sub(r"(?<=[A-Za-z0-9])[._-]\s+(?=[A-Za-z])", lambda m: m.group(0).strip(), line)
        line = re.sub(r"\s+([._-])(?=[A-Za-z])", r"\1", line)
        for local in re.findall(r"\b([A-Za-z][A-Za-z0-9._-]*)\s*@", line):
            parts = tuple(p.casefold() for p in re.split(r"[._-]+", local) if p)
            if len(parts) == 3 and len(parts[-1]) <= 3:
                parts = parts[:2]
            if parts not in found:
                found.append(parts)
    return found


def same_person(data: dict, previous: dict | None) -> bool:
    if not previous or not compact_name(data.get("person_name")):
        return False
    if compact_name(data.get("person_name")) != compact_name(previous.get("person_name")):
        return False
    email = lambda value: re.sub(r"\s+", "", str(value or "")).casefold()
    mobile = lambda value: re.sub(r"\D", "", str(value or ""))
    return bool((email(data.get("email")) and email(data.get("email")) == email(previous.get("email")))
                or (len(mobile(data.get("mobile"))) >= 10
                    and mobile(data.get("mobile")) == mobile(previous.get("mobile"))))


def person_name_is_printed(name: str, source: str, blocks: list[dict]) -> bool:
    compact = compact_name(name)
    if not compact or _is_organization_name(name) or compact in _corporate_fragments(blocks):
        return False
    for line in _name_evidence_lines(source):
        line = re.sub(r"^(?:氏名|姓名|名前)\s*[:：]\s*", "", line.strip())
        value = compact_name(line)
        if value == compact or value.startswith((compact + "（", compact + "(")):
            return True
    return (compact in _spatial_name_candidates(blocks)
            or any(compact_name(line) == compact for block in blocks
                   for line in _name_evidence_lines(str(block.get("text") or ""))))


@dataclass(frozen=True)
class NameCandidate:
    spelling: str
    source: str
    priority: int


def name_candidates(source: str, blocks: list[dict]) -> list[NameCandidate]:
    """Collect all printed candidates before choosing; never use the filename.

    Phonetic association outranks name lines and assembled OCR rows.
    Equal-priority people remain ambiguous.
    """
    candidates = []

    def add(spelling: str, reason: str, priority: int) -> None:
        spelling = " ".join(spelling.split())
        if spelling and not _is_organization_name(spelling) and compact_name(spelling) not in corporate_fragments:
            candidate = NameCandidate(spelling, reason, priority)
            if candidate not in candidates:
                candidates.append(candidate)

    corporate_fragments = _corporate_fragments(blocks)
    for spelling, _first, _second in _printed_identity(source, blocks):
        add(spelling, "printed_roman_identity", 3)
    for spelling, _reading in _spatial_ruby_identities(blocks):
        add(spelling, "printed_ruby_identity", 3)
    for spelling, _reading in _fragmented_ruby_readings(blocks):
        add(spelling, "printed_ruby_name", 3)
    japanese_part = rf"[{PERSON_CHARS}]{{1,8}}"
    for line in _name_evidence_lines(source):
        line = re.sub(r"^(?:氏名|姓名|名前)\s*[:：]\s*", "", line.strip())
        line = re.sub(r"\s*[（(].*[）)]\s*$", "", line)
        if re.fullmatch(rf"{japanese_part}[\s　]+{japanese_part}", line):
            parts = line.split()
            compact = compact_name(line)
            # Two isolated kanji may be just a surname. A valid model
            # name or a same-person previous value can still establish a full
            # two-character name, without inventing one from fragments.
            if len(compact) < 3:
                continue
            if (re.fullmatch(r"(?:東京都|北海道|[一-龯]{2,3}[県府])", parts[0])
                    or any(re.search(r"(?:建築士|技術士|技士|税理士|弁護士|司法書士)$", part) for part in parts)):
                continue
            add(line, "printed_name_line", 2)
        elif re.fullmatch(rf"[{KANJI_CHARS}]{{3,6}}", line):
            add(line, "unsegmented_name_line", 1)
    for spelling in _spatial_name_candidates(blocks):
        add(spelling, "assembled_ocr_row", 2)
    prominent = _prominent_spaced_kanji_name(source, blocks)
    if prominent:
        add(prominent, "spaced_ocr_name", 1)
    # OCR blocks can split within a given name; preserve the
    # reconstructed row, rather than representing a second printed person.
    rows = {compact_name(candidate.spelling) for candidate in candidates
            if candidate.source == "assembled_ocr_row"}
    fragments = {compact_name(block.get("text")) for block in _spatial_name_blocks(blocks)}
    return [candidate for candidate in candidates if not (
        candidate.source == "printed_name_line" and compact_name(candidate.spelling) in fragments
        and any(row.startswith(compact_name(candidate.spelling)) and row != compact_name(candidate.spelling)
                for row in rows))]


def unique_name_candidate(source: str, blocks: list[dict]) -> tuple[NameCandidate | None, list[NameCandidate]]:
    candidates = name_candidates(source, blocks)
    if not candidates:
        return None, []
    strongest = max(candidate.priority for candidate in candidates)
    preferred = [candidate for candidate in candidates if candidate.priority == strongest]
    if len({compact_name(candidate.spelling) for candidate in preferred}) != 1:
        return None, candidates
    # Retain an explicit surname/given boundary rather than the compact alias.
    return max(preferred, key=lambda candidate: len(candidate.spelling.split()) == 2), candidates

def _with_spatial_name_candidates(raw_text: str, blocks: list[dict]) -> str:
    """Append names reconstructed from adjacent, large kanji OCR blocks.

    A frequent example is a surname split into two boxes while the given name is
    in a third box.  Their top coordinates can differ by a few pixels, so OCR's
    normal top-to-bottom sort produces ``橋 / 秀 明 / 口``.  The boxes themselves
    still retain enough layout information to safely reconstruct a full name.
    """
    candidates = _spatial_name_candidates(blocks)
    if not candidates:
        return raw_text
    lines = "\n".join(f"- {candidate}" for candidate in candidates)
    return f"{raw_text}\n\n【座標補正による氏名候補】\n{lines}"


@_cached_geometry
def _corporate_fragments(blocks: list[dict]) -> set[str]:
    """Recognize words next to a corporation marker, even in separate boxes."""
    corporate_blocks = [{"box": box, "side": block.get("_side")}
                        for block in blocks
                        if _CORPORATE_MARKER.search(compact_name(block.get("text")))
                        and (box := _box_coordinates(block.get("box")))]
    fragments, independent = set(), set()
    for block in blocks:
        text = compact_name(block.get("text"))
        box = _box_coordinates(block.get("box"))
        if not text or box is None:
            continue
        # A mixed Japanese/roman block next to a corporate marker also excludes
        # its Japanese portion, which the text evidence now checks separately.
        spellings = {text, *(compact_name(line) for line in _name_evidence_lines(str(block.get("text") or "")))}
        candidate = {"box": box, "side": block.get("_side")}
        if any(company["side"] == candidate["side"] and _same_text_line(company, candidate)
               and (_is_horizontal_neighbour(candidate, company, max_gap_ratio=2)
                    if box[0] < company["box"][0] else _is_horizontal_neighbour(company, candidate, max_gap_ratio=2))
               for company in corporate_blocks):
            fragments.update(spellings)
        else:
            independent.update(spellings)
    return fragments - independent


@_cached_geometry
def _spatial_name_blocks(blocks: list[dict]) -> list[dict]:
    """Select prominent kanji relative to each card side, independent of pixels."""
    blocks = _name_layout_blocks(blocks)
    corporate_fragments = _corporate_fragments(blocks)
    by_side: dict[str, list[dict]] = {}
    for block in blocks:
        spelling = " ".join(str(block.get("text") or "").split())
        role_name = split_printed_role(spelling)
        if role_name:
            spelling = role_name[1]
        text = _kanji_text(spelling)
        box = _box_coordinates(block.get("box"))
        if (not text or len(text) > 6 or _is_organization_name(text)
                or text in corporate_fragments or box is None):
            continue
        by_side.setdefault(str(block.get("_side") or "unknown"), []).append(
            {"text": text, "box": box, "side": block.get("_side"),
             "spelling": spelling}
        )
    selected = []
    for side_blocks in by_side.values():
        height = max(block["box"][3] - block["box"][1] for block in side_blocks)
        selected.extend(block for block in side_blocks if block["box"][3] - block["box"][1] >= height * .55)
    return selected


@_cached_geometry
def _name_layout_blocks(blocks: list[dict]) -> list[dict]:
    """Compare sideways OCR boxes in reading order, leaving saved boxes intact."""
    orientations: dict[str, list[bool]] = {}
    for block in blocks:
        text = compact_name(block.get("text"))
        box = _box_coordinates(block.get("box"))
        if len(text) < 3 or box is None:
            continue
        width, height = box[2] - box[0], box[3] - box[1]
        if max(width, height) >= min(width, height) * 1.5:
            orientations.setdefault(str(block.get("_side") or "unknown"), []).append(height > width)
    sideways = {side for side, directions in orientations.items()
                if len(directions) >= 3 and sum(directions) / len(directions) >= .8}
    if not sideways:
        return blocks
    aligned = []
    for block in blocks:
        box = _box_coordinates(block.get("box"))
        if str(block.get("_side") or "unknown") in sideways and box is not None:
            x1, y1, x2, y2 = box
            block = {**block, "box": [y1, -x2, y2, -x1]}
        aligned.append(block)
    return aligned


@_cached_geometry
def _spatial_name_candidates(blocks: list[dict]) -> list[str]:
    """Return likely Japanese names formed by aligned, prominent OCR boxes."""
    return list(dict.fromkeys(row['text'] for row in _spatial_name_rows(blocks)))


@_cached_geometry
def _spatial_name_rows(blocks: list[dict]) -> list[dict]:
    """Keep each reconstructed row's position as well as its spelling."""
    by_side: dict[str, list[dict]] = {}
    for block in _spatial_name_blocks(blocks):
        by_side.setdefault(str(block["side"] or "unknown"), []).append(block)

    candidates: list[dict] = []
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
                if run and _is_horizontal_neighbour(run[-1], block, max_gap_ratio=2):
                    run.append(block)
                    continue
                _append_name_row(candidates, run)
                run = [block]
            _append_name_row(candidates, run)
    return candidates


def _same_text_line(left: dict, right: dict) -> bool:
    left_y1, left_y2 = left["box"][1], left["box"][3]
    right_y1, right_y2 = right["box"][1], right["box"][3]
    return abs((left_y1 + left_y2) - (right_y1 + right_y2)) / 2 <= min(left_y2 - left_y1, right_y2 - right_y1) * 0.4


def _append_name_row(candidates: list[dict], blocks: list[dict]) -> None:
    if not blocks:
        return
    if len(blocks) == 1 and not re.fullmatch(
        rf"[{KANJI_CHARS}]{{1,4}} [{KANJI_CHARS}]{{1,4}}", blocks[0]["spelling"]
    ):
        return
    candidate = "".join(block["text"] for block in blocks)
    # Two to six kanji covers ordinary Japanese full names while excluding most
    # split department labels and sentences.
    minimum = 3 if len(blocks) == 1 else 2
    if minimum <= len(candidate) <= 6 and not _is_organization_name(candidate):
        candidates.append({'text': candidate, 'side': blocks[0]['side'],
                           'box': (min(b['box'][0] for b in blocks), min(b['box'][1] for b in blocks),
                                   max(b['box'][2] for b in blocks), max(b['box'][3] for b in blocks))})


@_cached_geometry
def _spatial_roman_name_rows(blocks: list[dict]) -> list[dict]:
    """Link a small roman label below exactly one complete Japanese name row."""
    found = []
    for block in _name_layout_blocks(blocks):
        match = re.fullmatch(r'([A-Za-z]{3,})\s+([A-Za-z]{3,})', str(block.get('text') or '').strip())
        box = _box_coordinates(block.get('box'))
        if not match or box is None:
            continue
        x1, y1, x2, y2 = box
        matches = []
        for row in _spatial_name_rows(blocks):
            rx1, ry1, rx2, ry2 = row['box']
            height = ry2 - ry1
            overlap = min(x2, rx2) - max(x1, rx1)
            if (row['side'] == block.get('_side') and (y2 - y1) <= height * .8
                    and -.1 * height <= y1 - ry2 <= height * .8
                    and overlap >= min(x2 - x1, rx2 - rx1) * .8):
                matches.append(row)
        if len(matches) == 1:
            found.append({'text': matches[0]['text'], 'roman': tuple(p.casefold() for p in match.groups())})
    return found


def _spatial_roman_pairs(name: str, blocks: list[dict]) -> set[tuple[str, str]]:
    return {row['roman'] for row in _spatial_roman_name_rows(blocks) if row['text'] == compact_name(name)}


def _is_horizontal_neighbour(left: dict, right: dict, max_gap_ratio: float = .8) -> bool:
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
    return -min(left_height, right_height) * 0.3 <= gap <= (left_height + right_height) * max_gap_ratio


def _kanji_text(value) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    if re.fullmatch(rf"[{KANJI_CHARS}]+", text):
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


_CORPORATE_MARKER = re.compile(
    r"株式会社|有限会社|合同会社|合名会社|合資会社|医療法人|学校法人|社会福祉法人|社団法人|財団法人"
)


@_cached_geometry
def _roman_labeled_name_rows(blocks: list[dict]) -> list[dict]:
    """Locate two kanji fields with small roman labels immediately above them.

    An exact personal email component must corroborate the roman pair. Geometry
    is local to one card side; unknown kanji readings are not synthesized here.
    """
    emails = email_name_parts("\n".join(str(block.get("text") or "") for block in blocks))
    corporate_fragments = _corporate_fragments(blocks)
    roman, japanese = [], []
    for index, block in enumerate(blocks):
        box = _box_coordinates(block.get("box"))
        if box is None:
            continue
        text = str(block.get("text") or "").strip()
        item = {"index": index, "text": text, "box": box, "side": block.get("_side")}
        if re.fullmatch(r"[A-Za-z]{3,}", text):
            roman.append(item)
        elif ((kanji := _kanji_text(text)) and len(kanji) <= 4
              and kanji not in corporate_fragments):
            japanese.append({**item, "text": kanji})

    def below(label: dict) -> dict | None:
        rx1, ry1, rx2, ry2 = label["box"]
        matches = []
        for name in japanese:
            if name["side"] != label["side"]:
                continue
            x1, y1, x2, y2 = name["box"]
            height = y2 - y1
            overlap = min(x2, rx2) - max(x1, rx1)
            if (height < (ry2 - ry1) * 1.3 or not -.1 * height <= y1 - ry2 <= .8 * height
                    or overlap < min(x2 - x1, rx2 - rx1) * .5):
                continue
            matches.append(name)
        return matches[0] if len(matches) == 1 else None

    rows, seen = [], set()
    for left in roman:
        for right in roman:
            if (left["side"] != right["side"] or left["box"][0] >= right["box"][0]
                    or not _same_text_line(left, right)
                    or not _is_horizontal_neighbour(left, right, max_gap_ratio=4)):
                continue
            pair = (left["text"].casefold(), right["text"].casefold())
            if not any(_email_matches_roman_name(parts, pair, allow_single=True) for parts in emails):
                continue
            a, b = below(left), below(right)
            if (a is None or b is None or a["box"][0] >= b["box"][0]
                    or not _same_text_line(a, b)
                    or not _is_horizontal_neighbour(a, b, max_gap_ratio=2)):
                continue
            key = (a["index"], b["index"])
            if key not in seen:
                rows.append({"names": [a, b], "roman": pair})
                seen.add(key)
    return rows


def _printed_identity(raw_text: str, blocks: list[dict] | None = None) -> list[tuple[str, str, str]]:
    """Associate Japanese names with email-corroborated roman spellings."""
    lines = _name_evidence_lines(raw_text)
    email_pairs = email_name_parts(raw_text)
    found = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"([A-Za-z]{3,})\s+([A-Za-z]{3,})", line)
        if not match:
            continue
        roman = tuple(part.casefold() for part in match.groups())
        if not any(_email_matches_roman_name(pair, roman, allow_single=True) for pair in email_pairs):
            continue
        for previous in (index - 1, index - 2):
            if previous < 0:
                continue
            candidate = lines[previous]
            if previous == index - 2 and not re.fullmatch(r"\d+", lines[index - 1]):
                continue
            if _is_organization_name(candidate):
                continue
            if re.fullmatch(rf"[{KANJI_CHARS}]{{1,4}}[\s　]+[{KANJI_CHARS}]{{1,4}}", candidate):
                found.append((" ".join(candidate.split()), *[part.casefold() for part in match.groups()]))
                break
    for row in _roman_labeled_name_rows(blocks or []):
        if any(_is_organization_name(name["text"]) for name in row["names"]):
            continue
        spelling = " ".join(name["text"] for name in row["names"])
        if not 2 <= len(compact_name(spelling)) <= 6 or _is_organization_name(spelling):
            continue
        identity = (spelling, *row["roman"])
        if identity not in found:
            found.append(identity)
    return found


def _linked_roman_pairs(name: str, source: str, blocks: list[dict] | None = None) -> set[tuple[str, str]]:
    return {(first, second) for spelling, first, second in _printed_identity(source, blocks)
            if compact_name(spelling) == compact_name(name)}


def _email_matches_roman_name(email_parts: tuple[str, ...], roman: tuple[str, str],
                            allow_single: bool = False) -> bool:
    """Require two matching name components, allowing one printed initial."""
    # A single account component is sufficient only when the caller has an
    # adjacent Japanese/roman identity. It does not establish surname order.
    if allow_single and len(email_parts) == 1:
        return len(email_parts[0]) >= 3 and roman.count(email_parts[0]) == 1
    if len(email_parts) != 2:
        return False
    for ordered in (roman, roman[::-1]):
        if email_parts == ordered:
            return True
        for initial, full in ((0, 1), (1, 0)):
            if (len(email_parts[initial]) == 1
                    and email_parts[initial] == ordered[initial][0]
                    and email_parts[full] == ordered[full]):
                return True
    return False


def _is_organization_name(name: str) -> bool:
    value = compact_name(name)
    return bool(value in ROLE_TITLES or split_printed_role(name) or _CORPORATE_MARKER.search(value) or re.search(
        r"(?:店|営業所|事業所|出張所|工場|本社|本部|部|課|室|局|センター|グループ|チーム|課長|部長|室長|係長|工場長|担当|取締役|役員|社長|会長|専務|常務|代表|主任|エンジニア|リーダー|編集者|研究員|技師|建築士|技術士|技士|税理士|弁護士|司法書士)$", value))


def _prominent_spaced_kanji_name(raw_text: str, blocks: list[dict]) -> str:
    """Find one large, four-character name that OCR split into single kanji."""
    printed_lines = {line.strip() for line in raw_text.splitlines()}
    candidates = set()
    for block in _spatial_name_blocks(blocks):
        value = block["spelling"]
        if (re.fullmatch(rf"[{KANJI_CHARS}](?: [{KANJI_CHARS}]){{3}}", value)
                and value in printed_lines
                and not _is_organization_name(value)):
            candidates.add(value)
    return candidates.pop() if len(candidates) == 1 else ""


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


def _explicit_kana_for_name(raw_text: str, name: str) -> str:
    compact_name = re.sub(r"\s+", "", name)
    if not compact_name:
        return ""
    found = set()
    for line in raw_text.splitlines():
        # Remove the entire name first so a kana given name cannot become ruby.
        compact_line = re.sub(r"\s+", "", line)
        if compact_name not in compact_line:
            continue
        match = re.search(r"[（(]([ぁ-ゖァ-ヶー]+[\s　]+[ぁ-ゖァ-ヶー]+)[）)]", line)
        if match and _normalize_kana_field(match.group(1)):
            found.add(_normalize_kana_field(match.group(1)))
        # Unlabelled fragments such as 'カナ' are not a full-name reading.
        suffix = re.sub(r"\s+", "", compact_line.split(compact_name, 1)[1])
        labelled = re.match(r"(?:ふりがな|フリガナ|かな|カナ)[:：]?([ぁ-ゖァ-ヶー]+)", suffix)
        if labelled:
            found.add(_normalize_kana_field(labelled.group(1)))
    return found.pop() if len(found) == 1 else ""


def _ruby_kana_for_name(blocks: list[dict], name: str) -> str:
    compact_name = re.sub(r"\s+", "", name)
    spatial = {reading for printed, reading in _spatial_ruby_identities(blocks)
               if re.sub(r"\s+", "", printed) == compact_name}
    if len(spatial) == 1:
        return spatial.pop()
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


@_cached_geometry
def _spatial_ruby_identities(blocks: list[dict]) -> list[tuple[str, str]]:
    """Read a two-block kanji name and the smaller ruby printed above each block."""
    names = []
    for block in blocks:
        text = _kanji_text(block.get("text"))
        box = _box_coordinates(block.get("box"))
        if text and 1 <= len(text) <= 3 and box and not _is_organization_name(text):
            names.append({"text": text, "box": box, "side": block.get("_side")})

    def ruby_for(name: dict) -> str:
        nx1, ny1, nx2, ny2 = name["box"]
        height = ny2 - ny1
        ruby = []
        for block in blocks:
            if block.get("_side") != name["side"]:
                continue
            text = _normalize_kana_field(block.get("text"))
            box = _box_coordinates(block.get("box"))
            if not text or " " in text or box is None:
                continue
            x1, y1, x2, y2 = box
            if y2 - y1 >= height * .65 or min(nx2, x2) <= max(nx1, x1):
                continue
            if y1 >= ny1 or not (ny1 - height <= y2 <= ny1 + height * .2):
                continue
            ruby.append((x1, text))
        ruby.sort()
        return "".join(text for _x, text in ruby) if 1 <= len(ruby) <= 3 else ""

    found = set()
    for left in names:
        for right in names:
            if left is right or left["side"] != right["side"]:
                continue
            if not (left["box"][0] < right["box"][0] and _same_text_line(left, right)
                    and _is_horizontal_neighbour(left, right)):
                continue
            family, given = ruby_for(left), ruby_for(right)
            if family and given:
                found.add((f"{left['text']} {right['text']}", f"{family} {given}"))
    return sorted(found)


@_cached_geometry
def _fragmented_ruby_readings(blocks: list[dict]) -> list[tuple[str, str]]:
    """Collect small kana above a single name box without inventing a boundary."""
    found = set()
    for name_block in blocks:
        name = _kanji_text(name_block.get("text"))
        box = _box_coordinates(name_block.get("box"))
        if not name or not 3 <= len(name) <= 6 or _is_organization_name(name) or box is None:
            continue
        nx1, ny1, nx2, ny2 = box
        height = ny2 - ny1
        if nx2 - nx1 < height * 2:
            continue
        ruby = []
        for block in blocks:
            if block.get("_side") != name_block.get("_side"):
                continue
            text = _normalize_kana_field(block.get("text"))
            position = _box_coordinates(block.get("box"))
            if not text or position is None:
                continue
            x1, y1, x2, y2 = position
            if (y2 - y1 >= height * .65 or not ny1 - height <= y2 <= ny1 + height * .2
                    or y1 >= ny1 or not nx1 <= (x1 + x2) / 2 <= nx2):
                continue
            ruby.append((x1, (y1 + y2) / 2, x2, text))
        ruby.sort()
        if ((2 <= len(ruby) <= 6 or (len(ruby) == 1 and len(ruby[0][3].split()) >= 3))
                and ruby[-1][2] - ruby[0][0] >= (nx2 - nx1) * .6
                and max(r[1] for r in ruby) - min(r[1] for r in ruby) <= height * .3
                and all(left[2] <= right[0] for left, right in zip(ruby, ruby[1:]))):
            found.add((name, "".join(r[3].replace(' ', '') for r in ruby)))
    return sorted(found)


def _adjacent_ocr_one(name: str, blocks: list[dict]) -> bool:
    """Locate a separate OCR '1' immediately after the selected kanji name."""
    blocks = _name_layout_blocks(blocks)
    for block in blocks:
        box = _box_coordinates(block.get("box"))
        if compact_name(block.get("text")) != compact_name(name) or box is None:
            continue
        left = {"box": box}
        for other in blocks:
            right_box = _box_coordinates(other.get("box"))
            if (str(other.get("text") or "").strip() != "1" or right_box is None
                    or other.get("_side") != block.get("_side") or right_box[0] <= box[0]):
                continue
            if (_same_text_line(left, {"box": right_box})
                    and _is_horizontal_neighbour(left, {"box": right_box}, max_gap_ratio=.5)
                    and right_box[3] - right_box[1] >= (box[3] - box[1]) * .5):
                return True
    return False


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

    for parts in email_name_parts(raw_text):
        if len(parts) == 2 and all(part.isalpha() for part in parts):
            add_pair(*parts)
    return pairs


def _email_corroborated_roman_pairs(raw_text: str) -> list[tuple[str, str]]:
    """Return printed roman names also present at the start of an email address.

    Some companies append a short account suffix (for example, ``.az``), so
    matching the entire local part would discard the person's printed name.
    """
    email_parts = email_name_parts(raw_text)
    return [pair for pair in _roman_name_pairs(raw_text)
            if any(_email_matches_roman_name(parts, pair) for parts in email_parts)]


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
        # Plain romanization cannot mark the boundary between ん and い.
        "shinichi": "しんいち", "junichi": "じゅんいち", "shunichi": "しゅんいち",
    }
    if text in common_given_names:
        return common_given_names[text]
    syllables = {
        "kya": "きゃ", "kyu": "きゅ", "kyo": "きょ", "sha": "しゃ", "shu": "しゅ", "sho": "しょ",
        "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ", "ja": "じゃ", "ju": "じゅ", "jo": "じょ",
        "nya": "にゃ", "nyu": "にゅ", "nyo": "にょ",
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


def _compact_for_evidence(value: str) -> str:
    return re.sub(r"[\s()（）\-ー－−./:：・·･]", "", value).casefold()


def _roman_ocr_matches(value: str, candidates: list[dict]) -> list[dict]:
    """Repair one unreadable roman character only against component readings.

    Valid roman spellings are never rewritten. Multiple supported readings
    remain separate choices for the caller to reject as ambiguous.
    """
    text = value.casefold()
    if not re.fullmatch(r"[a-z]{3,32}", text) or _roman_to_hiragana(text):
        return []
    supported = {_normalize_kana_field(candidate.get("reading")) for candidate in candidates}
    supported.discard("")
    matches: dict[str, set[str]] = {}
    for index, original in enumerate(text):
        for replacement in ascii_lowercase:
            if replacement == original:
                continue
            spelling = text[:index] + replacement + text[index + 1:]
            reading = _roman_to_hiragana(spelling)
            if reading in supported:
                matches.setdefault(reading, set()).add(spelling)
    return [{"observed": text, "reading": reading, "roman_candidates": sorted(spellings)}
            for reading, spellings in sorted(matches.items())]


def _model_verified_roman_readings(source: str, family_candidates: list[dict],
                                   reading_candidates: list[dict]) -> dict[str, list[list[str]]]:
    """Match BOTH printed roman components to surname and full-name candidates.

    This also works across card sides without assuming an email is correct.
    A surname match alone never authorizes an unrelated roman given name.
    """
    matches: dict[str, list[list[str]]] = {}
    families = {_normalize_kana_field(candidate.get("reading")) for candidate in family_candidates}
    full_names = {_normalize_kana_field(candidate.get("reading")) for candidate in reading_candidates}
    for first, second in _roman_name_pairs(source):
        a, b = _roman_to_hiragana(first), _roman_to_hiragana(second)
        if not a or not b:
            continue
        for family in families:
            if not family or " " in family:
                continue
            for full in full_names:
                if not full.startswith(family) or len(full) == len(family) or " " in full:
                    continue
                given = full[len(family):]
                if any(_prefer_printed_kana(family, surname) == family
                       and _prefer_printed_kana(given, forename) == given
                       for surname, forename in ((a, b), (b, a))):
                    pairs = matches.setdefault(f"{family} {given}", [])
                    if [first, second] not in pairs:
                        pairs.append([first, second])
    return matches


def printed_reading(data: dict, source: str, blocks: list[dict],
                    family_candidates: list[dict] | None = None,
                    reading_candidates: list[dict] | None = None) -> dict:
    """Choose only phonetic evidence associated with this name, detecting conflicts."""
    name = data.get("person_name") or ""
    if not name:
        return {}
    kana = {_explicit_kana_for_name(line, name) for line in source.splitlines()}
    kana.discard("")
    ruby = _ruby_kana_for_name(blocks, name)
    if ruby:
        kana.add(ruby)
    kana.update(reading for spelling, reading in _spatial_ruby_identities(blocks)
                if compact_name(spelling) == compact_name(name))
    if len(kana) > 1:
        return {"status": "uncertain", "reason": "conflicting_printed_readings"}
    if kana:
        return {"status": "printed", "selected": kana.pop()}

    current = _normalize_kana_field(data.get("person_name_kana")).split()
    families = list(dict.fromkeys(_normalize_kana_field(c.get("reading")) for c in family_candidates or []))
    if len(current) == 2 and not families:
        families.append(current[0])
    linked = _linked_roman_pairs(name, source, blocks)
    pairs = linked | set(_email_corroborated_roman_pairs(source))
    if (not pairs and family_candidates and reading_candidates
            and person_name_is_printed(name, source, blocks)):
        verified = _model_verified_roman_readings(source, family_candidates, reading_candidates)
        if len(verified) > 1:
            return {"status": "uncertain", "reason": "conflicting_roman_readings"}
        if verified:
            selected, roman_pairs = next(iter(verified.items()))
            return {"status": "roman", "selected": selected,
                    "verification": "family_and_full_name_model_match", "roman_pairs": roman_pairs}
    choices = set()
    for first, second in pairs:
        before_choices = set(choices)
        a, b = _roman_to_hiragana(first), _roman_to_hiragana(second)
        if not a or not b:
            continue
        for family in families:
            if _prefer_printed_kana(family, a) == family:
                given = _prefer_printed_kana(current[1], b) if len(current) == 2 else b
                choices.add(f"{family} {given}")
                break
            if _prefer_printed_kana(family, b) == family:
                given = _prefer_printed_kana(current[1], a) if len(current) == 2 else a
                choices.add(f"{family} {given}")
                break
        if choices == before_choices and ((first, second) in linked or not families):
            # A printed Japanese/roman identity outranks an unrelated LLM
            # reading. If it is not adjacent, require email corroboration and
            # an unambiguous given-name/order hint.
            ordered = _ordered_roman_reading(first, second, [] if (first, second) in linked else current)
            if ordered is None:
                # A model can misread a rare surname while correctly reading
                # the given name. The supported given component establishes
                # roman order without treating the email tail as a surname.
                given_readings = set()
                for candidate in reading_candidates or []:
                    full = _normalize_kana_field(candidate.get("reading"))
                    for family in families:
                        if family and full.startswith(family) and len(full) > len(family):
                            given_readings.add(full[len(family):])
                first_is_given = any(_prefer_printed_kana(given, a) == given for given in given_readings)
                second_is_given = any(_prefer_printed_kana(given, b) == given for given in given_readings)
                if first_is_given != second_is_given:
                    ordered = (b, a) if first_is_given else (a, b)
            if ordered:
                family, given = ordered
                if families:
                    supported = {f for f in families if _prefer_printed_kana(f, family) == f}
                    if supported:
                        family = sorted(supported, key=len, reverse=True)[0]
                choices.add(f"{family} {given}")
    if len(choices) > 1:
        return {"status": "uncertain", "reason": "conflicting_roman_readings"}
    if choices:
        return {"status": "roman", "selected": choices.pop()}
    return {}
