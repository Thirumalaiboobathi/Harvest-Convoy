"""ADR-013 Part 2 Decision 18: /linkfarmer. Fully stateless and taps
only -- every id the next step needs travels in callback_data, so there
is no in-memory store to test for restart-survival (there is nothing to
lose). Mirrors test_route_override.py's testing shape for a multi-tap
operator flow.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram import messages_en, proxy_registration, webhook
from harvest_convoy.telegram.client import SendResult


def _cluster(operator_chat_id: int = 999) -> Cluster:
    return Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
        operator_chat_id=operator_chat_id, operator_language="en",
    )


def _proxy_farmer(farmer_id="farmer-proxy-abc123") -> Farmer:
    return Farmer(farmer_id=farmer_id, name="Muthu Pandian", cluster_id="c1", telegram_chat_id=None)


def _proxy_plot(farmer_id="farmer-proxy-abc123", plot_id="plot-proxy-abc123") -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id="c1",
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.5,
        registered_by="operator:999", registered_at="2026-01-01T00:00:00+00:00",
    )


def _self_farmer(chat_id=555, farmer_id="farmer-555") -> Farmer:
    return Farmer(farmer_id=farmer_id, name="Muthu P.", cluster_id="c1", telegram_chat_id=chat_id)


def _self_plot(farmer_id="farmer-555", plot_id="plot-555") -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id="c1",
        lat=9.871, lon=77.461, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 2), area_acres=2.6,
        registered_by="self", registered_at="2026-06-01T00:00:00+00:00",
    )


class _FakeClient:
    def __init__(self):
        self.sent: list[tuple] = []
        self.answered: list[tuple] = []
        self.edited_markup: list[tuple] = []
        self.edited_text: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SendResult(success=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered.append((callback_query_id, text, show_alert))
        return SendResult(success=True)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markup.append((chat_id, message_id, reply_markup))
        return SendResult(success=True)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited_text.append((chat_id, message_id, text, reply_markup))
        return SendResult(success=True)


def _seed_pair(storage) -> None:
    storage.put_cluster(_cluster())
    storage.put_farmer(_proxy_farmer())
    storage.put_plot(_proxy_plot())
    storage.put_farmer(_self_farmer())
    storage.put_plot(_self_plot())


def _cbq(cbq_id: str, data: str, tapper_id: int) -> dict:
    return {
        "id": cbq_id, "data": data,
        "message": {"chat": {"id": 999}, "message_id": 1},
        "from": {"id": tapper_id},
    }


# ---------------------------------------------------------------------
# /linkfarmer command
# ---------------------------------------------------------------------

def test_linkfarmer_with_nothing_unlinked_replies_plainly(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    client = _FakeClient()

    webhook.handle_update(
        client,
        {"message": {"chat": {"id": 999}, "text": "/linkfarmer", "from": {"id": 999}}},
        storage, "2026-kuruvai",
    )

    assert client.sent == [(999, messages_en.linkfarmer_nothing_to_link(), None)]


def test_linkfarmer_lists_unlinked_proxy_farmers_as_buttons(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    webhook.handle_update(
        client,
        {"message": {"chat": {"id": 999}, "text": "/linkfarmer", "from": {"id": 999}}},
        storage, "2026-kuruvai",
    )

    assert len(client.sent) == 1
    _cid, _text, reply_markup = client.sent[0]
    buttons = reply_markup["inline_keyboard"]
    assert buttons == [[{"text": "Muthu Pandian", "callback_data": "linkfarmer_proxy:farmer-proxy-abc123"}]]


def test_linkfarmer_from_non_operator_is_refused_and_starts_no_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    webhook.handle_update(
        client,
        {"message": {"chat": {"id": 12345}, "text": "/linkfarmer", "from": {"id": 12345}}},
        storage, "2026-kuruvai",
    )

    # Refused generically (Tamil default -- no cluster/operator language
    # is resolvable for an identity that isn't this cluster's operator).
    assert len(client.sent) == 1
    assert client.sent[0][0] == 12345
    # Nothing about the real candidates was ever sent to the impostor.
    assert "Muthu Pandian" not in client.sent[0][1]


# ---------------------------------------------------------------------
# Full three-tap happy path
# ---------------------------------------------------------------------

def test_linkfarmer_happy_path_transplants_chat_id_and_retires_the_duplicate_plot(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    webhook.handle_linkfarmer_proxy_callback(
        client, _cbq("cb1", "linkfarmer_proxy:farmer-proxy-abc123", 999), storage,
    )
    assert client.edited_text[-1][2] == messages_en.linkfarmer_pick_match_prompt("Muthu Pandian")
    match_buttons = client.edited_text[-1][3]["inline_keyboard"]
    assert match_buttons == [[{"text": "Muthu P.", "callback_data": "linkfarmer_match:farmer-proxy-abc123:farmer-555"}]]

    webhook.handle_linkfarmer_match_callback(
        client, _cbq("cb2", "linkfarmer_match:farmer-proxy-abc123:farmer-555", 999), storage,
    )
    confirm_text = client.edited_text[-1][2]
    assert "Muthu Pandian" in confirm_text and "Muthu P." in confirm_text

    webhook.handle_linkfarmer_confirm_callback(
        client, _cbq("cb3", "linkfarmer_confirm:farmer-proxy-abc123:farmer-555:yes", 999), storage,
    )

    proxy_farmer = storage.get_farmer("farmer-proxy-abc123")
    duplicate_farmer = storage.get_farmer("farmer-555")
    duplicate_plot = storage.get_plot("plot-555")
    proxy_plot = storage.get_plot("plot-proxy-abc123")

    assert proxy_farmer.telegram_chat_id == 555
    assert duplicate_farmer.telegram_chat_id is None  # cleared, not left dangling
    assert duplicate_plot.retired_reason == "linked_to:farmer-proxy-abc123"
    # The proxy's own plot details are untouched -- no reconciliation.
    assert proxy_plot.area_acres == 2.5
    assert proxy_plot.transplant_date == date(2026, 5, 1)
    assert client.answered[-1][1] == messages_en.linkfarmer_linked_toast()


def test_linkfarmer_confirm_no_changes_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    webhook.handle_linkfarmer_confirm_callback(
        client, _cbq("cb1", "linkfarmer_confirm:farmer-proxy-abc123:farmer-555:no", 999), storage,
    )

    assert storage.get_farmer("farmer-proxy-abc123").telegram_chat_id is None
    assert storage.get_farmer("farmer-555").telegram_chat_id == 555
    assert storage.get_plot("plot-555").retired_reason is None
    assert client.answered[-1][1] == messages_en.linkfarmer_cancelled_toast()


def test_a_linked_proxy_farmer_no_longer_appears_in_a_second_linkfarmer_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    ok = proxy_registration.apply_link(storage, "farmer-proxy-abc123", "farmer-555")
    assert ok is True

    webhook.handle_update(
        client,
        {"message": {"chat": {"id": 999}, "text": "/linkfarmer", "from": {"id": 999}}},
        storage, "2026-kuruvai",
    )

    assert client.sent == [(999, messages_en.linkfarmer_nothing_to_link(), None)]


# ---------------------------------------------------------------------
# Authorization -- non-operator taps at every step, proven to mutate nothing
# ---------------------------------------------------------------------

def test_linkfarmer_proxy_from_non_operator_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    webhook.handle_linkfarmer_proxy_callback(
        client, _cbq("cb1", "linkfarmer_proxy:farmer-proxy-abc123", 54321), storage,
    )

    assert client.edited_text == []
    assert storage.get_farmer("farmer-proxy-abc123").telegram_chat_id is None


def test_linkfarmer_match_from_non_operator_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    webhook.handle_linkfarmer_match_callback(
        client, _cbq("cb1", "linkfarmer_match:farmer-proxy-abc123:farmer-555", 54321), storage,
    )

    assert client.edited_text == []


def test_linkfarmer_confirm_from_non_operator_is_refused_and_links_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_pair(storage)
    client = _FakeClient()

    webhook.handle_linkfarmer_confirm_callback(
        client, _cbq("cb1", "linkfarmer_confirm:farmer-proxy-abc123:farmer-555:yes", 54321), storage,
    )

    assert storage.get_farmer("farmer-proxy-abc123").telegram_chat_id is None
    assert storage.get_farmer("farmer-555").telegram_chat_id == 555
    assert storage.get_plot("plot-555").retired_reason is None
    assert client.edited_markup == []
