"""Leave at / Arrive by: London clock resolution and arrive-by planning checked against the
routing engine, including park closures and trips that span midnight."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lantern.access import LONDON
from lantern.config import PLACES
from lantern.routing import route_pair
from lantern.store import LocalStore
from lantern.timing import next_occurrence, parse_clock, plan_arrival


def ll(key):
    return (PLACES[key]["lon"], PLACES[key]["lat"])


O, D = "imperial_south_ken", "wilson_house"


def test_parse_clock():
    assert parse_clock("22:30") == (22, 30) and parse_clock("0030") == (0, 30)
    for bad in ("24:00", "12:60", "noon", "", "7"):
        with pytest.raises(ValueError):
            parse_clock(bad)


def test_next_occurrence_rolls_into_tomorrow():
    now = datetime(2026, 11, 14, 21, 0, tzinfo=LONDON)
    assert next_occurrence(22, 30, now) == datetime(2026, 11, 14, 22, 30, tzinfo=LONDON)
    assert next_occurrence(0, 30, now) == datetime(2026, 11, 15, 0, 30, tzinfo=LONDON)
    assert next_occurrence(21, 0, now + timedelta(seconds=40)).day == 14     # same minute is today
    assert next_occurrence(20, 58, now).day == 15                            # already passed


@pytest.mark.parametrize("arrive", [
    datetime(2026, 11, 14, 23, 0, tzinfo=LONDON),      # evening, Hyde Park open
    datetime(2026, 11, 15, 0, 30, tzinfo=LONDON),      # across midnight, both parks shut on the way
    datetime(2026, 11, 15, 0, 2, tzinfo=LONDON),       # leaves around the Hyde Park cut-off
    datetime(2026, 11, 15, 14, 0, tzinfo=LONDON),      # daytime, Kensington Gardens open
])
def test_arrive_by_is_latest_feasible_departure(g, arrive):
    plans = plan_arrival(g, ll(O), ll(D), arrive)
    assert set(plans) == {"fastest", "preferred"}
    for kind, p in plans.items():
        # Verified against the routing engine at the departure it proposes.
        r = route_pair(g, ll(O), ll(D), p["depart"])
        assert p["depart"] + timedelta(seconds=r[kind]["time_s"]) <= arrive
        assert p["arrive"] <= arrive
        # ...and it is the latest: no later whole-minute departure also arrives on time.
        for later in range(1, 40):
            t = p["depart"] + timedelta(minutes=later)
            if t > arrive:
                break
            r2 = route_pair(g, ll(O), ll(D), t)
            assert t + timedelta(seconds=r2[kind]["time_s"]) > arrive, (kind, later)
    assert plans["preferred"]["depart"] <= plans["fastest"]["depart"]


@pytest.fixture()
def client(tmp_path, monkeypatch, g):
    import lantern.api as api

    for state, name in ((api._state, "store"), (api._demo_state, "store_demo")):
        state.clear()
        state.update(api._blank(), store=LocalStore(tmp_path / name))
    monkeypatch.setattr(api, "OVERLAY_TTL_S", 0.0)
    monkeypatch.setattr(api, "_now", lambda: datetime(2026, 11, 14, 21, 0, tzinfo=LONDON))
    return TestClient(api.app)


def test_api_leave_at_and_arrive_by(client):
    d = client.get("/api/route", params={"mode": "leave", "time": "22:30"}).json()
    assert d["time"]["mode"] == "leave" and d["time"]["target"]["local"] == "22:30"
    assert d["fastest"]["depart"]["local"] == "22:30" and d["fastest"]["depart"]["day"] == 0
    assert d["fastest"]["nav"]["depart"]["instruction"].startswith("Head ")

    a = client.get("/api/route", params={"mode": "arrive", "time": "00:30"}).json()
    assert a["time"]["mode"] == "arrive" and a["time"]["target"]["day"] == 1
    assert a["time"]["target"]["date"] == "Sun 15 Nov"
    for kind in ("fastest", "preferred"):
        assert a[kind]["depart"]["day"] == 0 and a[kind]["arrive"]["day"] in (0, 1)
        assert a[kind]["arrive"]["iso"] <= a["time"]["target"]["iso"]
        assert a[kind]["late"] is False


def test_arrive_by_offers_the_shorter_earlier_walk(client):
    # Arriving by 00:30 means leaving after Hyde Park has shut (a long way round). Leaving by
    # 23:30, while it is open, is a much shorter walk: the app says so instead of hiding it.
    a = client.get("/api/route", params={"mode": "arrive", "time": "00:30"}).json()
    e = a["time"]["earlier"]
    assert e and e["depart"]["local"] == "23:30" and "Hyde Park" in e["open_zones"]
    assert e["fastest_minutes"] + 8 <= a["fastest"]["minutes"]
    # No such hint when the latest departure is already the short walk.
    b = client.get("/api/route", params={"mode": "arrive", "time": "23:00"}).json()
    assert b["time"]["earlier"] is None
    assert client.get("/api/route", params={"mode": "leave", "time": "22:30"}).json()["time"]["earlier"] is None


def test_api_arrive_by_too_late_and_bad_time(client):
    r = client.get("/api/route", params={"mode": "arrive", "time": "21:10"})
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "too_late" and body["leave_now_arrival"]["local"] > "21:10"
    r = client.get("/api/route", params={"mode": "leave", "time": "25:00"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_time"


def test_demo_source_is_separate_from_live(client, g):
    from conftest import NOW, stub_assessor
    import lantern.api as api
    from lantern.agent import run as agent_run
    from lantern.agent.replay import replay_feed

    agent_run.execute(g, api.store("demo"), replay_feed(NOW, "1", include_capture=False),
                      assessor=stub_assessor, now=NOW, mode="replay")
    when = NOW.isoformat()
    live = client.get("/api/route", params={"when": when}).json()
    demo = client.get("/api/route", params={"when": when, "source": "demo"}).json()
    assert live["overlay"]["version"] == 0 and not live["changes"]["entries"]      # replay never reaches live
    assert demo["overlay"]["version"] == 1 and demo["overlay"]["source"] == "demo"
    assert demo["changes"]["changed"] and demo["changes"]["entries"][0]["synthetic"] is True
    assert client.get("/api/route", params={"source": "elsewhere"}).status_code == 400
