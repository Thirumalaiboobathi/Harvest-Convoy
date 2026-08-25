"""ADR-014: /help, a read-only status command for a registered farmer or
the cluster's operator. Hermetic -- FileStorage only, no live calls, no
Bedrock; every answer is a direct read of already-stored records.
"""

from __future__ import annotations

from datetime import date

import pytest

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import AdvanceNoticeRecord
from harvest_convoy.telegram import help as help_command
from harvest_convoy.telegram import messages_en, messages_ta, webhook
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.telegram.registration import RegistrationState, save_state

SEASON = "2026-kuruvai"
CLUSTER_ID = "help-cluster"
OPERATOR_CHAT_ID = 900
FARMER_CHAT_ID = 501
IMPOSTER_CHAT_ID = 66666


class _FakeClient:
    def __init__(self):
        self.sent_messages: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent_messages.append((chat_id, text, reply_markup))
        return SendResult(success=True)


def _cluster(*, operator_chat_id: int | None = OPERATOR_CHAT_ID, operator_language: str = "en") -> Cluster:
    return Cluster(
        cluster_id=CLUSTER_ID, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
        operator_chat_id=operator_chat_id, operator_language=operator_language,
    )


def _farmer(fid: str = "f1", chat_id: int | None = FARMER_CHAT_ID, language: str = "en") -> Farmer:
    return Farmer(farmer_id=fid, name="Real Farmer", cluster_id=CLUSTER_ID, telegram_chat_id=chat_id, language=language)


def _plot(pid: str = "p1", fid: str = "f1", *, village: str | None = "Kamatchipuram", retired: bool = False) -> Plot:
    return Plot(
        plot_id=pid, farmer_id=fid, cluster_id=CLUSTER_ID,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 12), area_acres=2.5,
        village=village,
        retired_reason=("linked_to:someone" if retired else None),
    )


def _advance_notice(plot_id: str = "p1", fid: str = "f1", *, projected_date: str = "2026-09-09") -> AdvanceNoticeRecord:
    return AdvanceNoticeRecord(
        plot_id=plot_id, farmer_id=fid, cluster_id=CLUSTER_ID, season_id=SEASON,
        sent_at="2026-09-02T09:00:00+00:00", projected_maturity_date=projected_date,
    )


# --- build_help_reply ---

def test_farmer_reply_with_advance_notice_shows_the_real_date(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot())
    storage.put_advance_notice_record(_advance_notice())

    text = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert "Kamatchipuram" in text
    assert "2.5 acres" in text
    assert "12 May 2026" in text
    assert "expected around 9 September 2026" in text


def test_farmer_reply_without_advance_notice_names_the_week_before_schedule(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot())
    # No AdvanceNoticeRecord seeded.

    text = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert "not available yet" in text
    assert "about a week before your plot is ready" in text


def test_farmer_reply_with_no_village_says_so_honestly(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot(village=None))

    text = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert "village not recorded" in text
    assert "None" not in text


def test_operator_reply_when_operator_has_no_farmer_record(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_language="ta"))

    text = help_command.build_help_reply(storage, CLUSTER_ID, OPERATOR_CHAT_ID, SEASON)

    assert text == messages_ta.help_operator_reply("Test Cluster")


def test_operator_who_also_farms_sees_his_own_plot_first(tmp_path) -> None:
    """ADR-014 Decision 2, reversed on review: the farmer view must win
    even when the same chat_id is also the operator -- his own crop must
    not be unreachable through his own status command."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=FARMER_CHAT_ID))
    storage.put_farmer(_farmer(chat_id=FARMER_CHAT_ID))
    storage.put_plot(_plot())

    text = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert "Kamatchipuram" in text  # the farmer reply, not skipped
    assert "You're also the operator for Test Cluster" in text
    assert "/addfarmer" in text
    # Not the standalone operator reply -- that never mentions his plot.
    assert "You're the operator for" not in text


def test_unregistered_sender_gets_a_bilingual_hint(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())

    text = help_command.build_help_reply(storage, CLUSTER_ID, IMPOSTER_CHAT_ID, SEASON)

    assert messages_en.help_unregistered() in text
    assert messages_ta.help_unregistered() in text


def test_farmer_with_no_active_plot_gets_an_honest_answer_not_a_crash(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    # No plot seeded at all.

    text = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert text == messages_en.help_no_plot_found()


def test_farmer_whose_only_plot_is_retired_gets_no_plot_found(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot(retired=True))

    text = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert text == messages_en.help_no_plot_found()


def test_farmer_with_two_active_plots_picks_the_lower_plot_id_deterministically(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot(pid="p2", village="Second Village"))
    storage.put_plot(_plot(pid="p1", village="First Village"))

    first = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)
    second = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert "First Village" in first
    assert first == second


def test_missing_cluster_env_degrades_to_help_unavailable(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")

    assert help_command.build_help_reply(storage, None, FARMER_CHAT_ID, SEASON) == messages_ta.help_unavailable()


def test_unresolvable_cluster_id_degrades_to_help_unavailable(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    # No Cluster seeded for CLUSTER_ID at all.

    text = help_command.build_help_reply(storage, CLUSTER_ID, FARMER_CHAT_ID, SEASON)

    assert text == messages_ta.help_unavailable()


# --- webhook.handle_update dispatch ---

def _help_update(chat_id: int, text: str = "/help") -> dict:
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_help_command_dispatches_and_is_case_insensitive(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", CLUSTER_ID)
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot())
    client = _FakeClient()

    webhook.handle_update(client, _help_update(FARMER_CHAT_ID, "/HELP"), storage, SEASON)

    assert len(client.sent_messages) == 1
    assert client.sent_messages[0][0] == FARMER_CHAT_ID
    assert "Kamatchipuram" in client.sent_messages[0][1]


def test_help_mid_registration_leaves_pending_state_untouched(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", CLUSTER_ID)
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    pending_chat_id = 12345
    save_state(RegistrationState(chat_id=pending_chat_id, village="Somewhere"))
    client = _FakeClient()

    webhook.handle_update(client, _help_update(pending_chat_id), storage, SEASON)

    assert len(client.sent_messages) == 1
    from harvest_convoy.telegram.registration import get_or_create_state
    state_after = get_or_create_state(pending_chat_id)
    assert state_after.village == "Somewhere"
