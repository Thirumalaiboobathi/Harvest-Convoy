from datetime import date

from harvest_convoy.telegram.registration import (
    RegistrationState,
    RegistrationStep,
    IncomingMessage,
    advance_registration,
    get_or_create_state,
    handle_incoming,
    save_state,
)


def test_full_happy_path_reaches_complete() -> None:
    state = RegistrationState(chat_id=1)

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


def test_empty_village_reply_reprompts_without_advancing() -> None:
    state = RegistrationState(chat_id=1)
    new_state, reply = advance_registration(state, IncomingMessage(text="   "))
    assert new_state.step == RegistrationStep.AWAITING_VILLAGE
    assert new_state == state


def test_text_instead_of_location_reprompts() -> None:
    state = RegistrationState(chat_id=1, step=RegistrationStep.AWAITING_LOCATION, village="V")
    new_state, reply = advance_registration(state, IncomingMessage(text="near the temple"))
    assert new_state.step == RegistrationStep.AWAITING_LOCATION
    assert "didn't look like" in reply


def test_unparseable_transplant_info_reprompts_and_names_whats_missing() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.AWAITING_TRANSPLANT_INFO,
        village="V", lat=1.0, lon=1.0,
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="soon-ish"))
    assert new_state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO
    assert "date" in reply and "area" in reply


def test_date_only_missing_area_reprompts_naming_only_area() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.AWAITING_TRANSPLANT_INFO,
        village="V", lat=1.0, lon=1.0,
    )
    new_state, reply = advance_registration(state, IncomingMessage(text="18 May 2026"))
    assert new_state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO
    assert "area" in reply
    assert "date" not in reply.split("area")[0]  # "date" shouldn't be in the missing list


def test_iso_date_format_parses() -> None:
    state = RegistrationState(
        chat_id=1,
        step=RegistrationStep.AWAITING_TRANSPLANT_INFO,
        village="V", lat=1.0, lon=1.0,
    )
    new_state, _ = advance_registration(state, IncomingMessage(text="2026-05-18, 3 acres"))
    assert new_state.transplant_date == date(2026, 5, 18)
    assert new_state.area_acres == 3.0


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
    save_state(RegistrationState(chat_id=chat_id))  # ensure clean slate

    reply1 = handle_incoming(chat_id, IncomingMessage(text="Kamatchipuram"))
    assert "location" in reply1.lower() or "share" in reply1.lower()

    # ... farmer goes quiet for a while ...

    state = get_or_create_state(chat_id)
    assert state.step == RegistrationStep.AWAITING_LOCATION
    assert state.village == "Kamatchipuram"

    reply2 = handle_incoming(chat_id, IncomingMessage(location=(9.865, 77.454)))
    state = get_or_create_state(chat_id)
    assert state.step == RegistrationStep.AWAITING_CROP_CONFIRM
    assert state.village == "Kamatchipuram"  # earlier answer preserved
