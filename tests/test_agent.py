from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import NIGHT, NOW, stub_assessor
from lantern import tfl
from lantern.agent import run as agent_run
from lantern.agent.assess import rules_assessor, verify_assessment
from lantern.agent.match import match_event, norm_name, street_edges
from lantern.agent.replay import replay_feed, replay_records
from lantern.agent.validate import gate
from lantern.config import PLACES
from lantern.overlay import active_entries, empty_overlay, validate_overlay
from lantern.routing import route_pair

OD = ((PLACES["imperial_south_ken"]["lon"], PLACES["imperial_south_ken"]["lat"]),
      (PLACES["wilson_house"]["lon"], PLACES["wilson_house"]["lat"]))


def _run(g, store, step="1", now=NOW, assessor=stub_assessor, capture=False):
    return agent_run.execute(g, store, replay_feed(now, step, include_capture=capture),
                             assessor=assessor, now=now, mode="replay")


def _decisions(res):
    return {d["event_id"]: d for d in res["decisions"]}


# ---------------------------------------------------------------- full replay
def test_replay_decisions_and_publication(g, store):
    res = _run(g, store)
    run, dec = res["run"], _decisions(res)
    assert run["status"] == "published" and run["version_after"] == 1
    expected = {
        "REPLAY-001": "publish_close", "REPLAY-002": "ignore_not_pedestrian",
        "REPLAY-003": "reject_uncertain", "REPLAY-004": "reject_stale",
        "REPLAY-005": "reject_location", "REPLAY-006": "publish_restrict",
        "REPLAY-007": "ignore_cleared", "REPLAY-008": "ignore_not_pedestrian",
    }
    assert {k: v["decision"] for k, v in dec.items()} == expected
    ov = store.read_published()
    assert ov["mode"] == "replay" and validate_overlay(ov, g.edge_index) == []
    assert {e["entry_id"]: e["action"] for e in ov["entries"]} == {
        "REPLAY-001": "close", "REPLAY-006": "restrict"}
    assert ov["checks"]["passed"] and ov["checks"]["total"] == 16
    # Every decision that changes routing carries its evidence, source and affected segments.
    d = dec["REPLAY-001"]
    assert d["evidence"] == "both footways are closed" and d["edge_ids"] and d["source_url"]
    assert "disagree" in dec["REPLAY-005"]["reason"]


def test_relevant_event_changes_route_and_irrelevant_does_not(g, store):
    before = route_pair(g, *OD, NIGHT, overlay=None)
    _run(g, store)
    ov = store.read_published()
    after = route_pair(g, *OD, NIGHT, overlay=ov, force_active=True)
    closed = set(next(e for e in ov["entries"] if e["entry_id"] == "REPLAY-001")["edge_ids"])
    assert closed & set(before["fastest"]["edge_ids"])            # it was on the route
    for kind in ("fastest", "preferred"):
        assert not closed & set(after[kind]["edge_ids"])          # and is avoided now
    assert after["fastest"]["time_s"] > before["fastest"]["time_s"]

    # A feed containing only the vehicle-only closure publishes nothing and changes nothing.
    recs, meta = replay_records(NOW, "1", include_capture=False)
    only_vehicle = [r for r in recs if r["id"] in ("REPLAY-002", "REPLAY-008")]
    from lantern.store import LocalStore
    s2 = LocalStore(store.root.parent / "s2")
    res = agent_run.execute(g, s2, agent_run.fixture_feed(only_vehicle, "vehicle-only"),
                            assessor=stub_assessor, now=NOW, mode="replay")
    assert res["run"]["status"] in ("no_change", "published")
    ov2 = s2.read_published() or empty_overlay()
    assert ov2["entries"] == []
    same = route_pair(g, *OD, NIGHT, overlay=ov2, force_active=True)
    assert same["fastest"]["edge_ids"] == before["fastest"]["edge_ids"]
    assert same["preferred"]["edge_ids"] == before["preferred"]["edge_ids"]


# ---------------------------------------------------------------- expiry / withdrawal
def test_entry_expires_at_valid_to_without_a_new_run(g, store):
    _run(g, store)
    ov = store.read_published()
    en = next(e for e in ov["entries"] if e["entry_id"] == "REPLAY-001")
    from lantern.overlay import parse_ts
    end = parse_ts(en["valid_to"])
    assert en in active_entries(ov, end - timedelta(minutes=1))
    assert en not in active_entries(ov, end + timedelta(minutes=1))


def test_withdrawn_when_record_leaves_feed(g, store):
    _run(g, store, "1")
    res = _run(g, store, "2", now=NOW + timedelta(minutes=30))
    assert res["run"]["status"] == "published" and res["run"]["version_after"] == 2
    assert _decisions(res)["REPLAY-001"]["decision"] == "withdrawn"
    ov = store.read_published()
    assert [e["entry_id"] for e in ov["entries"]] == ["REPLAY-006"]
    assert store.read_version(1) is not None            # history is kept


def test_expired_entry_removed_on_later_run(g, store):
    _run(g, store, "1")
    later = NOW + timedelta(hours=11)                    # REPLAY-001 ends at NOW + 10 h
    recs, _ = replay_records(NOW, "1", include_capture=False)   # same records, not re-timed
    res = agent_run.execute(g, store, agent_run.fixture_feed(recs, "stale-feed"),
                            assessor=stub_assessor, now=later, mode="replay")
    dec = _decisions(res)
    assert dec["REPLAY-001"]["decision"] in ("reject_stale", "expired")
    assert "REPLAY-001" not in {e["entry_id"] for e in store.read_published()["entries"]}


def test_unchanged_feed_is_a_no_change_run(g, store):
    _run(g, store, "1")
    recs, _ = replay_records(NOW, "1", include_capture=False)
    res = agent_run.execute(g, store, agent_run.fixture_feed(recs, "same"),
                            assessor=stub_assessor, now=NOW + timedelta(minutes=30), mode="replay")
    assert res["run"]["status"] == "no_change"
    assert store.read_published()["version"] == 1


# ---------------------------------------------------------------- failure handling
def test_failed_fetch_keeps_last_good_version(g, store):
    _run(g, store)
    good = store.read_published()

    def broken():
        raise ConnectionError("TfL API unreachable")

    res = agent_run.execute(g, store, broken, assessor=stub_assessor, now=NOW, mode="live")
    assert res["run"]["status"] == "failed" and "unreachable" in res["run"]["error"]
    assert store.read_published() == good
    assert store.read_heartbeat()["status"] == "failed"


def test_truncated_live_feed_is_treated_as_outage(g, store):
    _run(g, store)
    good = store.read_published()
    res = agent_run.execute(g, store, agent_run.fixture_feed([], "empty"),
                            assessor=stub_assessor, now=NOW, mode="live")
    assert res["run"]["status"] == "failed"
    assert store.read_published() == good                # nothing was withdrawn


def test_assessor_failure_holds_new_events_and_carries_verified_ones(g, store):
    _run(g, store)

    def flaky(ev):
        raise TimeoutError("model unavailable")

    recs, _ = replay_records(NOW, "1", include_capture=False)
    store.write_state({})                                # drop the assessment cache
    res = agent_run.execute(g, store, agent_run.fixture_feed(recs, "same"),
                            assessor=flaky, now=NOW + timedelta(minutes=30), mode="replay")
    dec = _decisions(res)
    assert dec["REPLAY-001"]["decision"] == "carried"
    assert dec["REPLAY-002"]["decision"] == "hold_assessment_failed"
    assert {e["entry_id"] for e in store.read_published()["entries"]} == {"REPLAY-001", "REPLAY-006"}


def test_invalid_records_are_rejected_not_fatal(g, store):
    recs, _ = replay_records(NOW, "1", include_capture=False)
    recs.append({"id": "BROKEN-1", "comments": "no status or dates"})
    recs.append({"id": "BROKEN-2", "comments": "x", "status": "Active",
                 "startDateTime": "not-a-date", "point": "[-0.17,51.5]"})
    res = agent_run.execute(g, store, agent_run.fixture_feed(recs, "bad"),
                            assessor=stub_assessor, now=NOW, mode="replay")
    dec = _decisions(res)
    assert res["run"]["status"] == "published"
    assert dec["BROKEN-1"]["decision"] == "reject_invalid_record"
    assert dec["BROKEN-2"]["decision"] == "reject_invalid_record"


# ---------------------------------------------------------------- evidence + location checks
def test_evidence_must_be_verbatim_and_about_pedestrians(g):
    recs, _ = replay_records(NOW, "1", include_capture=False)
    ev = tfl.normalise(next(r for r in recs if r["id"] == "REPLAY-001"))[0]
    good = stub_assessor(ev)
    assert verify_assessment(ev, good) == []
    invented = dict(good, evidence_quote="the pavement is completely blocked")
    assert "not found verbatim" in verify_assessment(ev, invented)[0]
    off_topic = dict(good, evidence_quote="due to a burst water main")
    assert "does not mention pedestrians" in verify_assessment(ev, off_topic)[0]


def test_hallucinated_quote_is_rejected_by_the_run(g, store):
    def liar(ev):
        a = stub_assessor(ev)
        if ev["id"] == "REPLAY-002":      # vehicle-only record, model claims a footway closure
            a.update(pedestrian_impact="footway_closed", basis="explicit",
                     evidence_quote="the footway is closed to pedestrians",
                     blocks_all_pedestrian_passage="yes")
        return a

    res = _run(g, store, assessor=liar)
    assert _decisions(res)["REPLAY-002"]["decision"] == "reject_unverified_evidence"
    assert "REPLAY-002" not in {e["entry_id"] for e in store.read_published()["entries"]}


def test_location_matching(g):
    assert norm_name("[A4] KENSINGTON ROAD (SW7 )") == "kensington road"
    assert norm_name("Queen's Gate") == norm_name("QUEENS GATE")
    assert street_edges(g, "Exhibition Rd") == street_edges(g, "Exhibition Road")
    recs, _ = replay_records(NOW, "1", include_capture=False)
    ev = tfl.normalise(next(r for r in recs if r["id"] == "REPLAY-001"))[0]
    m = match_event(g, ev, stub_assessor(ev))
    assert m["ok"] and 100 < m["length_m"] < 500 and m["anchor_distance_m"] < 30
    names = {g.edges[g.edge_index[e]]["name"] for e in m["edge_ids"]}
    assert names == {"Exhibition Road"}
    # Unknown street and unbounded extents are refused.
    bad = dict(stub_assessor(ev), street="Nonexistent Avenue")
    assert not match_event(g, dict(ev, streets=[]), bad)["ok"]
    far = dict(stub_assessor(ev), cross_streets=["Prince Consort Road", "Praed Street"])
    assert not match_event(g, ev, far)["ok"]


def test_one_sided_closure_is_published_as_restriction(g, store):
    """'The footway is closed' with no statement that people cannot pass must not sever a street."""
    recs, _ = replay_records(NOW, "1", include_capture=False)
    r5 = next(r for r in recs if r["id"] == "REPLAY-005")
    fixed = dict(r5, id="REPLAY-005B",
                 point=json.dumps([-0.17598, 51.511632]))     # coordinates now agree with the text
    canned = dict(stub_assessor({"id": "REPLAY-005"}))
    res = agent_run.execute(g, store, agent_run.fixture_feed([fixed], "one-sided"),
                            assessor=lambda ev: dict(canned), now=NOW, mode="replay")
    d = _decisions(res)["REPLAY-005B"]
    assert d["decision"] == "publish_restrict" and "restriction" in d["reason"]


# ---------------------------------------------------------------- gate
def test_gate_rejects_entry_that_breaks_baseline_journeys(g):
    # Close every segment touching the node nearest Wilson House: journeys there become impossible.
    p = PLACES["wilson_house"]
    target, _ = g.nearest_node(p["lon"], p["lat"])
    ring = {g.edges[i]["id"] for i, _ in g.adj[target]}
    nxt = {g.edges[j]["id"] for i, v in g.adj[target] for j, _ in g.adj[v]}
    nxt2 = {g.edges[k]["id"] for i, v in g.adj[target] for j, w in g.adj[v] for k, _ in g.adj[w]}
    bad = {"entry_id": "BAD-1", "source": "test", "action": "close", "edge_ids": sorted(ring | nxt | nxt2),
           "evidence": "x", "valid_from": NOW.isoformat(), "valid_to": (NOW + timedelta(hours=5)).isoformat()}
    fine = {"entry_id": "OK-1", "source": "test", "action": "restrict",
            "edge_ids": [g.edges[i]["id"] for i in street_edges(g, "Praed Street")[:3]],
            "evidence": "x", "valid_from": NOW.isoformat(), "valid_to": (NOW + timedelta(hours=5)).isoformat()}
    cand = dict(empty_overlay(), version=1, entries=[bad, fine])
    final, rejected, report = gate(g, cand, NOW)
    assert [r["entry"]["entry_id"] for r in rejected] == ["BAD-1"]
    assert [e["entry_id"] for e in final["entries"]] == ["OK-1"]
    assert report["passed"]


def test_conflicting_records_newest_wins(g, store):
    recs, _ = replay_records(NOW, "1", include_capture=False)
    r1 = next(r for r in recs if r["id"] == "REPLAY-001")
    older = dict(r1, id="REPLAY-001-OLD", lastModifiedTime="2026-11-14T08:00:00Z")
    canned = stub_assessor({"id": "REPLAY-001"})
    res = agent_run.execute(g, store, agent_run.fixture_feed([r1, older], "conflict"),
                            assessor=lambda ev: dict(canned), now=NOW, mode="replay")
    dec = _decisions(res)
    assert dec["REPLAY-001"]["decision"] == "publish_close"
    assert dec["REPLAY-001-OLD"]["decision"] == "reject_conflict"
    assert [e["entry_id"] for e in store.read_published()["entries"]] == ["REPLAY-001"]


def test_rules_fallback_is_labelled_and_conservative():
    recs, _ = replay_records(NOW, "1", include_capture=False)
    by = {r["id"]: tfl.normalise(r)[0] for r in recs}
    assert rules_assessor(by["REPLAY-002"])["pedestrian_impact"] == "none"
    a = rules_assessor(by["REPLAY-001"])
    assert a["_assessor"] == "rules-fallback" and a["pedestrian_impact"] == "footway_closed"


def test_captured_live_feed_normalises_cleanly():
    recs, meta = replay_records(NOW, "1", include_capture=True)
    assert meta["captured_records"] >= 100 and meta["synthetic_records"] == 8
    bad = [r["id"] for r in recs if tfl.normalise(r)[0] is None]
    assert bad == []
