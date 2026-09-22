"""Вход по номеру телефона и витрина «Посмотреть демо».

Отдельным файлом от auth.py нарочно: auth отвечает на вопрос «чья база открыта»
и висит на каждом запросе, а здесь всего три страницы. Смешать их — значит
поставить редко меняющийся сторож рядом с часто меняющимся экраном.

ВХОД БЕЗ ПОДТВЕРЖДЕНИЯ, и на экране это сказано прямо. Решение владельца
16.09.2026: «просто по номеру телефона, без пароля, без подтверждения». Мера
временная и осознанная; код из СМС появится вместе с загрузкой чеков из кабинета
ФНС, где он нужен и так. Пока его нет, честная строка на экране — единственное,
что отличает осознанную меру от дыры, о которой забыли.

ДЕМО — отдельное рабочее место с вымышленными покупками. Оно нужно, чтобы человек
увидел работающий расчёт до того, как принесёт свои чеки. Настоящие рабочие места
начинаются ПУСТЫМИ: подсовывать живому человеку выдуманные покупки в его
собственную историю нельзя, он примет их за свои.
"""
from __future__ import annotations

import logging

from flask import Blueprint, redirect, render_template, request, url_for

from app import users
from app.web import auth

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)


def _safe_next(raw: str | None) -> str:
    """Куда вернуть человека после входа. Чужие адреса сюда не пролезут.

    Открытый перевод по чужому адресу — классическая беда страниц входа: ссылка
    вида /login?next=https://зло.example приводит человека на подделку сразу
    после того, как он ввёл номер, и выглядит это как продолжение работы. Поэтому
    берём только путь внутри сайта, начинающийся с одной косой черты.
    """
    value = (raw or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@bp.get("/login")
def login():
    if auth.current_phone():
        return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html", next=_safe_next(request.args.get("next")),
                           error=None, typed="")


@bp.post("/login")
def enter():
    typed = request.form.get("phone", "")
    phone, complaint = users.resolve_login(typed)
    if not phone:
        return render_template("login.html", next=_safe_next(request.form.get("next")),
                               error=complaint, typed=typed), 400
    users.open_workspace(phone)
    users.deactivate()                 # база снимается тут же: её выберет before_request
    auth.sign_in(phone)
    log.info("вход: %s", users.display(phone))
    return redirect(_safe_next(request.form.get("next")))


@bp.post("/demo")
def demo():
    """Витрина. Заводится и наполняется при первом открытии, дальше живёт как есть."""
    users.open_workspace(users.DEMO)
    _fill_demo()
    users.deactivate()
    auth.sign_in(users.DEMO)
    return redirect("/")


@bp.post("/logout")
def logout():
    auth.sign_out()
    return redirect(url_for("auth.login"))


def _fill_demo() -> None:
    """Налить демо-данные, если их ещё нет. Тихо: пустая витрина не повод падать."""
    from app import repo

    try:
        if repo.list_products():
            return
        import os
        import sys

        tools = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import seed

        seed.main()
    except Exception as exc:  # noqa: BLE001 — витрина без данных лучше, чем отказ войти
        log.warning("демо-данные не налились (%s)", exc)


__all__ = ["bp"]
