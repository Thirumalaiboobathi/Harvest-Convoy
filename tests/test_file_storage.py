from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import DecisionRecord, LedgerEntry, SeasonRolloverPrompt


def _cluster() -> Cluster:
    return Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
    )


def _farmer(farmer_id: str, cluster_id: str = "c1") -> Farmer:
    return Farmer(farmer_id=farmer_id, name=f"Farmer {farmer_id}", cluster_id=cluster_id)


def _plot(plot_id: str, cluster_id: str = "c1", farmer_id: str = "f1") -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=cluster_id,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 18), area_acres=1.5,
    )


def test_list_cluster_ids_returns_every_seeded_cluster_sorted(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(Cluster(
        cluster_id="zebra", name="Z", machine_capacity_acres_per_day=3.5,
        machine_start_lat=1.0, machine_start_lon=1.0,
    ))
    storage.put_cluster(Cluster(
        cluster_id="apple", name="A", machine_capacity_acres_per_day=3.5,
        machine_start_lat=1.0, machine_start_lon=1.0,
    ))
    assert storage.list_cluster_ids() == ["apple", "zebra"]


def test_list_cluster_ids_empty_when_no_clusters_seeded(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert storage.list_cluster_ids() == []


def test_cluster_round_trips(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert storage.get_cluster("c1") is None
    storage.put_cluster(_cluster())
    loaded = storage.get_cluster("c1")
    assert loaded is not None
    assert loaded.name == "Test Cluster"


def test_plot_round_trips_including_the_date_field(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_plot(_plot("p1"))
    loaded = storage.get_plot("p1")
    assert loaded is not None
    assert loaded.transplant_date == date(2026, 5, 18)
    assert isinstance(loaded.transplant_date, date)


def test_get_plots_for_cluster_filters_correctly(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_plot(_plot("p1", cluster_id="c1"))
    storage.put_plot(_plot("p2", cluster_id="c1"))
    storage.put_plot(_plot("p3", cluster_id="c2"))

    plots = storage.get_plots_for_cluster("c1")
    assert {p.plot_id for p in plots} == {"p1", "p2"}


def test_get_farmers_for_cluster_filters_correctly(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_farmer(_farmer("f1", cluster_id="c1"))
    storage.put_farmer(_farmer("f2", cluster_id="c1"))
    storage.put_farmer(_farmer("f3", cluster_id="c2"))

    farmers = storage.get_farmers_for_cluster("c1")
    assert {f.farmer_id for f in farmers} == {"f1", "f2"}


def test_data_survives_reopening_the_same_file(tmp_path) -> None:
    path = tmp_path / "s.json"
    FileStorage(path).put_plot(_plot("p1"))

    reopened = FileStorage(path)
    assert reopened.get_plot("p1") is not None


def test_missing_plot_returns_none_not_an_error(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert storage.get_plot("does-not-exist") is None


def test_missing_farmer_with_no_ledger_history_returns_empty_list(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert storage.get_ledger_entries("nobody") == []


def _entry(farmer_id: str, season_id: str, days_bumped: int = 1) -> LedgerEntry:
    return LedgerEntry(
        farmer_id=farmer_id, season_id=season_id, days_bumped=days_bumped,
        outcome="bumped", resolved_at="2026-08-17T00:00:00+00:00",
        cluster_id="c1", plot_id="p1", opponent_plot_id="p2",
    )


def test_put_ledger_entry_succeeds_once(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    result = storage.put_ledger_entry(_entry("f1", "season-1"))
    assert result.success is True
    assert storage.get_ledger_entries("f1") == [_entry("f1", "season-1")]


def test_put_ledger_entry_is_idempotent_per_farmer_and_season(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    first = storage.put_ledger_entry(_entry("f1", "season-1", days_bumped=1))
    second = storage.put_ledger_entry(_entry("f1", "season-1", days_bumped=99))

    assert first.success is True
    assert second.success is False
    # The second (would-be duplicate) write did not overwrite the first.
    entries = storage.get_ledger_entries("f1")
    assert len(entries) == 1
    assert entries[0].days_bumped == 1


def test_put_ledger_entry_allows_different_seasons_for_same_farmer(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_ledger_entry(_entry("f1", "season-1"))
    storage.put_ledger_entry(_entry("f1", "season-2"))

    entries = storage.get_ledger_entries("f1")
    assert {e.season_id for e in entries} == {"season-1", "season-2"}


def test_corrupt_storage_file_degrades_to_empty_rather_than_crashing(tmp_path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{not valid json")
    storage = FileStorage(path)
    assert storage.get_plot("anything") is None
    assert storage.get_ledger_entries("anyone") == []


def test_legacy_farmer_record_without_language_key_defaults_to_tamil(tmp_path) -> None:
    """A pre-ADR-008 stored record (written before Farmer.language
    existed) has no "language" key in its dict at all -- Farmer(**raw)
    must fall through to the dataclass default ("ta"), not KeyError.
    Hand-built directly (bypassing put_farmer, which would always write
    the current shape) so this actually proves the legacy-record claim
    rather than trusting it."""
    storage = FileStorage(tmp_path / "s.json")
    storage._data["farmers"]["legacy1"] = {
        "farmer_id": "legacy1", "name": "Old Record", "cluster_id": "c1",
        "telegram_chat_id": 555,
        # no "language" key -- simulates a record from before this field existed
    }
    storage._save()

    reloaded = FileStorage(tmp_path / "s.json")
    farmer = reloaded.get_farmer("legacy1")
    assert farmer is not None
    assert farmer.language == "ta"


def test_legacy_plot_record_without_area_unit_key_defaults_to_acre(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage._data["plots"]["legacyp1"] = {
        "plot_id": "legacyp1", "farmer_id": "f1", "cluster_id": "c1",
        "lat": 9.87, "lon": 77.46, "crop": "paddy", "variety": "ADT45",
        "transplant_date": "2026-05-18", "area_acres": 2.0,
        # no "area_unit" key
    }
    storage._save()

    reloaded = FileStorage(tmp_path / "s.json")
    plot = reloaded.get_plot("legacyp1")
    assert plot is not None
    assert plot.area_unit == "acre"


def test_legacy_cluster_record_without_maturity_gdd_override_key_defaults_to_none(tmp_path) -> None:
    """Same free-default story as language/area_unit, for ADR-008's
    per-cluster maturity threshold (Decision 2) -- a cluster seeded
    before that field existed reads back as None, which is exactly what
    scheduling/solver.py checks for to trigger its fallback-and-log path."""
    storage = FileStorage(tmp_path / "s.json")
    storage._data["clusters"]["legacyc1"] = {
        "cluster_id": "legacyc1", "name": "Old Cluster",
        "machine_capacity_acres_per_day": 3.5,
        "machine_start_lat": 9.865, "machine_start_lon": 77.454,
        "operator_chat_id": None,
        # no "maturity_gdd_override" key
    }
    storage._save()

    reloaded = FileStorage(tmp_path / "s.json")
    cluster = reloaded.get_cluster("legacyc1")
    assert cluster is not None
    assert cluster.maturity_gdd_override is None


def test_legacy_cluster_record_without_operator_language_key_defaults_to_tamil(tmp_path) -> None:
    """Same free-default story, for Cluster.operator_language (added in
    the same review round that reversed ADR-008 Decision 8's original
    English-only scope boundary for operator-facing text)."""
    storage = FileStorage(tmp_path / "s.json")
    storage._data["clusters"]["legacyc2"] = {
        "cluster_id": "legacyc2", "name": "Old Cluster 2",
        "machine_capacity_acres_per_day": 3.5,
        "machine_start_lat": 9.865, "machine_start_lon": 77.454,
        "operator_chat_id": None, "maturity_gdd_override": None,
        # no "operator_language" key
    }
    storage._save()

    reloaded = FileStorage(tmp_path / "s.json")
    cluster = reloaded.get_cluster("legacyc2")
    assert cluster is not None
    assert cluster.operator_language == "ta"


# --- Plot harvest lifecycle -- ADR-009 Part 1.5 ---

def test_mark_and_get_harvested_plot_ids_round_trips(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.mark_plot_harvested("p01", "c1", "2026-kuruvai", dispatched_at="2026-08-16")
    storage.mark_plot_harvested("p02", "c1", "2026-kuruvai", dispatched_at="2026-08-16")

    assert storage.get_harvested_plot_ids("c1", "2026-kuruvai") == {"p01", "p02"}


def test_a_season_with_no_records_returns_an_empty_set(tmp_path) -> None:
    """No separate Season entity -- season_id is just an opaque string, so
    a fresh one has no prior harvest state by construction. See ADR-009
    Part 1.5, Decision D."""
    storage = FileStorage(tmp_path / "s.json")
    storage.mark_plot_harvested("p01", "c1", "2026-kuruvai", dispatched_at="2026-08-16")

    assert storage.get_harvested_plot_ids("c1", "2026-samba") == set()
    assert storage.get_harvested_plot_ids("unknown-cluster", "2026-kuruvai") == set()


def test_clear_plot_harvest_returns_a_plot_to_the_schedulable_pool(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.mark_plot_harvested("p01", "c1", "2026-kuruvai", dispatched_at="2026-08-16")

    result = storage.clear_plot_harvest("p01", "c1", "2026-kuruvai")

    assert result.success
    assert storage.get_harvested_plot_ids("c1", "2026-kuruvai") == set()


def test_clear_plot_harvest_on_an_unmarked_plot_is_a_no_op_success(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")

    result = storage.clear_plot_harvest("never-marked", "c1", "2026-kuruvai")

    assert result.success
    assert storage.get_harvested_plot_ids("c1", "2026-kuruvai") == set()


def test_mark_plot_harvested_is_overwrite_not_error_on_a_second_call(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.mark_plot_harvested("p01", "c1", "2026-kuruvai", dispatched_at="2026-08-16")

    result = storage.mark_plot_harvested("p01", "c1", "2026-kuruvai", dispatched_at="2026-08-17")

    assert result.success
    assert storage.get_harvested_plot_ids("c1", "2026-kuruvai") == {"p01"}


def test_harvest_state_persists_across_a_reload(tmp_path) -> None:
    path = tmp_path / "s.json"
    storage = FileStorage(path)
    storage.mark_plot_harvested("p01", "c1", "2026-kuruvai", dispatched_at="2026-08-16")

    reloaded = FileStorage(path)
    assert reloaded.get_harvested_plot_ids("c1", "2026-kuruvai") == {"p01"}


# --- Decision records -- ADR-010 Part 0.5 ---

def _decision_record(
    plot_id: str, season_id: str = "2026-kuruvai", decision_date: str = "2026-09-09",
    **overrides,
) -> DecisionRecord:
    base = dict(
        plot_id=plot_id, farmer_id=f"farmer-{plot_id}", cluster_id="c1",
        season_id=season_id, decision_date=decision_date,
        accumulated_gdd=1681.4, maturity_gdd_used=1637.0, threshold_source="calibrated",
        outcome="contested", days_past_maturity=6, urgency=0.3, route_position=None,
        rain_threshold_mm=5.0, forecast_horizon_days=16, usable_harvest_days=3,
        machine_capacity_acres_per_day=3.5, capacity_budget_acres=10.5,
    )
    base.update(overrides)
    return DecisionRecord(**base)


def test_decision_record_round_trips(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    record = _decision_record(
        "p03", opponent_plot_id="p04",
        own_claim={"urgency_score": 0.3, "concedes": False},
        opponent_claim={"urgency_score": 0.05, "concedes": False},
        rounds_run=3, resolution="escalated", fairness_decisive=None,
    )
    result = storage.put_decision_record(record)

    assert result.success
    loaded = storage.get_decision_record("p03", "2026-kuruvai", "2026-09-09")
    assert loaded == record


def test_get_decision_record_returns_none_when_not_found(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert storage.get_decision_record("nope", "2026-kuruvai", "2026-09-09") is None


def test_put_decision_record_overwrites_on_a_retried_write(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(_decision_record("p03", resolution="escalated"))
    storage.put_decision_record(_decision_record("p03", resolution="escalated_won"))

    loaded = storage.get_decision_record("p03", "2026-kuruvai", "2026-09-09")
    assert loaded.resolution == "escalated_won"


def test_get_decision_records_for_plot_filters_by_season_when_given(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(_decision_record("p03", season_id="2026-kuruvai"))
    storage.put_decision_record(_decision_record("p03", season_id="2026-samba"))

    kuruvai_only = storage.get_decision_records_for_plot("p03", season_id="2026-kuruvai")
    assert len(kuruvai_only) == 1
    assert kuruvai_only[0].season_id == "2026-kuruvai"


def test_get_decision_records_for_plot_returns_every_season_when_none_given(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(_decision_record("p03", season_id="2026-kuruvai"))
    storage.put_decision_record(_decision_record("p03", season_id="2026-samba"))

    all_seasons = storage.get_decision_records_for_plot("p03")
    assert {r.season_id for r in all_seasons} == {"2026-kuruvai", "2026-samba"}


def test_get_decision_records_for_cluster_filters_by_cluster_and_season(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(_decision_record("p03", cluster_id="c1"))
    storage.put_decision_record(_decision_record("p07", cluster_id="c2"))

    records = storage.get_decision_records_for_cluster("c1", "2026-kuruvai")
    assert {r.plot_id for r in records} == {"p03"}


def test_a_plot_with_no_decision_history_returns_an_empty_list(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert storage.get_decision_records_for_plot("never-decided") == []


# --- Season rollover -- ADR-011 Part 1 ---

def _rollover_prompt(plot_id: str, cluster_id: str = "c1", **overrides) -> SeasonRolloverPrompt:
    base = dict(
        plot_id=plot_id, farmer_id=f"farmer-{plot_id}", cluster_id=cluster_id,
        old_season_id="2026-kuruvai", new_season_id="2026-samba",
        asked_at="2026-10-01T00:00:00+00:00",
    )
    base.update(overrides)
    return SeasonRolloverPrompt(**base)


def test_season_rollover_prompt_round_trips(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    prompt = _rollover_prompt("p01", replied=True, replied_at="2026-10-02T00:00:00+00:00")
    result = storage.put_season_rollover_prompt(prompt)

    assert result.success
    assert storage.get_season_rollover_prompt("p01", "2026-samba") == prompt


def test_get_season_rollover_prompt_returns_none_when_not_found(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert storage.get_season_rollover_prompt("nope", "2026-samba") is None


def test_declined_and_unresponsive_are_distinguishable_in_storage(tmp_path) -> None:
    """The asymmetry ADR-011 Part 1 requires: replied=False (declined)
    and replied=None (never answered) are different, separately
    recoverable facts, not one collapsed value."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_season_rollover_prompt(_rollover_prompt(
        "p-declined", replied=False, replied_at="2026-10-02T00:00:00+00:00",
    ))
    storage.put_season_rollover_prompt(_rollover_prompt("p-unresponsive"))  # replied stays None

    declined = storage.get_season_rollover_prompt("p-declined", "2026-samba")
    unresponsive = storage.get_season_rollover_prompt("p-unresponsive", "2026-samba")

    assert declined.replied is False
    assert declined.replied_at is not None
    assert unresponsive.replied is None
    assert unresponsive.replied_at is None


def test_get_season_rollover_prompts_for_cluster_filters_correctly(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_season_rollover_prompt(_rollover_prompt("p01", cluster_id="c1"))
    storage.put_season_rollover_prompt(_rollover_prompt("p02", cluster_id="c2"))

    prompts = storage.get_season_rollover_prompts_for_cluster("c1", "2026-samba")
    assert {p.plot_id for p in prompts} == {"p01"}
