from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    base_path: str
    llm_provider: str
    llm_base_url: str
    llm_model: str
    gemini_api_key: str
    gemini_model: str
    gemini_base_url: str
    ocr_device: str
    yomitoku_lite: bool
    max_upload_mb: int
    multi_user_enabled: bool
    session_ttl_hours: int


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", "/data"))
    return Settings(
        data_dir=data_dir,
        base_path=os.getenv("BASE_PATH", "/").rstrip("/") or "/",
        llm_provider=os.getenv("LLM_PROVIDER", "ollama").strip().lower(),
        llm_base_url=os.getenv("LLM_BASE_URL", "http://ollama:11434").rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", "qwen2.5:7b").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip(),
        gemini_base_url=os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/"),
        ocr_device=os.getenv("OCR_DEVICE", "cpu").strip(),
        yomitoku_lite=_bool_env("YOMITOKU_LITE", True),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "20")),
        multi_user_enabled=_bool_env("MULTI_USER_ENABLED", False),
        session_ttl_hours=int(os.getenv("SESSION_TTL_HOURS", "720")),
    )


settings = load_settings()
