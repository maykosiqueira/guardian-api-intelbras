"""Pytest config for the middleware (FastAPI) tests.

Adds ``intelbras-guardian-api`` to sys.path so ``app.*`` imports resolve
without installing the middleware as a package. Only needs pydantic/aiohttp,
which the middleware already depends on — no running server, no HTTP calls.
"""
import pathlib
import sys

_MIDDLEWARE = pathlib.Path(__file__).resolve().parents[2] / "intelbras-guardian-api"
sys.path.insert(0, str(_MIDDLEWARE))
