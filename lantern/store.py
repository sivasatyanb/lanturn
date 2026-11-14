"""Where published overlays and the agent's audit trail live.

Three interchangeable backends share one data contract (see overlay.py):

- LocalStore            files under var/store, for development and tests.
- DatabricksVolumeStore the production path for the app: reads the overlay that the
                        Databricks job exported to a Unity Catalog volume, over REST.
- (in the notebook)     databricks/lantern_agent_job.py writes Delta tables + the volume.

The app only ever reads. Publication is atomic: a version file is written first and the
"latest" pointer is replaced last, so a failed run leaves the previous version in place.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import HTTP_HEADERS, VAR_DIR


class LocalStore:
    kind = "local"

    def __init__(self, root: Path | None = None):
        self.root = Path(root or VAR_DIR / "store")
        (self.root / "overlay").mkdir(parents=True, exist_ok=True)

    # -- overlay ----------------------------------------------------------
    def read_published(self) -> dict | None:
        p = self.root / "overlay" / "latest.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def read_version(self, version: int) -> dict | None:
        p = self.root / "overlay" / f"v{version:06d}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def publish(self, overlay: dict) -> None:
        body = json.dumps(overlay, indent=1)
        (self.root / "overlay" / f"v{overlay['version']:06d}.json").write_text(body, encoding="utf-8")
        tmp = self.root / "overlay" / "latest.json.tmp"
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, self.root / "overlay" / "latest.json")

    # -- state / heartbeat ------------------------------------------------
    def _read(self, name: str, default):
        p = self.root / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def _write(self, name: str, obj) -> None:
        tmp = self.root / (name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        os.replace(tmp, self.root / name)

    def read_state(self) -> dict:
        return self._read("state.json", {})

    def write_state(self, state: dict) -> None:
        self._write("state.json", state)

    def read_heartbeat(self) -> dict | None:
        return self._read("heartbeat.json", None)

    # -- audit trail ------------------------------------------------------
    def record_run(self, run: dict, decisions: list[dict], raw_records: list | None = None,
                   events: list | None = None) -> None:
        with open(self.root / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(run) + "\n")
        with open(self.root / "decisions.jsonl", "a", encoding="utf-8") as f:
            for d in decisions:
                f.write(json.dumps(d) + "\n")
        self._write("heartbeat.json", {
            "run_id": run["run_id"], "status": run["status"], "mode": run["mode"],
            "finished_at": run["finished_at"], "version": run["version_after"],
            "summary": run.get("summary"),
        })
        self._write("last_run.json", {"run": run, "decisions": decisions})

    def last_run(self) -> dict | None:
        return self._read("last_run.json", None)

    def list_runs(self, n: int = 20) -> list[dict]:
        p = self.root / "runs.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(x) for x in lines[-n:]][::-1]


def _token_expiry(tok: dict) -> float:
    """Epoch seconds at which a `databricks auth token` result expires (conservative)."""
    from datetime import datetime

    exp = tok.get("expiry")
    if exp:
        try:
            # Go prints nanoseconds; Python accepts at most microseconds.
            head, _, tail = exp.partition(".")
            if tail:
                frac = "".join(ch for ch in tail if ch.isdigit())
                zone = tail[len(frac):] or "+00:00"
                exp = f"{head}.{frac[:6]}{zone}"
            return datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    if tok.get("expires_in"):
        return time.time() + float(tok["expires_in"])
    return time.time() + 300          # unknown: re-mint within five minutes


class DatabricksVolumeStore:
    """Read-only view of what the Databricks job published (Files API on a UC volume).

    Auth: an OAuth token minted by the Databricks CLI for the named profile
    (`databricks auth login`). Nothing secret is stored in this repo or its config.
    """
    kind = "databricks"

    def __init__(self, profile: str | None = None, volume_path: str | None = None,
                 cache_dir: Path | None = None):
        self.profile = profile or os.environ.get("DATABRICKS_PROFILE", "walknight")
        self.volume = (volume_path or os.environ.get(
            "LANTERN_VOLUME", "/Volumes/workspace/lantern/published")).rstrip("/")
        self.cache = LocalStore(cache_dir or VAR_DIR / "databricks_cache")
        self._token: tuple[str, float] | None = None
        self._host: str | None = None

    def _cli(self) -> str:
        exe = os.environ.get("DATABRICKS_CLI")
        if exe:
            return exe
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
        for p in local.glob("Databricks.DatabricksCLI_*/databricks.exe"):
            return str(p)
        return "databricks"

    def _auth(self, force: bool = False) -> tuple[str, str]:
        """(host, bearer token). The CLI hands back its cached token, which may be close to
        expiry, so we honour the token's own expiry time and never assume a lifetime."""
        if force or self._token is None or self._token[1] < time.time() + 120:
            env = dict(os.environ, MSYS_NO_PATHCONV="1")
            out = subprocess.run([self._cli(), "auth", "token", "--profile", self.profile],
                                 capture_output=True, text=True, timeout=30, env=env, check=True)
            tok = json.loads(out.stdout)
            self._token = (tok["access_token"], _token_expiry(tok))
            if self._host is None:
                env_out = subprocess.run([self._cli(), "auth", "env", "--profile", self.profile],
                                         capture_output=True, text=True, timeout=30, env=env, check=True)
                self._host = json.loads(env_out.stdout)["env"]["DATABRICKS_HOST"].rstrip("/")
        return self._host, self._token[0]

    def _get_json(self, name: str) -> Any | None:
        import requests

        for attempt in (1, 2):
            host, token = self._auth(force=attempt == 2)
            r = requests.get(f"{host}/api/2.0/fs/files{self.volume}/{name}",
                             headers={"Authorization": f"Bearer {token}",
                                      "User-Agent": HTTP_HEADERS["User-Agent"]}, timeout=20)
            if r.status_code in (401, 403) and attempt == 1:
                continue                      # token rejected: mint a new one and retry once
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                # Never include the workspace URL in the message: it reaches the browser.
                raise RuntimeError(f"Databricks Files API returned {r.status_code} for {name}")
            return r.json()

    def read_published(self) -> dict | None:
        doc = self._get_json("overlay_latest.json")
        if doc is not None:
            self.cache.publish(doc)       # last good copy, served if Databricks is unreachable
        return doc

    def read_heartbeat(self) -> dict | None:
        return self._get_json("heartbeat.json")

    def last_run(self) -> dict | None:
        return self._get_json("last_run.json")

    def list_runs(self, n: int = 20) -> list[dict]:
        runs = self._get_json("runs_recent.json") or []
        return runs[:n]


def get_store(kind: str | None = None):
    kind = kind or os.environ.get("LANTERN_STORE", "local")
    if kind == "databricks":
        return DatabricksVolumeStore()
    return LocalStore()


DEMO_VOLUME = "/Volumes/workspace/lantern_demo/published"


def get_demo_store(kind: str | None = None):
    """Where the labelled replay publishes. Separate from the live store end to end (its own
    Databricks job, schema and volume), so the live schedule cannot overwrite a replay and a
    replay cannot reach normal planning."""
    kind = kind or os.environ.get("LANTERN_STORE", "local")
    if kind == "databricks":
        return DatabricksVolumeStore(
            volume_path=os.environ.get("LANTERN_DEMO_VOLUME", DEMO_VOLUME),
            cache_dir=VAR_DIR / "databricks_cache_demo")
    return LocalStore(VAR_DIR / "store_demo")
