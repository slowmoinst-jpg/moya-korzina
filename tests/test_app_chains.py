"""Сети в приложении: каждая должна быть принята сервером и знать свой адрес.

ЗАЧЕМ (живой промах 20.10.2026). В приложении было восемь сетей, а в единой базе
одиннадцать: FixPrice сервер обходил и цены у него были (6 624 строки), но в приложении
его не было — значит собрать его корзину в фоне человек не мог. Добавил — и тут же
ошибся доменом: написал `fixprice.ru`, а настоящий адрес витрины `fix-price.com`
(поймал старый тест про артикул из адреса карточки, tests/test_phone_learns_skus.py).

Ошибка тихая: приложение открыло бы пустую страницу или чужой домен, а сервер отбросил
бы присланные карточки как «не наша сеть» — и в журнале было бы просто «0 карточек».

ВТОРАЯ ПОЛОВИНА ТОЙ ЖЕ ОШИБКИ — СЕРВЕРНЫЕ СПИСКИ (живой промах 20.10.2026, сборка
положила 0-1 позицию из 4). FixPrice был в APK, но отсутствовал в `SEED_STORES` и в
`ABILITIES`: справочник магазинов его не знал, экран подключений его не показывал — и
на телефоне сети не было вовсе, хотя в приложении она была. Поэтому проверка одна на
всё сразу: код сети, стартовый адрес и хозяева адресов у приложения и у сервера
обязаны сходиться, а сеть приложения обязана быть и в справочнике магазинов, и в
списке подключений.
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlsplit

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrecord, config, phonecart, repo, store_accounts  # noqa: E402
from app.db import init_db  # noqa: E402

SOURCE = os.path.join(ROOT, "android", "app", "app", "src", "main", "java",
                      "ru", "moyakorzina", "app", "MainActivity.java")
ROW = re.compile(r'\{"([^"]+)",\s*"([^"]+)",\s*"(https://[^"]+)",\s*"([^"]+)"\}')


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Пустая база рабочего места — та же, какой её создаёт сервер при первом запуске."""
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "chains.db"))
    init_db()


def app_chains() -> list[tuple[str, str, str, list[str]]]:
    """Сети из приложения: (название, код, стартовый адрес, свои хосты)."""
    with open(SOURCE, encoding="utf-8") as fh:
        text = fh.read()
    return [(name, code, url, [h.strip() for h in hosts.split(",") if h.strip()])
            for name, code, url, hosts in ROW.findall(text)]


def test_the_app_lists_the_chains_it_claims():
    chains = app_chains()

    assert len(chains) >= 8, f"сетей в приложении {len(chains)} — список разобрался не весь"
    codes = [code for _, code, _, _ in chains]
    assert len(codes) == len(set(codes)), f"код сети повторяется: {codes}"
    assert "fixprice" in codes, "FixPrice должен быть в приложении: он есть в единой базе"


def test_the_server_accepts_every_chain_the_app_offers():
    """Сеть есть в приложении, а сервер её код не знает — карточки и корзина пропадут."""
    for name, code, _, _ in app_chains():
        assert code in phonecart.CHAINS, f"{name} ({code}): сервер не примет её с телефона"


def test_every_app_chain_names_its_own_hosts():
    """Адрес из приложения и хозяева адресов у сервера обязаны сходиться."""
    for name, code, url, hosts in app_chains():
        server_hosts = cartrecord.CHAIN_HOSTS.get(code)
        assert server_hosts, f"{name} ({code}): у сервера нет записи о её адресах"

        start = (urlsplit(url).hostname or "").lstrip(".")
        assert any(start == h or start.endswith("." + h) for h in server_hosts), (
            f"{name}: приложение открывает {start}, а сервер считает своими "
            f"{server_hosts} — карточки оттуда будут отброшены как чужие")
        for host in hosts:
            assert host in server_hosts, (
                f"{name}: приложение записывает запросы {host}, а сервер знает {server_hosts}")


def test_the_app_and_the_server_know_the_same_hosts_of_each_chain():
    """Свои адреса сети — один и тот же список у приложения и у сервера.

    По этому списку приложение решает, какие запросы записывать, а сервер — какие
    карточки принимать. Разойдясь, они теряли бы данные молча: телефон записывал бы
    запрос к узлу, который сервер не считает своим, и отчёт приходил бы пустым.
    """
    for name, code, _url, hosts in app_chains():
        assert set(hosts) == set(cartrecord.CHAIN_HOSTS.get(code, ())), (
            f"{name} ({code}): приложение знает адреса {sorted(hosts)}, "
            f"сервер — {sorted(cartrecord.CHAIN_HOSTS.get(code, ()))}")


def test_every_app_chain_is_in_the_store_register(fresh_db):
    """Сеть в приложении, а в справочнике магазинов её нет — на телефоне её не видно.

    Живой промах 20.10.2026: FixPrice был в списке сетей APK, но отсутствовал в
    SEED_STORES, и на телефоне сети не было вовсе. Сторож не сверяет список со
    списком, а создаёт базу тем же init_db, что и сервер: так он ловит и случай,
    когда строку сида убрали, и случай, когда досыпка перестала работать.

    Покраснеет, если убрать fixprice из app/db.py:SEED_STORES.
    """
    codes = {store.code for store in repo.list_stores()}
    for name, code, _url, _hosts in app_chains():
        assert code in codes, f"{name} ({code}): сети нет в справочнике магазинов (app/db.py)"

    fixprice = repo.get_store("fixprice")
    assert fixprice is not None, "FixPrice пропал из справочника — на телефоне его не будет"
    assert fixprice.name == "FixPrice", "имя сети на телефоне — из справочника магазинов"


def test_every_app_chain_is_on_the_connections_screen():
    """Сеть приложения без записи в ABILITIES не попадёт в список подключений.

    Экран подключений строится из store_accounts.connections(), а тот — из
    ABILITIES: сеть, которой там нет, на телефоне просто не показывается, и
    собрать её корзину человек не может.
    """
    for name, code, _url, _hosts in app_chains():
        assert code in store_accounts.ABILITIES, (
            f"{name} ({code}): нет в store_accounts.ABILITIES — на телефоне сеть не видна")


def test_the_phone_and_the_server_name_the_same_chains():
    """Сервер принимает каждую сеть приложения, а словарь адресов не отстал.

    `phonecart.CHAINS` собран из `cartrecord.CHAIN_HOSTS`, поэтому проверяются две
    вещи: каждая сеть APK принята сервером и сам CHAINS — ровно те сети, чьи адреса
    сервер знает. Сетей в CHAINS БОЛЬШЕ, чем в приложении: Vprok и Монетка заведены
    в единой базе, но своего окна в приложении у них ещё нет. Это запас, а не
    расхождение; ошибкой было бы обратное — сеть в APK, которой сервер не знает.
    """
    app_codes = {code for _name, code, _url, _hosts in app_chains()}

    assert app_codes <= set(phonecart.CHAINS), (
        f"сервер не открывает сети приложения: {sorted(app_codes - set(phonecart.CHAINS))}")
    assert set(phonecart.CHAINS) == set(cartrecord.CHAIN_HOSTS), (
        "phonecart.CHAINS разошёлся с картой адресов cartrecord.CHAIN_HOSTS")

