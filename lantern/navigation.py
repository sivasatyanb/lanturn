"""Turn instructions for the navigation preview, derived from route geometry and street names.

Everything here comes from the walking graph: the heading is the bearing of the first usable
stretch of the route, turns are measured from the geometry either side of each junction, and
street names are the OSM names carried on the edges (sidewalks mapped as separate ways carry
the name of the road they run beside, see graph.build). Nothing is invented: an unnamed path
is called a path.
"""
from __future__ import annotations

import math
from typing import Any

from .access import ZONE_LABELS
from .geo import to_xy

WINDOW_M = 18.0          # geometry either side of a junction used to measure a turn
HEADING_M = 25.0         # stretch of route used for the initial heading
CLUSTER_M = 22.0         # junctions closer than this are one manoeuvre
TURN_MIN_DEG = 35.0      # below this a junction is "straight on"
SHORT_RUN_M = 25.0       # a named stretch shorter than this is not announced on its own
PATH_KINDS = {"footway", "path", "pedestrian", "cycleway", "bridleway"}
COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float | None:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _turn(before: float | None, after: float | None) -> float:
    """Signed turn in degrees: negative = left, positive = right."""
    if before is None or after is None:
        return 0.0
    return (after - before + 180.0) % 360.0 - 180.0


def _turn_type(angle: float) -> str:
    a = abs(angle)
    side = "right" if angle > 0 else "left"
    if a < TURN_MIN_DEG:
        return "straight"
    if a < 60:
        return "slight_" + side
    if a < 135:
        return side
    return "sharp_" + side


_VERB = {"straight": "Continue", "slight_left": "Bear left", "slight_right": "Bear right",
         "left": "Turn left", "right": "Turn right",
         "sharp_left": "Turn sharp left", "sharp_right": "Turn sharp right"}


class _Line:
    """Route polyline in local metres with distance lookups."""

    def __init__(self, coords: list[list[float]]):
        self.ll = coords
        self.xy = [to_xy(c[0], c[1]) for c in coords]
        self.cum = [0.0]
        for i in range(1, len(self.xy)):
            self.cum.append(self.cum[-1] + math.dist(self.xy[i - 1], self.xy[i]))
        self.total = self.cum[-1]

    def at(self, s: float) -> tuple[float, float]:
        s = min(max(s, 0.0), self.total)
        lo, hi = 0, len(self.cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        span = self.cum[hi] - self.cum[lo]
        f = (s - self.cum[lo]) / span if span > 0 else 0.0
        (x0, y0), (x1, y1) = self.xy[lo], self.xy[hi]
        return x0 + f * (x1 - x0), y0 + f * (y1 - y0)

    def bearing(self, s0: float, s1: float) -> float | None:
        return _bearing(self.at(s0), self.at(s1))


def _describe(edge: dict) -> str:
    """Words for a stretch with no street name."""
    if edge.get("steps"):
        return "the steps"
    if edge.get("enclosed") == "underpass":
        return "the underpass"
    if edge["hw"] in PATH_KINDS:
        zone = edge.get("zone")
        return f"the path in {ZONE_LABELS[zone]}" if zone in ZONE_LABELS else "the footpath"
    return "the unnamed road"


def _smoothed_names(edges: list[dict]) -> list[str | None]:
    """Edge names with very short stretches (crossings, corner sidewalks) folded into the
    stretch before them, so a 6 m crossing does not read as a change of street."""
    runs: list[list] = []          # [name, length, first edge, last edge]
    for i, e in enumerate(edges):
        if runs and runs[-1][0] == e.get("name"):
            runs[-1][1] += e["len"]
            runs[-1][3] = i
        else:
            runs.append([e.get("name"), e["len"], i, i])
    names: list[str | None] = [None] * len(edges)
    prev = None
    for k, (name, length, a, b) in enumerate(runs):
        if 0 < k < len(runs) - 1 and length < SHORT_RUN_M:
            name = prev
        for i in range(a, b + 1):
            names[i] = name
        prev = name
    return names


def build(oriented: list[tuple[dict, list]]) -> dict[str, Any]:
    """Navigation summary for a route given as [(edge, coords in travel order), ...]."""
    if not oriented:
        return {"heading": 0.0, "start": None, "length_m": 0, "depart": None, "maneuvers": []}
    edges = [e for e, _ in oriented]
    coords: list[list[float]] = []
    for _, cs in oriented:
        coords.extend(cs if not coords else cs[1:])
    line = _Line(coords)
    # Edge boundaries from the stored edge lengths, rescaled to the polyline length.
    raw = [0.0]
    for e in edges:
        raw.append(raw[-1] + e["len"])
    scale = line.total / raw[-1] if raw[-1] > 0 else 1.0
    bounds = [r * scale for r in raw]             # distance along the route at each edge boundary
    names = _smoothed_names(edges)

    heading = line.bearing(0.0, min(HEADING_M, line.total)) or 0.0
    first = edges[0]
    start_name = names[0]
    depart_on = start_name or _describe(first)
    depart = {
        "type": "depart", "at_m": 0, "street": start_name,
        "compass": COMPASS[int((heading + 22.5) // 45) % 8],
        "instruction": f"Head {COMPASS[int((heading + 22.5) // 45) % 8]} "
                       f"{'on' if start_name else 'along'} {depart_on}",
    }

    # Candidate junctions: a real turn, or the start of a differently named street.
    cands = []
    for j in range(1, len(edges)):
        s = bounds[j]
        ang = _turn(line.bearing(max(0.0, s - WINDOW_M), s), line.bearing(s, min(line.total, s + WINDOW_M)))
        renamed = names[j] != names[j - 1] and names[j] is not None
        if abs(ang) >= TURN_MIN_DEG or renamed:
            cands.append((j, s, ang))
    # Junctions a few metres apart (a crossing, a corner) are one manoeuvre.
    clusters: list[list] = []
    for c in cands:
        if clusters and c[1] - clusters[-1][-1][1] <= CLUSTER_M:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    maneuvers = []
    last_at = 0.0
    for cl in clusters:
        j0, s0 = cl[0][0], cl[0][1]
        j1, s1 = cl[-1][0], cl[-1][1]
        ang = _turn(line.bearing(max(0.0, s0 - WINDOW_M), s0), line.bearing(s1, min(line.total, s1 + WINDOW_M)))
        kind = _turn_type(ang)
        before, after = names[j0 - 1], names[j1]
        if kind == "straight" and (after is None or after == before):
            continue                                    # wiggle that cancels out, nothing to say
        pivot = max(cl, key=lambda c: abs(c[2]))
        at = pivot[1]
        if at - last_at < 8.0 and maneuvers:
            continue
        if at < 8.0:
            # A turn within the first few steps is part of setting off, not a manoeuvre.
            continue
        if after and after == before:
            text = f"{_VERB[kind]} to stay on {after}"
        elif after:
            text = f"{_VERB[kind]} onto {after}"
        else:
            text = f"{_VERB[kind]} onto {_describe(edges[j1])}"
        maneuvers.append({"type": kind, "at_m": round(at), "leg_m": round(at - last_at),
                          "street": after, "angle": round(ang), "instruction": text})
        last_at = at
    maneuvers.append({"type": "arrive", "at_m": round(line.total), "leg_m": round(line.total - last_at),
                      "street": None, "angle": 0, "instruction": "Arrive at your destination"})
    return {"heading": round(heading, 1), "start": coords[0], "length_m": round(line.total),
            "depart": depart, "maneuvers": maneuvers}
