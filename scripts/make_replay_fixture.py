"""Generate data/fixtures/replay_scenario.json: SYNTHETIC TfL-style records for the replay demo.

These records are NOT real disruptions. They are written in the phrasing TfL uses in the
live feed (see data/fixtures/tfl_road_disruptions_capture.json for real examples) and are
placed on real streets in the demo area so that each branch of the agent can be shown
and tested on demand. Every record id starts with "REPLAY-" and carries "_synthetic": true.

Start/end times are stored as hour offsets and materialised when the replay runs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lantern.agent.match import _junction, street_edges  # noqa: E402
from lantern.config import DATA_FIXTURES  # noqa: E402
from lantern.geo import to_lonlat  # noqa: E402
from lantern.graph import load_graph  # noqa: E402

g = load_graph()


def junction(street: str, cross: str) -> list[float]:
    j = _junction(g, street_edges(g, street), street_edges(g, cross), None)
    if j is None:
        raise SystemExit(f"no junction {street} x {cross}")
    lon, lat = to_lonlat(*j)
    return [round(lon, 6), round(lat, 6)]


def midpoint(a: list[float], b: list[float]) -> list[float]:
    return [round((a[0] + b[0]) / 2, 6), round((a[1] + b[1]) / 2, 6)]


def rec(rid, location, comments, update, point, start_h, end_h, *, category="Works",
        sub="Utility works", severity="Moderate", status="Active", closures=False, expect=None):
    return {
        "id": rid, "_synthetic": True, "_expect": expect,
        "_start_offset_h": start_h, "_end_offset_h": end_h,
        "category": category, "subCategory": sub, "severity": severity, "status": status,
        "location": location, "comments": comments, "currentUpdate": update,
        "point": json.dumps(point), "geography": {"type": "Point", "coordinates": point},
        "hasClosures": closures, "isProvisional": False, "levelOfInterest": "Medium",
    }


ex_a = junction("Exhibition Road", "Prince Consort Road")
ex_b = junction("Exhibition Road", "Kensington Road")

records = [
    # 1. Relevant and explicit: closes a stretch on the demo route.
    rec("REPLAY-001", "EXHIBITION ROAD (SW7 ) (Westminster)",
        "Exhibition Road (Both directions) between Prince Consort Road and Kensington Road - "
        "The road, cycle lane and both footways are closed due to a burst water main. "
        "There is no pedestrian access past the site.",
        "Thames Water are on site. Repairs are expected to continue overnight.",
        midpoint(ex_a, ex_b), -1, 10, sub="Emergency works", severity="Serious", closures=True,
        expect="publish_close"),
    # 2. Vehicle-only closure: must not change walking routes.
    rec("REPLAY-002", "[A4] KENSINGTON ROAD (SW7 ) (Westminster)",
        "[A4] Kensington Road (Eastbound) at the junction of Queen's Gate - "
        "The road is closed eastbound for emergency gas works. Traffic is being diverted via Queen's Gate.",
        "Delays are possible.",
        junction("Kensington Road", "Queen's Gate"), -2, 20, closures=True, severity="Serious",
        expect="ignore_not_pedestrian"),
    # 3. Suggestive but not explicit: held back.
    rec("REPLAY-003", "PRAED STREET (W2 ) (Westminster)",
        "Praed Street (Both directions) at the junction of London Street - "
        "Lane one is closed for crane operations at a building site. Pedestrians should take care.",
        "Delays are possible.",
        junction("Praed Street", "London Street"), -3, 30,
        expect="reject_uncertain|ignore_not_pedestrian"),
    # 4. Explicit but already over: stale.
    rec("REPLAY-004", "GLOUCESTER ROAD (SW7 ) (Kensington & Chelsea)",
        "Gloucester Road (Both directions) at the junction of Cromwell Road - "
        "The footway is closed due to scaffolding works.",
        "Works are complete.",
        junction("Gloucester Road", "Cromwell Road"), -30, -6, expect="reject_stale"),
    # 5. Text and coordinates disagree: location cannot be verified.
    rec("REPLAY-005", "[A402] BAYSWATER ROAD (W2 ) (Westminster)",
        "[A402] Bayswater Road (Westbound) at the junction of Lancaster Terrace - "
        "The footway is closed due to a collapsed manhole cover.",
        "Engineers are on site.",
        junction("Edgware Road", "Sussex Gardens"), -1, 12, expect="reject_location"),
    # 6. Explicit restriction near the destination: penalised, still walkable.
    rec("REPLAY-006", "SUSSEX GARDENS (W2 ) (Westminster)",
        "Sussex Gardens (Westbound) at the junction of Southwick Street - "
        "The footway is restricted due to Thames Water works.",
        "Delays are possible.",
        junction("Sussex Gardens", "Southwick Street"), -4, 40, severity="Minimal",
        expect="publish_restrict"),
    # 7. Newer update contradicts the older comment: superseded.
    rec("REPLAY-007", "QUEEN'S GATE (SW7 ) (Kensington & Chelsea)",
        "Queen's Gate (Northbound) at the junction of Prince Consort Road - "
        "The footway is closed due to a fallen tree.",
        "The tree has been removed and the footway has now reopened.",
        junction("Queen's Gate", "Prince Consort Road"), -5, 3, sub="Hazard", category="Hazard(s)",
        expect="ignore_cleared"),
    # 8. Station closure: says nothing about the street outside.
    rec("REPLAY-008", "[A402] BAYSWATER ROAD (W2 ) (Westminster)",
        "[A402] Bayswater Road (Both directions) at the junction of Lancaster Terrace - "
        "Lancaster Gate Underground station is closed for lift replacement works. "
        "Road traffic is not affected.",
        "No reported delays.",
        junction("Bayswater Road", "Lancaster Terrace"), -48, 200, category="Planned events",
        sub="Station closure", severity="Minimal", expect="ignore_not_pedestrian"),
]

out = {
    "_about": "SYNTHETIC records for the Lantern replay demo. Not real TfL disruptions. "
              "See scripts/make_replay_fixture.py.",
    "steps": {
        "1": [r["id"] for r in records],
        # Step 2: the burst main is fixed and drops out of the feed -> entry withdrawn.
        "2": [r["id"] for r in records if r["id"] != "REPLAY-001"],
    },
    "records": records,
}
path = DATA_FIXTURES / "replay_scenario.json"
path.write_text(json.dumps(out, indent=1), encoding="utf-8")
print("wrote", path, len(records), "records")
for r in records:
    print(" ", r["id"], r["point"], "->", r["_expect"])
