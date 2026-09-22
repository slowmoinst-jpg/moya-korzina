"""Перенос общей базы в рабочее место одного номера.

До входа по телефону база была одна — data/basket.db. Теперь у каждого номера своя
папка, и старая база сама собой ничьей не становится: приложение её не читает.
Этот скрипт отдаёт её тому, чья она была.

Запуск из корня проекта:
    .venv\\Scripts\\python.exe tools/adopt_legacy_db.py +7 999 123-45-67

Старый файл остаётся на месте — копируем, не переносим: если номер введён с
опечаткой, ничего не потеряно.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, users  # noqa: E402


def main(argv: list[str]) -> int:
    phone = users.normalize_phone(" ".join(argv))
    if not phone:
        print("Нужен номер телефона: python tools/adopt_legacy_db.py +7 999 123-45-67")
        return 2
    source = config.db_path()
    if config.db_override() or not os.path.exists(source):
        print(f"Общей базы нет: {source}")
        return 1
    target = users.db_path_for(phone)
    if os.path.exists(target):
        print(f"У номера {users.display(phone)} база уже есть: {target}. Не трогаю.")
        return 1
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(source, target)
    print(f"Скопировано: {source} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
