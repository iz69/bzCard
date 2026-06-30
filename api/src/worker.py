from __future__ import annotations

import logging
import threading
import time

from .services import repository
from .services.card_classifier import check_business_card
from .services.extractor import extract_card_fields
from .services.image_store import (
    create_processed_images,
    relative_path,
    resolve_data_path,
    rotate_processed_images,
    save_rotated_candidate,
)
from .services.ocr import OcrResult, run_yomitoku

logger = logging.getLogger("bzcard.worker")
_started = False


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_worker_loop, daemon=True, name="bzcard-worker")
    thread.start()


def _worker_loop() -> None:
    while True:
        try:
            job = repository.claim_next_job()
            if job is None:
                time.sleep(1)
                continue
            _run_job(job)
        except Exception:
            logger.exception("Worker loop failed")
            time.sleep(3)


def _run_job(job: dict) -> None:
    card_id = job["card_id"]
    try:
        card = repository.get_card(card_id)
        if card is None:
            repository.finish_job(job["id"])
            return

        if job["type"] == "reextract":
            raw_text = _combined_ocr_text(repository.get_card_images(card_id))
            if not raw_text.strip():
                raise RuntimeError("OCR text is empty; run full reprocess first")
            extracted = extract_card_fields(raw_text, [])
            repository.save_extraction_result(card_id, extracted.data, extracted.duration_ms)
            repository.finish_job(job["id"])
            return

        repository.set_card_status(card_id, "preprocessing")
        images = repository.get_card_images(card_id)
        if not images:
            raise RuntimeError("No images registered for this card")
        for image in images:
            _process_image(card_id, image)

        raw_text = _combined_ocr_text(repository.get_card_images(card_id))
        card_check = check_business_card(raw_text)
        if not card_check.is_likely_card:
            repository.set_card_status(card_id, "not_card", card_check.reason)
            repository.finish_job(job["id"])
            return

        extracted = extract_card_fields(raw_text, [])
        repository.save_extraction_result(card_id, extracted.data, extracted.duration_ms)
        repository.finish_job(job["id"])
    except Exception as exc:
        message = str(exc)[:2000]
        logger.exception("Job %s failed", job["id"])
        repository.fail_job(job["id"], card_id, message)


def _process_image(card_id: str, image: dict) -> None:
    side = image["side"]
    original_path = resolve_data_path(image["original_image_path"])
    processed_path, thumbnail_path = create_processed_images(original_path, card_id, side)
    repository.set_card_processing_artifacts(
        card_id,
        relative_path(processed_path),
        relative_path(thumbnail_path),
        side,
    )

    ocr = _run_ocr_with_auto_rotation(processed_path, thumbnail_path, image.get("ocr_direction") or "horizontal")
    repository.save_detected_ocr_direction(card_id, ocr.direction, side)
    repository.save_ocr_result(card_id, ocr.raw_text, ocr.blocks, ocr.duration_ms, side)


def _run_ocr_with_auto_rotation(processed_path, thumbnail_path, direction: str) -> OcrResult:
    ocr = run_yomitoku(processed_path, direction)
    if direction != "auto" or ocr.direction != "vertical" or not _is_portrait_image(processed_path):
        return ocr

    best: tuple[int, OcrResult, int] | None = None
    for degrees in (90, -90):
        candidate_path = save_rotated_candidate(processed_path, degrees)
        try:
            candidate = run_yomitoku(candidate_path, "auto")
            if candidate.direction != "horizontal":
                continue
            score = _rotation_score(candidate, ocr)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, candidate, degrees)
        finally:
            candidate_path.unlink(missing_ok=True)

    if best is None:
        return ocr

    _score, rotated_ocr, degrees = best
    rotate_processed_images(processed_path, thumbnail_path, degrees)
    return rotated_ocr


def _is_portrait_image(path) -> bool:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
    return height > width * 1.15


def _rotation_score(candidate: OcrResult, original: OcrResult) -> int:
    candidate_text_len = len(candidate.raw_text.replace("\n", ""))
    original_text_len = len(original.raw_text.replace("\n", ""))
    if candidate_text_len < max(20, int(original_text_len * 0.65)):
        return 0
    if candidate.horizontal_score < max(3, original.vertical_score):
        return 0
    return candidate.horizontal_score - candidate.vertical_score + candidate_text_len // 20


def _combined_ocr_text(images: list[dict]) -> str:
    chunks = []
    labels = {"front": "表面", "back": "裏面"}
    for image in images:
        text = (image.get("ocr_text") or "").strip()
        if not text:
            continue
        label = labels.get(image["side"], image["side"])
        chunks.append(f"【{label}】\n{text}")
    return "\n\n".join(chunks)
