"""Shared pytest setup.

Tests marked `slow` load the real models (Demucs, piano transcription, Basic
Pitch). They are skipped in a plain `pytest` run; select them with
`pytest -m slow` (or run everything with `pytest -m "slow or not slow"`).
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: runs a real model (seconds to a minute); select with -m slow")


def pytest_collection_modifyitems(config, items):
    if "slow" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="model test; run with -m slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
