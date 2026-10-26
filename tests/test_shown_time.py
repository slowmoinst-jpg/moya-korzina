from __future__ import annotations

from datetime import datetime

from app import freshness
from app.web.phone import cheaper


def test_shown_explicit_utc_string_converts_to_moscow_time():
    utc_str = "2026-10-25T22:29:00+00:00"
    res = freshness.shown(utc_str)
    assert res is not None
    assert res == datetime(2026, 10, 26, 1, 29, tzinfo=freshness.SHOWN_TZ)
    assert res.strftime("%d.%m %H:%M") == "26.10 01:29"


def test_shown_epoch_seconds_converts_to_moscow_time():
    utc_str = "2026-10-25T22:29:00+00:00"
    epoch_sec = datetime.fromisoformat(utc_str).timestamp()
    res = freshness.shown(epoch_sec)
    assert res is not None
    assert res == datetime(2026, 10, 26, 1, 29, tzinfo=freshness.SHOWN_TZ)
    assert res.strftime("%d.%m %H:%M") == "26.10 01:29"


def test_shown_invalid_or_none_returns_none():
    assert freshness.shown(None) is None
    assert freshness.shown("мусор") is None
    assert freshness.shown("") is None
    assert freshness.shown(False) is None


def test_shown_naive_string_matches_local_astimezone():
    naive_str = "2026-10-25T22:29:00"
    expected = datetime.fromisoformat(naive_str).astimezone(freshness.SHOWN_TZ)
    assert freshness.shown(naive_str) == expected


def test_cheaper_moment_text_utc_string():
    assert cheaper._moment_text("2026-10-25T22:29:00+00:00") == "26.10 01:29"
