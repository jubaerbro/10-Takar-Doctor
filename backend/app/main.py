"""Application entry point.  Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import core
from .api import ROUTES, api_error_handler, hub, principal_from_token
from .core import ApiError, Config

# Importing these modules registers their routes.
from . import accounts, wallet, matching, admin  # noqa: E402,F401


async def health(request):
    return JSONResponse({"ok": True})


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
        routes=[Route("/health", health), *ROUTES, WebSocketRoute("/ws", websocket)],
        exception_handlers={ApiError: api_error_handler},
        middleware=middleware,
        lifespan=lifespan,
    )


def app():
    """Factory used by uvicorn:  uvicorn app.main:app --factory"""
    return create_app()
