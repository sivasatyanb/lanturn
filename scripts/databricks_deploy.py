"""Deploy and operate the Lantern agent on Databricks.

  python scripts/databricks_deploy.py deploy            upload code + data, create/update the job (paused)
  python scripts/databricks_deploy.py deploy --unpause  ...and leave the 30-minute schedule running
  python scripts/databricks_deploy.py run [--wait]      trigger one live run now
  python scripts/databricks_deploy.py pause | unpause
  python scripts/databricks_deploy.py status            last run + what the app would read

Demo (replay) path, isolated from the live job, tables and volume:
  python scripts/databricks_deploy.py deploy-demo       create schema lantern_demo + job lantern-agent-demo
  python scripts/databricks_deploy.py run --replay 1|2 [--wait]     replay on the demo job only
  python scripts/databricks_deploy.py status --demo

Prerequisites (once):
  databricks auth login --host <workspace-url> --profile walknight
  databricks secrets create-scope lantern --profile walknight
  databricks secrets put-secret lantern anthropic_api_key --profile walknight
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lantern import databricks_ops as ops  # noqa: E402
from lantern.env import load_env  # noqa: E402
from lantern.store import DatabricksVolumeStore  # noqa: E402

CATALOG, SCHEMA = "workspace", "lantern"
BUNDLE_LOCAL = ROOT / "var" / "lantern_bundle.zip"
BUNDLE_REMOTE = f"dbfs:/Volumes/{CATALOG}/{SCHEMA}/code/lantern_bundle.zip"


def build_bundle() -> dict:
    """Zip the lantern package and the data it needs. One file = one atomic upload."""
    import hashlib
    import zipfile
    from datetime import datetime, timezone

    files = sorted(p for p in (ROOT / "lantern").rglob("*.py"))
    files += [ROOT / "data/processed/graph.json.gz"]
    files += sorted((ROOT / "data/fixtures").glob("*.json"))
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes())
    info = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sha256": h.hexdigest()[:16], "files": len(files)}
    BUNDLE_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE_LOCAL, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(ROOT).as_posix())
        z.writestr("BUNDLE_INFO.json", json.dumps(info))
    return info


def cli(*args: str) -> str:
    store = DatabricksVolumeStore()
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    out = subprocess.run([store._cli(), *args, "--profile", store.profile],
                         capture_output=True, text=True, env=env, cwd=ROOT)
    if out.returncode != 0:
        raise SystemExit(f"databricks {' '.join(args[:3])} failed:\n{out.stderr.strip()[:800]}")
    return out.stdout


def cli_ok(*args: str) -> bool:
    store = DatabricksVolumeStore()
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    out = subprocess.run([store._cli(), *args, "--profile", store.profile],
                         capture_output=True, text=True, env=env, cwd=ROOT)
    return out.returncode == 0 or "already exists" in (out.stderr + out.stdout).lower()


def deploy(unpause: bool) -> None:
    if not cli_ok("schemas", "create", SCHEMA, CATALOG):
        raise SystemExit(f"could not create schema {CATALOG}.{SCHEMA}")
    for vol in ("code", "published"):
        if not cli_ok("volumes", "create", CATALOG, SCHEMA, vol, "MANAGED"):
            raise SystemExit(f"could not create volume {CATALOG}.{SCHEMA}.{vol}")
    info = build_bundle()
    print(f"bundle {info} -> {BUNDLE_REMOTE}")
    cli("fs", "cp", str(BUNDLE_LOCAL), BUNDLE_REMOTE, "--overwrite")
    cli("workspace", "mkdirs", ops.WORKSPACE_ROOT)
    print(f"upload notebook -> {ops.NOTEBOOK_PATH}")
    cli("workspace", "import", ops.NOTEBOOK_PATH, "--file", "databricks/lantern_agent_job.py",
        "--format", "SOURCE", "--language", "PYTHON", "--overwrite")
    jid = ops.upsert_job(paused=not unpause)
    print(f"job '{ops.JOB_NAME}' id={jid} schedule=every 30 min ({'UNPAUSED' if unpause else 'PAUSED'})")


def deploy_demo() -> None:
    """Create the isolated replay path: schema, volumes, code bundle, notebook copy and job.
    Touches nothing that the live job uses."""
    schema = ops.DEMO_SCHEMA
    if not cli_ok("schemas", "create", schema, CATALOG):
        raise SystemExit(f"could not create schema {CATALOG}.{schema}")
    for vol in ("code", "published"):
        if not cli_ok("volumes", "create", CATALOG, schema, vol, "MANAGED"):
            raise SystemExit(f"could not create volume {CATALOG}.{schema}.{vol}")
    info = build_bundle()
    remote = f"dbfs:/Volumes/{CATALOG}/{schema}/code/lantern_bundle.zip"
    print(f"bundle {info} -> {remote}")
    cli("fs", "cp", str(BUNDLE_LOCAL), remote, "--overwrite")
    cli("workspace", "mkdirs", ops.DEMO_WORKSPACE_ROOT)
    print(f"upload notebook -> {ops.DEMO_NOTEBOOK_PATH}")
    cli("workspace", "import", ops.DEMO_NOTEBOOK_PATH, "--file", "databricks/lantern_agent_job.py",
        "--format", "SOURCE", "--language", "PYTHON", "--overwrite")
    jid = ops.upsert_demo_job()
    print(f"job '{ops.DEMO_JOB_NAME}' id={jid} schema={CATALOG}.{schema} (no schedule; run on demand)")


def run(replay: str | None, wait: bool) -> int:
    # A replay always goes to the demo job; the live job only ever sees the live feed.
    res = ops.trigger_job("replay" if replay else "live", replay, target="demo" if replay else "live")
    rid = res["run_id"]
    print("started run", rid)
    if not wait:
        return 0
    while True:
        r = ops.call("GET", "/api/2.2/jobs/runs/get", params={"run_id": rid})
        state = r["state"]
        if state["life_cycle_state"] in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            break
        print("  ", state["life_cycle_state"], flush=True)
        time.sleep(10)
    out = ops.run_output(rid)
    print(json.dumps({"state": out["state"], "result": out["result"], "error": out["error"]}, indent=1))
    return 0 if out["state"].get("result_state") == "SUCCESS" else 1


def set_paused(paused: bool) -> None:
    jid = ops.find_job()
    if jid is None:
        raise SystemExit("job not found; deploy first")
    ops.call("POST", "/api/2.2/jobs/update", {"job_id": jid, "new_settings": {
        "schedule": {**ops.job_settings(paused)["schedule"]}}})
    print("schedule", "PAUSED" if paused else "UNPAUSED")


def status(demo: bool = False) -> None:
    from lantern.store import get_demo_store

    store = get_demo_store("databricks") if demo else DatabricksVolumeStore()
    print("path:", "demo (replay)" if demo else "live")
    print("job status:", json.dumps(ops.job_status("demo" if demo else "live"), indent=1))
    ov = store.read_published()
    print("published overlay:", None if ov is None else
          {"version": ov["version"], "mode": ov["mode"], "published_at": ov["published_at"],
           "entries": [(e["entry_id"], e["action"], len(e["edge_ids"])) for e in ov["entries"]]})
    print("heartbeat:", store.read_heartbeat())
    runs = store.list_runs(60)
    by = {}
    for r in runs:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(f"recent runs: {len(runs)} {by}")


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy")
    d.add_argument("--unpause", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--replay", choices=["1", "2"])
    r.add_argument("--wait", action="store_true")
    sub.add_parser("pause")
    sub.add_parser("unpause")
    sub.add_parser("deploy-demo")
    s = sub.add_parser("status")
    s.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.cmd == "deploy":
        deploy(a.unpause)
    elif a.cmd == "deploy-demo":
        deploy_demo()
    elif a.cmd == "run":
        return run(a.replay, a.wait)
    elif a.cmd == "pause":
        set_paused(True)
    elif a.cmd == "unpause":
        set_paused(False)
    elif a.cmd == "status":
        status(a.demo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
