"""Segment cost model.

Units. Every segment e has a walking time tau_e = length_e / speed (seconds).
The generalised cost of a route is

    C(route) = sum_e tau_e * (1 + lam * sum_k w_k * x_{k,e})        [equivalent seconds]

where x_{k,e} in [0, 1] says how much of segment e is exposed to night-time
condition k, w_k is a dimensionless weight and lam >= 0 is the user's overall
preference strength. lam = 0 gives the fastest-walking baseline.

Interpretation of a weight: w_k is the extra walking time a person accepts to
avoid one unit of time in condition k. With w_unlit = 1, one minute on a
segment observed to be unlit costs the same as two minutes on a lit one.
The weights are stated preference ratios, not fitted risk coefficients, and the
resulting scores are not probabilities of anything (see docs/MODEL.md).

Missing data. Lighting is observed (OSM `lit` tag) on roughly 60 % of network
length. For an unobserved segment we use the expected exposure
x = P(unlit | segment group), estimated from the observed segments of the same
group in the same area with shrinkage towards the area-wide rate. The segment
stays flagged as "unknown" everywhere in the output, and the sensitivity
analysis reruns routing with that prior forced to 0 and to 1.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import WALK_SPEED_MPS
from .graph import MAIN_ROADS, Graph

STEPS_SPEED_FACTOR = 0.6        # stairs are slower than level walking
ISOLATED_ROAD_DIST_M = 25.0     # no vehicle-carrying street within this distance
PRIOR_SHRINK_M = 500.0          # pseudo-length for shrinkage of the group prior


@dataclass(frozen=True)
class Weights:
    unlit: float = 1.0       # observed unlit (or expected, if lighting unknown)
    isolated: float = 0.5    # away from any street: fewer people, fewer exits
    enclosed: float = 1.0    # underpass / covered passage: limited sightlines and exits
    quiet: float = 0.2       # no main road and no evening venue alongside (proxy)
    disrupted: float = 1.0   # agent-published restriction on this segment

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_WEIGHTS = Weights()
FEATURES = ("unlit", "isolated", "enclosed", "quiet")


def lighting_group(e: dict) -> str:
    if e.get("zone"):
        return "park"
    if e["road_dist"] > ISOLATED_ROAD_DIST_M:
        return "off_street"
    cls = e.get("road_class") or e["hw"]
    if cls in MAIN_ROADS:
        return "main_road"
    if e["hw"] == "service":
        return "service"
    return "local_street"


def lighting_priors(g: Graph) -> dict:
    """P(unlit | group) from observed segments, length-weighted, shrunk to the global rate."""
    obs = {}
    tot_unlit = tot_obs = 0.0
    for e in g.edges:
        grp = lighting_group(e)
        d = obs.setdefault(grp, {"unlit_m": 0.0, "observed_m": 0.0, "unknown_m": 0.0})
        if e["lit"] is None:
            d["unknown_m"] += e["len"]
        else:
            d["observed_m"] += e["len"]
            tot_obs += e["len"]
            if e["lit"] == "no":
                d["unlit_m"] += e["len"]
                tot_unlit += e["len"]
    p_global = tot_unlit / tot_obs if tot_obs else 0.5
    out = {"_global": round(p_global, 4)}
    for grp, d in obs.items():
        p = (d["unlit_m"] + PRIOR_SHRINK_M * p_global) / (d["observed_m"] + PRIOR_SHRINK_M)
        out[grp] = {
            "p_unlit": round(p, 4),
            "observed_km": round(d["observed_m"] / 1000, 2),
            "unknown_km": round(d["unknown_m"] / 1000, 2),
            "coverage": round(d["observed_m"] / (d["observed_m"] + d["unknown_m"]), 3),
        }
    return out


class EdgeFeatures:
    """Per-edge walking time and exposure vectors, computed once per graph."""

    def __init__(self, g: Graph, unknown_prior: float | None = None):
        self.g = g
        self.priors = lighting_priors(g)
        n = len(g.edges)
        self.tau = [0.0] * n
        self.x = {k: [0.0] * n for k in FEATURES}
        self.lit_known = [False] * n
        for i, e in enumerate(g.edges):
            speed = WALK_SPEED_MPS * (STEPS_SPEED_FACTOR if e["steps"] else 1.0)
            self.tau[i] = e["len"] / speed
            if e["lit"] == "no":
                self.x["unlit"][i] = 1.0
                self.lit_known[i] = True
            elif e["lit"] == "yes":
                self.lit_known[i] = True
            else:
                self.x["unlit"][i] = (unknown_prior if unknown_prior is not None
                                      else self.priors[lighting_group(e)]["p_unlit"])
            isolated = e["road_dist"] > ISOLATED_ROAD_DIST_M
            self.x["isolated"][i] = 1.0 if isolated else 0.0
            if e["enclosed"] == "underpass":
                self.x["enclosed"][i] = 1.0
            elif e["enclosed"] == "covered_passage":
                self.x["enclosed"][i] = 0.5
            main_road = (e.get("road_class") or e["hw"]) in MAIN_ROADS
            venues = e.get("venues") or 0
            self.x["quiet"][i] = 0.0 if (main_road or venues > 0) else 1.0

    def penalty(self, w: Weights) -> list[float]:
        """sum_k w_k x_{k,e} for every edge (dimensionless)."""
        wd = w.as_dict()
        n = len(self.tau)
        out = [0.0] * n
        for k in FEATURES:
            wk = wd[k]
            if wk:
                xs = self.x[k]
                for i in range(n):
                    if xs[i]:
                        out[i] += wk * xs[i]
        return out
