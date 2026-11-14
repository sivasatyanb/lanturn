"""HTTP API + static mobile web app.

Run:  python -m uvicorn lantern.api:app --port 8000
The app only reads the published overlay; it never edits routing data itself.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .access import LONDON, ZONE_LABELS, zone_status
from .config import BBOX, DEFAULT_DESTINATION, DEFAULT_ORIGIN, PLACES, ROOT, WALK_SPEED_MPS
from .cost import DEFAULT_WEIGHTS
from .env import load_env
from .graph import load_graph
from .overlay import active_entries, empty_overlay, parse_ts
from .routing import Context, RouteError, features_for, route_pair
from .store import LocalStore, get_store

load_env()
app = FastAPI(title="Lantern", version="0.1.0")
WEB = ROOT / "web"
OVERLAY_TTL_S = float(os.environ.get("LANTERN_OVERLAY_TTL_S", "20"))
STALE_AFTER_S = 2 * 3600      # warn if the agent has not completed a run for this long

_state: dict[str, Any] = {"overlay": None, "fetched": 0.0, "error": None, "heartbeat": None,
                          "source": None}
_lock = threading.Lock()
_trigger: dict[str, Any] = {"running": False, "last": None}


def graph():
    return load_graph()


def store():
    if "store" not in _state:
        _state["store"] = get_store()
    return _state["store"]


def published(force: bool = False) -> dict:
    """Latest published overlay, cached briefly. On store failure the last good copy is kept."""
    with _lock:
        if not force and _state["overlay"] is not None and time.time() - _state["fetched"] < OVERLAY_TTL_S:
            return _state["overlay"]
        st = store()
        try:
            ov = st.read_published() or empty_overlay()
            hb = st.read_heartbeat()
            _state.update(overlay=ov, heartbeat=hb, error=None, source=st.kind)
        except Exception as exc:  # noqa: BLE001 - keep serving the last good overlay
            _state["error"] = _safe_error(exc)
            if _state["overlay"] is None:
                cached = getattr(st, "cache", None)
                ov = (cached.read_published() if cached else None) or empty_overlay()
                _state.update(overlay=ov, source=f"{st.kind}-cache")
        _state["fetched"] = time.time()
        return _state["overlay"]


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
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, detail={"code": "bad_time", "message": "Unreadable departure time."})
    return dt if dt.tzinfo else dt.replace(tzinfo=LONDON)


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


def _freshness() -> dict:
    hb = _state.get("heartbeat") or {}
    finished = parse_ts(hb.get("finished_at"))
    age = (datetime.now(timezone.utc) - finished).total_seconds() if finished else None
    return {"last_run_at": hb.get("finished_at"), "last_run_status": hb.get("status"),
            "last_run_mode": hb.get("mode"), "age_s": round(age) if age is not None else None,
            "stale": age is None or age > STALE_AFTER_S,
            "store": _state.get("source"), "store_error": _state.get("error")}


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
        "attribution": [
            "Walking network and lighting tags (c) OpenStreetMap contributors (ODbL)",
            "Disruptions: Powered by TfL Open Data. Contains OS data (c) Crown copyright and database rights 2016 and Geomni UK Map data (c) and database rights [2019]",
            "Park opening hours: The Royal Parks",
        ],
    }


@app.get("/api/route")
def api_route(from_: str = Query(DEFAULT_ORIGIN, alias="from"), to: str = DEFAULT_DESTINATION,
              when: str | None = None, lam: float = Query(1.0, ge=0.0, le=3.0)):
    g = graph()
    (o, o_info), (d, d_info) = _parse_place(from_), _parse_place(to)
    t = _parse_when(when)
    ov = published()
    try:
        r = route_pair(g, o, d, t, overlay=ov, lam=lam)
    except RouteError as exc:
        status = 404 if exc.code in ("no_route", "outside_coverage") else 400
        return JSONResponse(status_code=status, content={"code": exc.code, "message": exc.message})

    # What did the published overlay change? Compare with the same request without it.
    ctx: Context = r["_ctx"]
    changes = {"changed": False, "entries": [], "fastest_delta_s": 0.0, "preferred_delta_s": 0.0}
    if ctx.entries:
        try:
            base = route_pair(g, o, d, t, overlay=None, lam=lam)
            changes["fastest_delta_s"] = round(r["fastest"]["time_s"] - base["fastest"]["time_s"], 1)
            changes["preferred_delta_s"] = round(r["preferred"]["time_s"] - base["preferred"]["time_s"], 1)
            base_edges = set(base["fastest"]["edge_ids"]) | set(base["preferred"]["edge_ids"])
            fast_changed = r["fastest"]["edge_ids"] != base["fastest"]["edge_ids"]
            pref_changed = r["preferred"]["edge_ids"] != base["preferred"]["edge_ids"]
            changes["changed"] = fast_changed or pref_changed
            changes["fastest_changed"], changes["preferred_changed"] = fast_changed, pref_changed
            changes["previous"] = {
                "fastest": {"minutes": base["fastest"]["minutes"], "via": base["fastest"]["via"],
                            "segments": base["fastest"]["segments"]},
                "preferred": {"minutes": base["preferred"]["minutes"], "via": base["preferred"]["via"],
                              "segments": base["preferred"]["segments"]},
            }
            now_edges = set(r["fastest"]["edge_ids"]) | set(r["preferred"]["edge_ids"])
            for en in ctx.entries:
                ids = set(en["edge_ids"])
                on_old = bool(ids & base_edges)
                on_new = bool(ids & now_edges)
                if on_old or on_new:
                    pe = _public_entry(en)
                    pe["affects"] = "rerouted" if (on_old and not on_new) else "on_route"
                    changes["entries"].append(pe)
        except RouteError:
            pass

    return {
        "from": o_info, "to": d_info,
        "when": r["when"], "when_local": t.astimezone(LONDON).strftime("%H:%M"),
        "dark": r["dark"], "lam": lam, "weights": r["weights"],
        "identical": r["identical"], "extra_s": r["extra_s"],
        "fastest": _strip(r["fastest"]), "preferred": _strip(r["preferred"]),
        "closed_zones": r["closed_zones"],
        "overlay": {"version": ov["version"], "mode": ov.get("mode"),
                    "published_at": ov.get("published_at"),
                    "active_entries": len(ctx.entries), **_freshness()},
        "changes": changes,
        "coverage_note": "Lighting comes from OpenStreetMap tags and is unknown on parts of the "
                         "network. Scores describe observed street conditions, not personal risk.",
    }


@app.get("/api/overlay")
def api_overlay(refresh: bool = False):
    ov = published(force=refresh)
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
            **_freshness()}


@app.get("/api/agent/last-run")
def api_last_run():
    try:
        lr = store().last_run()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, detail={"code": "store_unavailable", "message": str(exc)[:200]})
    if not lr:
        return {"run": None, "decisions": []}
    decisions = [d for d in lr["decisions"] if d["decision"] != "reject_invalid_record"]
    run = {k: v for k, v in lr["run"].items() if k != "traceback"}
    return {"run": run, "decisions": decisions}


@app.get("/api/agent/runs")
def api_runs(n: int = 10):
    try:
        runs = store().list_runs(n)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, detail={"code": "store_unavailable", "message": str(exc)[:200]})
    return {"runs": [{k: v for k, v in r.items() if k != "traceback"} for r in runs]}


def _run_local(mode: str, step: str | None) -> None:
    from .agent import run as agent_run
    from .agent.replay import replay_feed

    try:
        now = datetime.now(timezone.utc)
        feed = replay_feed(now, step or "1") if mode == "replay" else agent_run.live_feed
        res = agent_run.execute(graph(), store(), feed, now=now, mode=mode)
        _trigger["last"] = {"status": res["run"]["status"], "run_id": res["run"]["run_id"],
                            "summary": res["run"]["summary"], "error": res["run"]["error"]}
    except Exception as exc:  # noqa: BLE001
        _trigger["last"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _trigger["running"] = False
        published(force=True)


@app.post("/api/agent/run")
def api_agent_run(mode: str = "live", step: str | None = None):
    """Demo control: start one agent run now. Only for the local store; with the
    Databricks store the scheduled job does this (or use scripts/databricks_deploy.py run)."""
    if os.environ.get("LANTERN_ALLOW_TRIGGER", "1") != "1":
        raise HTTPException(403, detail={"code": "disabled", "message": "Manual runs are disabled."})
    if mode not in ("live", "replay") or step not in (None, "1", "2"):
        raise HTTPException(400, detail={"code": "bad_request", "message": "mode must be live or replay."})
    if not isinstance(store(), LocalStore):
        from . import databricks_ops
        try:
            return databricks_ops.trigger_job(mode, step)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, detail={"code": "trigger_failed", "message": str(exc)[:300]})
    if _trigger["running"]:
        return {"started": False, "running": True}
    _trigger["running"] = True
    threading.Thread(target=_run_local, args=(mode, step), daemon=True).start()
    return {"started": True, "running": True, "where": "local"}


@app.get("/api/agent/status")
def api_agent_status():
    if not isinstance(store(), LocalStore):
        from . import databricks_ops
        try:
            return databricks_ops.job_status()
        except Exception as exc:  # noqa: BLE001
            return {"running": False, "last": {"status": "unknown", "error": str(exc)[:200]}}
    return {"running": _trigger["running"], "last": _trigger["last"], "where": "local"}


@app.get("/api/health")
def api_health():
    g = graph()
    return {"ok": True, "segments": len(g.edges), **_freshness()}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=str(WEB)), name="web")
