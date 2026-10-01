"""Serialize image mutations and OCR for one card across processes."""
from contextlib import contextmanager
import fcntl
import hashlib

from fastapi import HTTPException

from ..config import settings


@contextmanager
def card_data_lock(card_id: str, *, wait: bool = False):
    directory = settings.data_dir / ".card-locks"
    directory.mkdir(exist_ok=True)
    path = directory / hashlib.sha256(card_id.encode()).hexdigest()
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise HTTPException(409, "名刺を処理中です。しばらく待ってから再試行してください") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
