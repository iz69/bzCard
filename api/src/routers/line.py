from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import shutil

import requests
from fastapi import APIRouter, HTTPException, Request, status

from ..auth import issue_session, user_allowed_in_current_mode
from ..services import repository
from ..services.image_store import card_dir, make_card_id, relative_path, save_original_bytes, sha256_file

logger = logging.getLogger("bzcard.line")
router = APIRouter(prefix="/line")
CONTENT = "https://api-data.line.me/v2/bot/message/{}/content"
REPLY = "https://api.line.me/v2/bot/message/reply"
VERIFY = "https://api.line.me/oauth2/v2.1/verify"


@router.post("/auth/login")
def liff_login(payload: dict) -> dict:
    connection = repository.get_line_connection(str(payload.get("connection_id") or ""))
    token = str(payload.get("id_token") or "")
    if not connection or not token or not connection.get("line_login_channel_id"):
        raise HTTPException(400, "LIFF connection is not configured")
    try:
        response = requests.post(VERIFY, data={"id_token": token, "client_id": connection["line_login_channel_id"]}, timeout=10)
        verified = response.json() if response.ok else {}
    except requests.RequestException as exc:
        raise HTTPException(502, "Could not verify LINE ID token") from exc
    if not verified.get("sub"):
        raise HTTPException(401, "Invalid LINE ID token")

    line_user_id = str(verified["sub"])
    link_token = str(payload.get("link_token") or "")
    if not connection.get("line_user_id") and link_token and repository.consume_line_link_request(link_token, connection["id"]):
        try:
            repository.bind_line_identity(connection["id"], line_user_id)
        except Exception as exc:
            logger.info("LINE identity binding was rejected for connection %s", connection["id"])
            raise HTTPException(409, "This LINE account is already linked to another bzCard user") from exc
        connection = repository.get_line_connection(connection["id"]) or connection
    if connection.get("line_user_id") != line_user_id:
        raise HTTPException(403, "This LINE account is not linked to this bzCard user")
    user = repository.get_user_by_id(connection["owner_user_id"])
    if not user or user["status"] != "active" or not user_allowed_in_current_mode(user):
        raise HTTPException(403, "Linked user is unavailable")
    session, expires_at = issue_session(user["id"])
    return {"session_token": session, "expires_at": expires_at}


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    body = await request.body()
    signature = request.headers.get("x-line-signature", "")
    connection = _connection_for_signature(body, signature)
    if connection is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid LINE signature")
    owner = repository.get_user_by_id(connection["owner_user_id"])
    if owner is None or not user_allowed_in_current_mode(owner):
        logger.info("ignored LINE webhook for a disabled user connection")
        return {"status": "disabled"}
    for event in (await request.json()).get("events", []):
        try:
            _handle(event, connection)
        except Exception:
            logger.exception("LINE event failed")
    return {"status": "ok"}


def _connection_for_signature(body: bytes, signature: str) -> dict | None:
    if not signature:
        return None
    for connection in repository.list_line_connections():
        try:
            secret, _ = repository.connection_credentials(connection)
        except Exception:
            # A corrupt connector must not make the other official accounts unusable.
            logger.warning("Could not read credentials for LINE connection %s", connection["id"])
            continue
        if not secret:
            continue
        expected = base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")
        if hmac.compare_digest(expected, signature):
            return connection
    return None


def _handle(event: dict, connection: dict) -> None:
    message = event.get("message") or {}
    raw_id = event.get("webhookEventId") or message.get("id")
    if not raw_id:
        return
    event_id = f"{connection['id']}:{raw_id}"
    sender = (event.get("source") or {}).get("userId")
    if not repository.claim_line_event(event_id, event.get("type"), sender, message.get("id")):
        return

    reply_token = event.get("replyToken")
    # The webhook signature proves the official account, not the LINE sender.
    # A connector is usable only by its explicitly linked LINE account.
    if not sender or sender != connection.get("line_user_id"):
        repository.finish_line_event(event_id, "unauthorized")
        _reply(reply_token, "この公式LINEは、連携済みのLINEアカウントのみ利用できます。", connection)
        return
    if event.get("type") != "message":
        repository.finish_line_event(event_id, "ignored")
        return

    owner = connection["owner_user_id"]
    if message.get("type") == "text":
        query = str(message.get("text") or "").strip()
        if not query:
            repository.finish_line_event(event_id, "ignored")
            _reply(reply_token, "検索したい氏名または会社名を送ってください。", connection)
            return
        cards = repository.list_user_cards(owner, q=query)
        repository.finish_line_event(event_id, "searched")
        _reply(reply_token, _search(cards, connection), connection)
        return
    if message.get("type") != "image" or not message.get("id"):
        repository.finish_line_event(event_id, "ignored")
        _reply(reply_token, "名刺画像を送ってください。", connection)
        return

    card_id = make_card_id()
    try:
        data, content_type = _download(message["id"], connection)
        original = save_original_bytes(data, content_type, card_id)
        digest = sha256_file(original)
        duplicate = repository.get_user_owned_card_by_original_sha256(digest, owner)
        if duplicate:
            shutil.rmtree(card_dir(card_id), ignore_errors=True)
            repository.finish_line_event(event_id, "duplicate", duplicate["id"])
            _reply(reply_token, _link_message("すでに登録済みの名刺でした。", duplicate["id"], connection), connection)
            return
        repository.create_card(card_id, relative_path(original), digest, "auto", owner)
        repository.set_card_source(card_id, "line", message["id"])
        repository.finish_line_event(event_id, "queued", card_id)
        _reply(reply_token, _link_message("名刺画像を受け付けました。", card_id, connection), connection)
    except Exception as exc:
        repository.finish_line_event(event_id, "error", error_message=str(exc))
        _reply(reply_token, "画像を取り込めませんでした。", connection)
        raise


def _download(message_id: str, connection: dict) -> tuple[bytes, str]:
    _, token = repository.connection_credentials(connection)
    response = requests.get(CONTENT.format(message_id), headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if not response.ok:
        raise HTTPException(502, "Failed to download LINE image")
    return response.content, response.headers.get("content-type", "image/jpeg")


def _reply(reply_token: str | None, text: str, connection: dict) -> None:
    if not reply_token:
        return
    try:
        _, token = repository.connection_credentials(connection)
        requests.post(REPLY, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}, timeout=10).raise_for_status()
    except Exception:
        logger.exception("LINE reply failed")


def _link_message(text: str, card_id: str, connection: dict) -> str:
    url = _liff_url(connection, card_id)
    return f"{text}\n確認: {url}" if url else text


def _liff_url(connection: dict, card_id: str) -> str:
    base = connection.get("liff_url") or ""
    if not base:
        return ""
    return f"{base}{'&' if '?' in base else '?'}connection={connection['id']}&card={card_id}"


def _search(cards: list[dict], connection: dict) -> str:
    if not cards:
        return "一致する名刺は見つかりませんでした。"
    lines = [f"{len(cards)}件見つかりました。"]
    for index, card in enumerate(cards[:5], 1):
        title = card.get("person_name") or card.get("company_name") or "名称未設定"
        company = card.get("company_name") or "-"
        lines += ["", f"{index}. {title}", f"   {company}", _liff_url(connection, card["id"])]
    return "\n".join(lines)
