"""Private HTTP endpoint for the local name reading model."""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import torch

from model import KanaModel, MODEL_VERSION

torch.set_num_threads(2)
model = KanaModel(Path(os.getenv("KANA_CHECKPOINT", "/model/checkpoint_best.pt")))
lock = threading.Lock()
logging.basicConfig(level=logging.INFO)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok", "model": MODEL_VERSION})
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/predict":
            self.respond(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024:
                raise ValueError("invalid body size")
            body = json.loads(self.rfile.read(size))
            name = body.get("name")
            family = body.get("family")
            if not isinstance(name, str) or not isinstance(family, str):
                raise ValueError("name and family must be strings")
            with lock:
                candidates = model.predict(name)
                family_candidates = model.predict(family)
            self.respond(200, {"model": MODEL_VERSION, "candidates": candidates, "family_candidates": family_candidates})
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})

    def respond(self, status: int, body: dict):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


ThreadingHTTPServer(("0.0.0.0", 8001), Handler).serve_forever()
