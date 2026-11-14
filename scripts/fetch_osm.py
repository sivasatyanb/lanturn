"""Download the raw OpenStreetMap extract for the demo area via the Overpass API.

Output: data/raw/osm_extract.json (git-ignored; reproducible with this script).
Data (c) OpenStreetMap contributors, ODbL 1.0.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lantern.config import BBOX, DATA_RAW, USER_AGENT  # noqa: E402

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

S, W, N, E = BBOX
B = f"{S},{W},{N},{E}"
QUERY = f"""
[out:json][timeout:120];
(
  way["highway"]({B});
  node["highway"="street_lamp"]({B});
  node["highway"="crossing"]({B});
  node["barrier"]({B});
  node["railway"~"station|subway_entrance"]({B});
  node["amenity"~"pub|bar|restaurant|fast_food|cafe|nightclub|convenience|pharmacy|police|hospital|theatre|cinema"]({B});
  node["shop"~"convenience|supermarket"]({B});
  way["leisure"~"park|garden"]["name"]({B});
  relation["leisure"~"park|garden"]["name"]({B});
);
out body;
>;
out skel qt;
"""


def main() -> int:
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    out = DATA_RAW / "osm_extract.json"
    last_err = None
    for url in ENDPOINTS:
        for attempt in range(2):
            try:
                print(f"POST {url} (attempt {attempt + 1})", flush=True)
                r = requests.post(url, data={"data": QUERY},
                                  headers={"User-Agent": USER_AGENT}, timeout=180)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    print("  ", last_err, flush=True)
                    time.sleep(5)
                    continue
                data = r.json()
                data["_lantern"] = {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "endpoint": url, "bbox": BBOX,
                    "osm_base": data.get("osm3s", {}).get("timestamp_osm_base"),
                }
                out.write_text(json.dumps(data), encoding="utf-8")
                kinds = {}
                for el in data["elements"]:
                    kinds[el["type"]] = kinds.get(el["type"], 0) + 1
                print(f"saved {out} ({out.stat().st_size/1e6:.1f} MB) {kinds} osm_base={data['_lantern']['osm_base']}")
                return 0
            except Exception as exc:  # network errors: try the next mirror
                last_err = repr(exc)
                print("  ", last_err, flush=True)
                time.sleep(5)
    print("FAILED:", last_err)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
