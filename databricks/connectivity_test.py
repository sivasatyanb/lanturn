# Databricks notebook source
# Connectivity and environment probe for serverless compute. Sends no credentials.
import json
import sys

import requests

out = {"python": sys.version.split()[0]}
probes = {
    # An unauthenticated POST should come back 401 from Anthropic if egress works.
    "anthropic_api": ("POST", "https://api.anthropic.com/v1/messages"),
    "tfl_road_disruption": ("GET", "https://api.tfl.gov.uk/Road/all/Disruption?stripContent=true"),
}
headers = {"User-Agent": "ImperialHackathonWalkingRoutes/0.1", "Accept": "application/json"}
for name, (method, url) in probes.items():
    try:
        r = requests.request(method, url, headers=headers, json={} if method == "POST" else None, timeout=20)
        out[name] = {"status": r.status_code, "bytes": len(r.content)}
    except Exception as exc:  # noqa: BLE001
        out[name] = {"error": repr(exc)[:300]}

for mod in ("shapely", "anthropic", "numpy", "pandas"):
    try:
        m = __import__(mod)
        out[f"import_{mod}"] = getattr(m, "__version__", "ok")
    except Exception as exc:  # noqa: BLE001
        out[f"import_{mod}"] = "missing: " + repr(exc)[:80]

dbutils.notebook.exit(json.dumps(out))  # noqa: F821
