"""ADR-012 Part 2: operator self-enrollment. Hermetic -- no live
Telegram/Bedrock calls. Covers the full flow (command -> language tap ->
enroll or replace), every failure path named in the ADR, and the
chat_id-binding requirement (Decision 1): a button tap must come from
the exact chat that sent the original /operator command, not just carry
well-formed callback_data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from harvest_convoy.models import Cluster
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import OperatorAuditEvent, OperatorEnrollmentCode
from harvest_convoy.telegram import messages_en, messages_ta, operator_enrollment, webhook
from harvest_convoy.telegram.client import SendResult
from scripts import generate_operator_code

CLUSTER_ID = "opcode-test-cluster"
REAL_OPERATOR_CHAT_ID = 501
IMPOSTER_CHAT_ID = 77777
OLD_OPERATOR_CHAT_ID = 400


class _FakeClient:
    def __init__(self):
        self.sent_messages: list[tuple] = []
        self.answered_callbacks: list[tuple] = []
        self.edited_markups: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent_messages.append((chat_id, text, reply_markup))
        return SendResult(success=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered_callbacks.append((callback_query_id, text, show_alert))
        return SendResult(success=True)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markups.append((chat_id, message_id, reply_markup))
        return SendResult(success=True)


def _cluster(operator_chat_id: int | None = None) -> Cluster:
    return Cluster(
        cluster_id=CLUSTER_ID, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454, operator_chat_id=operator_chat_id,
    )


def _code(
    code: str = "AAAA1111", *, used_at: str | None = None, used_by_chat_id: int | None = None,
    expires_in_days: int = 14, cluster_id: str = CLUSTER_ID,
) -> OperatorEnrollmentCode:
    now = datetime.now(timezone.utc)
    return OperatorEnrollmentCode(
        code=code, cluster_id=cluster_id, created_at=now.isoformat(),
        expires_at=(now + timedelta(days=expires_in_days)).isoformat(),
        used_at=used_at, used_by_chat_id=used_by_chat_id,
    )


def _message_update(chat_id: int, text: str) -> dict:
    return {"message": {"chat": {"id": chat_id}, "text": text, "from": {"id": chat_id}}}


def _lang_tap(chat_id: int, cluster_id: str, code: str, language: str) -> dict:
    return {
        "callback_query": {
            "id": "cbq-lang", "data": f"operator_lang:{cluster_id}:{code}:{language}",
            "message": {"chat": {"id": chat_id}, "message_id": 1},
            "from": {"id": chat_id},
        }
    }


def _replace_tap(chat_id: int, cluster_id: str, code: str, language: str, answer: str) -> dict:
    return {
        "callback_query": {
            "id": "cbq-replace", "data": f"operator_replace:{cluster_id}:{code}:{language}:{answer}",
            "message": {"chat": {"id": chat_id}, "message_id": 1},
            "from": {"id": chat_id},
        }
    }


def setup_function(_):
    operator_enrollment._STATE_STORE.clear()


# --- parse_operator_command ---

def test_parse_operator_command_extracts_and_uppercases_the_code() -> None:
    assert operator_enrollment.parse_operator_command("/operator abc123") == "ABC123"


def test_parse_operator_command_case_insensitive_and_ignores_trailing_junk() -> None:
    assert operator_enrollment.parse_operator_command("/Operator ABC123 please") == "ABC123"


def test_parse_operator_command_none_for_no_code() -> None:
    assert operator_enrollment.parse_operator_command("/operator") is None
    assert operator_enrollment.parse_operator_command("/operator   ") is None


def test_parse_operator_command_none_for_unrelated_text() -> None:
    assert operator_enrollment.parse_operator_command("hello") is None
    assert operator_enrollment.parse_operator_command(None) is None


# --- The command itself ---

def test_valid_code_sends_language_prompt_and_starts_pending_state(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()

    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")

    assert len(client.sent_messages) == 1
    text, reply_markup = client.sent_messages[0][1], client.sent_messages[0][2]
    assert "தமிழ்" in text or "language" in text.lower()
    assert reply_markup is not None
    pending = operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID)
    assert pending is not None
    assert pending.cluster_id == CLUSTER_ID
    assert pending.code == "AAAA1111"


def test_malformed_command_sends_usage_message_no_pending_state(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()

    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator"), storage, "s")

    assert len(client.sent_messages) == 1
    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is None


def test_unknown_code_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()

    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator NOSUCHCODE"), storage, "s")

    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is None
    assert "coordinator" in client.sent_messages[0][1].lower() or "ஒருங்கிணைப்பாளர்" in client.sent_messages[0][1]


def test_reused_code_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_operator_enrollment_code(
        _code(used_at="2026-01-01T00:00:00+00:00", used_by_chat_id=999)
    )
    client = _FakeClient()

    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")

    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is None


def test_expired_code_is_refused_with_expiry_specific_wording(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_operator_enrollment_code(_code(expires_in_days=-1))
    client = _FakeClient()

    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")

    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is None
    text = client.sent_messages[0][1]
    assert text == operator_enrollment._bilingual_expired_code_text()


def test_code_referencing_a_nonexistent_cluster_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_operator_enrollment_code(_code(cluster_id="ghost-cluster"))
    client = _FakeClient()

    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")

    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is None


# --- Language tap: fresh enrollment ---

def test_language_tap_with_no_existing_operator_enrolls_immediately(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()
    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")

    webhook.handle_update(
        client, _lang_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "ta"), storage, "s",
    )

    updated_cluster = storage.get_cluster(CLUSTER_ID)
    assert updated_cluster.operator_chat_id == REAL_OPERATOR_CHAT_ID
    assert updated_cluster.operator_language == "ta"
    code_record = storage.get_operator_enrollment_code("AAAA1111")
    assert code_record.used_at is not None
    assert code_record.used_by_chat_id == REAL_OPERATOR_CHAT_ID
    events = storage.get_operator_audit_events_for_cluster(CLUSTER_ID)
    assert len(events) == 1
    assert events[0].event_type == "enrolled"
    assert events[0].new_operator_chat_id == REAL_OPERATOR_CHAT_ID
    assert events[0].previous_operator_chat_id is None
    assert events[0].code_used == "AAAA1111"
    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is None
    # A confirmation message was sent to the new operator.
    confirmations = [m for m in client.sent_messages if m[0] == REAL_OPERATOR_CHAT_ID]
    assert any("Test Cluster" in m[1] for m in confirmations)


def test_language_tap_from_a_different_chat_than_the_command_is_refused(tmp_path) -> None:
    """Decision 1: callback_data alone (well-formed, referencing a
    genuinely valid code) must not be enough -- the tap must come from
    the chat that sent the original /operator command."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()
    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")

    webhook.handle_update(
        client, _lang_tap(IMPOSTER_CHAT_ID, CLUSTER_ID, "AAAA1111", "en"), storage, "s",
    )

    assert storage.get_cluster(CLUSTER_ID).operator_chat_id is None
    code_record = storage.get_operator_enrollment_code("AAAA1111")
    assert code_record.used_at is None
    assert storage.get_operator_audit_events_for_cluster(CLUSTER_ID) == []
    # The real pending state (for the real chat) is untouched.
    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is not None


def test_language_tap_with_no_pending_state_at_all_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()

    webhook.handle_update(
        client, _lang_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "ta"), storage, "s",
    )

    assert storage.get_cluster(CLUSTER_ID).operator_chat_id is None


def test_double_tap_second_chats_language_tap_finds_the_code_already_used(tmp_path) -> None:
    """Two different chats both hold a pending enrollment for the same
    code (e.g. a code shared by mistake) -- whichever taps first wins;
    the second is refused via the code's own used_at check, not a crash
    or a second enrollment."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()
    second_chat_id = 502
    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")
    webhook.handle_update(client, _message_update(second_chat_id, "/operator AAAA1111"), storage, "s")

    webhook.handle_update(
        client, _lang_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "ta"), storage, "s",
    )
    webhook.handle_update(
        client, _lang_tap(second_chat_id, CLUSTER_ID, "AAAA1111", "en"), storage, "s",
    )

    assert storage.get_cluster(CLUSTER_ID).operator_chat_id == REAL_OPERATOR_CHAT_ID


# --- Language tap: existing operator -> replacement flow ---

def test_language_tap_with_existing_operator_sends_replacement_prompt_not_silent_overwrite(
    tmp_path,
) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=OLD_OPERATOR_CHAT_ID))
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()
    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")

    webhook.handle_update(
        client, _lang_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "en"), storage, "s",
    )

    # Not overwritten yet.
    assert storage.get_cluster(CLUSTER_ID).operator_chat_id == OLD_OPERATOR_CHAT_ID
    code_record = storage.get_operator_enrollment_code("AAAA1111")
    assert code_record.used_at is None
    assert storage.get_operator_audit_events_for_cluster(CLUSTER_ID) == []
    prompt_messages = [m for m in client.sent_messages if m[0] == REAL_OPERATOR_CHAT_ID]
    assert any("Test Cluster" in m[1] for m in prompt_messages)
    assert prompt_messages[-1][2] is not None  # a Yes/No keyboard was attached


def test_replace_yes_performs_the_replacement_and_logs_both_chat_ids(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=OLD_OPERATOR_CHAT_ID))
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()
    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")
    webhook.handle_update(
        client, _lang_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "en"), storage, "s",
    )

    webhook.handle_update(
        client, _replace_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "en", "yes"), storage, "s",
    )

    updated_cluster = storage.get_cluster(CLUSTER_ID)
    assert updated_cluster.operator_chat_id == REAL_OPERATOR_CHAT_ID
    assert updated_cluster.operator_language == "en"
    events = storage.get_operator_audit_events_for_cluster(CLUSTER_ID)
    assert len(events) == 1
    assert events[0].event_type == "replaced"
    assert events[0].new_operator_chat_id == REAL_OPERATOR_CHAT_ID
    assert events[0].previous_operator_chat_id == OLD_OPERATOR_CHAT_ID
    code_record = storage.get_operator_enrollment_code("AAAA1111")
    assert code_record.used_at is not None


def test_replace_no_leaves_cluster_untouched_and_code_still_valid(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=OLD_OPERATOR_CHAT_ID))
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()
    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")
    webhook.handle_update(
        client, _lang_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "en"), storage, "s",
    )

    webhook.handle_update(
        client, _replace_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "en", "no"), storage, "s",
    )

    assert storage.get_cluster(CLUSTER_ID).operator_chat_id == OLD_OPERATOR_CHAT_ID
    code_record = storage.get_operator_enrollment_code("AAAA1111")
    assert code_record.used_at is None  # a genuine change of mind isn't a wasted code
    assert storage.get_operator_audit_events_for_cluster(CLUSTER_ID) == []
    assert operator_enrollment.get_pending_state(REAL_OPERATOR_CHAT_ID) is None


def test_replace_tap_from_a_different_chat_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=OLD_OPERATOR_CHAT_ID))
    storage.put_operator_enrollment_code(_code())
    client = _FakeClient()
    webhook.handle_update(client, _message_update(REAL_OPERATOR_CHAT_ID, "/operator AAAA1111"), storage, "s")
    webhook.handle_update(
        client, _lang_tap(REAL_OPERATOR_CHAT_ID, CLUSTER_ID, "AAAA1111", "en"), storage, "s",
    )

    webhook.handle_update(
        client, _replace_tap(IMPOSTER_CHAT_ID, CLUSTER_ID, "AAAA1111", "en", "yes"), storage, "s",
    )

    assert storage.get_cluster(CLUSTER_ID).operator_chat_id == OLD_OPERATOR_CHAT_ID


# --- Storage round-trip ---

def test_operator_enrollment_code_round_trips_through_file_storage(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    record = _code()
    storage.put_operator_enrollment_code(record)
    assert storage.get_operator_enrollment_code("AAAA1111") == record
    assert storage.get_operator_enrollment_code("NOPE") is None


def test_operator_audit_event_round_trips_through_file_storage(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    event = OperatorAuditEvent(
        cluster_id=CLUSTER_ID, event_type="enrolled", occurred_at="2026-08-22T10:00:00+00:00",
        code_used="AAAA1111", new_operator_chat_id=REAL_OPERATOR_CHAT_ID,
    )
    storage.put_operator_audit_event(event)
    events = storage.get_operator_audit_events_for_cluster(CLUSTER_ID)
    assert events == [event]
    assert storage.get_operator_audit_events_for_cluster("other-cluster") == []


def test_operator_audit_events_are_never_pruned_by_unrelated_storage_operations(tmp_path) -> None:
    """OperatorAuditEvent exists so operator authority can be
    reconstructed months later -- nothing may ever delete or overwrite
    one. There is deliberately no delete method for this entity in any
    storage backend; this test proves the operations most likely to
    someday grow one (a cluster update at season rollover, clearing a
    machine's status, clearing a plot's harvest record) leave every
    prior audit event for the cluster untouched."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=OLD_OPERATOR_CHAT_ID))
    first = OperatorAuditEvent(
        cluster_id=CLUSTER_ID, event_type="enrolled", occurred_at="2026-01-01T00:00:00+00:00",
        code_used="C1", new_operator_chat_id=OLD_OPERATOR_CHAT_ID,
    )
    second = OperatorAuditEvent(
        cluster_id=CLUSTER_ID, event_type="replaced", occurred_at="2026-06-01T00:00:00+00:00",
        code_used="C2", new_operator_chat_id=REAL_OPERATOR_CHAT_ID,
        previous_operator_chat_id=OLD_OPERATOR_CHAT_ID,
    )
    storage.put_operator_audit_event(first)
    storage.put_operator_audit_event(second)

    # Operations that mutate other state for the same cluster, none of
    # which should touch operator_audit at all.
    storage.put_cluster(_cluster(operator_chat_id=REAL_OPERATOR_CHAT_ID))  # e.g. season rollover
    storage.clear_machine_status(CLUSTER_ID)
    storage.clear_plot_harvest("some-plot", CLUSTER_ID, "s1")

    events = storage.get_operator_audit_events_for_cluster(CLUSTER_ID)
    assert events == [first, second]


def test_generate_code_produces_8_char_uppercase_hex() -> None:
    code = operator_enrollment.generate_code()
    assert len(code) == 8
    assert code == code.upper()
    int(code, 16)  # raises ValueError if not valid hex


def test_operator_as_of_finds_the_most_recent_eligible_event(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_operator_audit_event(OperatorAuditEvent(
        cluster_id=CLUSTER_ID, event_type="enrolled", occurred_at="2026-08-01T00:00:00+00:00",
        code_used="C1", new_operator_chat_id=100,
    ))
    storage.put_operator_audit_event(OperatorAuditEvent(
        cluster_id=CLUSTER_ID, event_type="replaced", occurred_at="2026-08-10T00:00:00+00:00",
        code_used="C2", new_operator_chat_id=200, previous_operator_chat_id=100,
    ))

    # As of a date between the two events -- the first is in effect.
    result = operator_enrollment.operator_as_of(storage, CLUSTER_ID, "2026-08-05T23:59:59+00:00")
    assert result.new_operator_chat_id == 100

    # As of a date after both -- the second is in effect.
    result2 = operator_enrollment.operator_as_of(storage, CLUSTER_ID, "2026-08-15T23:59:59+00:00")
    assert result2.new_operator_chat_id == 200

    # As of a date before either -- no operator recorded yet.
    result3 = operator_enrollment.operator_as_of(storage, CLUSTER_ID, "2026-07-01T23:59:59+00:00")
    assert result3 is None


# --- scripts/generate_operator_code.py ---

def test_generate_operator_code_fails_for_a_nonexistent_cluster(tmp_path, monkeypatch, capsys) -> None:
    storage = FileStorage(tmp_path / "s.json")
    monkeypatch.setattr(generate_operator_code, "get_storage", lambda: storage)

    exit_code = generate_operator_code.main(["--cluster-id", "ghost"])

    assert exit_code == 1
    assert "no cluster found" in capsys.readouterr().err


def test_generate_operator_code_writes_a_fetchable_unused_code(tmp_path, monkeypatch, capsys) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    monkeypatch.setattr(generate_operator_code, "get_storage", lambda: storage)

    exit_code = generate_operator_code.main(["--cluster-id", CLUSTER_ID])

    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "Test Cluster" in printed

    # Recover the code printed and confirm it's real, unused, valid.
    code_line = [line for line in printed.splitlines() if line.strip() and line.strip().isalnum()][0]
    code = code_line.strip()
    record = storage.get_operator_enrollment_code(code)
    assert record is not None
    assert record.cluster_id == CLUSTER_ID
    assert record.used_at is None
    record_status = operator_enrollment.validate_code(storage, code)[1]
    assert record_status == "valid"
