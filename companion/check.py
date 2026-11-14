"""Check the walking companion's dependencies one by one, without a browser or a microphone.

    .venv-agent/Scripts/python companion/check.py

Reports, for each piece, whether it works with the credentials in .env. Values are never
printed. The avatar itself (Synthesia) can only be proven by a real call; this only checks
that its key is present.

  1. LiveKit project       LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET  (list rooms)
  2. Voice (text-to-speech) LiveKit Inference, same LiveKit keys
  3. Speech recognition     LiveKit Inference, same LiveKit keys (transcribes the audio from 2)
  4. Conversation           Claude, ANTHROPIC_API_KEY
  5. Avatar                 Synthesia, SYNTHESIA_API_KEY (presence only)
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiohttp  # noqa: E402
from livekit import api  # noqa: E402
from livekit.agents import inference, llm as lk_llm, stt as lk_stt  # noqa: E402
from livekit.plugins import anthropic  # noqa: E402

import agent as worker  # noqa: E402  (model and voice settings; does not start the worker)

PHRASE = "Head north on Exhibition Road, then continue onto West Carriage Drive."


def short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:160]}"


async def check_livekit() -> str:
    lk = api.LiveKitAPI()
    try:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest())
        return f"ok ({len(rooms.rooms)} open rooms)"
    finally:
        await lk.aclose()


async def check_tts(session: aiohttp.ClientSession) -> tuple[str, list]:
    tts = inference.TTS(model=worker.TTS_MODEL, voice=worker.VOICE_ID, http_session=session)
    frames, t0, first = [], time.time(), None
    async for chunk in tts.synthesize(PHRASE):
        if first is None:
            first = time.time() - t0
        frames.append(chunk.frame)
    secs = sum(f.samples_per_channel / f.sample_rate for f in frames)
    return f"ok ({secs:.1f} s of audio, first audio after {first:.2f} s)", frames


async def check_stt(session: aiohttp.ClientSession, frames: list) -> str:
    stt = inference.STT(model=worker.STT_MODEL, language="en", http_session=session)
    stream = stt.stream()

    async def feed() -> None:
        for f in frames:                       # paced like live speech; recognisers expect a stream
            stream.push_frame(f)
            await asyncio.sleep(f.samples_per_channel / f.sample_rate * 0.5)
        await asyncio.sleep(2.5)               # trailing silence lets the recogniser finish the turn
        stream.end_input()

    task = asyncio.create_task(feed())
    final, interim = [], ""
    async for ev in stream:
        if not ev.alternatives:
            continue
        if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT:
            final.append(ev.alternatives[0].text)
        elif ev.type == lk_stt.SpeechEventType.INTERIM_TRANSCRIPT:
            interim = ev.alternatives[0].text
    await task
    await stream.aclose()
    heard = " ".join(final).strip() or interim.strip()
    return f"ok ({worker.STT_MODEL} heard: {heard!r})" if heard else "no transcript returned"


async def check_llm() -> str:
    model = anthropic.LLM(model=worker.LLM_MODEL, max_tokens=2000)
    ctx = lk_llm.ChatContext()
    ctx.add_message(role="system", content=worker.build_instructions(
        {"to": "Wilson House", "first": "Head north on Exhibition Road"}))
    ctx.add_message(role="user", content="Hi! Which way do I go first?")
    t0, first, out = time.time(), None, []
    stream = model.chat(chat_ctx=ctx)
    async for chunk in stream:
        if chunk.delta and chunk.delta.content:
            if first is None:
                first = time.time() - t0
            out.append(chunk.delta.content)
    await stream.aclose()
    reply = "".join(out).strip()
    return f"ok ({worker.LLM_MODEL}, first words after {first:.1f} s, {time.time() - t0:.1f} s total): {reply[:140]!r}"


async def main() -> int:
    results: dict[str, str] = {}
    frames: list = []
    try:
        results["1 LiveKit project"] = await asyncio.wait_for(check_livekit(), 30)
    except Exception as exc:  # noqa: BLE001
        results["1 LiveKit project"] = "FAILED " + short(exc)
    async with aiohttp.ClientSession() as session:
        try:
            results["2 Voice (LiveKit Inference TTS)"], frames = await asyncio.wait_for(check_tts(session), 60)
        except Exception as exc:  # noqa: BLE001
            results["2 Voice (LiveKit Inference TTS)"] = "FAILED " + short(exc)
        if frames:
            try:
                results["3 Speech recognition (LiveKit Inference STT)"] = await asyncio.wait_for(check_stt(session, frames), 60)
            except Exception as exc:  # noqa: BLE001
                results["3 Speech recognition (LiveKit Inference STT)"] = "FAILED " + short(exc)
        else:
            results["3 Speech recognition (LiveKit Inference STT)"] = "skipped (no audio to transcribe)"
    try:
        results["4 Conversation (Claude)"] = await asyncio.wait_for(check_llm(), 90)
    except Exception as exc:  # noqa: BLE001
        results["4 Conversation (Claude)"] = "FAILED " + short(exc)
    results["5 Avatar (Synthesia key)"] = "present (prove with a real call)" if os.environ.get(
        "SYNTHESIA_API_KEY", "").strip() else "MISSING: set SYNTHESIA_API_KEY in .env"
    for name, res in results.items():
        print(f"{name:<46} {res}")
    return 0 if all(not r.startswith(("FAILED", "MISSING")) for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
