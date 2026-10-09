import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routers import system


class SystemVersionTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(system.router)
        # No lifespan, database, worker, or external model services are started.
        self.client = TestClient(self.app)

    def test_build_metadata_preserves_existing_model_versions(self):
        self.app.dependency_overrides[system.require_user] = lambda: {"id": "test-user"}
        settings = SimpleNamespace(ocr_recognizer_model="parseq-large-v4_1", ocr_device="cpu")
        llm = {"provider": "ollama", "model": "test-model", "status": "unavailable"}
        kana = {"model": "test-kana", "status": "unavailable"}
        with patch.object(system, "settings", settings), \
                patch.object(system, "_package_version", return_value="0.10.0"), \
                patch.object(system, "_llm_version_info", return_value=llm), \
                patch.object(system, "_kana_version_info", return_value=kana):
            for configured, expected in [(None, "dev"), ("", "dev"), ("dev", "dev"), ("v0.9.3", "v0.9.3")]:
                with self.subTest(build_version=configured), patch.dict(os.environ):
                    if configured is None:
                        os.environ.pop("BZCARD_BUILD_VERSION", None)
                    else:
                        os.environ["BZCARD_BUILD_VERSION"] = configured
                    response = self.client.get("/api/system/versions")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {
                        "api": {"version": expected},
                        "ocr": {"engine": "yomitoku", "version": "0.10.0", "lite": False,
                                "device": "cpu", "recognizer_model": "parseq-large-v4_1"},
                        "llm": llm,
                        "kana": kana,
                    })

    def test_build_metadata_still_requires_a_user_session(self):
        with patch.object(system, "_llm_version_info") as llm:
            response = self.client.get("/api/system/versions")
            self.assertEqual(response.status_code, 401)
            llm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
