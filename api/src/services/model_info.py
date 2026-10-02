from importlib.metadata import PackageNotFoundError, version

import requests
from ..config import settings

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


def _kana_version_info() -> dict:
    result = {"model": "kanjikana-1.9o", "status": "unavailable"}
    if not settings.kana_base_url:
        result["status"] = "not_configured"
        return result
    try:
        response = requests.get(f"{settings.kana_base_url}/health", timeout=3)
        response.raise_for_status()
        body = response.json()
        result["model"] = body.get("model") or result["model"]
        result["status"] = body.get("status") or "unavailable"
    except (requests.RequestException, ValueError):
        pass
    return result
