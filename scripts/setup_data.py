"""Reproducible data setup: one command rebuilds everything the app needs.

  python scripts/setup_data.py            fetch OSM, build the graph, regenerate the replay fixture
  python scripts/setup_data.py --offline  rebuild from the existing raw extract (no network)
  python scripts/setup_data.py --capture  also capture today's TfL feed for replay/evaluation

The repository already contains the built graph (data/processed/graph.json.gz), so the
app runs without this step. Rebuilding changes edge ids only if OpenStreetMap changed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def step(*args: str) -> None:
    print(">", " ".join(args), flush=True)
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--capture", action="store_true")
    a = ap.parse_args()
    if not a.offline:
        step("scripts/fetch_osm.py")
    step("scripts/build_graph.py")
    step("scripts/make_replay_fixture.py")
    if a.capture:
        from lantern import tfl
        from lantern.config import DATA_FIXTURES
        raw, meta = tfl.fetch_raw()
        (DATA_FIXTURES / "tfl_road_disruptions_capture.json").write_text(
            json.dumps({"_meta": meta, "records": raw}), encoding="utf-8")
        print(f"captured {len(raw)} TfL records at {meta['fetched_at']}")
    print("done. Sources and licences: docs/DATA.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
