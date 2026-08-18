# ADR-008: Tamil Nadu Generalization and Tamil-Language Farmer Interface

- Status: **Implemented, Tamil closed (2026-08-18).** Approved with four
  adjustments — see the inline "APPROVED, adjusted" notes in Decisions
  2, 4, 7, and 12 for exactly what changed from the original proposal —
  then built in order (a)-(e), followed by four rounds of native-speaker
  review (Decisions 14–16). One real bug caught mid-implementation and
  fixed inline (a bare-number area fallback that misread the date's own
  digits — see Decision 9's correction note); one pre-existing bug fixed
  alongside the Tamil work but confirmed independent of it (the
  crop-confirmation step accepted any non-empty reply, no real yes/no
  check — Decision 11); one pre-existing gap found and closed
  (webhook.py's escalation resolution reason was hand-authored English
  with no language awareness — now dispatches through
  messages_ta.py/messages_en.py); a live-verified Nova Pro Tamil-script
  generation failure that reversed the original architecture from
  native-prompted to templated (Decisions 14–15). Full suite: 286
  passed, 2 deselected (`bedrock`-marked live tests, collection-verified
  not to be broken, not executed to avoid unnecessary cost). Nothing
  here touched the deployed AgentCore artifact. Every Tamil string
  dumped via `uv run python -m scripts.print_tamil_strings` for
  native-speaker review across four rounds — round 4 is the final
  wording.
- Date: 2026-08-17 (Decisions 1–13), updated 2026-08-18 (Decisions
  14–16)

## Context

Phases 0–7 are complete and deployed against one seeded demo cluster
(Kamatchipuram, Theni district). Two pieces of work, both farmer-facing
correctness/credibility questions, not cosmetics:

1. Nothing in the deterministic core is actually district-specific — GDD is
   accumulated per-plot from that plot's own lat/lon. But one constant
   *is* Theni-derived, and its name says so. This ADR audits every
   Theni-shaped assumption, states plainly whether generalizing to other TN
   districts is safe, and adds a second real seeded cluster in a
   different climate to demonstrate it rather than assert it.
2. The farmer interface is English-only today. This ADR adds Tamil as a
   first-class, per-farmer language, architected so a third language is a
   config addition later, but implements and verifies exactly one now —
   the only one you can personally verify.

No changes here touch the deployed AgentCore artifact, per your
instruction — this is code + a second local/seeded cluster only, until
you say otherwise. Crop scope stays paddy ADT 45 only; a maize roadmap
line goes in the README, not in this system.

**No `CLAUDE.md` exists in this repo** — I read the ADRs and source
instead. Flagging this since you asked me to read it first; happy to
create one from what's accumulated in the ADRs if useful, separately from
this work.

---

## Part 1: Generalizing from Theni to all of Tamil Nadu

### Decision 1: audit result — one Theni-shaped assumption, and it's real

Grepped `src/` and `scripts/` for `theni`/`kamatchipuram`/hardcoded
lat-lon literals. Findings:

- **`agronomy/`, `scheduling/`, `agents/coordinator.py`, `agents/advocate.py`:
  zero district-specific code.** GDD accumulation
  (`watcher.py:run_daily_watch`) calls `get_daily_temperatures(p.lat,
  p.lon, p.transplant_date, today)` per plot — every plot's thermal time
  comes from *that plot's own coordinates*, not a fixed location. Capacity,
  route, decay, and fairness math take a `Cluster`/`Plot`/`Farmer` as
  arguments and touch no geography beyond what's passed in.
- **`app.py`, `check_watcher_health.py`, `seed_cluster.py`: Kamatchipuram
  appears only as a payload example / CLI default / the one seeded
  fixture set** — not a code assumption, a data gap (Decision 3 below).
- **The one real constant:** `crop_params.py`'s
  `KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED` (19.2133), used exactly once,
  to derive `MATURITY_GDD_ESTIMATED` (1729.2 GDD, ADR-002). This is the
  single global maturity threshold every cluster's every plot is compared
  against, regardless of where that plot actually is.

### Decision 2: does a Theni-derived threshold misproject maturity elsewhere? Yes, measurably — here's the number

You asked me not to paper over this, so the direct answer: **applying
`MATURITY_GDD_ESTIMATED` to a different district's real weather does
change projected maturity dates by a material amount, and I can show you
by how much, because I re-ran the exact ADR-002 methodology against a
second real Tamil Nadu location just now.**

Same window (May 15–Aug 12, 2021–2025, 450 days), same formula
(`T_BASE_C = 10.0`), live Open-Meteo Archive data, this time at
**Naducauvery, Thiruvaiyaru taluk, Thanjavur district** (10.861°N,
79.046°E — the second cluster site, Decision 3):

| | Theni (existing) | Naducauvery, Thanjavur (new) |
|---|---|---|
| 2021 | 19.169 | 21.810 |
| 2022 | 19.111 | 20.728 |
| 2023 | 20.385 | 21.996 |
| 2024 | 18.361 | 20.668 |
| 2025 | 19.041 | 21.053 |
| **5-yr mean GDD/day** | **19.2133** | **21.2509** |

Thanjavur's delta plains run **~2.04 GDD/day hotter** than Theni's
foothill-adjacent climate over this window — about **10.6% faster**
thermal-time accrual. Concretely: a plot transplanted the same calendar
day in each district does **not** reach `MATURITY_GDD_ESTIMATED` (1729.2)
on the same calendar day — Thanjavur gets there in ≈1729.2/21.2509 ≈
**81 days**, Theni in exactly 90 (by construction of the existing
constant). **That's roughly a 9-day earlier maturity call in Thanjavur
than the same transplant date would produce in Theni**, under the current
single shared threshold.

**Is this a bug? No — and here's the distinction that matters:**

- The *mechanism* (accumulate real per-plot GDD, compare to one threshold)
  is exactly what a thermal-time model is supposed to do, and it's
  already fully location-independent in this codebase — a hotter district
  correctly projecting *earlier* maturity from the same transplant date is
  the model working as designed, not malfunctioning.
- What's uncertain is whether **1729.2 GDD is the right threshold** for
  ADT 45 at all — and that uncertainty was already disclosed in
  ADR-001/002 for Theni alone. It does not get *worse* by using the same
  number in Thanjavur; the same single estimate is applied uniformly
  everywhere, which is the only defensible thing to do without a second
  independently-sourced district-specific threshold (which doesn't exist —
  I didn't find one for Thanjavur either).
- What I can't verify, and want to say plainly rather than imply I
  checked: TNAU's `ADT45_CROP_DURATION_DAYS = 110` figure doesn't state
  which district's climate it was calibrated against. If TNAU's day-count
  assumed something closer to Thanjavur's warmer delta conditions, then
  the *Theni*-anchored threshold is the one running low; if TNAU assumed
  something closer to Theni's, Thanjavur's projected dates are the ones
  running early. There's no way to tell from the source I have, and no
  ground-truth maturity date in either district to check either
  projection against. That's a real, standing gap — same category as the
  decay curve — not something this ADR resolves, only makes visible with
  real numbers instead of leaving it implicit.

**APPROVED, adjusted (2026-08-17): rename is not enough — derive the
threshold per cluster.** Your call: a 9-day drift across TN is a
correctness bug, not a caveat to footnote. Rename happens, but so does
real per-cluster calibration:

```python
# RENAMED from KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED. Same value
# (19.2133), same derivation (ADR-002). Renamed because this is now
# documented as the FALLBACK reference rate, not a claim that Theni's
# climate is representative of every cluster -- see ADR-008 Decision 2.
KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED: float = 19.2133
```

**New module `agronomy/calibration.py`** generalizes ADR-002's exact
methodology (same window, same T_BASE, same 5-year mean) to any
`(lat, lon)`:

```python
def derive_reference_gdd_rate(lat, lon, *, years=5, today=None) -> float:
    """Mean daily GDD at (lat, lon) over the fixed May15-Aug12 window,
    across the `years` most recently complete calendar years -- the exact
    ADR-002 methodology, generalized. Reuses weather.openmeteo's existing,
    already-tested Archive fetch (get_daily_temperatures); raises
    WeatherError if Open-Meteo is unreachable -- caller decides the
    fallback."""

def derive_cluster_maturity_gdd(lat, lon, *, years=5, today=None) -> float:
    """derive_reference_gdd_rate(...) * ADT45_FIELD_DURATION_DAYS_ESTIMATED
    -- the per-cluster equivalent of MATURITY_GDD_ESTIMATED, computed from
    that cluster's own climate instead of Theni's."""
```

**`Cluster` gains `maturity_gdd_override: float | None = None`** — the
per-cluster derived threshold, persisted like any other Cluster field
(free default for existing stored clusters, same pattern as
`Farmer.language` in Decision 6). `scheduling/solver.py`'s `solve()`
resolves the effective threshold once per run:

```python
maturity_gdd = cluster.maturity_gdd_override
if maturity_gdd is None:
    maturity_gdd = crop_params.MATURITY_GDD_ESTIMATED
    logger.warning(
        "MATURITY THRESHOLD FALLBACK: cluster=%s has no derived "
        "maturity_gdd_override -- using the global Theni-reference "
        "constant (%.1f GDD) instead of this cluster's own climatology. "
        "Run the seed script with --calibrate to fix this.",
        cluster.cluster_id, maturity_gdd,
    )
```

...and passes it into `assess_plot(..., maturity_gdd=maturity_gdd)`
(new keyword-only parameter, defaults to the existing global constant so
every direct caller that doesn't pass one — `trigger_scenario.py`,
existing tests — keeps working unchanged).

**Calibration is opt-in and network-gated, not automatic at seed time** —
`uv run pytest` must stay hermetic (README's existing promise), so
`seed_cluster.py --write` alone does **not** call Open-Meteo. A new
`--calibrate` flag does: `seed_into_storage(storage, calibrate=True)`
calls `derive_cluster_maturity_gdd()` for that cluster's coordinates and
stores the result on the persisted `Cluster` record (the in-memory
`CLUSTER` fixture constant stays untouched — tests importing it directly
keep getting `maturity_gdd_override=None`, hermetic, unaffected). If
calibration fails (Open-Meteo down), seeding still succeeds with
`maturity_gdd_override=None` and prints why — the loud runtime warning
above is what catches an uncalibrated cluster in production, not a hard
failure at seed time.

README's honesty section gets Decision 2's table, states plainly that the
threshold is now per-cluster derived where calibration has run, that
`MATURITY_GDD_ESTIMATED` (1729.2) remains the documented fallback and
is **still DERIVED, not sourced** — calibration fixes *which climate*
the threshold reflects, it does not fix the deeper, still-open question
of whether 90 field-days is the right duration for ADT 45 at all.

### Decision 3: second seeded cluster — Naducauvery, Thanjavur district

Real place, coordinates fetched directly from Wikipedia just now (not
search-surfaced): **Naducauvery, Thiruvaiyaru taluk, Thanjavur district,
10°51′40″N 79°02′46″E (10.861, 79.046)**. Thiruvaiyaru taluk sits in the
Cauvery delta, Tamil Nadu's principal rice-growing belt — a genuinely
different growing environment from Theni's semi-arid interior (Decision 2's
measured ~10.6% GDD/day gap is the concrete evidence, not just the
"delta vs. foothill" label).

New module `scripts/seed_cluster_naducauvery.py`, same shape as
`seed_cluster.py` (`CLUSTER`, `FARMERS`, `PLOTS`, `seed_into_storage`,
`main`) — a parallel file, not a modification of the existing one, so
every test that imports `seed_cluster.PLOTS`/`FARMERS`/`CLUSTER` directly
(per ADR-005 Decision 3's explicit promise) keeps working unmodified.
8 plots, 8 Thanjavur-appropriate farmer names, small illustrative
coordinate offsets around the village center (same pattern as
Kamatchipuram's `_OFFSETS`), transplant dates spread to produce a mix of
outcomes against live weather over time — disclosed as synthetic fixture
data exactly like Kamatchipuram already is, not re-litigating that
honesty call, just repeating it for a second cluster.

`machine_capacity_acres_per_day` stays 3.5 (same as Kamatchipuram) —
deliberately not varied, so the demonstrated difference between the two
clusters is purely the climate/GDD effect from Decision 2, not confounded
by also changing capacity.

### Decision 4: coordinator — confirmed cluster-independent; watcher — confirmed NOT iterating, and that's the real gap

**Coordinator:** confirmed by reading, not assumed. `run_cluster()` /
`run_cluster_with_claims()` take `plots`, `decisions`, `cluster_id`,
`storage` as arguments and hold no module-level mutable state across
calls; `build_plot_facts()` reads fairness history via
`storage.get_ledger_history(farmer_id, ...)`, itself keyed by
`FARMER#{farmer_id}` with no cluster leakage. Two clusters calling
`run_cluster()` concurrently or sequentially cannot see each other's
state. This part of your ask is already true today.

**Watcher: not true today, and this is the real generalization gap.**
`watcher.run_daily_watch(cluster_id, season_id, ...)` takes exactly one
`cluster_id` — there is no loop over clusters anywhere in `watcher.py` or
`app.py`. The deployed EventBridge schedule fires the Lambda shim with a
single fixed payload (`{"cluster_id": "kamatchipuram", ...}` per
`check_watcher_health.py`'s default and `app.py`'s docstring example).
Adding a second cluster today would silently do nothing for it unless
something new triggers a second check.

**APPROVED, adjusted (2026-08-17): iterate inside `run_daily_watch`
itself, not a new sibling function — code only, deployed path untouched.**
`run_daily_watch`'s first parameter becomes `cluster_id: str | list[str]`.
A single string reproduces today's exact behavior (one summary dict,
byte-for-byte the same code path) — this is what `app.py`'s handler and
the deployed Lambda/EventBridge payload keep sending, so **`app.py` does
not change at all**. A list runs each cluster independently and returns a
list of summary dicts, one cluster's exception never blocking another's
(each iteration still goes through the same per-cluster top-level
`try/except` that already exists):

```python
# watcher.py
def run_daily_watch(
    cluster_id: str | list[str], season_id: str, *, storage=None,
    today=None, telegram_client=None, get_claim=None, force=False,
) -> dict | list[dict]:
    storage = storage or get_storage()
    today = today or date.today()
    client = telegram_client or TelegramClient()

    if isinstance(cluster_id, list):
        return [
            _run_daily_watch_one(cid, season_id, storage, today, client, get_claim, force)
            for cid in cluster_id
        ]
    return _run_daily_watch_one(cluster_id, season_id, storage, today, client, get_claim, force)
```

(existing function body becomes the private `_run_daily_watch_one` helper,
unchanged internally.) `storage`/`today`/`client` are resolved once
before branching, not re-resolved per cluster in the loop.

**The deployed EventBridge schedule and Lambda shim are not touched.**
They keep sending `{"cluster_id": "kamatchipuram", "season_id": ...}` —
a single string — until after the hackathon, per your instruction. This
ADR's implementation proves multi-cluster iteration works in code and
tests; wiring a second cluster into the live schedule is a separate,
later decision.

### Decision 5: README

New section stating the system runs anywhere in Tamil Nadu because GDD is
computed per-plot, with the two seeded clusters (Kamatchipuram/Theni,
Naducauvery/Thanjavur) as the evidence, plus Decision 2's table so the
"anywhere in TN" claim carries its own disclosed uncertainty rather than
reading as stronger than it is. Also adds the maize roadmap line you
asked for: *"Maize is the defensible second crop for a future season —
combine-harvested, moisture-critical at harvest, and real published GDD
literature exists for it, unlike the derived paddy threshold above. Not
attempted here — see the scope note in ADR-008 for why widening the crop
list now would compound rather than fix the sourcing gap."*

---

## Part 2: Tamil-language farmer interface

Scope, restated so it's checkable against what follows: **Tamil only**,
`Farmer.language` (not a cluster/global setting), architected for a third
language later, hand-authored fixed templates (no free translation of
scheduling-critical wording), advocate `argument` generated natively in
Tamil by the model, everything else (plot_ids, logs, traces, storage)
stays English.

### Decision 6: `language` is a `Farmer` field, not global

```python
# models.py
@dataclass(frozen=True)
class Farmer:
    farmer_id: str
    name: str
    cluster_id: str
    telegram_chat_id: int | None = None
    language: str = "ta"  # "ta" | "en" -- see messages_ta.py / messages_en.py
```

Default `"ta"` (dataclass default) is what makes "existing farmers with
no language field default to Tamil without crashing" free and automatic:
both `FileStorage.get_farmer` (`Farmer(**raw)`) and `DynamoStorage`'s
equivalent construct `Farmer` from `**kwargs` off a stored dict — a
legacy record missing the `"language"` key simply falls through to the
dataclass default. No migration script needed, no explicit `.get(...,
"ta")` shim in either storage backend. I'll add a test that constructs a
`FileStorage` record with the pre-language dict shape directly (not
through `put_farmer`) and confirms `get_farmer` returns `language="ta"` —
proving the claim rather than trusting the dataclass mechanics blind.

### Decision 7: registration — language choice folded into message 1, not a fifth message

The four farmer-typed/shared inputs stay exactly four (village, location,
crop confirmation, transplant info + area) — I'm not adding a fifth
required round-trip. What changes is message 1 itself: it becomes
bilingual with two inline-keyboard buttons (தமிழ் / English) *and* still
asks for the village name in the same message, in both languages:

> Welcome to Harvest Convoy / அறுவடை கான்வாய்க்கு வரவேற்கிறோம்.
> தமிழ் / English?
>
> What village is your plot in? / உங்கள் வயல் இருக்கும் கிராமத்தின் பெயர் என்ன?

Two ways forward from here, both land on the same next step
(`AWAITING_LOCATION`) with no extra state transition added:

1. **Farmer taps a button** (a `callback_query`, zero typing): sets
   `language` explicitly on the in-progress `RegistrationState`, no new
   message sent — the village prompt was already shown in both languages
   in message 1, so there's nothing to re-send.
2. **Farmer replies directly with the village name** (skips the button):
   `language` is inferred from script — Tamil Unicode block
   (`U+0B80`–`U+0BFF`) present anywhere in the reply → `language="ta"`
   (already the default, so this just makes it explicit rather than
   changing behavior); pure Latin-script reply → **stays `"ta"`, does
   not switch to `"en"`.** This is a deliberate asymmetry, not an
   oversight: Latin script is ambiguous between "this farmer wants
   English" and "this farmer is typing Tanglish" (romanized Tamil), and
   guessing wrong in the English direction is the worse failure mode —
   a farmer who can't read Tamil script but types in Tamil (rare) is
   better served by re-prompting than one who reads only Tamil getting
   silently switched to English because they typed a village name in
   Latin letters, which is common and says nothing about reading
   preference. Only an explicit tap sets `"en"`.

**APPROVED (2026-08-17), no changes** — keeping the flow at four farmer
messages outweighs a dedicated language screen. Implementing as specced
above.

`RegistrationState` gains a `language: str = "ta"` field; `PROMPTS` moves
from the current single English dict to `messages_ta.PROMPTS` /
`messages_en.PROMPTS`, selected by `state.language` at each step.

### Decision 8: `messages_ta.py` / `messages_en.py` — one module per language, hand-authored, slotted

Every fixed farmer-facing string currently inline in `registration.py`
and `notify.py` moves into two new modules,
`telegram/messages_ta.py` and `telegram/messages_en.py`, same shape in
both so a third language later is "add `messages_hi.py` matching this
shape," not a refactor:

```python
# telegram/messages_ta.py (shape shown, wording is a first pass -- see
# the terminal dump at the end of implementation; every string in here
# needs your correction, not just approval)
PROMPTS: dict[RegistrationStep, str] = {...}
COMPLETE_MESSAGE: str = ...

def harvest_scheduled(plot: Plot, route_position: int) -> str: ...
def not_ready(plot: Plot) -> str: ...
def escalation_resolved_won(plot: Plot) -> str: ...
def escalation_resolved_lost(plot: Plot, *, other_farmer_name: str, reason: str) -> str: ...
```

`notify.py`'s builders (`build_harvest_scheduled_text`, `build_not_ready_text`,
`build_escalation_resolved_text`) each take the relevant `Farmer` (or an
explicit `language: str`) and dispatch to the matching module —
`{"ta": messages_ta, "en": messages_en}[farmer.language]`. `PROMPTS` and
`COMPLETE_MESSAGE` currently live at module scope in `registration.py` as
bare English strings; those move out entirely, `registration.py` keeps
only the state machine and calls into whichever language module matches
`state.language`.

**Scope boundary:** `operator_route_summary` and the operator-facing
`send_escalation` *boilerplate* (both go to `cluster.operator_chat_id`, a
single cooperative dispatcher, not a specific farmer with a registered
language preference — "Only one plot can get today's machine," "Who
should get it?", the facts lines) **stay English-only** — no
per-operator language field exists on `Cluster`, and the brief scoped
this as farmer-facing. **One deliberate exception, per Decision 12's
approval:** the added `argument` line inside that same escalation
message is generated in *that plot's own farmer's* language, so the
operator's message ends up mixed-language by design — English structure
and facts, with each side's argument line in Tamil or English depending
on which farmer registered which. Flagging this explicitly since it's a
real, visible consequence of Decision 12, not an accident. If you'd
rather the argument line also stay English regardless of the farmer's
language (simpler for the operator to read, less faithful to "the
advocate speaks for the farmer"), say so — cheap to change.

**Not free translation for the four scheduling-critical shapes**
(`harvest_scheduled`, `not_ready`, `escalation_resolved` won/lost,
`operator_route_summary`) — hand-authored Tamil with the same variable
slots (`area_acres`/`area_unit`, `route_position`, `other_farmer_name`,
`reason`) as the English originals, same information content, checked
against each other side by side so neither language silently promises or
omits something the other doesn't. I am not a native speaker and I am
treating my Tamil as a first draft, not a deliverable — see Decision 12.

### Decision 9: area unit — `Plot` gains `area_unit`, ஏக்கர்/சென்ட் accepted on input, displayed back in the farmer's unit

`Plot.area_acres: float` stays the canonical value everything (capacity
math, route summaries to the operator) computes against — no downstream
consumer changes. A new field carries *how the farmer expressed it*,
for display only:

```python
# models.py
@dataclass(frozen=True)
class Plot:
    ...
    area_acres: float
    area_unit: Literal["acre", "cent"] = "acre"  # display-only; area_acres
    # stays canonical. 1 acre = 100 cents is the standard South Indian
    # land-measurement definition (TN revenue/land-records convention),
    # not an agronomic estimate -- no sourcing caveat needed, it's a
    # fixed unit conversion.
```

Registration's transplant-info parser (`_parse_area` in
`registration.py`) gains unit-word recognition for both units, in both
scripts, plus Tanglish spellings:

- Acre: `acres?`, `ac\b`, `ஏக்கர்`, `ekar`, `eekar`
- Cent: `cents?`, `சென்ட்`, `sent`, `cent`

Parses to a raw farmer-entered number + detected unit, converts to
canonical `area_acres` (`raw/100` if cent, `raw` if acre), and stores the
detected unit on `Plot.area_unit`. Display side (`notify.py`): if
`area_unit == "cent"`, show `area_acres * 100` formatted as cents in
whichever language's message; if `"acre"`, show `area_acres` as-is —
same pattern the messages already use for `route_position`/other slots.

**Correction (2026-08-17), caught during implementation:** this
paragraph originally proposed a bare-number-defaults-to-acres fallback,
describing it as "the existing regex's implicit assumption." That
description was wrong — the original English-only parser had no such
fallback; it always required an explicit unit word. I implemented the
imagined fallback anyway on the first pass, and it broke immediately: a
bare-number regex over free text also matches the *date's own digits*
("18" in "18 May 2026"), so `test_date_only_missing_area_reprompts_naming_only_area`
caught it parsing "18 May 2026" as 18 acres with no date at all.
**Fixed by removing the fallback** — an explicit unit word (ஏக்கர்/acre/
ekar or சென்ட்/cent/sent) is required, matching the original parser's
actual behavior, not a new restriction.

### Decision 10: dates rendered naturally, Tamil month names, Western digits

Both language modules get a `format_date(d: date) -> str` helper:

- English: `"18 August 2026"` (already the informal style used in the
  existing `PROMPTS` example).
- Tamil: `"ஆகஸ்ட் 18, 2026"` — standard Tamil transliterations of the
  Gregorian month names (ஜனவரி, பிப்ரவரி, மார்ச், ஏப்ரல், மே, ஜூன், ஜூலை,
  ஆகஸ்ட், செப்டம்பர், அக்டோபர், நவம்பர், டிசம்பர்) — these are the
  conventional forms used in Tamil newspapers and government notices, an
  ordinary linguistic convention, not a sourced/derived constant the
  honesty rule applies to. Still: first draft, your correction expected
  (Decision 12).

**Western digits (0–9), not Tamil numeral glyphs (௦–௯) — stated choice,
not a default I didn't think about.** Tamil numeral glyphs are largely
unfamiliar in everyday reading in contemporary Tamil Nadu (newspapers,
government forms, and SMS/chat Tamil overwhelmingly use Western digits
even in full Tamil-script text) — using them would plausibly make dates
*harder* to read for the actual audience, not more natural. Correct me
if that's wrong for the farmers you have in mind.

Transplant-date parsing (`_parse_date`) gains a Tamil month-name lookup
mirroring the existing `_MONTHS` dict, plus common Tanglish month
spellings (`mei` for மே, `augast`/`agasth` for ஆகஸ்ட், etc.) — same
"first pass, your correction expected" caveat.

### Decision 11: Tanglish acceptance at every free-text step, yes/no matters most

Two free-text steps take unconstrained input already (village name: no
parsing, stored as-is in whatever script the farmer used, no change
needed there beyond accepting it). Two need real matcher work:

**Crop confirmation (`AWAITING_CROP_CONFIRM`):** currently accepts *any*
non-empty reply as confirmation (`registration.py:141-145` — no actual
yes/no check exists today, just "reply anything"). **Confirmed
(2026-08-17): this is a real bug independent of the language work, fixed
with its own test regardless of Tamil support** — a farmer who fat-fingers
a reply or sends an unrelated message currently gets registered as
confirmed anyway. Per your instruction that this step matters most, I'm
tightening it, not just localizing it — a real yes/no matcher, in both
languages plus Tanglish, with a dedicated English-only test proving the
old any-reply behavior is gone even before the language modules exist:

```python
_YES_WORDS = {
    # English
    "yes", "y", "ok", "okay",
    # Tamil script
    "ஆம்", "ஆமாம்", "சரி", "ஓகே",
    # Tanglish (romanized Tamil)
    "aam", "aama", "aamaa", "seri", "sari", "oke",
}
```

No-reply / unrecognized input re-prompts (same pattern as every other
step) rather than defaulting either way — a farmer who types something
unmatched should be asked again, not silently treated as having
confirmed. First-pass word list; flagging explicitly that a native
speaker's list is likely both longer and different, and this is exactly
the kind of list where a wrong assumption costs real cost (mis-registering
a crop confirmation), so treat this one as the highest-priority item in
the Decision 12 review, not just one line among many.

**Transplant date + area (`AWAITING_TRANSPLANT_INFO`):** the existing
regex-based date/area parsers (Decision 9, 10) already need to accept
Tamil digits-with-Tamil-month-names, Tanglish month names, and both unit
words/Tanglish spellings — covered above, restated here only to confirm
this step is "every free-text step," not skipped.

### Decision 12: the advocate `argument` — a real finding, and a design fork I'm flagging before implementing

You said: *"The only generative text is the advocate `argument` shown in
the escalation. Have the advocate produce Tamil directly via its system
prompt."* I went to find where `argument` is rendered to a human before
touching the prompt, and found something worth telling you before I
implement anything: **`argument` is not shown to any human anywhere in
the current code.**

- `notify.py:build_escalation_text` (the operator-facing escalation
  message) renders `_overripe_phrase(claim.days_past_maturity)` and the
  bumped flag — **not** `claim.argument`.
- `webhook.py:_resolution_reason` (the loser's `escalation_resolved`
  message) builds its own hand-authored sentence from
  `bumped_last_season`/`days_past_maturity` — again, **not**
  `claim.argument`.
- The only place `argument` is actually consumed is agent-to-agent:
  `negotiate_pair()` passes the losing side's `claim.argument` to the
  next round's opponent as `opponent_argument` (`advocate.py:_build_prompt`).

So today, "the advocate argument shown in the escalation" describes
something that doesn't exist yet in the UI — the field is real, generated,
and threaded through the pipeline, but discarded before reaching a
human.

**APPROVED, adjusted (2026-08-17): option 1 — surface `argument` in the
operator-facing escalation message, structured facts first and
unchanged, argument as one clearly-labeled line below them.** Your
framing: the LLM's reasoning currently reaches no human, which makes the
advocate layer invisible despite being the architecture's centerpiece.
Confirmed requirements, restated as what I'm building:

- `build_escalation_text` keeps rendering `_overripe_phrase` + bumped
  flag exactly as today, unchanged, first — those are what the operator
  decides on, and nothing about presentation should make the model's
  prose read as more authoritative than the facts above it.
- One added line per plot, visually distinct and explicitly labeled as
  generated reasoning, not fact — e.g. `  "argument": <claim.argument>`
  or `  agent's case: "<argument>"` (exact wording is a Decision-12-style
  first draft, corrected in the Decision-13 terminal dump like every
  other string).
- **Presentational only — a model string must never block a scheduling
  decision.** If `argument` is empty, whitespace-only, or the claim came
  from `advocate.py`'s fallback path (`_fallback_claim`, ADR-003
  Decision 3 — model error, throttling, failed structured-output
  validation), the escalation still renders correctly from facts alone:
  either omit the argument line entirely for that side, or render a
  neutral placeholder (`"(no argument available)"`) — never a blank
  line, a crash, or a stalled message. **Explicit test**: build an
  `EscalationPayload` from claims produced by `_fallback_claim` (or a
  hand-built `AdvocateClaim(argument="")`) and assert
  `build_escalation_text` still returns valid, complete text — the
  facts-only path is a first-class case, not an afterthought.
- This is what makes Tamil-generation on `argument` worth doing — it
  now has a real reader.

Mechanically: `PlotFacts` gains `language: str`
(looked up via `storage.get_farmer(facts.farmer_id).language` in
`build_plot_facts`); `advocate.py` gets two cached system-prompt variants
(`CACHED_SYSTEM_PROMPT_TA`, `CACHED_SYSTEM_PROMPT_EN`) selected by
`facts.language` instead of one — preserves the existing prompt-caching
win (ADR-006 Decision 6) per-language rather than breaking it by
interpolating a variable instruction into a single cached prompt. The
Tamil variant instructs the model to write `argument` in Tamil directly
(your instruction, not a translation step) — same structured-output
schema, same ground-truth-override discipline (ADR-003 Decision 1)
unchanged; only the free-text `argument` field's language changes.

### Decision 13: tests

1. **Every template renders without `KeyError`, both languages.** A
   parametrized test drives every function in `messages_ta.py` and
   `messages_en.py` with representative dummy data (both `area_unit`
   values, both won/lost escalation branches) and asserts no exception —
   catches a missing slot or a copy-pasted format string immediately,
   not on a live phone.
2. **A Tamil-registered farmer gets Tamil.** `notify.py` dispatch test:
   `Farmer(language="ta")` → `send_not_ready` (etc.) produces text from
   `messages_ta`, not `messages_en` — asserted by checking for a
   Tamil-only substring, not just "not equal to the English string."
3. **Transliterated input accepted at every free-text step.** Parametrized
   over the crop-confirm yes-matcher (Decision 11) and the date/area
   parser (Decision 9/10) with a table of Tanglish samples.
4. **Legacy farmer record defaults to Tamil without crashing.** Constructs
   a stored `Farmer` dict with no `"language"` key directly (bypassing
   `put_farmer`, simulating a pre-this-ADR record) against both
   `FileStorage` and `DynamoStorage` (the latter via its existing
   moto/local-endpoint test pattern, whatever `test_dynamo_storage.py`
   already uses), asserts `get_farmer(...).language == "ta"`.

### Deliverable: every Tamil string printed for your review

After implementing, a small script (or a `pytest -s` block, whichever is
less throwaway) prints every string in `messages_ta.py` — every
`PROMPTS` entry, `COMPLETE_MESSAGE`, every message-builder function
called with representative dummy data (both units, both escalation
outcomes, a few date/route-position values) — plus the `_YES_WORDS` list
and the Tamil month-name table, all in one block, labeled by source
location, so you can read and correct every one in a single pass rather
than hunting through files. I will not treat my own Tamil phrasing as
correct by default anywhere in this deliverable.

---

## Decision 14: native-speaker review round 1 (2026-08-17) — bugs, wording, and a live-Bedrock finding

You reviewed the first Tamil-string dump and sent back a specific,
graded list: bugs, wording, length, word-list gaps, and one thing to
verify live before touching code. Recorded here as a real revision, not
folded silently into the strings above.

**Bugs fixed:**

1. **Date format was month-first** (`"ஆகஸ்ட் 18, 2026"`), wrong for
   Indian convention (day-first). Fixed to `"18 ஆகஸ்ட் 2026"` in
   `messages_ta.format_date`. Regression test added
   (`test_format_date_is_day_first_in_both_languages`).
2. **No "no" path existed at crop-confirmation.** `_YES_WORDS` existed
   but nothing handled a farmer declining — a real dead end for anyone
   with a non-paddy plot (they'd loop on the re-prompt forever). Added
   `_NO_WORDS`/`_is_no`, a new terminal `RegistrationStep.CROP_CONFIRM_DECLINED`,
   and `CROP_CONFIRM_DECLINED_MESSAGE` in both languages — acknowledges,
   states paddy-only scope, exits cleanly. Tested in English, Tamil, and
   Tanglish.
3. **Operator-facing text (route summary, escalation dispatch) was
   English-only.** This was ADR-008 Decision 8's original scope
   boundary, disclosed at the time as deliberate (no per-operator
   language field existed). **Confirmed: that was a decision, not an
   omission — and you asked for it reversed.** `Cluster.operator_language`
   (default `"ta"`) now selects these; `notify.py`'s route-summary and
   escalation builders, `short_label`/`full_label`, `overripe_phrase`,
   the bumped suffix, and the escalation intro/question/argument-label
   all moved into `messages_ta.py`/`messages_en.py` and dispatch on it.
   The escalation message is deliberately mixed-language when the two
   farmers differ (Decision 12's design): operator chrome in
   `operator_language`, each side's `argument` text in that plot's own
   farmer's language.

**Wording fixed**, all in `messages_ta.py`, all first-pass corrections
from you, not independently re-derived:

4. Product name — was transliterated (`அறுவடை கான்வாய்`), should stay
   Latin script as a proper noun. Fixed to `Harvest Convoy-க்கு
   வரவேற்கிறோம்!`.
5. Paperclip/location prompt — replaced `பேப்பர்கிளிப் ஐகான்` with the
   📎 emoji, and now shows both `Location` and `இருப்பிடம்` since
   Telegram's own attachment-menu label follows the farmer's *Telegram
   app* language setting, not this bot's language choice — showing both
   covers either case.
6. `"தானியம் இன்னும் நிரம்பிக்கொண்டிருக்கிறது"` (grain still filling,
   a literal translation) replaced with `"கதிர் இன்னும் முற்றவில்லை"`
   (grain-head not yet ripened) — the term farmers actually use — as the
   core not-ready statement itself, not just a word swap inside the
   sentence that got cut (see length note below).
7. `"#3 நிறுத்தம்"` → `"வரிசையில் 3வது"` (3rd in the line) in
   `harvest_scheduled`. English's `"stop #3"` is unchanged — you flagged
   this as a Tamil-specific fix.
8. **"Operator" word choice — asked, not guessed.** Presented three
   options (ஆபரேட்டர் / இயந்திரம் ஓட்டுபவர் / டிரைவர்) via a direct
   question rather than picking one. **You kept ஆபரேட்டர்** — no change.

**Length cut**, both languages, parity maintained (same load-bearing
content, same reduction, not just the Tamil side trimmed):

- `not_ready`: cut to "not ready / don't cut / we're tracking it /
  you'll hear from us" — the "grain filling, safer standing than cut
  early" rationale clause is gone. Existing test assertions rewritten to
  match (`test_not_ready_message_gives_no_action_and_promises_silence`).
- `escalation_resolved_lost`: cut to who/why, not on route, operator
  sends next, and — kept unmistakable, per your instruction — standing
  longer raises grain-loss risk.

**Yes-words widened** for real phone typing, not textbook forms: added
`ஆமா`, `சரிங்க`/`seringa`, `ஆகட்டும்`, `ok ok`, `done`, `confirm` to
`_YES_WORDS`. Still a first pass — expected to grow further.

### The advocate system prompt: verified live before touching anything, and it's worse than "reads like translated English"

Per your explicit instruction, ran the live advocate in Tamil mode
before changing the prompt — 8 real calls through the actual
`get_advocate_claim()` code path (real Kamatchipuram plots, real
`_cached_system_prompt_for("ta")`), plus one supplemental call, for 5
Tamil `argument` samples total. Verbatim, unedited:

```
[p05] இந்ன்து புனக்கது தக்க தா஡ைல்தது.
[p07] திஂன்துத்தயாடுத்து
[p01] ஊமகட்த் மறந்தம் மறமிக்கம் மறஂடகடும்.
[p03] ஊண்ட கம்பத்டுககம்
[p-extra] ஆகிகணிகிச் மடுத்டல் உனிப்படு மட்தமட்டு மடுக்கப்படு.
```

**This is not stilted or translated-sounding Tamil — it isn't Tamil.**
Checked the actual Unicode code points, not just how it renders: every
character is validly inside the Tamil block (U+0B80–U+0BFF), so this
isn't an encoding bug or a font problem. The model is emitting
grammatically invalid consonant/vowel-sign clusters (e.g. `ந்ன்` — two
virama-joined nasals in a row, not a real Tamil sequence) and using rare
signs like anusvara (`ஂ`, U+0B82) in positions no real word would.
Nova Pro, instructed via an appended English-language directive to
"write in Tamil script," is producing well-formed-looking but
non-lexical garbage — worse than the failure mode you asked me to watch
for, not the same one.

**Per your instruction, I have not touched the prompt or the approach.**
This is exactly the decision point you reserved for yourself: author a
native-Tamil system prompt directly, or drop the LLM from this field
for Tamil and template `argument` from the structured facts instead
(`urgency`, `days_past_maturity`, `bumped_last_season` already fully
determine a small set of honest sentences — no model needed to produce
one). Waiting for your call before changing `advocate.py`.

**Token/cost re-measurement**, same live run, now genuinely exercising
both cached system-prompt variants (4 calls each, alternating `ta`/`en`
across the 8 seeded farmers — a realistic mixed cluster, not an
all-one-language run that would only prove one cache path):

| | value |
|---|---|
| Calls | 8 (2 fits + 2 contested-pair + 4 too-green, same split as the documented gate) |
| Total input tokens | 5,899 |
| Total output tokens | 2,234 |
| **Total cost** | **$0.0119** |

This is higher than the single-variant $0.0081 figure currently in the
README/ADR-006 — expected, since a 50/50 language split means the
system-prompt cache is now split two ways instead of one, so roughly
half the calls pay a fresh cache read against the *other* variant's
cache line instead of reusing the same one every time. **README's cost
section needs updating to state this explicitly** (single-language
cluster vs. mixed-language cluster are two different numbers now) —
flagging this as still outstanding, not yet applied, pending your
prompt-approach decision above since that could change per-call token
counts too.

### Consequences of this round

- Two real, previously-invisible bugs fixed (crop-confirm dead end,
  Tamil date order), both regression-tested.
- Operator-facing text is no longer a disclosed English-only exception —
  `Cluster.operator_language` closes it, same dispatch pattern as
  farmer-facing text.
- The Tamil-generation approach for `argument` is **not shippable as
  designed** — confirmed by live output, not assumed. This is now a
  blocking decision, not a wording nit: whatever you choose (native
  prompt vs. templated-from-facts) is a real code change to
  `advocate.py`, not another string correction.
- 273 tests pass (2 `bedrock`-marked deselected, as before). All new
  behavior (no-path, operator-language dispatch, length-cut wording) has
  its own test, not just a re-dump.

---

## Decision 15: the advocate argument decision, made (2026-08-18) — templated, not native-prompted

You made the call from Decision 14's finding directly, without asking
for more live samples: **template `argument` from structured facts for
Tamil; do not attempt a native Tamil system prompt.** Reasoning you gave,
recorded because it's the right general principle, not just this case:
"those five samples are not accented or awkward Tamil — they are not
words. This is a tokenizer-level limitation in Nova Pro, not a prompting
problem, and a Tamil-authored prompt will not fix it. A model that
cannot emit valid Tamil likely cannot reliably read Tamil instructions
either."

### Two diagnostics run before implementing, as instructed

**Does Nova Pro garble Tanglish (romanized Tamil) too, or only Tamil
script?** Built a throwaway Tanglish-instructed system-prompt variant
(not shipped), ran 3 live calls. Result: **mostly clean, genuinely
readable output** —

```
Enga plot ready aayidichu, udambai vechu
Ithu ready aayila, action aanathe vendaam.
Enakku ready aayidichu, thungadi thunnai venum.
```

Two of three are fully coherent, correct Tanglish ("This isn't ready
yet, no action needed." is a completely clean sentence); the third has a
clean opening and a garbled tail. This is a real, usable middle path —
recorded here as a tested, available option, **not implemented**, since
you asked for templating specifically.

**Does the Tamil-script instruction corrupt other fields, or is it
isolated to `argument`?** Ran the same 3 fact sets through paired calls
(English-instructed vs. Tamil-instructed), comparing every structured
field before any ground-truth override. Result: **identical and correct
in every case** —

| plot | field | ground truth | EN call | TA call |
|---|---|---|---|---|
| p-cmp1 | concedes | — | False | False |
| p-cmp1 | urgency_score | 0.3 | 0.3 | 0.3 |
| p-cmp1 | days_past_maturity | 6 | 6 | 6 |
| p-cmp2 | concedes | — | True | True |
| p-cmp2 | urgency_score | 0.0 | 0.0 | 0.0 |
| p-cmp3 | rain_vulnerability | moderate | moderate | moderate |
| p-cmp3 | acres | 3.5 | 3.5 | 3.5 |

Only `argument` was ever wrong. The model's actual judgment (`concedes`)
and its ability to echo the other structured fields correctly were
unaffected by the Tamil instruction — confirming it's safe to keep using
a live model call for `concedes` even for Tamil-registered farmers, and
only template the prose.

### Implementation

1. **`telegram/messages_ta.py:advocate_argument(...)`** — new function,
   same shape/discipline as every other templated string in this module.
   Branches on `is_ready`, `concedes` (the model's own decision, passed
   in), `bumped_last_season`, `rain_vulnerability`, and
   `days_past_maturity`, in that priority order, returning one of seven
   fixed, hand-written Tamil sentences. Deterministic — same facts always
   produce the same sentence (tested:
   `test_advocate_argument_never_returns_model_prose`).
2. **`agents/advocate.py`**: `SYSTEM_PROMPT_TA`, `CACHED_SYSTEM_PROMPT_TA`,
   and `_cached_system_prompt_for` are gone. Back to one
   `CACHED_SYSTEM_PROMPT`, used for every call regardless of
   `facts.language` — the model is never asked to write Tamil prose
   again. `get_advocate_claim` still makes exactly one live call per
   plot per round (unchanged call count/shape); after the call, it uses
   `claim.concedes` (the model's genuine decision) either way, but picks
   `argument` by language: the model's own text for `"en"`,
   `messages_ta.advocate_argument(...)` for `"ta"`.
3. **Fallback path unchanged.** `_fallback_claim` (model error,
   throttling, no structured output) was already plain English
   regardless of language before this decision — not affected, and
   still irrelevant to what a human sees either way, since
   `notify._argument_line` omits any `claim.degraded` argument from
   display (Decision 12).

### A real bug caught in the same review pass: Tamil day-count pluralization

You flagged `"1 நாட்கள் அதிக பழுத்தது"` — plural நாட்கள் with the count
1, should be `"1 நாள்"`. Checked every place a day count is
interpolated into Tamil text, not just the flagged one — found the same
bug in `resolution_reason`'s "grain standing N days longer" branch
(`"1 நாட்களாக"`, same error). Fixed both via a new
`messages_ta._day_word(n, adverbial=...)` helper (நாள்/நாட்கள் for the
plain case, நாளாக/நாட்களாக for the "for/as N days" case
`resolution_reason` needs) — one helper, both call sites, tested for
both n=1 and n>1 at each site.

### Two word choices, asked directly rather than picked

- **"agent's case (not fact)" label**: `"ஏஜென்ட்டின் வாதம் (உண்மை
  அல்ல)"` read as a truth-value disclaimer ("not true") rather than "this
  is interpretation, not verified data." Presented three options; **you
  picked `"ஏஜென்ட்டின் கருத்து:"`** ("agent's opinion/view:") — states
  it's subjective without implying it's false.
- **"overripe" term**: `"அதிக பழுத்தது"` (literal "excessively
  ripened"). Presented three options; **you picked `"அளவுக்கு மேல்
  பழுத்தது"`** ("ripened beyond the right point").

Both applied in `messages_ta.py`'s `escalation_argument_label()` and
`overripe_phrase()`.

### Verification

**Mixed-language escalation layout**, checked directly (not assumed):
built a real escalation with one English-registered and one
Tamil-registered farmer, rendered `build_escalation_text` under both
`operator_language` values. Renders cleanly both directions — quotes,
line breaks, and the indented argument line all correct with mixed
English/Tamil content in the same message; no truncation, no encoding
issues. Confirms the deliberate mixed-language design (Decision 12) is
visually sound, not just correct in theory.

**Token/cost re-measurement**, same live 8-plot Kamatchipuram scenario,
same 50/50 Tamil/English farmer mix as Decision 14's measurement, now
against the single restored system prompt:

| | value |
|---|---|
| Calls | 8 |
| Total input tokens | 6,182 |
| Total output tokens | 1,747 |
| **Total cost** | **$0.0105** |

Close to the original single-prompt baseline ($0.0081, measured on an
all-English run) — the residual difference is ordinary call-to-call
variance in live, non-deterministic model output (each call's internal
`<thinking>` reasoning length varies a little), not a structural
regression. Materially below the two-variant measurement this replaces
($0.0119), as expected: one cached prefix, reused by every call
regardless of farmer language, same as before Tamil support existed.
**This is the number now in the README**, replacing $0.0081 there with
an explanation of why it moved and by how much.

### README write-up

Added a new "What we learned building Tamil support" section (README,
between "What we learned deploying" and "Cost") — same standard as the
existing AgentCore section: what was tried, the exact live evidence
(all five verbatim samples), what was checked before deciding, and what
shipped instead. Per your framing: this is stronger evidence of
engineering judgment than a feature that happened to work first try.

### Consequences

- `advocate.py` is simpler than the version this ADR round started
  from — one system prompt, not two, and the removed complexity was
  removed *because it didn't work*, not for its own sake.
- The advocate's live judgment (`concedes`) is unchanged and still
  genuine for every plot regardless of language; only free-text prose
  generation was removed from the model's job for Tamil.
- A real, tested Tanglish option is now on record for later, without
  having shipped it prematurely.
- Two more real bugs (day-count pluralization, ×2 call sites) caught and
  fixed in the same pass that was nominally "just" a decision
  implementation.
- 282 tests pass (2 `bedrock`-marked deselected). Full suite, not just
  the new tests, re-run clean.

---

## Decision 16: round-3 review — final four items, then Tamil is closed (2026-08-18)

Four items, accepted as the last round before recording/demo.

**1. `escalation_argument_label` re-picked in context.** "ஏஜென்ட்டின்
கருத்து:" (agent's opinion) was itself a replacement for the original
"not fact" wording, but lost the fact/reasoning distinction the operator
actually needs — everything else in the escalation message is measured,
this one line is generated, and the label has to carry that. Rendered
both candidates (`"ஏஜென்ட் சொல்வது:"` — "what the agent says" and
`"ஏஜென்ட்டின் பரிந்துரை:"` — "agent's recommendation") inside a real
full `build_escalation_text` output, not in isolation, and asked
directly. **Picked: `"ஏஜென்ட்டின் பரிந்துரை:"`.**

**2. Acre-decimal check.** Verified directly, not assumed:
`format_area(2.5, "acre")` → `"2.5 ஏக்கர்"` in both language modules —
`:g` formatting, no rounding to a whole number. The English escalation's
`"3.0ac"`-style rendering the question referenced no longer exists
either; `short_label`/`full_label` moved to `format_area` in Decision 14
(`"3 acres"`, not `"3.0ac"`). Cent rendering: not a special integer
cast — the same `:g` format applied to `area_acres * 100`. It comes out
whole in every case registration can actually produce, because a farmer
who types "250 cents" gets `area_acres = 2.5` exactly (250/100), and
formatting multiplies back by 100 exactly — an exact round trip, not
rounding. A genuinely fractional-cent value (not producible via
registration today, but not blocked by the function either) still shows
its decimal — confirmed with `format_area(0.755, "cent")` →
`"75.5 சென்ட்"`. Two new tests in `test_messages.py`
(`test_format_area_preserves_fractional_acres`,
`test_format_area_cents_are_whole_for_whole_cent_registrations`), run
against both language modules.

**3. Cost figure re-measured three times, not once.** The single-sample
$0.0105 rested on ordinary call-to-call variance in live, non-
deterministic model output, presented with more precision than one
observation supports. Ran the exact same live 8-plot Kamatchipuram
scenario (`measure_live_tamil_and_cost.py`, unchanged) three times:
$0.0118, $0.0106, $0.0112. README now states **"≈$0.011 per run
(measured $0.0106–$0.0118 across 3 live runs)"** instead of a single
four-decimal figure.

**4. README write-up rewritten** with the three points required
explicitly rather than left implicit: (a) the corruption never reached
a scheduling decision — every structured field matched ground truth in
both languages, stated plainly so a reader doesn't assume the whole
agent layer was compromised; (b) the failure is script-level, not
language-level — Tanglish came back largely coherent, with a verbatim
sample showing a clean opening and a garbled tail
(`"Enakku ready aayidichu, thungadi thunnai venum."`) alongside a fully
clean one; (c) the Tamil path was templated, not worked around, because
this string reaches an operator making a real scheduling decision and
unverifiable generated text doesn't belong there. All five original
garbled Tamil-script samples and all three Tanglish samples are quoted
verbatim in the README section.

Re-dumped `D:\tmp\tamil_strings.md` (round 3) after applying the label
change. Full suite re-run clean after all four items.

**Tamil support is closed as of this round** — no further changes
planned before the demo recording.

---

## Consequences

- `KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED` renames to
  `..._REFERENCE_ESTIMATED` and becomes the documented fallback rate;
  `Cluster.maturity_gdd_override` lets each cluster carry its own derived
  threshold, closing the 9-day drift rather than just disclosing it.
  `MATURITY_GDD_ESTIMATED` (1729.2) stays DERIVED-not-sourced regardless —
  calibration fixes *which climate* the threshold reflects, not the
  deeper open question of whether 90 field-days is right at all.
- Two seeded clusters exist after this: Kamatchipuram (Theni, existing,
  untouched) and Naducauvery (Thanjavur, new) — both real places, both
  synthetic fixture data, disclosed identically.
- `run_daily_watch` accepts `str | list[str]` for `cluster_id`; a single
  string is byte-for-byte the existing behavior, so `app.py` and the
  deployed EventBridge/Lambda payload do not change at all. Multi-cluster
  iteration is proven in tests, not wired into the live schedule.
- `Farmer.language`, `Plot.area_unit`, and `Cluster.maturity_gdd_override`
  are new fields with safe defaults; no migration needed for any storage
  backend, verified by tests that don't just trust the dataclass default
  but check it against a hand-built legacy record.
- Four scheduling-critical message shapes get hand-authored Tamil
  templates, not machine translation; the crop-confirmation step gets a
  real yes/no matcher where none existed before — confirmed as a
  standalone bug fix, not gated on the language rollout.
- The advocate's `argument` now reaches a human for the first time: one
  labeled line in the operator's escalation message, generated in the
  owning farmer's language, always degrading cleanly to facts-only when
  the model output is missing or came from the fallback path.
- Implementation order: (a) GDD per-cluster derivation + rename,
  (b) watcher iteration, (c) second cluster fixture — **stop and report
  here** — (d) advocate argument surfacing, (e) Tamil, ending with every
  Tamil string printed in one block for review.
