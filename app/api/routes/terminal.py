"""
Premium trading terminal SPA (new UI).

Serves the static terminal application (HTML/CSS/JS) at /terminal.
The legacy single-page dashboard remains available at /dashboard for
backward compatibility.

Adds two minimal read-only endpoints required by the terminal UI:
  GET /notifications          — persisted notification history (read-only)
  GET /telegram/status        — Telegram configuration/connection state
"""

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.logging import logger

router = APIRouter()

_TERMINAL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static", "terminal")
_TERMINAL_DIR = os.path.abspath(_TERMINAL_DIR)


async def serve_terminal_index():
    """Returns the shared premium terminal SPA (index.html) with the configured
    dashboard refresh interval injected.  Used by BOTH /dashboard and /terminal
    so the two routes load the exact same application.

    The JS/CSS asset URLs are versioned with a per-request timestamp so the
    browser can never serve a stale bundle after a deploy.
    """
    from fastapi.responses import HTMLResponse

    from app.config.settings import get_settings

    index = os.path.join(_TERMINAL_DIR, "index.html")
    if not os.path.exists(index):
        return JSONResponse(
            {"status": "UNAVAILABLE", "detail": "Terminal static files not found. Run the app from the project root."},
            status_code=404,
        )
    settings = get_settings()
    refresh_ms = max(5, int(getattr(settings, "DASHBOARD_REFRESH_SECONDS", 30))) * 1000
    try:
        with open(index, encoding="utf-8") as f:
            html = f.read()
    except OSError as exc:  # noqa: BLE001
        return JSONResponse({"status": "UNAVAILABLE", "detail": str(exc)}, status_code=500)
    inject = f"<script>window.XAU_REFRESH_MS = {refresh_ms};</script>"
    html = html.replace('<script src="/terminal/static/js/app.js">', inject + '<script src="/terminal/static/js/app.js">')
    # Cache-bust: version the static asset URLs so stale browser caches can
    # never show the old Live Market bundle.
    import time
    ver = int(time.time())
    for asset in (
        "/terminal/static/css/terminal.css",
        "/terminal/static/js/api.js",
        "/terminal/static/js/ui.js",
        "/terminal/static/js/charts.js",
        "/terminal/static/js/app.js",
    ):
        html = html.replace(f'"{asset}"', f'"{asset}?v={ver}"')
    return HTMLResponse(content=html)


@router.get("/terminal", include_in_schema=False)
async def get_terminal():
    """Returns the premium trading terminal SPA (shared with /dashboard)."""
    return await serve_terminal_index()


@router.get("/terminal/{path:path}", include_in_schema=False)
async def get_terminal_asset(path: str):
    """Serves terminal static assets (css/js) and SPA fallback routes.

    Adds cache-control headers so the browser always fetches the latest JS/CSS
    (prevents stale-bundle "Failed to fetch" symptoms after a deploy).
    """
    # Strip "static/" prefix from the URL path so /terminal/static/css/foo.css
    # resolves to _TERMINAL_DIR/css/foo.css, not _TERMINAL_DIR/static/css/foo.css.
    rel = path[7:] if path.startswith("static/") else path
    full = os.path.normpath(os.path.join(_TERMINAL_DIR, rel))
    if full.startswith(_TERMINAL_DIR) and os.path.isfile(full):
        return FileResponse(
            full,
            headers={
                "Cache-Control": "no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    index = os.path.join(_TERMINAL_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)  # SPA fallback for client-side routes
    return JSONResponse({"detail": "Not found"}, status_code=404)


@router.get("/notifications")
async def list_notifications(limit: int = 50):
    """Returns persisted notification history (read-only)."""
    from sqlalchemy import select

    from app.database.connection import async_session_factory
    from app.database.models import NotificationLogModel

    async with async_session_factory() as session:
        stmt = (
            select(NotificationLogModel)
            .order_by(NotificationLogModel.created_at.desc())
            .limit(min(max(limit, 1), 500))
        )
        rows = list((await session.execute(stmt)).scalars().all())
    return [
        {
            "id": r.id,
            "created_at": r.created_at,
            "channel": r.channel,
            "recipient": r.recipient,
            "status": r.status,
            "error_message": r.error_message,
            "message": (r.message_content or "")[:200],
        }
        for r in rows
    ]


@router.get("/telegram/status")
async def telegram_status():
    """Returns Telegram configuration/connection state (read-only, no secrets)."""
    from app.config.settings import get_settings
    settings = get_settings()
    configured = bool(settings.TELEGRAM_ENABLED and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)
    return {
        "enabled": bool(settings.TELEGRAM_ENABLED),
        "configured": configured,
        "candidate_alerts": bool(settings.CANDIDATE_TELEGRAM_ALERTS_ENABLED),
        "status": "CONFIGURED" if configured else "DISABLED",
        "note": "Bot token and chat id are never exposed to the client.",
    }


def mount_terminal_static(app) -> None:
    """Mounts the terminal static directory if present (for assets)."""
    if os.path.isdir(_TERMINAL_DIR):
        try:
            app.mount("/terminal/static", StaticFiles(directory=_TERMINAL_DIR), name="terminal_static")
            logger.info("Terminal static files mounted at /terminal/static.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not mount terminal static files: %s", exc)
