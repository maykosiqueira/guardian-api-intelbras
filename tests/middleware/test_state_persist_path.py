"""Where the session file lands decides whether a restart keeps you logged in.

The add-on gets one persistent mount, /data, and announces it in DATA_PATH.
`state_manager` ignored it and wrote to a path relative to its own source
(/app/data in the add-on image), which lives in the container layer and is
discarded on restart: every restart asked for the Intelbras login and the
panel password again. The compose install mounts its volume on ./data beside
the app, so it never showed the bug.
"""
import importlib
import json
import os
import sys

import pytest


def _reload(monkeypatch, data_path):
    if data_path is None:
        monkeypatch.delenv("DATA_PATH", raising=False)
    else:
        monkeypatch.setenv("DATA_PATH", str(data_path))
    sys.modules.pop("app.services.state_manager", None)
    return importlib.import_module("app.services.state_manager")


def test_sessions_file_follows_data_path(monkeypatch, tmp_path):
    sm = _reload(monkeypatch, tmp_path)
    assert sm.SESSIONS_FILE == tmp_path / "sessions.json"


def test_sessions_file_falls_back_to_the_source_relative_path(monkeypatch):
    sm = _reload(monkeypatch, None)
    assert sm.SESSIONS_FILE == sm._LEGACY_SESSIONS_FILE


def test_state_from_the_old_path_is_still_read(monkeypatch, tmp_path):
    sm = _reload(monkeypatch, tmp_path)
    sm._LEGACY_SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    sm._LEGACY_SESSIONS_FILE.write_text(json.dumps({
        "tokens": {"sess-1": {"access_token": "t"}},
        "device_passwords": {},
    }))
    try:
        assert sm.InMemoryStateManager()._tokens == {"sess-1": {"access_token": "t"}}
    finally:
        sm._LEGACY_SESSIONS_FILE.unlink()
