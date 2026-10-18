"""Уточнённое исправление находки — подменой, без правки рабочей копии.

Кнопка становится участником той же охраны, что link_soon:
  link.claim(phone)   — под _RUNNING_LOCK: проход уже идёт → запомнить сверку в _AGAIN и
                        вернуть False; иначе записать телефон в _RUNNING и вернуть True;
  link.release(phone) — снять метку и, если за время кнопки звали link_soon (_AGAIN),
                        передать это ОДНИМ проходом фоном (link_soon), а не в запросе.
links._do_link_catalog: claim → refresh.link_products() → finally release. Вызов
refresh.link_products сохранён: его подменяют тесты экрана.

Как плагин pytest: python -m pytest -p fixplug ...
"""
from __future__ import annotations

import logging

log = logging.getLogger("fixplug")


def claim(phone, sync: bool = True) -> bool:
    from app.catalog import link

    if not phone:
        return True
    with link._RUNNING_LOCK:
        if phone in link._RUNNING:
            link._AGAIN[phone] = link._AGAIN.get(phone, False) or sync
            return False
        link._RUNNING.add(phone)
        return True


def release(phone) -> None:
    from app.catalog import link

    if not phone:
        return
    with link._RUNNING_LOCK:
        link._RUNNING.discard(phone)
        again = link._AGAIN.pop(phone, None)
    if again is not None:
        link.link_soon(phone, sync=again)


def button_pass(phone):
    """То же, что кнопка, без Flask: для проб."""
    from app.catalog.refresh import link_products

    if not claim(phone):
        return None
    try:
        return link_products()
    finally:
        release(phone)


def _do_link_catalog(products) -> None:
    from flask import flash

    try:
        from app.catalog.refresh import link_products
    except Exception as exc:  # noqa: BLE001
        flash(f"Единый каталог сейчас недоступен ({exc}). Остальное на экране работает.", "warn")
        return
    if not products:
        flash("Опознавать пока нечего — список товаров пуст.", "info")
        return
    from app import users

    phone = users.current()
    if not claim(phone):
        flash("Сверка с единым каталогом уже идёт фоном — очередь ниже обновится, когда она"
              " закончится.", "info")
        return
    try:
        found = link_products()
    except Exception as exc:  # noqa: BLE001
        log.exception("опознание в едином каталоге не удалось")
        flash(f"Опознание не удалось: {exc}. Остальное на экране работает.", "bad")
        return
    finally:
        release(phone)
    if found.get("failed"):
        flash(f"Не записано из-за сбоя — {found['failed']}: эти товары пройдут при следующей"
              " сверке, можно нажать ещё раз.", "warn")
    if found["linked"]:
        weak = found.get("weak") or 0
        doubtful = found.get("doubtful") or 0
        extra = ""
        if weak:
            extra += (f" Под вопросом — {weak}: артикулы записаны кандидатами и уедут в"
                      " корзину, когда вы подтвердите их у товара по сети.")
        if doubtful:
            extra += f" Не уверены — {doubtful}: подходят несколько товаров, угадывать не стали."
        flash(f"Опознано {found['linked']} из {found['tried']}: артикулы сетей записаны, "
              f"цены по адресу спросит «Обновить цены».{extra}", "ok")
    elif found.get("doubtful"):
        flash(f"Не уверены — {found['doubtful']}: подходят несколько товаров каталога,"
              " угадывать не стали. Ниже — живой подбор.", "info")
    elif not found.get("failed"):
        flash(f"Ни один из {found['tried']} товаров в каталоге не опознан — либо каталог "
              "ещё не собран, либо названия расходятся. Ниже — живой подбор.", "info")


def install() -> None:
    from app.catalog import link
    from app.web.screens import links as screen

    link.claim = claim
    link.release = release
    screen._do_link_catalog = _do_link_catalog


def pytest_configure(config):  # noqa: ARG001 — плагин pytest
    import os
    import sys

    wt = "."
    if wt not in sys.path:
        sys.path.insert(0, wt)
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    install()
