"""TfL Unified API: road disruptions. Powered by TfL Open Data.

Endpoint: GET https://api.tfl.gov.uk/Road/all/Disruption
Docs: https://api.tfl.gov.uk/swagger/ui/index.html (Road). No date-range parameter exists,
so we fetch the full London feed (~100-150 records) and filter to the demo area ourselves.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .config import HTTP_HEADERS
from .geo import in_bbox
from .overlay import parse_ts

FEED_URL = "https://api.tfl.gov.uk/Road/all/Disruption"
SOURCE = "tfl_road_disruption"
REQUIRED = ("id", "comments", "startDateTime", "status")


def fetch_raw(timeout: float = 30.0) -> tuple[list[dict], dict]:
    """Fetch the live feed. Returns (records, fetch metadata). Raises on HTTP/parse failure."""
    import requests

    params = {}
    key = os.environ.get("TFL_APP_KEY")
    if key:
        params["app_key"] = key
    r = requests.get(FEED_URL, headers=HTTP_HEADERS, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError("TfL feed did not return a list")
    meta = {"url": FEED_URL, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "http_status": r.status_code, "records": len(data)}
    return data, meta


def _parse_point(s: Any) -> tuple[float, float] | None:
    try:
        if isinstance(s, str):
            lon, lat = json.loads(s)
        else:
            lon, lat = s
        return float(lon), float(lat)
    except Exception:  # noqa: BLE001
        return None


def _parse_linestring(s: Any) -> list[tuple[float, float]]:
    try:
        pts = json.loads(s) if isinstance(s, str) else s
        return [(float(p[0]), float(p[1])) for p in pts]
    except Exception:  # noqa: BLE001
        return []


def normalise(rec: dict) -> tuple[dict | None, str | None]:
    """Raw TfL record -> flat event dict, or (None, reason) if the record is unusable."""
    missing = [k for k in REQUIRED if not rec.get(k)]
    if missing:
        return None, "missing fields: " + ",".join(missing)
    point = _parse_point(rec.get("point")) or _parse_point(
        (rec.get("geography") or {}).get("coordinates"))
    lines = []
    streets = []
    for st in rec.get("streets") or []:
        if st.get("name"):
            streets.append({"name": st["name"], "closure": st.get("closure"),
                            "directions": st.get("directions")})
        for seg in st.get("segments") or []:
            ls = _parse_linestring(seg.get("lineString"))
            if len(ls) >= 2:
                lines.append(ls)
    polygons = []
    geom = rec.get("geometry") or {}
    if geom.get("type") == "Polygon":
        polygons = [geom["coordinates"][0]]
    elif geom.get("type") == "MultiPolygon":
        polygons = [p[0] for p in geom["coordinates"]]
    if point is None and not lines and not polygons:
        return None, "no usable geometry"
    try:
        start = parse_ts(rec.get("startDateTime"))
        end = parse_ts(rec.get("endDateTime"))
        modified = parse_ts(rec.get("lastModifiedTime") or rec.get("currentUpdateDateTime"))
    except ValueError:
        return None, "unparseable timestamps"
    ev = {
        "id": str(rec["id"]),
        "source": SOURCE,
        "source_url": f"{FEED_URL}/{rec['id']}",
        "category": rec.get("category"),
        "sub_category": rec.get("subCategory"),
        "severity": rec.get("severity"),
        "status": rec.get("status"),
        "location": rec.get("location"),
        "comments": rec.get("comments") or "",
        "current_update": rec.get("currentUpdate") or "",
        "has_closures": rec.get("hasClosures"),
        "is_provisional": rec.get("isProvisional"),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "modified": modified.isoformat() if modified else None,
        "point": list(point) if point else None,
        "lines": [[list(p) for p in ls] for ls in lines],
        "polygons": polygons,
        "streets": streets,
    }
    return ev, None


def in_area(ev: dict, pad_m: float = 50.0) -> bool:
    pts = []
    if ev["point"]:
        pts.append(ev["point"])
    for ls in ev["lines"]:
        pts.extend(ls)
    for poly in ev["polygons"]:
        pts.extend(poly)
    return any(in_bbox(p[0], p[1], pad_m) for p in pts)


def text_of(ev: dict) -> str:
    """The source text Claude may quote from, exactly as published."""
    parts = [ev.get("location") or "", ev.get("comments") or "", ev.get("current_update") or ""]
    return "\n".join(p for p in parts if p)
