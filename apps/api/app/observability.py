import logging
import re
import time
import uuid
from typing import Any

import sentry_sdk
import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import settings

log = structlog.get_logger()
_REQUEST_ID = re.compile(r"[\w-]{1,64}")


def configure_logging() -> None:
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.environment == "production"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


# Never leave the process: credentials, cookies, webhook signatures, the cron secret.
_SECRET_HEADERS = {"authorization", "cookie", "x-hub-signature-256", "x-cron-secret"}


def scrub_event(event: Any, _hint: Any) -> Any:
    """Sentry before_send: strip secret headers and the request body (webhook payloads, manifests)."""
    if request := event.get("request"):
        request.pop("data", None)
        request.pop("cookies", None)
        request["headers"] = {
            k: v
            for k, v in (request.get("headers") or {}).items()
            if k.lower() not in _SECRET_HEADERS
        }
    return event


def configure_sentry() -> None:
    """Report unhandled errors to Sentry, only when SENTRY_DSN is set."""
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
            before_send=scrub_event,
        )


# Responses are JSON for programmatic clients: never framed, never sniffed, HTTPS only.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id + security headers + access log + last-resort 500 envelope (no traceback leaks)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id", "")
        if not _REQUEST_ID.fullmatch(rid):
            rid = uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error")
            response = JSONResponse({"data": None, "error": "Internal server error"}, 500)
        response.headers["X-Request-ID"] = rid
        response.headers.update(SECURITY_HEADERS)
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return response
