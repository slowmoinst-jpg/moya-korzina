import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.shopbrowser import live

def test_live_availability():
    # На Windows доступно всегда
    assert live.is_live_available() is True

def test_live_status_idle():
    st = live.get_live_status("magnit", "79990000001")
    assert st["ok"] is True
    assert st["status"] == "idle"

def test_start_invalid_chain():
    res = live.start_live_login("invalid_store_xyz", "79990000001")
    assert res["ok"] is False
    assert "не поддерживается" in res["error"]

def test_stop_not_running():
    res = live.stop_live_login("magnit", "79990000001")
    assert res["ok"] is True
    assert res["status"] == "closed"

def test_save_not_running():
    res = live.save_live_login("magnit", "79990000001")
    assert res["ok"] is False
    assert "не открыто" in res["error"]
