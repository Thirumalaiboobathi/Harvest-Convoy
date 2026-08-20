"""Structured contracts between deterministic scheduling and the LLM layer.

PlotFacts is built entirely from Phase 1/2 output -- deterministic, no LLM
involvement. AdvocateClaim is what an advocate agent returns; per ADR-003
Decision 1, every factual field on a returned claim is overwritten with the
matching PlotFacts value before use, so the LLM's only real contributions
are `argument` and `concedes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

RainVulnerability = Literal["none", "low", "moderate", "severe"]

# DERIVED, not independently sourced: bucketed off the already-DERIVED
# `urgency` (decay_fraction) value from agronomy/decay.py rather than a
# second, independent day-count threshold. See ADR-003 Decision 1.
_RAIN_VULNERABILITY_LOW_MAX = 0.25
_RAIN_VULNERABILITY_MODERATE_MAX = 0.6


def classify_rain_vulnerability(is_ready: bool, urgency: float) -> RainVulnerability:
    """A too-green plot's grain is too light to lodge; vulnerability climbs
    with how far a ready plot has decayed past maturity."""
    if not is_ready:
        return "none"
    if urgency <= 0.0:
        return "none"
    if urgency <= _RAIN_VULNERABILITY_LOW_MAX:
        return "low"
    if urgency <= _RAIN_VULNERABILITY_MODERATE_MAX:
        return "moderate"
    return "severe"


@dataclass(frozen=True)
class PlotFacts:
    """Deterministic facts handed to an advocate agent. Every field here
    comes from Phase 1/2 computation -- nothing here is ever produced by
    an LLM."""

    plot_id: str
    farmer_id: str
    is_ready: bool  # False for TOO_GREEN
    days_past_maturity: int  # 0 for TOO_GREEN (there is no "past maturity")
    urgency: float  # decay_fraction; 0.0 for TOO_GREEN
    rain_vulnerability: RainVulnerability
    acres: float
    bumped_last_season: bool
    weighted_bump_days: float = 0.0  # decayed sum across all recorded
    # seasons (storage/fairness.py:weighted_bump_days) -- see ADR-005
    # Decision 4. bumped_last_season stays as the plain fact for
    # farmer-facing copy; this is what coordinator.py's scoring actually
    # uses.
    language: str = "ta"  # this plot's farmer's registered language --
    # advocate.py uses this to generate `argument` directly in that
    # language (not translated), see ADR-008 Decision 12.


class AdvocateClaim(BaseModel):
    """Structured claim an advocate agent returns. Never prose."""

    plot_id: str
    urgency_score: float = Field(ge=0.0, le=1.0)
    days_past_maturity: int = Field(ge=0)
    rain_vulnerability: RainVulnerability
    acres: float = Field(ge=0.0)
    bumped_last_season: bool
    weighted_bump_days: float = Field(ge=0.0, default=0.0)
    argument: str = Field(description="One sentence, max 25 words.")
    concedes: bool
    # True when this claim came from advocate.py's fallback path
    # (_fallback_claim -- model error, throttling, failed structured-
    # output validation) rather than genuine model judgment. Presentational
    # signal only: notify.py's escalation rendering uses this to omit the
    # "agent's case" line rather than showing generic fallback text as if
    # it were real reasoning. See ADR-008 Decision 12.
    degraded: bool = False

    @field_validator("argument")
    @classmethod
    def _truncate_to_25_words(cls, value: str) -> str:
        words = value.split()
        if len(words) <= 25:
            return value
        return " ".join(words[:25])

    @classmethod
    def from_facts(
        cls, facts: PlotFacts, *, argument: str, concedes: bool, degraded: bool = False
    ) -> "AdvocateClaim":
        """Build a claim entirely from ground-truth facts plus the two
        fields an LLM (or a fallback path) is actually allowed to decide."""
        return cls(
            plot_id=facts.plot_id,
            urgency_score=facts.urgency,
            days_past_maturity=facts.days_past_maturity,
            rain_vulnerability=facts.rain_vulnerability,
            acres=facts.acres,
            bumped_last_season=facts.bumped_last_season,
            weighted_bump_days=facts.weighted_bump_days,
            argument=argument,
            concedes=concedes,
            degraded=degraded,
        )


@dataclass(frozen=True)
class EscalationPayload:
    """Exactly one message to a human: both plots, the trade-off, two tap
    targets (Phase 4 renders this as an inline keyboard)."""

    cluster_id: str
    plot_a_id: str
    plot_b_id: str
    claim_a: AdvocateClaim
    claim_b: AdvocateClaim
    rounds_run: int
    reason: str
    # The trigger day this escalation was raised on -- lets webhook.py
    # find the exact DecisionRecord (keyed by plot_id/season_id/
    # decision_date) to update once a human resolves it. See ADR-010
    # Part 0.5 Decision C/D.
    decision_date: str = ""


@dataclass(frozen=True)
class TriggerContext:
    """The weather/capacity/threshold context that produced one trigger
    day's solve() classification -- computed once by watcher.py (it
    already has every input) and threaded into the coordinator purely so
    it can attach these to each plot's persisted DecisionRecord. Not a
    new computation, just not previously passed downstream. See ADR-010
    Part 0.5 Decision C.
    """

    decision_date: str
    rain_threshold_mm: float
    forecast_horizon_days: int
    usable_harvest_days: int
    maturity_gdd_used: float
    threshold_source: str  # "calibrated" | "fallback"
    machine_capacity_acres_per_day: float
    capacity_budget_acres: float
    # "scheduled" (the normal daily trigger) | "breakdown_recompute" (a
    # machine-breakdown recompute, ADR-011 Part 2) -- copied straight
    # onto every DecisionRecord this trigger writes, so a later reader
    # can tell why a plot's numbers look the way they do.
    trigger_reason: str = "scheduled"
    # "none" | "brief" | "sustained" (RainEventClass.value) and the
    # urgency boost it produced -- ADR-011 Part 3. Copied onto every
    # DecisionRecord this trigger writes, same "make the number visible
    # in the audit trail" discipline as everything else here.
    rain_event_classification: str = "none"
    rain_urgency_boost: float = 0.0
