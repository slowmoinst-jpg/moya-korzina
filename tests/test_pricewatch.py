"""Дозор цен: цены корзин обновляются сами, круглые сутки, и только то, что устарело.

Сеть здесь не трогаем (tests/conftest.py): коннектор подменён, рабочие места лежат во
временной папке. Проверяется то, ради чего дозор есть: устаревшая цена спрашивается,
свежая — нет; пишется только живая цена этого прохода, а не копия вчерашнего чека;
сеть домашнего выхода при спящем туннеле пропускается, не постучав.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, homeexit, pricewatch, repo, users  # noqa: E402
from app.connectors import cache  # noqa: E402
from app.matcher import matcher  # noqa: E402
from app.models import PriceSnapshot, Product  # noqa: E402

PHONE = "79990000001"


def _stamp(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


class FakeConnector:
    """Коннектор сети: отвечает заготовленными снимками и помнит, о чём спросили."""

    def __init__(self, code: str, answers: dict[str, PriceSnapshot], asked: list):
        self.code = code
        self.answers = answers
        self.asked = asked

    def get_prices(self, skus):
        self.asked.append((self.code, sorted(skus)))
        return [self.answers[s] for s in skus if s in self.answers]


@pytest.fixture
def places(tmp_path, monkeypatch):
    """Рабочие места во временной папке и дозор без чужих настроек."""
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(homeexit, "STATE_DIR", str(tmp_path / "pace"))
    monkeypatch.setattr(pricewatch, "_ASKED", {})
    monkeypatch.setattr(pricewatch, "_EXIT_UP", {})
    settings = {"chains": ["magnit", "dixy"], "refresh_after_min": 120, "baskets": 3, "demo": True}
    real = config.get

    def get(key, default=None):
        if key.startswith("prices.watch."):
            value = settings.get(key.rsplit(".", 1)[1])
            return default if value is None else value
        if key == "connectors.home_exit":
            return {"proxy": "socks5://172.17.0.1:1080", "chains": ["dixy"]}
        return real(key, default)

    monkeypatch.setattr(config, "get", get)
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: True)
    yield settings
    users.deactivate()


@pytest.fixture
def network(monkeypatch):
    """Подменённые сети: answers[сеть][артикул] -> снимок; asked — кого о чём спросили."""
    answers: dict[str, dict[str, PriceSnapshot]] = {"magnit": {}, "dixy": {}}
    asked: list = []
    monkeypatch.setattr(matcher, "_get_connector",
                        lambda code, location=None: FakeConnector(code, answers.get(code, {}), asked))
    return answers, asked


def _sku(code: str, n: int) -> str:
    """Артикул сети, как у настоящих: у Магнита число, у Дикси десятизначный код."""
    return f"{1000 + n}" if code == "magnit" else f"{2000300000 + n}"


def _place(phone: str = PHONE, goods: int = 2) -> list[tuple[int, dict[str, int]]]:
    """Рабочее место с корзиной: товары, опознанные в Магните и Дикси. -> [(товар, {сеть: sp_id})]."""
    users.open_workspace(phone)
    try:
        basket = repo.create_basket("Неделя")
        out = []
        for n in range(goods):
            product_id = repo.upsert_product(Product(None, f"Товар {n}", unit="pcs"))
            repo.set_basket_item(basket, product_id, 1)
            ids = {}
            for code in ("magnit", "dixy"):
                store = repo.get_store(code)
                sp_id = repo.upsert_store_product(store.id, _sku(code, n), f"Товар {n}")
                repo.confirm_mapping(product_id, sp_id, confirmed=True)
                ids[code] = sp_id
            out.append((product_id, ids))
        return out
    finally:
        users.deactivate()


def _snapshots(phone: str, sp_id: int) -> list[dict]:
    users.open_workspace(phone)
    try:
        from app.db import get_conn
        with get_conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM store_prices WHERE store_product_id=? ORDER BY id", (sp_id,))]
    finally:
        users.deactivate()


# ---------- что устарело ----------
def test_only_old_or_missing_live_prices_are_due(places):
    goods = _place(goods=4)
    users.open_workspace(PHONE)
    try:
        (fresh, f_ids), (old, o_ids), (reference, r_ids), (never, _) = goods
        repo.save_price(f_ids["magnit"], 90.0, fetched_at=_stamp(0.5))
        repo.save_price(o_ids["magnit"], 90.0, fetched_at=_stamp(3))
        repo.save_price(r_ids["magnit"], 90.0, fetched_at=_stamp(0.1), source="fallback")
        store = repo.get_store("magnit")

        due = pricewatch.stale([fresh, old, reference, never], store, hours=2)
    finally:
        users.deactivate()

    assert due == [old, reference, never], "свежую живую цену второй раз не спрашиваем"


@pytest.mark.parametrize("code, sku", [("dixy", "hist-{pid}"), ("magnit", "magnit-ogurcy-450")])
def test_a_made_up_article_is_not_asked(places, code, sku):
    """«hist-…» — строка чека, «magnit-…» — строка справочника: сеть о них не знает.

    Так сопоставлено демо: без этого дозор каждые два часа «спрашивал» бы сеть о
    товарах, которых у неё нет, и писал в журнал «устарело 2, записано 0».
    """
    users.open_workspace(PHONE)
    try:
        store = repo.get_store(code)
        product_id = repo.upsert_product(Product(None, "Хлеб", unit="pcs"))
        sp_id = repo.upsert_store_product(store.id, sku.format(pid=product_id), "Хлеб")
        repo.confirm_mapping(product_id, sp_id, confirmed=True)

        assert pricewatch.stale([product_id], store, hours=2) == []
    finally:
        users.deactivate()


# ---------- проход ----------
def test_a_pass_refreshes_the_stale_and_writes_only_live_prices(places, network):
    answers, asked = network
    (first, first_ids), (second, second_ids) = _place()
    answers["magnit"]["1000"] = PriceSnapshot("magnit", "1000", 89.0)
    # второй товар коннектор отдал из чека: цена вчерашняя, живой она не станет
    answers["magnit"]["1001"] = PriceSnapshot("magnit", "1001", 120.0,
                                                    fetched_at="2026-09-01T10:00:00")

    tallies = {t.code: t for t in pricewatch.run_once(only=["magnit"])}

    assert asked == [("magnit", ["1000", "1001"])]
    assert (tallies["magnit"].due, tallies["magnit"].updated) == (2, 1)
    assert [s["price"] for s in _snapshots(PHONE, first_ids["magnit"])] == [89.0]
    assert _snapshots(PHONE, second_ids["magnit"]) == [], "копия чека каждые два часа не нужна"


def test_a_fresh_price_is_left_alone(places, network):
    answers, asked = network
    [(product, ids)] = _place(goods=1)
    users.open_workspace(PHONE)
    try:
        repo.save_price(ids["magnit"], 89.0, fetched_at=_stamp(0.5))
    finally:
        users.deactivate()

    pricewatch.run_once(only=["magnit"])

    assert asked == [], "цену, снятую полчаса назад, у сети не спрашиваем"


def test_a_sleeping_home_exit_skips_its_chain_without_knocking(places, network, monkeypatch):
    """Туннель спит — Дикси в этом проходе пропущена целиком: сервер она не пускает."""
    answers, asked = network
    _place(goods=1)
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: False)

    tallies = {t.code: t for t in pricewatch.run_once()}

    assert [code for code, _ in asked] == ["magnit"], "Магнит не на домашнем выходе — идёт своим путём"
    assert "домашний выход" in tallies["dixy"].skipped

    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: True)
    pricewatch.run_once()

    assert ("dixy", ["2000300000"]) in asked, "выход поднялся — Дикси спрошена на ближайшем проходе"


def test_a_chain_paused_after_a_check_is_skipped(places, network):
    """Сеть через дом показала «я не робот» — дозор её не трогает, пока идёт пауза."""
    answers, asked = network
    _place(goods=1)
    homeexit.note_refusal("dixy")

    tallies = {t.code: t for t in pricewatch.run_once()}

    assert [code for code, _ in asked] == ["magnit"]
    assert "я не робот" in tallies["dixy"].skipped


def test_an_item_without_a_live_price_is_not_asked_every_quarter_hour(places, network):
    """Сеть живой цены не дала — следующая попытка через срок обновления, а не через 15 минут."""
    answers, asked = network
    _place(goods=1)

    pricewatch.run_once(only=["dixy"])
    pricewatch.run_once(only=["dixy"])

    assert asked == [("dixy", ["2000300000"])]


def test_every_place_gets_its_own_fresh_price(places, network):
    """Два рабочих места с одним товаром — свежая цена ложится в базу каждого."""
    answers, asked = network
    _place(phone=PHONE, goods=1)
    _place(phone="79990000002", goods=1)
    answers["magnit"]["1000"] = PriceSnapshot("magnit", "1000", 89.0)

    tallies = {t.code: t for t in pricewatch.run_once(only=["magnit"])}

    assert tallies["magnit"].places == 2 and tallies["magnit"].updated == 2


def test_demo_can_be_left_out(places, network):
    places["demo"] = False
    _place(phone=users.DEMO, goods=1)

    pricewatch.run_once(only=["magnit"])

    assert network[1] == []


def test_dry_run_counts_and_does_not_ask(places, network):
    answers, asked = network
    _place(goods=2)

    tallies = {t.code: t for t in pricewatch.run_once(only=["magnit"], dry_run=True)}

    assert asked == [] and tallies["magnit"].due == 2


def test_one_broken_place_does_not_stop_the_others(places, network, monkeypatch):
    answers, asked = network
    _place(phone=PHONE, goods=1)
    _place(phone="79990000002", goods=1)
    real = pricewatch.basket_products

    def flaky(limit):
        if users.current() == PHONE:
            raise RuntimeError("база занята")
        return real(limit)

    monkeypatch.setattr(pricewatch, "basket_products", flaky)

    tallies = {t.code: t for t in pricewatch.run_once(only=["magnit"])}

    assert asked == [("magnit", ["1000"])], "второе рабочее место обошлось"
    assert tallies["magnit"].errors


# ---------- кэш и служба ----------
def test_only_the_price_request_is_held_to_the_short_cache(places, monkeypatch):
    """Суженный срок кэша — вокруг цен, а не вокруг подбора магазина к адресу.

    Справочник магазинов Магнита идёт через дом, и спрашивать его каждые два часа ради
    того, что за два часа не меняется, — лишний запрос через адрес владельца.
    """
    [(product, ids)] = _place(goods=1)
    seen = {}

    def connector(code, location=None):
        seen["place"] = cache._MAX_AGE.get()

        class Prices:
            def get_prices(self, skus):
                seen["prices"] = cache._MAX_AGE.get()
                return []

        return Prices()

    monkeypatch.setattr(matcher, "_get_connector", connector)
    pricewatch.run_once(only=["magnit"])

    assert seen == {"place": None, "prices": pytest.approx(3600.0)}


def test_the_watch_does_not_take_cache_older_than_it_allows(tmp_path, monkeypatch):
    """Ответ пятичасовой давности кнопке годится, дозору — нет: он пришёл за свежим."""
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache.cache_set("magnit", "card", {"price": 89.0})
    path = cache._path("magnit", "card")
    old = time.time() - 5 * 3600
    os.utime(path, (old, old))

    assert cache.cache_get("magnit", "card") == {"price": 89.0}
    with cache.max_age(3600):
        assert cache.cache_get("magnit", "card") is None
    assert cache.cache_get("magnit", "card") == {"price": 89.0}, "сужение живёт только внутри"


def test_the_watch_can_be_switched_off(monkeypatch):
    real = config.get
    monkeypatch.setattr(config, "get", lambda key, default=None: (
        False if key == "prices.watch.enabled" else real(key, default)))

    assert pricewatch.serve_in_background() is None


def test_the_watch_runs_in_its_own_thread(monkeypatch):
    started = []
    monkeypatch.setattr(pricewatch, "serve", lambda: started.append(True))

    thread = pricewatch.serve_in_background()
    thread.join(2)

    assert started == [True] and thread.name == "pricewatch" and thread.daemon


def test_the_service_starts_the_watch(monkeypatch):
    """Служба korzina-jobs поднимает дозор вместе с приёмной дверью, до обхода."""
    from app.catalog import store, worker

    events = []
    monkeypatch.setattr(store, "init", lambda: None)
    monkeypatch.setattr(worker.api, "serve_in_background", lambda: events.append("api"))
    monkeypatch.setattr(worker.pricewatch, "serve_in_background", lambda: events.append("watch"))
    monkeypatch.setattr(store, "revive_stuck", lambda: (_ for _ in ()).throw(SystemExit))

    with pytest.raises(SystemExit):
        worker.serve()

    assert events == ["api", "watch"]
