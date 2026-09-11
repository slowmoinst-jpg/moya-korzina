"""Загрузка конфигурации из config.yaml."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.yaml")

DEFAULTS: dict[str, Any] = {
    "db_path": "data/basket.db",
    "baseline_store": "pyaterochka",
    "extra_order_penalty_rub": 150.0,
    "weight_tolerance_pct": 20,
    "connectors": {"rate_limit_rps": 1.0, "cache_ttl_hours": 6, "timeout_sec": 10},
    "optimizer": {"max_stores": 2, "top_n": 3},
}


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        for key, value in loaded.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key] = {**cfg[key], **value}
            else:
                cfg[key] = value
    return cfg


def get(path: str, default: Any = None) -> Any:
    """get('connectors.cache_ttl_hours') -> 6"""
    node: Any = load_config()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def db_path() -> str:
    p = get("db_path", "data/basket.db")
    return p if os.path.isabs(p) else os.path.join(ROOT, p)
