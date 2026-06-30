from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    api_token: str
    base_path: str
    llm_base_url: str
    llm_model: str
    ocr_device: str
    yomitoku_lite: bool
    max_upload_mb: int
    line_channel_secret: str
    line_channel_access_token: str
    line_login_channel_id: str
    line_liff_url: str
    line_invite_codes: tuple[str, ...]
    line_session_ttl_hours: int
    line_card_scope: str


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", "/data"))
    return Settings(
        data_dir=data_dir,
        api_token=os.getenv("APP_API_TOKEN", "change-me").strip(),
        base_path=os.getenv("BASE_PATH", "/").rstrip("/") or "/",
        llm_base_url=os.getenv("LLM_BASE_URL", "http://ollama:11434").rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", "qwen2.5:7b").strip(),
        ocr_device=os.getenv("OCR_DEVICE", "cpu").strip(),
        yomitoku_lite=_bool_env("YOMITOKU_LITE", True),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "20")),
        line_channel_secret=os.getenv("LINE_CHANNEL_SECRET", "").strip(),
        line_channel_access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip(),
        line_login_channel_id=os.getenv("LINE_LOGIN_CHANNEL_ID", "").strip(),
        line_liff_url=os.getenv("LINE_LIFF_URL", "").strip(),
        line_invite_codes=tuple(
            code.strip()
            for code in os.getenv("LINE_INVITE_CODES", "").split(",")
            if code.strip()
        ),
        line_session_ttl_hours=int(os.getenv("LINE_SESSION_TTL_HOURS", "720")),
        line_card_scope=os.getenv("LINE_CARD_SCOPE", "personal").strip().lower(),
    )


settings = load_settings()
