from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..auth import require_token
from ..services import repository
from ..services.image_store import (
    card_dir,
    make_card_id,
    relative_path,
    resolve_data_path,
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
