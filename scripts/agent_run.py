"""Run the agent once from the command line (local store).

  python scripts/agent_run.py                 live TfL feed
  python scripts/agent_run.py --replay 1      captured feed + synthetic records (burst main)
  python scripts/agent_run.py --replay 2      same, with the burst main withdrawn

The Databricks job runs the same lantern.agent.run.execute(); see databricks/.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lantern.agent import run as agent_run  # noqa: E402
from lantern.agent.replay import replay_feed  # noqa: E402
from lantern.env import load_env  # noqa: E402
from lantern.graph import load_graph  # noqa: E402
from lantern.store import LocalStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", choices=["1", "2"], help="replay step instead of the live feed")
    ap.add_argument("--json", action="store_true", help="print the full run record")
    args = ap.parse_args()
    load_env()
    g = load_graph()
    store = LocalStore()
    now = datetime.now(timezone.utc)
    if args.replay:
        result = agent_run.execute(g, store, replay_feed(now, args.replay), now=now, mode="replay")
    else:
        result = agent_run.execute(g, store, agent_run.live_feed, now=now, mode="live")
    run = result["run"]
    if args.json:
        print(json.dumps(result, indent=1))
        return 0 if run["status"] != "failed" else 1
    print(f"{run['run_id']}  mode={run['mode']}  status={run['status']}  "
          f"v{run['version_before']} -> v{run['version_after']}  assessor={run['assessor']}")
    print(" ", run["summary"], "|", run.get("error") or "")
    for d in result["decisions"]:
        if d["decision"] == "reject_invalid_record":
            continue
        print(f"  {d['event_id']:<14} {d['decision']:<26} {(d['reason'] or '')[:150]}")
        if d.get("evidence"):
            print(f"  {'':<14} evidence: \"{d['evidence']}\"")
        if d.get("edge_ids"):
            print(f"  {'':<14} segments: {len(d['edge_ids'])}  action: {d['action']}")
    return 0 if run["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
