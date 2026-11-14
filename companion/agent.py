"""Lantern walking companion: LiveKit agent worker with a Synthesia interactive avatar.

Run (from the repo root, in the agent virtualenv; see companion/README.md):

    .venv-agent/Scripts/python companion/agent.py dev

What each part does:
  - LiveKit Inference: speech recognition and the companion's voice (uses the LiveKit keys,
    no extra provider keys).
  - Claude (ANTHROPIC_API_KEY): the conversation.
  - Synthesia (SYNTHESIA_API_KEY): renders the avatar from the companion's voice and joins
    the room as a participant publishing video + audio.

The worker is dispatched by name to rooms created by the app (lantern/companion.py). The trip
Lantern computed arrives as dispatch metadata; the companion may only repeat those directions.
All keys are read from the git-ignored .env and never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

from livekit.agents import Agent, AgentSession, JobContext, JobProcess, WorkerOptions, cli, inference  # noqa: E402
from livekit.plugins import anthropic, silero, synthesia  # noqa: E402

AGENT_NAME = "lantern-companion"                       # must match lantern/companion.py
# Synthesia avatar and voice. Defaults are the ones used in Synthesia's own quickstart.
AVATAR_ID = os.environ.get("SYNTHESIA_AVATAR_ID", "8788bef1-8020-46e0-a8f4-510ea9989b25")
VOICE_ID = os.environ.get("COMPANION_VOICE_ID", "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4")
LLM_MODEL = os.environ.get("COMPANION_LLM_MODEL", "claude-opus-5-5")
STT_MODEL = os.environ.get("COMPANION_STT_MODEL", "deepgram/nova-3")
TTS_MODEL = os.environ.get("COMPANION_TTS_MODEL", "cartesia/sonic-3.6")
BEAT = Path(os.environ.get("LANTERN_VAR_DIR", ROOT / "var")) / "companion_worker.json"

log = logging.getLogger("lantern.companion")

PERSONA = """You are the Lantern walking companion: a warm, calm voice keeping a student company \
on a walk home at night in London. You are on a live video call with them while they walk.

How to talk:
- Spoken English, British spelling, one or two short sentences at a time. No lists, no markdown.
- Be good company: chat, ask an easy question now and then, follow their lead.

Directions:
- The Lantern app shows the route on screen. You are company, not the navigator.
- If they ask about the route, answer only from the DIRECTIONS FROM LANTERN below, in your own \
words. If the answer is not there, say the screen has the detail. Never invent streets, turns, \
times, closures or lighting.
- Never say a route or a street is safe or unsafe. Lantern describes street conditions, not risk.
- If they say they are in danger or being followed, tell them to call 999 straight away.
"""


def build_instructions(trip: dict) -> str:
    lines = []
    if trip.get("from") or trip.get("to"):
        lines.append(f"Trip: {trip.get('from') or 'start'} to {trip.get('to') or 'destination'}.")
    if trip.get("route"):
        lines.append(f"Chosen route: {trip['route']}.")
    facts = [f"{trip['minutes']} minutes" if trip.get("minutes") else "",
             trip.get("distance") or "", f"arriving about {trip['eta']}" if trip.get("eta") else ""]
    facts = [f for f in facts if f]
    if facts:
        lines.append("Walk: " + ", ".join(facts) + ".")
    if trip.get("first"):
        lines.append(f"First instruction: {trip['first']}.")
    for i, step in enumerate(trip.get("steps") or [], 1):
        lines.append(f"Step {i}: {step}")
    body = "\n".join(lines) if lines else "(No directions were supplied for this call.)"
    return f"{PERSONA}\nDIRECTIONS FROM LANTERN\n{body}\n"


def greeting(trip: dict) -> str:
    to = trip.get("to")
    hello = f"Hi, I'm here. I'll keep you company on the way to {to}." if to else \
        "Hi, I'm here. I'll keep you company on your walk."
    first = trip.get("first")
    return f"{hello} To start: {first}." if first else hello


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    try:
        trip = json.loads(ctx.job.metadata or "{}")
        if not isinstance(trip, dict):
            trip = {}
    except ValueError:
        trip = {}

    session = AgentSession(
        stt=inference.STT(model=STT_MODEL, language="en"),
        llm=anthropic.LLM(model=LLM_MODEL, max_tokens=2000),
        tts=inference.TTS(model=TTS_MODEL, voice=VOICE_ID),
        vad=ctx.proc.userdata["vad"],
    )
    # The avatar must be attached before the session starts, or it never appears.
    avatar = synthesia.AvatarSession(synthesia.AvatarConfig(avatar_ids=[AVATAR_ID]), join_timeout=60.0)
    for attempt in range(3):
        try:
            await avatar.start(session, room=ctx.room)
            break
        except synthesia.SynthesiaError as exc:
            log.warning("avatar start failed (attempt %d): %s", attempt + 1, type(exc).__name__)
            if not getattr(exc, "retryable", False) or attempt == 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))

    await session.start(agent=Agent(instructions=build_instructions(trip)), room=ctx.room)
    # The greeting is Lantern's own text, spoken as written.
    await session.say(greeting(trip), allow_interruptions=False)


def _heartbeat() -> None:
    """Let the app tell 'worker not running' apart from 'call failed'."""
    BEAT.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            tmp = BEAT.with_suffix(".tmp")
            tmp.write_text(json.dumps({"at": time.time(), "pid": os.getpid(), "agent": AGENT_NAME}),
                           encoding="utf-8")
            os.replace(tmp, BEAT)
        except OSError:
            pass
        time.sleep(15)


if __name__ == "__main__":
    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
                           "SYNTHESIA_API_KEY", "ANTHROPIC_API_KEY") if not os.environ.get(k, "").strip()]
    if missing:
        raise SystemExit("Missing in .env (names only): " + ", ".join(missing))
    threading.Thread(target=_heartbeat, daemon=True).start()
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm, agent_name=AGENT_NAME))
