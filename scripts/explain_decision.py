"""Decision replay: reconstruct, from stored records only, why the system
scheduled (or didn't schedule) one plot on one date. See ADR-010 Part 1.

Read-only. Makes no Bedrock calls, writes nothing to Storage, never
touches the deployed AgentCore artifact.

Every number in the output was either read directly from a
harvest_convoy.storage.interface.DecisionRecord (persisted by the
coordinator at the moment it decided, ADR-010 Part 0.5) or is a plain
fact from another stored record (Plot, Farmer, Cluster, LedgerEntry,
HarvestConfirmation). Nothing is recomputed or estimated. A decision made
before Part 0.5 shipped has no DecisionRecord and cannot be recovered --
the report says so explicitly, distinct from "this never happened."

Usage:
    uv run python -m scripts.explain_decision --plot-id p03 --date 2026-09-09
    uv run python -m scripts.explain_decision --plot-id p03 --date 2026-09-09 \\
        --season 2026-kuruvai --out decision_p03.json

--season is optional: if omitted, the script tries to infer it from a
DecisionRecord (exact decision_date match) or, failing that, a
LedgerEntry (resolved_at date match) referencing this plot. If neither
resolves to exactly one season, dispatch/confirmation detail that needs a
season_id is reported as unavailable rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.reporting.provenance import Provenance, build_provenance, render_provenance_text
from harvest_convoy.storage import get_storage
from harvest_convoy.storage.interface import DecisionRecord, HarvestConfirmation, LedgerEntry, Storage
from harvest_convoy.telegram.messages_en import format_area, format_date
from harvest_convoy.watcher import confirmation_status

# The standing categories of fact this project computes fresh on every
# trigger day and never persisted before ADR-010 Part 0.5 -- see ADR-010's
# "Prerequisite finding." Printed once whenever no DecisionRecord is found
# for the requested plot/date, never silently omitted.
_PRE_PART_0_5_GAPS = [
    "Accumulated GDD and which maturity threshold (calibrated or global "
    "fallback) was in effect on this date.",
    "The rain forecast and usable-harvest-day/capacity-budget context "
    "that triggered scheduling on this date.",
    "This plot's classification (fits / contested / too green) on this "
    "specific date.",
    "Any detail of a negotiation on this date: the competing plot's "
    "claim, either side's argument, how many rounds ran, or whether the "
    "fairness bonus was decisive.",
]

_NEVER_RECORDED_NOTE = (
    "No decision record exists for this plot on this date. This project "
    "began persisting a full decision record per plot per trigger day on "
    "2026-08-19 (ADR-010 Part 0.5); a decision made before that date was "
    "never recorded, and there is no way to recover it -- recomputing it "
    "now would not reproduce what was actually decided, only a plausible "
    "substitute, which this project's own rule refuses to present as "
    "fact. A decision made after that date and still missing here means "
    "either this plot was never evaluated on this date, or a write "
    "failure occurred (see the DECISION RECORD WRITE FAILED log line for "
    "this plot/date, if any)."
)


@dataclass(frozen=True)
class DecisionReplayResult:
    provenance: Provenance
    plot_id: str
    requested_date: str
    season_id: str | None
    season_resolution_note: str
    plot: Plot | None
    farmer: Farmer | None
    cluster: Cluster | None
    decision_record: DecisionRecord | None
    decision_gap_note: str | None
    ledger_entries: list[LedgerEntry]
    confirmation: HarvestConfirmation | None
    confirmation_status_label: str | None
    currently_harvested: bool | None
    data_gaps: list[str]


def _resolve_season(
    storage: Storage, plot_id: str, farmer_id: str, requested_date: str, given_season: str | None,
) -> tuple[str | None, str]:
    if given_season is not None:
        return given_season, "season provided on the command line"

    decisions_all_seasons = storage.get_decision_records_for_plot(plot_id, season_id=None)
    matching_decisions = [d for d in decisions_all_seasons if d.decision_date == requested_date]
    if len(matching_decisions) == 1:
        season_id = matching_decisions[0].season_id
        return season_id, f"season inferred from a decision record dated {requested_date}: {season_id}"
    if len(matching_decisions) > 1:
        seasons = sorted({d.season_id for d in matching_decisions})
        return None, (
            f"{len(matching_decisions)} decision records exist for this plot dated "
            f"{requested_date}, across seasons {seasons} -- ambiguous, pass --season "
            "to pick one"
        )

    ledger_entries = storage.get_ledger_entries(farmer_id)
    matching_ledger = [
        e for e in ledger_entries
        if e.resolved_at[:10] == requested_date and plot_id in (e.plot_id, e.opponent_plot_id)
    ]
    if len(matching_ledger) == 1:
        season_id = matching_ledger[0].season_id
        return season_id, (
            f"no decision record found; season inferred from a ledger entry "
            f"dated {requested_date}: {season_id}"
        )
    if len(matching_ledger) > 1:
        seasons = sorted({e.season_id for e in matching_ledger})
        return None, (
            f"{len(matching_ledger)} ledger entries reference this plot dated "
            f"{requested_date}, across seasons {seasons} -- ambiguous, pass --season "
            "to pick one"
        )

    return None, (
        "no --season given, and no decision record or ledger entry dated "
        f"{requested_date} references this plot -- dispatch/confirmation detail "
        "that needs a season_id is unavailable"
    )


def build_result(
    storage: Storage, *, plot_id: str, requested_date: str, season: str | None,
) -> DecisionReplayResult:
    plot = storage.get_plot(plot_id)
    farmer = storage.get_farmer(plot.farmer_id) if plot is not None else None
    cluster = storage.get_cluster(plot.cluster_id) if plot is not None else None

    farmer_id = plot.farmer_id if plot is not None else ""
    season_id, season_note = _resolve_season(storage, plot_id, farmer_id, requested_date, season)

    decision_record = None
    decision_gap_note = None
    confirmation = None
    confirmation_status_label = None
    currently_harvested = None

    if season_id is not None:
        decision_record = storage.get_decision_record(plot_id, season_id, requested_date)
        confirmation = storage.get_harvest_confirmation(plot_id, season_id)
        if confirmation is not None:
            confirmation_status_label = confirmation_status(confirmation, date.today())
        if cluster is not None:
            currently_harvested = plot_id in storage.get_harvested_plot_ids(cluster.cluster_id, season_id)

    if decision_record is None:
        decision_gap_note = _NEVER_RECORDED_NOTE

    ledger_entries = []
    if farmer is not None:
        all_ledger = storage.get_ledger_entries(farmer.farmer_id)
        ledger_entries = sorted(
            (e for e in all_ledger if plot_id in (e.plot_id, e.opponent_plot_id)),
            key=lambda e: e.resolved_at,
        )

    data_gaps = list(_PRE_PART_0_5_GAPS) if decision_record is None else []

    provenance = build_provenance(
        storage,
        cluster_ids=[cluster.cluster_id] if cluster is not None else [],
        season_ids=[season_id] if season_id is not None else [],
    )

    return DecisionReplayResult(
        provenance=provenance,
        plot_id=plot_id,
        requested_date=requested_date,
        season_id=season_id,
        season_resolution_note=season_note,
        plot=plot,
        farmer=farmer,
        cluster=cluster,
        decision_record=decision_record,
        decision_gap_note=decision_gap_note,
        ledger_entries=ledger_entries,
        confirmation=confirmation,
        confirmation_status_label=confirmation_status_label,
        currently_harvested=currently_harvested,
        data_gaps=data_gaps,
    )


def _narrative_from_decision_record(result: DecisionReplayResult) -> str:
    r = result.decision_record
    plot, farmer = result.plot, result.farmer
    farmer_name = farmer.name if farmer is not None else "an unknown farmer"
    cluster_name = result.cluster.name if result.cluster is not None else "an unknown cluster"
    area = format_area(plot.area_acres, plot.area_unit) if plot is not None else "an unknown area"

    lines = [
        f"On {result.requested_date}, {farmer_name}'s {area} plot ({result.plot_id}, "
        f"{cluster_name}) was classified {r.outcome.upper().replace('_', ' ')}.",
        "",
        f"Accumulated GDD: {r.accumulated_gdd:.1f} against a maturity threshold of "
        f"{r.maturity_gdd_used:.1f} ({r.threshold_source}).",
    ]
    if r.days_past_maturity is not None:
        lines.append(f"Days past maturity: {r.days_past_maturity}. Urgency: {r.urgency:.2f}.")
    lines.append(
        f"That day's usable-harvest-day window: {r.usable_harvest_days} day(s) "
        f"(rain threshold {r.rain_threshold_mm}mm, {r.forecast_horizon_days}-day forecast "
        f"horizon), capacity budget {r.capacity_budget_acres:.1f} acres at "
        f"{r.machine_capacity_acres_per_day} acres/day."
    )
    if r.route_position is not None:
        lines.append(f"Route position: {r.route_position}.")

    if r.opponent_plot_id is not None:
        lines.append("")
        lines.append(f"This plot was paired against {r.opponent_plot_id}. {r.rounds_run} round(s) ran.")
        if r.own_claim is not None:
            lines.append(f"This plot's final claim: {r.own_claim}")
        if r.opponent_claim is not None:
            lines.append(f"{r.opponent_plot_id}'s final claim: {r.opponent_claim}")
        lines.append(f"Recorded resolution: {r.resolution}.")
        if r.resolved_at is not None:
            lines.append(f"Resolved at: {r.resolved_at}.")
        else:
            lines.append("Not yet resolved in storage (escalated, awaiting a human tap).")
        fairness_label = {
            True: "yes -- the fairness bonus changed which side won",
            False: "no -- the same side would have won on raw urgency alone",
            None: "not applicable (resolved by concession, by a human tap, or not yet resolved)",
        }[r.fairness_decisive]
        lines.append(f"Fairness bonus decisive: {fairness_label}.")
    elif r.resolution == "contested_no_partner_this_round":
        lines.append("")
        lines.append(
            "This plot was CONTESTED but had no pairing partner this trigger day "
            "(an odd number of contested plots) -- it did not enter negotiation."
        )

    return "\n".join(lines)


def render_text(result: DecisionReplayResult) -> str:
    lines = [render_provenance_text(result.provenance), ""]
    lines.append(f"DECISION REPLAY -- plot {result.plot_id}, date {result.requested_date}")
    lines.append("")

    if result.plot is None:
        lines.append(f"No Plot record exists for plot_id={result.plot_id!r}.")
    else:
        lines.append(f"Season: {result.season_id or '(not resolved)'} -- {result.season_resolution_note}")
        lines.append("")
        if result.decision_record is not None:
            lines.append(_narrative_from_decision_record(result))
        else:
            lines.append(result.decision_gap_note or "")

        lines.append("")
        lines.append("-" * 72)
        lines.append("ON RECORD, SEPARATE FROM THE DECISION ITSELF")
        lines.append("-" * 72)

        if not result.ledger_entries:
            lines.append("No fairness ledger entries reference this plot.")
        else:
            for e in result.ledger_entries:
                lines.append(
                    f"  Ledger [{e.season_id}]: outcome={e.outcome}, days_bumped={e.days_bumped}, "
                    f"opponent={e.opponent_plot_id or '(none)'}, resolved_at={e.resolved_at}"
                )

        if result.confirmation is None:
            lines.append("No harvest confirmation record exists for this plot/season.")
        else:
            c = result.confirmation
            lines.append(
                f"  Harvest confirmation: scheduled_date={c.scheduled_date}, "
                f"asked_at={c.asked_at or '(never asked)'}, confirmed={c.confirmed}, "
                f"status={result.confirmation_status_label}"
            )

        if result.currently_harvested is not None:
            lines.append(f"  Currently marked harvested (as of now): {result.currently_harvested}")

        if result.data_gaps:
            lines.append("")
            lines.append("-" * 72)
            lines.append("NOT RECORDED, AND WHY")
            lines.append("-" * 72)
            for gap in result.data_gaps:
                lines.append(f"  - {gap}")

    return "\n".join(lines)


def to_json_dict(result: DecisionReplayResult) -> dict:
    return asdict(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--plot-id", required=True, help="e.g. p03")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, the date to replay")
    parser.add_argument(
        "--season", default=None,
        help="Season id, e.g. 2026-kuruvai. Inferred from stored records if omitted.",
    )
    parser.add_argument("--out", default=None, help="Path to write the JSON report to")
    args = parser.parse_args(argv)

    storage = get_storage()
    plot = storage.get_plot(args.plot_id)
    if plot is None:
        print(f"error: no plot found with plot_id={args.plot_id!r}", file=sys.stderr)
        return 1

    result = build_result(
        storage, plot_id=args.plot_id, requested_date=args.date, season=args.season,
    )

    print(render_text(result))

    if args.out is not None:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(to_json_dict(result), f, indent=2, default=str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
