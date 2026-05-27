"""Pytest config for the integration's pure-logic unit tests.

`state_logic` has no Home Assistant imports, so we add the integration
directory to sys.path and import it directly — no HA install required.
"""
import pathlib
import sys

_INTEGRATION = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "intelbras_guardian"
)
sys.path.insert(0, str(_INTEGRATION))
