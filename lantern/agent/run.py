"""One autonomous agent run: fetch -> validate -> assess -> locate -> gate -> publish.

The run is bounded and has exactly three outcomes:
  published   a new overlay version replaced the previous one
  no_change   the feed was processed and the published overlay is still correct
  failed      something broke; the previous overlay stays published untouched

Every record that reaches the demo area gets a decision with its evidence, whatever
the outcome, so the audit trail explains what was rejected as well as what changed.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .. import tfl
from ..graph import Graph
from ..overlay import SCHEMA, empty_overlay, parse_ts, validate_overlay
from . import assess as assess_mod
from .match import match_event
from .validate import gate

MIN_LIVE_RECORDS = 20                    # the London-wide feed normally has 100+ records
DEFAULT_VALIDITY = timedelta(hours=24)   # for records with no end time; re-checked each run
ACTIVE_STATUSES = ("active",)            # prefix match, case-insensitive

Feed = Callable[[], tuple[list[dict], dict]]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _entry_signature(en: dict) -> tuple:
    return (en["entry_id"], en["action"], tuple(en["edge_ids"]), en.get("valid_to"),
            en.get("source_modified"))


def _decision(run_id: str, now: datetime, ev: dict | None, decision: str, reason: str, **extra) -> dict:
    d = {
        "run_id": run_id, "decided_at": _iso(now),
        "event_id": ev["id"] if ev else extra.pop("event_id", None),
        "source": tfl.SOURCE,
        "source_url": ev.get("source_url") if ev else None,
        "source_modified": ev.get("modified") if ev else None,
        "location": ev.get("location") if ev else None,
        "decision": decision, "reason": reason,
        "evidence": None, "assessment": None, "match": None, "edge_ids": [], "action": None,
    }
    d.update(extra)
    return d


def execute(g: Graph, store, feed: Feed, assessor: assess_mod.Assessor | None = None,
            now: datetime | None = None, mode: str = "live") -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    started = datetime.now(timezone.utc)
    run_id = f"run-{started.strftime('%Y%m%dT%H%M%S')}Z"
    prev = store.read_published() or empty_overlay()
    run: dict[str, Any] = {
        "run_id": run_id, "mode": mode, "started_at": _iso(started), "as_of": _iso(now),
        "status": "failed", "version_before": prev["version"], "version_after": prev["version"],
        "feed": {}, "counts": {}, "error": None, "summary": None, "checks": None,
        "assessor": None,
    }
    decisions: list[dict] = []
    raw: list[dict] | None = None
    events: list[dict] = []
    try:
        # ---- 1. fetch ----------------------------------------------------
        raw, meta = feed()
        run["feed"] = dict(meta)
        if mode == "live" and len(raw) < MIN_LIVE_RECORDS:
            raise RuntimeError(f"feed returned only {len(raw)} records; treating as an outage "
                               "rather than withdrawing published entries")

        # ---- 2. normalise + validate records, filter to area --------------
        invalid = 0
        for rec in raw:
            ev, why = tfl.normalise(rec)
            if ev is None:
                invalid += 1
                decisions.append(_decision(run_id, now, None, "reject_invalid_record", why,
                                           event_id=str(rec.get("id"))))
                continue
            if tfl.in_area(ev):
                events.append(ev)
        run["feed"].update(invalid_records=invalid, in_area=len(events))

        # ---- 3. assess + locate each in-area record -----------------------
        assessor = assessor or assess_mod.default_assessor()
        state = store.read_state()
        cache: dict[str, dict] = dict(state.get("assessments", {}))
        new_cache: dict[str, dict] = {}
        prev_entries = {en["entry_id"]: en for en in prev["entries"]}
        proposed: list[dict] = []
        proposal_decisions: dict[str, dict] = {}

        for ev in events:
            end, start = parse_ts(ev.get("end")), parse_ts(ev.get("start"))
            status = (ev.get("status") or "").lower()
            if end and end <= now:
                decisions.append(_decision(run_id, now, ev, "reject_stale",
                                           f"record ended {ev['end']}, before this run"))
                continue
            if not status.startswith(ACTIVE_STATUSES):
                decisions.append(_decision(run_id, now, ev, "reject_not_active",
                                           f"status is '{ev.get('status')}', not active"))
                continue
            if start and start > now + timedelta(hours=12):
                decisions.append(_decision(run_id, now, ev, "defer_future",
                                           f"starts {ev['start']}, more than 12 h ahead"))
                continue

            key = assess_mod.cache_key(ev)
            try:
                a = cache.get(key) or assessor(ev)
            except Exception as exc:  # noqa: BLE001 - one bad record must not sink the run
                old = prev_entries.get(ev["id"])
                if old and old.get("source_modified") == ev.get("modified"):
                    proposed.append(old)
                    proposal_decisions[ev["id"]] = _decision(
                        run_id, now, ev, "carried", "assessment unavailable this run; record unchanged "
                        "since it was last verified, so the existing entry is kept",
                        edge_ids=old["edge_ids"], action=old["action"], evidence=old.get("evidence"))
                else:
                    decisions.append(_decision(run_id, now, ev, "hold_assessment_failed",
                                               f"assessment failed: {type(exc).__name__}: {str(exc)[:200]}"))
                continue
            new_cache[key] = a
            run["assessor"] = a.get("_assessor")
            common = {"assessment": {k: v for k, v in a.items() if not k.startswith("_")},
                      "assessor": a.get("_assessor"), "evidence": a.get("evidence_quote") or None}

            if a.get("superseded_or_cleared"):
                decisions.append(_decision(run_id, now, ev, "ignore_cleared",
                                           "latest update says the problem has ended", **common))
                continue
            impact = a.get("pedestrian_impact")
            if impact == "none":
                decisions.append(_decision(run_id, now, ev, "ignore_not_pedestrian",
                                           a.get("reasoning") or "no pedestrian impact stated", **common))
                continue
            if impact == "unclear" or a.get("basis") != "explicit":
                decisions.append(_decision(run_id, now, ev, "reject_uncertain",
                                           "pedestrian impact is not stated explicitly in the source; "
                                           "held back rather than guessed", **common))
                continue
            problems = assess_mod.verify_assessment(ev, a)
            if problems:
                decisions.append(_decision(run_id, now, ev, "reject_unverified_evidence",
                                           "; ".join(problems), **common))
                continue

            m = match_event(g, ev, a)
            if not m["ok"]:
                decisions.append(_decision(run_id, now, ev, "reject_location", m["reason"],
                                           match=m, **common))
                continue

            # A footway closure only removes segments when we know people cannot pass:
            # either the closed side was resolved, or the source says passage is blocked.
            if impact == "footway_closed" and (m.get("side_resolved")
                                               or a.get("blocks_all_pedestrian_passage") == "yes"):
                action = "close"
                why = "explicit closure; " + ("closed side resolved" if m.get("side_resolved")
                                              else "source says pedestrians cannot pass")
            else:
                action = "restrict"
                why = ("explicit footway closure but the source does not establish that pedestrians "
                       "cannot pass (other side may be open): published as a restriction"
                       if impact == "footway_closed" else f"explicit {impact.replace('_', ' ')}")
            entry = {
                "entry_id": ev["id"], "source": ev["source"], "source_url": ev["source_url"],
                "action": action, "edge_ids": m["edge_ids"], "street": m.get("street"),
                "length_m": m.get("length_m"),
                "summary": a.get("reasoning"), "evidence": a["evidence_quote"],
                "impact": impact,
                "valid_from": ev.get("start") or _iso(now),
                "valid_to": ev.get("end") or _iso(now + DEFAULT_VALIDITY),
                "end_assumed": ev.get("end") is None,
                "decided_at": _iso(now), "source_modified": ev.get("modified"),
                "location": ev.get("location"),
            }
            proposed.append(entry)
            proposal_decisions[ev["id"]] = _decision(
                run_id, now, ev, f"publish_{action}", why, match=m, edge_ids=m["edge_ids"],
                action=action, **common)

        # ---- 4. conflicts between records ---------------------------------
        # Two records claiming the same segments: the more recently modified one wins.
        proposed.sort(key=lambda en: en.get("source_modified") or "", reverse=True)
        claimed: dict[str, str] = {}
        resolved: list[dict] = []
        for en in proposed:
            overlap = [e for e in en["edge_ids"] if e in claimed]
            if overlap:
                winner = claimed[overlap[0]]
                kept = [e for e in en["edge_ids"] if e not in claimed]
                note = (f"{len(overlap)} segments also claimed by {winner}, which was modified "
                        "more recently and takes precedence")
                d = proposal_decisions[en["entry_id"]]
                d["conflict"] = note
                if not kept:
                    d["decision"], d["reason"] = "reject_conflict", note
                    d["edge_ids"], d["action"] = [], None
                    continue
                en = dict(en, edge_ids=kept)
                d["edge_ids"] = kept
            for e in en["edge_ids"]:
                claimed[e] = en["entry_id"]
            resolved.append(en)
        resolved.sort(key=lambda en: en["entry_id"])

        # ---- 5. gate: structure + baseline journeys -----------------------
        candidate = {
            "schema": SCHEMA, "version": prev["version"] + 1, "parent_version": prev["version"],
            "published_at": _iso(datetime.now(timezone.utc)), "as_of": _iso(now),
            "run_id": run_id, "mode": mode, "entries": resolved, "checks": {},
        }
        structural = validate_overlay(candidate, known_edge_ids=g.edge_index)
        if structural:
            raise RuntimeError("candidate overlay failed structural validation: " + "; ".join(structural))
        final, rejected, report = gate(g, candidate, now)
        for rj in rejected:
            d = proposal_decisions[rj["entry"]["entry_id"]]
            d["decision"], d["reason"] = "reject_validation", rj["reason"]
            d["action"] = None
        report_small = {k: v for k, v in report.items() if k != "journeys"}
        report_small["journeys"] = [
            {k: v for k, v in j.items()} for j in report["journeys"]]
        final["checks"] = report_small
        run["checks"] = {k: v for k, v in report_small.items() if k != "journeys"}
        if not report["passed"]:
            raise RuntimeError("baseline journeys fail even without new entries; nothing published")
        decisions.extend(proposal_decisions.values())

        # ---- 6. expire / withdraw what is no longer supported -------------
        final_ids = {en["entry_id"] for en in final["entries"]}
        seen_ids = {ev["id"] for ev in events}
        for old_id, old in prev_entries.items():
            if old_id in final_ids:
                continue
            end = parse_ts(old.get("valid_to"))
            if end and end <= now:
                why, dec = f"validity ended {old['valid_to']}", "expired"
            elif old_id not in seen_ids:
                why, dec = "record is no longer in the source feed", "withdrawn"
            else:
                why, dec = "record is still in the feed but no longer passes the checks above", "withdrawn"
            decisions.append(_decision(run_id, now, None, dec, why, event_id=old_id,
                                       edge_ids=old["edge_ids"], action=old["action"],
                                       location=old.get("location")))

        # ---- 7. publish (or confirm) --------------------------------------
        changed = (sorted(map(_entry_signature, final["entries"]))
                   != sorted(map(_entry_signature, prev["entries"]))) or prev.get("mode") != mode
        if changed:
            store.publish(final)
            run["status"], run["version_after"] = "published", final["version"]
        else:
            run["status"] = "no_change"
        store.write_state({"assessments": new_cache})

        counts: dict[str, int] = {}
        for d in decisions:
            counts[d["decision"]] = counts.get(d["decision"], 0) + 1
        run["counts"] = counts
        run["summary"] = (f"{run['feed'].get('records', len(raw))} records, {len(events)} in area, "
                          f"{len(final['entries'])} published entries, "
                          f"{report['total'] - len(report['failed'])}/{report['total']} baseline journeys pass")
    except Exception as exc:  # noqa: BLE001 - a failed run must leave the last good overlay in place
        run["status"] = "failed"
        run["error"] = f"{type(exc).__name__}: {exc}"
        run["traceback"] = traceback.format_exc(limit=4)
        run["summary"] = "run failed; previous overlay version left in place"
    run["finished_at"] = _iso(datetime.now(timezone.utc))
    try:
        store.record_run(run, decisions, raw, events)
    except Exception as exc:  # noqa: BLE001
        run["audit_error"] = f"{type(exc).__name__}: {exc}"
    return {"run": run, "decisions": decisions}


def live_feed() -> tuple[list[dict], dict]:
    return tfl.fetch_raw()


def fixture_feed(records: list[dict], label: str) -> Feed:
    def feed():
        return records, {"url": f"fixture:{label}", "fetched_at": _iso(datetime.now(timezone.utc)),
                         "http_status": None, "records": len(records)}
    return feed
