"""Плагин pytest: прогнать тесты репозитория с вариантом правила штук.

  set SK1_FIX=finder|strip|skip1|spaced|none
  python -m pytest -p no:cacheprovider -p fixplug <тесты>
(папка плагина — в PYTHONPATH). Код рабочей копии не меняется: подмена в памяти.
"""
from __future__ import annotations

import os
import sys

ROOT = "."
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def pytest_configure(config):
    import variants

    variant = os.environ.get("SK1_FIX", "none")
    variants.use(variant)
    print(f"\n[fixplug] вариант правила штук: {variant}")


def pytest_report_header(config):
    return f"fixplug SK1_FIX={os.environ.get('SK1_FIX', 'none')}"


def pytest_sessionfinish(session, exitstatus):
    from app.catalog import match

    probe = "Кофе MacCoffee 3в1 стик 20шт"
    print(f"\n[fixplug] в конце прогона pack_count({probe!r}) = {match.pack_count(probe)}")
