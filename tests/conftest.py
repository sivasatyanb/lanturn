"""Shared fixtures. Tests never call the network: the feed is a fixture and the judgment
step is a stub that returns the assessments Claude gave for the same records (recorded
14 Nov 2026), so the deterministic pipeline around the model is what is under test."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lantern.access import LONDON  # noqa: E402
from lantern.graph import load_graph  # noqa: E402
from lantern.store import LocalStore  # noqa: E402

NIGHT = datetime(2026, 11, 14, 22, 30, tzinfo=LONDON)        # Hyde Park open, Kensington Gardens shut
LATE = datetime(2026, 11, 15, 0, 30, tzinfo=LONDON)          # both parks shut
NOW = datetime(2026, 11, 14, 21, 0, tzinfo=timezone.utc)     # "current time" for agent runs


def _a(impact="none", basis="none", quote="", blocks="unknown", street="", extent="unknown",
       cross=(), side="unknown", cleared=False, why="stub"):
    return {"pedestrian_impact": impact, "basis": basis, "evidence_quote": quote,
            "blocks_all_pedestrian_passage": blocks, "street": street, "extent": extent,
            "cross_streets": list(cross), "side": side, "superseded_or_cleared": cleared,
            "reasoning": why, "_assessor": "stub"}


CANNED = {
    "REPLAY-001": _a("footway_closed", "explicit", "both footways are closed", "yes",
                     "Exhibition Road", "between", ("Prince Consort Road", "Kensington Road")),
    "REPLAY-002": _a(street="Kensington Road", extent="junction", cross=("Queen's Gate",)),
    "REPLAY-003": _a("unclear", "inferred", "Pedestrians should take care.", "unknown",
                     "Praed Street", "junction", ("London Street",)),
    "REPLAY-004": _a("footway_closed", "explicit", "The footway is closed", "no",
                     "Gloucester Road", "junction", ("Cromwell Road",)),
    "REPLAY-005": _a("footway_closed", "explicit", "The footway is closed", "no",
                     "Bayswater Road", "junction", ("Lancaster Terrace",)),
    "REPLAY-006": _a("footway_restricted", "explicit",
                     "The footway is restricted due to Thames Water works.", "no",
                     "Sussex Gardens", "junction", ("Southwick Street",)),
    "REPLAY-007": _a("footway_closed", "explicit", "The footway is closed", "no",
                     "Queen's Gate", "junction", ("Prince Consort Road",), cleared=True),
    "REPLAY-008": _a(street="Bayswater Road", extent="junction", cross=("Lancaster Terrace",)),
}


def stub_assessor(ev: dict) -> dict:
    return dict(CANNED.get(ev["id"], _a()))


@pytest.fixture(scope="session")
def g():
    return load_graph()


@pytest.fixture()
def store(tmp_path):
    return LocalStore(tmp_path / "store")


@pytest.fixture()
def assessor():
    return stub_assessor
