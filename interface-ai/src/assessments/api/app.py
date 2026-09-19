"""FastAPI application factory."""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse

from assessments import __version__
from assessments.api import capabilities, operator
from assessments.config import Settings, get_settings
from assessments.errors import register_error_handlers
from assessments.logging import request_id_var

logger = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="assessments", version=__version__)
    app.state.settings = settings
    register_error_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Each request runs in its own task context, so the var is request-scoped. It is not
        # reset here so the outermost 500 handler can still read it.
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        request_id_var.set(request_id)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        # Console polling is high-volume and uninteresting; everything else is logged.
        polling = request.method == "GET" and request.url.path.startswith("/api/sessions")
        (logger.debug if polling else logger.info)(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        return response

    app.include_router(operator.router)
    app.include_router(capabilities.router)
    app.include_router(capabilities.pages)

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/catalog")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
