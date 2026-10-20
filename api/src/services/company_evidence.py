"""Recover corporate markers without including printed trade memberships."""
from __future__ import annotations

import re

from .name_evidence import (
    _CORPORATE_MARKER, _box_coordinates, _is_horizontal_neighbour,
    _name_layout_blocks, _same_text_line, compact_name,
)


_ASSOCIATION = r'[（(](?:一社|公社|一財|公財|社|財)[）)]'
_MEMBERSHIP = re.compile(r'\s*' + _ASSOCIATION + r'\s*(?=[^\n]*(?:会員|加盟))')
_OFFICE_ENDINGS = ('営業所', '事業所', '支店', '出張所', '支社')


def separate_printed_office(data: dict, source: str, blocks: list[dict]) -> bool:
    """Use an independently printed office to separate a combined company row."""
    rows = list(dict.fromkeys(line.strip() for line in
                [*source.splitlines(), *(str(b.get('text') or '') for b in blocks)] if line.strip()))
    offices = {compact_name(row): row for row in rows
               if len(compact_name(row)) <= 40 and not _CORPORATE_MARKER.search(row)
               and any(compact_name(row).endswith(end) and len(compact_name(row)) > len(end)
                       for end in _OFFICE_ENDINGS)
               and not re.search(r'[@:：/]', row)}
    current = compact_name(data.get('company_name'))
    matches = {}
    for row in rows:
        if not _CORPORATE_MARKER.search(row):
            continue
        for office, printed_office in offices.items():
            pattern = r'\s*'.join(map(re.escape, office)) + r'\s*$'
            match = re.search(pattern, row)
            if not match:
                continue
            company = row[:match.start()].strip()
            value = compact_name(company)
            if (not _CORPORATE_MARKER.search(value) or _CORPORATE_MARKER.fullmatch(value)
                    or current and current not in (value, compact_name(row))):
                continue
            entry = matches.setdefault(value, {'company': company, 'offices': {}})
            entry['offices'][office] = printed_office
    if len(matches) != 1:
        return False
    selected = next(iter(matches.values()))
    data['company_name'] = selected['company']
    if len(selected['offices']) == 1:
        office = next(iter(selected['offices'].values()))
        department = compact_name(data.get('department'))
        if not department or department in _OFFICE_ENDINGS or department == compact_name(office):
            data['department'] = office
    return True


def recover_layout_company(data: dict, source: str, blocks: list[dict]) -> bool:
    """Require local geometry for split legal forms and explicit membership labels."""
    rows = [{'text': str(b.get('text') or '').strip(), 'box': _box_coordinates(b.get('box')),
             'side': b.get('_side')} for b in _name_layout_blocks(blocks)]
    if not rows:
        rows = [{'text': line.strip(), 'box': None, 'side': None} for line in source.splitlines()]
    markers = [row for row in rows if row['box'] and _CORPORATE_MARKER.fullmatch(compact_name(row['text']))]
    for upper in rows:
        if not upper['box'] or compact_name(upper['text']) not in {'有限', '株式', '合同', '合名', '合資'}:
            continue
        x1, y1, x2, y2 = upper['box']
        for lower in rows:
            if not lower['box'] or upper['side'] != lower['side'] or compact_name(lower['text']) != '会社':
                continue
            lx1, ly1, lx2, ly2 = lower['box']
            width = min(x2 - x1, lx2 - lx1)
            height = min(y2 - y1, ly2 - ly1)
            if (abs((x1 + x2) - (lx1 + lx2)) / 2 <= width * .3
                    and -.1 * height <= ly1 - y2 <= height * .5):
                markers.append({'text': compact_name(upper['text']) + '会社',
                                'box': (min(x1, lx1), y1, max(x2, lx2), ly2), 'side': upper['side']})

    current = re.sub(r'\s*' + _ASSOCIATION + r'\s*$', '', str(data.get('company_name') or '')).strip()
    current_body = _CORPORATE_MARKER.sub('', compact_name(current))
    candidates = set()
    for row in rows:
        membership = _MEMBERSHIP.search(row['text'])
        body = row['text'][:membership.start()].strip() if membership else row['text']
        compact = compact_name(body)
        if not compact or _CORPORATE_MARKER.fullmatch(compact):
            continue
        if current_body and current_body != _CORPORATE_MARKER.sub('', compact):
            continue
        if membership and _CORPORATE_MARKER.search(compact):
            candidates.add(body)
            continue
        matched = {compact_name(marker['text']) for marker in markers
                   if row['box'] and marker['side'] == row['side']
                   and marker['box'][0] < row['box'][0] and _same_text_line(marker, row)
                   and _is_horizontal_neighbour(marker, row, max_gap_ratio=1)}
        if len(matched) == 1:
            candidates.add(matched.pop() + ' ' + body)
        elif membership and current_body:
            candidates.add(body)
    if len(candidates) != 1:
        return False
    data['company_name'] = candidates.pop()
    return True
