"""Route proposal and operator override -- ADR-013. Covers:
route_override_status() derivation, Accept (records only), Modify/swap/
drop/Done (taps-only edit-in-place flow), the fairness-bump distinction
(only a confirmed drop bumps; a pure reorder never does), authorization,
and the four failure paths named in the ADR.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.fairness import get_ledger_history
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import HarvestConfirmation, RouteOverride
from harvest_convoy.telegram import webhook
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.watcher import route_override_status

SEASON = "2026-kuruvai"
TODAY = date(2026, 9, 9)
OPERATOR_CHAT_ID = 900


class _FakeClient:
    def __init__(self):
        self.sent_messages: list[tuple] = []
        self.answered_callbacks: list[tuple] = []
        self.edited_markups: list[tuple] = []
        self.edited_texts: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent_messages.append((chat_id, text, reply_markup))
        return SendResult(success=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered_callbacks.append((callback_query_id, text, show_alert))
        return SendResult(success=True)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markups.append((chat_id, message_id, reply_markup))
        return SendResult(success=True)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited_texts.append((chat_id, message_id, text, reply_markup))
        return SendResult(success=True)


def _cluster(*, operator_language: str = "en") -> Cluster:
    return Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
        operator_chat_id=OPERATOR_CHAT_ID, operator_language=operator_language,
    )


def _farmer(fid: str, chat_id: int, name: str) -> Farmer:
    return Farmer(farmer_id=fid, name=name, cluster_id="c1", telegram_chat_id=chat_id, language="en")


def _plot(pid: str, fid: str) -> Plot:
    return Plot(
        plot_id=pid, farmer_id=fid, cluster_id="c1",
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.0,
    )


def _seed(storage: FileStorage, plots: list[Plot], farmers: list[Farmer]) -> None:
    storage.put_cluster(_cluster())
    for f in farmers:
        storage.put_farmer(f)
    for p in plots:
        storage.put_plot(p)


def _override(
    proposed: list[str], *, current: list[str] | None = None,
    accepted_at: str | None = None, last_modified_at: str | None = None,
    last_notified_route: list[str] | None = None,
) -> RouteOverride:
    return RouteOverride(
        cluster_id="c1", season_id=SEASON, decision_date=TODAY.isoformat(),
        proposed_route=proposed, current_route=current if current is not None else list(proposed),
        proposed_at="2026-09-09T06:00:00+00:00",
        accepted_at=accepted_at, last_modified_at=last_modified_at,
        last_notified_route=last_notified_route,
    )


def _cbq(callback_id: str, data: str, from_id: int, *, chat_id: int = 999, message_id: int = 1) -> dict:
    return {
        "id": callback_id, "data": data,
        "message": {"chat": {"id": chat_id}, "message_id": message_id},
        "from": {"id": from_id},
    }


def _standard_seed(storage: FileStorage) -> tuple[Farmer, Farmer, Farmer]:
    farmers = [_farmer("f1", 101, "Farmer One"), _farmer("f2", 102, "Farmer Two"), _farmer("f3", 103, "Farmer Three")]
    plots = [_plot("p1", "f1"), _plot("p2", "f2"), _plot("p3", "f3")]
    _seed(storage, plots, farmers)
    return tuple(farmers)


# --- route_override_status() ---

def test_route_override_status_no_response_when_untouched() -> None:
    o = _override(["p1", "p2"])
    assert route_override_status(o) == "no_response"


def test_route_override_status_accepted_when_explicit_tap_recorded() -> None:
    o = _override(["p1", "p2"], accepted_at="2026-09-09T10:00:00+00:00")
    assert route_override_status(o) == "accepted"


def test_route_override_status_modified_regardless_of_accepted_at() -> None:
    o = _override(["p1", "p2"], current=["p2", "p1"], accepted_at="2026-09-09T10:00:00+00:00")
    assert route_override_status(o) == "modified"


# --- Accept ---

def test_accept_records_and_notifies_nobody(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    client = _FakeClient()

    webhook.handle_route_accept_callback(
        client, _cbq("cbq1", f"route_accept:c1:{SEASON}:{TODAY.isoformat()}", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.accepted_at is not None
    assert updated.current_route == ["p1", "p2", "p3"]
    assert client.sent_messages == []  # nothing withheld from farmers, nothing to notify


def test_accept_from_non_operator_is_refused_and_mutates_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    client = _FakeClient()

    webhook.handle_route_accept_callback(
        client, _cbq("cbq1", f"route_accept:c1:{SEASON}:{TODAY.isoformat()}", 12345),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.accepted_at is None
    assert client.sent_messages == []
    assert client.answered_callbacks[-1][2] is True  # show_alert refusal


def test_accept_with_no_from_field_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1"]))
    client = _FakeClient()
    cbq = {"id": "cbq1", "data": f"route_accept:c1:{SEASON}:{TODAY.isoformat()}", "message": {"chat": {"id": 999}, "message_id": 1}}

    webhook.handle_route_accept_callback(client, cbq, storage, today=TODAY)

    assert storage.get_route_override("c1", SEASON, TODAY.isoformat()).accepted_at is None


# --- Modify / swap ---

def test_modify_opens_the_edit_view(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    client = _FakeClient()

    webhook.handle_route_modify_callback(
        client, _cbq("cbq1", f"route_modify:c1:{SEASON}:{TODAY.isoformat()}", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    assert len(client.edited_texts) == 1
    _, _, text, reply_markup = client.edited_texts[0]
    assert "Farmer One" in text and "Farmer Two" in text and "Farmer Three" in text
    # 3 stop rows + 1 Done row
    assert len(reply_markup["inline_keyboard"]) == 4


def test_swap_reorders_and_writes_no_ledger_entry(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    f1, f2, _ = _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    client = _FakeClient()

    webhook.handle_route_swap_callback(
        client, _cbq("cbq1", f"route_swap:c1:{SEASON}:{TODAY.isoformat()}:2", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.current_route == ["p2", "p1", "p3"]
    assert updated.last_modified_at is not None
    # The actual "is this a bump" behavior: a pure reorder must never
    # write a LedgerEntry, regardless of how the position changed.
    assert get_ledger_history(f1.farmer_id, storage) == []
    assert get_ledger_history(f2.farmer_id, storage) == []


def test_swap_on_an_already_confirmed_plot_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id=SEASON,
        scheduled_date=TODAY.isoformat(), confirmed=True, confirmed_at="2026-09-09T19:00:00+00:00",
    ))
    client = _FakeClient()

    webhook.handle_route_swap_callback(
        client, _cbq("cbq1", f"route_swap:c1:{SEASON}:{TODAY.isoformat()}:2", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.current_route == ["p1", "p2", "p3"]  # unchanged


# --- Drop / drop-confirm ---

def test_drop_shows_confirmation_and_mutates_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    client = _FakeClient()

    webhook.handle_route_drop_callback(
        client, _cbq("cbq1", f"route_drop:c1:{SEASON}:{TODAY.isoformat()}:p2", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.current_route == ["p1", "p2", "p3"]  # not dropped yet
    _, _, text, reply_markup = client.edited_texts[0]
    assert "Farmer Two" in text
    assert "route_drop_confirm:c1" in reply_markup["inline_keyboard"][0][0]["callback_data"]


def test_drop_of_the_last_plot_gets_a_warning_in_the_confirm_prompt(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1"]))
    client = _FakeClient()

    webhook.handle_route_drop_callback(
        client, _cbq("cbq1", f"route_drop:c1:{SEASON}:{TODAY.isoformat()}:p1", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    _, _, text, _ = client.edited_texts[0]
    assert "nobody scheduled" in text.lower()


def test_drop_confirm_yes_un_harvests_cancels_confirmation_bumps_and_notifies_immediately(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    f1, f2, _ = _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    storage.mark_plot_harvested("p2", "c1", SEASON, dispatched_at="2026-09-09T06:00:00+00:00")
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p2", farmer_id="f2", cluster_id="c1", season_id=SEASON,
        scheduled_date=TODAY.isoformat(),
    ))
    client = _FakeClient()

    webhook.handle_route_drop_confirm_callback(
        client,
        _cbq("cbq1", f"route_drop_confirm:c1:{SEASON}:{TODAY.isoformat()}:p2:yes", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.current_route == ["p1", "p3"]
    assert "p2" not in storage.get_harvested_plot_ids("c1", SEASON)  # reversal hook reused

    confirmation = storage.get_harvest_confirmation("p2", SEASON)
    assert confirmation.cancelled is True
    assert confirmation.cancellation_reason == "operator_override"

    history = get_ledger_history(f2.farmer_id, storage)
    assert len(history) == 1
    assert history[0].outcome == "operator_override"
    assert history[0].decided_by == "operator_override"
    assert history[0].opponent_plot_id is None
    assert history[0].days_bumped == webhook.DEFAULT_DAYS_BUMPED

    # Immediate notification -- not batched behind a Done tap.
    farmer_2_messages = [m for m in client.sent_messages if m[0] == 102]
    assert len(farmer_2_messages) == 1
    assert "won't be coming" in farmer_2_messages[0][1]

    # The other two farmers are untouched.
    assert get_ledger_history(f1.farmer_id, storage) == []


def test_drop_confirm_no_returns_to_edit_view_unchanged(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    client = _FakeClient()

    webhook.handle_route_drop_confirm_callback(
        client,
        _cbq("cbq1", f"route_drop_confirm:c1:{SEASON}:{TODAY.isoformat()}:p2:no", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.current_route == ["p1", "p2", "p3"]
    assert client.sent_messages == []


def test_drop_confirm_on_an_already_confirmed_plot_is_refused_and_mutates_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    f1, f2, _ = _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p2", farmer_id="f2", cluster_id="c1", season_id=SEASON,
        scheduled_date=TODAY.isoformat(), confirmed=True, confirmed_at="2026-09-09T19:00:00+00:00",
    ))
    client = _FakeClient()

    webhook.handle_route_drop_confirm_callback(
        client,
        _cbq("cbq1", f"route_drop_confirm:c1:{SEASON}:{TODAY.isoformat()}:p2:yes", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.current_route == ["p1", "p2", "p3"]
    assert get_ledger_history(f2.farmer_id, storage) == []
    assert client.sent_messages == []


def test_drop_confirm_from_non_operator_is_refused_and_mutates_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    f1, f2, _ = _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    storage.mark_plot_harvested("p2", "c1", SEASON, dispatched_at="2026-09-09T06:00:00+00:00")
    client = _FakeClient()

    webhook.handle_route_drop_confirm_callback(
        client,
        _cbq("cbq1", f"route_drop_confirm:c1:{SEASON}:{TODAY.isoformat()}:p2:yes", 12345),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.current_route == ["p1", "p2", "p3"]
    assert "p2" in storage.get_harvested_plot_ids("c1", SEASON)
    assert get_ledger_history(f2.farmer_id, storage) == []
    assert client.sent_messages == []


# --- Done: batched reorder notifications ---

def test_done_notifies_only_farmers_whose_position_changed(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    # p1 and p2 swapped relative to the original proposal; p3 unchanged.
    storage.put_route_override(_override(["p1", "p2", "p3"], current=["p2", "p1", "p3"]))
    client = _FakeClient()

    webhook.handle_route_done_callback(
        client, _cbq("cbq1", f"route_done:c1:{SEASON}:{TODAY.isoformat()}", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    notified_chat_ids = {m[0] for m in client.sent_messages}
    assert notified_chat_ids == {101, 102}  # f1, f2 -- not f3

    updated = storage.get_route_override("c1", SEASON, TODAY.isoformat())
    assert updated.last_notified_route == ["p2", "p1", "p3"]


def test_second_done_tap_with_no_further_changes_renotifies_nobody(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(
        ["p1", "p2", "p3"], current=["p2", "p1", "p3"], last_notified_route=["p2", "p1", "p3"],
    ))
    client = _FakeClient()

    webhook.handle_route_done_callback(
        client, _cbq("cbq1", f"route_done:c1:{SEASON}:{TODAY.isoformat()}", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    assert client.sent_messages == []


# --- Failure paths ---

def test_stale_route_from_a_previous_day_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    yesterday = date(2026, 9, 8)
    storage.put_route_override(RouteOverride(
        cluster_id="c1", season_id=SEASON, decision_date=yesterday.isoformat(),
        proposed_route=["p1"], current_route=["p1"], proposed_at="2026-09-08T06:00:00+00:00",
    ))
    client = _FakeClient()

    webhook.handle_route_accept_callback(
        client, _cbq("cbq1", f"route_accept:c1:{SEASON}:{yesterday.isoformat()}", OPERATOR_CHAT_ID),
        storage, today=TODAY,
    )

    updated = storage.get_route_override("c1", SEASON, yesterday.isoformat())
    assert updated.accepted_at is None
    assert client.answered_callbacks[-1][2] is True


def test_wrong_operator_swap_tap_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _standard_seed(storage)
    storage.put_route_override(_override(["p1", "p2", "p3"]))
    client = _FakeClient()

    webhook.handle_route_swap_callback(
        client, _cbq("cbq1", f"route_swap:c1:{SEASON}:{TODAY.isoformat()}:2", 55555),
        storage, today=TODAY,
    )

    assert storage.get_route_override("c1", SEASON, TODAY.isoformat()).current_route == ["p1", "p2", "p3"]
