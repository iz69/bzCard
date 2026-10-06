import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PublicPathTests(unittest.TestCase):
    def test_docs_and_schema_follow_the_configured_public_path(self):
        # Load the actual application with each deployment's environment.
        # TestClient is used without a lifespan, so no database or worker starts.
        script = """
import json
from fastapi.testclient import TestClient
from src.main import app
client = TestClient(app, base_url="https://cards.example")
docs = client.get("/docs")
schema = client.get("/openapi.json")
print(json.dumps({
    "root_path": app.root_path,
    "docs_status": docs.status_code,
    "docs": docs.text,
    "schema_status": schema.status_code,
    "servers": schema.json()["servers"],
}))
"""
        with tempfile.TemporaryDirectory() as directory:
            for configured, prefix in [
                ("/", ""),
                ("", ""),
                ("/bzcard-api/", "/bzcard-api"),
                (" /tools/cards-api/// ", "/tools/cards-api"),
            ]:
                with self.subTest(base_path=configured):
                    environment = dict(os.environ, BASE_PATH=configured, DATA_DIR=directory)
                    result = subprocess.run(
                        [sys.executable, "-B", "-c", script],
                        cwd=Path(__file__).resolve().parent,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                    response = json.loads(result.stdout)
                    self.assertEqual(response["root_path"], prefix)
                    self.assertEqual(response["docs_status"], 200)
                    self.assertEqual(response["schema_status"], 200)
                    self.assertIn("url: '" + prefix + "/openapi.json'", response["docs"])
                    self.assertNotIn("url: '//openapi.json'", response["docs"])
                    self.assertEqual(response["servers"], [{"url": prefix or "/"}])


if __name__ == "__main__":
    unittest.main()
