"""Cross-process exclusion between user deletion and in-flight user requests."""
from contextlib import contextmanager
import fcntl
import hashlib

from fastapi import HTTPException

from ..config import settings


@contextmanager
def user_data_lock(user_id: str, *, exclusive: bool = False):
    directory = settings.data_dir / ".user-locks"
    directory.mkdir(exist_ok=True)
    # Keep lock files: unlinking one could let a new request lock another inode.
    path = directory / hashlib.sha256(user_id.encode()).hexdigest()
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HTTPException(409, "利用者の処理中です。しばらく待ってから再試行してください") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
