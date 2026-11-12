"""Application entry point.  Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from pathlib import Path

from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import core
from .api import ROUTES, api_error_handler, hub, principal_from_token
from .core import ApiError, Config

# Importing these modules registers their routes.
from . import accounts, wallet, matching, bookings, admin  # noqa: E402,F401


async def health(request):
    return JSONResponse({"ok": True})


PANEL_DIR = Path(__file__).parent / "panel"
PANEL_FILES = {"": ("index.html", "text/html"), "app.js": ("app.js", "text/javascript"), "style.css": ("style.css", "text/css")}
PANEL_HEADERS = {
    # The panel may only load its own files: no inline scripts, no outside sites, no framing by other pages.
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
                               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


async def panel(request):
    """The admin panel: a small web page served by the backend itself, at /panel/."""
    entry = PANEL_FILES.get(request.path_params.get("name", ""))
    if not entry:
        return JSONResponse({"error": {"code": "not_found", "message": "Not found."}}, status_code=404)
    return FileResponse(PANEL_DIR / entry[0], media_type=entry[1], headers=PANEL_HEADERS)


async def panel_redirect(request):
    return RedirectResponse("/panel/")


async def websocket(ws: WebSocket):
    """Live events for the apps. Connect to /ws?token=<login token>. Messages look like {"event": ..., "data": ...}."""
    try:
        user = principal_from_token(ws.query_params.get("token", ""), ("patient", "doctor"))
    except ApiError:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.add(user["id"], ws)
    try:
        while True:
            await ws.receive_text()   # the apps only listen; anything they send is ignored
            if user["role"] == "doctor":
                core.run("UPDATE doctor_profiles SET last_seen = ? WHERE user_id = ?", core.now(), user["id"])
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(user["id"], ws)


def create_app(config: Config | None = None, background_sweep: bool = True) -> Starlette:
    core.configure(config or Config.from_env())
    core.connect(core.config.database_path)
    admin.ensure_first_admin()
    hub.connections.clear()
    admin.FAILED_LOGINS.clear()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async def loop():
            while True:
                await asyncio.sleep(1)
                try:
                    await matching.tick()
                except Exception:   # keep the loop alive, but never hide the error
                    logging.getLogger("tentaka").exception("Housekeeping sweep failed")

        task = asyncio.create_task(loop()) if background_sweep else None
        yield
        if task:
            task.cancel()

    middleware = []
    if core.config.cors_origins:
        middleware.append(Middleware(CORSMiddleware, allow_origins=core.config.cors_origins,
                                     allow_methods=["*"], allow_headers=["*"]))
    return Starlette(
        routes=[Route("/health", health), Route("/panel", panel_redirect), Route("/panel/", panel),
                Route("/panel/{name}", panel), *ROUTES, WebSocketRoute("/ws", websocket)],
        exception_handlers={ApiError: api_error_handler},
        middleware=middleware,
        lifespan=lifespan,
    )


def app():
    """Factory used by uvicorn:  uvicorn app.main:app --factory"""
    return create_app()
