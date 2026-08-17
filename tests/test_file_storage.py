from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import LedgerEntry


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
