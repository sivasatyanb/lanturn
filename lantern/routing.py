"""On-demand routing: fastest baseline vs preferred night route, with explanations."""
from __future__ import annotations

import heapq
from datetime import datetime, timezone
from typing import Any

from . import navigation
from .access import ZONE_LABELS, closed_zones, is_dark
from .cost import DEFAULT_WEIGHTS, EdgeFeatures, Weights
from .graph import Graph
from .overlay import active_entries

_FEATURE_CACHE: dict[tuple[int, float | None], EdgeFeatures] = {}


def features_for(g: Graph, unknown_prior: float | None = None) -> EdgeFeatures:
    key = (id(g), unknown_prior)
    if key not in _FEATURE_CACHE:
        _FEATURE_CACHE[key] = EdgeFeatures(g, unknown_prior)
    return _FEATURE_CACHE[key]


class RouteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Context:
    """Everything that depends on departure time and the published overlay."""

    def __init__(self, g: Graph, when: datetime, overlay: dict | None = None,
                 use_overlay: bool = True, force_active: bool = False):
        self.g = g
        self.when = when
        self.zones_closed = closed_zones(when)
        if not use_overlay or not overlay:
            self.entries = []
        elif force_active:      # regression testing: judge entries regardless of validity window
            self.entries = list(overlay.get("entries", []))
        else:
            self.entries = active_entries(overlay, when)
        self.closed: set[int] = set()
        self.closed_by: dict[int, str] = {}      # edge idx -> reason key
        self.restricted: dict[int, str] = {}     # edge idx -> entry id
        if self.zones_closed:
            for i, e in enumerate(g.edges):
                if e["zone"] in self.zones_closed:
                    self.closed.add(i)
                    self.closed_by[i] = "zone:" + e["zone"]
        for en in self.entries:
            for eid in en["edge_ids"]:
                i = g.edge_index.get(eid)
                if i is None:
                    continue
                if en["action"] == "close":
                    self.closed.add(i)
                    self.closed_by[i] = "entry:" + en["entry_id"]
                else:
                    self.restricted[i] = en["entry_id"]

    def node_usable(self, nid: int) -> bool:
        return any(i not in self.closed for i, _ in self.g.adj[nid])


def shortest_path(g: Graph, src: int, dst: int, cost: list[float],
                  closed: set[int]) -> tuple[list[int], float] | None:
    """Dijkstra over edge indices. Returns (edge index path, total cost) or None."""
    dist = {src: 0.0}
    prev: dict[int, tuple[int, int]] = {}
    heap = [(0.0, src)]
    done = set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        if u == dst:
            break
        done.add(u)
        for ei, v in g.adj[u]:
            if ei in closed or v in done:
                continue
            nd = d + cost[ei]
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = (u, ei)
                heapq.heappush(heap, (nd, v))
    if dst not in dist:
        return None
    path = []
    node = dst
    while node != src:
        u, ei = prev[node]
        path.append(ei)
        node = u
    path.reverse()
    return path, dist[dst]


def _edge_costs(feat: EdgeFeatures, ctx: Context, w: Weights, lam: float) -> list[float]:
    tau = feat.tau
    if lam <= 0:
        return tau
    pen = feat.penalty(w)
    cost = [t * (1.0 + lam * p) for t, p in zip(tau, pen)]
    for i in ctx.restricted:
        cost[i] += tau[i] * lam * w.disrupted
    return cost


def _oriented_coords(g: Graph, path: list[int], src: int) -> list[tuple[int, list]]:
    out = []
    node = src
    for ei in path:
        e = g.edges[ei]
        if e["u"] == node:
            out.append((ei, e["coords"]))
            node = e["v"]
        else:
            out.append((ei, e["coords"][::-1]))
            node = e["u"]
    return out


def summarise(g: Graph, feat: EdgeFeatures, ctx: Context, path: list[int], src: int,
              w: Weights, lam: float) -> dict[str, Any]:
    """Route summary: time, exposure breakdown, coverage, map segments."""
    tau = feat.tau
    time_s = sum(tau[i] for i in path)
    length = sum(g.edges[i]["len"] for i in path)
    wd = w.as_dict()
    expo_s = {k: 0.0 for k in feat.x}
    lit_m = {"yes": 0.0, "no": 0.0, "unknown": 0.0}
    restricted_s = 0.0
    names: dict[str, float] = {}
    zones: dict[str, float] = {}
    steps = 0
    segments: list[dict] = []
    oriented = _oriented_coords(g, path, src)
    for ei, coords in oriented:
        e = g.edges[ei]
        for k in feat.x:
            expo_s[k] += tau[ei] * feat.x[k][ei]
        status = e["lit"] or "unknown"
        lit_m[status] += e["len"]
        if ei in ctx.restricted:
            restricted_s += tau[ei]
        if e["name"]:
            names[e["name"]] = names.get(e["name"], 0.0) + e["len"]
        if e["zone"]:
            zones[e["zone"]] = zones.get(e["zone"], 0.0) + e["len"]
        if e["steps"]:
            steps += 1
        if segments and segments[-1]["lit"] == status:
            segments[-1]["coords"].extend(coords[1:])
            segments[-1]["len"] += e["len"]
        else:
            segments.append({"lit": status, "coords": list(coords), "len": e["len"]})
    for s in segments:
        s["len"] = round(s["len"], 1)
    penalty_s = sum(wd[k] * expo_s[k] for k in expo_s) + wd["disrupted"] * restricted_s
    via = [n for n, _ in sorted(names.items(), key=lambda kv: -kv[1])[:3]]
    return {
        "time_s": round(time_s, 1),
        "minutes": max(1, round(time_s / 60)),
        "length_m": round(length),
        "generalised_cost_s": round(time_s + lam * penalty_s, 1),
        "penalty_s": round(penalty_s, 1),
        "exposure_s": {k: round(v, 1) for k, v in expo_s.items()},
        "restricted_s": round(restricted_s, 1),
        "lighting_m": {k: round(v) for k, v in lit_m.items()},
        "lighting_share": {k: round(v / length, 3) if length else 0.0 for k, v in lit_m.items()},
        "lighting_coverage": round((lit_m["yes"] + lit_m["no"]) / length, 3) if length else 0.0,
        "via": via,
        "zones": [{"zone": z, "label": ZONE_LABELS.get(z, z), "length_m": round(m)}
                  for z, m in zones.items()],
        "steps": steps,
        "segments": segments,
        "nav": navigation.build([(g.edges[ei], coords) for ei, coords in oriented]),
        "edge_ids": [g.edges[i]["id"] for i in path],
    }


def _snap(g: Graph, ctx: Context, lon: float, lat: float, what: str) -> int:
    nid, d = g.nearest_node(lon, lat, max_m=300.0, allow=ctx.node_usable)
    if nid is None:
        raise RouteError("outside_coverage",
                         f"The {what} is outside the area we have walking data for.")
    return nid


def route_pair(g: Graph, origin: tuple[float, float], dest: tuple[float, float],
               when: datetime | None = None, overlay: dict | None = None,
               weights: Weights = DEFAULT_WEIGHTS, lam: float = 1.0,
               unknown_prior: float | None = None, use_overlay: bool = True,
               force_active: bool = False) -> dict[str, Any]:
    """Compute the fastest baseline and the preferred route for (lon, lat) endpoints."""
    when = when or datetime.now(timezone.utc)
    feat = features_for(g, unknown_prior)
    ctx = Context(g, when, overlay, use_overlay, force_active)
    src = _snap(g, ctx, origin[0], origin[1], "start")
    dst = _snap(g, ctx, dest[0], dest[1], "destination")
    if src == dst:
        raise RouteError("same_place", "Start and destination are the same place.")

    fast = shortest_path(g, src, dst, feat.tau, ctx.closed)
    if fast is None:
        raise RouteError("no_route", "No walkable route is open between these places right now.")
    pref = shortest_path(g, src, dst, _edge_costs(feat, ctx, weights, lam), ctx.closed)
    fast_sum = summarise(g, feat, ctx, fast[0], src, weights, lam)
    pref_sum = summarise(g, feat, ctx, pref[0], src, weights, lam)
    same = fast[0] == pref[0]
    return {
        "when": when.isoformat(),
        "dark": is_dark(when),
        "lam": lam,
        "weights": weights.as_dict(),
        "fastest": fast_sum,
        "preferred": pref_sum,
        "identical": same,
        "extra_s": round(pref_sum["time_s"] - fast_sum["time_s"], 1),
        "closed_zones": list(ctx.zones_closed.values()),
        "overlay_entries_active": [en["entry_id"] for en in ctx.entries],
        "_ctx": ctx, "_src": src, "_dst": dst,
    }
