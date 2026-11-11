"""LiveKit video rooms: join tokens for the two people on a call, and closing the room when the call ends.

The apps never see the API secret. They receive a short-lived token that lets one person into one room.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

import jwt

from . import core

log = logging.getLogger("tentaka.video")


def configured() -> bool:
    cfg = core.config
    return bool(cfg.livekit_url and cfg.livekit_api_key and cfg.livekit_api_secret)


def join_credentials(room: str, identity: str, valid_until: float) -> dict | None:
    """What an app needs to join: the server address, the room and a token that works for this call only.

    Room and identity are random ids, never names or phone numbers.
    """
    if not configured():
        return None
    cfg = core.config
    claims = {
        "iss": cfg.livekit_api_key, "sub": identity, "nbf": int(time.time()) - 10, "exp": int(valid_until),
        "video": {"room": room, "roomJoin": True, "canPublish": True, "canSubscribe": True,
                  "canPublishSources": ["camera", "microphone"]},   # no screen sharing
    }
    return {"url": cfg.livekit_url, "room": room, "token": jwt.encode(claims, cfg.livekit_api_secret, algorithm="HS256")}


def _api_base() -> str:
    url = core.config.livekit_url.rstrip("/")
    return url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)


def delete_room_request(room: str) -> urllib.request.Request:
    cfg = core.config
    token = jwt.encode({"iss": cfg.livekit_api_key, "sub": "backend", "nbf": int(time.time()) - 10, "exp": int(time.time()) + 60,
                        "video": {"roomCreate": True}}, cfg.livekit_api_secret, algorithm="HS256")
    return urllib.request.Request(
        _api_base() + "/twirp/livekit.RoomService/DeleteRoom", data=json.dumps({"room": room}).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})


def _delete_room(room: str) -> None:
    try:
        with urllib.request.urlopen(delete_room_request(room), timeout=10):
            pass
    except Exception as e:   # the call has already been ended and billed in our records; this only cuts the media
        log.warning("Could not close video room %s: %s", room, e)


def close_room(room: str) -> None:
    """Disconnect everyone once a call has ended, so nobody can keep talking past the time limit.

    Runs in the background: a slow or failed request to LiveKit must never delay ending or billing the call.
    """
    if configured():
        threading.Thread(target=_delete_room, args=(room,), daemon=True).start()
