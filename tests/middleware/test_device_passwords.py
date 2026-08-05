"""Device passwords must outlive the session that registered them.

Second half of the 2026-08-05 outage: re-authenticating produced a new
session_id, and because passwords were stored under {session_id: {device_id}},
the fresh session had none. `has_saved_password` went false, so the middleware
stopped talking ISECNet to the panel and Home Assistant lost zone open/closed,
battery, signal and the panic buttons — with no error anywhere.
"""
import asyncio
import importlib

import pytest

from app.services.state_manager import InMemoryStateManager

# app/services/__init__.py binds the singleton to the name `state_manager`,
# shadowing the submodule, so the module has to come from sys.modules.
_state_manager_module = importlib.import_module("app.services.state_manager")


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _state_manager_module, "SESSIONS_FILE", tmp_path / "sessions.json"
    )
    return InMemoryStateManager()


def test_password_survives_a_new_session(manager):
    asyncio.run(manager.set_device_password("old-session", "550793", "1920"))

    # The user re-authenticates: brand new session id, same panel.
    assert asyncio.run(manager.get_device_password("new-session", "550793")) == "1920"


def test_has_saved_password_is_true_for_a_fresh_session(manager):
    """This is what drives has_saved_password, and therefore the buttons."""
    asyncio.run(manager.set_device_password("old-session", "550793", "1920"))

    saved = asyncio.run(manager.get_all_device_passwords("brand-new-session"))
    assert saved == {"550793": "1920"}


def test_legacy_per_session_file_is_migrated(manager, tmp_path):
    legacy = {
        "dead-session-a": {"501118": "1920", "519470": "0123"},
        "dead-session-b": {"550793": "1920"},
    }
    migrated = manager._migrate_device_passwords(legacy)

    assert migrated == {"501118": "1920", "519470": "0123", "550793": "1920"}


def test_migration_is_idempotent(manager):
    already_flat = {"550793": "1920"}
    assert manager._migrate_device_passwords(already_flat) == already_flat


def test_logout_does_not_erase_the_panel_password(manager):
    """Logging out must not cost the next session its ISECNet access."""
    asyncio.run(manager.set_device_password("session", "550793", "1920"))
    asyncio.run(manager.cleanup_session_passwords("session"))

    assert asyncio.run(manager.get_device_password("session", "550793")) == "1920"


def test_delete_removes_the_password(manager):
    asyncio.run(manager.set_device_password("session", "550793", "1920"))
    asyncio.run(manager.delete_device_password("session", "550793"))

    assert asyncio.run(manager.get_device_password("session", "550793")) is None
