"""Persisting state must never block the event loop.

`_save_sessions` (json.dump + fsync + rename) used to run synchronously on
every successful status poll — once a second — on the Raspberry Pi's SD
card. Measured on 2026-08-25: fsync up to 2.75 s in a quiet window, and the
middleware froze for 10-40 s dozens of times a day (71 stalls >= 10 s in one
day). Every freeze longer than Home Assistant's 30 s client timeout made HA
report every open zone as closed.
"""
import asyncio
import importlib
import json
import threading

import pytest

from app.services.state_manager import InMemoryStateManager

_state_manager_module = importlib.import_module("app.services.state_manager")


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _state_manager_module, "SESSIONS_FILE", tmp_path / "sessions.json"
    )
    return InMemoryStateManager()


def test_status_write_is_deferred_and_throttled(manager, monkeypatch):
    writes = []
    monkeypatch.setattr(manager, "_write_snapshot", lambda data: writes.append(data))

    async def run():
        await manager.set_last_known_status(550793, {"arm_mode": "disarmed"})
        assert writes == [], "the request path must not write inline"

        await asyncio.sleep(0.05)  # first status: written promptly, in the background
        assert len(writes) == 1
        assert writes[0]["last_known_status"]["550793"]["arm_mode"] == "disarmed"

        # Once a second for the next minute: no further writes.
        for _ in range(5):
            await manager.set_last_known_status(550793, {"arm_mode": "armed_away"})
            await asyncio.sleep(0.01)
        assert len(writes) == 1
        assert manager._persist_dirty is True

        # Shutdown flushes what is pending.
        await manager.flush()
        assert len(writes) == 2
        assert writes[-1]["last_known_status"]["550793"]["arm_mode"] == "armed_away"

    asyncio.run(run())


def test_token_write_lands_after_the_coalesce_delay(manager, tmp_path):
    manager._persist_delay = 0.02

    async def run():
        await manager.set_token("sess-1", {"access_token": "a", "expires_at": "2999-01-01T00:00:00"})
        assert not (tmp_path / "sessions.json").exists()
        await asyncio.sleep(0.2)
        data = json.loads((tmp_path / "sessions.json").read_text())
        assert "sess-1" in data["tokens"]

    asyncio.run(run())


def test_earlier_deadline_replaces_a_pending_later_one(manager, monkeypatch):
    writes = []
    monkeypatch.setattr(manager, "_write_snapshot", lambda data: writes.append(data))
    manager._persist_delay = 0.02

    async def run():
        # First status write happens at once; the second is deferred ~60 s.
        await manager.set_last_known_status(1, {"arm_mode": "disarmed"})
        await asyncio.sleep(0.05)
        await manager.set_last_known_status(1, {"arm_mode": "armed_home"})
        assert manager._persist_task is not None and not manager._persist_task.done()
        # A token write (0.02 s deadline) must not wait behind the 60 s one.
        await manager.set_token("s", {"access_token": "t"})
        await asyncio.sleep(0.2)
        assert len(writes) == 2
        assert "s" in writes[-1]["tokens"]
        assert writes[-1]["last_known_status"]["1"]["arm_mode"] == "armed_home"

    asyncio.run(run())


def test_write_runs_in_a_worker_thread(manager, monkeypatch):
    seen = []
    real = manager._write_snapshot
    monkeypatch.setattr(
        manager, "_write_snapshot", lambda data: (seen.append(threading.get_ident()), real(data))
    )

    async def run():
        await manager.set_last_known_status(2, {"arm_mode": "disarmed"})
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert seen and seen[0] != threading.get_ident()


def test_snapshot_is_a_deep_copy(manager):
    manager._last_known_status["9"] = {"zones": [{"index": 0}]}
    snap = manager._snapshot()
    snap["last_known_status"]["9"]["zones"].append({"index": 1})
    assert manager._last_known_status["9"]["zones"] == [{"index": 0}]
