"""Карта страниц и их адреса. Отсюда растёт навигация и сюда добавляют экраны.

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ПРЕЖНЕГО. В Streamlit экран выбирался значением в
session_state, и у него не было адреса: на корзину нельзя было дать ссылку,
кнопка «назад» уводила из приложения, а две вкладки рядом мешали друг другу.
Здесь у каждого экрана свой адрес, и это не украшение — это то, ради чего
затевался переезд.

РАЗДЕЛЫ И ЭКРАНЫ повторяют выбранное владельцем направление «Цвет магазина»:
четыре раздела, на телефоне — нижняя панель. Порядок и названия взяты из
app/ui/main.py один в один, чтобы человек не переучивался.

КАК ДОБАВЛЯЮТ ЭКРАН. Новый файл app/web/screens/<имя>.py с функцией page(),
возвращающей готовую страницу, и строка в SCREENS ниже. Пока экран не перенесён,
он всё равно стоит в навигации и открывается честной заглушкой: спрятать его
значило бы сделать вид, что приложение умеет меньше, чем умеет, — а оно умеет,
просто пока на старом интерфейсе.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from flask import Flask, render_template

from app.web import auth

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Screen:
    """Экран: адрес, название, надзаголовок и раздел, в котором он живёт."""

    key: str
    path: str
    title: str
    eyebrow: str
    section: str


# Порядок внутри раздела — порядок в меню. Он же порядок в нижней панели телефона.
SCREENS: tuple[Screen, ...] = (
    Screen("home", "/", "Главная", "Что это и четыре настройки", "Настройка"),
    Screen("stores", "/stores", "Магазины", "Откуда берутся цены", "Настройка"),
    Screen("accounts", "/accounts", "Кабинеты", "Вход в свои аккаунты сетей", "Настройка"),
    Screen("receipts", "/receipts", "Мои чеки", "Загрузка из сервиса ФНС", "Настройка"),
    Screen("cards", "/cards", "Карты и акции", "Условия банков и магазинов", "Настройка"),
    Screen("about", "/about", "О продукте", "Что умеет и чего не умеет", "Настройка"),

    Screen("basket", "/basket", "Корзина", "Что покупаем", "Корзина"),
    Screen("result", "/result", "Результат", "Разбиение и экономия", "Корзина"),
    Screen("orders", "/orders", "Заказы", "Покупки из чеков ФНС и добавленные вручную", "Корзина"),
    Screen("history", "/history", "История", "Покупки семьи", "Корзина"),

    Screen("products", "/products", "Товары", "Что покупаем и где это лежит", "Каталог"),
    Screen("prices", "/prices", "Цены", "Что отдают сети прямо сейчас", "Каталог"),
    Screen("compare", "/compare", "Сравнение", "Один товар — все доставки", "Каталог"),

    Screen("links", "/links", "Связи", "Один товар — разные названия в сетях", "Связи"),
)

SCREEN_BY_KEY = {s.key: s for s in SCREENS}
SECTIONS: tuple[str, ...] = ("Настройка", "Корзина", "Каталог", "Связи")


def screens_of(section: str) -> list[Screen]:
    return [s for s in SCREENS if s.section == section]


def install(flask_app: Flask) -> None:
    """Повесить страницы. Перенесённые — своим модулем, остальные — заглушкой."""
    from app.web.screens import home, stores

    # Перенесённые экраны. Остальные пока отвечают честной заглушкой — см. _not_yet.
    ready = {"home": home.page, "stores": stores.page}
    from app.web.screens import compare, links, prices, products
    ready.update(products=products.page, prices=prices.page, compare=compare.page, links=links.page)
    from app.web.screens import receipts
    ready["receipts"] = receipts.page
    from app.web.screens import about, cards
    ready.update(cards=cards.page, about=about.page)
    from app.web.screens import basket, history, result
    ready.update(basket=basket.page, result=result.page, history=history.page)
    # У корзины есть ещё один адрес — подсказка поиска по мере набора. Она не экран
    # (отвечает данными, а не страницей), поэтому вешает её сам экран, а в SCREENS
    # её нет: иначе она встала бы в меню отдельным пунктом.
    basket.install(flask_app)
    from app.web.screens import orders
    ready["orders"] = orders.page
    from app.web.screens import accounts
    ready["accounts"] = accounts.page
    # Кабинет магазина в меню не стоит: в него заходят с «Кабинетов», по
    # конкретной сети, — и у него своё действие, отвечающее снимком, а не страницей.
    from app.web.screens import cabinet
    cabinet.install(flask_app)

    @flask_app.context_processor
    def _menu():
        # Адрес нужен КАЖДОЙ странице: он стоит в шапке и определяет все цены,
        # которые на ней показаны. Читаем его здесь, а не в каждом экране, —
        # забытый в одном экране адрес означал бы пустую шапку на нём одном.
        from app import location as client_place
        try:
            address = client_place.address()
        except Exception:                        # noqa: BLE001
            address = None
        return {"SECTIONS": SECTIONS, "SCREENS": SCREENS, "screens_of": screens_of,
                "address": address, "screen": None}

    @flask_app.post("/address")
    def screen_home_address():
        """Сохранить адрес. Поставить его в очередь на загрузку цен — забота location."""
        from flask import redirect, request
        from app import location as client_place

        client_place.save_address(request.form.get("address", ""))
        return redirect("/?saved=1")

    @flask_app.get("/address/suggest")
    def screen_address_suggest():
        """Подсказки адреса по мере набора.

        ЗАЧЕМ ОТДЕЛЬНЫМ МАРШРУТОМ. Подсказки ходят в чужую службу (DaData, а без
        ключа — OpenStreetMap), и делать это при отрисовке главной значило бы
        задерживать её на каждом открытии. Здесь же запрос идёт только тогда,
        когда человек печатает.

        ОШИБКУ НАРУЖУ НЕ БРОСАЕМ. Подсказка — помощь, а не шаг: не ответила
        служба, не нашлось ключа, кончился интернет — человек допишет адрес сам,
        как дописывал раньше. Пустой список честнее красной плашки.
        """
        from flask import jsonify, request
        from app import geo

        query = (request.args.get("q") or "").strip()
        try:
            found = geo.suggest(query)
        except Exception:                            # noqa: BLE001
            log.warning("подсказки адреса не пришли", exc_info=True)
            found = []
        return jsonify({"items": found[:6]})

    for screen in SCREENS:
        view = ready.get(screen.key)
        if view is None:
            view = _not_yet(screen)
        flask_app.add_url_rule(screen.path, endpoint=f"screen.{screen.key}",
                               view_func=auth.needs_phone(view))

    @flask_app.errorhandler(404)
    def _lost(_exc):
        return render_template("lost.html", screen=None), 404


def _not_yet(screen: Screen):
    """Экран, который ещё не переехал. Говорит правду и уводит туда, где он работает.

    Заглушка, а не пустая страница и не скрытый пункт меню: приложение этот экран
    умеет, просто он пока живёт на прежнем интерфейсе. Спрятать его — соврать о
    возможностях; показать пустым — соврать о состоянии.
    """
    def view():
        return render_template("not_yet.html", screen=screen), 200

    view.__name__ = f"not_yet_{screen.key}"
    return view


__all__ = ["install", "SCREENS", "SECTIONS", "SCREEN_BY_KEY", "Screen", "screens_of"]
