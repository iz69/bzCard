from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from ..auth import require_user
from ..config import settings
from ..services.model_info import _package_version, _llm_version_info, _kana_version_info

router = APIRouter(prefix="/api/system", dependencies=[Depends(require_user)])


@router.get("/versions")
def get_runtime_versions() -> dict:
    """Return the API build version and the models used for card processing."""
    return {
        "api": {"version": os.getenv("BZCARD_BUILD_VERSION", "dev").strip() or "dev"},
        "ocr": {
            "engine": "yomitoku",
            "version": _package_version("yomitoku"),
            "lite": "tiny" in settings.ocr_recognizer_model,
            "device": settings.ocr_device,
            "recognizer_model": settings.ocr_recognizer_model,
        },
        "llm": _llm_version_info(),
        "kana": _kana_version_info(),
    }
