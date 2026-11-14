"""Export the demo journey as SVG fragments for the Figma design file.

The Figma screens use real geometry: parks and main roads from the walking graph, and the
actual fastest / better-lit routes (before and after the replay closure), projected into a
390 x 844 phone frame. Output: var/design/map_*.svg and var/design/map_meta.json.

  python scripts/export_design_map.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lantern.access import LONDON  # noqa: E402
from lantern.agent import run as agent_run  # noqa: E402
from lantern.agent.replay import replay_feed  # noqa: E402
from lantern.config import DATA_RAW, PLACES, VAR_DIR  # noqa: E402
from lantern.geo import to_xy  # noqa: E402
from lantern.graph import MAIN_ROADS, PARK_ZONES, load_graph  # noqa: E402
from lantern.routing import route_pair  # noqa: E402
from lantern.store import LocalStore  # noqa: E402

W, H = 390, 844
BOX = (44, 196, 346, 452)          # where the journey should sit: left, top, right, bottom (px)
OUT = VAR_DIR / "design"
COL = {"fast": "#62C6FF", "lit": "#FFC857", "unlit": "#C07CFF", "unknown": "#7F8AA6",
       "alert": "#FF7A6B", "park": "#12261F", "road": "#263252", "prev": "#C9D1E6"}


def main() -> int:
    from shapely.geometry import LineString

    g = load_graph()
    when = datetime(2026, 11, 14, 22, 30, tzinfo=LONDON)
    o, d = PLACES["imperial_south_ken"], PLACES["wilson_house"]
    od = ((o["lon"], o["lat"]), (d["lon"], d["lat"]))
    before = route_pair(g, *od, when)

    # Replay overlay, produced by the real agent code with the recorded assessments.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
    from conftest import stub_assessor  # noqa: E402
    store = LocalStore(OUT / "store")
    now = datetime.now(timezone.utc)
    agent_run.execute(g, store, replay_feed(now, "1", include_capture=False),
                      assessor=stub_assessor, now=now, mode="replay")
    overlay = store.read_published()
    after = route_pair(g, *od, when, overlay=overlay, force_active=True)

    # Projection: fit both states of the journey into BOX, y pointing down.
    pts = [to_xy(*c) for r in (before, after) for k in ("fastest", "preferred")
           for s in r[k]["segments"] for c in s["coords"]]
    minx, maxx = min(p[0] for p in pts), max(p[0] for p in pts)
    miny, maxy = min(p[1] for p in pts), max(p[1] for p in pts)
    scale = min((BOX[2] - BOX[0]) / (maxx - minx), (BOX[3] - BOX[1]) / (maxy - miny))
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    px0, py0 = (BOX[0] + BOX[2]) / 2, (BOX[1] + BOX[3]) / 2

    def P(lon, lat):
        x, y = to_xy(lon, lat)
        return (px0 + (x - cx) * scale, py0 - (y - cy) * scale)

    def path(coords, tol=0.6, nd=1):
        line = LineString([P(*c) for c in coords]).simplify(tol)
        return "M" + " L".join(f"{x:.{nd}f} {y:.{nd}f}" for x, y in line.coords)

    def visible(coords):
        return any(-20 <= x <= W + 20 and -20 <= y <= H + 20 for x, y in (P(*c) for c in coords))

    # ---- base map: parks + main roads ---------------------------------
    raw = json.loads((DATA_RAW / "osm_extract.json").read_text(encoding="utf-8"))
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in raw["elements"] if e["type"] == "node"}
    parks = []
    for e in raw["elements"]:
        if e["type"] == "way" and e["id"] in PARK_ZONES:
            parks.append((PARK_ZONES[e["id"]], [nodes[n] for n in e["nodes"] if n in nodes]))
    # Main roads, merged into continuous lines and simplified to whole pixels so the
    # vector stays small enough to paste into Figma in one call.
    from shapely.geometry import MultiLineString
    from shapely.ops import linemerge

    road_lines = [LineString([P(*c) for c in e["coords"]]) for e in g.edges
                  if e["hw"] in MAIN_ROADS and visible(e["coords"])]
    merged = linemerge(MultiLineString(road_lines))
    geoms = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    parts = []
    for ln in geoms:
        ln = ln.simplify(2.0)
        if ln.length >= 6:
            parts.append("M" + " L".join(f"{x:.0f} {y:.0f}" for x, y in ln.coords))
    road_d = " ".join(parts)
    base = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" fill="none" xmlns="http://www.w3.org/2000/svg">']
    for name, ring in parks:
        base.append(f'<path id="park/{name}" d="{path(ring, 1.5, 0)} Z" fill="{COL["park"]}"/>')
    base.append(f'<path id="roads/main" d="{road_d}" stroke="{COL["road"]}" stroke-width="1.6" '
                'stroke-linecap="round" stroke-linejoin="round"/>')
    base.append("</svg>")

    def route_svg(r, selected, prev=None, closure=None):
        out = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" fill="none" xmlns="http://www.w3.org/2000/svg">']
        if prev is not None:
            dd = " ".join(path(s["coords"]) for s in prev[selected]["segments"])
            out.append(f'<path id="route/previous" d="{dd}" stroke="{COL["prev"]}" stroke-opacity="0.6" '
                       'stroke-width="2.5" stroke-dasharray="1 6" stroke-linecap="round"/>')
        other = "fastest" if selected == "preferred" else "preferred"
        dd = " ".join(path(s["coords"]) for s in r[other]["segments"])
        colour = COL["fast"] if other == "fastest" else COL["lit"]
        out.append(f'<path id="route/{other}" d="{dd}" stroke="{colour}" stroke-opacity="0.8" stroke-width="4" '
                   'stroke-linecap="round" stroke-linejoin="round"/>')
        dd = " ".join(path(s["coords"]) for s in r[selected]["segments"])
        out.append(f'<path id="route/{selected}/casing" d="{dd}" stroke="#060A14" stroke-width="9" '
                   'stroke-linecap="round" stroke-linejoin="round"/>')
        main = COL["lit"] if selected == "preferred" else COL["fast"]
        for status, colour in (("yes", main), ("unknown", COL["unknown"]), ("no", COL["unlit"])):
            segs = [s for s in r[selected]["segments"] if s["lit"] == status]
            if segs:
                dd = " ".join(path(s["coords"]) for s in segs)
                label = {"yes": "lit", "unknown": "not-recorded", "no": "unlit"}[status]
                out.append(f'<path id="route/{selected}/{label}" d="{dd}" stroke="{colour}" stroke-width="5.5" '
                           'stroke-linecap="round" stroke-linejoin="round"/>')
        if closure:
            dd = " ".join(path(c) for c in closure)
            out.append(f'<path id="closure/casing" d="{dd}" stroke="#2B0D09" stroke-width="9" stroke-linecap="round"/>')
            out.append(f'<path id="closure/segment" d="{dd}" stroke="{COL["alert"]}" stroke-width="5" '
                       'stroke-dasharray="3 6" stroke-linecap="round"/>')
        out.append("</svg>")
        return "".join(out)

    close = next(en for en in overlay["entries"] if en["action"] == "close")
    closure_coords = [g.edges[g.edge_index[e]]["coords"] for e in close["edge_ids"]]
    allc = [c for seg in closure_coords for c in seg]
    cmid = P(*allc[len(allc) // 2])

    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "map_base.svg": "".join(base),
        "map_routes_compare.svg": route_svg(before, "preferred"),
        "map_routes_update.svg": route_svg(after, "preferred", prev=before, closure=closure_coords),
    }
    for name, svg in files.items():
        (OUT / name).write_text(svg, encoding="utf-8")

    def card(r, k):
        s = r[k]
        lit = int(s["lighting_share"]["yes"] * 100 + 0.5)      # same rounding as the app (half up)
        unlit = int(s["lighting_share"]["no"] * 100 + 0.5)
        return {"minutes": s["minutes"], "km": round(s["length_m"] / 1000, 1), "lit": lit, "unlit": unlit,
                "unknown": max(0, 100 - lit - unlit), "via": s["via"][:2]}

    meta = {
        "frame": [W, H], "origin": [round(v, 1) for v in P(o["lon"], o["lat"])],
        "destination": [round(v, 1) for v in P(d["lon"], d["lat"])],
        "closure_marker": [round(v, 1) for v in cmid],
        "compare": {"fastest": card(before, "fastest"), "preferred": card(before, "preferred"),
                    "extra_min": round(before["extra_s"] / 60),
                    "isolated_less_m": round((before["fastest"]["exposure_s"]["isolated"]
                                              - before["preferred"]["exposure_s"]["isolated"]) * 1.34 / 10) * 10},
        "update": {"fastest": card(after, "fastest"), "preferred": card(after, "preferred"),
                   "extra_min": round(after["extra_s"] / 60),
                   "delta_min": round((after["preferred"]["time_s"] - before["preferred"]["time_s"]) / 60),
                   "closure": {"street": close["street"], "evidence": close["evidence"],
                               "length_m": close["length_m"], "summary": close["summary"]}},
        "svg_bytes": {k: len(v) for k, v in files.items()},
    }
    (OUT / "map_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
