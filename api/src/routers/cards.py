from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..auth import require_token
from ..services import repository
from ..services.image_store import (
    card_dir,
    image_metadata,
    make_card_id,
    relative_path,
    resolve_data_path,
    rotate_page_image,
    save_original_upload,
    sha256_file,
)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


@router.post("/cards/upload")
async def upload_card(
    file: UploadFile = File(...),
    direction: str = Query("auto", pattern="^(auto|horizontal|vertical)$"),
) -> dict:
    card_id = make_card_id()
    original = await save_original_upload(file, card_id)
    original_sha256 = sha256_file(original)
    duplicate = repository.get_card_by_original_sha256(original_sha256)
    if duplicate is not None:
        shutil.rmtree(card_dir(card_id), ignore_errors=True)
        return {
            "card_id": duplicate["id"],
            "job_id": None,
            "status": duplicate["status"],
            "duplicate": True,
        }
    job_id = repository.create_card(card_id, relative_path(original), original_sha256, direction)
    return {"card_id": card_id, "job_id": job_id, "status": "queued"}


@router.post("/cards/{card_id}/back/upload")
async def upload_back_image(
    card_id: str,
    file: UploadFile = File(...),
    direction: str = Query("auto", pattern="^(auto|horizontal|vertical)$"),
) -> dict:
    card = repository.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    if repository.get_active_job(card_id) is not None:
        raise HTTPException(status_code=409, detail="Card is currently processing")

    original = await save_original_upload(file, card_id, "back")
    original_sha256 = sha256_file(original)
    duplicate = repository.get_card_by_original_sha256(original_sha256)
    if duplicate is not None and duplicate["id"] != card_id:
        original.unlink(missing_ok=True)
        return {
            "card_id": duplicate["id"],
            "job_id": None,
            "status": duplicate["status"],
            "duplicate": True,
        }
    job_id = repository.set_back_image(card_id, relative_path(original), original_sha256, direction)
    return {"card_id": card_id, "job_id": job_id, "status": "queued", "side": "back"}


@router.get("/cards")
def list_cards(
    q: str | None = None,
    status: str | None = None,
) -> dict:
    return {"items": repository.list_cards(q=q, status=status)}


@router.get("/cards/{card_id}")
def get_card(card_id: str) -> dict:
    card = repository.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.patch("/cards/{card_id}")
def update_card(card_id: str, payload: dict) -> dict:
    if repository.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    card = repository.update_card_fields(card_id, payload)
    return card or {}


@router.delete("/cards/{card_id}")
def delete_card(card_id: str) -> dict:
    if not repository.delete_card(card_id):
        raise HTTPException(status_code=404, detail="Card not found")
    shutil.rmtree(card_dir(card_id), ignore_errors=True)
    return {"status": "deleted"}


@router.post("/cards/{card_id}/rotate")
def rotate_card_image(
    card_id: str,
    side: str = Query("front", pattern="^(front|back)$"),
    degrees: int = Query(..., ge=-90, le=90),
) -> dict:
    if degrees not in {-90, 90}:
        raise HTTPException(status_code=400, detail="degrees must be -90 or 90")
    card = repository.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    if repository.get_active_job(card_id) is not None:
        raise HTTPException(status_code=409, detail="Card is currently processing")

    if side == "back":
        original_field = "back_original_image_path"
        processed_field = "back_processed_image_path"
        thumbnail_field = "back_thumbnail_path"
        thumbnail_name = "thumbnail_back.jpg"
    else:
        original_field = "original_image_path"
        processed_field = "processed_image_path"
        thumbnail_field = "thumbnail_path"
        thumbnail_name = "thumbnail.jpg"

    original_rel = card.get(original_field)
    image_rel = card.get(processed_field) or original_rel
    if not original_rel or not image_rel:
        raise HTTPException(status_code=404, detail="Image not ready")

    thumbnail_rel = card.get(thumbnail_field) or relative_path(card_dir(card_id) / thumbnail_name)
    image_path = resolve_data_path(image_rel)
    thumbnail_path = resolve_data_path(thumbnail_rel)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    rotate_page_image(image_path, thumbnail_path, degrees)
    original_changed = image_rel == original_rel
    original_sha256 = sha256_file(image_path) if original_changed else None
    width, height, file_size = image_metadata(image_path) if original_changed else (None, None, None)
    updated = repository.update_image_orientation_metadata(
        card_id=card_id,
        side=side,
        original_sha256=original_sha256,
        thumbnail_path=thumbnail_rel,
        width=width,
        height=height,
        file_size=file_size,
        original_changed=original_changed,
    )
    return {"card": updated, "side": side, "degrees": degrees}


@router.post("/cards/{card_id}/reprocess")
def reprocess_card(
    card_id: str,
    direction: str = Query("auto", pattern="^(auto|horizontal|vertical)$"),
) -> dict:
    if repository.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    active = repository.get_active_job(card_id)
    if active is not None:
        return {"job_id": active["id"], "status": active["status"], "direction": direction}
    repository.set_ocr_direction(card_id, direction)
    repository.set_back_ocr_direction(card_id, direction)
    job_id = repository.enqueue_job(card_id, "process_card")
    return {"job_id": job_id, "status": "queued", "direction": direction}


@router.post("/cards/{card_id}/reextract")
def reextract_card(card_id: str) -> dict:
    if repository.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    active = repository.get_active_job(card_id)
    if active is not None:
        return {"job_id": active["id"], "status": active["status"]}
    job_id = repository.enqueue_job(card_id, "reextract")
    return {"job_id": job_id, "status": "queued"}


def _image_response(card_id: str, field: str) -> FileResponse:
    card = repository.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    rel = card.get(field)
    if not rel:
        raise HTTPException(status_code=404, detail="Image not ready")
    path = resolve_data_path(rel)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")
    return FileResponse(path)


@router.get("/cards/{card_id}/original-image")
def original_image(card_id: str) -> FileResponse:
    return _image_response(card_id, "original_image_path")


@router.get("/cards/{card_id}/processed-image")
def processed_image(card_id: str) -> FileResponse:
    return _image_response(card_id, "processed_image_path")


@router.get("/cards/{card_id}/thumbnail")
def thumbnail(card_id: str) -> FileResponse:
    return _image_response(card_id, "thumbnail_path")


@router.get("/cards/{card_id}/back-original-image")
def back_original_image(card_id: str) -> FileResponse:
    return _image_response(card_id, "back_original_image_path")


@router.get("/cards/{card_id}/back-processed-image")
def back_processed_image(card_id: str) -> FileResponse:
    return _image_response(card_id, "back_processed_image_path")


@router.get("/cards/{card_id}/back-thumbnail")
def back_thumbnail(card_id: str) -> FileResponse:
    return _image_response(card_id, "back_thumbnail_path")


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
