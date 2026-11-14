"""Deterministic location verification: turn "footway closed on X between A and B"
into a set of graph segments, and check it against the record's own coordinates.

No model is involved here. A proposed location is accepted only if
  1. the named street exists in the walking network,
  2. the extent resolves to a bounded stretch (cross streets found, or a point),
  3. that stretch lies near the coordinates TfL published for the record, and
  4. the stretch is not implausibly large.
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..geo import to_xy
from ..graph import MAIN_ROADS, VEHICLE_ROADS, Graph

POINT_RADIUS_M = 45.0        # stretch of street affected around a point/junction
ANCHOR_TOLERANCE_M = 150.0   # max distance between matched stretch and TfL coordinates
JUNCTION_TOLERANCE_M = 30.0  # two streets "meet" if their segments come this close
CORRIDOR_HALF_WIDTH_M = 45.0
MAX_STRETCH_M = 700.0        # between two cross streets
MAX_TOTAL_LEN_M = 2500.0     # all matched segments (both footways + carriageway + crossings)

_ABBR = {"rd": "road", "st": "street", "ave": "avenue", "av": "avenue", "sq": "square",
         "pl": "place", "gdns": "gardens", "ln": "lane", "ct": "court", "dr": "drive"}


def norm_name(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"\[[^\]]*\]", " ", s)          # road numbers such as [A4]
    s = re.sub(r"\([^)]*\)", " ", s)           # "(Northbound)", postcodes
    s = re.sub(r"[^a-z0-9' ]+", " ", s.lower()).replace("'", "")
    toks = [_ABBR.get(t, t) for t in s.split()]
    if toks and toks[0] == "the":
        toks = toks[1:]
    return " ".join(toks)


def _name_index(g: Graph) -> dict[str, list[int]]:
    idx = getattr(g, "_name_index", None)
    if idx is None:
        idx = {}
        for i, e in enumerate(g.edges):
            n = norm_name(e["name"])
            if n:
                idx.setdefault(n, []).append(i)
        g._name_index = idx
    return idx


def street_edges(g: Graph, name: str) -> list[int]:
    return _name_index(g).get(norm_name(name), [])


def _xy_coords(e: dict) -> list[tuple[float, float]]:
    return [to_xy(*c) for c in e["coords"]]


def _mid(e: dict) -> tuple[float, float]:
    pts = _xy_coords(e)
    return pts[len(pts) // 2] if len(pts) > 2 else (
        (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2)


def _pt_seg_dist(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _pt_edge_dist(p, e: dict) -> float:
    pts = _xy_coords(e)
    return min(_pt_seg_dist(p, pts[k], pts[k + 1]) for k in range(len(pts) - 1))


def _junction(g: Graph, street_idx: list[int], cross_idx: list[int],
              near: tuple[float, float] | None) -> tuple[float, float] | None:
    """Point where two named streets meet (closest approach of their segments).

    If the streets meet more than once, the meeting point nearest `near` is used.
    """
    cross_pts = [p for j in cross_idx for p in _xy_coords(g.edges[j])]
    if not cross_pts:
        return None
    cands = []
    for i in street_idx:
        for p in _xy_coords(g.edges[i]):
            d = min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in cross_pts)
            if d <= JUNCTION_TOLERANCE_M:
                cands.append((d, p))
    if not cands:
        return None
    if near is not None:
        cands.sort(key=lambda c: (round(math.hypot(c[1][0] - near[0], c[1][1] - near[1]) / 60), c[0]))
    else:
        cands.sort(key=lambda c: c[0])
    return cands[0][1]


def _anchor_points(ev: dict) -> list[tuple[float, float]]:
    pts = []
    if ev.get("point"):
        pts.append(to_xy(*ev["point"]))
    for ls in ev.get("lines") or []:
        pts.extend(to_xy(*p) for p in ls)
    return pts


def _side_filter(g: Graph, idx: list[int], side: str) -> tuple[list[int], str]:
    """Keep only the footway on the named side, when footways are mapped separately."""
    roads = [i for i in idx if g.edges[i]["hw"] in VEHICLE_ROADS]
    walks = [i for i in idx if g.edges[i]["hw"] not in VEHICLE_ROADS]
    if not roads or not walks:
        return idx, "side not resolvable: footways are not mapped separately here"
    cx = sum(_mid(g.edges[i])[0] for i in roads) / len(roads)
    cy = sum(_mid(g.edges[i])[1] for i in roads) / len(roads)
    keep = []
    for i in walks:
        e = g.edges[i]
        if e.get("footway") == "crossing":
            continue   # crossings span the carriageway and belong to neither side
        mx, my = _mid(e)
        # Offset from the nearest carriageway point, not the centroid, so bends are handled.
        best = min(((math.hypot(mx - p[0], my - p[1]), p) for r in roads
                    for p in _xy_coords(g.edges[r])), key=lambda t: t[0])[1]
        dx, dy = mx - best[0], my - best[1]
        ok = {"north": dy > 1.5, "south": dy < -1.5, "east": dx > 1.5, "west": dx < -1.5}[side]
        if ok:
            keep.append(i)
    if not keep:
        return idx, f"side not resolvable: no separately mapped footway on the {side} side"
    return keep, f"limited to the {side}-side footway ({len(keep)} segments)"


def match_event(g: Graph, ev: dict, a: dict) -> dict[str, Any]:
    """Resolve an assessed event to graph segments. Returns {'ok', 'edge_ids', ...}."""
    out: dict[str, Any] = {"ok": False, "edge_ids": [], "checks": [], "notes": []}
    anchors = _anchor_points(ev)
    if not anchors:
        out["reason"] = "record has no point or line coordinates to verify against"
        return out
    anchor0 = anchors[0]

    # 1. Street exists in the walking network.
    names = [a.get("street") or ""] + [s["name"] for s in ev.get("streets") or []]
    idx: list[int] = []
    used_name = None
    for nm in names:
        idx = street_edges(g, nm)
        if idx:
            used_name = nm
            break
    if not idx:
        out["reason"] = f"street '{a.get('street')}' not found in the walking network"
        return out
    out["street"] = g.edges[idx[0]]["name"]
    out["checks"].append(f"street '{used_name}' found: {len(idx)} segments")

    # 2. Resolve the extent to a bounded stretch.
    extent = a.get("extent") or "unknown"
    cross = [c for c in (a.get("cross_streets") or []) if norm_name(c)]
    selected: list[int] = []
    if extent == "between" and len(cross) >= 2:
        ja = _junction(g, idx, street_edges(g, cross[0]), anchor0)
        jb = _junction(g, idx, street_edges(g, cross[1]), anchor0)
        if ja is None or jb is None:
            missing = cross[0] if ja is None else cross[1]
            out["reason"] = f"could not find where {out['street']} meets '{missing}'"
            return out
        span = math.hypot(jb[0] - ja[0], jb[1] - ja[1])
        if span > MAX_STRETCH_M:
            out["reason"] = f"stretch between cross streets is {span:.0f} m, above the {MAX_STRETCH_M:.0f} m limit"
            return out
        if span < 5:
            out["reason"] = "both cross streets resolve to the same junction"
            return out
        ux, uy = (jb[0] - ja[0]) / span, (jb[1] - ja[1]) / span
        for i in idx:
            mx, my = _mid(g.edges[i])
            t = (mx - ja[0]) * ux + (my - ja[1]) * uy
            perp = abs(-(mx - ja[0]) * uy + (my - ja[1]) * ux)
            if -5 <= t <= span + 5 and perp <= CORRIDOR_HALF_WIDTH_M:
                selected.append(i)
        out["checks"].append(f"between '{cross[0]}' and '{cross[1]}': {span:.0f} m stretch")
    elif extent == "junction" and cross:
        j = _junction(g, idx, street_edges(g, cross[0]), anchor0)
        if j is None:
            out["reason"] = f"could not find where {out['street']} meets '{cross[0]}'"
            return out
        selected = [i for i in idx if _pt_edge_dist(j, g.edges[i]) <= POINT_RADIUS_M]
        out["checks"].append(f"junction with '{cross[0]}' located")
    elif ev.get("lines"):
        line_pts = [to_xy(*p) for ls in ev["lines"] for p in ls]
        segs = [(line_pts[k], line_pts[k + 1]) for k in range(len(line_pts) - 1)]
        selected = [i for i in idx
                    if min(_pt_seg_dist(_mid(g.edges[i]), s[0], s[1]) for s in segs) <= 25.0]
        out["checks"].append("extent taken from TfL street segment geometry")
    else:
        selected = [i for i in idx if _pt_edge_dist(anchor0, g.edges[i]) <= POINT_RADIUS_M]
        if extent == "whole_street":
            out["notes"].append("source says whole street; limited to the stretch at TfL's coordinates")
        out["checks"].append(f"stretch within {POINT_RADIUS_M:.0f} m of TfL's coordinates")

    if not selected:
        out["reason"] = f"no segment of {out['street']} at the described location"
        return out

    # 3. Agreement with the coordinates TfL published.
    d = min(_pt_edge_dist(p, g.edges[i]) for p in anchors for i in selected)
    out["anchor_distance_m"] = round(d, 1)
    if d > ANCHOR_TOLERANCE_M:
        out["reason"] = (f"text location is {d:.0f} m from TfL's coordinates "
                         f"(limit {ANCHOR_TOLERANCE_M:.0f} m): text and geometry disagree")
        return out
    out["checks"].append(f"{d:.0f} m from TfL's coordinates")

    # 4. Side of the street, if the text names one.
    side = a.get("side") or "unknown"
    if side in ("north", "south", "east", "west"):
        selected, note = _side_filter(g, selected, side)
        out["notes"].append(note)
        out["side_resolved"] = note.startswith("limited")
    else:
        out["side_resolved"] = False

    total = sum(g.edges[i]["len"] for i in selected)
    if total > MAX_TOTAL_LEN_M:
        out["reason"] = f"matched {total:.0f} m of network, above the {MAX_TOTAL_LEN_M:.0f} m limit"
        return out
    out["ok"] = True
    out["edge_ids"] = sorted(g.edges[i]["id"] for i in selected)
    out["length_m"] = round(total)
    out["main_road"] = any((g.edges[i].get("road_class") or g.edges[i]["hw"]) in MAIN_ROADS
                           for i in selected)
    return out
