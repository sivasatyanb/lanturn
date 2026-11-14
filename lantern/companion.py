"""Walking companion: server side of the live avatar call.

How the call is put together (Synthesia Interactive Avatar API over LiveKit):

  browser  --POST /api/companion/session-->  this module  (mints a short-lived room token)
  browser  --joins the LiveKit room with that token, microphone on, camera off
  LiveKit  --dispatches the "lantern-companion" agent worker (companion/agent.py) to the room
  worker   --speech-to-text -> Claude -> text-to-speech, and asks Synthesia to render the avatar,
             which joins the same room and publishes video + audio

Secrets stay here and in the worker: LIVEKIT_API_SECRET signs tokens, SYNTHESIA_API_KEY and
ANTHROPIC_API_KEY are read only by the worker. The browser receives the room URL and a token
that can join one room for fifteen minutes, nothing else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from typing import Any

from .config import VAR_DIR
from .env import load_env

AGENT_NAME = "lantern-companion"
TOKEN_TTL_S = 15 * 60
WORKER_BEAT = VAR_DIR / "companion_worker.json"     # written by companion/agent.py while it runs
WORKER_FRESH_S = 45
REQUIRED = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SYNTHESIA_API_KEY",
            "ANTHROPIC_API_KEY")

_sessions: dict[str, dict[str, Any]] = {}


class CompanionError(Exception):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _worker_beat() -> dict | None:
    """The running worker's heartbeat, or None if there is no fresh one."""
    try:
        beat = json.loads(WORKER_BEAT.read_text(encoding="utf-8"))
        return beat if time.time() - float(beat.get("at", 0)) < WORKER_FRESH_S else None
    except (OSError, ValueError):
        return None


def worker_running() -> bool:
    return _worker_beat() is not None


def avatar_expected() -> bool:
    """False only when a running worker says it is in voice-only mode (COMPANION_AVATAR=off)."""
    beat = _worker_beat()
    return True if beat is None else bool(beat.get("avatar", True))


def missing_config() -> list[str]:
    """Names (never values) of required settings that are not set. The Synthesia key is not
    required while the running worker is deliberately voice-only."""
    load_env()
    needed = [k for k in REQUIRED if k != "SYNTHESIA_API_KEY" or avatar_expected()]
    return [k for k in needed if not (os.environ.get(k) or "").strip()]


def public_status() -> dict:
    return {"configured": not missing_config(), "worker": worker_running()}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt(claims: dict, secret: str) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def _clean(value: Any, limit: int) -> str:
    """Plain single-line text of bounded length (this ends up in the companion's instructions)."""
    text = re.sub(r"[\x00-\x1f\x7f<>{}\[\]`]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def route_context(body: dict) -> dict:
    """The directions Lantern computed for this trip, in the bounded form the worker is given."""
    steps = body.get("steps") if isinstance(body.get("steps"), list) else []
    return {
        "from": _clean(body.get("from"), 60),
        "to": _clean(body.get("to"), 60),
        "route": _clean(body.get("route"), 20),
        "minutes": _clean(body.get("minutes"), 4),
        "distance": _clean(body.get("distance"), 12),
        "eta": _clean(body.get("eta"), 8),
        "first": _clean(body.get("first"), 140),
        "steps": [_clean(s, 120) for s in steps[:14]],
    }


def user_token(room: str, identity: str, metadata: str) -> str:
    key, secret = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
    now = int(time.time())
    return _jwt({
        "iss": key, "sub": identity, "name": "Walker", "nbf": now - 5, "exp": now + TOKEN_TTL_S,
        "video": {"roomJoin": True, "room": room, "canPublish": True, "canSubscribe": True,
                  "canPublishData": True},
        # Ask LiveKit to send our agent worker to this room, with the trip as its context.
        "roomConfig": {"agents": [{"agentName": AGENT_NAME, "metadata": metadata}],
                       "syncStreams": True},
    }, secret)


def start_session(body: dict) -> dict:
    missing = missing_config()
    if missing:
        raise CompanionError("not_configured",
                             "The companion is not set up on this server yet.", 503)
    if not worker_running():
        raise CompanionError("worker_offline",
                             "The companion service is not running. Try again in a moment.", 503)
    sid = uuid.uuid4().hex[:12]
    room = f"lantern-walk-{sid}"
    metadata = json.dumps(route_context(body), separators=(",", ":"))
    token = user_token(room, f"walker-{sid}", metadata)
    _sessions[sid] = {"room": room, "started": time.time()}
    return {"session_id": sid, "url": os.environ["LIVEKIT_URL"], "token": token, "room": room}


def end_session(session_id: str | None) -> None:
    """Close the room so the avatar and the agent leave even if the browser vanished."""
    sess = _sessions.pop(session_id or "", None)
    if not sess:
        return
    try:
        import requests

        key, secret = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
        now = int(time.time())
        admin = _jwt({"iss": key, "sub": "lantern-server", "nbf": now - 5, "exp": now + 60,
                      "video": {"roomCreate": True, "roomAdmin": True, "room": sess["room"]}}, secret)
        host = re.sub(r"^ws", "http", os.environ["LIVEKIT_URL"]).rstrip("/")
        requests.post(f"{host}/twirp/livekit.RoomService/DeleteRoom", json={"room": sess["room"]},
                      headers={"Authorization": f"Bearer {admin}"}, timeout=8)
    except Exception:  # noqa: BLE001 - best effort; the room also closes when everyone leaves
        pass
