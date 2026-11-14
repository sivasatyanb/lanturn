"""Judgment step: does this disruption record change what a pedestrian can do?

Claude reads the published text of one TfL record and returns a structured assessment.
Everything Claude returns is treated as a claim to be checked, not as a fact:
the evidence quote must appear verbatim in the source text, and the location it names
must be confirmed against the walking network and the record's own coordinates
(see match.py) before anything is published.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Callable

from ..tfl import text_of

MODEL = "claude-opus-5-5"
PROMPT_VERSION = "2026-11-14.1"

IMPACTS = ("footway_closed", "footway_restricted", "crossing_affected", "none", "unclear")

SCHEMA = {
    "type": "object",
    "properties": {
        "pedestrian_impact": {"type": "string", "enum": list(IMPACTS)},
        "basis": {"type": "string", "enum": ["explicit", "inferred", "none"]},
        "evidence_quote": {"type": "string"},
        "blocks_all_pedestrian_passage": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "street": {"type": "string"},
        "extent": {"type": "string",
                   "enum": ["between", "junction", "point", "whole_street", "unknown"]},
        "cross_streets": {"type": "array", "items": {"type": "string"}},
        "side": {"type": "string", "enum": ["north", "south", "east", "west", "both", "unknown"]},
        "superseded_or_cleared": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["pedestrian_impact", "basis", "evidence_quote", "blocks_all_pedestrian_passage",
                 "street", "extent", "cross_streets", "side", "superseded_or_cleared", "reasoning"],
    "additionalProperties": False,
}

SYSTEM = """\
You are the judgment step in an automated pipeline that keeps a night-time walking route \
planner for London students up to date. The pipeline reads Transport for London road \
disruption records. Those records are written for drivers, so most of them describe lane \
closures, roadworks and delays that make no difference to someone on foot. A few of them do \
change what a pedestrian can do: a footway (pavement) that is closed or narrowed, a \
pedestrian crossing or subway that is out of use, a street closed to everyone including \
people walking.

Your job is to read one record and say what it establishes about pedestrians. Your answer \
is checked by deterministic code afterwards and, if it passes, it changes the routes that \
real people are shown, so the pipeline needs you to separate what the text states from what \
is merely plausible.

How to judge:
- A closure or restriction for vehicles does not by itself tell us anything about the \
footway. "Road closed", "lane closed", "carriageway closed" and "reduced to one lane" are \
vehicle statements. Unless the text also says something about pedestrians, footways, \
pavements, footpaths, crossings or subways, the impact is "none".
- Use "footway_closed" when the text states that a footway, pavement, footpath, pedestrian \
subway or the whole street is closed to people on foot. Use "footway_restricted" when it \
states the footway is narrowed, partly obstructed, or pedestrians are diverted but can still \
get through. Use "crossing_affected" when the statement is about a pedestrian crossing or \
its signals rather than the footway alongside the road.
- "basis" is "explicit" only when the source text says it in words. If you are reasoning \
from the kind of works to a likely effect on pedestrians, that is "inferred". If there is no \
pedestrian impact, basis is "none".
- If the text is ambiguous about whether pedestrians are affected, answer "unclear" rather \
than guessing; the pipeline holds unclear records back, which is the intended behaviour.
- "evidence_quote" must be copied character for character from the record text, as one \
contiguous span, and it should be the shortest span that supports your impact. Leave it \
empty when the impact is "none". The pipeline rejects quotes it cannot find in the source.
- The "currentUpdate" text is newer than the "comments" text. If the update says the \
problem has ended (reopened, cleared, works complete), set superseded_or_cleared to true.
- "blocks_all_pedestrian_passage" is "yes" only if the text says pedestrians cannot get \
past at all (for example both footways closed, no pedestrian access, street closed to \
pedestrians). If a footway on one side is closed and nothing suggests the other side is \
affected, answer "no". Otherwise "unknown".
- Location: give the street the pedestrian impact is on, without road numbers such as \
[A4]. "extent" is "between" when the text bounds it by two cross streets (list both in \
cross_streets), "junction" when it is at or near one cross street (list it), "point" for a \
specific spot such as outside a named building, "whole_street" when the whole street is \
affected, and "unknown" otherwise. "side" is the side of the street affected when the text \
says so, either as a compass side or implied by a direction of travel only if the text \
names the footway on that side; otherwise "unknown".
- "reasoning" is one or two plain sentences for the audit log.
"""

Assessor = Callable[[dict], dict]


def cache_key(ev: dict) -> str:
    h = hashlib.sha256((PROMPT_VERSION + "\n" + text_of(ev)).encode("utf-8")).hexdigest()[:16]
    return f"{ev['id']}:{ev.get('modified')}:{h}"


def _user_prompt(ev: dict) -> str:
    return (
        "Record from the TfL Unified API road disruption feed.\n\n"
        f"id: {ev['id']}\n"
        f"category: {ev.get('category')} / {ev.get('sub_category')}\n"
        f"severity: {ev.get('severity')}\n"
        f"status: {ev.get('status')}\n"
        f"hasClosures: {ev.get('has_closures')}\n"
        f"starts: {ev.get('start')}\nends: {ev.get('end')}\n"
        f"streets listed by TfL: {json.dumps(ev.get('streets') or [])}\n\n"
        "<record_text>\n"
        f"location: {ev.get('location') or ''}\n"
        f"comments: {ev.get('comments') or ''}\n"
        f"currentUpdate: {ev.get('current_update') or ''}\n"
        "</record_text>\n\n"
        "Assess what this record establishes for pedestrians."
    )


def claude_assessor(effort: str = "medium", api_key: str | None = None) -> Assessor:
    """Assessor backed by the Claude API. The key is read from the environment or passed
    in by the caller (e.g. from a Databricks secret); it is never logged or stored."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def assess(ev: dict) -> dict:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": _user_prompt(ev)}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined to assess this record")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("assessment truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError("no text block in model response")
        out = json.loads(text)
        out["_assessor"] = f"claude:{response.model}"
        out["_usage"] = {"input_tokens": response.usage.input_tokens,
                         "output_tokens": response.usage.output_tokens}
        return out

    return assess


_PED_WORDS = r"(footway|footpath|pavement|pedestrian|subway|crossing)"
_CLOSED_WORDS = r"(closed|closure|blocked|no access)"
_RESTRICT_WORDS = r"(restrict|narrow|obstruct|divert|partial)"


def rules_assessor(ev: dict) -> dict:
    """Keyword fallback used only when no Claude credential is configured.

    It is deliberately conservative and is labelled in every decision record, so a run
    made with it can never be mistaken for one that used model judgment.
    """
    text = text_of(ev)
    low = text.lower()
    impact, basis, quote, blocks = "none", "none", "", "unknown"
    for sentence in re.split(r"(?<=[.!?])\s+|\n| - ", text):
        s = sentence.lower()
        if not re.search(_PED_WORDS, s):
            continue
        if "crossing" in s or "signals" in s:
            impact = "crossing_affected"
        elif re.search(_CLOSED_WORDS, s) and re.search(r"(footway|footpath|pavement|subway)", s):
            impact = "footway_closed"
        elif re.search(_RESTRICT_WORDS, s):
            impact = "footway_restricted"
        else:
            impact = "unclear"
        basis = "explicit" if impact != "unclear" else "inferred"
        quote = sentence.strip()
        break
    if re.search(r"(both footways|no pedestrian access|closed to pedestrians)", low):
        blocks = "yes"
    streets = ev.get("streets") or []
    street = streets[0]["name"] if streets else re.sub(r"\[[^\]]*\]\s*", "", (ev.get("location") or "").split("(")[0]).strip().title()
    return {
        "pedestrian_impact": impact, "basis": basis, "evidence_quote": quote,
        "blocks_all_pedestrian_passage": blocks, "street": street, "extent": "point",
        "cross_streets": [], "side": "unknown",
        "superseded_or_cleared": bool(re.search(r"(reopened|now clear|cleared|works complete)", low)),
        "reasoning": "Keyword rules fallback (no model judgment).",
        "_assessor": "rules-fallback",
    }


def default_assessor() -> Assessor:
    mode = os.environ.get("LANTERN_ASSESSOR", "auto")
    if mode == "rules":
        return rules_assessor
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return claude_assessor()
    if mode == "claude":
        raise RuntimeError("LANTERN_ASSESSOR=claude but no Claude credential is configured")
    return rules_assessor


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_assessment(ev: dict, a: dict) -> list[str]:
    """Deterministic checks on the model's claim. Returns problems (empty = passes)."""
    problems = []
    if a.get("pedestrian_impact") not in IMPACTS:
        problems.append("unknown impact value")
        return problems
    if a["pedestrian_impact"] in ("footway_closed", "footway_restricted", "crossing_affected"):
        quote = _norm_ws(a.get("evidence_quote") or "")
        if len(quote) < 8:
            problems.append("no evidence quote supplied")
        elif quote not in _norm_ws(text_of(ev)):
            problems.append("evidence quote not found verbatim in source text")
        elif not re.search(_PED_WORDS + r"|on foot|walk", quote):
            problems.append("evidence quote does not mention pedestrians or footways")
        if not (a.get("street") or "").strip():
            problems.append("no street named")
    return problems
