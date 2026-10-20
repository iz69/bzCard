from __future__ import annotations

import sys
import time
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..config import settings
from .name_evidence import _is_organization_name, _kanji_text, _roman_labeled_name_rows
from .address_evidence import PREFECTURES

_engine = None
logger = logging.getLogger("bzcard.ocr")


@dataclass
class OcrResult:
    raw_text: str
    blocks: list[dict]
    duration_ms: int
    direction: str
    horizontal_score: int = 0
    vertical_score: int = 0


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))
    from yomitoku import OCR

    _engine = OCR(
        configs={"text_recognizer": {"model_name": settings.ocr_recognizer_model}},
        device=settings.ocr_device,
        visualize=False,
    )
    return _engine


def run_yomitoku(image_path: Path, direction: str = "horizontal") -> OcrResult:
    started = time.perf_counter()
    engine = _get_engine()

    with Image.open(image_path) as image:
        img_array = np.array(image.convert("RGB"))[:, :, ::-1].copy()

    results, _ = engine(img_array)
    blocks = _extract_blocks(results)
    blocks = _refine_roman_labeled_names(engine, img_array, blocks)
    blocks = _refine_truncated_prefectures(engine, img_array, blocks)
    horizontal_score, vertical_score = _orientation_scores(blocks)
    if direction == "auto":
        direction = _detect_direction(horizontal_score, vertical_score)
    blocks = _sort_blocks(blocks, direction)

    if blocks:
        raw_text = "\n".join(block["text"] for block in blocks if block.get("text"))
    else:
        raw_text = str(results)
        blocks = [{"text": raw_text, "box": None, "font_size": 0}]

    duration_ms = int((time.perf_counter() - started) * 1000)
    return OcrResult(
        raw_text=raw_text.strip(),
        blocks=blocks,
        duration_ms=duration_ms,
        direction=direction,
        horizontal_score=horizontal_score,
        vertical_score=vertical_score,
    )


def _refine_roman_labeled_names(engine, image: np.ndarray, blocks: list[dict]) -> list[dict]:
    """Retry incomplete roman-labeled kanji using slightly larger image crops.

    Both padding sizes must agree with high recognition scores. Preserve the
    detector geometry and record the initial reading for later inspection.
    """
    rows = [row for row in _roman_labeled_name_rows(blocks)
            if any(_is_organization_name(name["text"]) for name in row["names"])
            or sum(len(name["text"]) for name in row["names"]) < 3][:2]
    if not rows:
        return blocks
    polygons, targets = [], []
    height, width = image.shape[:2]
    for row in rows:
        for name in row["names"]:
            x1, y1, x2, y2 = name["box"]
            for ratio in (.1, .2):
                padding = max(2, round((y2 - y1) * ratio))
                box = [max(0, int(x1) - padding), max(0, int(y1) - padding),
                       min(width, int(x2) + padding), min(height, int(y2) + padding)]
                a, b, c, d = box
                polygons.append([[a, b], [c, b], [c, d], [a, d]])
                targets.append((name["index"], box))
    try:
        result, _ = engine.recognizer(image, polygons)
        if len(result.contents) != len(targets) or len(result.scores) != len(targets):
            return blocks
    except Exception:
        logger.exception("Name crop recognition failed; retaining initial OCR")
        return blocks
    readings: dict[int, list[dict]] = {}
    for (index, box), text, score in zip(targets, result.contents, result.scores):
        readings.setdefault(index, []).append({"text": str(text).strip(), "score": float(score), "box": box})
    updated = list(blocks)
    for row in rows:
        names = row["names"]
        proposals = [readings[name["index"]] for name in names]
        if any(len({v["text"] for v in values}) != 1 or min(v["score"] for v in values) < .9
               for values in proposals):
            continue
        spelling = [values[0]["text"] for values in proposals]
        if (not all(_kanji_text(text) and not _is_organization_name(text) for text in spelling)
                or not 3 <= sum(len(_kanji_text(text)) for text in spelling) <= 6):
            continue
        for name, text, variants in zip(names, spelling, proposals):
            original = blocks[name["index"]]
            if text != original["text"]:
                updated[name["index"]] = {**original, "text": text,
                    "_recognition_retry": {"method": "expanded_name_crop", "original_text": original["text"],
                                           "variants": variants}}
    return updated


def _refine_truncated_prefectures(engine, image: np.ndarray, blocks: list[dict]) -> list[dict]:
    """Re-read an address missing its first prefecture character from the image.

    Geography only triggers recognition. Both crops must actually read the
    complete address; never insert a character based on the place name alone.
    """
    targets, polygons = [], []
    height, width = image.shape[:2]
    for index, block in enumerate(blocks):
        text, box = str(block.get("text") or ""), block.get("box")
        if not box or len(box) != 4 or any(text.startswith(name) for name in PREFECTURES):
            continue
        expected = [name[0] + text for name in PREFECTURES if text.startswith(name[1:])]
        if len(expected) != 1 or not re.search(r"[市区郡].*[0-9０-９]", text):
            continue
        if len(targets) >= 4:
            break
        variants = []
        for ratio in (.3, .4):
            x1, y1, x2, y2 = box
            padding = max(2, round((y2 - y1) * ratio))
            crop = [max(0, int(x1) - padding), max(0, int(y1) - padding),
                    min(width, int(x2) + padding), min(height, int(y2) + padding)]
            a, b, c, d = crop
            polygons.append([[a, b], [c, b], [c, d], [a, d]])
            variants.append(crop)
        targets.append((index, expected[0], variants))
    if not targets:
        return blocks
    try:
        result, _ = engine.recognizer(image, polygons)
        if len(result.contents) != len(polygons) or len(result.scores) != len(polygons):
            return blocks
    except Exception:
        logger.exception("Address crop recognition failed; retaining initial OCR")
        return blocks
    updated = list(blocks)
    for position, (index, expected, crops) in enumerate(targets):
        variants = [{"text": str(result.contents[position * 2 + offset]).strip(),
                     "score": float(result.scores[position * 2 + offset]), "box": crop}
                    for offset, crop in enumerate(crops)]
        if all(variant["text"] == expected and variant["score"] >= .9 for variant in variants):
            original = blocks[index]
            updated[index] = {**original, "text": expected,
                "_recognition_retry": {"method": "expanded_address_crop", "original_text": original["text"],
                                       "variants": variants}}
    return updated


def _detect_direction(horizontal_score: int, vertical_score: int) -> str:
    if vertical_score >= 3 and vertical_score > horizontal_score:
        return "vertical"
    return "horizontal"


def _orientation_scores(blocks: list[dict]) -> tuple[int, int]:
    boxes = [block.get("box") for block in blocks if block.get("box")]
    if not boxes:
        return 0, 0

    vertical_score = 0
    horizontal_score = 0
    for box in boxes:
        if not isinstance(box, list) or len(box) < 4:
            continue
        width = max(1.0, float(box[2]) - float(box[0]))
        height = max(1.0, float(box[3]) - float(box[1]))
        ratio = height / width
        if ratio >= 2.2:
            vertical_score += 1
        elif ratio <= 0.75:
            horizontal_score += 1

    return horizontal_score, vertical_score


def _sort_blocks(blocks: list[dict], direction: str) -> list[dict]:
    if direction == "vertical":
        return sorted(
            blocks,
            key=lambda b: (
                -(_box_value(b.get("box"), 0)),
                _box_value(b.get("box"), 1),
            ),
        )
    return sorted(
        blocks,
        key=lambda b: (
            _box_value(b.get("box"), 1),
            _box_value(b.get("box"), 0),
        ),
    )


def _box_value(box: Any, index: int) -> float:
    if isinstance(box, (list, tuple)) and len(box) > index:
        try:
            return float(box[index])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _extract_blocks(results: Any) -> list[dict]:
    """Yomitoku 0.14.0 returns OCRSchema.words (WordPrediction instances)."""
    blocks = []
    for word in results.words:
        text = word.content.strip()
        if not text:
            continue
        xs = [point[0] for point in word.points]
        ys = [point[1] for point in word.points]
        box = [min(xs), min(ys), max(xs), max(ys)]
        blocks.append({"text": text, "box": box, "font_size": max(0, box[3] - box[1])})
    return blocks
