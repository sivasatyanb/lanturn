"""Publication gate: rerun baseline journeys against a candidate overlay.

An overlay is only published if every baseline journey is still routable, every
returned route is a valid connected walk that avoids closed segments, and no journey
gets implausibly longer. Entries that break a journey are rejected individually.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from ..access import LONDON
from ..config import BASELINE_JOURNEYS, PLACES
from ..graph import Graph
from ..routing import RouteError, route_pair

MAX_SLOWDOWN_RATIO = 1.5     # candidate fastest time / no-overlay fastest time
SLOWDOWN_GRACE_S = 300.0     # ...but always allow up to 5 extra minutes


def reference_times(now: datetime) -> list[datetime]:
    """Two representative night departures: 22:30 (Hyde Park open) and 00:30 (parks shut)."""
    d = now.astimezone(LONDON).date()
    return [datetime.combine(d, time(22, 30), tzinfo=LONDON),
            datetime.combine(d + timedelta(days=1), time(0, 30), tzinfo=LONDON)]


def verify_route(g: Graph, route: dict, ctx, src: int, dst: int) -> list[str]:
    """Structural validity of one returned route."""
    problems = []
    node = src
    for eid in route["edge_ids"]:
        i = g.edge_index.get(eid)
        if i is None:
            problems.append(f"unknown edge {eid}")
            break
        if i in ctx.closed:
            problems.append(f"route uses closed segment {eid}")
        e = g.edges[i]
        if e["u"] == node:
            node = e["v"]
        elif e["v"] == node:
            node = e["u"]
        else:
            problems.append(f"route is not contiguous at {eid}")
            break
    if not problems and node != dst:
        problems.append("route does not end at the destination")
    if route["time_s"] <= 0:
        problems.append("non-positive walking time")
    return problems


def _run_one(g: Graph, o: str, d: str, when: datetime, overlay: dict | None) -> dict[str, Any]:
    po, pd = PLACES[o], PLACES[d]
    try:
        r = route_pair(g, (po["lon"], po["lat"]), (pd["lon"], pd["lat"]), when,
                       overlay=overlay, force_active=True)
    except RouteError as exc:
        return {"ok": False, "error": exc.code}
    problems = []
    for kind in ("fastest", "preferred"):
        problems += [f"{kind}: {p}" for p in verify_route(g, r[kind], r["_ctx"], r["_src"], r["_dst"])]
    if r["preferred"]["time_s"] + 1e-6 < r["fastest"]["time_s"]:
        problems.append("preferred route is faster than the fastest route")
    return {"ok": not problems, "problems": problems,
            "fastest_s": r["fastest"]["time_s"], "preferred_s": r["preferred"]["time_s"],
            "fastest_edges": r["fastest"]["edge_ids"], "preferred_edges": r["preferred"]["edge_ids"]}


def run_baseline(g: Graph, overlay: dict | None, now: datetime) -> dict[str, dict]:
    out = {}
    for when in reference_times(now):
        for o, d in BASELINE_JOURNEYS:
            out[f"{o}>{d}@{when.strftime('%H:%M')}"] = _run_one(g, o, d, when, overlay)
    return out


def compare(base: dict[str, dict], cand: dict[str, dict]) -> dict[str, Any]:
    journeys, failures = [], []
    for key, c in cand.items():
        b = base[key]
        row = {"journey": key, "ok": c["ok"]}
        if not b["ok"]:
            # The network itself cannot serve this journey: not the overlay's fault.
            row.update(ok=True, note="baseline itself unavailable: " + str(b.get("error") or b.get("problems")))
        elif not c["ok"]:
            row["problem"] = c.get("error") or "; ".join(c.get("problems", []))
            failures.append(key)
        else:
            delta = c["fastest_s"] - b["fastest_s"]
            row.update(baseline_min=round(b["fastest_s"] / 60, 1),
                       candidate_min=round(c["fastest_s"] / 60, 1),
                       delta_s=round(delta, 1),
                       fastest_changed=c["fastest_edges"] != b["fastest_edges"],
                       preferred_changed=c["preferred_edges"] != b["preferred_edges"])
            if c["fastest_s"] > max(b["fastest_s"] * MAX_SLOWDOWN_RATIO, b["fastest_s"] + SLOWDOWN_GRACE_S):
                row["ok"] = False
                row["problem"] = (f"journey becomes {delta / 60:.0f} min longer, beyond the "
                                  f"{MAX_SLOWDOWN_RATIO}x / {SLOWDOWN_GRACE_S / 60:.0f} min limit")
                failures.append(key)
        journeys.append(row)
    changed = [j["journey"] for j in journeys if j.get("fastest_changed") or j.get("preferred_changed")]
    return {"passed": not failures, "total": len(journeys), "failed": failures,
            "routes_changed": changed, "journeys": journeys}


def gate(g: Graph, candidate: dict, now: datetime) -> tuple[dict, list[dict], dict]:
    """Returns (overlay to publish, rejected entries with reasons, test report).

    If the full candidate fails, each entry is tested alone and the failing ones are
    dropped; the remainder is re-tested. If that still fails nothing new is accepted.
    """
    base = run_baseline(g, None, now)
    report = compare(base, run_baseline(g, candidate, now))
    report["baseline_ok"] = all(v["ok"] for v in base.values())
    if report["passed"]:
        return candidate, [], report

    rejected, keep = [], []
    for en in candidate["entries"]:
        solo = dict(candidate, entries=[en])
        rep = compare(base, run_baseline(g, solo, now))
        if rep["passed"]:
            keep.append(en)
        else:
            first = next(j for j in rep["journeys"] if j["journey"] in rep["failed"])
            rejected.append({"entry": en, "reason": f"{first['journey']}: {first.get('problem')}"})
    reduced = dict(candidate, entries=keep)
    report2 = compare(base, run_baseline(g, reduced, now))
    report2["baseline_ok"] = report["baseline_ok"]
    report2["first_attempt_failed"] = report["failed"]
    if not report2["passed"]:
        for en in keep:
            rejected.append({"entry": en, "reason": "combination with other entries breaks baseline journeys"})
        reduced = dict(candidate, entries=[])
        report2 = compare(base, run_baseline(g, reduced, now))
        report2["baseline_ok"] = report["baseline_ok"]
    return reduced, rejected, report2
