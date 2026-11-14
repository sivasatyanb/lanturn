"""Static configuration: demo area, places, constants. No secrets live here."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
DATA_FIXTURES = ROOT / "data" / "fixtures"
VAR_DIR = Path(os.environ.get("LANTERN_VAR_DIR", ROOT / "var"))

# Demo area: Imperial South Kensington campus <-> Wilson House (Paddington),
# including Hyde Park / Kensington Gardens and the streets around them.
# (south, west, north, east) in WGS84 degrees.
BBOX = (51.4900, -0.2000, 51.5240, -0.1500)

# Outbound HTTP identity. Generic on purpose: no personal details.
USER_AGENT = "ImperialHackathonWalkingRoutes/0.1"
HTTP_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# Walking speed used to turn metres into seconds (1.34 m/s ~ 4.8 km/h,
# the conventional free-flow adult walking speed used in pedestrian planning).
WALK_SPEED_MPS = 1.34

PLACES = {
    "imperial_south_ken": {
        "name": "Imperial College, South Ken",
        "short": "Imperial (Exhibition Rd)",
        "lat": 51.49870, "lon": -0.17455,
    },
    "wilson_house": {
        "name": "Wilson House, Sussex Gardens",
        "short": "Wilson House",
        "lat": 51.51722, "lon": -0.17105,
    },
    "south_ken_station": {
        "name": "South Kensington station",
        "short": "South Ken station",
        "lat": 51.49412, "lon": -0.17390,
    },
    "paddington_station": {
        "name": "Paddington station",
        "short": "Paddington",
        "lat": 51.51540, "lon": -0.17550,
    },
    "queensway": {
        "name": "Queensway station",
        "short": "Queensway",
        "lat": 51.51045, "lon": -0.18720,
    },
    "gloucester_road": {
        "name": "Gloucester Road station",
        "short": "Gloucester Rd",
        "lat": 51.49440, "lon": -0.18290,
    },
    "marble_arch": {
        "name": "Marble Arch",
        "short": "Marble Arch",
        "lat": 51.51320, "lon": -0.15890,
    },
    "high_st_ken": {
        "name": "High Street Kensington station",
        "short": "High St Ken",
        "lat": 51.50090, "lon": -0.19250,
    },
}

DEFAULT_ORIGIN = "imperial_south_ken"
DEFAULT_DESTINATION = "wilson_house"

# Baseline test journeys rerun before any overlay is published (see agent/validate.py).
BASELINE_JOURNEYS = [
    ("imperial_south_ken", "wilson_house"),
    ("wilson_house", "imperial_south_ken"),
    ("south_ken_station", "paddington_station"),
    ("imperial_south_ken", "queensway"),
    ("gloucester_road", "wilson_house"),
    ("imperial_south_ken", "marble_arch"),
    ("high_st_ken", "paddington_station"),
    ("imperial_south_ken", "gloucester_road"),
]
