"""FastAPI application factory.

Security (spec 8 / NFR-10 / 15.6): the server binds to 127.0.0.1 only; every ``/api`` request must carry
the per-launch session token, a loopback Host header (DNS-rebinding guard) and - when present - a loopback
Origin. The token is injected into the served ``index.html``; other websites cannot read it cross-origin.
"""

from __future__ import annotations

import html
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from houseagent import __version__
from houseagent.api import properties, sites, system, tasks
from houseagent.config import Settings, get_settings
from houseagent.db import session as dbs
from houseagent.errors import AppError, ErrorCode, new_correlation_id
from houseagent.logging_setup import configure_logging
from houseagent.runtime import state as runtime

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
TOKEN_HEADER = "x-houseagent-token"
# Paths under /api that work without the token.
_OPEN_PATHS = {f"{API_PREFIX}/health"}
# Writes still allowed while the database is read-only (so the user can recover).
_READ_ONLY_ALLOWED = (f"{API_PREFIX}/backups", f"{API_PREFIX}/export", f"{API_PREFIX}/i18n/missing")


def _error(code: ErrorCode, status: int, details: dict | None = None, message_key: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error_code": code.value,
            "message_key": message_key or f"error.{code.value}",
            "correlation_id": new_correlation_id(),
            "details": details or {},
        },
    )


def _allowed_hosts(settings: Settings) -> set[str]:
    port = runtime.port
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if settings.is_dev:
        hosts |= {"127.0.0.1:5173", "localhost:5173", "testserver"}
    return hosts


def _allowed_origins(settings: Settings) -> set[str]:
    return {f"http://{h}" for h in _allowed_hosts(settings)}


def init_database(settings: Settings) -> None:
    from houseagent.services.seed import seed

    settings.ensure_dirs()
    dbs.init_engine(settings)
    dbs.run_migrations(settings)
    if dbs.read_only_reason() is None:
        with dbs.session_scope() as db:
            seed(db, include_mock=settings.enable_mock_site)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.session_token:
        runtime.token = settings.session_token

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.logs_dir)
        init_database(settings)
        from houseagent.scheduler.service import scheduler_service

        if settings.start_background:
            scheduler_service.start()
        log.info("HouseAgent %s started on %s (data: %s)", __version__, runtime.origin, settings.data_dir)
        try:
            yield
        finally:
            if settings.start_background:
                scheduler_service.shutdown()
            dbs.dispose_engine()

    app = FastAPI(
        title="HouseAgent",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.is_dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.is_dev else None,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if path.startswith("/api/"):
            host = request.headers.get("host", "")
            if host not in _allowed_hosts(settings):
                return _error(ErrorCode.UNAUTHORIZED, 403, {"reason": "host"})
            origin = request.headers.get("origin")
            if origin and origin not in _allowed_origins(settings):
                return _error(ErrorCode.UNAUTHORIZED, 403, {"reason": "origin"})
            if (
                path not in _OPEN_PATHS
                and not (settings.is_dev and path.startswith("/api/docs"))
                and path != "/api/openapi.json"
            ):
                if request.headers.get(TOKEN_HEADER) != runtime.token:
                    return _error(ErrorCode.UNAUTHORIZED, 401, {"reason": "token"})
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and dbs.read_only_reason()
                and not path.startswith(_READ_ONLY_ALLOWED)
            ):
                return _error(ErrorCode.READ_ONLY, 403, {"reason": dbs.read_only_reason()})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not path.startswith("/mock-site"):
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.exception_handler(AppError)
    async def app_error(_r: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_error(_r: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{"loc": [str(x) for x in e.get("loc", [])], "msg": str(e.get("msg", ""))[:200]} for e in exc.errors()]
        return _error(ErrorCode.VALIDATION_ERROR, 422, {"errors": errs})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.UNKNOWN_ERROR
            return _error(code, exc.status_code)
        return HTMLResponse("Not found", status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(_r: Request, exc: Exception) -> JSONResponse:
        cid = new_correlation_id()
        log.exception("unhandled error [%s]", cid)
        # No stack traces or internals in the response (production must not expose debug detail).
        return JSONResponse(
            status_code=500,
            content={
                "error_code": ErrorCode.UNKNOWN_ERROR.value,
                "message_key": "error.UNKNOWN_ERROR",
                "correlation_id": cid,
                "details": {},
            },
        )

    for r in (system.router, sites.router, tasks.router, properties.router):
        app.include_router(r, prefix=API_PREFIX)

    if settings.enable_mock_site:
        from houseagent.mock_site.app import create_mock_app

        app.mount("/mock-site", create_mock_app())

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist

    def index_html() -> HTMLResponse:
        index = dist / "index.html"
        if not index.exists():
            return HTMLResponse(
                "<h1>HouseAgent</h1><p>Frontend build not found. Run scripts\\build.ps1 "
                "or start the Vite dev server (scripts\\dev.ps1).</p>",
                status_code=503,
            )
        text = index.read_text(encoding="utf-8")
        meta = f'<meta name="houseagent-token" content="{html.escape(runtime.token)}">'
        return HTMLResponse(text.replace("<head>", "<head>" + meta, 1), headers={"Cache-Control": "no-store"})

    @app.get("/", include_in_schema=False)
    def root() -> HTMLResponse:
        return index_html()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:
        if full_path.startswith(("api/", "mock-site")):
            return _error(ErrorCode.NOT_FOUND, 404)
        candidate = (dist / full_path).resolve()
        if dist.exists() and candidate.is_file() and Path(dist.resolve()) in candidate.parents:
            return FileResponse(candidate)
        return index_html()
