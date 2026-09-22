"""Вход, переданный с телефона: что принимаем, что отвергаем и почему.

ПОЧЕМУ ЭТИ ПРОВЕРКИ СТРОЖЕ ОБЫЧНЫХ. Сюда приезжает то, чем человек доказывает
магазину, что он это он, — и приезжает ВСТАВКОЙ ИЗ БУФЕРА, то есть попасть туда
могло что угодно. Ошибка здесь не «не сработало», а «сервер ходит в магазин с
чужим ключом» или «кабинет отмечен подключённым, а входа в нём нет».

Дорога появилась 20.09.2026 из стены, а не из удобства: окно магазина на сервере
получило от Магнита проверку Yandex SmartCaptcha, а после неё «Аккаунт
заблокирован», тогда как тот же аккаунт с телефона владельца открывается как
обычно. Возможной её делает замер 17.09.2026: кука входа `mg_at` не HttpOnly,
страница видит все двадцать одну куку magnit.ru.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.shopbrowser import handoff  # noqa: E402


def packed(**fields) -> str:
    """То, что кладёт в буфер закладка docs/hand.src.js."""
    body = {"store": "magnit", "host": "magnit.ru",
            "cookies": [{"name": "mg_at", "value": "к" * 100},
                        {"name": "mg_udi", "value": "3d8da145"}]}
    body.update(fields)
    return json.dumps(body, ensure_ascii=False)


# ---------- что принимаем ----------

def test_a_handed_over_login_becomes_a_state_the_browser_understands():
    """На выходе — тот же словарь, что кладёт в рабочее место обычный вход.

    Иначе ветки разойдутся: наполнение корзины подставляет состояние в браузер
    одним и тем же способом, и «почти такой же» словарь сломал бы её молча.
    """
    state = handoff.parse("magnit", packed())

    assert state["origins"] == []
    assert {c["name"] for c in state["cookies"]} == {"mg_at", "mg_udi"}
    assert all(c["domain"] == ".magnit.ru" and c["path"] == "/" for c in state["cookies"])


def test_the_value_of_the_login_cookie_survives_untouched():
    """Значение куки не режется и не чинится: это ключ, а не текст.

    Закладка режет строку буфера по ПЕРВОМУ знаку равенства именно поэтому —
    у ключа Магнита в хвосте бывает «=» по правилам base64.
    """
    state = handoff.parse("magnit", packed(cookies=[{"name": "mg_at", "value": "abc.def=="}]))

    assert state["cookies"][0]["value"] == "abc.def=="


def test_a_subdomain_is_still_our_own_house():
    """Кука с поддомена сети — своя: сети раздают их и с www, и с api."""
    state = handoff.parse("magnit", packed(host="www.magnit.ru",
                                           cookies=[{"name": "mg_at", "value": "x",
                                                     "domain": "www.magnit.ru"}]))

    assert state["cookies"][0]["domain"] == ".www.magnit.ru"


# ---------- что отвергаем ----------

def test_a_login_to_another_chain_is_refused_by_name():
    """Вставили вход одной сети в кабинет другой — говорим это прямо.

    Самая простая ошибка человека и самая дорогая: промолчать значило бы
    записать Магниту вход во ВкусВилл и объявить кабинет подключённым.
    """
    with pytest.raises(handoff.Rejected) as why:
        handoff.parse("vkusvill", packed())

    assert "другую сеть" in str(why.value) and "magnit" in str(why.value)


def test_cookies_from_a_strangers_site_never_get_in():
    """Куки чужого сайта не принимаем, даже когда сеть названа верно.

    Поле store в переданном пишет закладка, а править его может кто угодно:
    решает не подпись, а домен, с которого куки сняты.
    """
    with pytest.raises(handoff.Rejected) as why:
        handoff.parse("magnit", packed(host="magnit.ru.zlo.example"))

    assert "чужого сайта" in str(why.value)


def test_a_cookie_of_a_lookalike_domain_is_dropped():
    """«magnit.ru.zlo.example» в домене куки — не наш дом, и это проверяется отдельно.

    Хозяин проверяется совпадением целиком или по точке слева. Проверка
    вхождением подстроки пропустила бы и этот домен, и «notmagnit.ru».
    """
    with pytest.raises(handoff.Rejected):
        handoff.parse("magnit", packed(cookies=[
            {"name": "mg_at", "value": "x", "domain": "magnit.ru.zlo.example"},
            {"name": "a", "value": "b", "domain": "notmagnit.ru"},
        ]))


def test_junk_instead_of_the_bookmarklets_text_says_what_to_do():
    """Вставили не то — объясняем, что именно ожидалось, а не «ошибка разбора»."""
    with pytest.raises(handoff.Rejected) as why:
        handoff.parse("magnit", "привет")

    assert "закладка" in str(why.value).lower()


def test_an_empty_jar_points_at_the_likely_mistake():
    """Кук нет — скорее всего закладку нажали не на странице магазина."""
    with pytest.raises(handoff.Rejected) as why:
        handoff.parse("magnit", packed(cookies=[]))

    assert "не на странице магазина" in str(why.value)


def test_a_monstrous_value_is_not_a_cookie():
    """Значение длиной в мегабайт — не ключ, а вставленное не глядя.

    Настоящий mg_at — 648 знаков (замер 17.09.2026), потолок взят с запасом
    больше чем на порядок.
    """
    state = handoff.parse("magnit", packed(cookies=[
        {"name": "mg_at", "value": "x"},
        {"name": "big", "value": "y" * (handoff.MAX_VALUE + 1)},
    ]))

    assert {c["name"] for c in state["cookies"]} == {"mg_at"}


def test_a_name_that_is_not_a_cookie_name_is_dropped():
    """Кириллица и пробелы в имени куки не встречаются — это вставленный кусок чужого."""
    state = handoff.parse("magnit", packed(cookies=[
        {"name": "mg_at", "value": "x"},
        {"name": "имя куки", "value": "y"},
        {"name": "", "value": "z"},
    ]))

    assert {c["name"] for c in state["cookies"]} == {"mg_at"}


# ---------- вошёл ли человек ----------

def test_magnit_without_its_login_cookie_is_known_to_be_a_guest():
    """У Магнита имя куки входа снято живьём — значит ответ твёрдый.

    Гостю сеть тоже выдаёт куки (mg_udi, shopCode), и принять их значило бы
    отметить подключённым кабинет, в который никто не входил.
    """
    state = handoff.parse("magnit", packed(cookies=[{"name": "mg_udi", "value": "x"}]))

    assert handoff.looks_logged_in("magnit", state) is False


def test_magnit_with_its_login_cookie_is_known_to_be_inside():
    state = handoff.parse("magnit", packed())

    assert handoff.looks_logged_in("magnit", state) is True


def test_a_chain_whose_login_cookie_we_never_measured_answers_i_do_not_know():
    """Пять сетей из шести подписи входа нам не показали — так и отвечаем.

    False здесь был бы выдумкой ровно такой же силы, как True: имя куки входа у
    них достоверно не установлено (signals.AUTH_COOKIE), а угаданное имя либо
    запретило бы работающий вход, либо объявило вошедшим гостя.
    """
    state = handoff.parse("vkusvill", packed(store="vkusvill", host="vkusvill.ru"))

    assert handoff.looks_logged_in("vkusvill", state) is None


# ---------- что показываем человеку ----------

def test_the_report_never_shows_a_cookie_value():
    """Значение куки входа — это и есть вход. Показать его значит положить на стол."""
    secret = "СЕКРЕТНОЕ-ЗНАЧЕНИЕ-КЛЮЧА"
    state = handoff.parse("magnit", packed(cookies=[{"name": "mg_at", "value": secret}]))

    said = handoff.summary(state)

    assert secret not in said
    assert "mg_at" in said and "1" in said
