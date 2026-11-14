# Databricks notebook source
# MAGIC %md
# MAGIC # Lantern agent job
# MAGIC
# MAGIC One scheduled run of the autonomous disruption agent, on Databricks serverless compute:
# MAGIC
# MAGIC 1. **Ingest** the TfL road disruption feed into `bronze_tfl_road_disruptions` (raw JSON, append-only).
# MAGIC 2. **Validate** and normalise every record into `silver_disruptions` (schema checks, timestamps, geometry, in-area flag).
# MAGIC 3. **Decide**: Claude judges what each in-area record means for pedestrians; deterministic code verifies the
# MAGIC    quoted evidence, matches the location to the walking network and reruns baseline journeys.
# MAGIC 4. **Publish** a new immutable row in `overlay_versions` (+ `overlay_entries`) only if every gate passes, then
# MAGIC    export the current version to the `published` volume, which is what the mobile app reads.
# MAGIC
# MAGIC A failed run writes an `agent_runs` row and leaves the previous overlay version in place.

# COMMAND ----------

# MAGIC %pip install -q anthropic shapely

# COMMAND ----------

dbutils.library.restartPython()  # noqa: F821

# COMMAND ----------

import json
import os
import re
import sys
from datetime import datetime, timezone

dbutils.widgets.dropdown("mode", "live", ["live", "replay"], "Feed")          # noqa: F821
dbutils.widgets.dropdown("replay_step", "1", ["1", "2"], "Replay step")       # noqa: F821
dbutils.widgets.text("catalog", "workspace", "Catalog")                       # noqa: F821
dbutils.widgets.text("schema", "lantern", "Schema")                           # noqa: F821

MODE = dbutils.widgets.get("mode")                # noqa: F821
STEP = dbutils.widgets.get("replay_step")         # noqa: F821
CATALOG = dbutils.widgets.get("catalog")          # noqa: F821
SCHEMA = dbutils.widgets.get("schema")            # noqa: F821
NS = f"{CATALOG}.{SCHEMA}"
VOLUME = f"/Volumes/{CATALOG}/{SCHEMA}/published"

os.environ["LANTERN_VAR_DIR"] = "/tmp/lantern_var"

# Code and data ship as one zip in a Unity Catalog volume (uploaded by
# scripts/databricks_deploy.py). Extract it to local disk and import from there.
import time
import zipfile

LOCAL_ROOT = "/tmp/lantern_app"
BUNDLE = f"/Volumes/{CATALOG}/{SCHEMA}/code/lantern_bundle.zip"
for attempt in range(1, 5):
    try:
        with zipfile.ZipFile(BUNDLE) as z:
            z.extractall(LOCAL_ROOT)
            BUNDLE_INFO = json.loads(z.read("BUNDLE_INFO.json"))
        break
    except OSError as exc:
        if attempt == 4:
            raise
        print(f"reading {BUNDLE} failed ({exc}); retry {attempt}/3")
        time.sleep(3 * attempt)
print("code bundle:", BUNDLE_INFO)
if LOCAL_ROOT not in sys.path:
    sys.path.insert(0, LOCAL_ROOT)

from lantern import tfl  # noqa: E402
from lantern.agent import run as agent_run  # noqa: E402
from lantern.agent.assess import claude_assessor  # noqa: E402
from lantern.agent.replay import replay_feed  # noqa: E402
from lantern.graph import load_graph  # noqa: E402
from lantern.overlay import parse_ts  # noqa: E402

print(f"mode={MODE} step={STEP} namespace={NS} volume={VOLUME}")

# COMMAND ----------

# MAGIC %md ## Tables and volume

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {NS}")                    # noqa: F821
spark.sql(f"CREATE VOLUME IF NOT EXISTS {NS}.published")          # noqa: F821

DDL = {
    "bronze_tfl_road_disruptions":
        "run_id STRING, fetched_at TIMESTAMP, mode STRING, record_id STRING, synthetic BOOLEAN, raw_json STRING",
    "silver_disruptions":
        "run_id STRING, fetched_at TIMESTAMP, record_id STRING, valid BOOLEAN, reject_reason STRING, "
        "in_area BOOLEAN, synthetic BOOLEAN, category STRING, sub_category STRING, severity STRING, "
        "status STRING, location STRING, comments STRING, current_update STRING, has_closures BOOLEAN, "
        "start_ts TIMESTAMP, end_ts TIMESTAMP, modified_ts TIMESTAMP, lon DOUBLE, lat DOUBLE, "
        "n_lines INT, n_polygons INT",
    "agent_decisions":
        "run_id STRING, decided_at TIMESTAMP, event_id STRING, decision STRING, reason STRING, "
        "action STRING, evidence STRING, assessor STRING, n_segments INT, edge_ids ARRAY<STRING>, "
        "assessment_json STRING, match_json STRING, source_url STRING, source_modified STRING, location STRING",
    "agent_runs":
        "run_id STRING, mode STRING, status STRING, started_at TIMESTAMP, finished_at TIMESTAMP, "
        "version_before INT, version_after INT, records INT, in_area INT, summary STRING, error STRING, "
        "counts_json STRING, checks_json STRING, assessor STRING",
    "overlay_versions":
        "version INT, parent_version INT, published_at TIMESTAMP, run_id STRING, mode STRING, "
        "entry_count INT, doc_json STRING",
    "overlay_entries":
        "version INT, entry_id STRING, action STRING, street STRING, n_segments INT, length_m DOUBLE, "
        "valid_from TIMESTAMP, valid_to TIMESTAMP, evidence STRING, summary STRING, source_url STRING, "
        "edge_ids ARRAY<STRING>",
}
for name, cols in DDL.items():
    spark.sql(f"CREATE TABLE IF NOT EXISTS {NS}.{name} ({cols}) USING DELTA")   # noqa: F821

spark.sql(f"""
CREATE OR REPLACE VIEW {NS}.current_overlay_entries AS
SELECT e.* FROM {NS}.overlay_entries e
WHERE e.version = (SELECT max(version) FROM {NS}.overlay_versions)
""")  # noqa: F821


def append(table: str, rows: list[dict]) -> None:
    if rows:
        cols = [c.strip().split(" ")[0] for c in DDL[table].split(", ")]
        data = [tuple(r.get(c) for c in cols) for r in rows]
        spark.createDataFrame(data, schema=DDL[table]).write.mode("append").saveAsTable(f"{NS}.{table}")  # noqa: F821


def write_volume(name: str, obj) -> None:
    path = f"{VOLUME}/{name}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    try:
        os.replace(tmp, path)
    except OSError:                      # volumes without rename support: plain overwrite
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        try:
            os.remove(tmp)
        except OSError:
            pass


def read_volume(name: str, default=None):
    try:
        with open(f"{VOLUME}/{name}", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        return default

# COMMAND ----------

# MAGIC %md ## Store: Delta tables are the system of record, the volume is the serving copy

# COMMAND ----------


class DeltaStore:
    kind = "databricks"

    def read_published(self):
        rows = spark.sql(  # noqa: F821
            f"SELECT doc_json FROM {NS}.overlay_versions ORDER BY version DESC LIMIT 1").collect()
        return json.loads(rows[0]["doc_json"]) if rows else None

    def publish(self, overlay: dict) -> None:
        append("overlay_versions", [{
            "version": overlay["version"], "parent_version": overlay.get("parent_version"),
            "published_at": parse_ts(overlay["published_at"]), "run_id": overlay["run_id"],
            "mode": overlay["mode"], "entry_count": len(overlay["entries"]),
            "doc_json": json.dumps(overlay),
        }])
        append("overlay_entries", [{
            "version": overlay["version"], "entry_id": en["entry_id"], "action": en["action"],
            "street": en.get("street"), "n_segments": len(en["edge_ids"]),
            "length_m": float(en.get("length_m") or 0), "valid_from": parse_ts(en.get("valid_from")),
            "valid_to": parse_ts(en.get("valid_to")), "evidence": en.get("evidence"),
            "summary": en.get("summary"), "source_url": en.get("source_url"),
            "edge_ids": en["edge_ids"],
        } for en in overlay["entries"]])
        # Serving copy last: the app only ever sees a version that is already in Delta.
        write_volume("overlay_latest.json", overlay)

    def read_state(self) -> dict:
        return read_volume("state/assessments.json", {}) or {}

    def write_state(self, state: dict) -> None:
        write_volume("state/assessments.json", state)

    def read_heartbeat(self):
        return read_volume("heartbeat.json")

    def record_run(self, run: dict, decisions: list, raw_records=None, events=None) -> None:
        fetched = parse_ts(run["feed"].get("fetched_at")) or parse_ts(run["started_at"])
        if raw_records:
            append("bronze_tfl_road_disruptions", [{
                "run_id": run["run_id"], "fetched_at": fetched, "mode": run["mode"],
                "record_id": str(r.get("id")), "synthetic": bool(r.get("_synthetic")),
                "raw_json": json.dumps(r),
            } for r in raw_records])
            silver = []
            for r in raw_records:
                ev, why = tfl.normalise(r)
                row = {"run_id": run["run_id"], "fetched_at": fetched, "record_id": str(r.get("id")),
                       "valid": ev is not None, "reject_reason": why,
                       "synthetic": bool(r.get("_synthetic"))}
                if ev:
                    row.update(
                        in_area=tfl.in_area(ev), category=ev["category"], sub_category=ev["sub_category"],
                        severity=ev["severity"], status=ev["status"], location=ev["location"],
                        comments=ev["comments"], current_update=ev["current_update"],
                        has_closures=ev["has_closures"], start_ts=parse_ts(ev["start"]),
                        end_ts=parse_ts(ev["end"]), modified_ts=parse_ts(ev["modified"]),
                        lon=ev["point"][0] if ev["point"] else None,
                        lat=ev["point"][1] if ev["point"] else None,
                        n_lines=len(ev["lines"]), n_polygons=len(ev["polygons"]))
                silver.append(row)
            append("silver_disruptions", silver)
        append("agent_decisions", [{
            "run_id": d["run_id"], "decided_at": parse_ts(d["decided_at"]), "event_id": d.get("event_id"),
            "decision": d["decision"], "reason": d.get("reason"), "action": d.get("action"),
            "evidence": d.get("evidence"), "assessor": d.get("assessor"),
            "n_segments": len(d.get("edge_ids") or []), "edge_ids": d.get("edge_ids") or [],
            "assessment_json": json.dumps(d.get("assessment")) if d.get("assessment") else None,
            "match_json": json.dumps(d.get("match")) if d.get("match") else None,
            "source_url": d.get("source_url"), "source_modified": d.get("source_modified"),
            "location": d.get("location"),
        } for d in decisions])
        append("agent_runs", [{
            "run_id": run["run_id"], "mode": run["mode"], "status": run["status"],
            "started_at": parse_ts(run["started_at"]), "finished_at": parse_ts(run["finished_at"]),
            "version_before": run["version_before"], "version_after": run["version_after"],
            "records": run["feed"].get("records"), "in_area": run["feed"].get("in_area"),
            "summary": run.get("summary"), "error": run.get("error"),
            "counts_json": json.dumps(run.get("counts")), "checks_json": json.dumps(run.get("checks")),
            "assessor": run.get("assessor"),
        }])
        slim = {k: v for k, v in run.items() if k != "traceback"}
        write_volume("heartbeat.json", {
            "run_id": run["run_id"], "status": run["status"], "mode": run["mode"],
            "finished_at": run["finished_at"], "version": run["version_after"],
            "summary": run.get("summary")})
        write_volume("last_run.json", {"run": slim, "decisions": decisions})
        recent = [slim] + (read_volume("runs_recent.json", []) or [])
        write_volume("runs_recent.json", recent[:60])
        # Keep the serving copy in step with Delta even when nothing new was published.
        if read_volume("overlay_latest.json") is None:
            current = self.read_published()
            if current:
                write_volume("overlay_latest.json", current)

# COMMAND ----------

# MAGIC %md ## Run the agent

# COMMAND ----------

# The Claude key lives in the `lantern` secret scope. It is passed straight to the client
# and never printed. Terminal pastes sometimes wrap a secret in control characters, so the
# key is extracted by its pattern.
_secret = dbutils.secrets.get(scope="lantern", key="anthropic_api_key")  # noqa: F821
_m = re.search(r"sk-ant-[A-Za-z0-9_\-]+", _secret or "")
if not _m:
    raise RuntimeError("Secret lantern/anthropic_api_key does not contain a Claude API key")
assessor = claude_assessor(api_key=_m.group(0))
del _secret, _m

g = load_graph()
store = DeltaStore()
now = datetime.now(timezone.utc)
feed = replay_feed(now, STEP) if MODE == "replay" else agent_run.live_feed
result = agent_run.execute(g, store, feed, assessor=assessor, now=now, mode=MODE)
run = result["run"]

print(f"{run['run_id']}  mode={run['mode']}  status={run['status']}  "
      f"v{run['version_before']} -> v{run['version_after']}  assessor={run['assessor']}")
print(run["summary"], "|", run.get("error") or "")
for d in result["decisions"]:
    if d["decision"] != "reject_invalid_record":
        print(f"  {str(d['event_id']):<14} {d['decision']:<26} {(d['reason'] or '')[:140]}")

# COMMAND ----------

# MAGIC %md ## What this run decided, and what is published now

# COMMAND ----------

display(spark.sql(f"""
SELECT event_id, decision, action, n_segments, evidence, reason
FROM {NS}.agent_decisions WHERE run_id = '{run['run_id']}' AND decision <> 'reject_invalid_record'
ORDER BY decision
"""))  # noqa: F821

# COMMAND ----------

display(spark.sql(f"SELECT version, entry_id, action, street, n_segments, length_m, valid_to, evidence FROM {NS}.current_overlay_entries"))  # noqa: F821

# COMMAND ----------

display(spark.sql(f"""
SELECT run_id, mode, status, version_before, version_after, records, in_area, finished_at, summary, error
FROM {NS}.agent_runs ORDER BY finished_at DESC LIMIT 20
"""))  # noqa: F821

# COMMAND ----------

summary = {k: run.get(k) for k in ("run_id", "mode", "status", "version_before", "version_after",
                                    "summary", "error", "counts", "assessor")}
if run["status"] == "failed":
    # Surface the failure to the Jobs UI. The audit row and heartbeat are already written and
    # the previous overlay version is still the published one.
    raise RuntimeError("Lantern agent run failed: " + json.dumps(summary))
dbutils.notebook.exit(json.dumps(summary))  # noqa: F821
