from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import shutil
import secrets
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import settings
from ..services import repository
from ..services.image_store import (
    card_dir,
    make_card_id,
    relative_path,
    resolve_data_path,
    save_original_bytes,
    sha256_file,
)
from ..services.timeutil import now_iso

logger = logging.getLogger("bzcard.line")

router = APIRouter(prefix="/line")
line_bearer_scheme = HTTPBearer(auto_error=False)

LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message/{message_id}/content"
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_VERIFY_ID_TOKEN_URL = "https://api.line.me/oauth2/v2.1/verify"


def _require_active_line_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(line_bearer_scheme),
) -> dict:
    return _require_line_session(credentials)


@router.post("/auth/login")
def login(payload: dict) -> dict:
    _require_line_login_config()
    id_token = str(payload.get("id_token") or "").strip()
    invite_code = str(payload.get("invite_code") or "").strip()
    if not id_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="id_token is required")

    profile = _verify_id_token(id_token)
    line_user_id = profile["sub"]
    user = repository.upsert_line_user_profile(
        line_user_id=line_user_id,
        display_name=profile.get("name"),
        picture_url=profile.get("picture"),
    )

    if user["status"] == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="LINE user is suspended")

    if user["status"] != "active":
        if invite_code:
            if not _valid_invite_code(invite_code):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid invite code")
            user = repository.activate_line_user(line_user_id)
        else:
            return {"user": _public_user(user), "status": user["status"], "needs_invite": True}

    token, expires_at = _issue_line_session(line_user_id)
    return {
        "user": _public_user(user),
        "session_token": token,
        "expires_at": expires_at,
        "needs_invite": False,
    }


@router.get("/auth/me")
def me(user: dict = Depends(_require_active_line_user)) -> dict:
    return {"user": _public_user(user)}


@router.post("/auth/logout")
def logout(credentials: HTTPAuthorizationCredentials | None = Depends(line_bearer_scheme)) -> dict:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        repository.delete_line_session(_session_token_hash(credentials.credentials))
    return {"status": "ok"}


@router.get("/cards")
def list_my_cards(
    q: str | None = None,
    status: str | None = None,
    user: dict = Depends(_require_active_line_user),
) -> dict:
    return {"items": _list_accessible_cards(user, q=q, status=status)}


@router.get("/cards/{card_id}")
def get_my_card(card_id: str, user: dict = Depends(_require_active_line_user)) -> dict:
    card = _get_accessible_card(card_id, user)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
    return card


@router.patch("/cards/{card_id}")
def update_my_card(card_id: str, payload: dict, user: dict = Depends(_require_active_line_user)) -> dict:
    if _get_accessible_card(card_id, user) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
    return repository.update_card_fields(card_id, payload) or {}


@router.get("/cards/{card_id}/{image_name}")
def get_my_card_image(
    card_id: str,
    image_name: str,
    user: dict = Depends(_require_active_line_user),
) -> FileResponse:
    fields = {
        "original-image": "original_image_path",
        "processed-image": "processed_image_path",
        "thumbnail": "thumbnail_path",
        "back-original-image": "back_original_image_path",
        "back-processed-image": "back_processed_image_path",
        "back-thumbnail": "back_thumbnail_path",
    }
    field = fields.get(image_name)
    if field is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    card = _get_accessible_card(card_id, user)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
    rel = card.get(field)
    if not rel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not ready")
    path = resolve_data_path(rel)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file not found")
    return FileResponse(path)


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    body = await request.body()
    _require_line_config()
    _verify_signature(body, request.headers.get("x-line-signature", ""))
    payload = await request.json()

    for event in payload.get("events", []):
        try:
            _handle_event(event)
        except Exception:
            logger.exception("failed to handle LINE event")

    return {"status": "ok"}


def _handle_event(event: dict) -> None:
    event_type = event.get("type")
    source = event.get("source") or {}
    line_user_id = source.get("userId")
    message = event.get("message") or {}
    message_id = message.get("id")
    event_id = event.get("webhookEventId") or message_id
    reply_token = event.get("replyToken")

    if not event_id:
        return

    if not repository.claim_line_event(event_id, event_type, line_user_id, message_id):
        return

    user = repository.get_line_user(line_user_id) if line_user_id else None
    if user is None or user["status"] != "active":
        repository.finish_line_event(event_id, "unauthorized")
        _reply(reply_token, _registration_message(user))
        return

    if event_type != "message":
        repository.finish_line_event(event_id, "ignored")
        _reply(reply_token, "名刺画像を送ってください。JPEG/PNGに対応しています。")
        return

    if message.get("type") == "text":
        query = str(message.get("text") or "").strip()
        _handle_search(event_id, reply_token, line_user_id, query)
        return

    if message.get("type") != "image" or not message_id:
        repository.finish_line_event(event_id, "ignored")
        _reply(reply_token, "名刺画像を送るか、検索したい名前や会社名を入力してください。")
        return

    card_id = make_card_id()
    try:
        image_data, content_type = _download_message_content(message_id)
        original = save_original_bytes(image_data, content_type, card_id)
        original_sha256 = sha256_file(original)
        duplicate = _get_duplicate_card(original_sha256, line_user_id)
        if duplicate is not None:
            shutil.rmtree(card_dir(card_id), ignore_errors=True)
            repository.finish_line_event(event_id, "duplicate", duplicate["id"])
            _reply(reply_token, _accepted_message(duplicate["id"], duplicate=True))
            return

        job_id = repository.create_card(card_id, relative_path(original), original_sha256, "auto")
        repository.set_card_source(card_id, "line", message_id)
        repository.set_card_owner(card_id, line_user_id)
        repository.finish_line_event(event_id, "queued", card_id)
        logger.info("queued LINE card %s with job %s", card_id, job_id)
        _reply(reply_token, _accepted_message(card_id))
    except HTTPException as exc:
        repository.finish_line_event(event_id, "error", None, str(exc.detail))
        _reply(reply_token, f"画像を取り込めませんでした: {exc.detail}")
    except Exception as exc:
        repository.finish_line_event(event_id, "error", None, str(exc))
        _reply(reply_token, "画像を取り込めませんでした。サーバログを確認してください。")
        raise


def _handle_search(event_id: str, reply_token: str | None, line_user_id: str, query: str) -> None:
    normalized = _normalize_search_query(query)
    if not normalized:
        repository.finish_line_event(event_id, "ignored")
        _reply(reply_token, "検索したい名前、会社名、電話番号、メールアドレスなどを入力してください。")
        return

    cards = _list_accessible_cards({"line_user_id": line_user_id}, q=normalized)
    repository.finish_line_event(event_id, "searched")
    _reply(reply_token, _search_result_message(normalized, cards))


def _require_line_config() -> None:
    if not settings.line_channel_secret or not settings.line_channel_access_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LINE integration is not configured",
        )


def _require_line_login_config() -> None:
    if not settings.line_login_channel_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LINE login channel ID is not configured",
        )


def _line_card_scope() -> str:
    if settings.line_card_scope == "owner":
        return "owner"
    return "personal"


def _list_accessible_cards(user: dict, q: str | None = None, status: str | None = None) -> list[dict]:
    if _line_card_scope() == "owner":
        return repository.list_line_user_cards(user["line_user_id"], q=q, status=status)
    return repository.list_cards(q=q, status=status)


def _get_accessible_card(card_id: str, user: dict) -> dict | None:
    if _line_card_scope() == "owner":
        return repository.get_line_user_card(card_id, user["line_user_id"])
    return repository.get_card(card_id)


def _get_duplicate_card(original_sha256: str, line_user_id: str) -> dict | None:
    if _line_card_scope() == "owner":
        return repository.get_line_owned_card_by_original_sha256(original_sha256, line_user_id)
    return repository.get_card_by_original_sha256(original_sha256)


def _verify_signature(body: bytes, signature: str) -> None:
    digest = hmac.new(settings.line_channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid LINE signature")


def _verify_id_token(id_token: str) -> dict:
    response = requests.post(
        LINE_VERIFY_ID_TOKEN_URL,
        data={"id_token": id_token, "client_id": settings.line_login_channel_id},
        timeout=10,
    )
    if not response.ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid LINE ID token")
    payload = response.json()
    if not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="LINE ID token has no user")
    return payload


def _valid_invite_code(invite_code: str) -> bool:
    return any(hmac.compare_digest(invite_code, allowed) for allowed in settings.line_invite_codes)


def _issue_line_session(line_user_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=settings.line_session_ttl_hours)
    ).isoformat(timespec="seconds")
    repository.create_line_session(_session_token_hash(token), line_user_id, expires_at)
    return token, expires_at


def _require_line_session(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing LINE session token")
    user = repository.get_line_session_user(_session_token_hash(credentials.credentials), now_iso())
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid LINE session token")
    return user


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _download_message_content(message_id: str) -> tuple[bytes, str]:
    response = requests.get(
        LINE_CONTENT_URL.format(message_id=message_id),
        headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
        timeout=20,
    )
    if not response.ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to download LINE message content: {response.status_code}",
        )
    return response.content, response.headers.get("content-type", "image/jpeg")


def _reply(reply_token: str | None, text: str) -> None:
    if not reply_token:
        return
    try:
        requests.post(
            LINE_REPLY_URL,
            headers={
                "Authorization": f"Bearer {settings.line_channel_access_token}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
            timeout=10,
        ).raise_for_status()
    except Exception:
        logger.exception("failed to reply to LINE")


def _accepted_message(card_id: str, duplicate: bool = False) -> str:
    status = "すでに登録済みの名刺でした。" if duplicate else "名刺画像を受け付けました。"
    if settings.line_liff_url:
        separator = "&" if "?" in settings.line_liff_url else "?"
        return f"{status}\n確認: {settings.line_liff_url}{separator}card={card_id}"
    return f"{status}\n管理画面で処理結果を確認してください。"


def _search_result_message(query: str, cards: list[dict]) -> str:
    if not cards:
        return f"「{query}」に一致する名刺は見つかりませんでした。"

    total = len(cards)
    lines = [f"「{query}」で{total}件見つかりました。"]
    for index, card in enumerate(cards[:5], start=1):
        title = card.get("person_name") or card.get("company_name") or "名称未設定"
        company = card.get("company_name") or "-"
        tags = card.get("tags") or ""
        lines.append("")
        lines.append(f"{index}. {title}")
        lines.append(f"   {company}")
        if tags:
            lines.append(f"   タグ: {tags}")
        if settings.line_liff_url:
            separator = "&" if "?" in settings.line_liff_url else "?"
            lines.append(f"   確認: {settings.line_liff_url}{separator}card={card['id']}")
    if total > 5:
        lines.append("")
        lines.append(f"ほか{total - 5}件あります。検索語を追加すると絞り込めます。")
    return "\n".join(lines)


def _normalize_search_query(query: str) -> str:
    prefixes = ("検索 ", "検索　", "/search ", "/search　", "s ", "s　")
    for prefix in prefixes:
        if query.lower().startswith(prefix):
            query = query[len(prefix):]
            break
    return query.strip()


def _registration_message(user: dict | None) -> str:
    if user and user.get("status") == "suspended":
        return "このLINEアカウントは利用停止中です。"
    if settings.line_liff_url:
        return f"利用登録が必要です。\n登録または名刺一覧: {settings.line_liff_url}"
    return "利用登録が必要です。管理者に連絡してください。"


def _public_user(user: dict) -> dict:
    return {
        "line_user_id": user["line_user_id"],
        "display_name": user.get("display_name"),
        "picture_url": user.get("picture_url"),
        "status": user["status"],
        "role": user["role"],
    }
