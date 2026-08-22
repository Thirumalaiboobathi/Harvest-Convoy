"""ADR-010 Part 1: explain_decision.py. Hermetic -- FileStorage only, no
live calls, no Bedrock. See ADR-010 for what this script can and cannot
recover and why.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import (
    DecisionRecord,
    HarvestConfirmation,
    LedgerEntry,
    OperatorAuditEvent,
)
from scripts import explain_decision

SEASON = "2026-kuruvai"


def _cluster(cluster_id: str = "c1") -> Cluster:
    return Cluster(
        cluster_id=cluster_id, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
    )


def _farmer(farmer_id: str, name: str = "Kannan Raja", cluster_id: str = "c1") -> Farmer:
    return Farmer(farmer_id=farmer_id, name=name, cluster_id=cluster_id)


def _plot(plot_id: str, farmer_id: str, cluster_id: str = "c1") -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=cluster_id,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 12), area_acres=3.0,
    )


def _decision_record(plot_id: str, farmer_id: str, cluster_id: str, decision_date: str, **overrides) -> DecisionRecord:
    base = dict(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=cluster_id,
        season_id=SEASON, decision_date=decision_date,
        accumulated_gdd=1681.4, maturity_gdd_used=1637.0, threshold_source="calibrated",
        outcome="fits", days_past_maturity=6, urgency=0.3, route_position=0,
        rain_threshold_mm=5.0, forecast_horizon_days=16, usable_harvest_days=3,
        machine_capacity_acres_per_day=3.5, capacity_budget_acres=10.5,
        resolved_at="2026-09-09T12:00:00+00:00",
    )
    base.update(overrides)
    return DecisionRecord(**base)


def test_plot_not_found_exits_1(tmp_path, capsys) -> None:
    storage = FileStorage(tmp_path / "s.json")
    exit_code = explain_decision.main(["--plot-id", "nope", "--date", "2026-09-09"])
    assert exit_code == 1
    assert "no plot found" in capsys.readouterr().err


def test_full_replay_when_a_decision_record_exists(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(_decision_record("p03", "f1", "c1", "2026-09-09"))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.decision_record is not None
    assert result.decision_record.accumulated_gdd == 1681.4
    assert result.decision_gap_note is None
    assert result.data_gaps == []
    assert result.season_id == SEASON
    assert "inferred from a decision record" in result.season_resolution_note

    text = explain_decision.render_text(result)
    assert "1681.4" in text
    assert "1637.0" in text
    assert "calibrated" in text


def test_resolved_negotiation_narrative_includes_both_claims(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(_decision_record(
        "p03", "f1", "c1", "2026-09-09",
        outcome="contested", opponent_plot_id="p04",
        own_claim={"plot_id": "p03", "urgency_score": 0.3, "argument": "my case"},
        opponent_claim={"plot_id": "p04", "urgency_score": 0.05, "argument": "their case"},
        rounds_run=3, resolution="escalated_won", fairness_decisive=None,
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=SEASON,
    )
    text = explain_decision.render_text(result)

    assert "p04" in text
    assert "my case" in text
    assert "their case" in text
    assert "escalated_won" in text
    assert "not applicable" in text  # fairness_decisive=None


def test_pending_escalation_states_not_yet_resolved(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(_decision_record(
        "p03", "f1", "c1", "2026-09-09",
        outcome="contested", opponent_plot_id="p04", rounds_run=3,
        resolution="escalated", resolved_at=None,
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=SEASON,
    )
    text = explain_decision.render_text(result)
    assert "awaiting a human tap" in text


def test_never_recorded_is_distinct_from_did_not_happen(tmp_path) -> None:
    """The load-bearing sentence: a plot/date predating ADR-010 Part 0.5
    (or otherwise missing a DecisionRecord) must say so explicitly, never
    render a silently thinner report that looks like a clean absence."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    # A ledger entry exists (something happened), but no DecisionRecord.
    storage.put_ledger_entry(LedgerEntry(
        farmer_id="f1", season_id=SEASON, days_bumped=1, outcome="bumped",
        resolved_at="2025-08-19T00:00:00+00:00", cluster_id="c1",
        plot_id="p03", opponent_plot_id="p04",
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2025-08-19", season=None,
    )

    assert result.decision_record is None
    assert result.decision_gap_note is not None
    assert "never recorded" in result.decision_gap_note
    assert len(result.data_gaps) == 4
    assert result.season_id == SEASON  # still inferred, from the ledger entry
    assert "ledger entry" in result.season_resolution_note

    text = explain_decision.render_text(result)
    assert "No decision record exists" in text
    assert "NOT RECORDED, AND WHY" in text


def test_season_omitted_and_unresolvable_degrades_without_crashing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    # Nothing at all references this plot on this date.

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.season_id is None
    assert "no --season given" in result.season_resolution_note
    assert result.confirmation is None
    assert result.currently_harvested is None


def test_farmer_record_missing_renders_as_stated_absence(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_plot(_plot("p03", "no-such-farmer"))  # farmer never seeded
    storage.put_decision_record(_decision_record("p03", "no-such-farmer", "c1", "2026-09-09"))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=SEASON,
    )

    assert result.farmer is None
    assert result.plot is not None
    # Must not crash rendering text with a missing farmer.
    text = explain_decision.render_text(result)
    assert "unknown farmer" in text


def test_cluster_record_missing_renders_as_stated_absence(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1", cluster_id="ghost-cluster"))  # cluster never seeded
    storage.put_decision_record(_decision_record("p03", "f1", "ghost-cluster", "2026-09-09"))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=SEASON,
    )

    assert result.cluster is None
    text = explain_decision.render_text(result)
    assert "unknown cluster" in text


def test_confirmation_and_harvested_status_included_when_season_known(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.mark_plot_harvested("p03", "c1", SEASON, dispatched_at="2026-09-09")
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p03", farmer_id="f1", cluster_id="c1", season_id=SEASON,
        scheduled_date="2026-09-09", confirmed=True, confirmed_at="2026-09-09T18:00:00+00:00",
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=SEASON,
    )

    assert result.confirmation is not None
    assert result.confirmation_status_label == "confirmed_yes"
    assert result.currently_harvested is True


def test_multiple_decision_records_same_date_different_seasons_is_ambiguous(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(_decision_record("p03", "f1", "c1", "2026-09-09"))
    storage.put_decision_record(DecisionRecord(
        **{**_decision_record("p03", "f1", "c1", "2026-09-09").__dict__, "season_id": "2026-samba"}
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.season_id is None
    assert "ambiguous" in result.season_resolution_note


def test_text_and_json_are_built_from_one_result_object(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(_decision_record("p03", "f1", "c1", "2026-09-09"))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=SEASON,
    )
    text = explain_decision.render_text(result)
    json_dict = explain_decision.to_json_dict(result)

    # Every fact asserted in the text also appears in the JSON's raw records.
    assert json_dict["decision_record"]["accumulated_gdd"] == 1681.4
    assert "1681.4" in text
    assert json.dumps(json_dict, default=str)  # fully JSON-serializable


def test_provenance_header_present_in_both_outputs(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )
    text = explain_decision.render_text(result)
    json_dict = explain_decision.to_json_dict(result)

    assert "PROVENANCE" in text
    assert "FileStorage" in text
    assert json_dict["provenance"]["storage_backend"] == "FileStorage"
    assert json_dict["provenance"]["git_commit"]


def test_main_writes_json_to_out_path(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "s.json"
    storage = FileStorage(storage_path)
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(_decision_record("p03", "f1", "c1", "2026-09-09"))

    monkeypatch.setattr(
        "harvest_convoy.storage.file_storage.DEFAULT_FILE_PATH", storage_path
    )
    out_path = tmp_path / "out.json"
    exit_code = explain_decision.main(
        ["--plot-id", "p03", "--date", "2026-09-09", "--out", str(out_path)]
    )

    assert exit_code == 0
    assert out_path.exists()
    written = json.loads(out_path.read_text())
    assert written["decision_record"]["plot_id"] == "p03"


def test_no_bedrock_calls_possible_in_this_script(tmp_path, monkeypatch) -> None:
    """Patches the model provider to raise, then runs the script to
    completion -- proves no Bedrock call is reachable from this code path,
    not just that none is currently made."""
    storage_path = tmp_path / "s.json"
    storage = FileStorage(storage_path)
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))

    def _boom(*args, **kwargs):
        raise AssertionError("no Bedrock calls expected in a reporting script")

    monkeypatch.setattr("harvest_convoy.agents.advocate.get_advocate_claim", _boom)
    monkeypatch.setattr(
        "harvest_convoy.storage.file_storage.DEFAULT_FILE_PATH", storage_path
    )

    exit_code = explain_decision.main(["--plot-id", "p03", "--date", "2026-09-09"])
    assert exit_code == 0


# --- ADR-012 Decision 2: operator identity surfaced where it affects a decision ---

def test_escalated_decision_surfaces_the_operator_in_effect_that_day(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(
        _decision_record("p03", "f1", "c1", "2026-09-09", resolution="escalated_won")
    )
    storage.put_operator_audit_event(OperatorAuditEvent(
        cluster_id="c1", event_type="enrolled", occurred_at="2026-09-01T10:00:00+00:00",
        code_used="ABCD1234", new_operator_chat_id=555,
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.operator_at_decision is not None
    assert result.operator_at_decision.new_operator_chat_id == 555
    assert result.operator_relevance_note is None
    text = explain_decision.render_text(result)
    assert "555" in text
    assert "ABCD1234" in text


def test_breakdown_recompute_decision_surfaces_the_operator_too(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(
        _decision_record("p03", "f1", "c1", "2026-09-09", trigger_reason="breakdown_recompute")
    )
    storage.put_operator_audit_event(OperatorAuditEvent(
        cluster_id="c1", event_type="enrolled", occurred_at="2026-09-01T10:00:00+00:00",
        code_used="ABCD1234", new_operator_chat_id=555,
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.operator_at_decision is not None
    assert result.operator_at_decision.new_operator_chat_id == 555


def test_operator_involved_decision_with_no_audit_event_gets_a_relevance_note(tmp_path) -> None:
    """The operator was set by hand (predates ADR-012) -- no
    OperatorAuditEvent exists, so this is stated plainly rather than
    silently showing nothing."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(
        _decision_record("p03", "f1", "c1", "2026-09-09", resolution="escalated_lost")
    )

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.operator_at_decision is None
    assert result.operator_relevance_note is not None
    assert "no OperatorAuditEvent exists" in result.operator_relevance_note
    text = explain_decision.render_text(result)
    assert "no OperatorAuditEvent exists" in text


def test_operator_not_involved_decision_shows_nothing_about_operators(tmp_path) -> None:
    """An ordinary FITS decision an operator never touched -- no clutter."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(_decision_record("p03", "f1", "c1", "2026-09-09", outcome="fits"))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.operator_at_decision is None
    assert result.operator_relevance_note is None
    text = explain_decision.render_text(result)
    assert "operator" not in text.lower()


def test_operator_active_before_the_decision_date_is_correctly_found_same_day(tmp_path) -> None:
    """An operator enrolled earlier the SAME day as the decision --
    proves the end-of-day cutoff handles a bare decision_date against a
    full ISO timestamp correctly (a naive string compare would wrongly
    exclude this)."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p03", "f1"))
    storage.put_decision_record(
        _decision_record("p03", "f1", "c1", "2026-09-09", resolution="escalated_won")
    )
    storage.put_operator_audit_event(OperatorAuditEvent(
        cluster_id="c1", event_type="enrolled", occurred_at="2026-09-09T06:00:00+00:00",
        code_used="ABCD1234", new_operator_chat_id=555,
    ))

    result = explain_decision.build_result(
        storage, plot_id="p03", requested_date="2026-09-09", season=None,
    )

    assert result.operator_at_decision is not None
    assert result.operator_at_decision.new_operator_chat_id == 555
