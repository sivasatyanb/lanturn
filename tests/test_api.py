from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import NOW, stub_assessor
from lantern.agent import run as agent_run
from lantern.agent.replay import replay_feed
from lantern.store import LocalStore


@pytest.fixture()
def client(tmp_path, monkeypatch, g):
    import lantern.api as api

    store = LocalStore(tmp_path / "store")
    api._state.clear()
    api._state.update(overlay=None, fetched=0.0, error=None, heartbeat=None, source=None, store=store)
    monkeypatch.setattr(api, "OVERLAY_TTL_S", 0.0)
    return TestClient(api.app), store


def test_normal_flow(client):
    c, _ = client
    cfg = c.get("/api/config").json()
    assert cfg["default"] == {"from": "imperial_south_ken", "to": "wilson_house"}
    assert 0.5 < sum(cfg["coverage"]["lighting_by_length"].values()) <= 1.001
    r = c.get("/api/route", params={"when": "2026-11-14T22:30:00"})
    assert r.status_code == 200
    d = r.json()
    assert d["fastest"]["minutes"] <= d["preferred"]["minutes"]
    assert d["when_local"] == "22:30" and d["dark"] is True
    assert d["overlay"]["version"] == 0 and d["changes"]["changed"] is False
    assert d["overlay"]["stale"] is True                  # the agent has never run in this store
    assert "edge_ids" not in d["fastest"]
    assert d["fastest"]["segments"][0]["coords"]
    assert c.get("/").status_code == 200


def test_errors_are_structured(client):
    c, _ = client
    r = c.get("/api/route", params={"from": "nowhere"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_place"
    r = c.get("/api/route", params={"from": "-0.05,51.55"})
    assert r.status_code == 404 and r.json()["code"] == "outside_coverage"
    r = c.get("/api/route", params={"when": "yesterday-ish"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_time"


def test_app_consumes_published_overlay(client, g):
    c, store = client
    before = c.get("/api/route").json()
    agent_run.execute(g, store, replay_feed(NOW, "1", include_capture=False),
                      assessor=stub_assessor, now=NOW, mode="replay")
    # Use a departure inside the entry's validity window.
    when = NOW.isoformat()
    after = c.get("/api/route", params={"when": when}).json()
    assert after["overlay"]["version"] == 1 and after["overlay"]["mode"] == "replay"
    assert after["overlay"]["stale"] is False
    ch = after["changes"]
    assert ch["changed"] and ch["fastest_delta_s"] > 0
    closed = [e for e in ch["entries"] if e["action"] == "close"]
    assert closed and closed[0]["entry_id"] == "REPLAY-001" and closed[0]["synthetic"] is True
    assert closed[0]["evidence"] == "both footways are closed" and closed[0]["geometry"]
    assert ch["previous"]["fastest"]["segments"]
    ov = c.get("/api/overlay").json()
    assert ov["version"] == 1 and ov["checks"]["passed"] is True
    lr = c.get("/api/agent/last-run").json()
    assert lr["run"]["status"] == "published" and len(lr["decisions"]) == 8
    assert before["overlay"]["version"] == 0


def test_store_failure_serves_last_good_overlay(client, g, monkeypatch):
    c, store = client
    agent_run.execute(g, store, replay_feed(NOW, "1", include_capture=False),
                      assessor=stub_assessor, now=NOW, mode="replay")
    assert c.get("/api/route", params={"when": NOW.isoformat()}).json()["overlay"]["version"] == 1

    def boom():
        raise ConnectionError("databricks unreachable")

    monkeypatch.setattr(store, "read_published", boom)
    d = c.get("/api/route", params={"when": NOW.isoformat()}).json()
    assert d["overlay"]["version"] == 1                    # still routing on the last good version
    assert "unreachable" in d["overlay"]["store_error"]
