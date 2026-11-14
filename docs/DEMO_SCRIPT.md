# Two-minute demo video script (V2)

Format the organisers ask for: screen recording with voice-over, no intro, no music, one real run from start to
finish, say what is automated and what is mocked, label sped-up sections. Hard cut at 2:00.

Recorded in desktop Chrome with the device toolbar at 390 x 844. The story stays centred on the agent:
live TfL feed, Databricks, verified routing update. The walking companion is a separate feature shown at the end.

## Set up (10 minutes before recording)

1. **Terminal 1, the app:** `.venv/Scripts/python -m uvicorn lantern.api:app --port 8000`
2. **Terminal 2, the companion worker:** `.venv-agent/Scripts/python companion/agent.py dev`
   First run `.venv-agent/Scripts/python companion/check.py`; all five lines must say ok or present.
3. **Reset the replay** so the demo starts without the closure:
   `.venv/Scripts/python scripts/databricks_deploy.py run --replay 2 --wait`
   (this only touches the demo job `lantern-agent-demo`; the live job is never involved).
4. **Chrome:** open DevTools, device toolbar, 390 x 844, and two tabs:
   - Tab A, normal planning: `http://localhost:8000/?t=2230`
   - Tab B, demo mode: `http://localhost:8000/?demo=1&t=2230`
5. **Microphone:** in Tab A press Start!, tap the video-call button once and allow the microphone for
   `localhost`, check you see and hear the avatar, hang up, Exit navigation. The permission prompt will not
   appear again during the recording.
6. **Databricks:** one window on Jobs & Pipelines showing `lantern-agent` (schedule: every 30 minutes) and
   `lantern-agent-demo`, and a SQL editor tab with
   `SELECT event_id, decision, evidence, reason FROM workspace.lantern_demo.agent_decisions ORDER BY decided_at DESC LIMIT 12`.
7. **Figma:** the file from `docs/DESIGN.md`, on the screens page.

If you record more than 20 hours after a replay run, run step 3 and the replay again: the synthetic closure is
valid for 20 hours from the run.

## Recording sequence

| Time | Do exactly this | Say |
|---|---|---|
| 0:00-0:18 | **Tab A.** Leave at 22:30, Imperial to Wilson House. Tap **Fastest**, then **Better lit**. Point at the three legend entries. | "Half past ten, and a first-year is walking from Imperial to her hall in Paddington. Lantern compares two routes. Fastest: 30 minutes, 70% on streets recorded as lit. Better lit: one minute longer, 97%. Yellow is lit, solid grey is recorded unlit, dashed grey is unknown, so she can see what we don't know." |
| 0:18-0:33 | Tap **Arrive by**, set **00:30**, **Set arrival**. Point at "tomorrow, Sun 15 Nov", the leave time and the hint. | "Now she wants to be home by half past midnight. By then both parks are shut, so the walk is 51 minutes and she must leave at 23:38. Lantern checks that against the router and tells her the better plan: leave by 23:30, while Hyde Park is open, and it is 30 minutes." |
| 0:33-0:48 | Tap **Leave at** back to 22:30 (or the hint's **Use**). Tap the status line "TfL checked ... no closures". Scroll the LIVE decisions. Switch to the Databricks job page showing the 30-minute schedule. | "Routes go stale, so an agent keeps them current. Every 30 minutes this Databricks job pulls Transport for London's disruption feed and Claude decides what each record means on foot. This is the live run: lane closures, no effect on pedestrians, nothing published. That is the right answer most of the time." |
| 0:48-1:13 | **Tab B (demo mode).** Tap the status line, press **Replay: burst main**. Speed this up and caption it **"4x: a real run takes about 90 seconds"**. Toast appears, routes redraw with the closure marker. Tap the status line: evidence with the SYNTHETIC tag. Flash the SQL decisions table. | "To show a change on demand we replay the captured feed plus eight synthetic records, labelled synthetic everywhere and published on a separate Databricks path so they can never reach real users. Same agent, same checks. Claude must quote the sentence it relies on; code checks the quote is in the source, matches the street to our walking network and TfL's coordinates, and reruns sixteen baseline journeys. A road closed to cars: ignored. 'Pedestrians should take care': held. A burst main closing both footways on Exhibition Road: accepted, and the route goes round it, three minutes longer." |
| 1:13-1:25 | Still Tab B. Press **Start!**. | "Start. The map turns to face the way she walks. The first instruction comes from the real route and street names, and it already avoids the closure: in 160 metres, turn left onto Prince Consort Road." |
| 1:25-1:48 | Tap the **video-call** button (top right). Wait for the avatar and its greeting. Ask aloud: **"Which way do I go first?"** Let it answer. Tap **hang up**. Tap **Exit navigation**. | (Let the avatar speak.) Then: "Walking alone at night is easier with company. This is a live Synthesia avatar, with Claude doing the talking, and it only repeats directions Lantern gave it. Hanging up leaves navigation open." |
| 1:48-2:00 | README architecture diagram, then the Figma file (components and screens). | "Under it: an OpenStreetMap walking graph with a cost model we can explain weight by weight; Claude reads, plain code verifies; Databricks schedules, stores every decision and serves the result; and the interface is designed in Figma, with the app's colours and type generated from that file." |

In testing the avatar appeared about three seconds after the tap and began its greeting straight away; the
reply to a question began three to four seconds after the question ended. If the avatar is slower to appear, keep
recording and cut the wait with a caption "avatar connecting, cut". Do not speed up the conversation itself.

## Numbers to re-check on the morning (they change)

- Route times and lit shares at 22:30: read them off the cards.
- Arrive by 00:30: the leave time, duration and the "leave by" hint are computed live; read them off the screen.
- Replay: the extra minutes after the closure (three at the time of writing) and the first instruction in
  navigation.
- If you mention run counts, use `python scripts/databricks_deploy.py status` and say the real figure.

## What is automated, manual or mocked (say this if asked)

- **Automated:** feed ingestion, validation, Claude assessment, evidence and location checks, the
  baseline-journey gate, publication, expiry and withdrawal, the app picking up new versions and applying
  closures to routing, the arrive-by search, the turn instructions.
- **Manual in the demo:** pressing Replay (the live schedule runs by itself every 30 minutes).
- **Mocked:** the eight `REPLAY-*` records are synthetic and live on a separate demo path. The live feed had no
  pedestrian-relevant event in our area during the hackathon, which is what the live run shows.
- **Preview only:** navigation does not track position; time, distance and ETA are fixed at the start.
- **Live:** the companion call (Synthesia avatar, LiveKit, Claude).

## Likely questions and short answers

- **Is "Better lit" safer?** We don't claim that. It minimises time exposed to observed conditions (unlit,
  off-street, enclosed, quiet) at weights we state. Nothing is fitted to crime outcomes; `docs/MODEL.md` says why.
- **Where is the closure warning in the normal app?** A verified closure is applied to routing automatically.
  The status line says "1 closure applied" and opens the evidence. There is no alarm banner in everyday planning.
- **Can replay data leak into real routes?** No. Replay runs on its own Databricks job, schema and volume, and
  the app reads it only in demo mode. A test asserts the live path is untouched by a replay run.
- **How does Arrive by work?** Closures make a route's duration depend on departure time, so it searches for the
  latest departure whose own route still arrives on time and re-checks it against the router. Tests cover park
  closing times and trips across midnight.
- **What if Claude is wrong?** A wrong "closed" needs a verbatim quote about pedestrians, a street that exists,
  text that agrees with TfL's coordinates, and sixteen journeys that still work. A wrong "ignore" costs a missed
  closure, which is the state every other map app is already in.
- **What does the companion know?** Only the trip Lantern sent it: destination, route, time and the listed
  instructions. It is told to use nothing else for directions and never to call a route safe.
- **What if the call fails or the mic is refused?** A short message and Retry in the call window. Navigation
  keeps working.
- **Was it really designed in Figma?** Prototyped in code first, then systematised and refined in Figma through
  Figma's connector: native variables, components and screens. Figma is the source of truth for the tokens,
  including the map colours; `docs/DESIGN.md` lists the decisions made there.
- **Why Databricks?** Schedule, storage, audit trail and serving are all there: bronze and silver tables for the
  feed, a decisions table, immutable overlay versions, and a volume the app reads. The app has no other source
  of disruption data.
