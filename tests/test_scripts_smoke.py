"""Smoke tests for every script entrypoint in scripts/.

Exists because scripts/run_polling.py called webhook.handle_update() with
a stale (pre-Phase-5) signature and nothing caught it until it was run
live -- nothing in scripts/ is exercised by the rest of the test suite,
so a signature drift there is invisible to CI. These tests import each
script and drive its main() with fake/patched dependencies (no real
network, no real AWS, no long waits) far enough to prove every call to a
cross-module function -- especially ones whose signature has changed
before -- is still shaped correctly. A stale call site fails these with a
TypeError from unittest.mock's autospec, in CI, not on someone's phone.

Not full behavioral tests of what each script does -- see
test_coordinator.py, test_webhook.py, etc. for that. This layer only
answers "does main() still wire its calls together correctly."
"""

from __future__ import annotations

import sys
from unittest.mock import create_autospec

import httpx
import pytest

from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram import webhook
from scripts import (
    print_tamil_strings,
    run_polling,
    seed_cluster,
    seed_cluster_naducauvery,
    trigger_scenario,
)


class _FakeResponse:
    """Same fake used by tests/test_client.py -- kept local since these
    tests exercise raw httpx.get calls the client itself doesn't make."""

    def __init__(self, json_data: dict) -> None:
        self._json = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json


class _StopSmokeTest(Exception):
    """Raised by a mocked call to break out of a script's loop/flow once
    we've proven the call reached us with the right signature -- lets the
    test stop deterministically instead of letting a real polling loop or
    wait_for_resolution's 300s window run."""


# ---------------------------------------------------------------------
# seed_cluster.py
# ---------------------------------------------------------------------

def test_seed_cluster_main_without_write(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["seed_cluster.py"])
    seed_cluster.main()
    assert "Kamatchipuram" in capsys.readouterr().out


def test_seed_cluster_main_with_write(monkeypatch, tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    monkeypatch.setattr(sys, "argv", ["seed_cluster.py", "--write"])
    monkeypatch.setattr(seed_cluster, "get_storage", lambda: storage)

    seed_cluster.main()

    assert storage.get_cluster("kamatchipuram") is not None
    assert len(storage.get_plots_for_cluster("kamatchipuram")) == 8


# ---------------------------------------------------------------------
# seed_cluster_naducauvery.py -- ADR-008 Decision 3, the second cluster
# ---------------------------------------------------------------------

def test_seed_cluster_naducauvery_main_without_write(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["seed_cluster_naducauvery.py"])
    seed_cluster_naducauvery.main()
    assert "Naducauvery" in capsys.readouterr().out


def test_seed_cluster_naducauvery_main_with_write(monkeypatch, tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    monkeypatch.setattr(sys, "argv", ["seed_cluster_naducauvery.py", "--write"])
    monkeypatch.setattr(seed_cluster_naducauvery, "get_storage", lambda: storage)

    seed_cluster_naducauvery.main()

    assert storage.get_cluster("naducauvery") is not None
    assert len(storage.get_plots_for_cluster("naducauvery")) == 8


def test_both_clusters_seed_into_the_same_storage_without_colliding(tmp_path) -> None:
    """farmer_id/plot_id namespaces (bare f01.. vs nc-f01..) must not
    collide when both demo clusters are seeded into one Storage backend
    -- this is the actual multi-district claim, not just two scripts that
    happen to both run."""
    storage = FileStorage(tmp_path / "storage.json")

    seed_cluster.seed_into_storage(storage)
    seed_cluster_naducauvery.seed_into_storage(storage)

    assert len(storage.get_plots_for_cluster("kamatchipuram")) == 8
    assert len(storage.get_plots_for_cluster("naducauvery")) == 8
    assert len(storage.get_farmers_for_cluster("kamatchipuram")) == 8
    assert len(storage.get_farmers_for_cluster("naducauvery")) == 8

    # Kamatchipuram's f01 is untouched by seeding the second cluster.
    assert storage.get_farmer("f01").name == "Muthu Pandian"
    assert storage.get_farmer("nc-f01").name == "Marimuthu Iyer"


# ---------------------------------------------------------------------
# print_tamil_strings.py -- ADR-008 Part 2 deliverable
# ---------------------------------------------------------------------

def test_print_tamil_strings_main_runs_without_error(capsys) -> None:
    from harvest_convoy.telegram import messages_ta

    print_tamil_strings.main()

    out = capsys.readouterr().out
    assert "GREETING_INTRO" in out
    assert messages_ta.GREETING_INTRO in out


# ---------------------------------------------------------------------
# run_polling.py
# ---------------------------------------------------------------------

def test_run_polling_main_calls_handle_update_with_current_signature(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-smoke-test")
    monkeypatch.setattr(sys, "argv", ["run_polling.py", "--season-id", "2099-test"])
    monkeypatch.setattr(
        run_polling, "get_storage", lambda: FileStorage(tmp_path / "storage.json")
    )

    fake_update = {"update_id": 1, "message": {"chat": {"id": 1}, "text": "hi"}}
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"result": [fake_update]})
    )

    spy = create_autospec(webhook.handle_update, side_effect=_StopSmokeTest)
    monkeypatch.setattr(run_polling.webhook, "handle_update", spy)

    with pytest.raises(_StopSmokeTest):
        run_polling.main()

    spy.assert_called_once()
    args = spy.call_args.args
    assert args[1] == fake_update
    assert args[3] == "2099-test"


# ---------------------------------------------------------------------
# trigger_scenario.py
# ---------------------------------------------------------------------

def test_trigger_scenario_main_offline_calls_handle_update_with_current_signature(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-smoke-test")
    monkeypatch.setattr(sys, "argv", ["trigger_scenario.py", "999", "--offline"])
    monkeypatch.setattr(
        trigger_scenario, "get_storage", lambda: FileStorage(tmp_path / "storage.json")
    )

    # TelegramClient posts (send_harvest_scheduled, send_not_ready,
    # send_operator_route_summary, send_escalation) -- succeed instantly,
    # no real network. --offline always escalates deterministically (see
    # module docstring), so main() reaches wait_for_resolution() next.
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse({"ok": True, "result": {"message_id": 1}})
    )

    fake_callback_update = {
        "update_id": 1,
        "callback_query": {"id": "cb1", "data": "resolve:kamatchipuram:p03:p04:p03"},
    }
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"result": [fake_callback_update]})
    )

    spy = create_autospec(webhook.handle_update, side_effect=_StopSmokeTest)
    monkeypatch.setattr(trigger_scenario.webhook, "handle_update", spy)

    with pytest.raises(_StopSmokeTest):
        trigger_scenario.main()

    spy.assert_called_once()
    args, kwargs = spy.call_args.args, spy.call_args.kwargs
    assert args[1] == fake_callback_update
    assert args[3] == trigger_scenario.CURRENT_SEASON_ID
    assert "lookup_farmer_for_plot" in kwargs
