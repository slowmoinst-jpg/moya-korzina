"""Рабочие места: каждому номеру телефона — своя база.

Зачем. Приложение выросло из установки «для себя», где база одна и человек один.
Как только адрес появился на сервере, туда придут разные люди, и их чеки, корзины и
карты не должны лежать в одной куче. Простейшее разделение, которое не требует
переписывать ни репозиторий, ни экраны, — отдельный файл базы на человека:
data/users/<номер>/basket.db. Схема та же, справочник магазинов тот же, а вот
покупки, товары, сопоставления, корзины и адрес — свои.

Как это работает. Путь к базе подставляется через контекст (app.config.set_db_override)
на каждом прогоне интерфейса, до первого обращения к репозиторию. Репозиторий по-прежнему
зовёт config.db_path() и получает базу того человека, чей прогон идёт. Потоков
приложение не заводит, поэтому контекст не теряется; если заведёт — контекст надо
будет передать в поток явно (contextvars.copy_context().run).

Вход пока БЕЗ ПОДТВЕРЖДЕНИЯ: решение владельца 16.09.2026 — «просто по номеру
телефона, без пароля, без подтверждения». Значит, кто знает номер, тот и войдёт.
Это осознанная временная мера, и на экране входа об этом сказано словами; подтверждение
по коду появится вместе с загрузкой чеков из кабинета ФНС, где код из СМС нужен и так.

Демо. Рабочее место «demo» — витрина с вымышленным чеком и условиями карт: оно
наполняется само при первом открытии. Настоящие рабочие места начинаются ПУСТЫМИ:
подсовывать живому человеку вымышленные покупки в его собственную историю нельзя.
"""
from __future__ import annotations

import contextvars
import os
import re

from app import config

DEMO = "demo"
USERS_DIR = os.path.join(config.ROOT, "data", "users")

_CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("korzina_phone", default=None)


def normalize_phone(raw: str | None) -> str | None:
    """«+7 (999) 123-45-67» -> «79991234567»; «demo» -> «demo»; иначе None.

    Принимаем всё, что человек привык вводить: с плюсом, скобками, дефисами, с
    восьмёркой вместо семёрки, без кода страны вовсе. Не принимаем то, что телефоном
    быть не может: короче десяти цифр или длиннее одиннадцати. Один номер должен
    давать одну и ту же папку, иначе человек с «8 999…» и «+7 999…» получит две базы.
    """
    text = (raw or "").strip()
    if text.lower() == DEMO:
        return DEMO
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    return None


def display(phone: str | None) -> str:
    """Как номер показывается в шапке: «+7 999 123-45-67» или «Демо»."""
    if not phone:
        return ""
    if phone == DEMO:
        return "Демо"
    if len(phone) == 11:
        return f"+{phone[0]} {phone[1:4]} {phone[4:7]}-{phone[7:9]}-{phone[9:]}"
    return phone


def workspace_dir(phone: str) -> str:
    return os.path.join(USERS_DIR, phone)


def db_path_for(phone: str) -> str:
    return os.path.join(workspace_dir(phone), "basket.db")


def exists(phone: str) -> bool:
    return os.path.exists(db_path_for(phone))


def activate(phone: str) -> str:
    """Направить репозиторий в базу этого человека. Возвращает путь к базе.

    Вызывается на КАЖДОМ прогоне интерфейса: контекст живёт в потоке прогона, а
    Streamlit между прогонами его не хранит.
    """
    path = db_path_for(phone)
    config.set_db_override(path)
    _CURRENT.set(phone)
    return path


def deactivate() -> None:
    config.set_db_override(None)
    _CURRENT.set(None)


def current() -> str | None:
    """Чьё рабочее место активно в этом контексте; None — общая база."""
    return _CURRENT.get()


def open_workspace(phone: str) -> str:
    """Создать (если нет) и открыть базу человека: папка, схема, справочник магазинов.

    Идемпотентно. Демо-данные здесь НЕ наливаются — это забота интерфейса, и только
    для рабочего места demo.
    """
    from app.db import init_db

    path = activate(phone)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    init_db()
    return path


def list_workspaces() -> list[str]:
    """Номера, у которых уже есть база. Нужно утилитам, а не интерфейсу."""
    if not os.path.isdir(USERS_DIR):
        return []
    return sorted(name for name in os.listdir(USERS_DIR)
                  if os.path.exists(db_path_for(name)))


def resolve_login(raw: str | None) -> tuple[str | None, str | None]:
    """Что делать с тем, что человек ввёл: (номер, None) либо (None, объяснение)."""
    if not (raw or "").strip():
        return None, "Введите номер телефона."
    phone = normalize_phone(raw)
    if not phone:
        return None, ("Это не похоже на номер: нужно десять цифр после кода страны, "
                      "например +7 999 123-45-67.")
    return phone, None
