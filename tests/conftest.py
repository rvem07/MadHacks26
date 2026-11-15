"""Shared fixtures: every test gets an isolated, freshly seeded store.

AFD_TODAY is pinned to the demo date so edge ages (and therefore staleness
flags) are deterministic regardless of when the tests run.
"""
import pytest

from afd.config import reset_settings
from afd.seed import seed_all
from afd.store import reset_store

DEMO_TODAY = "2026-11-14"


def pytest_addoption(parser):
    parser.addoption('--run-sentinel-browser', action='store_true', default=False,
                     help='Run headless Chrome with a temporary localhost fixture server')


@pytest.fixture
def store(tmp_path):
    reset_settings(data_dir=tmp_path, today_override=DEMO_TODAY, auto_reply_s=-1.0)
    s = reset_store(tmp_path)
    seed_all(s)
    return s
