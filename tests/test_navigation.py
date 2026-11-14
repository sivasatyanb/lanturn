"""Navigation preview: heading and turn instructions come from route geometry and street names."""
from __future__ import annotations

import math

from conftest import NIGHT
from lantern import navigation
from lantern.config import PLACES
from lantern.geo import to_lonlat
from lantern.routing import route_pair


def ll(key):
    return (PLACES[key]["lon"], PLACES[key]["lat"])


def edge(points_m, name=None, hw="residential", **extra):
    """A synthetic edge from local metre coordinates."""
    coords = [list(to_lonlat(x, y)) for x, y in points_m]
    length = sum(math.dist(points_m[i], points_m[i + 1]) for i in range(len(points_m) - 1))
    e = {"len": length, "name": name, "hw": hw, "steps": False, "enclosed": None, "zone": None}
    e.update(extra)
    return (e, coords)


def test_real_route_instructions(g):
    r = route_pair(g, ll("imperial_south_ken"), ll("wilson_house"), NIGHT)
    for kind in ("fastest", "preferred"):
        nav = r[kind]["nav"]
        assert nav["depart"]["instruction"] == "Head north on Exhibition Road"
        assert nav["heading"] > 335 or nav["heading"] < 25            # first stretch runs north
        assert abs(nav["length_m"] - r[kind]["length_m"]) < 0.02 * r[kind]["length_m"]
        ms = nav["maneuvers"]
        assert ms[-1]["type"] == "arrive" and ms[-1]["at_m"] == nav["length_m"]
        assert [m["at_m"] for m in ms] == sorted(m["at_m"] for m in ms)
        assert all(m["at_m"] >= 8 for m in ms)
        named = [m for m in ms if m["street"]]
        assert named and all(m["street"] in m["instruction"] for m in named)
        assert nav["start"] == r[kind]["segments"][0]["coords"][0]


def test_right_and_left_turns_use_street_names():
    route = [edge([(0, 0), (0, 120)], "Alpha Road"), edge([(0, 120), (150, 120)], "Beta Street"),
             edge([(150, 120), (150, 300)], "Gamma Lane")]
    nav = navigation.build(route)
    assert nav["heading"] < 1 or nav["heading"] > 359
    assert nav["depart"]["instruction"] == "Head north on Alpha Road"
    first, second, last = nav["maneuvers"]
    assert first["type"] == "right" and first["at_m"] == 120 and first["instruction"] == "Turn right onto Beta Street"
    assert second["type"] == "left" and second["at_m"] == 270 and second["instruction"] == "Turn left onto Gamma Lane"
    assert last["type"] == "arrive" and last["at_m"] == 450


def test_unnamed_path_is_called_a_path():
    route = [edge([(0, 0), (80, 0)], None, "footway"),
             edge([(80, 0), (80, -90)], None, "footway", zone="hyde_park")]
    nav = navigation.build(route)
    assert nav["depart"]["instruction"] == "Head east along the footpath"
    assert nav["maneuvers"][0]["instruction"] == "Turn right onto the path in Hyde Park"
    assert nav["maneuvers"][0]["street"] is None


def test_route_without_a_turn_only_arrives():
    nav = navigation.build([edge([(0, 0), (0, 200)], "Long Road"), edge([(0, 200), (0, 520)], "Long Road")])
    assert [m["type"] for m in nav["maneuvers"]] == ["arrive"]
    assert nav["maneuvers"][0]["at_m"] == 520


def test_short_crossing_does_not_read_as_a_new_street():
    # 6 m unnamed crossing between two stretches of the same street: nothing to announce.
    route = [edge([(0, 0), (0, 100)], "High Street"), edge([(0, 100), (0, 106)], None, "footway"),
             edge([(0, 106), (0, 240)], "High Street")]
    assert [m["type"] for m in navigation.build(route)["maneuvers"]] == ["arrive"]


def test_empty_route():
    assert navigation.build([])["maneuvers"] == []
