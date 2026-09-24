"""The Crate Digger server."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, get_settings
from ..sources.base import SourceError
from .auth import guard, router as auth_router
from .deps import AppState
from .routes_audio import router as audio_router
from .routes_breaks import router as breaks_router
from .routes_dig import router as dig_router
from .routes_library import router as library_router

STATIC_DIR = Path(__file__).parent / "static"

DESCRIPTION = """
A local-first crate-digging studio.

Search public-domain and openly-licensed audio archives, audition records fast,
chop what you keep, and drag the pieces straight into your DAW. Nothing leaves
your machine except the searches themselves.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_state = AppState(settings)
        app.state.crate = app_state
        await app_state.startup()
        if not settings.password_hash:
            logging.getLogger("crate").warning(
                "NO PASSWORD SET — every endpoint is open to anyone who can "
                "reach %s:%s, and /api/hunt will download for hours on request. "
                "Fine on your own machine; set one with `crate hashpw --write` "
                "before this is reachable from anywhere else.",
                settings.host, settings.port,
            )
        try:
            yield
        finally:
            await app_state.shutdown()

    app = FastAPI(
        title="Crate Digger",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(SourceError)
    async def _source_error(_: Request, exc: SourceError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    async def strip_base_path(request: Request, call_next):
        """Tolerate a proxy that does not strip the mount prefix.

        `handle_path` in Caddy strips /crate before forwarding; `handle` and a
        plain reverse_proxy do not. Without this the app sees /crate/api/...,
        matches no route and no open path, and redirects to /crate/login —
        which it also does not recognise, so it redirects again. An infinite
        loop that presents as "the URL is broken", from one word in a config
        file. Accept both shapes instead.
        """
        base = settings.base_path
        if base:
            path = request.scope["path"]
            if path == base:
                request.scope["path"] = "/"
            elif path.startswith(base + "/"):
                request.scope["path"] = path[len(base):]
        return await call_next(request)

    # Added last, so it is outermost and runs before the auth guard sees a path.
    app.middleware("http")(guard)
    app.middleware("http")(strip_base_path)
    app.include_router(auth_router)
    app.include_router(dig_router)
    app.include_router(library_router)
    app.include_router(audio_router)
    app.include_router(breaks_router)

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        app_state = request.app.state.crate
        return {
            "ok": True,
            "library_dir": str(settings.library_dir),
            "export_dir": str(settings.export_dir) if settings.export_dir else None,
            "db": str(settings.db_path),
            "fts": app_state.db.has_fts,
            "active_jobs": app_state.jobs.active,
            "auth": bool(settings.password_hash),
        }

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
