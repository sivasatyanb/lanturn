# Devpost submission draft

Fill the bracketed items, then paste. Submission closes **Sunday 15 November 2026, 12:00 GMT**; nothing can be
changed after that and the last commit must be before 12:00.

## Required fields

| Field | Value |
|---|---|
| Project name | Lantern |
| Tagline | Night walking routes for students that explain their trade-offs and update themselves. |
| Track | Autonomous Agents |
| Bounties entered | Databricks (track bounty). Figma, Best-Designed AI Product (open to all tracks; does not count towards the two). |
| GitHub repository | [public repo URL] |
| Demo video (max 2:00) | [video URL] |
| Figma file or Figma Make link | https://www.figma.com/design/1GlEqbaZG7xphZQwJ98Tic (set sharing to "anyone with the link can view" first) |
| Team members | [each person's full name, Luma signup email and university email, and add each to the Devpost project] |

## Inspiration

Walking back from the library at 22:30, a map app gives you one answer: the fastest route. From Imperial to our
halls in Paddington that route cuts through unlit paths in Hyde Park. The advice everyone gives, "stick to busy,
well-lit streets", has no button. And no route planner knows that a pavement on your way was closed an hour ago.

## What it does

Lantern shows two walking routes side by side: the fastest, and a "better lit" one that accepts a little extra
time to stay on streets recorded as lit, close to other streets and out of underpasses. For Imperial to Wilson
House at 22:30 that is 31 minutes instead of 30, with 97% of the route on lit streets instead of 70%. It states
the cost of the trade, colours the route by what is known about lighting, and shows the share it knows nothing
about. It never claims a route is safe.

An autonomous agent keeps the routes current. Every 30 minutes a Databricks job fetches Transport for London's
road disruption feed, and for each record in our area Claude decides what it means for someone on foot. Most
records are lane closures and mean nothing. When one does matter, the agent must quote the sentence it relies
on; code then checks the quote exists in the source, matches the street to the walking network, checks it
against TfL's coordinates, and reruns sixteen baseline journeys. Only then is a new versioned routing overlay
published, and the app redraws the route and shows the evidence. Stale records expire, withdrawn ones are
removed, and a failed run leaves the last good version in place.

## How we built it

- **Walking graph:** 20,831 segments of OpenStreetMap (South Kensington to Paddington), with lighting tags, Royal
  Parks opening hours as time-dependent closures, and routes computed on demand with Dijkstra.
- **Cost model:** walking time plus weighted time exposed to four observed conditions (unlit, off-street,
  enclosed, quiet), all in seconds, with weights we can explain one by one. Unrecorded lighting is treated as
  unknown, estimated per street type from recorded segments, and reported on every route.
- **Agent:** Claude Opus 5.5 with structured output for the judgment; deterministic Python for evidence checking,
  location matching, conflict resolution, the baseline-journey gate and publication.
- **Databricks:** a scheduled serverless notebook job; Delta tables for the raw feed (bronze), validated records
  (silver), every decision with its evidence, every run, and immutable overlay versions; a Unity Catalog volume
  that serves the current version to the app; the Claude key in a secret scope.
- **App and design:** a mobile web app (FastAPI + MapLibre GL, installable PWA). The route comparison flow is
  designed in Figma as 35 variables, 12 text styles, 12 components and three screens, using the real route
  geometry as editable vectors. The app's colours, radii, spacing and type are generated from the Figma
  variables, so the file is the source of truth for the look (`docs/DESIGN.md`).

## Challenges we ran into

- There is no open street-light dataset for Westminster or Kensington & Chelsea, and no open pedestrian counts
  for London streets. We had to build a model that is honest about missing data instead of one that hides it.
- We wanted a fitted risk model and talked ourselves out of it: crime and casualty counts without a pedestrian
  exposure denominator mostly measure how busy a street is, and would send people down empty alleys.
- TfL's feed is written for drivers. "Road closed" tells you nothing about the pavement, and a footway closed on
  one side should not sever a street.
- The live feed had no pedestrian-relevant event in our area during the hackathon, so we built a labelled replay
  that goes through exactly the same job.

## Accomplishments we're proud of

- The agent's default answer is "no change", and it can explain every rejection.
- On 113 real TfL records plus 8 synthetic ones, the judgment step matched our labels on all 121, and all 11
  quotes it offered were verbatim. (Small sample, our own labels; details in the repo.)
- [N of N] scheduled Databricks runs completed overnight. [fill from `databricks_deploy.py status`]
- Halving or doubling any weight keeps 95% of journeys in the same corridor.

## What we learned

A transparent score with visible unknowns is easier to defend than a fitted model with an invisible denominator
problem. And an agent is more convincing when the model does the reading and plain code does the checking.

## What's next

Let people set their own trade-off; add boroughs that publish lamp-column data; add borough roadworks feeds; and,
if night-time footfall estimates become available, fit the casualty model we describe in `docs/MODEL.md`.

## Built with

python, fastapi, maplibre-gl, openstreetmap, databricks, delta-lake, unity-catalog, claude, anthropic-api,
tfl-unified-api, figma
