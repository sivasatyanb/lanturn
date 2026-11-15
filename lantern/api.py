"""HTTP API + static mobile web app.

Run:  python -m uvicorn lantern.api:app --port 8000
The app only reads the published overlay; it never edits routing data itself.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import companion
from .access import LONDON, ZONE_LABELS, zone_status
from .config import BBOX, DEFAULT_DESTINATION, DEFAULT_ORIGIN, PLACES, ROOT, WALK_SPEED_MPS
from .cost import DEFAULT_WEIGHTS
from .env import load_env
from .graph import load_graph
from .overlay import active_entries, empty_overlay, parse_ts
from .routing import RouteError, features_for, route_pair
from .store import LocalStore, get_demo_store, get_store
from .timing import (GRACE, KINDS, day_offset, earlier_shorter_option, next_occurrence, parse_clock,
                     plan_arrival)

load_env()
app = FastAPI(title="Lanturn", version="0.2.0")
WEB = ROOT / "web"
OVERLAY_TTL_S = float(os.environ.get("LANTERN_OVERLAY_TTL_S", "20"))
STALE_AFTER_S = 2 * 3600      # warn if the agent has not completed a run for this long

# Two independent data paths. "live" is what the scheduled agent publishes from the real TfL
# feed, and is what normal planning uses. "demo" is where the labelled replay publishes, so a
# scheduled live run can never overwrite it and replay data never reaches normal planning.
SOURCES = ("live", "demo")


def _blank() -> dict[str, Any]:
    return {"overlay": None, "fetched": 0.0, "error": None, "heartbeat": None, "source": None}


_state: dict[str, Any] = _blank()            # live
_demo_state: dict[str, Any] = _blank()       # demo (replay)
_lock = threading.Lock()
_trigger: dict[str, dict[str, Any]] = {s: {"running": False, "last": None} for s in SOURCES}


def graph():
    return load_graph()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _st(source: str = "live") -> dict[str, Any]:
    return _demo_state if source == "demo" else _state


def _source(source: str | None) -> str:
    if source in (None, "", "live"):
        return "live"
    if source == "demo":
        return "demo"
    raise HTTPException(400, detail={"code": "bad_source", "message": "source must be live or demo."})


def store(source: str = "live"):
    st = _st(source)
    if "store" not in st:
        st["store"] = get_demo_store() if source == "demo" else get_store()
    return st["store"]


def published(force: bool = False, source: str = "live") -> dict:
    """Latest published overlay, cached briefly. On store failure the last good copy is kept."""
    state = _st(source)
    with _lock:
        if not force and state["overlay"] is not None and time.time() - state["fetched"] < OVERLAY_TTL_S:
            return state["overlay"]
        st = store(source)
        try:
            ov = st.read_published() or empty_overlay()
            hb = st.read_heartbeat()
            state.update(overlay=ov, heartbeat=hb, error=None, source=st.kind)
        except Exception as exc:  # noqa: BLE001 - keep serving the last good overlay
            state["error"] = _safe_error(exc)
            if state["overlay"] is None:
                cached = getattr(st, "cache", None)
                ov = (cached.read_published() if cached else None) or empty_overlay()
                state.update(overlay=ov, source=f"{st.kind}-cache")
        state["fetched"] = time.time()
        return state["overlay"]


def _safe_error(exc: Exception) -> str:
    """Short error text for the UI, with any URL or host removed."""
    import re

    msg = re.sub(r"https?://\S+", "[url]", str(exc))
    return f"{type(exc).__name__}: {msg[:140]}"


def _parse_place(s: str) -> tuple[tuple[float, float], dict]:
    if s in PLACES:
        p = PLACES[s]
        return (p["lon"], p["lat"]), {"id": s, "name": p["name"], "short": p["short"],
                                      "lon": p["lon"], "lat": p["lat"]}
    try:
        lon, lat = (float(x) for x in s.split(","))
    except ValueError:
        raise HTTPException(400, detail={"code": "bad_place", "message": f"Unknown place '{s}'."})
    return (lon, lat), {"id": None, "name": "Dropped pin", "short": "Dropped pin", "lon": lon, "lat": lat}


def _parse_when(s: str | None) -> datetime:
    if not s or s == "now":
        return _now()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, detail={"code": "bad_time", "message": "Unreadable time."})
    return dt if dt.tzinfo else dt.replace(tzinfo=LONDON)


def _resolve_time(mode: str | None, clock: str | None, when: str | None,
                  arrive: str | None, now: datetime) -> tuple[str, datetime, str | None]:
    """(mode, target, clock). The target is the departure for now/leave and the arrival for
    arrive. `time=HH:MM` means the next time a London clock shows it; `when` (departure) and
    `arrive` (arrival) are explicit ISO times."""
    if arrive:
        return "arrive", _parse_when(arrive), None
    if when and when != "now":
        return "leave", _parse_when(when), None
    mode = mode or "now"
    if mode == "now":
        return "now", now, None
    if mode not in ("leave", "arrive"):
        raise HTTPException(400, detail={"code": "bad_time", "message": "mode must be now, leave or arrive."})
    try:
        h, m = parse_clock(clock or "")
    except ValueError:
        raise HTTPException(400, detail={"code": "bad_time",
                                         "message": "Time must be HH:MM on a 24-hour clock."})
    return mode, next_occurrence(h, m, now), f"{h:02d}:{m:02d}"


def _clock(dt: datetime, now: datetime) -> dict:
    """A time as the app shows it: London clock, day offset from today, short date."""
    loc = dt.astimezone(LONDON)
    return {"iso": dt.isoformat(), "local": loc.strftime("%H:%M"), "day": day_offset(dt, now),
            "date": f"{loc:%a} {loc.day} {loc:%b}"}


def _edge_geoms(edge_ids: list[str]) -> list[list[list[float]]]:
    g = graph()
    return [g.edges[g.edge_index[e]]["coords"] for e in edge_ids if e in g.edge_index]


def _public_entry(en: dict, with_geom: bool = True) -> dict:
    out = {k: en.get(k) for k in ("entry_id", "source", "source_url", "action", "street", "summary",
                                  "evidence", "impact", "valid_from", "valid_to", "decided_at",
                                  "length_m", "location", "end_assumed")}
    out["synthetic"] = str(en.get("entry_id", "")).startswith("REPLAY-")
    if with_geom:
        out["geometry"] = _edge_geoms(en["edge_ids"])
    return out


def _freshness(source: str = "live") -> dict:
    state = _st(source)
    hb = state.get("heartbeat") or {}
    finished = parse_ts(hb.get("finished_at"))
    age = (datetime.now(timezone.utc) - finished).total_seconds() if finished else None
    return {"source": source,
            "last_run_at": hb.get("finished_at"), "last_run_status": hb.get("status"),
            "last_run_mode": hb.get("mode"), "age_s": round(age) if age is not None else None,
            "stale": age is None or age > STALE_AFTER_S,
            "store": state.get("source"), "store_error": state.get("error")}


def _strip(route: dict) -> dict:
    return {k: v for k, v in route.items() if k != "edge_ids"}


@app.get("/api/config")
def api_config():
    g = graph()
    feat = features_for(g)
    return {
        "places": [{"id": k, **{f: v[f] for f in ("name", "short", "lon", "lat")}} for k, v in PLACES.items()],
        "default": {"from": DEFAULT_ORIGIN, "to": DEFAULT_DESTINATION},
        "bbox": {"south": BBOX[0], "west": BBOX[1], "north": BBOX[2], "east": BBOX[3]},
        "walk_speed_mps": WALK_SPEED_MPS,
        "weights": DEFAULT_WEIGHTS.as_dict(),
        "coverage": {"lighting_by_length": g.meta["lighting_coverage_by_length"],
                     "lighting_priors": feat.priors,
                     "network_km": g.meta["total_km"], "segments": g.meta["edges"],
                     "osm_base": g.meta["osm_base"]},
        "companion": companion.public_status(),
        "attribution": [
            "Walking network and lighting tags (c) OpenStreetMap contributors (ODbL)",
            "Disruptions: Powered by TfL Open Data. Contains OS data (c) Crown copyright and database rights 2016 and Geomni UK Map data (c) and database rights [2019]",
            "Park opening hours: The Royal Parks",
        ],
    }


def _error(exc: RouteError) -> JSONResponse:
    status = 404 if exc.code in ("no_route", "outside_coverage") else 400
    return JSONResponse(status_code=status, content={"code": exc.code, "message": exc.message})


@app.get("/api/route")
def api_route(from_: str = Query(DEFAULT_ORIGIN, alias="from"), to: str = DEFAULT_DESTINATION,
              mode: str | None = None, time_: str | None = Query(None, alias="time"),
              when: str | None = None, arrive: str | None = None,
              lam: float = Query(1.0, ge=0.0, le=3.0), source: str | None = None):
    """Fastest and better-lit routes.

    Timing: `mode=now` (default), `mode=leave&time=HH:MM`, `mode=arrive&time=HH:MM` (24-hour,
    Europe/London, next occurrence), or an explicit ISO `when` (departure) / `arrive`.
    """
    g = graph()
    src = _source(source)
    (o, o_info), (d, d_info) = _parse_place(from_), _parse_place(to)
    now = _now()
    tmode, target, clock = _resolve_time(mode, time_, when, arrive, now)
    ov = published(source=src)

    # One plan per route kind: when to leave, when you arrive, and the routing result for that
    # departure. For "arrive by" the two kinds can have different departure times.
    plans: dict[str, dict] = {}
    try:
        if tmode == "arrive":
            found = plan_arrival(g, o, d, target, overlay=ov, lam=lam)
            for kind in KINDS:
                plans[kind] = found.get(kind) or found["fastest" if kind == "preferred" else "preferred"]
            if not found.get("preferred"):       # only the fastest makes it: show it for both
                plans["preferred"] = dict(found["fastest"], borrowed="fastest")
            elif not found.get("fastest"):
                plans["fastest"] = dict(found["preferred"], borrowed="preferred")
        else:
            r = route_pair(g, o, d, target, overlay=ov, lam=lam)
            for kind in KINDS:
                plans[kind] = {"depart": target, "result": r,
                               "arrive": target + timedelta(seconds=r[kind]["time_s"])}
    except RouteError as exc:
        if tmode == "arrive" and exc.code == "no_route":
            return JSONResponse(status_code=404, content={
                "code": "no_route",
                "message": f"No walking route is open that arrives by {_clock(target, now)['local']}."})
        return _error(exc)

    def route_of(kind: str) -> dict:
        p = plans[kind]
        return p["result"][p.get("borrowed", kind)]

    if tmode == "arrive":
        latest = max(p["depart"] for p in plans.values())
        if latest < now - GRACE:
            msg = (f"To arrive by {_clock(target, now)['local']} you would have needed to leave by "
                   f"{_clock(latest, now)['local']}.")
            body: dict[str, Any] = {"code": "too_late", "needed_departure": _clock(latest, now)}
            try:
                rn = route_pair(g, o, d, now, overlay=ov, lam=lam)
                eta = now + timedelta(seconds=rn["fastest"]["time_s"])
                body["leave_now_arrival"] = _clock(eta, now)
                msg += f" Leaving now gets you there at about {body['leave_now_arrival']['local']}."
            except RouteError:
                pass
            body["message"] = msg
            return JSONResponse(status_code=409, content=body)

    fast, pref = route_of("fastest"), route_of("preferred")
    identical = fast["edge_ids"] == pref["edge_ids"]

    # What did the published overlay change? Compare with the same request without it.
    entries_by_id: dict[str, dict] = {}
    for p in plans.values():
        for en in p["result"]["_ctx"].entries:
            entries_by_id[en["entry_id"]] = en
    changes: dict[str, Any] = {"changed": False, "entries": [], "fastest_delta_s": 0.0,
                               "preferred_delta_s": 0.0}
    if entries_by_id:
        try:
            bases: dict[datetime, dict] = {}
            base: dict[str, dict] = {}
            for kind in KINDS:
                t = plans[kind]["depart"]
                if t not in bases:
                    bases[t] = route_pair(g, o, d, t, overlay=None, lam=lam)
                base[kind] = bases[t][plans[kind].get("borrowed", kind)]
            cur = {"fastest": fast, "preferred": pref}
            for kind in KINDS:
                changes[f"{kind}_delta_s"] = round(cur[kind]["time_s"] - base[kind]["time_s"], 1)
                changes[f"{kind}_changed"] = cur[kind]["edge_ids"] != base[kind]["edge_ids"]
            changes["changed"] = changes["fastest_changed"] or changes["preferred_changed"]
            changes["previous"] = {kind: {"minutes": base[kind]["minutes"], "via": base[kind]["via"],
                                          "segments": base[kind]["segments"]} for kind in KINDS}
            base_edges = set(base["fastest"]["edge_ids"]) | set(base["preferred"]["edge_ids"])
            now_edges = set(fast["edge_ids"]) | set(pref["edge_ids"])
            for en in entries_by_id.values():
                ids = set(en["edge_ids"])
                on_old, on_new = bool(ids & base_edges), bool(ids & now_edges)
                if on_old or on_new:
                    pe = _public_entry(en)
                    pe["affects"] = "rerouted" if (on_old and not on_new) else "on_route"
                    changes["entries"].append(pe)
        except RouteError:
            pass

    def public(kind: str, route: dict) -> dict:
        p = plans[kind]
        out = _strip(route)
        out["depart"], out["arrive"] = _clock(p["depart"], now), _clock(p["arrive"], now)
        out["late"] = p["depart"] < now - GRACE
        return out

    zones: dict[str, dict] = {}
    for p in plans.values():
        for z in p["result"]["closed_zones"]:
            zones[z["zone"]] = z
    lead = plans["preferred"]

    # Arrive by: mention it when leaving a little earlier makes the walk much shorter.
    earlier = None
    if tmode == "arrive":
        opt = earlier_shorter_option(g, o, d, plans["fastest"], overlay=ov, lam=lam)
        if opt and opt["depart"] >= now - GRACE:
            r = opt["result"]
            earlier = {"depart": _clock(opt["depart"], now),
                       "fastest_minutes": r["fastest"]["minutes"],
                       "preferred_minutes": r["preferred"]["minutes"],
                       "saves_minutes": round(opt["saves_s"] / 60), "open_zones": opt["open_zones"]}
    return {
        "from": o_info, "to": d_info,
        "when": lead["depart"].isoformat(),
        "when_local": lead["depart"].astimezone(LONDON).strftime("%H:%M"),
        "time": {"mode": tmode, "clock": clock, "now": _clock(now, now), "target": _clock(target, now),
                 "earlier": earlier},
        "dark": lead["result"]["dark"], "lam": lam, "weights": lead["result"]["weights"],
        "identical": identical, "extra_s": round(pref["time_s"] - fast["time_s"], 1),
        "fastest": public("fastest", fast), "preferred": public("preferred", pref),
        "closed_zones": list(zones.values()),
        "overlay": {"version": ov["version"], "mode": ov.get("mode"),
                    "published_at": ov.get("published_at"),
                    "active_entries": len(entries_by_id), **_freshness(src)},
        "changes": changes,
        "coverage_note": "Lighting comes from OpenStreetMap tags and is unknown on parts of the "
                         "network. Scores describe observed street conditions, not personal risk.",
    }


@app.get("/api/overlay")
def api_overlay(refresh: bool = False, source: str | None = None):
    src = _source(source)
    ov = published(force=refresh, source=src)
    now = datetime.now(timezone.utc)
    act = {en["entry_id"] for en in active_entries(ov, now)}
    entries = []
    for en in ov.get("entries", []):
        pe = _public_entry(en)
        pe["active_now"] = en["entry_id"] in act
        entries.append(pe)
    checks = ov.get("checks") or {}
    return {"version": ov["version"], "parent_version": ov.get("parent_version"),
            "mode": ov.get("mode"), "published_at": ov.get("published_at"),
            "run_id": ov.get("run_id"), "entries": entries,
            "checks": {k: checks.get(k) for k in ("passed", "total", "failed", "routes_changed")},
            "zones": [zone_status(z, now) for z in ZONE_LABELS],
            **_freshness(src)}


@app.get("/api/agent/last-run")
def api_last_run(source: str | None = None):
    try:
        lr = store(_source(source)).last_run()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, detail={"code": "store_unavailable", "message": _safe_error(exc)})
    if not lr:
        return {"run": None, "decisions": []}
    decisions = [d for d in lr["decisions"] if d["decision"] != "reject_invalid_record"]
    run = {k: v for k, v in lr["run"].items() if k != "traceback"}
    return {"run": run, "decisions": decisions}


@app.get("/api/agent/runs")
def api_runs(n: int = 10, source: str | None = None):
    try:
        runs = store(_source(source)).list_runs(n)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, detail={"code": "store_unavailable", "message": _safe_error(exc)})
    return {"runs": [{k: v for k, v in r.items() if k != "traceback"} for r in runs]}


def _run_local(mode: str, step: str | None, source: str) -> None:
    from .agent import run as agent_run
    from .agent.replay import replay_feed

    trig = _trigger[source]
    try:
        now = datetime.now(timezone.utc)
        feed = replay_feed(now, step or "1") if mode == "replay" else agent_run.live_feed
        res = agent_run.execute(graph(), store(source), feed, now=now, mode=mode)
        trig["last"] = {"status": res["run"]["status"], "run_id": res["run"]["run_id"],
                        "summary": res["run"]["summary"], "error": res["run"]["error"]}
    except Exception as exc:  # noqa: BLE001
        trig["last"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        trig["running"] = False
        published(force=True, source=source)


@app.post("/api/agent/run")
def api_agent_run(mode: str = "replay", step: str | None = None):
    """Demo control: start one agent run now. A replay always runs on the demo data path
    (its own Databricks job, tables and volume), a live run on the live path."""
    if os.environ.get("LANTERN_ALLOW_TRIGGER", "1") != "1":
        raise HTTPException(403, detail={"code": "disabled", "message": "Manual runs are disabled."})
    if mode not in ("live", "replay") or step not in (None, "1", "2"):
        raise HTTPException(400, detail={"code": "bad_request", "message": "mode must be live or replay."})
    source = "demo" if mode == "replay" else "live"
    if not isinstance(store(source), LocalStore):
        from . import databricks_ops
        try:
            return databricks_ops.trigger_job(mode, step, target=source)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, detail={"code": "trigger_failed", "message": _safe_error(exc)})
    trig = _trigger[source]
    if trig["running"]:
        return {"started": False, "running": True}
    trig["running"] = True
    threading.Thread(target=_run_local, args=(mode, step, source), daemon=True).start()
    return {"started": True, "running": True, "where": "local", "source": source}


@app.get("/api/agent/status")
def api_agent_status(source: str | None = None):
    src = _source(source)
    if not isinstance(store(src), LocalStore):
        from . import databricks_ops
        try:
            return databricks_ops.job_status(target=src)
        except Exception as exc:  # noqa: BLE001
            return {"running": False, "last": {"status": "unknown", "error": _safe_error(exc)}}
    return {"running": _trigger[src]["running"], "last": _trigger[src]["last"], "where": "local"}


@app.post("/api/companion/session")
def api_companion_session(body: dict | None = None):
    """Start a walking-companion call. Returns only what the browser needs to join the room;
    the Synthesia and LiveKit secrets stay on the server."""
    try:
        return companion.start_session(body or {})
    except companion.CompanionError as exc:
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message})


@app.post("/api/companion/end")
def api_companion_end(body: dict | None = None):
    companion.end_session((body or {}).get("session_id"))
    return {"ended": True}


@app.get("/api/health")
def api_health():
    g = graph()
    return {"ok": True, "segments": len(g.edges), **_freshness()}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=str(WEB)), name="web")
