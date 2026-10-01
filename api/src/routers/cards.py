from __future__ import annotations

import shutil
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..auth import require_user_data_access as require_user
from ..services import repository, feedback
from ..services.card_data_lock import card_data_lock
from ..services.image_store import (
    card_dir,
    create_processed_images,
    make_card_id,
    relative_path,
    resolve_data_path,
    rotate_page_image,
    save_original_upload,
    sha256_file,
)

router = APIRouter(prefix="/api")


@router.post("/cards/upload")
async def upload_card(
    file: UploadFile = File(...),
    direction: str = Query("auto", pattern="^(auto|horizontal|vertical)$"),
    user: dict = Depends(require_user),
) -> dict:
    card_id = make_card_id()
    original = await save_original_upload(file, card_id)
    original_sha256 = sha256_file(original)
    duplicate = repository.get_user_owned_card_by_original_sha256(original_sha256, user["id"])
    if duplicate is not None:
        shutil.rmtree(card_dir(card_id), ignore_errors=True)
        return {
            "card_id": duplicate["id"],
            "job_id": None,
            "status": duplicate["status"],
            "duplicate": True,
        }
    try:
        job_id = repository.create_card(
            card_id,
            relative_path(original),
            original_sha256,
            direction,
            user["id"],
        )
    except ValueError as exc:
        # The user may have been stopped while the request body was uploading.
        shutil.rmtree(card_dir(card_id))
        raise HTTPException(403, str(exc)) from exc
    except BaseException:
        shutil.rmtree(card_dir(card_id), ignore_errors=True)
        raise
    return {"card_id": card_id, "job_id": job_id, "status": "queued"}


@router.post("/cards/{card_id}/back/upload")
async def upload_back_image(
    card_id: str,
    file: UploadFile = File(...),
    direction: str = Query("auto", pattern="^(auto|horizontal|vertical)$"),
    user: dict = Depends(require_user),
) -> dict:
    card = repository.get_user_card(card_id, user["id"])
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    with card_data_lock(card_id):
        card = repository.get_user_card(card_id, user["id"])
        if card is None:
            raise HTTPException(404, "Card not found")
        if repository.get_active_job(card_id) is not None:
            raise HTTPException(status_code=409, detail="Card is currently processing")

        original = await save_original_upload(file, card_id, "back")
        original_sha256 = sha256_file(original)
        duplicate = repository.get_user_owned_card_by_original_sha256(original_sha256, user["id"])
        if duplicate is not None and duplicate["id"] != card_id:
            original.unlink(missing_ok=True)
            return {
                "card_id": duplicate["id"],
                "job_id": None,
                "status": duplicate["status"],
                "duplicate": True,
            }
        try:
            job_id = repository.set_back_image(card_id, relative_path(original), original_sha256, direction)
        except BaseException:
            original.unlink(missing_ok=True)
            raise
        previous = card.get("back_original_image_path")
        if previous and previous != relative_path(original):
            try:
                old_path = resolve_data_path(previous)
                from ..database import connection
                with connection() as conn:
                    referenced = conn.execute("SELECT 1 FROM card_images WHERE original_image_path = ? OR processed_image_path = ? OR thumbnail_path = ?", (previous, previous, previous)).fetchone()
                if not referenced and old_path.parent == card_dir(card_id).resolve():
                    old_path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger("bzcard.cards").warning("Old back image cleanup failed for %s", card_id)
        return {"card_id": card_id, "job_id": job_id, "status": "queued", "side": "back"}


@router.get("/cards")
def list_cards(
    q: str | None = None,
    status: str | None = None,
    user: dict = Depends(require_user),
) -> dict:
    return {"items": repository.list_user_cards(user["id"], q=q, status=status)}


@router.get("/contacts")
def list_contacts(
    q: str | None = None,
    status: str | None = None,
    user: dict = Depends(require_user),
) -> dict:
    return {"items": repository.list_user_contacts(user["id"], q=q, status=status)}


@router.get("/contacts/{contact_id}")
def get_contact(contact_id: str, user: dict = Depends(require_user)) -> dict:
    contact = repository.get_user_contact(user["id"], contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.get("/cards/{card_id}")
def get_card(card_id: str, user: dict = Depends(require_user)) -> dict:
    card = repository.get_user_card(card_id, user["id"])
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.patch("/cards/{card_id}")
def update_card(card_id: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    if repository.get_user_card(card_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    card = repository.update_card_fields(card_id, payload)
    return card or {}


@router.delete("/cards/{card_id}")
def delete_card(card_id: str, user: dict = Depends(require_user)) -> dict:
    if repository.get_user_card(card_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    with card_data_lock(card_id):
        repository.delete_card(card_id)
        shutil.rmtree(card_dir(card_id), ignore_errors=True)
        return {"status": "deleted"}


@router.post("/cards/{card_id}/rotate")
def rotate_card_image(
    card_id: str,
    side: str = Query("front", pattern="^(front|back)$"),
    degrees: int = Query(..., ge=-90, le=90),
    user: dict = Depends(require_user),
) -> dict:
    if degrees not in {-90, 90}:
        raise HTTPException(status_code=400, detail="degrees must be -90 or 90")
    card = repository.get_user_card(card_id, user["id"])
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    with card_data_lock(card_id):
        card = repository.get_user_card(card_id, user["id"])
        if card is None:
            raise HTTPException(404, "Card not found")
        if repository.get_active_job(card_id) is not None:
            raise HTTPException(status_code=409, detail="Card is currently processing")

        image = next((item for item in card.get("images", []) if item["side"] == side), None)
        if image is None:
            raise HTTPException(404, "Image not ready")
        image_rel = image.get("processed_image_path")
        thumbnail_rel = image.get("thumbnail_path")
        if not image_rel or not thumbnail_rel:
            processed, thumb = create_processed_images(
                resolve_data_path(image["original_image_path"]), card_id, side,
                (image["manual_rotation"] + image["auto_rotation"]) % 360,
            )
            image_rel, thumbnail_rel = relative_path(processed), relative_path(thumb)
            repository.set_card_processing_artifacts(card_id, image_rel, thumbnail_rel, side)
            repository.set_card_status(card_id, card["status"], card.get("error_message"))
        image_path = resolve_data_path(image_rel)
        if not image_path.is_file():
            raise HTTPException(404, "Image file not found")
        rotate_page_image(image_path, resolve_data_path(thumbnail_rel), degrees)
        updated = repository.update_image_orientation_metadata(card_id, side, degrees, thumbnail_rel)
        return {"card": updated, "side": side, "degrees": degrees}



@router.post("/cards/{card_id}/reprocess")
def reprocess_card(
    card_id: str,
    direction: str = Query("auto", pattern="^(auto|horizontal|vertical)$"),
    user: dict = Depends(require_user),
) -> dict:
    if repository.get_user_card(card_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    active = repository.get_active_job(card_id)
    if active is not None:
        return {"job_id": active["id"], "status": active["status"], "direction": direction}
    with card_data_lock(card_id):
        active = repository.get_active_job(card_id)
        if active is not None:
            return {"job_id": active["id"], "status": active["status"], "direction": direction}
        repository.set_ocr_direction(card_id, direction)
        repository.set_back_ocr_direction(card_id, direction)
        job_id = repository.enqueue_job(card_id, "process_card")
        return {"job_id": job_id, "status": "queued", "direction": direction}


@router.post("/cards/{card_id}/reextract")
def reextract_card(card_id: str, user: dict = Depends(require_user)) -> dict:
    if repository.get_user_card(card_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    active = repository.get_active_job(card_id)
    if active is not None:
        return {"job_id": active["id"], "status": active["status"]}
    with card_data_lock(card_id):
        active = repository.get_active_job(card_id)
        if active is not None:
            return {"job_id": active["id"], "status": active["status"]}
        job_id = repository.enqueue_job(card_id, "reextract")
        return {"job_id": job_id, "status": "queued"}


def _image_response(card_id: str, field: str, user: dict) -> FileResponse:
    card = repository.get_user_card(card_id, user["id"])
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
def original_image(card_id: str, user: dict = Depends(require_user)) -> FileResponse:
    return _image_response(card_id, "original_image_path", user)


@router.get("/cards/{card_id}/processed-image")
def processed_image(card_id: str, user: dict = Depends(require_user)) -> FileResponse:
    return _image_response(card_id, "processed_image_path", user)


@router.get("/cards/{card_id}/thumbnail")
def thumbnail(card_id: str, user: dict = Depends(require_user)) -> FileResponse:
    return _image_response(card_id, "thumbnail_path", user)


@router.get("/cards/{card_id}/back-original-image")
def back_original_image(card_id: str, user: dict = Depends(require_user)) -> FileResponse:
    return _image_response(card_id, "back_original_image_path", user)


@router.get("/cards/{card_id}/back-processed-image")
def back_processed_image(card_id: str, user: dict = Depends(require_user)) -> FileResponse:
    return _image_response(card_id, "back_processed_image_path", user)


@router.get("/cards/{card_id}/back-thumbnail")
def back_thumbnail(card_id: str, user: dict = Depends(require_user)) -> FileResponse:
    return _image_response(card_id, "back_thumbnail_path", user)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: dict = Depends(require_user)) -> dict:
    job = repository.get_user_job(job_id, user["id"])
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/cards/{card_id}/corrections")
def card_corrections(card_id: str, user: dict = Depends(require_user)) -> dict:
    if repository.get_user_card(card_id, user["id"]) is None:
        raise HTTPException(404, "Card not found")
    return {"items": feedback.list_corrections(user["id"], card_id)}


@router.get("/corrections")
def user_corrections(user: dict = Depends(require_user)) -> dict:
    return {"items": feedback.list_corrections(user["id"])}


@router.patch("/corrections/{correction_id}")
def update_correction(correction_id: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    if not isinstance(payload.get("active"), bool):
        raise HTTPException(400, "active must be boolean")
    try:
        result = feedback.set_correction_active(user["id"], correction_id, payload["active"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Correction not found")
    return result


@router.get("/cards/{card_id}/extractions")
def card_extractions(card_id: str, user: dict = Depends(require_user)) -> dict:
    if repository.get_user_card(card_id, user["id"]) is None:
        raise HTTPException(404, "Card not found")
    from ..database import connection
    with connection() as conn:
        rows = conn.execute("SELECT * FROM extraction_runs WHERE card_id = ? AND owner_user_id = ? ORDER BY created_at DESC, rowid DESC", (card_id, user["id"])).fetchall()
        return {"items": [dict(row) for row in rows]}
