import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.config import settings
from app.database import close_all
from app.models.mongo import ensure_indexes
from app.routers import (
    clusters,
    health,
    ingest,
    internal,
    pr_gate,
    remediation,
    services,
    stats,
    vulnerabilities,
)
from app.services import cluster_engine

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("startup", environment=settings.environment)
    await ensure_indexes()
    if settings.environment == "production":  # load the SBERT model off the event loop
        asyncio.create_task(asyncio.to_thread(cluster_engine.load_model))
    yield
    await close_all()


app = FastAPI(title="DephGraph API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"data": None, "error": msg}, status_code=status)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return _err(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return _err(422, msg)


@app.exception_handler(httpx.HTTPError)
async def upstream_error(_: Request, exc: httpx.HTTPError) -> JSONResponse:
    log.warning("upstream_failed", error=str(exc))
    return _err(502, "Upstream service unavailable")


for r in (
    health,
    ingest,
    services,
    vulnerabilities,
    remediation,
    stats,
    internal,
    clusters,
    pr_gate,
):
    app.include_router(r.router)
