"""Headless conversational round trip with the companion worker: no browser, no microphone.

    .venv-agent/Scripts/python companion/agent.py dev       # terminal 1 (COMPANION_AVATAR=off for voice only)
    .venv-agent/Scripts/python companion/roundtrip.py       # terminal 2

Joins a real LiveKit room with the same kind of token the app gives the browser (so LiveKit
dispatches the worker exactly as it would for a real call), waits for the companion's greeting,
speaks a question aloud (synthesised speech published as the microphone track), and
transcribes what the companion says back. It proves dispatch, speech recognition, Claude and
voice end to end. It does not prove the avatar: it does not look at video.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiohttp  # noqa: E402
from livekit import rtc  # noqa: E402
from livekit.agents import inference, stt as lk_stt  # noqa: E402

import agent as worker  # noqa: E402
from lantern import companion  # noqa: E402

QUESTION = "Which way do I go first?"
TRIP = {"from": "Imperial College, South Ken", "to": "Wilson House", "route": "Better lit",
        "minutes": 31, "distance": "2.5 km", "eta": "23:01",
        "first": "Head north on Exhibition Road, then in 350 m continue onto West Carriage Drive",
        "steps": ["After 350 m: Continue onto West Carriage Drive", "After 2.5 km: Arrive at your destination"]}
SPEECH_LEVEL = 500          # int16 peak above which a frame counts as speech
RATE = 24000


def loud(frame: rtc.AudioFrame) -> bool:
    data = np.frombuffer(frame.data, dtype=np.int16)
    return data.size > 0 and int(np.abs(data).max()) > SPEECH_LEVEL


async def transcribe(session: aiohttp.ClientSession, frames: list) -> str:
    stt = inference.STT(model=worker.STT_MODEL, language="en", http_session=session)
    stream = stt.stream()

    async def feed() -> None:
        for f in frames:
            stream.push_frame(f)
            await asyncio.sleep(f.samples_per_channel / f.sample_rate * 0.25)
        await asyncio.sleep(2.5)
        stream.end_input()

    task = asyncio.create_task(feed())
    final, interim = [], ""
    async for ev in stream:
        if ev.alternatives and ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT:
            final.append(ev.alternatives[0].text)
        elif ev.alternatives and ev.type == lk_stt.SpeechEventType.INTERIM_TRANSCRIPT:
            interim = ev.alternatives[0].text
    await task
    await stream.aclose()
    return " ".join(final).strip() or interim.strip()


async def main() -> int:
    sid = uuid.uuid4().hex[:10]
    room_name = f"lantern-roundtrip-{sid}"
    token = companion.user_token(room_name, f"tester-{sid}", json.dumps(companion.route_context(TRIP)))
    room = rtc.Room()
    agent_frames: asyncio.Queue = asyncio.Queue()
    seen: dict[str, str] = {}

    async def pump(track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
            agent_frames.put_nowait(ev.frame)

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        seen[participant.identity] = seen.get(participant.identity, "") + f" {track.kind}"
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(pump(track))

    t0 = time.time()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    source = rtc.AudioSource(RATE, 1)
    mic = rtc.LocalAudioTrack.create_audio_track("microphone", source)
    await room.local_participant.publish_track(mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    print(f"joined room {room_name}; waiting for the companion (worker must be running)")

    silence = rtc.AudioFrame(b"\x00\x00" * (RATE // 50), RATE, 1, RATE // 50)       # 20 ms
    speaking = asyncio.Event()

    async def microphone() -> None:      # a real microphone never stops sending frames
        while not speaking.is_set():
            await source.capture_frame(silence)
            await asyncio.sleep(0.02)

    mic_task = asyncio.create_task(microphone())

    async def utterance(limit_s: float) -> tuple[list, float | None]:
        """Agent audio from the first loud frame until 1.5 s of quiet. Returns (frames, start time)."""
        frames, started, quiet, deadline = [], None, 0.0, time.time() + limit_s
        while time.time() < deadline:
            try:
                f = await asyncio.wait_for(agent_frames.get(), 1.0)
            except asyncio.TimeoutError:
                if started:
                    quiet += 1.0
                    if quiet >= 1.5:
                        break
                continue
            if loud(f):
                started = started or time.time()
                quiet = 0.0
            elif started:
                quiet += f.samples_per_channel / f.sample_rate
            if started:
                frames.append(f)
                if quiet >= 1.5:
                    break
        return frames, started

    result = 1
    async with aiohttp.ClientSession() as session:
        greeting, g_start = await utterance(75)
        if not greeting:
            print("FAILED: no speech from the companion within 75 s. Is companion/agent.py running?")
        else:
            print(f"greeting started {g_start - t0:.1f} s after joining")
            # Speak the question as the microphone.
            tts = inference.TTS(model=worker.TTS_MODEL, voice=worker.VOICE_ID, http_session=session)
            q_frames = [c.frame async for c in tts.synthesize(QUESTION)]
            speaking.set()
            await mic_task
            while not agent_frames.empty():
                agent_frames.get_nowait()
            for f in q_frames:
                await source.capture_frame(f)
            asked = time.time()
            speaking.clear()
            mic_task = asyncio.create_task(microphone())
            reply, r_start = await utterance(45)
            heard_greeting = await transcribe(session, greeting)
            print(f"companion greeting: {heard_greeting!r}")
            print(f"asked aloud:        {QUESTION!r}")
            if reply:
                heard_reply = await transcribe(session, reply)
                print(f"companion reply:    {heard_reply!r}")
                print(f"reply began {r_start - asked:.1f} s after the question ended")
                result = 0
            else:
                print("FAILED: the companion did not answer within 45 s")
    print("participants and tracks seen:", {k: v.strip() for k, v in seen.items()})
    speaking.set()
    await mic_task
    await room.disconnect()
    companion._sessions["roundtrip"] = {"room": room_name, "started": t0}
    companion.end_session("roundtrip")            # close the room so the worker job ends
    return result


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
