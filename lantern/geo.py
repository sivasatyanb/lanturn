"""Small geodesy helpers. Everything works in a local metric projection."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

from .config import BBOX

LAT0 = (BBOX[0] + BBOX[2]) / 2
LON0 = (BBOX[1] + BBOX[3]) / 2
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_xy(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 -> local metres (equirectangular about the demo-area centre).

    Error is below 0.1 % over the ~4 km demo area, far below OSM positional noise.
    """
    return ((lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT)


def to_lonlat(x: float, y: float) -> tuple[float, float]:
    return (x / M_PER_DEG_LON + LON0, y / M_PER_DEG_LAT + LAT0)


def dist_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    x1, y1 = to_xy(lon1, lat1)
    x2, y2 = to_xy(lon2, lat2)
    return math.hypot(x2 - x1, y2 - y1)


def in_bbox(lon: float, lat: float, pad_m: float = 0.0) -> bool:
    s, w, n, e = BBOX
    dlat = pad_m / M_PER_DEG_LAT
    dlon = pad_m / M_PER_DEG_LON
    return (s - dlat) <= lat <= (n + dlat) and (w - dlon) <= lon <= (e + dlon)


def sunset_utc(d: date, lat: float = LAT0, lon: float = LON0) -> datetime:
    """Sunset (solar zenith 90.833 deg), NOAA approximation, about 1 minute accuracy."""
    n = d.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + 0.5)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    lat_r = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl))
              - math.tan(lat_r) * math.tan(decl))
    ha = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))
    minutes = 720 - 4 * (lon - ha) - eqtime  # minutes after 00:00 UTC
    return datetime.combine(d, time(0, 0), tzinfo=timezone.utc) + timedelta(minutes=minutes)
