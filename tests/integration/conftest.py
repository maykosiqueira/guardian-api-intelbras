"""Pytest config for integration tests (Home Assistant imported, mocked hass).

These tests exercise the coordinator and entities against a mocked
GuardianApiClient and a lightweight MagicMock ``hass`` — no running HA, no
physical panel, no deploy. They only require ``homeassistant`` to be
importable (so the entities/coordinator classes load); they do NOT use the
heavy ``pytest-homeassistant-custom-component`` fixtures, which avoids the
Windows ProactorEventLoop/pytest-socket conflict and keeps the tests fast
and portable.

A dedicated CI job installs Home Assistant and runs ``tests/integration``;
the lightweight CI job runs the pure ``tests/`` (no HA needed).
"""
import pathlib
import sys

# Project root on sys.path so `custom_components.intelbras_guardian.*` imports.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
