# Walking companion

A live avatar video call inside Lantern's navigation preview. The companion greets the walker, reads out
Lantern's first instruction, then keeps them company. It is a separate feature from the disruption agent.

## How a call is put together

```
browser ── POST /api/companion/session ──> Lantern API      mints a 15-minute token for one new room
browser ── joins the LiveKit room (microphone on, camera off)
LiveKit ── dispatches the "lantern-companion" worker (companion/agent.py) to that room
worker  ── speech recognition -> Claude -> voice, and asks Synthesia to render the avatar,
           which joins the same room and publishes video + audio
```

| Dependency | What it does | Credentials |
|---|---|---|
| LiveKit project | rooms and media transport | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| LiveKit Inference | speech recognition (Deepgram Nova 3) and voice (Cartesia Sonic) | the LiveKit keys; no provider keys |
| Claude (Haiku 5.5) | conversation, via `livekit-plugins-anthropic` | `ANTHROPIC_API_KEY` |
| Synthesia Interactive Avatar API | avatar video, lip-synced to the companion's voice | `SYNTHESIA_API_KEY` |

Synthesia's API only renders the avatar: it has no prompt, greeting or knowledge setting. Context is passed
the LiveKit way. The app sends the trip (destination, route, time, Lantern's instructions) to the API, which
puts a bounded, plain-text copy in the room's agent-dispatch metadata; the worker builds the companion's
instructions from it and speaks Lantern's own greeting text as written.

## Setup

All values go in the git-ignored `.env` at the repo root. Nothing is sent to the browser except the room URL
and a short-lived room token.

1. `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`: from the LiveKit project (Settings > API keys).
2. `SYNTHESIA_API_KEY`: Synthesia Studio > Developers > API keys > New API key, with the scope
   **Interactive Avatars (write)**. A legacy (v2) key does not work for these endpoints.
3. `ANTHROPIC_API_KEY`: already used by the agent.
4. Optional: `SYNTHESIA_AVATAR_ID` (default: the avatar used in Synthesia's quickstart; only synthetic and
   personal avatars can be interactive, copy the "Interactive ID" from the Avatars page),
   `COMPANION_VOICE_ID` (a Cartesia voice id), `COMPANION_LLM_MODEL` (default `claude-haiku-5-5`, chosen
   so spoken replies start quickly; `claude-opus-5-5` also works but starts replying later).

The worker needs its own virtualenv because `livekit-agents` ships x64 wheels only on Windows. On an ARM64
Windows machine use an x64 Python (3.10 to 3.14); elsewhere any supported Python works.

```bash
py -3.14 -m venv .venv-agent                                   # an x64 Python on Windows
.venv-agent/Scripts/python -m pip install -r companion/requirements.txt
```

## Run

```bash
.venv-agent/Scripts/python companion/check.py        # checks each dependency; prints no secrets
.venv-agent/Scripts/python companion/agent.py dev    # terminal 1: the worker (leave it running)
.venv/Scripts/python -m uvicorn lantern.api:app --port 8000     # terminal 2: the app
```

Open the app, press **Start!**, then the video-call button at the top right. Microphone access needs
`http://localhost` or HTTPS. Do not run the worker with `console`: that mode has no room, so no avatar.
Run only one worker at a time: two workers with the same name share incoming calls between them.

## Tests that need no person

```bash
# Spoken round trip without a browser: joins a real room, waits for the greeting, speaks a question
# (synthesised speech as the microphone) and transcribes the companion's spoken answer.
.venv-agent/Scripts/python companion/roundtrip.py

# Browser call lifecycle in headless Chrome with a simulated microphone: connecting, live, hang up
# (navigation stays open, microphone released), exit navigation (call ends, microphone released).
node scripts/call_lifecycle.mjs
```

Both work with the worker in voice-only mode, which needs no Synthesia key:
`COMPANION_AVATAR=off .venv-agent/Scripts/python companion/agent.py dev`. Neither proves the avatar video;
that needs a real call with `SYNTHESIA_API_KEY` set.

## Behaviour and failure handling

| Situation | What the user sees |
|---|---|
| Call button tapped | microphone prompt, then "Connecting…" in a small window at the upper right |
| Microphone refused | "Microphone is blocked. Allow it, then retry." with Retry; navigation is unaffected |
| Keys missing on the server, or worker not running | a short message with Retry |
| Avatar cannot start (plan, quota, outage) or the worker is voice-only | the call continues as "Voice only": the companion can be heard and talked to, with no video |
| Nothing arrives within 75 seconds | "Couldn't reach the companion." with Retry |
| Hang up | the call window closes, the microphone is released, navigation stays open |
| Exit navigation | the call ends and the microphone is released |

The server also deletes the LiveKit room when a call ends, so the avatar and the worker leave even if the
browser tab was closed.

## What the companion may say about the route

Only what Lantern supplied for this trip. Its instructions say: you are company, not the navigator; answer
route questions only from the supplied directions; if the answer is not there, say the screen has it; never
say a street is safe or unsafe; if the walker is in danger, tell them to call 999.
