from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..config import settings

_engine = None


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
