# The routing model: formulation, evidence and limits

## 1. What is being optimised

The walking network is a graph. Each segment *e* (a stretch of one OpenStreetMap way between two junctions) has

- a walking time τₑ = ℓₑ / v seconds, with ℓₑ its length in metres and v = 1.34 m/s (stairs: 0.6 v);
- exposures xₖ,ₑ ∈ [0, 1] to each night-time condition *k*.

For a preference strength λ ≥ 0 and weights wₖ ≥ 0, the generalised cost of a route P is

```
C_λ(P) = Σ_{e ∈ P} τₑ · (1 + λ · Σₖ wₖ · xₖ,ₑ)
       = T(P) + λ · Σₖ wₖ · Eₖ(P)
```

where T(P) is walking time and Eₖ(P) = Σ τₑ xₖ,ₑ is the **time spent exposed to condition *k***, both in seconds.
The app shows two members of this family: **Fastest** = argmin C₀ and **Better lit** = argmin C₁.

Properties that follow from the form, and that the tests check:

- **Units.** Everything is seconds. wₖ is dimensionless: the extra walking time accepted to avoid one second in
  condition *k*. With w = 1, a second on an unlit segment costs the same as two seconds on a lit one.
- **Exposure, not presence.** A condition is weighted by how long you are in it. A 20 m unlit cut-through and a
  600 m unlit park path are not treated alike.
- **Monotonicity.** Raising wₖ never increases Eₖ on the chosen route (`test_weights_are_monotone`). Raising λ
  never shortens the route. C₁(Better lit) ≤ C₁(Fastest) and T(Fastest) ≤ T(Better lit) by construction.
- **Pareto reading.** Varying λ traces the supported part of the Pareto front between time and weighted exposure.
  The two cards are two points on it.
- **Hard constraints** are separate from the cost: segments in a park whose gates are shut (or shut within
  30 minutes of departure), and segments the agent has published as closed, are removed for both routes.

## 2. The signals

| *k* | Definition of xₖ,ₑ | Unit it is observed at | Source | Coverage | wₖ |
|---|---|---|---|---|---:|
| `unlit` | 1 if `lit=no`; 0 if `lit` is `yes`, `24/7` or `automatic`; otherwise p̂(unlit \| group of *e*) | segment | OSM `lit` tag | recorded on 61% of length | 1.0 |
| `isolated` | 1 if the segment's midpoint is more than 25 m from any vehicle-carrying street | segment | OSM geometry | 100% | 0.5 |
| `enclosed` | 1 for `tunnel=yes` (pedestrian subway, underpass); 0.5 for a building passage or covered way | segment | OSM `tunnel`, `covered` | where tagged | 1.0 |
| `quiet` | 1 if no main road (tertiary or above) within 25 m **and** no pub, bar, restaurant, takeaway or nightclub within 50 m | segment | OSM road class, `amenity` nodes | 100%, but a proxy | 0.2 |
| `disrupted` | 1 on segments in an active agent entry with action `restrict` | segment | agent overlay | n/a | 1.0 |

Vehicle traffic and pedestrian activity are different things and we have a direct measurement of neither. Road
class stands in for the first and venue presence for the second; both are folded into one low-weight `quiet`
flag because that is all the data supports. It is labelled a proxy everywhere.

### Missing lighting

For a segment with no `lit` tag we do not know x. We use its expectation

```
x_unlit,e = p̂(unlit | g(e)) = (U_g + m · p̄) / (N_g + m)
```

where g(e) is the segment's group (park, off-street, main road, local street, service road), U_g and N_g are the
unlit and total *recorded* length in that group in this area, p̄ is the area-wide recorded unlit share and
m = 500 m is a shrinkage pseudo-length. Estimates (14 Nov 2026 extract): park 0.57, off-street 0.27,
service 0.06, main road 0.013, local street 0.009.

This is an assumption, and a biased one: mappers tag what they notice, so recorded segments are not a random
sample. Three things keep it honest. The segment stays flagged "not recorded" in every output and is drawn grey,
never yellow. The app reports the unrecorded share of each route. And `docs/SENSITIVITY.md` reruns everything with
the assumption forced to 0 and to 1, which turns out to be the largest uncertainty in the model.

## 3. Where the weights come from

They are stated preference ratios chosen before looking at any route output, not coefficients fitted to data.
We set an ordering and round magnitudes:

- `unlit` = 1 and `enclosed` = 1: the two conditions that most directly limit what a person can see and where
  they can go. One second there counts as two.
- `isolated` = 0.5: being away from any street matters, but it overlaps heavily with unlit park paths and we did
  not want to count the same path twice at full weight.
- `quiet` = 0.2: deliberately small, because it rests on proxies.

Background for the choice of factors, each checked against its source on 14 Nov 2026:

- **Lighting.** Welsh and Farrington's systematic review, *Effects of Improved Street Lighting on Crime* (Campbell
  Systematic Reviews 4(1), 2008, [doi:10.4073/csr.2008.13](https://doi.org/10.4073/csr.2008.13)), as summarised by
  the [College of Policing toolkit](https://www.college.police.uk/research/crime-reduction-toolkit/street-lighting):
  "Across the 13 studies reviewed, both violent and property crime was reduced by an average of 21% in areas with
  improved street lighting compared to areas without." The same review found that night-time crime did not fall
  more than daytime crime, so this is evidence that lighting is associated with less crime in an area, **not** that
  a lit street is safer than an unlit one at a given hour. It supports including lighting; it does not calibrate a weight.
- **Prospect, refuge and escape.** Fisher and Nasar, "Fear of Crime in Relation to Three Exterior Site Features:
  Prospect, Refuge, and Escape", *Environment and Behavior* 24(1), 1992, 35-65,
  [doi:10.1177/0013916592241002](https://doi.org/10.1177/0013916592241002): "fear of crime was highest in areas with
  refuge for potential offenders and low prospect and escape for potential victims." That study was about fear, on
  a university campus. It motivates `enclosed` and `isolated` as things people care about, which is what a route
  preference is.
- **Official advice.** The Metropolitan Police's
  [street robbery guidance](https://www.met.police.uk/cp/crime-prevention/personal-safety-how-to-stay-safe/mugging-street-robbery/):
  "Keep to busy, well-lit streets, walkways and paths which are more likely to be covered by CCTV."
- **Walking speed.** 1.34 m/s is the mean free-flow speed attributed to Weidmann (1993), as quoted by Daamen and
  Hoogendoorn, *EJTIR* 3(1), 2003, p. 41. We checked the secondary source, not Weidmann's report itself.
- **What we looked for and did not find:** a peer-reviewed figure for how much extra time or distance people accept
  at night for a lit or busier route. The closest study we could open (Basu et al., *Cities* 143, 2023,
  [doi:10.1016/j.cities.2023.104551](https://doi.org/10.1016/j.cities.2023.104551)) reports direction only. So the
  magnitudes above are ours, and the product should let users set λ themselves; the API already accepts it
  (`/api/route?lam=`).

## 4. Calibrated against outcomes, or scored on proxies?

Scored on observed conditions, with stated weights. Nothing here is calibrated against outcomes, and the app
never shows a risk figure.

We considered fitting a model and decided against it for reasons of data, not time alone:

- **Recorded crime** (police.uk) is monthly with no time of day, and each crime is moved to the nearest of a fixed
  set of anonymous points. In our area one month is about 3,100 crimes on 825 points. It cannot separate night
  from day, or the lit side of a junction from the unlit side.
- **Road casualties** (STATS19) do have exact coordinates, time and a `light_conditions` field, so a night-time
  pedestrian injury model is possible in principle.
- **Both lack a denominator.** A count per segment is mostly a count of how many people were there. Busy, lit main
  roads record the most incidents because they carry the most people. Fitting counts without exposure would teach
  the router to send people down empty alleys. The correct target is a rate per person-kilometre, and no open
  dataset gives night-time pedestrian flows per street in London.

What a fitted model would need, and what would justify it: segment-level night-time pedestrian flow estimates
(for example from a calibrated footfall model), STATS19 night pedestrian casualties as the outcome, a Poisson or
negative-binomial GLM with log-exposure offset and spatially blocked cross-validation, and a held-out deviance
improvement over the intercept-plus-exposure model. Until the denominator exists we think a transparent score
with visible unknowns is the more defensible product.

## 5. What we can and did validate

| Question | How | Result |
|---|---|---|
| Are returned routes valid walks? | every route in 16 baseline journeys checked for contiguity, endpoints, no closed segment, plausible pace | pass (`tests/test_routing.py`) |
| Does the model do what its definition says? | monotonicity in each weight; fastest ≤ preferred in time; preferred ≤ fastest in cost | pass |
| Do park hours match the published timetable? | sunset + 20 min against Royal Parks' 16:30 (Nov) and 21:45 (June) | within 6 minutes |
| How much does the recommendation depend on our weights? | halve and double each weight, 56 journeys | 95% keep the same corridor on average, worst case 82% |
| How much does it depend on unrecorded lighting? | force the prior to 0 and to 1 | 96% and 68% keep the same corridor |
| What does the default λ buy? | λ from 0 to 4 | at λ = 1: +67 s (3.5%), 97% less unlit time, 89% less off-street time |

Not validated, and not claimed: that Better lit routes have fewer incidents; that `lit=yes` means the lamps are
on tonight; that venue presence means people are about.

## 6. Baseline

The baseline is the fastest legal walking route on the same graph with the same closures (λ = 0). Every statement
the app makes about the preferred route is a difference against that baseline: extra minutes, lit share, metres
away from a street.
