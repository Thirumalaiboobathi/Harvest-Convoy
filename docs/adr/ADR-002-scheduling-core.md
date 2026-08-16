# ADR-002: GDD Rate Reconciliation, Decay Curve Resolution, Scheduling Core

- Status: Proposed (awaiting go-ahead)
- Date: 2026-08-16

## Context

Two things needed resolving before Phase 2 scheduling could be built on top
of Phase 1's agronomy core: the Phase 1 gate run's implied GDD rate didn't
match the assumed constant used to derive `MATURITY_GDD_ESTIMATED`, and
`decay.py` was stubbed with `NotImplementedError`, which blocks urgency
ranking. This ADR resolves both, then covers the scheduling design.

## Decision 1: GDD rate reconciled against real 5-year climatology

**The discrepancy, stated plainly:** the Phase 1 gate run computed 1947.2
accumulated GDD for a plot transplanted 2026-05-15, measured to 2026-08-16
(today) — a 94-day window. That's an implied rate of 1947.2 / 94 = **20.72
GDD/day**. `MATURITY_GDD_ESTIMATED` was derived from
`KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED = 17.5`, itself taken directly from
the brief's stated "27.5°C district mean" without independent verification
(flagged as such in ADR-001). A ~3.2 GDD/day gap between an assumed constant
and a real single-year measurement is exactly the kind of thing that constant
was flagged as needing follow-up for.

**Re-derivation:** queried Open-Meteo Archive API directly (fully historical,
no lag/bridging involved) for Theni coordinates (10.0104, 77.4768), the same
90-day window used for `ADT45_FIELD_DURATION_DAYS_ESTIMATED`
(May 15 – Aug 12), across the five most recent complete years:

| Year | Days | Mean GDD/day | Total GDD |
|---|---|---|---|
| 2021 | 90 | 19.169 | 1725.2 |
| 2022 | 90 | 19.111 | 1720.0 |
| 2023 | 90 | 20.385 | 1834.6 |
| 2024 | 90 | 18.361 | 1652.5 |
| 2025 | 90 | 19.041 | 1713.7 |

450 days total, overall mean = **19.2133 GDD/day**. (Method: mean of the
450 individual daily GDD values; the mean of the five yearly means is
identical to four decimal places, so no single year dominates.)

This 5-year mean (19.21) sits between the old assumed value (17.5) and the
Phase-1 single-year sample (20.72) — consistent with 2026 running warmer
than the 5-year average rather than the old constant being wildly wrong in
direction, just too low. Using the 5-year figure instead of either the
brief's unverified assumption or a single hot year is the right call:
climatology, not a one-off sample, should set a threshold used every season.

**Updated constants** (`crop_params.py`):

| Constant | Old | New |
|---|---|---|
| `KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED` | 17.5 (brief-asserted, unverified) | 19.2133 (computed, 5-yr Open-Meteo climatology) |
| `MATURITY_GDD_ESTIMATED` | 1575.0 | 1729.20 (+154.2, +9.8%) |

This constant remains a single, correctable line — if TNAU/ICAR thermal-time
data for ADT 45 turns up, both derived constants disappear in favor of a
directly-sourced number.

## Decision 2: overripe decay curve — no sourceable curve found; linear DERIVED proxy

Searched specifically for a loss curve indexed to **days past physiological
maturity** (the axis the coordinator needs for urgency ranking), beyond what
ADR-001 already covered. Findings:

- Wang et al. 2021 (*European Journal of Agronomy* 131:126382, Northeast
  China) reports 9.8–24.4% yield loss per 10-day harvest delay — but indexed
  to **days after heading** (45–59 DAH studied), not days past maturity.
  Same mismatch flagged in ADR-001; still unresolved, and the ScienceDirect
  abstract page 403'd on a second direct-fetch attempt, so I still can't
  independently confirm the number against primary text.
- **A search-engine synthesis produced a specific, plausible-looking figure —
  California rice head-rice-yield dropping from 63.8% to 45.8% over a 10-day
  delay — attributed to a named article. I fetched that article directly to
  confirm it before using it, and the number is not in the source.** The
  article mentions a harvest running "about 10 days later than usual" with
  no yield percentages attached. I'm recording this explicitly because it's
  a concrete instance of the exact failure mode the project's honesty rule
  exists to catch, and because it means I'm now treating *any* number that
  reached me only via search-summary (not a direct, successful fetch of
  primary text) as unverified by default — including the Wang et al. 9.8–24.4%
  figure above, which arrived the same way.
- The Champa rice PMC paper (PMC12240373, fetched directly) discusses harvest
  moisture bands (13–15%, 23–25%, 39–41%) and cites a 30-day-after-50%-flowering
  optimum, but has no quantified delay-vs-loss relationship either.
- Recurring but unquantified across nearly every source: paddy should be
  harvested within roughly 10–15 days of physiological maturity; risk of
  lodging, shattering, and moisture-related quality loss increases
  materially beyond that. Directionally consistent, not a curve.

**Conclusion: no citable, maturity-indexed, quantified loss curve for paddy
was found and verified.** Per your instruction, implementing the documented
linear proxy instead of leaving the stub in place:

```python
# crop_params.py
# DERIVED, NOT sourced: no citable day-past-maturity loss curve for paddy
# was found (see ADR-002 Decision 2 for what was searched, and a specific
# search-hallucinated figure that was caught and discarded). Modeled as a
# linear ramp from 0 at maturity to full loss (1.0) at this horizon, chosen
# as a round number past the recurring-but-unquantified "harvest within
# 10-15 days" guidance found across multiple sources. This sets the *shape*
# used for urgency ranking, not a validated yield-loss curve -- do not cite
# it as a quality-loss percentage in the demo narrative.
DECAY_HORIZON_DAYS_ESTIMATED: int = 20
```

```python
# decay.py
def decay_fraction(days_past_maturity: int) -> float:
    if days_past_maturity < 0:
        raise ValueError("days_past_maturity must be >= 0")
    return min(1.0, days_past_maturity / crop_params.DECAY_HORIZON_DAYS_ESTIMATED)
```

Monotonic, capped at 1.0, deterministic, unblocks Phase 2. Flagged as a
known limitation, not presented as measured.

## Decision 3: capacity budget

`scheduling/capacity.py` computes the number of usable harvest-days before a
rain event closes the window, from a forecast and a rain threshold (mm),
and converts that into an acreage budget via `machine_capacity_acres_per_day`
(default 3.5, per-cluster configurable — this is already a `Cluster` field
per the brief's entity model, not a new one).

- A "usable day" is any forecast day where precipitation stays at or below
  the threshold. The budget stops counting at the **first** day that exceeds
  it — once rain closes the window, days after that aren't usable even if a
  later day happens to be dry again, because the paddy is down/lodged/wet by
  then. This is a deliberate simplification (no re-opening after the first
  breach) and matches "harvest timing is forced by weather" from the brief.
- No rain in the forecast at all → budget is the full requested horizon
  (bounded by the 16-day forecast limit from ADR-001/Phase 1).
- Precipitation data comes from the same Open-Meteo Forecast call already
  built in Phase 1 (`precipitation_sum`, added to the existing daily field
  list) — no new API surface.

## Decision 4: route ordering

`scheduling/route.py` orders plots that fit the capacity budget by urgency
first (days-past-maturity / decay fraction, immature plots excluded before
this step — see Decision 5), then greedily sequences the accepted plots by
nearest-neighbor straight-line (haversine) distance from the machine's
current/last position, per the brief's explicit "no real road routing" scope
call. This is a deterministic greedy solver, not an optimal TSP solve —
sufficient for an 8-plot demo cluster and consistent with "nobody will
challenge straight-line distance."

## Decision 5: "too green" is a hard exclusion, not a ranking position

A plot below `MATURITY_GDD_ESTIMATED` is classified `not_ready` and removed
from contention entirely before ranking or capacity allocation — it cannot
be selected, contested, or routed regardless of how much capacity is left.
This is the load-bearing classification the brief calls out explicitly: the
system must be able to tell a farmer "not yet" as a first-class, confident
outcome, not a side effect of losing a ranking. Implemented as a distinct
`PlotStatus.NOT_READY` outcome returned before any capacity math runs, so it
can never be silently overridden by capacity availability.

## Consequences

- `MATURITY_GDD_ESTIMATED` moves from 1575 to 1729.2 — every plot's maturity
  date in the seed cluster shifts later by a few days versus what Phase 1's
  number would have produced. Sign this off along with the rest of the ADR;
  it's a second real change to the same constant in two phases, which is
  expected while it's grounded in progressively better data, not a sign of
  instability.
- The decay curve is explicitly a ranking-shape placeholder. The demo
  narrative and README must not claim it represents measured yield loss.
- Capacity/route logic remains 100% deterministic Python — no LLM
  involvement, consistent with the architectural rule.
