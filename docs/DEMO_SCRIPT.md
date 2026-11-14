# Two-minute demo video script

Format the organisers ask for: screen recording with voice-over, no intro, no music, one real run from start to
finish, say what is automated and what is mocked, label sped-up sections. Hard cut at 2:00.

## Before recording

1. `python scripts/databricks_deploy.py run --wait` so the published overlay is a clean live version with no entries.
2. Start the app (`python -m uvicorn lantern.api:app --port 8000`) and open `http://localhost:8000/?t=2230`
   in a 390 px wide browser window (Chrome DevTools device toolbar, iPhone 14).
3. Open the Databricks job page (Jobs & Pipelines > `lantern-agent`) in a second window, and a SQL editor tab with
   `SELECT event_id, decision, evidence, reason FROM workspace.lantern.agent_decisions ORDER BY decided_at DESC LIMIT 12`.
4. Have `python scripts/databricks_deploy.py status` output on screen once, for the run count.
5. Open the Figma file (link in `docs/DESIGN.md`) on the "Route comparison flow" page.

## Script

| Time | On screen | Voice-over |
|---|---|---|
| 0:00-0:15 | App, 22:30, Imperial to Wilson House. Two route cards. | "It's half past ten and a first-year is walking from Imperial's library to Wilson House in Paddington. Map apps give her the fastest way, which cuts through unlit paths in Hyde Park, and none of them know if a pavement on the way closed an hour ago. Lantern is for her." |
| 0:15-0:35 | Tap **Fastest**, then **Better lit**. Point at the lighting bars and the sentence under the cards. | "Lantern shows two routes side by side. Fastest: 30 minutes, 70% on streets recorded as lit. Better lit: one minute longer, 97% lit, and it stays off paths away from any road. Purple is recorded unlit, grey is not recorded, so she can see what we don't know. Kensington Gardens is shut at this hour, so neither route uses it." |
| 0:35-0:55 | Databricks job page: scheduled runs every 30 min. Open the latest run's decisions table. | "Routes go stale, so an agent keeps them current. This Databricks job runs every 30 minutes with nobody watching. This run is live: it pulled 113 Transport for London disruption records, found three in our area, and Claude judged all three to be lane closures that don't affect anyone on foot. Nothing published. That's the right answer most of the time." |
| 0:55-1:25 | Trigger the replay run (`databricks_deploy.py run --replay 1`). Speed this section up and caption it **"4x - a real run takes about 90 seconds"**. Then the decisions table. | "To show a change on demand we replay the captured feed plus eight synthetic records, labelled as synthetic everywhere. Same job, same checks. Claude reads each record and must quote the sentence it relies on. Code then checks the quote really is in the source, matches the street to our walking network, and checks it against TfL's coordinates. A road closed to cars: ignored. 'Pedestrians should take care': held, not explicit. Text says Bayswater Road but the coordinates are 800 metres away: rejected. A burst main closing both footways on Exhibition Road: accepted. Before publishing, the job reruns sixteen baseline journeys. All pass, so it writes version three to a Delta table." |
| 1:25-1:40 | Back to the app: toast "Agent published update", red banner, route redraws; tap **Why?**. | "The app picks it up on its own. Exhibition Road is closed, both routes now go round by Kensington Gore, three minutes longer, with the old route dotted. 'Why' shows the quoted evidence and that it's replay data. When the record leaves the feed, the agent withdraws the closure and the route goes back." |
| 1:40-1:52 | Architecture diagram from the README, then the Databricks tables, then the Figma file (Components page and the three screens). | "Under it: an OpenStreetMap walking graph with an exposure-time cost model we can explain weight by weight. Claude Opus 5.5 does the reading; deterministic code does the verifying. Databricks runs the schedule, stores every record, decision and overlay version in Delta, and serves the current version to the app. The interface is designed in Figma as variables and components, and the app's colours, spacing and type are generated from that file." |
| 1:52-2:00 | Run-history table / status output. | "The closest product is the main-roads walking option in Citymapper. Ours explains the trade-off, shows what it doesn't know, and updates itself. So far every scheduled run has finished, and the judgment step matched our labels on all 121 test records." |

## Numbers to re-check on the morning (they change)

- Route times and percentages at 22:30: read them off the app (`/api/route?when=...T22:30:00`).
- Overlay version numbers: whatever the job publishes.
- "Every scheduled run has finished": `python scripts/databricks_deploy.py status` prints the counts. Say the real
  figure, for example "31 of 31 scheduled runs".
- Citymapper comparison: confirm the feature still exists before saying it.

## What is automated, manual or mocked (say this if asked)

- Automated: feed ingestion, validation, Claude assessment, evidence and location checks, baseline-journey gate,
  publication, expiry and withdrawal, and the app picking up new versions.
- Manual in the demo: pressing run on the replay (the schedule would also do it, within 30 minutes).
- Mocked: the eight `REPLAY-*` records are synthetic. The live feed had no pedestrian-relevant event in our area
  during the hackathon, which is itself what the live run shows.

## Likely questions and short answers

- **Is "Better lit" safer?** We don't claim that. It minimises time exposed to observed conditions (unlit, off-street,
  enclosed, quiet) at weights we state. Nothing is fitted to crime outcomes; `docs/MODEL.md` says why.
- **Where do the weights come from?** They are stated trade-off ratios (one second unlit counts as two). The
  sensitivity analysis halves and doubles each one: 95% of journeys keep the same corridor.
- **What if Claude is wrong?** A wrong "closed" needs a verbatim quote about pedestrians, a street that exists, text
  that agrees with TfL's coordinates, and sixteen journeys that still work. A wrong "ignore" costs a missed
  closure, which is the state every other map app is already in.
- **What if the job fails?** The last good version stays published, entries expire on their own `valid_to`, and the
  app warns when the agent has not checked in for two hours.
- **Was it really designed in Figma?** Prototyped in code first, then systematised and refined in Figma through
  Figma's connector: native variables, components and screens, not pasted screenshots. Figma is now the source
  of truth for the tokens; `docs/DESIGN.md` lists the decisions made there.
- **Why Databricks?** Schedule, storage, audit trail and serving are all there: bronze and silver tables for the
  feed, a decisions table, immutable overlay versions, and a volume the app reads. The app has no other source of
  disruption data.
