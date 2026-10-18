"""pytest-плагин: подмена исправления (fixplug.install) в каждом тесте; режим — переменная FIX."""
import os

import pytest


@pytest.fixture(autouse=True)
def _fix_everywhere(monkeypatch):
    mode = os.environ.get("FIX", "none")
    if mode != "none":
        import fixplug
        fixplug.install(monkeypatch, mode)
    yield
