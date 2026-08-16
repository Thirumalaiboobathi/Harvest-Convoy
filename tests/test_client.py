import httpx

from harvest_convoy.telegram.client import TelegramClient


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {"ok": True, "result": {"message_id": 1}}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self) -> dict:
        return self._json


def test_send_message_succeeds_on_first_try(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    client = TelegramClient(token="fake-token")
    result = client.send_message(123, "hello")

    assert result.success is True
    assert len(calls) == 1
    assert calls[0][1]["chat_id"] == 123
    assert calls[0][1]["text"] == "hello"


def test_send_message_retries_then_succeeds(monkeypatch) -> None:
    attempts = {"n": 0}

    def fake_post(url, json, timeout):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("simulated network failure")
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("harvest_convoy.telegram.client.time.sleep", lambda s: None)

    client = TelegramClient(token="fake-token")
    result = client.send_message(123, "hello")

    assert result.success is True
    assert attempts["n"] == 3


def test_send_message_degrades_after_exhausting_retries(monkeypatch) -> None:
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("simulated permanent failure")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("harvest_convoy.telegram.client.time.sleep", lambda s: None)

    client = TelegramClient(token="fake-token")
    result = client.send_message(123, "hello")

    assert result.success is False
    assert result.error is not None


def test_telegram_api_error_response_is_treated_as_failure(monkeypatch) -> None:
    def fake_post(url, json, timeout):
        return _FakeResponse(json_data={"ok": False, "description": "chat not found"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("harvest_convoy.telegram.client.time.sleep", lambda s: None)

    client = TelegramClient(token="fake-token")
    result = client.send_message(999999, "hello")

    assert result.success is False


def test_answer_callback_query_builds_correct_payload(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    client = TelegramClient(token="fake-token")
    client.answer_callback_query("cbq123", "Done.", show_alert=True)

    assert calls[0]["callback_query_id"] == "cbq123"
    assert calls[0]["text"] == "Done."
    assert calls[0]["show_alert"] is True
