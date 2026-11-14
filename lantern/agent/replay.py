"""Replay feed: the captured live TfL feed plus labelled synthetic records.

Used for the repeatable demo and the tests. A replay run goes through exactly the same
code as a live run; only the feed differs, and the published overlay is marked
mode="replay" so the app can show it is not live data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..config import DATA_FIXTURES

SCENARIO = DATA_FIXTURES / "replay_scenario.json"
CAPTURE = DATA_FIXTURES / "tfl_road_disruptions_capture.json"


def materialise(rec: dict, now: datetime) -> dict:
    out = {k: v for k, v in rec.items() if not k.startswith("_") or k == "_synthetic"}
    start = now + timedelta(hours=rec["_start_offset_h"])
    end = now + timedelta(hours=rec["_end_offset_h"])
    out["startDateTime"] = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["endDateTime"] = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["lastModifiedTime"] = (now - timedelta(minutes=20)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return out


def replay_records(now: datetime, step: str = "1", include_capture: bool = True,
                   scenario_path=SCENARIO, capture_path=CAPTURE) -> tuple[list[dict], dict]:
    sc = json.loads(scenario_path.read_text(encoding="utf-8"))
    wanted = set(sc["steps"][str(step)])
    synthetic = [materialise(r, now) for r in sc["records"] if r["id"] in wanted]
    real = []
    captured_at = None
    if include_capture and capture_path.exists():
        cap = json.loads(capture_path.read_text(encoding="utf-8"))
        real = cap["records"]
        captured_at = cap["_meta"]["fetched_at"]
    meta = {
        "url": f"replay:step{step}", "http_status": None,
        "fetched_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "records": len(real) + len(synthetic),
        "synthetic_records": len(synthetic), "captured_records": len(real),
        "capture_fetched_at": captured_at,
    }
    return real + synthetic, meta


def replay_feed(now: datetime, step: str = "1", include_capture: bool = True):
    def feed():
        return replay_records(now, step, include_capture)
    return feed
