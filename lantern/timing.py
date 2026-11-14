"""Trip timing in Europe/London: Leave now, Leave at HH:MM, Arrive by HH:MM.

Access is time-dependent (park gates, overlay entries with validity windows), so a route's
duration depends on when you set off. "Arrive by" therefore cannot subtract a fixed duration:
it searches for the latest departure whose own route, computed for that departure, still
arrives on time, and the result is re-checked against the routing engine before it is returned.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .access import LONDON, closed_zones
from .graph import Graph
from .overlay import active_entries
from .routing import RouteError, route_pair

KINDS = ("fastest", "preferred")
SEARCH_BACK = timedelta(hours=12)      # how far before the arrival time to look for a departure
GRACE = timedelta(seconds=60)          # "22:30" chosen at 22:30:40 still means today


def parse_clock(s: str) -> tuple[int, int]:
    """'HH:MM' or 'HHMM' (24-hour) -> (hour, minute). Raises ValueError."""
    s = (s or "").strip()
    if ":" in s:
        hh, mm = s.split(":", 1)
    elif len(s) == 4 and s.isdigit():
        hh, mm = s[:2], s[2:]
    else:
        raise ValueError(s)
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(s)
    return h, m


def next_occurrence(hour: int, minute: int, now: datetime) -> datetime:
    """The next time a London clock reads hour:minute, today if it has not passed yet."""
    local = now.astimezone(LONDON)
    cand = datetime(local.year, local.month, local.day, hour, minute, tzinfo=LONDON)
    if cand < local - GRACE:
        nxt = local.date() + timedelta(days=1)
        cand = datetime(nxt.year, nxt.month, nxt.day, hour, minute, tzinfo=LONDON)
    return cand


def day_offset(when: datetime, now: datetime) -> int:
    """0 = today in London, 1 = tomorrow, ..."""
    return (when.astimezone(LONDON).date() - now.astimezone(LONDON).date()).days


def context_signature(when: datetime, overlay: dict | None) -> tuple:
    """What routing depends on at `when`: closed zones and active overlay entries."""
    return (tuple(sorted(closed_zones(when))),
            tuple(sorted(en["entry_id"] for en in active_entries(overlay, when))))


def plan_arrival(g: Graph, origin: tuple[float, float], dest: tuple[float, float],
                 arrive: datetime, overlay: dict | None = None, lam: float = 1.0) -> dict[str, Any]:
    """Latest departure for each route kind that arrives by `arrive`.

    Returns {kind: {"depart": datetime, "arrive": datetime, "result": route_pair(...)}}; a kind
    is missing if no departure in the 12 hours before `arrive` has an open route. Raises
    RouteError if neither kind can be planned.

    The search steps back a minute at a time from the arrival time. Routing is recomputed only
    when the context (closed parks, active closures) changes, so a 12-hour search costs a
    handful of route computations.
    """
    cache: dict[tuple, Any] = {}

    def pair_at(t: datetime):
        sig = context_signature(t, overlay)
        if sig not in cache:
            try:
                cache[sig] = route_pair(g, origin, dest, t, overlay=overlay, lam=lam)
            except RouteError as exc:
                if exc.code != "no_route":
                    raise                      # outside coverage, same place: no time will help
                cache[sig] = exc
        return cache[sig]

    arrive = arrive.astimezone(LONDON)
    t = arrive.replace(second=0, microsecond=0)
    found: dict[str, dict] = {}
    last_error: RouteError | None = None
    steps = int(SEARCH_BACK.total_seconds() // 60)
    for _ in range(steps + 1):
        r = pair_at(t)
        if isinstance(r, RouteError):
            last_error = r
        else:
            for kind in KINDS:
                if kind not in found and t + timedelta(seconds=r[kind]["time_s"]) <= arrive:
                    found[kind] = {"depart": t}
            if len(found) == len(KINDS):
                break
        t -= timedelta(minutes=1)

    # Re-check each answer against the routing engine at its own departure time.
    out: dict[str, dict] = {}
    for kind, f in found.items():
        dep = f["depart"]
        for _ in range(5):
            try:
                r = route_pair(g, origin, dest, dep, overlay=overlay, lam=lam)
            except RouteError:
                dep -= timedelta(minutes=1)
                continue
            arr = dep + timedelta(seconds=r[kind]["time_s"])
            if arr <= arrive:
                out[kind] = {"depart": dep, "arrive": arr, "result": r}
                break
            dep -= timedelta(minutes=1)
    if not out:
        raise last_error or RouteError(
            "no_route", "No walkable route is open in the 12 hours before that arrival time.")
    return out
