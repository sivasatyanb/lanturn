"""Build data/processed/graph.json.gz from the raw OSM extract."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lantern.graph import GRAPH_PATH, build  # noqa: E402

g = build()
g.save()
print(json.dumps({k: v for k, v in g.meta.items() if k != "stations"}, indent=1))
print("stations:", len(g.meta["stations"]))
print("saved", GRAPH_PATH, f"{GRAPH_PATH.stat().st_size/1e6:.2f} MB")
