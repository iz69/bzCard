from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_user, user_allowed_in_current_mode
from ..services import repository

router = APIRouter(prefix="/api/line-connections")


@router.get("/{connection_id}/liff-config")
def liff_config(connection_id: str) -> dict:
    config = repository.public_liff_configuration(connection_id)
    connection = repository.get_line_connection(connection_id)
    owner = repository.get_user_by_id(connection["owner_user_id"]) if connection else None
    if config is None or owner is None or not user_allowed_in_current_mode(owner):
        raise HTTPException(status_code=404, detail="LIFF connection is not configured")
    return config

@router.get("/me")
def mine(user: dict = Depends(require_user)) -> dict:
    return {"connection": repository.public_line_connection(repository.get_user_line_connection(user["id"]))}

@router.put("/me")
def save(payload: dict, user: dict = Depends(require_user)) -> dict:
    try:
        connection = repository.save_user_line_connection(user["id"], payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"connection": repository.public_line_connection(connection)}

@router.post("/me/link-url")
def link_url(user: dict = Depends(require_user)) -> dict:
    connection = repository.get_user_line_connection(user["id"])
    if connection is None or not connection.get("liff_url") or not connection.get("line_login_channel_id"):
        raise HTTPException(status_code=400, detail="先に公式LINE情報を保存してください")
    token, expires_at = repository.create_line_link_request(connection["id"])
    separator = "&" if "?" in connection["liff_url"] else "?"
    return {"url": f"{connection['liff_url']}{separator}connection={connection['id']}&link={token}", "expires_at": expires_at}
