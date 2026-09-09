from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import requests
from fastapi import APIRouter, Depends

from ..auth import require_token
from ..config import settings

router = APIRouter(prefix="/api/system", dependencies=[Depends(require_token)])


@router.get("/versions")
def get_runtime_versions() -> dict:
    """Return the OCR and configured LLM versions used for card processing."""
    return {
        "ocr": {
            "engine": "yomitoku",
            "version": _package_version("yomitoku"),
            "lite": settings.yomitoku_lite,
            "device": settings.ocr_device,
        },
        "llm": _llm_version_info(),
    }


def _package_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def _llm_version_info() -> dict:
    model = settings.llm_model if settings.llm_provider == "ollama" else settings.gemini_model
    result = {
        "provider": settings.llm_provider,
        "model": model,
        "status": "configured",
        "server_version": None,
        "model_digest": None,
        "model_modified_at": None,
        "model_details": None,
    }
    if settings.llm_provider != "ollama":
        return result

    try:
        version_response = requests.get(f"{settings.llm_base_url}/api/version", timeout=3)
        version_response.raise_for_status()
        result["server_version"] = version_response.json().get("version")

        models_response = requests.get(f"{settings.llm_base_url}/api/tags", timeout=3)
        models_response.raise_for_status()
        models = models_response.json().get("models", [])
        matching_model = next(
            (item for item in models if item.get("name") == settings.llm_model),
            None,
        )
        if matching_model is None:
            result["status"] = "model_not_installed"
            return result

        result["status"] = "available"
        result["model_digest"] = matching_model.get("digest")
        result["model_modified_at"] = matching_model.get("modified_at")
        result["model_details"] = matching_model.get("details")
    except requests.RequestException:
        result["status"] = "unavailable"

    return result
