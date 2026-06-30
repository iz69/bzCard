from __future__ import annotations

import json
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

    try:
        _engine = OCR(
            configs={"lite": settings.yomitoku_lite},
            device=settings.ocr_device,
            visualize=False,
        )
    except TypeError:
        _engine = OCR(device=settings.ocr_device, visualize=False)
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
    data = _to_plain_data(results)
    candidates: list[dict] = []
    _walk_for_blocks(data, candidates)

    normalized = []
    seen = set()
    for item in candidates:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        box = _normalize_box(item.get("box"))
        key = (text, tuple(box) if box else None)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "text": text,
                "box": box,
                "font_size": item.get("font_size") or _estimate_font_size(box),
            }
        )
    return normalized


def _to_plain_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_plain_data(v) for k, v in value.items()}

    for attr in ("to_json", "json"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                raw = method()
                return json.loads(raw) if isinstance(raw, str) else _to_plain_data(raw)
            except Exception:
                pass

    for attr in ("to_dict", "dict", "model_dump"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return _to_plain_data(method())
            except Exception:
                pass

    if hasattr(value, "__dict__"):
        return _to_plain_data(vars(value))
    return str(value)


def _walk_for_blocks(value: Any, out: list[dict]) -> None:
    if isinstance(value, list):
        for item in value:
            _walk_for_blocks(item, out)
        return

    if not isinstance(value, dict):
        return

    text = _first_text(value)
    if text:
        box = _first_box(value)
        out.append({"text": text, "box": box})

    for child in value.values():
        if isinstance(child, (dict, list)):
            _walk_for_blocks(child, out)


def _first_text(value: dict) -> str | None:
    for key in ("text", "content", "transcription", "value", "label"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _first_box(value: dict) -> Any:
    for key in ("box", "bbox", "bounding_box", "points", "polygon", "quad"):
        raw = value.get(key)
        if raw:
            return raw
    return None


def _normalize_box(box: Any) -> list[float] | None:
    if box is None:
        return None
    if isinstance(box, dict):
        if all(k in box for k in ("x", "y", "width", "height")):
            x = float(box["x"])
            y = float(box["y"])
            return [x, y, x + float(box["width"]), y + float(box["height"])]
        return None
    if isinstance(box, (list, tuple)):
        flat: list[float] = []
        for item in box:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    flat.extend([float(item[0]), float(item[1])])
                except (TypeError, ValueError):
                    pass
            else:
                try:
                    flat.append(float(item))
                except (TypeError, ValueError):
                    pass
        if len(flat) >= 4:
            xs = flat[0::2]
            ys = flat[1::2]
            if len(box) == 4 and not any(isinstance(item, (list, tuple)) for item in box):
                return flat[:4]
            return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _estimate_font_size(box: list[float] | None) -> float:
    if not box or len(box) < 4:
        return 0
    return max(0, float(box[3]) - float(box[1]))
