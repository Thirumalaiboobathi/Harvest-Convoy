# ADR-001: Agronomy Core — Crop Constants, Anchor Date, Weather Seam

- Status: Proposed (awaiting go-ahead)
- Date: 2026-08-16

## Context

Phase 1 builds the deterministic agronomy core: GDD accumulation, a maturity
threshold for ADT 45, and an Open-Meteo client that bridges historical and
forecast data. Per the project's honesty rule, every constant needs a source
or an explicit derivation — no bare numbers. This ADR records what I found,
what I could not verify, and the resulting design decisions, before any code
is written.

## Decision 1: GDD anchor is `transplant_date`, not `sowing_date`

Kuruvai-season paddy in Tamil Nadu is transplanted, not direct-seeded (direct
seeding guides I found are for other regions/seasons). Two dates exist per
plot: nursery sowing and field transplanting. They differ by the nursery
period, and thermal-time requirements quoted "from sowing" and "from
transplanting" are not interchangeable — using the wrong one shifts every
maturity date by the nursery period (roughly three weeks).

**We anchor GDD accumulation at `transplant_date`.** Reasons:

1. **It's what a farmer can reliably report.** Nursery sowing is often a
   shared, diffuse event (one nursery bed feeding several fields) that a
   smallholder may not precisely recall three months later. Transplanting —
   moving seedlings into *this specific plot* — is a single, memorable, and
   physically anchored event: the day the crop starts existing where the
   combine will one day harvest it.
2. **It matches the entity model.** A `Plot` is a field with lat/lon. GDD
   computed from that field's weather data is only meaningful once the crop
   is actually standing in that field's microclimate — during the nursery
   period the crop is (usually) somewhere else.
3. **It has precedent in the literature, not just convenience.** Rice
   phenology models commonly track thermal time from transplanting; e.g. a
   continuous-AGDD model for early rice phenology (Zhang et al., *Remote
   Sensing* 2022, MDPI, DOI 10.3390/rs14215337) states rice growth is
   "continuously accumulated from transplanting through physiological
   maturity," citing ~2060 GDD°C for long-duration cultivars on that basis.
   I could not fetch this paper's full text directly (MDPI returned HTTP 403
   to automated fetches) — this is a search-engine-surfaced quote, not a
   verified full-text read, and I'm flagging that distinction rather than
   overstating my confidence in it. It's corroborating evidence for the
   modeling choice, not the basis for a numeric constant.

**Field naming:** `Plot.transplant_date`, not `sowing_date`. If a future
direct-seeded scenario is ever added, that would need its own field and its
own duration constants — out of scope now (paddy/ADT45/transplanted only,
per the brief).

**Nursery age (needed to convert TNAU's sowing-anchored duration below into
a transplant-anchored one):** TNAU's seedling-age guidance for short-duration
varieties is 18–22 days. I take the midpoint, 20 days, and mark it
`ADT45_NURSERY_AGE_DAYS_ESTIMATED` — a chosen point in a cited range, not
itself independently measured for ADT 45.

## Decision 2: `T_BASE_C = 10.0`, sourced

Verified directly (fetched, not just search-surfaced): Sanwong, P., Sanitchon,
J., Dongsansuk, A., & Jothityangkoon, D. (2023), "High Temperature Alters
Phenology, Seed Development and Yield in Three Rice Varieties," *Plants*
12(3):666 (PMC9921536, open access). Equation 1 states explicitly: "Tbase is
the base temperature (Tbase of rice = 10.0 °C)."

You cited Pereira et al. 2025 (ScienceDirect, base/upper temperature
thresholds review) as giving a rice range of 8–12°C. I could not independently
verify that specific figure — both the ScienceDirect page and its ResearchGate
mirror returned HTTP 403 to automated fetches (paywalled/bot-blocked). I'm
citing Sanwong et al. as the primary, checkable source for the 10.0°C value
that matches the brief's locked constant, and noting Pereira et al. as a
secondary reference whose exact number I have not confirmed myself. If you
have paywall access, worth a five-minute check to fold in the real citation.

## Decision 3: ADT 45 maturity threshold — NOT sourced, derived (as instructed)

No TNAU or ICAR publication giving a direct thermal-time (GDD) requirement
for ADT 45 turned up despite several targeted searches (TNAU AgriTech portal,
ICAR/TNAU research highlights, ResearchGate heat-unit studies). Per your
instruction, this is derived from published crop duration, not invented.

**Crop duration — found a conflict, resolved it:**
- TNAU AgriTech Portal, "Paddy Varieties of Tamil Nadu"
  (agritech.tnau.ac.in/expert_system/paddy/TNvarieties.html — fetched
  directly): ADT 45 duration **110 days**, listed under the 90–120 day
  short-duration category, explicitly suitable for Kuruvai (among other short
  windows).
- An unofficial mirror ("agri.bot") claims 130–135 days, without citing TNAU
  or ICAR.
- I'm using **110 days** and discarding the 130–135 figure: it comes from a
  non-primary source, and 130–135 days doesn't fit a "short duration (90–120
  day)" classification that the same TNAU page simultaneously assigns to this
  variety for Kuruvai suitability — internally inconsistent with itself.

**Derivation, shown explicitly (this is what lands in `crop_params.py`):**

```
ADT45_CROP_DURATION_DAYS = 110          # TNAU AgriTech Portal (sowing to maturity)
ADT45_NURSERY_AGE_DAYS_ESTIMATED = 20    # DERIVED: midpoint of TNAU's 18-22 day
                                          # short-duration nursery age guidance
ADT45_FIELD_DURATION_DAYS_ESTIMATED = ADT45_CROP_DURATION_DAYS - ADT45_NURSERY_AGE_DAYS_ESTIMATED
                                          # = 90 days, transplant to maturity

KURUVAI_MEAN_GDD_PER_DAY_THENI = 17.5    # project-assumed (brief-supplied):
                                          # 27.5C district mean - 10C T_base.
                                          # NOT independently verified by me —
                                          # a real climatological figure computed
                                          # from Open-Meteo Archive history for the
                                          # actual demo coordinates/window would be
                                          # stronger; flagged as a follow-up once
                                          # the seed cluster's exact plots/dates are
                                          # fixed (Phase 2/7), not done now.

MATURITY_GDD_ESTIMATED = ADT45_FIELD_DURATION_DAYS_ESTIMATED * KURUVAI_MEAN_GDD_PER_DAY_THENI
                                          # = 90 * 17.5 = 1575 GDD from transplant_date
```

**This is a materially different number from the brief's original ~1900 GDD
placeholder** — not because the placeholder was arbitrary, but because it
implicitly assumed a sowing-anchored ~108-day duration, and switching to a
transplant anchor legitimately removes the nursery period's ~350 GDD from the
threshold. Both the anchor change and the resulting number need your sign-off
since they shift every projected maturity date in the system.

The constant is a plain module-level float in `crop_params.py`, not buried in
scheduling logic, so it's a one-line change later if TNAU/ICAR thermal-time
data for ADT 45 turns up.

## Decision 4: overripe decay curve — deferred out of this pass

Not in the Phase 1 gate ("compute maturity... reproducible" — no decay
requirement), but listed under `agronomy/decay.py` in the repo layout, so
flagging explicitly rather than silently dropping it.

What I found: one well-cited, directly quantified relationship — Wang et al.
2021 (*European Journal of Agronomy* 131:126382, Northeast China, 2017–2020
field trials) — reports a 10-day harvest delay costing 9.8–24.4% yield loss.
But it's indexed to **days after heading**, not days after physiological
maturity, and converting between those requires another uncited offset
(grain-filling duration, roughly 30 days for short-duration indica but not
confirmed for ADT 45). Stacking that conversion on top of an already 2.5×-wide
loss range would mean shipping a decay curve built from two compounded
guesses presented as one constant — exactly what the honesty rule is meant to
prevent.

**Recommendation: build `decay.py`'s interface now (so `scheduling/` in
Phase 2 has something to call) but leave its curve unparameterized /
explicitly stubbed, and treat sourcing it properly as a short, separate
follow-up** — either a more targeted search for maturity-indexed data, or a
deliberate choice with you to derive it transparently the way we just did for
the GDD threshold. Tell me if you'd rather I take a first pass at the derived
version now instead of stubbing it.

## Decision 5: Open-Meteo seam — detect and bridge, don't assume a fixed lag

Empirically tested against 10.0°N, 77.5°E (Theni-area) just now, live:

- Archive API default ("Best Match", no `models` param) returned real
  (non-null) daily max/min temperature **through today**, 2026-08-16 — no
  gap. Its documented "5 days delay" applies to raw ERA5 only.
- Forcing `models=era5` explicitly reproduces that lag: temperatures null for
  the most recent 6 days, real data stopping at 2026-08-10.
- Forecast API's `past_days` values for the overlapping window matched
  Archive's Best-Match values exactly (same underlying blended source).
- Forecast API forward horizon: confirmed 16 days (`forecast_days=16` →
  2026-08-16 through 2026-08-31).

**Design:** call Archive API with the default Best-Match blend (no `models`
override) for `[transplant_date, today]`. Treat any `null` daily value in the
response as "not yet settled" and, only for those specific missing dates,
backfill from the Forecast API's `past_days` data. This is dynamic gap
detection, not a hardcoded "always bridge the last N days" — it's correct
whether the current near-zero lag holds, reverts to the documented ~5-6 days,
or changes again, and it's what the seam test (below) actually exercises.

For `(today, today+15]`: Forecast API's forward daily values. Beyond day 15:
climatological mean (existing plan, unchanged) — implementation TBD in a
later Phase 1 commit or Phase 2 if the demo's forecast-horizon plots don't
need it yet.

**Timezone:** requesting `timezone=Asia/Kolkata` (confirmed supported,
`utc_offset_seconds: 19800`) on both endpoints, not UTC. Daily min/max must
align to IST calendar days to match farmer-reported dates and each other at
the join point — a UTC-vs-IST mismatch would silently misalign the seam by
part of a day even with correct gap detection.

## Decision 6: caching

Disk cache keyed on `(lat, lon, start_date, end_date, endpoint)`, stored as
JSON under a cache directory (not committed). Historical (Archive, fully
past, no nulls) responses are cached indefinitely — they don't change once
settled. Responses containing any recent/null-bridged days are cached with a
short TTL (or simply not cached for the trailing bridged window) since that
data can still be revised as ERA5-final replaces the IFS-blended values.
Exact cache location/format is an implementation detail, not a design
question — will land in `weather/openmeteo.py`.

## Tests (Phase 1 gate)

1. Hand-computed 5-day GDD window, fixed synthetic temps, asserted exact
   expected sum (arithmetic check, per your ask).
2. A day with mean temp below `T_BASE_C` contributes exactly 0 to GDD, not a
   negative number.
3. Archive→Forecast seam: no duplicate dates, no missing dates, across the
   join, run against live data for the same Theni-area coordinate used above.
4. (New, given the null-bridging design) a test that a null value in a
   historical response is correctly backfilled from the forecast source
   rather than silently propagated as missing or zero.

## Consequences

- Every number in `crop_params.py` will carry a source URL or a `# DERIVED:`
  comment; nothing bare.
- The transplant-anchor decision is a real, disclosed break from the
  brief's original ~1900 GDD placeholder — needs your explicit sign-off,
  not just an ADR note, since it changes every plot's projected date.
- `decay.py` ships as a stub interface this phase, not a sourced curve —
  flagging this as a known gap rather than quietly filling it with a shaky
  number.
- The weather seam is more defensive than "always bridge N days" — slightly
  more code (null-detection + selective backfill) for correctness that
  survives the lag characteristics changing.
