"""registration.py tests. See ADR-008 Part 2 for the Tamil-support
design this covers: bilingual message 1 (greeting + language buttons,
folded into the existing four-message budget rather than adding a
fifth), script-based language inference, a real crop-confirm yes/no
matcher (previously accepted any non-empty reply -- a pre-existing bug,
fixed here independent of language), and Tamil-script/Tanglish parsing
at every free-text step.
"""

from datetime import date

import pytest

from harvest_convoy.telegram import registration
from harvest_convoy.telegram.registration import (
    RegistrationState,
    RegistrationStep,
    IncomingMessage,
    advance_registration,
    get_or_create_state,
    handle_incoming,
    save_state,
)


# ---------------------------------------------------------------------
# Message 1: bilingual greeting + language buttons (ADR-008 Decision 7)
# ---------------------------------------------------------------------

def test_first_message_greets_bilingually_with_language_buttons_and_does_not_advance() -> None:
    state = RegistrationState(chat_id=1)
    new_state, reply = advance_registration(state, IncomingMessage(text="anything"))

    assert new_state.greeted is True
    assert new_state.step == RegistrationStep.AWAITING_VILLAGE  # still waiting for the real reply
    assert "தமிழ்" in reply.text and "English" in reply.text
    assert reply.reply_markup == registration.LANGUAGE_KEYBOARD


def test_language_button_tap_sets_language_without_a_chat_message() -> None:
    class _FakeClient:
        def __init__(self):
            self.acks = []

        def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
            self.acks.append((callback_query_id, text, show_alert))

    save_state(RegistrationState(chat_id=42))
    client = _FakeClient()
    callback_query = {
        "id": "cb1", "data": "lang:en",
        "message": {"chat": {"id": 42}},
        "from": {"id": 42},
    }

    registration.handle_language_callback(client, callback_query)

    state = get_or_create_state(42)
    assert state.language == "en"
    assert state.greeted is True
    assert client.acks == [("cb1", "English selected.", False)]


def test_language_tap_from_a_different_chat_is_refused_and_does_not_set_language() -> None:
    """ADR-012: a lang: tap must come from the same identity the keyboard
    was sent to. A stray tap in a group chat, or a forwarded keyboard,
    must not let a second person set this chat's language."""
    class _FakeClient:
        def __init__(self):
            self.acks = []

        def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
            self.acks.append((callback_query_id, text, show_alert))

    save_state(RegistrationState(chat_id=43))
    client = _FakeClient()
    callback_query = {
        "id": "cb2", "data": "lang:en",
        "message": {"chat": {"id": 43}},
        "from": {"id": 99999},
    }

    registration.handle_language_callback(client, callback_query)

    state = get_or_create_state(43)
    assert state.language == "ta"  # unchanged default -- the "en" tap must not apply
    assert state.greeted is False


def test_language_tap_with_no_from_field_is_refused() -> None:
    class _FakeClient:
        def __init__(self):
            self.acks = []

        def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
            self.acks.append((callback_query_id, text, show_alert))

    save_state(RegistrationState(chat_id=44))
    client = _FakeClient()
    callback_query = {
        "id": "cb3", "data": "lang:en",
        "message": {"chat": {"id": 44}},
    }

    registration.handle_language_callback(client, callback_query)

    state = get_or_create_state(44)
    assert state.language == "ta"  # unchanged default -- no from field means refused


def test_full_happy_path_reaches_complete() -> None:
    """Starts past the greeting (greeted=True) -- the greeting itself is
    covered above; this tests the four real farmer inputs."""
    state = RegistrationState(chat_id=1, greeted=True)

    state, reply = advance_registration(state, IncomingMessage(text="Kamatchipuram"))
    assert state.step == RegistrationStep.AWAITING_LOCATION
    assert state.village == "Kamatchipuram"

    state, reply = advance_registration(state, IncomingMessage(location=(9.865, 77.454)))
    assert state.step == RegistrationStep.AWAITING_CROP_CONFIRM
    assert state.lat == 9.865 and state.lon == 77.454

    state, reply = advance_registration(state, IncomingMessage(text="yes"))
    assert state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO

    state, reply = advance_registration(
        state, IncomingMessage(text="18 May 2026, 2.5 acres")
    )
    assert state.step == RegistrationStep.COMPLETE
    assert state.transplant_date == date(2026, 5, 18)
    assert state.area_acres == 2.5
    assert state.area_unit == "acre"


# ---------------------------------------------------------------------
# Language inference from village-name script (ADR-008 Decision 7)
# ---------------------------------------------------------------------

def test_village_reply_in_tamil_script_sets_language_to_tamil() -> None:
    """A farmer who never tapped the English button but replies in Tamil
    script gets an explicit Tamil confirmation (not just the untouched
    default) -- proven here starting from language="en" so a flip back
    to "ta" is only possible if the script really was detected."""
    state = RegistrationState(chat_id=1, greeted=True, language="en")
    new_state, reply = advance_registration(state, IncomingMessage(text="கமட்சிபுரம்"))
    assert new_state.language == "ta"
    assert new_state.village == "கமட்சிபுரம்"
    assert new_state.step == RegistrationStep.AWAITING_LOCATION


def test_village_reply_in_latin_script_does_not_auto_switch_to_english() -> None:
    """Latin script is ambiguous with Tanglish -- only an explicit button
    tap sets language="en". A farmer who never tapped and just types a
    village name in Latin letters stays on the default, Tamil."""
    state = RegistrationState(chat_id=1, greeted=True)  # language defaults to "ta"
    new_state, reply = advance_registration(state, IncomingMessage(text="Kamatchipuram"))
    assert new_state.language == "ta"


def test_village_reply_after_explicit_english_tap_stays_english() -> None:
    state = RegistrationState(chat_id=1, greeted=True, language="en")
    new_state, reply = advance_registration(state, IncomingMessage(text="Kamatchipuram"))
    assert new_state.language == "en"
    assert "location" in reply.text.lower() or "share" in reply.text.lower()


# ---------------------------------------------------------------------
# Existing edge-case coverage, updated for greeted=True starting states
# ---------------------------------------------------------------------

def test_empty_village_reply_reprompts_without_advancing() -> None:
    state = RegistrationState(chat_id=1, greeted=True)
    new_state, reply = advance_registration(state, IncomingMessage(text="   "))
    assert new_state.step == RegistrationStep.AWAITING_VILLAGE
    assert new_state == state


def test_text_instead_of_location_reprompts() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_LOCATION, village="V", language="en"
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="near the temple"))
    assert new_state.step == RegistrationStep.AWAITING_LOCATION
    assert "didn't look like" in reply.text


def test_unparseable_transplant_info_reprompts_and_names_whats_missing() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.AWAITING_TRANSPLANT_INFO,
        village="V", lat=1.0, lon=1.0, language="en",
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="soon-ish"))
    assert new_state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO
    assert "a date" in reply.text and "an area" in reply.text


def test_date_only_missing_area_reprompts_naming_only_area() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.AWAITING_TRANSPLANT_INFO,
        village="V", lat=1.0, lon=1.0, language="en",
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="18 May 2026"))
    assert new_state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO
    assert "an area" in reply.text
    assert "a date" not in reply.text.split("an area")[0]  # "a date" shouldn't be in the missing list


def test_iso_date_format_parses() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.AWAITING_TRANSPLANT_INFO,
        village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="2026-05-18, 3 acres"))
    assert new_state.transplant_date == date(2026, 5, 18)
    assert new_state.area_acres == 3.0
    assert new_state.area_unit == "acre"


def test_slash_date_format_parses() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.AWAITING_TRANSPLANT_INFO,
        village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="18/05/2026 - 1.25 ac"))
    assert new_state.transplant_date == date(2026, 5, 18)
    assert new_state.area_acres == 1.25


def test_complete_state_is_stable_on_further_messages() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.COMPLETE,
        village="V", lat=1.0, lon=1.0,
        transplant_date=date(2026, 5, 18), area_acres=2.0,
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="hello again"))
    assert new_state == state


def test_abandon_partway_and_return_later_resumes_from_saved_step() -> None:
    chat_id = 12345
    save_state(RegistrationState(chat_id=chat_id, greeted=True))  # ensure clean slate, past greeting

    reply1 = handle_incoming(chat_id, IncomingMessage(text="Kamatchipuram"))
    assert "location" in reply1.text.lower() or "share" in reply1.text.lower()

    # ... farmer goes quiet for a while ...

    state = get_or_create_state(chat_id)
    assert state.step == RegistrationStep.AWAITING_LOCATION
    assert state.village == "Kamatchipuram"

    reply2 = handle_incoming(chat_id, IncomingMessage(location=(9.865, 77.454)))
    state = get_or_create_state(chat_id)
    assert state.step == RegistrationStep.AWAITING_CROP_CONFIRM
    assert state.village == "Kamatchipuram"  # earlier answer preserved


# ---------------------------------------------------------------------
# Crop-confirm yes/no matcher (ADR-008 Decision 11) -- confirmed bug fix,
# independent of language: the old code accepted ANY non-empty reply.
# ---------------------------------------------------------------------

def test_crop_confirm_rejects_an_unrelated_reply() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_CROP_CONFIRM, village="V", lat=1.0, lon=1.0,
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="what do you mean"))
    assert new_state.step == RegistrationStep.AWAITING_CROP_CONFIRM  # did NOT advance


def test_crop_confirm_rejects_empty_reply() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_CROP_CONFIRM, village="V", lat=1.0, lon=1.0,
    )
    new_state, reply = advance_registration(state, IncomingMessage(text=""))
    assert new_state.step == RegistrationStep.AWAITING_CROP_CONFIRM


@pytest.mark.parametrize(
    "text",
    [
        "yes", "Yes", "Y", "ok", "OKAY", "ok ok", "done", "confirm",
        "ஆம்", "ஆமாம்", "ஆமா", "சரி", "சரிங்க", "ஓகே", "ஆகட்டும்",
        "aam", "seri", "sari", "seringa",
    ],
)
def test_crop_confirm_accepts_english_tamil_and_tanglish_yes(text: str) -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_CROP_CONFIRM, village="V", lat=1.0, lon=1.0,
    )
    new_state, reply = advance_registration(state, IncomingMessage(text=text))
    assert new_state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO


# ---------------------------------------------------------------------
# Crop-confirm "no" path (ADR-008 Decision 11 revision) -- previously
# there was no way to decline at all; any reply that wasn't a real "no"
# either fell through to the generic re-prompt (a dead end for a farmer
# with a non-paddy plot) or, before the yes/no matcher existed, was
# silently treated as confirmation.
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "text", ["no", "N", "not", "no no", "இல்லை", "வேண்டாம்", "illai", "vendam", "venam"]
)
def test_crop_confirm_accepts_english_tamil_and_tanglish_no(text: str) -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_CROP_CONFIRM, village="V", lat=1.0, lon=1.0,
        language="en",
    )
    new_state, reply = advance_registration(state, IncomingMessage(text=text))
    assert new_state.step == RegistrationStep.CROP_CONFIRM_DECLINED
    assert "paddy" in reply.text.lower()


def test_crop_confirm_declined_state_is_stable_on_further_messages() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.CROP_CONFIRM_DECLINED, village="V", lat=1.0, lon=1.0,
        language="en",
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="hello again"))
    assert new_state == state
    assert "paddy" in reply.text.lower()


def test_crop_confirm_declined_message_is_in_tamil_for_a_tamil_farmer() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_CROP_CONFIRM, village="V", lat=1.0, lon=1.0,
        language="ta",
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="இல்லை"))
    assert new_state.step == RegistrationStep.CROP_CONFIRM_DECLINED
    assert "நெல்" in reply.text  # "paddy", in Tamil


# ---------------------------------------------------------------------
# Tamil-script / Tanglish transplant-info parsing (ADR-008 Decisions 9, 10, 11)
# ---------------------------------------------------------------------

def test_tamil_month_name_parses() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_TRANSPLANT_INFO, village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="18 மே 2026, 2.5 ஏக்கர்"))
    assert new_state.transplant_date == date(2026, 5, 18)
    assert new_state.area_acres == 2.5
    assert new_state.area_unit == "acre"


def test_tanglish_month_name_parses() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_TRANSPLANT_INFO, village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="18 mei 2026, 2.5 acres"))
    assert new_state.transplant_date == date(2026, 5, 18)


def test_cent_unit_tamil_script_converts_to_canonical_acres() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_TRANSPLANT_INFO, village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="18 May 2026, 250 சென்ட்"))
    assert new_state.area_acres == 2.5  # 250 cents == 2.5 acres
    assert new_state.area_unit == "cent"  # displayed back in the unit the farmer used


def test_cent_unit_tanglish_converts_to_canonical_acres() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_TRANSPLANT_INFO, village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="18 May 2026, 50 cents"))
    assert new_state.area_acres == 0.5
    assert new_state.area_unit == "cent"


def test_acre_unit_tanglish_word_ekar() -> None:
    state = RegistrationState(
        chat_id=1, step=RegistrationStep.AWAITING_TRANSPLANT_INFO, village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="18 May 2026, 2 ekar"))
    assert new_state.area_acres == 2.0
    assert new_state.area_unit == "acre"


# ---------------------------------------------------------------------
# Persistence prerequisite + maturity projection (ADR-009 Part 3)
# ---------------------------------------------------------------------

from harvest_convoy.models import Cluster  # noqa: E402
from harvest_convoy.storage.file_storage import FileStorage  # noqa: E402
from harvest_convoy.weather.openmeteo import WeatherError  # noqa: E402


def _cluster(cluster_id="c1", maturity_gdd_override=None) -> Cluster:
    return Cluster(
        cluster_id=cluster_id, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
        maturity_gdd_override=maturity_gdd_override,
    )


def _complete_registration(
    chat_id: int, storage, *, transplant_text: str, sender_name: str = "Test Farmer",
) -> "registration.OutboundMessage":
    handle_incoming(chat_id, IncomingMessage(text="hi", sender_name=sender_name), storage)
    handle_incoming(chat_id, IncomingMessage(text="Kamatchipuram", sender_name=sender_name), storage)
    handle_incoming(chat_id, IncomingMessage(location=(9.87, 77.46), sender_name=sender_name), storage)
    handle_incoming(chat_id, IncomingMessage(text="yes", sender_name=sender_name), storage)
    return handle_incoming(
        chat_id, IncomingMessage(text=transplant_text, sender_name=sender_name), storage
    )


def test_registration_completion_persists_farmer_and_plot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    monkeypatch.setattr(
        registration, "project_maturity_for_plot", lambda plot, cluster: "2026-08-20"
    )

    _complete_registration(90001, storage, transplant_text="18 May 2026, 2.5 acres")

    farmer = storage.get_farmer("farmer-90001")
    plot = storage.get_plot("plot-90001")
    assert farmer is not None and farmer.name == "Test Farmer" and farmer.cluster_id == "c1"
    assert farmer.telegram_chat_id == 90001
    assert plot is not None
    assert plot.farmer_id == "farmer-90001"
    assert plot.transplant_date == date(2026, 5, 18)
    assert plot.area_acres == 2.5
    # ADR-013 Part 2: village is now persisted (previously asked and
    # discarded), and provenance is recorded even for self-registration.
    assert plot.village == "Kamatchipuram"
    assert plot.registered_by == "self"
    assert plot.registered_at is not None


def test_registration_completion_uses_telegram_sender_name(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    monkeypatch.setattr(
        registration, "project_maturity_for_plot", lambda plot, cluster: "2026-08-20"
    )

    _complete_registration(90002, storage, transplant_text="18 May 2026, 2.5 acres", sender_name="Muthu Pandian")

    assert storage.get_farmer("farmer-90002").name == "Muthu Pandian"


def test_registration_completion_appends_maturity_sentence_when_weather_available(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    monkeypatch.setattr(
        registration, "project_maturity_for_plot", lambda plot, cluster: "2026-08-20"
    )

    outbound = _complete_registration(90003, storage, transplant_text="18 May 2026, 2.5 acres")

    assert registration.messages_ta.COMPLETE_MESSAGE in outbound.text
    assert "20 ஆகஸ்ட் 2026" in outbound.text  # Tamil default, day-first format


def test_registration_completion_falls_back_to_plain_message_on_weather_error(
    monkeypatch, tmp_path
) -> None:
    """The required negative test: registration still completes, the
    farmer/plot still get persisted, only the projection sentence is
    omitted."""
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())

    def boom(plot, cluster):
        raise WeatherError("simulated Open-Meteo outage")

    monkeypatch.setattr(registration, "project_maturity_for_plot", boom)

    outbound = _complete_registration(90004, storage, transplant_text="18 May 2026, 2.5 acres")

    assert outbound.text == registration.messages_ta.COMPLETE_MESSAGE
    # Registration still completed and persisted -- weather failure never blocks it.
    assert storage.get_farmer("farmer-90004") is not None
    assert storage.get_plot("plot-90004") is not None
    assert get_or_create_state(90004).step == RegistrationStep.COMPLETE


def test_registration_completion_without_cluster_id_env_var_still_completes(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("HARVEST_CONVOY_CLUSTER_ID", raising=False)
    storage = FileStorage(tmp_path / "s.json")

    outbound = _complete_registration(90005, storage, transplant_text="18 May 2026, 2.5 acres")

    assert outbound.text == registration.messages_ta.COMPLETE_MESSAGE
    assert storage.get_farmer("farmer-90005") is None  # nothing to persist against
    assert get_or_create_state(90005).step == RegistrationStep.COMPLETE  # never blocked


def test_registration_completion_with_unknown_cluster_id_skips_persistence(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "does-not-exist")
    storage = FileStorage(tmp_path / "s.json")

    outbound = _complete_registration(90006, storage, transplant_text="18 May 2026, 2.5 acres")

    assert outbound.text == registration.messages_ta.COMPLETE_MESSAGE
    assert storage.get_farmer("farmer-90006") is None


def test_handle_incoming_without_storage_skips_persistence_entirely() -> None:
    """No storage passed at all -- the pre-Part-3 call shape, still
    supported for pure state-machine tests. Persistence is skipped, not
    defaulted to any other behavior; the farmer still completes."""
    save_state(RegistrationState(chat_id=90007, greeted=True))
    handle_incoming(90007, IncomingMessage(text="Kamatchipuram"))
    handle_incoming(90007, IncomingMessage(location=(9.87, 77.46)))
    handle_incoming(90007, IncomingMessage(text="yes"))
    outbound = handle_incoming(90007, IncomingMessage(text="18 May 2026, 2.5 acres"))

    assert outbound.text == registration.messages_ta.COMPLETE_MESSAGE
    assert get_or_create_state(90007).step == RegistrationStep.COMPLETE


def test_second_registration_from_same_chat_id_updates_existing_plot(
    monkeypatch, tmp_path
) -> None:
    """ADR-009 Prerequisite: a second complete run from the same chat_id
    updates the existing Farmer/Plot -- not a duplicate, not a rejection.
    Simulates the realistic trigger (a process restart loses in-memory
    RegistrationState -- see registration.py's _STATE_STORE docstring)
    by resetting state explicitly rather than the state machine looping
    back on itself, which it structurally cannot do today."""
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    monkeypatch.setattr(
        registration, "project_maturity_for_plot", lambda plot, cluster: "2026-08-20"
    )
    chat_id = 90008

    _complete_registration(chat_id, storage, transplant_text="1 May 2026, 2.5 acres")
    first_plot = storage.get_plot(f"plot-{chat_id}")
    assert first_plot.transplant_date == date(2026, 5, 1)
    assert first_plot.area_acres == 2.5

    # Simulate a process restart: fresh in-memory state, same chat_id.
    save_state(RegistrationState(chat_id=chat_id))
    _complete_registration(chat_id, storage, transplant_text="20 June 2026, 4.0 acres")

    updated_plot = storage.get_plot(f"plot-{chat_id}")
    assert updated_plot.transplant_date == date(2026, 6, 20)
    assert updated_plot.area_acres == 4.0
    assert len(storage.get_plots_for_cluster("c1")) == 1


def test_re_registration_after_linkfarmer_updates_the_canonical_proxy_plot_not_the_retired_duplicate(
    monkeypatch, tmp_path,
) -> None:
    """ADR-013 Part 2 Decision 18: generalizes ADR-009's "same chat_id ->
    update, not duplicate" rule from ID-derivation to a live lookup.
    Simulates the state after a real /linkfarmer link: a proxy-registered
    farmer_id now holds the real chat_id, and the original duplicate
    farmer-{chat_id}/plot-{chat_id} has been retired with its own
    telegram_chat_id cleared (apply_link's actual behavior -- see
    link_farmer.apply_link). A second self-registration from that same
    chat_id must update the canonical proxy record, not resurrect the
    retired duplicate by blindly re-minting farmer-{chat_id}."""
    from harvest_convoy.models import Farmer, Plot

    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    monkeypatch.setattr(
        registration, "project_maturity_for_plot", lambda plot, cluster: "2026-08-20"
    )
    chat_id = 90009

    # The proxy record (already linked -- has the real chat_id) and the
    # retired duplicate it was linked from.
    storage.put_farmer(Farmer(
        farmer_id="farmer-proxy-abc123", name="Original Name", cluster_id="c1",
        telegram_chat_id=chat_id,
    ))
    storage.put_plot(Plot(
        plot_id="plot-proxy-abc123", farmer_id="farmer-proxy-abc123", cluster_id="c1",
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.5,
        registered_by="operator:999", registered_at="2026-01-01T00:00:00+00:00",
    ))
    storage.put_farmer(Farmer(
        farmer_id=f"farmer-{chat_id}", name="Original Name", cluster_id="c1",
        telegram_chat_id=None,  # cleared by apply_link
    ))
    storage.put_plot(Plot(
        plot_id=f"plot-{chat_id}", farmer_id=f"farmer-{chat_id}", cluster_id="c1",
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.5,
        retired_reason="linked_to:farmer-proxy-abc123",
    ))

    _complete_registration(chat_id, storage, transplant_text="20 June 2026, 4.0 acres")

    # The canonical proxy plot is the one that changed.
    canonical_plot = storage.get_plot("plot-proxy-abc123")
    assert canonical_plot.transplant_date == date(2026, 6, 20)
    assert canonical_plot.area_acres == 4.0
    # Provenance preserved -- this plot's origin is still the operator's,
    # not overwritten to "self" just because the farmer touched it now.
    assert canonical_plot.registered_by == "operator:999"
    assert canonical_plot.registered_at == "2026-01-01T00:00:00+00:00"

    # The retired duplicate is untouched -- not resurrected.
    retired_plot = storage.get_plot(f"plot-{chat_id}")
    assert retired_plot.transplant_date == date(2026, 5, 1)
    assert retired_plot.retired_reason == "linked_to:farmer-proxy-abc123"

    # Still exactly two plots in the cluster -- no third was created.
    assert len(storage.get_plots_for_cluster("c1")) == 2
