"""Equity report: was allocation of the shared machine fair, and did the
fairness mechanism actually do anything? See ADR-010 Part 2.

Read-only. Makes no Bedrock calls, writes nothing to Storage, never
touches the deployed AgentCore artifact.

CAVEAT, restated here because it belongs wherever this report travels,
not just in a README: the seeded clusters (Kamatchipuram, Naducauvery)
are simulated -- no real farmers, no real harvests. This report
demonstrates that the reporting mechanism works; it says nothing about
real-world equity outcomes, because there are no real-world outcomes yet.

Usage:
    uv run python -m scripts.equity_report --cluster kamatchipuram --season 2026-kuruvai
    uv run python -m scripts.equity_report --cluster kamatchipuram --all-seasons --out equity.json

--all-seasons discovers season ids from LedgerEntry and DecisionRecord
records referencing this cluster's farmers/plots -- a season with zero
escalations and no persisted decision record (i.e. it predates ADR-010
Part 0.5, or nothing happened worth recording) will not be discovered
this way; pass --season explicitly for that season if you know its id.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date

from harvest_convoy.models import Farmer, Plot
from harvest_convoy.reporting.config import SMALLHOLDER_THRESHOLD_ACRES
from harvest_convoy.reporting.provenance import Provenance, build_provenance, render_provenance_text
from harvest_convoy.storage import get_storage
from harvest_convoy.storage.fairness import operator_follow_through_rate
from harvest_convoy.storage.interface import BreakdownDisplacement, RouteOverride, Storage
from harvest_convoy.watcher import confirmation_status, rollover_status, route_override_status

CAVEAT = (
    "The seeded clusters (Kamatchipuram, Naducauvery) are simulated -- no "
    "real farmers, no real harvests. This report demonstrates that the "
    "reporting mechanism works; it says nothing about real-world equity "
    "outcomes, because there are no real-world outcomes yet."
)

# ADR-010 Part 0.5 shipped this date -- a season entirely before it has no
# DecisionRecords by construction, not because nothing happened.
_DECISION_RECORDS_SINCE = "2026-08-19"


@dataclass(frozen=True)
class SeasonEquitySection:
    season_id: str
    served_plot_ids: list[str]
    served_acres: float
    never_served_plot_ids: list[str]
    smallholder_registered_count: int
    smallholder_registered_acres: float
    smallholder_served_count: int
    smallholder_served_acres: float
    larger_registered_count: int
    larger_registered_acres: float
    larger_served_count: int
    larger_served_acres: float
    confirmed_yes: int
    confirmed_no: int
    pending: int
    unknown: int
    cancelled: int
    follow_through_rate: float | None
    decision_records_found: int
    fairness_influenced_count: int
    fairness_applied_not_decisive_count: int
    fairness_not_applicable_count: int
    rollover_confirmed_plot_ids: list[str]
    rollover_declined_plot_ids: list[str]
    rollover_unknown_plot_ids: list[str]
    breakdown_displacements: list[BreakdownDisplacement]
    # Operator route activity (ADR-013): how often a proposed route stood
    # as computed (accepted explicitly or by silence -- the same outcome
    # for the machine either way, kept as separate counts for the same
    # reason ADR-011's declined/unknown split exists) versus was modified,
    # and, within modified, how many involved at least one drop (the
    # fairness-relevant action) versus reorder-only (no fairness
    # consequence -- see LedgerEntry.decided_by below instead).
    route_overrides_proposed: int
    route_overrides_accepted: int
    route_overrides_no_response: int
    route_overrides_modified: int
    route_overrides_modified_with_drop: int
    route_override_acceptance_rate: float | None
    # Who decided each of this season's fairness bumps (days_bumped > 0)
    # -- "agent" (reserved, unused today), "operator_escalation" (a human
    # broke a tie the agent asked for help on), "operator_override" (a
    # human reversed a decision the agent was confident about). Answers
    # "who decided how this village's machine was allocated" directly,
    # with the escalation/override split as the nuance underneath it.
    # See ADR-013 Decision 6/10.
    bumps_by_decided_by: dict[str, int]


@dataclass(frozen=True)
class RepeatBumpEntry:
    farmer_id: str
    farmer_name: str
    seasons: list[tuple[str, int]]  # (season_id, days_bumped), most recent first


@dataclass(frozen=True)
class EquityReport:
    provenance: Provenance
    caveat: str
    cluster_id: str
    smallholder_threshold_acres: float
    registered_plot_count: int
    registered_acres: float
    sections: list[SeasonEquitySection]
    repeat_bumps: list[RepeatBumpEntry]
    data_gaps: list[str]


def _discover_season_ids(storage: Storage, farmers: list[Farmer], plots: list[Plot]) -> list[str]:
    # Note: a season with only SeasonRolloverPrompt activity (rollover run,
    # but no escalation and no decision record yet) is not discoverable
    # here -- there is no "every season for this plot" rollover-prompt
    # query, only per-(plot, season) and per-(cluster, season) lookups.
    # In practice rollover always precedes real scheduling, so this is a
    # narrow gap, not a load-bearing one; pass --season explicitly for
    # such a season if you know its id.
    season_ids: set[str] = set()
    for f in farmers:
        for entry in storage.get_ledger_entries(f.farmer_id):
            season_ids.add(entry.season_id)
    for p in plots:
        for record in storage.get_decision_records_for_plot(p.plot_id, season_id=None):
            season_ids.add(record.season_id)
    return sorted(season_ids)


def _build_season_section(
    storage: Storage, cluster_id: str, season_id: str, plots: list[Plot],
) -> SeasonEquitySection:
    confirmations = storage.get_confirmations_for_cluster(cluster_id, season_id)
    served_plot_ids = {c.plot_id for c in confirmations}

    # "Never served" means exactly what it says -- received no harvest
    # slot at all, whether because it was too green all season, lost a
    # negotiation (with or without escalating), or was simply never
    # evaluated. A plot that was genuinely contested and lost is a
    # different, additional fact (visible via its DecisionRecord/
    # LedgerEntry, not hidden here) -- it does not change the fact that
    # it never got served.
    never_served = [p for p in plots if p.plot_id not in served_plot_ids]

    smallholders = [p for p in plots if p.area_acres <= SMALLHOLDER_THRESHOLD_ACRES]
    larger = [p for p in plots if p.area_acres > SMALLHOLDER_THRESHOLD_ACRES]

    def _acres(plot_list: list[Plot]) -> float:
        return sum(p.area_acres for p in plot_list)

    served_smallholders = [p for p in smallholders if p.plot_id in served_plot_ids]
    served_larger = [p for p in larger if p.plot_id in served_plot_ids]

    statuses = [confirmation_status(c, date.today()) for c in confirmations]
    counts = {
        s: statuses.count(s)
        for s in ("confirmed_yes", "confirmed_no", "pending", "unknown", "cancelled")
    }
    rate = operator_follow_through_rate(cluster_id, season_id, storage)

    # Breakdown displacements (ADR-011 Part 2): visible here so a reader
    # can see how many "never served" or "no-show"-looking cases were
    # actually machine failures, never counted as bumps anywhere above.
    breakdown_displacements = sorted(
        storage.get_breakdown_displacements_for_cluster(cluster_id, season_id),
        key=lambda d: (d.original_scheduled_date, d.plot_id),
    )

    decision_records = storage.get_decision_records_for_cluster(cluster_id, season_id)
    fairness_influenced = sum(1 for r in decision_records if r.fairness_decisive is True)
    fairness_not_decisive = sum(1 for r in decision_records if r.fairness_decisive is False)
    fairness_na = sum(1 for r in decision_records if r.fairness_decisive is None)

    # Season participation (ADR-011 Part 1): "declined" and "unknown"
    # both exclude a plot from scheduling identically, but stay reported
    # as separate lists here, never a single collapsed "excluded" count
    # -- a farmer who said no and a farmer nobody could reach are
    # different findings.
    rollover_prompts = storage.get_season_rollover_prompts_for_cluster(cluster_id, season_id)
    rollover_confirmed = sorted(
        p.plot_id for p in rollover_prompts if rollover_status(p) == "confirmed"
    )
    rollover_declined = sorted(
        p.plot_id for p in rollover_prompts if rollover_status(p) == "declined"
    )
    rollover_unknown = sorted(
        p.plot_id for p in rollover_prompts if rollover_status(p) == "unknown"
    )

    # Operator route activity (ADR-013).
    route_overrides = storage.get_route_overrides_for_cluster(cluster_id, season_id)
    route_statuses = [route_override_status(o) for o in route_overrides]
    route_accepted = route_statuses.count("accepted")
    route_no_response = route_statuses.count("no_response")
    route_modified = route_statuses.count("modified")
    route_modified_with_drop = sum(
        1 for o, s in zip(route_overrides, route_statuses)
        if s == "modified" and set(o.proposed_route) - set(o.current_route)
    )
    route_total = len(route_overrides)
    route_acceptance_rate = (
        (route_accepted + route_no_response) / route_total if route_total else None
    )

    # Who decided this season's fairness bumps (ADR-013 Decision 6/10) --
    # every LedgerEntry with days_bumped > 0 for a farmer currently on
    # this cluster's roster (same current-roster caveat as every other
    # per-season figure here -- Plot has no season dimension).
    bumps_by_decided_by: dict[str, int] = {}
    for farmer_id in {p.farmer_id for p in plots}:
        for entry in storage.get_ledger_entries(farmer_id):
            if entry.season_id == season_id and entry.days_bumped > 0:
                bumps_by_decided_by[entry.decided_by] = bumps_by_decided_by.get(entry.decided_by, 0) + 1

    return SeasonEquitySection(
        season_id=season_id,
        served_plot_ids=sorted(served_plot_ids),
        served_acres=_acres([p for p in plots if p.plot_id in served_plot_ids]),
        never_served_plot_ids=sorted(p.plot_id for p in never_served),
        smallholder_registered_count=len(smallholders),
        smallholder_registered_acres=_acres(smallholders),
        smallholder_served_count=len(served_smallholders),
        smallholder_served_acres=_acres(served_smallholders),
        larger_registered_count=len(larger),
        larger_registered_acres=_acres(larger),
        larger_served_count=len(served_larger),
        larger_served_acres=_acres(served_larger),
        confirmed_yes=counts["confirmed_yes"],
        confirmed_no=counts["confirmed_no"],
        pending=counts["pending"],
        unknown=counts["unknown"],
        cancelled=counts["cancelled"],
        follow_through_rate=rate,
        decision_records_found=len(decision_records),
        fairness_influenced_count=fairness_influenced,
        fairness_applied_not_decisive_count=fairness_not_decisive,
        fairness_not_applicable_count=fairness_na,
        rollover_confirmed_plot_ids=rollover_confirmed,
        rollover_declined_plot_ids=rollover_declined,
        rollover_unknown_plot_ids=rollover_unknown,
        breakdown_displacements=breakdown_displacements,
        route_overrides_proposed=route_total,
        route_overrides_accepted=route_accepted,
        route_overrides_no_response=route_no_response,
        route_overrides_modified=route_modified,
        route_overrides_modified_with_drop=route_modified_with_drop,
        route_override_acceptance_rate=route_acceptance_rate,
        bumps_by_decided_by=bumps_by_decided_by,
    )


def _repeat_bumps(storage: Storage, farmers: list[Farmer]) -> list[RepeatBumpEntry]:
    """Cross-season by nature -- put_ledger_entry allows at most one entry
    per farmer per season, so "bumped more than once" is inherently a
    multi-season question, not something --season alone can answer.
    Looks across every recorded season for this farmer, not just the
    ones requested on the command line."""
    result = []
    for f in farmers:
        entries = [e for e in storage.get_ledger_entries(f.farmer_id) if e.days_bumped > 0]
        if len(entries) > 1:
            entries_sorted = sorted(entries, key=lambda e: e.season_id, reverse=True)
            result.append(RepeatBumpEntry(
                farmer_id=f.farmer_id, farmer_name=f.name,
                seasons=[(e.season_id, e.days_bumped) for e in entries_sorted],
            ))
    return sorted(result, key=lambda r: r.farmer_id)


def build_result(storage: Storage, *, cluster_id: str, season_ids: list[str]) -> EquityReport:
    plots = storage.get_plots_for_cluster(cluster_id)
    farmers = storage.get_farmers_for_cluster(cluster_id)

    sections = [
        _build_season_section(storage, cluster_id, sid, plots) for sid in season_ids
    ]
    repeat_bumps = _repeat_bumps(storage, farmers)

    data_gaps = []
    for section in sections:
        if section.decision_records_found == 0:
            data_gaps.append(
                f"Season {section.season_id}: no persisted decision records found -- "
                "fairness-mechanism-activity is not reported for this season. Either "
                f"this season predates ADR-010 Part 0.5 (shipped {_DECISION_RECORDS_SINCE}), "
                "or nothing was ever contested in it."
            )

    return EquityReport(
        provenance=build_provenance(storage, cluster_ids=[cluster_id], season_ids=season_ids),
        caveat=CAVEAT,
        cluster_id=cluster_id,
        smallholder_threshold_acres=SMALLHOLDER_THRESHOLD_ACRES,
        registered_plot_count=len(plots),
        registered_acres=sum(p.area_acres for p in plots),
        sections=sections,
        repeat_bumps=repeat_bumps,
        data_gaps=data_gaps,
    )


def _render_section_text(s: SeasonEquitySection) -> list[str]:
    lines = [f"  Season {s.season_id}:"]
    lines.append(f"    Served: {len(s.served_plot_ids)} plot(s), {s.served_acres:g} acres")
    lines.append(
        f"    Never served this season: {len(s.never_served_plot_ids)} plot(s) "
        f"{s.never_served_plot_ids or ''}"
    )
    lines.append(
        f"    Smallholder (<= {SMALLHOLDER_THRESHOLD_ACRES:g} acres): "
        f"{s.smallholder_served_count}/{s.smallholder_registered_count} plots served "
        f"({s.smallholder_served_acres:g}/{s.smallholder_registered_acres:g} acres)"
    )
    lines.append(
        f"    Larger holdings: {s.larger_served_count}/{s.larger_registered_count} plots "
        f"served ({s.larger_served_acres:g}/{s.larger_registered_acres:g} acres)"
    )
    rate_str = f"{s.follow_through_rate:.0%}" if s.follow_through_rate is not None else "n/a (no replies yet)"
    lines.append(
        f"    Operator follow-through: {s.confirmed_yes} confirmed, {s.confirmed_no} no-show, "
        f"{s.pending} pending, {s.unknown} unknown (silence never counted either way), "
        f"{s.cancelled} cancelled (machine breakdown -- see below, never counted as a no-show) "
        f"-- rate: {rate_str}"
    )
    if s.decision_records_found == 0:
        lines.append("    Fairness mechanism activity: no decision records found for this season.")
    else:
        lines.append(
            f"    Fairness mechanism activity: {s.decision_records_found} decision record(s); "
            f"fairness bonus changed the outcome in {s.fairness_influenced_count}, applied but "
            f"did not change the outcome in {s.fairness_applied_not_decisive_count}, not "
            f"applicable (concession/human-resolved/not contested) in {s.fairness_not_applicable_count}."
        )
    total_rollover = (
        len(s.rollover_confirmed_plot_ids) + len(s.rollover_declined_plot_ids)
        + len(s.rollover_unknown_plot_ids)
    )
    if total_rollover == 0:
        lines.append(
            "    Season participation: no rollover prompt was ever run for this "
            "cluster/season (either it's the first season, or every plot registered "
            "directly rather than via rollover)."
        )
    else:
        lines.append(
            f"    Season participation: {len(s.rollover_confirmed_plot_ids)} confirmed "
            f"{s.rollover_confirmed_plot_ids}, {len(s.rollover_declined_plot_ids)} declined "
            f"{s.rollover_declined_plot_ids}, {len(s.rollover_unknown_plot_ids)} unknown/never "
            f"replied {s.rollover_unknown_plot_ids} -- declined and unknown are reported "
            f"separately because they are different facts about a person, not one collapsed "
            f"'excluded' state."
        )
    if not s.breakdown_displacements:
        lines.append("    Breakdown displacements: none recorded this season.")
    else:
        lines.append(
            f"    Breakdown displacements: {len(s.breakdown_displacements)} plot-day(s) -- "
            "never counted as a fairness bump; the ledger measures farmers losing to "
            "farmers, not mechanical failures:"
        )
        for d in s.breakdown_displacements:
            lines.append(f"      {d.original_scheduled_date}: {d.plot_id} (farmer {d.farmer_id})")
    if s.route_overrides_proposed == 0:
        lines.append("    Operator route activity: no route was ever proposed this season.")
    else:
        acceptance_str = (
            f"{s.route_override_acceptance_rate:.0%}"
            if s.route_override_acceptance_rate is not None else "n/a"
        )
        lines.append(
            f"    Operator route activity: {s.route_overrides_proposed} route(s) proposed -- "
            f"{s.route_overrides_accepted} explicitly accepted, "
            f"{s.route_overrides_no_response} stood without a response (silence is not a "
            f"veto, not counted as disagreement), {s.route_overrides_modified} modified "
            f"({s.route_overrides_modified_with_drop} involving at least one drop) -- "
            f"acceptance rate (accepted + no-response over proposed): {acceptance_str}."
        )
    if not s.bumps_by_decided_by:
        lines.append("    Who decided this season's fairness bumps: none recorded this season.")
    else:
        parts = ", ".join(f"{v} {k}" for k, v in sorted(s.bumps_by_decided_by.items()))
        lines.append(
            f"    Who decided this season's fairness bumps: {parts} -- 'agent' is a "
            "hypothetical fully-automatic path (unused in this codebase today); "
            "operator_escalation is a human breaking a tie the agent asked for help on; "
            "operator_override is a human reversing a decision the agent was confident about."
        )
    return lines


def render_text(result: EquityReport) -> str:
    lines = [render_provenance_text(result.provenance), ""]
    lines.append("CAVEAT: " + result.caveat)
    lines.append("")
    lines.append(f"EQUITY REPORT -- cluster {result.cluster_id}")
    lines.append(
        f"Smallholder threshold used: {result.smallholder_threshold_acres:g} acres "
        "(policy parameter, not sourced -- see harvest_convoy/reporting/config.py)"
    )
    lines.append(
        f"Registered (current roster): {result.registered_plot_count} plot(s), "
        f"{result.registered_acres:g} acres. NOTE: Plot has no season dimension -- this "
        "is the cluster's current plot roster, not necessarily what was registered at "
        "the start of any specific past season."
    )
    lines.append("")

    if not result.sections:
        lines.append("No seasons found for this cluster.")
    else:
        lines.append("PER-SEASON COVERAGE / FOLLOW-THROUGH / FAIRNESS ACTIVITY")
        for s in result.sections:
            lines.extend(_render_section_text(s))
            lines.append("")

    lines.append("-" * 72)
    lines.append("REPEAT BUMPS (across all recorded seasons for this cluster's farmers)")
    lines.append("-" * 72)
    if not result.repeat_bumps:
        lines.append("No farmer has been bumped in more than one recorded season.")
    else:
        for r in result.repeat_bumps:
            season_list = ", ".join(f"{sid} ({days}d)" for sid, days in r.seasons)
            lines.append(f"  {r.farmer_name} ({r.farmer_id}): {season_list}")

    if result.data_gaps:
        lines.append("")
        lines.append("-" * 72)
        lines.append("NOT RECORDED, AND WHY")
        lines.append("-" * 72)
        for gap in result.data_gaps:
            lines.append(f"  - {gap}")

    return "\n".join(lines)


def to_json_dict(result: EquityReport) -> dict:
    return asdict(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cluster", required=True, dest="cluster_id", help="e.g. kamatchipuram")
    season_group = parser.add_mutually_exclusive_group(required=True)
    season_group.add_argument("--season", default=None, help="e.g. 2026-kuruvai")
    season_group.add_argument(
        "--all-seasons", action="store_true",
        help="Discover every season with a ledger entry or decision record for this cluster.",
    )
    parser.add_argument("--out", default=None, help="Path to write the JSON report to")
    args = parser.parse_args(argv)

    storage = get_storage()
    cluster = storage.get_cluster(args.cluster_id)
    if cluster is None:
        print(f"error: no cluster found with cluster_id={args.cluster_id!r}", file=sys.stderr)
        return 1

    if args.all_seasons:
        plots = storage.get_plots_for_cluster(args.cluster_id)
        farmers = storage.get_farmers_for_cluster(args.cluster_id)
        season_ids = _discover_season_ids(storage, farmers, plots)
    else:
        season_ids = [args.season]

    result = build_result(storage, cluster_id=args.cluster_id, season_ids=season_ids)

    print(render_text(result))

    if args.out is not None:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(to_json_dict(result), f, indent=2, default=str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
