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
from app.limiter import RateLimitMiddleware
from app.models.mongo import ensure_indexes
from app.observability import (
    RequestContextMiddleware,
    configure_logging,
    configure_sentry,
)
from app.routers import (
    clusters,
    health,
    ingest,
    internal,
    me,
    pr_gate,
    remediation,
    runtime,
    services,
    stats,
    teams,
    vulnerabilities,
)
from app.services import pr_gate_engine

configure_logging()
configure_sentry()
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("startup", environment=settings.environment)
    await ensure_indexes()
    yield
    await pr_gate_engine.close_groq()
    await close_all()


app = FastAPI(title="DephGraph API", lifespan=lifespan)
# add_middleware: last added is outermost, so CORS headers also land on 429/500 responses
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
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
    me,
    clusters,
    pr_gate,
    runtime,
    teams,
):
    app.include_router(r.router)
