from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from ..auth import bearer_scheme, hash_password, issue_session, require_user, revoke_session, user_allowed_in_current_mode, verify_password
from ..config import settings
from ..database import backup_before_local_auth
from ..services import repository


router = APIRouter(prefix="/api/auth")


@router.get("/bootstrap-status")
def bootstrap_status() -> dict:
    return {"needs_bootstrap": not repository.has_users()}


@router.post("/bootstrap")
def bootstrap(payload: dict) -> dict:
    login_id, password = _credentials(payload)
    if repository.has_users():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Initial setup is already complete")
    backup_before_local_auth()
    user = repository.bootstrap_first_user(login_id, hash_password(password))
    token, expires_at = issue_session(user["id"])
    return {"user": _public_user(user), "session_token": token, "expires_at": expires_at}


@router.post("/login")
def login(payload: dict) -> dict:
    login_id, password = _credentials(payload)
    user = repository.get_user_by_login_id(login_id)
    if user is None or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="IDまたはパスワードが違います")
    if user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="このユーザーは利用停止中です")
    if not user_allowed_in_current_mode(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="このサーバーはシングルユーザーモードです")
    token, expires_at = issue_session(user["id"])
    return {"user": _public_user(user), "session_token": token, "expires_at": expires_at}


@router.get("/me")
def me(user: dict = Depends(require_user)) -> dict:
    return {"user": _public_user(user), "multi_user_enabled": settings.multi_user_enabled}


@router.post("/logout")
def logout(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> dict:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        revoke_session(credentials.credentials)
    return {"status": "ok"}


@router.post("/password")
def change_password(payload: dict, user: dict = Depends(require_user)) -> dict:
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    if not verify_password(current_password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="現在のパスワードが違います")
    _validate_password(new_password)
    repository.change_user_password(user["id"], hash_password(new_password))
    return {"status": "ok"}


@router.post("/users")
def create_user(payload: dict, admin: dict = Depends(require_user)) -> dict:
    _require_multi_user_mode()
    if admin["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理者権限が必要です")
    login_id, password = _credentials(payload)
    try:
        user = repository.create_user(login_id, hash_password(password), role="user")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"user": _public_user(user)}


@router.get("/users")
def list_users(admin: dict = Depends(require_user)) -> dict:
    _require_multi_user_mode()
    if admin["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理者権限が必要です")
    return {"items": [_public_user(user) for user in repository.list_users()]}


def _credentials(payload: dict) -> tuple[str, str]:
    login_id = str(payload.get("login_id") or "").strip()
    password = str(payload.get("password") or "")
    if not login_id or len(login_id) > 128:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ログインIDは1〜128文字で入力してください")
    _validate_password(password)
    return login_id, password


def _validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="パスワードは12文字以上にしてください")


def _require_multi_user_mode() -> None:
    if not settings.multi_user_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="シングルユーザーモードでは利用者を追加・管理できません")


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "login_id": user["login_id"],
        "role": user["role"],
        "status": user["status"],
    }
