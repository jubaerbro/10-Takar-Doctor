"""Small routing layer: login checks, request validation and the WebSocket event hub."""
from __future__ import annotations

import json
from typing import Callable

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.websockets import WebSocket

from . import core
from .core import ApiError

ROUTES: list[Route] = []


def authenticate(request: Request, roles: tuple[str, ...], admin_roles: tuple[str, ...] = ()) -> dict:
    """Return the logged-in user (or admin) as a dict, or raise 401/403."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError(401, "not_logged_in", "Please log in.")
    return principal_from_token(header[7:].strip(), roles, admin_roles)


def principal_from_token(token: str, roles: tuple[str, ...], admin_roles: tuple[str, ...] = ()) -> dict:
    claims = core.read_token(token)
    role = claims.get("role")
    if role not in roles:
        raise ApiError(403, "forbidden", "This account cannot do that.")
    if role == "admin":
        row = core.one("SELECT * FROM admins WHERE id = ?", claims["sub"])
        if not row or row["status"] != "active":
            raise ApiError(401, "invalid_token", "Please log in again.")
        if admin_roles and row["admin_role"] != "super_admin" and row["admin_role"] not in admin_roles:
            raise ApiError(403, "forbidden", "Your admin role cannot do that.")
        return {**dict(row), "role": "admin"}
    row = core.one("SELECT * FROM users WHERE id = ?", claims["sub"])
    if not row:
        raise ApiError(401, "invalid_token", "Please log in again.")
    if row["status"] != "active":
        raise ApiError(403, "suspended", "This account is suspended. Contact support.")
    return dict(row)


def route(path: str, methods: list[str], roles: tuple[str, ...] = (), admin_roles: tuple[str, ...] = (),
          body: type[BaseModel] | None = None):
    """Register an endpoint. The function receives (request, user, data) and returns a dict or a Response."""

    def decorator(fn: Callable):
        async def endpoint(request: Request):
            user = authenticate(request, roles, admin_roles) if roles else None
            data = None
            if body is not None:
                try:
                    data = body.model_validate(json.loads(await request.body() or b"{}"))
                except json.JSONDecodeError:
                    raise ApiError(400, "bad_json", "The request body is not valid JSON.")
                except ValidationError as e:
                    first = e.errors()[0]
                    field = ".".join(str(p) for p in first["loc"])
                    raise ApiError(422, "invalid_input", f"{field}: {first['msg']}")
            result = await fn(request, user, data)
            return result if isinstance(result, Response) else JSONResponse(result)

        ROUTES.append(Route(path, endpoint, methods=methods))
        return fn

    return decorator


async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


# --------------------------------------------------------------------------- live events


class Hub:
    """Keeps open WebSocket connections per user and pushes events to them."""

    def __init__(self):
        self.connections: dict[str, set[WebSocket]] = {}

    def add(self, user_id: str, ws: WebSocket) -> None:
        self.connections.setdefault(user_id, set()).add(ws)

    def remove(self, user_id: str, ws: WebSocket) -> None:
        self.connections.get(user_id, set()).discard(ws)

    async def send(self, user_id: str, event: str, data: dict) -> None:
        for ws in list(self.connections.get(user_id, ())):
            try:
                await ws.send_json({"event": event, "data": data})
            except Exception:
                self.remove(user_id, ws)

    async def send_all(self, events: list[tuple[str, str, dict]]) -> None:
        for user_id, event, data in events:
            await self.send(user_id, event, data)


hub = Hub()
