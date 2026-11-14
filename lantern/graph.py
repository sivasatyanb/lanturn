"""Build and load the pedestrian graph.

Source: OpenStreetMap (ODbL) via Overpass, see scripts/fetch_osm.py.
The graph is undirected. Every edge is a stretch of one OSM way between two
junction nodes and keeps its provenance (way id) and the raw observations we
route on. Missing observations stay missing (None): they are never coerced
to a value at build time.
"""
from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DATA_PROCESSED, DATA_RAW
from .geo import to_xy

GRAPH_PATH = DATA_PROCESSED / "graph.json.gz"

WALKABLE = {
    "footway", "pedestrian", "path", "steps", "living_street", "residential",
    "unclassified", "service", "tertiary", "secondary", "primary", "trunk",
    "tertiary_link", "secondary_link", "primary_link", "trunk_link",
    "cycleway", "bridleway",
}
VEHICLE_ROADS = {
    "residential", "unclassified", "tertiary", "secondary", "primary", "trunk",
    "tertiary_link", "secondary_link", "primary_link", "trunk_link", "living_street",
}
MAIN_ROADS = {"tertiary", "secondary", "primary", "trunk"}
FOOT_OK = {"yes", "designated", "permissive"}
ACCESS_BLOCKED = {"private", "no", "customers", "permit", "delivery", "military"}

# Named public parks with pedestrian opening hours (see lantern/access.py).
PARK_ZONES = {372975520: "hyde_park", 3986346: "kensington_gardens"}


def _lit(tags: dict) -> str | None:
    v = tags.get("lit")
    if v in ("yes", "24/7", "automatic"):
        return "yes"
    if v == "no":
        return "no"
    return None  # untagged or unparseable -> unknown


def _walkable(tags: dict) -> bool:
    hw = tags.get("highway")
    if hw not in WALKABLE:
        return False
    if tags.get("area") == "yes" and hw != "pedestrian":
        return False
    foot = tags.get("foot")
    if foot in ("no", "private", "customers"):
        return False
    if tags.get("access") in ACCESS_BLOCKED and foot not in FOOT_OK:
        return False
    if tags.get("indoor") == "yes":
        return False
    is_tunnel = tags.get("tunnel") in ("yes", "passage")
    if hw in VEHICLE_ROADS and hw != "living_street":
        # Vehicle tunnels/underpasses and carriageways without a footway are not walkable.
        if is_tunnel:
            return False
        if hw in MAIN_ROADS or hw.endswith("_link"):
            if tags.get("sidewalk") in ("no", "none") and foot not in FOOT_OK:
                return False
    if hw == "service" and tags.get("service") in ("parking_aisle", "driveway", "drive-through"):
        return False
    return True


@dataclass
class Graph:
    nodes: dict[int, tuple[float, float]]          # node id -> (lon, lat)
    edges: list[dict[str, Any]]                     # see build() for the schema
    meta: dict[str, Any] = field(default_factory=dict)
    adj: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # node -> [(edge idx, other node)]
    edge_index: dict[str, int] = field(default_factory=dict)

    def finalize(self) -> "Graph":
        adj = defaultdict(list)
        self.edge_index = {}
        for i, e in enumerate(self.edges):
            adj[e["u"]].append((i, e["v"]))
            adj[e["v"]].append((i, e["u"]))
            self.edge_index[e["id"]] = i
        self.adj = dict(adj)
        self._grid = None
        self._edge_tree = None
        return self

    # --- spatial lookups -------------------------------------------------
    def _node_grid(self, cell: float = 60.0):
        if getattr(self, "_grid", None) is None:
            grid = defaultdict(list)
            for nid in self.adj:
                x, y = to_xy(*self.nodes[nid])
                grid[(int(x // cell), int(y // cell))].append((nid, x, y))
            self._grid = (cell, dict(grid))
        return self._grid

    def nearest_node(self, lon: float, lat: float, max_m: float = 400.0,
                     allow=None) -> tuple[int | None, float]:
        """Nearest graph node; `allow(node_id)` can exclude nodes (e.g. inside a closed park)."""
        cell, grid = self._node_grid()
        x, y = to_xy(lon, lat)
        cx, cy = int(x // cell), int(y // cell)
        best, best_d = None, float("inf")
        for ring in range(0, int(max_m // cell) + 2):
            for i in range(cx - ring, cx + ring + 1):
                for j in range(cy - ring, cy + ring + 1):
                    if max(abs(i - cx), abs(j - cy)) != ring:
                        continue
                    for nid, nx_, ny_ in grid.get((i, j), ()):
                        if allow is not None and not allow(nid):
                            continue
                        d = math.hypot(nx_ - x, ny_ - y)
                        if d < best_d:
                            best, best_d = nid, d
            if best is not None and best_d <= ring * cell:
                break
        if best is None or best_d > max_m:
            return None, best_d
        return best, best_d

    def edge_lines(self):
        """(STRtree, [LineString]) over all edges in local metres. Built lazily."""
        if getattr(self, "_edge_tree", None) is None:
            from shapely import STRtree
            from shapely.geometry import LineString
            lines = [LineString([to_xy(*c) for c in e["coords"]]) for e in self.edges]
            self._edge_tree = (STRtree(lines), lines)
        return self._edge_tree

    def save(self, path: Path = GRAPH_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": self.meta,
            "nodes": {str(k): [round(v[0], 7), round(v[1], 7)] for k, v in self.nodes.items()},
            "edges": self.edges,
        }
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))


_CACHE: dict[str, Graph] = {}


def load_graph(path: Path = GRAPH_PATH) -> Graph:
    key = str(path)
    if key not in _CACHE:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            p = json.load(f)
        g = Graph(nodes={int(k): (v[0], v[1]) for k, v in p["nodes"].items()},
                  edges=p["edges"], meta=p["meta"])
        _CACHE[key] = g.finalize()
    return _CACHE[key]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build(raw_path: Path = DATA_RAW / "osm_extract.json") -> Graph:
    from shapely import STRtree
    from shapely.geometry import LineString, Point, Polygon

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    els = raw["elements"]
    node_ll: dict[int, tuple[float, float]] = {}
    node_tags: dict[int, dict] = {}
    ways = []
    for el in els:
        if el["type"] == "node":
            node_ll[el["id"]] = (el["lon"], el["lat"])
            if "tags" in el:
                node_tags[el["id"]] = el["tags"]
        elif el["type"] == "way":
            ways.append(el)

    # Park polygons (metric).
    zones = {}
    for w in ways:
        if w["id"] in PARK_ZONES:
            pts = [to_xy(*node_ll[n]) for n in w["nodes"] if n in node_ll]
            zones[PARK_ZONES[w["id"]]] = Polygon(pts).buffer(0)

    walk = [w for w in ways if _walkable(w.get("tags", {}))]
    use: dict[int, int] = defaultdict(int)
    for w in walk:
        ns = w["nodes"]
        for i, n in enumerate(ns):
            use[n] += 2 if i in (0, len(ns) - 1) else 1

    edges: list[dict[str, Any]] = []
    for w in walk:
        t = w["tags"]
        ns = [n for n in w["nodes"] if n in node_ll]
        if len(ns) < 2:
            continue
        start = 0
        seg = 0
        for i in range(1, len(ns)):
            if use[ns[i]] > 1 or i == len(ns) - 1:
                part = ns[start:i + 1]
                start = i
                coords = [node_ll[n] for n in part]
                xy = [to_xy(*c) for c in coords]
                length = sum(math.dist(xy[k], xy[k + 1]) for k in range(len(xy) - 1))
                if length <= 0 or part[0] == part[-1]:
                    continue
                hw = t["highway"]
                enclosed = None
                if t.get("tunnel") in ("yes", "passage"):
                    enclosed = "underpass"
                elif t.get("tunnel") == "building_passage" or t.get("covered") in ("yes", "building_arcade"):
                    enclosed = "covered_passage"
                edges.append({
                    "id": f"{w['id']}:{seg}",
                    "way": w["id"],
                    "u": part[0], "v": part[-1],
                    "len": round(length, 1),
                    "coords": [[round(c[0], 6), round(c[1], 6)] for c in coords],
                    "hw": hw,
                    "name": t.get("name"),
                    "name_src": "osm" if t.get("name") else None,
                    "lit": _lit(t),
                    "footway": t.get("footway"),
                    "steps": hw == "steps",
                    "enclosed": enclosed,
                    "oh": t.get("opening_hours"),
                })
                seg += 1

    # --- derived context: nearby road, zone, isolation -------------------
    lines = [LineString([to_xy(*c) for c in e["coords"]]) for e in edges]
    road_idx = [i for i, e in enumerate(edges) if e["hw"] in VEHICLE_ROADS]
    named_idx = [i for i in road_idx if edges[i]["name"]]
    road_tree = STRtree([lines[i] for i in road_idx])
    named_tree = STRtree([lines[i] for i in named_idx])

    for i, e in enumerate(edges):
        mid = lines[i].interpolate(0.5, normalized=True)
        e["zone"] = None
        if e["hw"] not in MAIN_ROADS:
            for zname, poly in zones.items():
                if poly.contains(mid):
                    e["zone"] = zname
                    break
        if e["hw"] in VEHICLE_ROADS:
            e["road_dist"] = 0.0
            e["road_class"] = e["hw"]
        else:
            j = int(road_tree.nearest(mid))
            d = lines[road_idx[j]].distance(mid)
            e["road_dist"] = round(float(d), 1)
            e["road_class"] = edges[road_idx[j]]["hw"] if d <= 25 else None
        # Sidewalks/crossings mapped as separate unnamed ways inherit the street name
        # of the nearest named road within 20 m (flagged as inferred).
        if not e["name"] and e["hw"] in ("footway", "cycleway", "path", "pedestrian"):
            j = int(named_tree.nearest(mid))
            d = lines[named_idx[j]].distance(mid)
            if d <= 20:
                e["name"] = edges[named_idx[j]]["name"]
                e["name_src"] = "nearest_road"

    # --- venues: evening-venue proxy (count within 50 m) -----------------
    venue_kinds = {"pub", "bar", "nightclub", "restaurant", "fast_food"}
    venues = [Point(to_xy(*node_ll[n])) for n, t in node_tags.items()
              if t.get("amenity") in venue_kinds and n in node_ll]
    stations = [(t.get("name"), node_ll[n]) for n, t in node_tags.items()
                if t.get("railway") == "station" and n in node_ll]
    vtree = STRtree(venues) if venues else None
    for i, e in enumerate(edges):
        e["venues"] = int(len(vtree.query(lines[i].buffer(50), predicate="intersects"))) if vtree is not None else None

    # --- keep the largest connected component ----------------------------
    parent: dict[int, int] = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for e in edges:
        ra, rb = find(e["u"]), find(e["v"])
        if ra != rb:
            parent[ra] = rb
    size: dict[int, float] = defaultdict(float)
    for e in edges:
        size[find(e["u"])] += e["len"]
    main = max(size, key=size.get)
    kept = [e for e in edges if find(e["u"]) == main]
    used_nodes = {n for e in kept for n in (e["u"], e["v"])}

    total_len = sum(e["len"] for e in kept)
    lit_len: dict[str, float] = defaultdict(float)
    for e in kept:
        lit_len[e["lit"] or "unknown"] += e["len"]
    meta = {
        "source": "OpenStreetMap via Overpass API",
        "licence": "ODbL 1.0, (c) OpenStreetMap contributors",
        "osm_base": raw.get("_lantern", {}).get("osm_base"),
        "fetched_at": raw.get("_lantern", {}).get("fetched_at"),
        "bbox": raw.get("_lantern", {}).get("bbox"),
        "edges": len(kept), "nodes": len(used_nodes),
        "dropped_edges_disconnected": len(edges) - len(kept),
        "total_km": round(total_len / 1000, 1),
        "lighting_coverage_by_length": {k: round(v / total_len, 4) for k, v in lit_len.items()},
        "stations": sorted({(n, round(ll[0], 5), round(ll[1], 5)) for n, ll in stations if n}),
    }
    g = Graph(nodes={n: node_ll[n] for n in used_nodes}, edges=kept, meta=meta)
    return g.finalize()
