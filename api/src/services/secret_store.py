from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

from ..config import settings


def _key() -> bytes:
    configured = os.getenv("LINE_CREDENTIALS_ENCRYPTION_KEY", "").strip()
    if configured:
        return configured.encode("ascii")
    path: Path = settings.data_dir / "line-credentials.key"
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key + b"\n")
    path.chmod(0o600)
    return key


def encrypt(value: str) -> str:
    return Fernet(_key()).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str | None) -> str:
    if not value:
        return ""
    return Fernet(_key()).decrypt(value.encode("ascii")).decode("utf-8")
