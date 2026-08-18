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
    }

    registration.handle_language_callback(client, callback_query)

    state = get_or_create_state(42)
    assert state.language == "en"
    assert state.greeted is True
    assert client.acks == [("cb1", "English selected.", False)]


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
