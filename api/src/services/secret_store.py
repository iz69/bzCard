from __future__ import annotations

import os
import fcntl
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet

from ..config import settings


def _key() -> bytes:
    configured = os.getenv("LINE_CREDENTIALS_ENCRYPTION_KEY", "").strip()
    if configured:
        return configured.encode("ascii")
    path: Path = settings.data_dir / "line-credentials.key"
    # Separate, persistent lock inode covers both existence checking and the
    # atomic publication of a complete key. Other processes never see half a key.
    with (settings.data_dir / ".line-credentials.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if path.exists():
                return path.read_bytes().strip()
            key = Fernet.generate_key()
            temporary = path.with_name(f".{path.name}.{uuid4().hex}")
            try:
                with temporary.open("xb") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(key + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return key
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def encrypt(value: str) -> str:
    return Fernet(_key()).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str | None) -> str:
    if not value:
        return ""
    return Fernet(_key()).decrypt(value.encode("ascii")).decode("utf-8")
