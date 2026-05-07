"""FastAPI application factory and ASGI entry point."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import STATIC_DIR, get_settings
from app.db import init_db
from app.plugins.registry import get_plugin_registry
from app.routers import accounts, auth, chats, plugins
from app.services.account_session import get_session_manager

log = logging.getLogger("funpay.app")
SETTINGS = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    # Best-effort discovery of installed plugin packages on startup.
    try:
        get_plugin_registry().discover()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Plugin discovery failed: %s", exc)
    if os.environ.get("FPK_PUBLIC_BIND_WARNING") == "1":
        log.warning(
            "FPK_HOST is not localhost (%s). The local panel exposes admin-level endpoints; "
            "binding it to a public interface is strongly discouraged.",
            SETTINGS.host,
        )
    log.info("FunPay Killer %s started (env=%s).", __version__, SETTINGS.env)
    try:
        yield
    finally:
        await get_session_manager().shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="FunPay Killer",
        version=__version__,
        docs_url=None if SETTINGS.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if SETTINGS.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    # Strict CORS: only the configured origin may talk to the API. We don't actually
    # allow cross-origin in normal operation since the SPA is served from the same
    # origin, but this is defence in depth in case someone changes FPK_ALLOWED_ORIGIN.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[SETTINGS.allowed_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        # OWASP Secure Headers: deny framing, deny content-type sniffing, set CSP, etc.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        # CSP: SPA + API only. No third-party frames, no inline scripts (Vite emits hashed
        # files), no remote resources beyond FunPay-hosted user avatars rendered in the chat
        # UI. `connect-src 'self'` keeps any plugin from exfiltrating data unless the operator
        # explicitly relaxes the policy.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://funpay.com https://*.funpay.com "
            "https://sfunpay.com https://*.sfunpay.com; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'",
        )
        if SETTINGS.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(chats.router)
    app.include_router(plugins.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    _mount_frontend(app)

    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA from `app/static/`. Falls back to a placeholder page."""
    index_html = STATIC_DIR / "index.html"
    if STATIC_DIR.exists() and index_html.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(STATIC_DIR / "assets"), check_dir=False),
            name="assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> Response:
            # Don't intercept API routes.
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            requested = STATIC_DIR / full_path
            if (
                full_path
                and requested.exists()
                and requested.is_file()
                and _is_within(STATIC_DIR, requested)
            ):
                return FileResponse(requested)
            return FileResponse(index_html)
    else:

        @app.get("/", include_in_schema=False)
        async def placeholder() -> Response:
            return JSONResponse(
                {
                    "status": "frontend-not-built",
                    "hint": (
                        "Run `python run.py` once (or `npm run build` in `frontend/`) "
                        "to build the SPA."
                    ),
                }
            )


def _is_within(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


app = create_app()
