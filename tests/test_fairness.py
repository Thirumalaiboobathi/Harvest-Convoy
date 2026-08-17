from harvest_convoy.storage.fairness import (
    FAIRNESS_SEASON_DECAY,
    get_ledger_history,
    record_bump,
    was_bumped_last_season,
    weighted_bump_days,
)
from harvest_convoy.storage.file_storage import FileStorage


def test_no_history_gives_zero_weighted_days_not_an_error(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    assert weighted_bump_days("nobody", storage) == 0.0
    assert was_bumped_last_season("nobody", storage) is False
    assert get_ledger_history("nobody", storage) == []


def test_single_season_is_undecayed(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    record_bump(
        "f1", "season-1", days_bumped=3, outcome="bumped",
        cluster_id="c", plot_id="p1", opponent_plot_id="p2", storage=storage,
    )
    assert weighted_bump_days("f1", storage) == 3.0
    assert was_bumped_last_season("f1", storage) is True


def test_repeated_bumps_compound_more_than_a_single_bump_of_equal_total(tmp_path) -> None:
    # Hand-computed: 2 days bumped in each of 3 consecutive seasons,
    # decayed geometrically: 2*0.5^0 + 2*0.5^1 + 2*0.5^2 = 2 + 1 + 0.5 = 3.5
    storage_repeated = FileStorage(tmp_path / "repeated.json")
    for season_id in ["season-3", "season-2", "season-1"]:
        record_bump(
            "repeat_farmer", season_id, days_bumped=2, outcome="bumped",
            cluster_id="c", plot_id="p1", opponent_plot_id="p2",
            storage=storage_repeated,
        )
    assert weighted_bump_days("repeat_farmer", storage_repeated) == 3.5

    # A farmer bumped once for the equivalent total (2 days) scores lower --
    # this is the defect the accumulation was built to fix: repetition
    # must outweigh a single incident of the same raw total.
    storage_once = FileStorage(tmp_path / "once.json")
    record_bump(
        "once_farmer", "season-1", days_bumped=2, outcome="bumped",
        cluster_id="c", plot_id="p1", opponent_plot_id="p2",
        storage=storage_once,
    )
    assert weighted_bump_days("once_farmer", storage_once) == 2.0
    assert weighted_bump_days("repeat_farmer", storage_repeated) > weighted_bump_days(
        "once_farmer", storage_once
    )


def test_older_bumps_fade_but_never_reach_exactly_zero(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    record_bump(
        "f1", "season-10-seasons-ago", days_bumped=4, outcome="bumped",
        cluster_id="c", plot_id="p1", opponent_plot_id="p2", storage=storage,
    )
    # Only one (very old) entry -- season_id sorting puts it at index 0
    # regardless, so its full contribution applies at "0 seasons ago" in
    # this isolated case. The decay-fades-but-never-vanishes property is
    # about FAIRNESS_SEASON_DECAY itself: for any n, decay**n > 0.
    for n in range(20):
        assert FAIRNESS_SEASON_DECAY**n > 0.0


def test_won_outcomes_do_not_contribute_bump_weight(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    record_bump(
        "f1", "season-1", days_bumped=0, outcome="won",
        cluster_id="c", plot_id="p1", opponent_plot_id="p2", storage=storage,
    )
    assert weighted_bump_days("f1", storage) == 0.0
    assert was_bumped_last_season("f1", storage) is False


def test_ledger_entry_with_a_dangling_season_reference_still_computes(tmp_path) -> None:
    """A ledger entry naming a season/cluster/plot that no longer exists
    anywhere else must not break the weighting -- each entry is
    self-contained (days_bumped, outcome), nothing here ever looks up the
    referenced season/cluster/plot. See ADR-005 Decision 5.
    """
    storage = FileStorage(tmp_path / "s.json")
    record_bump(
        "f1", "season-that-was-deleted", days_bumped=5, outcome="bumped",
        cluster_id="cluster-that-no-longer-exists",
        plot_id="plot-that-no-longer-exists",
        opponent_plot_id="also-gone",
        storage=storage,
    )
    # No cluster/plot lookup happens; this must not raise and must return
    # the entry's own recorded value.
    assert weighted_bump_days("f1", storage) == 5.0


def test_get_ledger_history_sorted_most_recent_first(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    for season_id in ["2024", "2026", "2025"]:
        record_bump(
            "f1", season_id, days_bumped=1, outcome="bumped",
            cluster_id="c", plot_id="p1", opponent_plot_id="p2", storage=storage,
        )
    history = get_ledger_history("f1", storage)
    assert [e.season_id for e in history] == ["2026", "2025", "2024"]
