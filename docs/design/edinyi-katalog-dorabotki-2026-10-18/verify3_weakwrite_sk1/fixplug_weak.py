"""Плагин pytest: предложенное исправление _WEAK подменой в памяти (рабочая копия не трогается).

_WEAK[key] ставится вместе с _SYNCED[key] — после materialize и set_master, — а не
сразу после _recheck. Запуск из корня рабочей копии:
    PYTHONPATH=<эта папка> python -m pytest -p no:cacheprovider -p fixplug_weak <тесты>
"""
from __future__ import annotations

import inspect


def _apply() -> None:
    from app.catalog import link

    src = inspect.getsource(link._link_one)
    old_weak = ('        if (current.get("method") or "").endswith("?") and write:\n'
                '            with _STATE_LOCK:\n'
                '                _WEAK[key] = True\n')
    assert old_weak in src, "текст _link_one изменился"
    src = src.replace(old_weak, "")
    old_synced = ('    with _STATE_LOCK:\n'
                  '        _SYNCED[key] = True\n')
    assert src.count(old_synced) == 1
    src = src.replace(old_synced,
                      '    with _STATE_LOCK:\n'
                      '        _SYNCED[key] = True\n'
                      '        if current and not force and (current.get("method") or "").endswith("?"):\n'
                      '            _WEAK[key] = True\n')
    exec(compile(src, "<fixed _link_one>", "exec"), link.__dict__)
    link._FIXED_WEAK = True


def pytest_configure(config):
    _apply()


def pytest_report_header(config):
    from app.catalog import link

    return f"fixplug_weak: исправление _WEAK применено = {getattr(link, '_FIXED_WEAK', False)}"
