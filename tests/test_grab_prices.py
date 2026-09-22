"""Закладка «Забрать цены»: собранная версия и границы, которые она не переходит.

ПОЧЕМУ У ЗАКЛАДКИ ВООБЩЕ ЕСТЬ СТОРОЖ. Её исходник читаемый, а в браузер уходит
собранная строка, и разойтись они могут молча: правка в исходнике есть, а в
закладке у человека — прошлая версия, и никто этого не заметит, потому что
закладка продолжает работать. Поэтому проверяется не «файл существует», а
совпадение собранного с исходником по опорным местам.

ВТОРАЯ ПОЛОВИНА ЗДЕСЬ ВАЖНЕЕ ПЕРВОЙ. Закладка живёт на чужой странице, где рядом
лежат куки, токены и кабинет человека. Сторожа ниже стоят на том, чего она делать
не должна: секрет в адресной строке, отправка без нажатия, чтение хранилищ.
"""
from __future__ import annotations

import os
import re
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "grab-prices.src.js")
PLAIN = os.path.join(ROOT, "docs", "grab-prices.min.txt")
PAGE = os.path.join(ROOT, "docs", "grab-prices.html")


@pytest.fixture(scope="module")
def built() -> str:
    with open(PLAIN, encoding="utf-8") as fh:
        raw = fh.read()
    assert raw.startswith("javascript:"), "закладка должна быть javascript:-ссылкой"
    return urllib.parse.unquote(raw[len("javascript:"):])


@pytest.fixture(scope="module")
def source() -> str:
    with open(SRC, encoding="utf-8") as fh:
        return fh.read()


def test_the_built_one_is_one_line_and_not_empty(built):
    """Адрес закладки однострочный: перевод строки рвёт её молча."""
    assert "\n" not in built and len(built) > 500


def test_the_built_one_matches_the_source(built, source):
    """Опорные места исходника обязаны быть в собранном — иначе он отстал.

    Проверяются не все строки подряд, а те, без которых закладка меняет смысл:
    какие сети она обслуживает и в какую дверь стучится.
    """
    for anchor in ("pyaterochka", "samokat", "/api/prices"):
        assert anchor in source, f"опора «{anchor}» пропала из исходника"
        assert anchor in built, ("собранная закладка отстала от исходника — "
                                 "пересобрать: python tools/build_bookmarklet.py tseny")


def test_it_serves_exactly_the_two_chains_the_door_accepts(built):
    """Дверь принимает пакеты только от Пятёрочки и Самоката — закладка тоже.

    Разойдись они, человек собрал бы витрину третьей сети и получил отказ уже
    после всей работы.
    """
    from app.pricebundle import BROWSER_STORES

    for chain in BROWSER_STORES:
        assert chain in built, f"закладка не знает сеть «{chain}», а дверь её принимает"
    assert "lenta" not in built and "magnit" not in built, \
        "закладка обслуживает сеть, пакеты от которой дверь не примет"


def test_the_secret_never_goes_into_the_address(built):
    """Секрет уезжает ТЕЛОМ запроса, и никогда параметром адреса.

    В адресной строке он осел бы в истории браузера и в журнале сервера навсегда,
    и дверь приложения такой запрос отвергает со словами «смените секрет»
    (app/api.py). Закладка обязана не доводить до этого.
    """
    assert "secret=" not in built, "секрет собрался в параметр адреса"
    assert re.search(r'method:\s*"POST"', built), "пакет уходит не телом запроса"
    # Секрет спрашивается и тут же используется — в хранилище страницы его нет.
    assert not re.search(r'setItem\(\s*"[^"]*secret', built, re.I), \
        "секрет сохраняется в хранилище чужой страницы"


def test_it_does_not_read_other_peoples_keys(built):
    """Из чужой страницы берётся витрина, а не её хранилища и куки."""
    for forbidden in ("document.cookie", "sessionStorage", "auth.token", "Authorization"):
        assert forbidden not in built, f"закладка трогает «{forbidden}»"


def test_nothing_is_sent_without_a_press(built, source):
    """Отправка висит на кнопке, а не на загрузке страницы.

    Закладка, которая шлёт пакет сама, — это уже не инструмент человека, а
    сборщик, работающий за его спиной.
    """
    fetch_at = built.find("/api/prices")
    assert fetch_at > 0
    # Отправка живёт внутри обработчика кнопки «Отправить в приложение».
    assert "Отправить в приложение" in source
    assert built.index("Отправить в приложение") < fetch_at, \
        "запрос к двери стоит раньше кнопки, которая его запускает"


def test_the_page_carries_the_built_link():
    """Страница с закладкой должна нести собранную ссылку, а не пустые метки."""
    with open(PAGE, encoding="utf-8") as fh:
        page = fh.read()
    assert "javascript:" in page, "закладка не собрана — python tools/build_bookmarklet.py tseny"
    assert "Забрать цены" in page


def test_the_builder_knows_every_bookmarklet():
    """Сборщик один на все закладки: два разошлись бы в первый же месяц.

    Третья, «Передать вход» (vhod), появилась 20.09.2026 из стены, а не из
    удобства: окно магазина на сервере не смогло войти в Магнит — сначала
    проверка Yandex SmartCaptcha, потом «Аккаунт заблокирован», — тогда как с
    телефона владельца тот же аккаунт открывается как обычно.
    """
    import tools.build_bookmarklet as builder

    assert set(builder.BOOKMARKLETS) == {"cheki", "tseny", "vhod"}
    for spec in builder.BOOKMARKLETS.values():
        assert os.path.exists(spec["src"]), f"исходник {spec['src']} пропал"
        assert os.path.exists(spec["page"]), f"страница {spec['page']} пропала"


def test_the_image_carries_the_built_bookmarklet():
    """Закладку надо положить В ОБРАЗ, иначе на сервере её нет вовсе.

    Замер 19.09.2026: в образе лежал только grab.min.txt — Dockerfile копировал
    ровно один файл. Собранная закладка цен была в репозитории и отсутствовала на
    сервере, и заметить это можно было только заглянув в контейнер.
    """
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
        docker = fh.read()
    assert "docs/grab-prices.min.txt" in docker, \
        "собранная закладка цен не копируется в образ — на сервере её не будет"
    assert "docs/grab.min.txt" in docker, "закладка чеков пропала из образа"
    assert "docs/hand.min.txt" in docker,         "собранная закладка «Передать вход» не копируется в образ — на сервере её не будет"
