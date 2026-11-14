"""Versioned routing overlay: the contract between the agent and the app.

An overlay version is an immutable JSON document:

{
  "schema": 1,
  "version": 7,                    # monotonically increasing integer
  "parent_version": 6,
  "published_at": "2026-11-14T22:30:05+00:00",
  "run_id": "run-20261114T223001Z",
  "mode": "live" | "replay",       # replay = fixture-driven demonstration, never mixed silently
  "entries": [
    {
      "entry_id": "TIMS-12345",    # stable id of the source event
      "source": "tfl_road_disruption",
      "source_url": "https://api.tfl.gov.uk/Road/all/Disruption/TIMS-12345",
      "action": "close" | "restrict",
      "edge_ids": ["4082536:3", ...],
      "street": "Exhibition Road",
      "summary": "Footway closed outside ...",
      "evidence": "verbatim quote from the source text",
      "valid_from": "...", "valid_to": "...",   # ISO 8601, tz-aware
      "decided_at": "...",
      "source_modified": "..."
    }
  ],
  "checks": {...}                  # regression results that gated publication
}

"close" removes the segments from every route. "restrict" keeps them walkable but
adds the `disrupted` penalty to the preferred route.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA = 1
ACTIONS = ("close", "restrict")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def empty_overlay() -> dict[str, Any]:
    return {"schema": SCHEMA, "version": 0, "parent_version": None, "published_at": None,
            "run_id": None, "mode": "live", "entries": [], "checks": {}}


def entry_active(entry: dict, when: datetime) -> bool:
    start = parse_ts(entry.get("valid_from"))
    end = parse_ts(entry.get("valid_to"))
    if start and when < start:
        return False
    if end and when >= end:
        return False
    return True


def active_entries(overlay: dict | None, when: datetime) -> list[dict]:
    if not overlay:
        return []
    return [e for e in overlay.get("entries", []) if entry_active(e, when)]


def validate_overlay(overlay: dict, known_edge_ids=None) -> list[str]:
    """Structural validation. Returns a list of problems (empty = valid)."""
    problems = []
    if overlay.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    if not isinstance(overlay.get("version"), int) or overlay["version"] < 0:
        problems.append("version must be a non-negative integer")
    seen = set()
    for i, en in enumerate(overlay.get("entries", [])):
        tag = f"entries[{i}]"
        for key in ("entry_id", "source", "action", "edge_ids", "evidence", "valid_to"):
            if not en.get(key):
                problems.append(f"{tag}: missing {key}")
        if en.get("action") not in ACTIONS:
            problems.append(f"{tag}: action must be one of {ACTIONS}")
        if en.get("entry_id") in seen:
            problems.append(f"{tag}: duplicate entry_id {en.get('entry_id')}")
        seen.add(en.get("entry_id"))
        try:
            vf, vt = parse_ts(en.get("valid_from")), parse_ts(en.get("valid_to"))
            if vf and vt and vt <= vf:
                problems.append(f"{tag}: valid_to is not after valid_from")
        except ValueError:
            problems.append(f"{tag}: unparseable validity timestamps")
        if known_edge_ids is not None:
            unknown = [e for e in en.get("edge_ids", []) if e not in known_edge_ids]
            if unknown:
                problems.append(f"{tag}: {len(unknown)} edge ids not in graph")
    return problems
