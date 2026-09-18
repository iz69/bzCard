from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .services import repository
from .config import settings
from .services.timeutil import now_iso

bearer_scheme = HTTPBearer(auto_error=False)


def user_allowed_in_current_mode(user: dict) -> bool:
    """Single-user mode permits only the server's administrator account."""
    return settings.multi_user_enabled or user.get("role") == "admin"


def require_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> dict:
    """Authenticate an active bzCard user from a server-issued session."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing user session token",
        )
    user = repository.get_session_user(_session_token_hash(credentials.credentials), now_iso())
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired user session token",
        )
    if not user_allowed_in_current_mode(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="このサーバーはシングルユーザーモードです",
        )
    return user


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(user_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    ).isoformat(timespec="seconds")
    repository.create_session(_session_token_hash(token), user_id, expires_at)
    return token, expires_at


def revoke_session(token: str) -> None:
    repository.delete_session(_session_token_hash(token))


def hash_password(password: str) -> str:
    """Return a self-contained scrypt password hash using a per-user random salt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, hash_b64 = stored.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_b64.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error):
        return False
