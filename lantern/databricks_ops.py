"""Thin Databricks REST helpers used by the deploy script and the app's demo controls.

Authentication is always an OAuth token minted by the Databricks CLI for a named profile
(`databricks auth login --profile walknight`). No token is ever written to disk by this code.
"""
from __future__ import annotations

import json
from typing import Any

from .config import HTTP_HEADERS
from .store import DatabricksVolumeStore

JOB_NAME = "lantern-agent"
WORKSPACE_ROOT = "/Shared/lantern"
NOTEBOOK_PATH = WORKSPACE_ROOT + "/lantern_agent_job"

# The demo (replay) path is a second job with its own notebook copy, schema, tables and volume.
# Nothing that targets "demo" ever modifies the live job above.
DEMO_JOB_NAME = "lantern-agent-demo"
DEMO_SCHEMA = "lantern_demo"
DEMO_WORKSPACE_ROOT = "/Shared/lantern_demo"
DEMO_NOTEBOOK_PATH = DEMO_WORKSPACE_ROOT + "/lantern_agent_job"

_store: DatabricksVolumeStore | None = None
_demo_store: DatabricksVolumeStore | None = None
_job_ids: dict[str, int | None] = {}


def _auth() -> tuple[str, str]:
    global _store
    if _store is None:
        _store = DatabricksVolumeStore()
    return _store._auth()


def _published(target: str) -> DatabricksVolumeStore:
    global _demo_store
    _auth()
    if target == "demo":
        if _demo_store is None:
            from .store import get_demo_store
            _demo_store = get_demo_store("databricks")
        return _demo_store
    return _store


def call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
    import requests

    host, token = _auth()
    r = requests.request(method, host + path, json=body, params=params, timeout=60,
                         headers={"Authorization": f"Bearer {token}",
                                  "User-Agent": HTTP_HEADERS["User-Agent"]})
    if r.status_code >= 400:
        raise RuntimeError(f"Databricks API {method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def find_job(target: str = "live") -> int | None:
    if _job_ids.get(target) is None:
        name = DEMO_JOB_NAME if target == "demo" else JOB_NAME
        jobs = call("GET", "/api/2.2/jobs/list", params={"name": name}).get("jobs", [])
        _job_ids[target] = jobs[0]["job_id"] if jobs else None
    return _job_ids[target]


def job_settings(paused: bool = True, interval_min: int = 30) -> dict:
    return {
        "name": JOB_NAME,
        "description": "Lantern autonomous disruption agent: ingest TfL feed, validate, decide, "
                       "publish a versioned routing overlay.",
        "max_concurrent_runs": 1,
        "tasks": [{
            "task_key": "agent_run",
            "notebook_task": {"notebook_path": NOTEBOOK_PATH,
                              "base_parameters": {"mode": "live", "replay_step": "1"}},
            "timeout_seconds": 900,
            "max_retries": 0,
        }],
        "schedule": {"quartz_cron_expression": f"0 0/{interval_min} * * * ?",
                     "timezone_id": "Europe/London",
                     "pause_status": "PAUSED" if paused else "UNPAUSED"},
        "tags": {"project": "lantern"},
    }


def demo_job_settings() -> dict:
    """The replay job: same notebook code, isolated schema, no schedule (run on demand)."""
    return {
        "name": DEMO_JOB_NAME,
        "description": "Lantern demo path: replays the captured TfL feed plus labelled synthetic "
                       "records through the same agent, into its own schema and volume.",
        "max_concurrent_runs": 1,
        "tasks": [{
            "task_key": "agent_run",
            "notebook_task": {"notebook_path": DEMO_NOTEBOOK_PATH,
                              "base_parameters": {"mode": "replay", "replay_step": "1",
                                                  "schema": DEMO_SCHEMA}},
            "timeout_seconds": 900,
            "max_retries": 0,
        }],
        "tags": {"project": "lantern", "path": "demo"},
    }


def upsert_job(paused: bool = True, interval_min: int = 30) -> int:
    settings = job_settings(paused, interval_min)
    jid = find_job()
    if jid is None:
        _job_ids["live"] = call("POST", "/api/2.2/jobs/create", settings)["job_id"]
    else:
        call("POST", "/api/2.2/jobs/reset", {"job_id": jid, "new_settings": settings})
    return _job_ids["live"]


def upsert_demo_job() -> int:
    settings = demo_job_settings()
    jid = find_job("demo")
    if jid is None:
        _job_ids["demo"] = call("POST", "/api/2.2/jobs/create", settings)["job_id"]
    else:
        call("POST", "/api/2.2/jobs/reset", {"job_id": jid, "new_settings": settings})
    return _job_ids["demo"]


def trigger_job(mode: str = "live", step: str | None = None, target: str = "live") -> dict:
    if (mode == "replay") != (target == "demo"):
        raise RuntimeError("replay runs only on the demo job, live runs only on the live job")
    jid = find_job(target)
    if jid is None:
        raise RuntimeError(f"Databricks {target} job not found; run scripts/databricks_deploy.py")
    params = {"mode": mode, "replay_step": step or "1"}
    if target == "demo":
        params["schema"] = DEMO_SCHEMA
    res = call("POST", "/api/2.2/jobs/run-now", {"job_id": jid, "notebook_params": params})
    return {"started": True, "running": True, "where": "databricks", "source": target,
            "run_id": res.get("run_id")}


def latest_run(target: str = "live") -> dict | None:
    jid = find_job(target)
    if jid is None:
        return None
    runs = call("GET", "/api/2.2/jobs/runs/list", params={"job_id": jid, "limit": 1}).get("runs", [])
    return runs[0] if runs else None


def job_status(target: str = "live") -> dict:
    r = latest_run(target)
    if not r:
        return {"running": False, "last": None, "where": "databricks"}
    st = r.get("state", {})
    running = st.get("life_cycle_state") in ("PENDING", "RUNNING", "QUEUED", "WAITING_FOR_RETRY", "BLOCKED")
    last = None
    if not running:
        hb = None
        try:
            hb = _published(target).read_heartbeat()
        except Exception:  # noqa: BLE001
            hb = None
        last = {"status": (hb or {}).get("status") or st.get("result_state", "").lower(),
                "run_id": (hb or {}).get("run_id"), "summary": (hb or {}).get("summary")}
    return {"running": running, "last": last, "where": "databricks",
            "run_page_url": r.get("run_page_url"), "databricks_state": st.get("life_cycle_state")}


def run_output(run_id: int) -> dict:
    run = call("GET", "/api/2.2/jobs/runs/get", params={"run_id": run_id})
    task_run = run["tasks"][0]["run_id"]
    out = call("GET", "/api/2.2/jobs/runs/get-output", params={"run_id": task_run})
    result = (out.get("notebook_output") or {}).get("result")
    return {"state": run.get("state"), "result": json.loads(result) if result else None,
            "error": out.get("error"), "run_page_url": run.get("run_page_url")}
