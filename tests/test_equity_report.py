"""ADR-010 Part 2: equity_report.py. Hermetic -- FileStorage only, no live
calls, no Bedrock.
"""

from __future__ import annotations

import json
from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.reporting.config import SMALLHOLDER_THRESHOLD_ACRES
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import (
    DecisionRecord,
    HarvestConfirmation,
    LedgerEntry,
    SeasonRolloverPrompt,
)
from scripts import equity_report

SEASON = "2026-kuruvai"


def _cluster(cluster_id: str = "c1") -> Cluster:
    return Cluster(
        cluster_id=cluster_id, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
    )


def _farmer(farmer_id: str, name: str | None = None, cluster_id: str = "c1") -> Farmer:
    return Farmer(farmer_id=farmer_id, name=name or f"Farmer {farmer_id}", cluster_id=cluster_id)


def _plot(plot_id: str, farmer_id: str, area_acres: float, cluster_id: str = "c1") -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=cluster_id,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 12), area_acres=area_acres,
    )


def _decision_record(plot_id: str, farmer_id: str, cluster_id: str, decision_date: str, **overrides) -> DecisionRecord:
    base = dict(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=cluster_id,
        season_id=SEASON, decision_date=decision_date,
        accumulated_gdd=1681.4, maturity_gdd_used=1637.0, threshold_source="calibrated",
        outcome="contested", days_past_maturity=6, urgency=0.3, route_position=None,
        rain_threshold_mm=5.0, forecast_horizon_days=16, usable_harvest_days=3,
        machine_capacity_acres_per_day=3.5, capacity_budget_acres=10.5,
        resolved_at="2026-09-09T12:00:00+00:00",
    )
    base.update(overrides)
    return DecisionRecord(**base)


def test_cluster_not_found_exits_1(tmp_path, capsys) -> None:
    storage = FileStorage(tmp_path / "s.json")
    exit_code = equity_report.main(["--cluster", "nope", "--season", SEASON])
    assert exit_code == 1
    assert "no cluster found" in capsys.readouterr().err


def test_coverage_math_registered_served_never_served(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_farmer(_farmer("f2"))
    storage.put_farmer(_farmer("f3"))
    storage.put_plot(_plot("p1", "f1", 2.0))  # served
    storage.put_plot(_plot("p2", "f2", 1.5))  # never served
    storage.put_plot(_plot("p3", "f3", 3.0))  # never served (contested and lost, no escalation)
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id=SEASON, scheduled_date="2026-09-09",
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert result.registered_plot_count == 3
    assert result.registered_acres == 6.5
    assert section.served_plot_ids == ["p1"]
    assert section.served_acres == 2.0
    assert section.never_served_plot_ids == ["p2", "p3"]


def test_contested_and_lost_without_escalation_still_counts_as_never_served(tmp_path) -> None:
    """'Never served' means exactly that -- received no harvest slot --
    regardless of whether the plot lost a fair negotiation (no LedgerEntry
    at all, since only an escalation resolution writes one) or an
    escalated one (which does write a LedgerEntry for the loser). Neither
    case should be excluded from 'never served' just because a record of
    the loss exists elsewhere."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_farmer(_farmer("f2"))
    storage.put_plot(_plot("p1", "f1", 2.0))
    storage.put_plot(_plot("p2", "f2", 1.0))
    # p2 lost an ESCALATED negotiation this season -- has a real LedgerEntry.
    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f2", season_id=SEASON, days_bumped=1, outcome="bumped",
        resolved_at="2026-09-09T00:00:00+00:00", cluster_id="c1",
        plot_id="p2", opponent_plot_id="p1",
    ))
    # Neither plot was ever actually dispatched -- no HarvestConfirmation for either.

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.served_plot_ids == []
    assert section.never_served_plot_ids == ["p1", "p2"]


def test_smallholder_split_at_the_threshold_boundary(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_farmer(_farmer("f2"))
    at_threshold = _plot("p1", "f1", SMALLHOLDER_THRESHOLD_ACRES)  # <=, so smallholder
    above_threshold = _plot("p2", "f2", SMALLHOLDER_THRESHOLD_ACRES + 0.01)  # larger
    storage.put_plot(at_threshold)
    storage.put_plot(above_threshold)

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.smallholder_registered_count == 1
    assert section.larger_registered_count == 1


def test_repeat_bumps_detected_across_seasons(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1", name="Repeat Farmer"))
    storage.put_farmer(_farmer("f2", name="Once Farmer"))
    storage.put_plot(_plot("p1", "f1", 1.0))
    storage.put_plot(_plot("p2", "f2", 1.0))

    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f1", season_id="2025-kuruvai", days_bumped=2, outcome="bumped",
        resolved_at="2025-08-01T00:00:00+00:00", cluster_id="c1", plot_id="p1", opponent_plot_id="px",
    ))
    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f1", season_id="2026-kuruvai", days_bumped=1, outcome="bumped",
        resolved_at="2026-08-01T00:00:00+00:00", cluster_id="c1", plot_id="p1", opponent_plot_id="py",
    ))
    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f2", season_id="2026-kuruvai", days_bumped=1, outcome="bumped",
        resolved_at="2026-08-01T00:00:00+00:00", cluster_id="c1", plot_id="p2", opponent_plot_id="pz",
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=["2026-kuruvai"])

    assert len(result.repeat_bumps) == 1
    repeat = result.repeat_bumps[0]
    assert repeat.farmer_id == "f1"
    assert set(repeat.seasons) == {("2025-kuruvai", 2), ("2026-kuruvai", 1)}
    # "Once Farmer" bumped exactly once -- not flagged as a repeat.
    assert all(r.farmer_id != "f2" for r in result.repeat_bumps)


def test_a_farmer_bumped_once_is_not_flagged_as_a_repeat(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))
    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f1", season_id=SEASON, days_bumped=2, outcome="bumped",
        resolved_at="2026-08-01T00:00:00+00:00", cluster_id="c1", plot_id="p1", opponent_plot_id="px",
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    assert result.repeat_bumps == []


def test_operator_follow_through_excludes_pending_and_unknown_from_the_rate(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_farmer(_farmer("f2"))
    storage.put_farmer(_farmer("f3"))
    storage.put_plot(_plot("p1", "f1", 1.0))
    storage.put_plot(_plot("p2", "f2", 1.0))
    storage.put_plot(_plot("p3", "f3", 1.0))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id=SEASON,
        scheduled_date="2026-09-09", confirmed=True, confirmed_at="2026-09-09T18:00:00+00:00",
    ))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p2", farmer_id="f2", cluster_id="c1", season_id=SEASON,
        scheduled_date="2026-09-09", confirmed=False, confirmed_at="2026-09-09T18:00:00+00:00",
    ))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p3", farmer_id="f3", cluster_id="c1", season_id=SEASON,
        scheduled_date="2026-09-09",  # never asked -- "unknown"
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.confirmed_yes == 1
    assert section.confirmed_no == 1
    assert section.unknown == 1
    assert section.follow_through_rate == 0.5  # 1/(1+1), the unknown excluded from both halves


def test_no_replies_yet_returns_none_not_zero(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id=SEASON, scheduled_date="2026-09-09",
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    assert result.sections[0].follow_through_rate is None


def test_fairness_mechanism_activity_counts_from_decision_records(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))
    storage.put_decision_record(_decision_record("p1", "f1", "c1", "2026-09-01", fairness_decisive=True))
    storage.put_decision_record(_decision_record("p1", "f1", "c1", "2026-09-02", fairness_decisive=False))
    storage.put_decision_record(_decision_record("p1", "f1", "c1", "2026-09-03", fairness_decisive=None))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.decision_records_found == 3
    assert section.fairness_influenced_count == 1
    assert section.fairness_applied_not_decisive_count == 1
    assert section.fairness_not_applicable_count == 1


def test_a_season_with_no_decision_records_reports_the_gap_explicitly(tmp_path) -> None:
    """A season predating ADR-010 Part 0.5 has zero decision records --
    the report must say so, not silently show 0/0/0 and let a reader
    assume fairness was never a factor."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])

    assert result.sections[0].decision_records_found == 0
    assert any("no persisted decision records" in gap for gap in result.data_gaps)
    text = equity_report.render_text(result)
    assert "no decision records found" in text.lower()


def test_all_seasons_aggregates_across_discovered_seasons(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))
    storage.put_decision_record(_decision_record("p1", "f1", "c1", "2025-09-01", season_id="2025-kuruvai"))
    storage.put_decision_record(_decision_record("p1", "f1", "c1", "2026-09-01", season_id="2026-kuruvai"))

    plots = storage.get_plots_for_cluster("c1")
    farmers = storage.get_farmers_for_cluster("c1")
    season_ids = equity_report._discover_season_ids(storage, farmers, plots)

    assert season_ids == ["2025-kuruvai", "2026-kuruvai"]


def test_season_participation_section_states_no_rollover_ever_run(tmp_path) -> None:
    """A season with zero SeasonRolloverPrompt records (the common case
    -- either the first season ever, or every plot registered directly)
    must say so plainly, not render a bare, ambiguous 0/0/0."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.rollover_confirmed_plot_ids == []
    assert section.rollover_declined_plot_ids == []
    assert section.rollover_unknown_plot_ids == []
    text = equity_report.render_text(result)
    assert "no rollover prompt was ever run" in text


def test_season_participation_section_lists_confirmed_declined_unknown_separately(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    for pid in ("p-yes", "p-no", "p-silent"):
        storage.put_farmer(_farmer(f"f-{pid}"))
        storage.put_plot(_plot(pid, f"f-{pid}", 1.0))
    storage.put_season_rollover_prompt(SeasonRolloverPrompt(
        plot_id="p-yes", farmer_id="f-p-yes", cluster_id="c1",
        old_season_id="2025-kuruvai", new_season_id=SEASON,
        asked_at="2026-08-01T00:00:00+00:00", replied=True, replied_at="2026-08-02T00:00:00+00:00",
    ))
    storage.put_season_rollover_prompt(SeasonRolloverPrompt(
        plot_id="p-no", farmer_id="f-p-no", cluster_id="c1",
        old_season_id="2025-kuruvai", new_season_id=SEASON,
        asked_at="2026-08-01T00:00:00+00:00", replied=False, replied_at="2026-08-02T00:00:00+00:00",
    ))
    storage.put_season_rollover_prompt(SeasonRolloverPrompt(
        plot_id="p-silent", farmer_id="f-p-silent", cluster_id="c1",
        old_season_id="2025-kuruvai", new_season_id=SEASON,
        asked_at="2026-08-01T00:00:00+00:00",
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.rollover_confirmed_plot_ids == ["p-yes"]
    assert section.rollover_declined_plot_ids == ["p-no"]
    assert section.rollover_unknown_plot_ids == ["p-silent"]
    text = equity_report.render_text(result)
    assert "declined and unknown are reported separately" in text


def test_empty_cluster_no_plots_at_all(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])

    assert result.registered_plot_count == 0
    assert result.sections[0].served_plot_ids == []
    text = equity_report.render_text(result)  # must not crash on an empty cluster
    assert "EQUITY REPORT" in text


def test_caveat_present_verbatim_in_both_outputs(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    text = equity_report.render_text(result)
    json_dict = equity_report.to_json_dict(result)

    assert equity_report.CAVEAT in text
    assert json_dict["caveat"] == equity_report.CAVEAT


def test_smallholder_threshold_stated_in_the_header(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    text = equity_report.render_text(result)

    assert f"{SMALLHOLDER_THRESHOLD_ACRES:g} acres" in text
    assert "policy parameter, not sourced" in text


def test_text_and_json_built_from_one_result_object(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    text = equity_report.render_text(result)
    json_dict = equity_report.to_json_dict(result)

    assert json_dict["registered_plot_count"] == 1
    assert "1 plot" in text.replace("plot(s)", "plot") or "1 plot(s)" in text
    assert json.dumps(json_dict, default=str)


def test_no_bedrock_calls_possible_in_this_script(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "s.json"
    storage = FileStorage(storage_path)
    storage.put_cluster(_cluster())

    def _boom(*args, **kwargs):
        raise AssertionError("no Bedrock calls expected in a reporting script")

    monkeypatch.setattr("harvest_convoy.agents.advocate.get_advocate_claim", _boom)
    monkeypatch.setattr("harvest_convoy.storage.file_storage.DEFAULT_FILE_PATH", storage_path)

    exit_code = equity_report.main(["--cluster", "c1", "--season", SEASON])
    assert exit_code == 0


def test_main_writes_json_to_out_path(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "s.json"
    storage = FileStorage(storage_path)
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1", 1.0))

    monkeypatch.setattr("harvest_convoy.storage.file_storage.DEFAULT_FILE_PATH", storage_path)
    out_path = tmp_path / "out.json"
    exit_code = equity_report.main(
        ["--cluster", "c1", "--season", SEASON, "--out", str(out_path)]
    )

    assert exit_code == 0
    written = json.loads(out_path.read_text())
    assert written["cluster_id"] == "c1"


def test_season_and_all_seasons_are_mutually_exclusive() -> None:
    import pytest

    with pytest.raises(SystemExit):
        equity_report.main(["--cluster", "c1", "--season", SEASON, "--all-seasons"])


def test_route_activity_counts_accepted_no_response_and_modified(tmp_path) -> None:
    """ADR-013 Decision 10: three RouteOverride records, one of each
    status, plus one modified-with-a-drop -- proves the section
    distinguishes all of them, not a single collapsed count."""
    from harvest_convoy.storage.interface import RouteOverride

    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_route_override(RouteOverride(
        cluster_id="c1", season_id=SEASON, decision_date="2026-09-01",
        proposed_route=["p1"], current_route=["p1"],
        proposed_at="2026-09-01T06:00:00+00:00", accepted_at="2026-09-01T09:00:00+00:00",
    ))
    storage.put_route_override(RouteOverride(
        cluster_id="c1", season_id=SEASON, decision_date="2026-09-02",
        proposed_route=["p1"], current_route=["p1"],
        proposed_at="2026-09-02T06:00:00+00:00",
    ))
    storage.put_route_override(RouteOverride(
        cluster_id="c1", season_id=SEASON, decision_date="2026-09-03",
        proposed_route=["p1", "p2"], current_route=["p2"],  # p1 dropped
        proposed_at="2026-09-03T06:00:00+00:00", last_modified_at="2026-09-03T10:00:00+00:00",
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.route_overrides_proposed == 3
    assert section.route_overrides_accepted == 1
    assert section.route_overrides_no_response == 1
    assert section.route_overrides_modified == 1
    assert section.route_overrides_modified_with_drop == 1
    assert section.route_override_acceptance_rate == 2 / 3


def test_route_activity_reports_no_route_ever_proposed(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]
    assert section.route_overrides_proposed == 0
    assert section.route_override_acceptance_rate is None
    assert "no route was ever proposed" in "\n".join(equity_report._render_section_text(section))


def test_bumps_by_decided_by_distinguishes_escalation_from_override(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_farmer(_farmer("f2"))
    storage.put_plot(_plot("p1", "f1", 2.0))
    storage.put_plot(_plot("p2", "f2", 2.0))
    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f1", season_id=SEASON, days_bumped=1, outcome="bumped",
        resolved_at="2026-09-01T00:00:00+00:00", cluster_id="c1", plot_id="p1",
        opponent_plot_id="px", decided_by="operator_escalation",
    ))
    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f2", season_id=SEASON, days_bumped=1, outcome="operator_override",
        resolved_at="2026-09-02T00:00:00+00:00", cluster_id="c1", plot_id="p2",
        opponent_plot_id=None, decided_by="operator_override",
    ))

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert section.bumps_by_decided_by == {"operator_escalation": 1, "operator_override": 1}
