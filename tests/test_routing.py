from __future__ import annotations

import pytest

from conftest import LATE, NIGHT
from lantern.agent.validate import verify_route
from lantern.config import BASELINE_JOURNEYS, PLACES
from lantern.cost import Weights
from lantern.geo import sunset_utc
from lantern.access import LONDON, zone_status
from lantern.routing import RouteError, route_pair

from datetime import date, datetime


def _od(o, d):
    return (PLACES[o]["lon"], PLACES[o]["lat"]), (PLACES[d]["lon"], PLACES[d]["lat"])


@pytest.mark.parametrize("o,d", BASELINE_JOURNEYS)
@pytest.mark.parametrize("when", [NIGHT, LATE])
def test_routes_are_valid_walks(g, o, d, when):
    r = route_pair(g, *_od(o, d), when)
    for kind in ("fastest", "preferred"):
        assert verify_route(g, r[kind], r["_ctx"], r["_src"], r["_dst"]) == []
        assert r[kind]["length_m"] > 100
        shares = r[kind]["lighting_share"]
        assert abs(sum(shares.values()) - 1) < 0.01
    # Fastest is the time minimum; preferred is the generalised-cost minimum.
    assert r["fastest"]["time_s"] <= r["preferred"]["time_s"] + 1e-6
    assert r["preferred"]["generalised_cost_s"] <= r["fastest"]["generalised_cost_s"] + 1e-6
    # Sanity: walking speed implies 12-13 min per km on level ground.
    for kind in ("fastest", "preferred"):
        pace = r[kind]["time_s"] / r[kind]["length_m"]
        assert 0.70 < pace < 0.85


def test_headline_journey_trades_time_for_lighting(g):
    r = route_pair(g, *_od("imperial_south_ken", "wilson_house"), NIGHT)
    assert not r["identical"]
    f, p = r["fastest"], r["preferred"]
    assert 0 < r["extra_s"] <= 5 * 60                       # a modest detour
    assert p["lighting_share"]["yes"] > f["lighting_share"]["yes"] + 0.1
    assert p["exposure_s"]["unlit"] < f["exposure_s"]["unlit"]
    assert p["exposure_s"]["isolated"] < f["exposure_s"]["isolated"]


def test_lambda_zero_reduces_to_fastest(g):
    r = route_pair(g, *_od("imperial_south_ken", "wilson_house"), NIGHT, lam=0.0)
    assert r["identical"]


def test_closed_parks_are_not_routed_through(g):
    r = route_pair(g, *_od("imperial_south_ken", "wilson_house"), LATE)
    assert {z["zone"] for z in r["closed_zones"]} == {"hyde_park", "kensington_gardens"}
    for kind in ("fastest", "preferred"):
        assert r[kind]["zones"] == []
    open_r = route_pair(g, *_od("imperial_south_ken", "wilson_house"), NIGHT)
    assert r["fastest"]["time_s"] > open_r["fastest"]["time_s"] + 600   # going round costs >10 min


def test_park_hours_match_published_times():
    # Kensington Gardens: official timetable says 16:30 in mid-November and 21:45 in mid-June.
    for d, hh, mm in ((date(2026, 11, 14), 16, 30), (date(2026, 6, 15), 21, 45)):
        closes = datetime.fromisoformat(
            zone_status("kensington_gardens", datetime(d.year, d.month, d.day, 12, 0, tzinfo=LONDON))["closes"])
        official = datetime(d.year, d.month, d.day, hh, mm, tzinfo=LONDON)
        assert abs((closes - official).total_seconds()) <= 6 * 60
    assert zone_status("hyde_park", datetime(2026, 11, 14, 22, 0, tzinfo=LONDON))["open"]
    # Within 30 minutes of the gates closing the park is not relied on.
    assert not zone_status("hyde_park", datetime(2026, 11, 14, 23, 45, tzinfo=LONDON))["open"]
    assert not zone_status("hyde_park", datetime(2026, 11, 15, 2, 0, tzinfo=LONDON))["open"]
    assert sunset_utc(date(2026, 11, 14)).hour == 16


def test_outside_coverage_and_same_place(g):
    with pytest.raises(RouteError) as e:
        route_pair(g, (-0.05, 51.55), (PLACES["wilson_house"]["lon"], PLACES["wilson_house"]["lat"]), NIGHT)
    assert e.value.code == "outside_coverage"
    p = PLACES["wilson_house"]
    with pytest.raises(RouteError) as e:
        route_pair(g, (p["lon"], p["lat"]), (p["lon"], p["lat"]), NIGHT)
    assert e.value.code == "same_place"


def test_unknown_lighting_is_reported_not_hidden(g):
    r = route_pair(g, *_od("imperial_south_ken", "wilson_house"), NIGHT)
    f = r["fastest"]
    assert f["lighting_share"]["unknown"] > 0
    assert f["lighting_coverage"] == pytest.approx(1 - f["lighting_share"]["unknown"], abs=0.01)
    # Forcing the prior for unknown segments to "all unlit" cannot make the preferred route
    # use more unknown-lighting distance than the optimistic prior does.
    pess = route_pair(g, *_od("imperial_south_ken", "wilson_house"), NIGHT, unknown_prior=1.0)
    opt = route_pair(g, *_od("imperial_south_ken", "wilson_house"), NIGHT, unknown_prior=0.0)
    assert pess["preferred"]["lighting_m"]["unknown"] <= opt["preferred"]["lighting_m"]["unknown"]


def test_weights_are_monotone(g):
    """Raising the weight on a condition never increases exposure to it on the chosen route."""
    od = _od("imperial_south_ken", "wilson_house")
    prev = None
    for w in (0.0, 0.5, 1.0, 2.0, 4.0):
        r = route_pair(g, *od, NIGHT, weights=Weights(unlit=w))
        x = r["preferred"]["exposure_s"]["unlit"]
        if prev is not None:
            assert x <= prev + 1e-6
        prev = x
