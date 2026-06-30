from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers.cards import router as cards_router
from .routers.line import router as line_router
from .services.repository import normalize_existing_company_names
from .worker import start_worker

logging.basicConfig(
    level=logging.INFO,
    format=":: %(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bzcard")

swagger_enabled = True

app = FastAPI(
    title="bzcard API",
    docs_url="/docs" if swagger_enabled else None,
    redoc_url="/redoc" if swagger_enabled else None,
    swagger_ui_parameters={"url": f"{settings.base_path}/openapi.json"},
    servers=[{"url": settings.base_path}],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cards_router)
app.include_router(line_router)


@app.on_event("startup")
def startup() -> None:
    init_db()
    normalized_count = normalize_existing_company_names()
    start_worker()
    if normalized_count:
        logger.info("normalized %s company names", normalized_count)
    logger.info("bzcard initialized at %s", settings.data_dir)


@app.get("/ping")
@app.head("/ping")
def ping() -> Response:
    return Response(status_code=200)
