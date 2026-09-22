"""Приём отчёта от расширения-сборщика.

Здесь защищается не разбор JSON, а четыре обещания, каждое из которых человек
проверить не может, а полагается на них ежедневно.

ПЕРВОЕ: отчёт принимается целиком или не принимается вовсе. «Половина принята» —
худшее из состояний: экран говорит «готово», а купоны или цены потерялись, и
человек узнает об этом, только не найдя скидку на кассе.

ВТОРОЕ: подключение — наблюдаемый факт. Вошёл — отмечаем, вышел — гасим, промолчал
— не трогаем. И умеет подключение ровно то, что реально пришло, а не то, что
обещано справочником: обещанная корзина, которой сегодня не было, отправит наряд
в никуда.

ТРЕТЬЕ: просроченный купон не показывается, но и не стирается. Показать — значит
пообещать скидку, которой нет; стереть — значит потерять знание о том, что сеть
такому покупателю вообще даёт.

ЧЕТВЁРТОЕ, и оно жёстче всех: у нас не оказывается ни одного чужого секрета.
Сборщик их не собирает, но приёмник обязан выжить и в тот день, когда в расширении
появится лишняя строка.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import collector, config, repo, store_accounts  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "collector.db"))
    init_db()


ITEM = {"sku": "4306830", "name": "Азу из курицы с картофельным пюре СытоЕдов 300г",
        "price": 176.99, "base_price": 294.99, "in_stock": True,
        "unit": "pcs", "weight_g": 300,
        "url": "https://5ka.ru/product/azu-iz-kuritsy--4306830/"}

# Срок нарочно далёкий: купон должен считаться действующим и через десять лет,
# иначе тест начнёт краснеть сам по себе в первый же просроченный день.
COUPON = {"id": "x5-30-milk", "title": "-30% на молоко Простоквашино",
          "ends_at": "2099-09-30", "value": "-30%"}

OLD_COUPON = {"id": "x5-15-bread", "title": "-15% на хлеб",
              "ends_at": "2020-01-01", "value": "-15%"}


def report(**kw) -> dict:
    body = {"store": "pyaterochka", "address": "Москва, Столярный переулок 2",
            "collected_at": "2026-09-16T23:40:00", "logged_in": True,
            "account": "Карта X5 •••1234", "gives": ["prices", "coupons", "cart"],
            "items": [ITEM], "coupons": [COUPON],
            "cart_result": {"ok": ["4306830"],
                            "failed": [{"sku": "77", "why": "нет в наличии"}]}}
    body.update(kw)
    return body


def everything_written() -> str:
    """Всё, что вообще легло в базу рабочего места, одной строкой.

    Грубо нарочно: сторож не должен знать, в какой именно таблице однажды окажется
    лишнее поле. Пусть смотрит везде.
    """
    with repo.get_conn() as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if not str(r[0]).startswith("sqlite_")]
        rows = []
        for table in tables:
            for row in c.execute(f"SELECT * FROM {table}"):
                rows.append(" ".join(str(value) for value in tuple(row)))
    return "\n".join(rows)


# ---------- одна дверь ----------
def test_one_call_takes_prices_coupons_login_and_cart(db):
    """Всё из одного отчёта — за один вызов, и сводка отвечает на все вопросы экрана."""
    summary = collector.accept(report())

    assert summary["store_name"] == "Пятёрочка"
    assert summary["prices_saved"] == 1 and summary["prices_skipped"] == 0
    assert summary["connected"] is True and summary["account"] == "Карта X5 •••1234"
    assert summary["coupons_saved"] == 1 and summary["coupons_active"] == 1
    assert summary["cart_ok"] == 1
    assert summary["cart_failed"] == [{"sku": "77", "why": "нет в наличии"}]


def test_report_text_is_accepted_as_well_as_an_object(db):
    """Отчёт приезжает из браузера текстом — разбирать его должна та же дверь."""
    summary = collector.accept(json.dumps(report(), ensure_ascii=False))
    assert summary["prices_saved"] == 1


def test_prices_go_through_the_price_bundle(db):
    """Цены пишет пакет цен, а не второй разбор внутри приёмника.

    Своя копия правил («ноль не цена», «акция не заменяет цену») разошлась бы с
    оригиналом в первый же день, и два пути дали бы разные цифры из одного файла.
    """
    summary = collector.accept(report(items=[ITEM, {"name": "Без артикула", "price": 10}]))

    assert summary["prices_saved"] == 1
    assert summary["prices_skipped"] == 1, "непонятая позиция считается, а не пропадает"

    store = repo.get_store("pyaterochka")
    product = repo.upsert_store_product(store.id, "4306830", ITEM["name"])
    snap = repo.latest_price(product)
    assert snap["price"] == 176.99
    assert snap["fetched_at"] == "2026-09-16T23:40:00"


def test_a_broken_report_leaves_nothing_behind(db):
    """Отказ на любой части отменяет весь отчёт — до первой записи.

    Магнит приложение спрашивает само, пакет цен по нему пакет цен не примет. Но
    в том же отчёте лежат честные купоны и признак входа, и вот их-то и нельзя
    принять наполовину: человек увидел бы «подключено», не получив цен.
    """
    with pytest.raises(ValueError, match="magnit"):
        collector.accept(report(store="magnit"))

    assert collector.coupons("magnit") == []
    assert repo.get_setting(collector.COUPONS_KEY) is None
    assert store_accounts.connection("magnit").connected is False


def test_unknown_store_is_refused(db):
    with pytest.raises(ValueError, match="perekrestok"):
        collector.accept(report(store="perekrestok"))


def test_a_text_that_is_not_json_is_refused(db):
    with pytest.raises(ValueError, match="JSON"):
        collector.accept("сборщик прислал не то")


def test_lists_that_are_not_lists_are_refused(db):
    """Кривой отчёт отвечает словами, а не падением где-то в глубине разбора."""
    with pytest.raises(ValueError, match="items"):
        collector.accept(report(items="одна позиция"))
    with pytest.raises(ValueError, match="coupons"):
        collector.accept(report(coupons={"id": "x"}))
    with pytest.raises(ValueError, match="cart_result"):
        collector.accept(report(cart_result="положил"))


def test_a_time_that_is_not_a_time_is_refused(db):
    """Неразобранное время ушло бы в историю цен строкой и перемешало бы её порядок."""
    with pytest.raises(ValueError, match="collected_at"):
        collector.accept(report(collected_at="вчера вечером"))


def test_utc_time_from_the_browser_becomes_local_time(db):
    """Браузер отдаёт время в UTC, база живёт по местному.

    Сложи их вместе — и снимок, собранный минуту назад, окажется «из будущего» на
    три часа, то есть свежее сегодняшнего.
    """
    stamp = collector.accept(report(collected_at="2026-09-16T20:40:00.000Z"))["collected_at"]

    assert "Z" not in stamp and "+" not in stamp
    assert (datetime.fromisoformat(stamp)
            == datetime.fromisoformat("2026-09-16T20:40:00+00:00").astimezone().replace(tzinfo=None))


# ---------- подключение ----------
def test_connection_gets_what_came_not_what_was_promised(db):
    """Справочник обещает Магниту корзину; сегодня её не принесли — значит её нет.

    Иначе наряд на корзину уедет в сеть, которая его сегодня не исполняет, и
    человек узнает об этом, не найдя товары у себя в магазине.
    """
    collector.accept(report(store="magnit", items=None, coupons=None,
                            cart_result=None, gives=["prices"]))

    assert store_accounts.can("magnit", store_accounts.PRICES)
    assert not store_accounts.can("magnit", store_accounts.CART)
    assert store_accounts.ABILITIES["magnit"].gives.count(store_accounts.CART) == 1, \
        "справочник по-прежнему обещает корзину — но обещание не факт"


def test_what_arrived_counts_even_if_the_collector_forgot_to_name_it(db):
    """Принесённое — доказательство. Купоны в отчёте значат, что купоны сеть отдаёт."""
    summary = collector.accept(report(gives=[]))

    assert set(summary["gives"]) == {"prices", "coupons", "cart"}
    assert store_accounts.can("pyaterochka", store_accounts.COUPONS)


def test_a_logout_puts_the_connection_out(db):
    """Человек вышел из аккаунта — врать, что он подключён, нельзя."""
    collector.accept(report())
    assert store_accounts.connection("pyaterochka").connected is True

    collector.accept(report(logged_in=False, items=None, coupons=None,
                            cart_result=None, gives=[]))

    assert store_accounts.connection("pyaterochka").connected is False


def test_silence_about_the_login_does_not_touch_the_connection(db):
    """Молчание — не «вышел». Отчёт со страницы каталога про вход ничего не знает.

    Гасить подключение от каждого такого отчёта значило бы выключать человеку
    личные цены на ровном месте.
    """
    collector.accept(report())
    quiet = report()
    quiet.pop("logged_in")

    collector.accept(quiet)

    assert store_accounts.connection("pyaterochka").connected is True


def test_a_login_written_as_text_is_refused(db):
    """Строка «false» в Python истинна — подключение зажглось бы ровно наоборот."""
    with pytest.raises(ValueError, match="logged_in"):
        collector.accept(report(logged_in="false"))


def test_a_full_card_number_never_lands_in_the_connection(db):
    """Подпись аккаунта показывать надо, платёжный реквизит — нет.

    Магазин подписывает вошедшего как умеет, и однажды подпишет полным номером
    карты. Хранить его мы не станем даже по чужой неосторожности.
    """
    collector.accept(report(account="Карта 4276123456781234"))

    account = store_accounts.connection("pyaterochka").account
    assert account == "Карта •••1234"
    assert "4276" not in everything_written()


def test_connection_stores_loyalty_points(db):
    summary = collector.accept(report(points=150.5))
    assert summary["points"] == 150.5
    assert store_accounts.connection("pyaterochka").points == 150.5


# ---------- купоны ----------
def test_coupons_are_kept_with_the_time_of_collection(db):
    """Без отметки времени человек не отличит «купонов нет» от «давно не смотрели»."""
    collector.accept(report())

    live = collector.coupons("pyaterochka")
    assert [c["title"] for c in live] == ["-30% на молоко Простоквашино"]
    assert live[0]["value"] == "-30%"
    assert collector.coupons_updated_at("pyaterochka") == "2026-09-16T23:40:00"


def test_expired_coupons_are_hidden_but_not_erased(db):
    """Показать просроченный — обещать скидку, которой нет. Стереть — потерять знание."""
    collector.accept(report(coupons=[COUPON, OLD_COUPON]))

    assert [c["id"] for c in collector.coupons("pyaterochka")] == ["x5-30-milk"]
    assert "x5-15-bread" in (repo.get_setting(collector.COUPONS_KEY) or ""), \
        "истёкший купон остаётся историей, а не исчезает молча"


def test_a_coupon_is_hidden_only_after_its_last_day(db):
    """День окончания — ещё рабочий день купона, а не первый день без него."""
    collector.accept(report(coupons=[{**COUPON, "ends_at": "2026-09-30"}]))

    assert collector.coupons("pyaterochka", today="2026-09-30")
    assert collector.coupons("pyaterochka", today="2026-10-01") == []


def test_a_new_report_replaces_the_live_coupons(db):
    """Потраченный купон исчез из аккаунта — и с экрана обязан исчезнуть тоже."""
    collector.accept(report(coupons=[COUPON, {"id": "x5-50-tea", "title": "-50% на чай",
                                              "ends_at": "2099-10-01"}]))
    collector.accept(report(coupons=[COUPON]))

    assert [c["id"] for c in collector.coupons("pyaterochka")] == ["x5-30-milk"]


def test_a_report_without_coupons_does_not_wipe_them(db):
    """Отчёт о ценах в кабинет не заходил — значит про купоны он ничего не сказал."""
    collector.accept(report())
    collector.accept(report(coupons=None, cart_result=None, gives=["prices"]))

    assert len(collector.coupons("pyaterochka")) == 1


def test_an_empty_list_with_a_promise_means_there_are_none(db):
    """«Смотрел, их нет» — это ответ, и он должен доехать до экрана."""
    collector.accept(report())
    collector.accept(report(coupons=[], gives=["coupons"]))

    assert collector.coupons("pyaterochka") == []


def test_a_coupon_without_a_title_is_counted_not_dropped(db):
    """«-30%» неизвестно на что человек либо не поймёт, либо поймёт неправильно.

    Считаем такие отдельно: пропавшие названия — первый признак сменившейся вёрстки.
    """
    summary = collector.accept(report(coupons=[COUPON, {"id": "x", "value": "-30%"}]))

    assert summary["coupons_saved"] == 1 and summary["coupons_skipped"] == 1


def test_coupons_of_one_store_do_not_leak_into_another(db):
    """Купон Пятёрочки в Магните — это лишние деньги в расчёте и обман на кассе."""
    collector.accept(report())
    collector.accept(report(store="magnit", items=None, cart_result=None,
                            gives=["coupons"],
                            coupons=[{"id": "m-20-tea", "title": "-20% на чай",
                                      "ends_at": "2099-10-01"}]))

    assert [c["id"] for c in collector.coupons("magnit")] == ["m-20-tea"]
    assert [c["id"] for c in collector.coupons("pyaterochka")] == ["x5-30-milk"]


# ---------- корзина ----------
def test_a_failed_line_without_a_reason_is_still_a_failed_line(db):
    """Причина дороже всего, но её отсутствие не повод терять сам факт «не легло»."""
    summary = collector.accept(report(cart_result={"ok": [], "failed": ["77"]}))

    assert summary["cart_ok"] == 0
    assert summary["cart_failed"] == [{"sku": "77", "why": None}]


def test_the_cart_contents_are_not_copied_into_the_base(db):
    """Что ЛЕГЛО в корзину — не наше знание, и копии этого списка в базе нет.

    Корзину человек правит руками у себя в магазине, и наша копия «там лежит вот
    это» разойдётся с ней молча: заметить расхождение будет нечем. Поэтому
    артикулы лёгших позиций не сохраняются нигде — ни числом, ни списком.

    ГРАНИЦА, КОТОРУЮ ЭТОТ СТОРОЖ ПРОВОДИТ. Запрещено хранить СОСТОЯНИЕ чужой
    корзины; разрешено хранить наше СОБЫТИЕ — «тогда-то мы положили столько-то, а
    вот это не легло, потому что нет в наличии». Второе верно навсегда и нужно
    человеку: он нажал «Передать корзину» в приложении и должен узнать там же,
    чем это кончилось, а не в журнале сервера, которого он не читает
    (см. collector.HANDOVER_KEY и экран «Кабинеты»).
    """
    # Артикул нарочно свой, ни на что не похожий: 4306830 из report() — это ещё и
    # артикул позиции в ценах, и сторож ловил бы его там, а не в корзине.
    collector.accept(report(cart_result={"ok": ["ЛЁГ-В-КОРЗИНУ-1"],
                                         "failed": [{"sku": "77", "why": "нет в наличии"}]}))

    assert "ЛЁГ-В-КОРЗИНУ-1" not in everything_written(),         "артикул лёгшей позиции сохранён — это копия чужой корзины, она разойдётся с настоящей"


def test_the_handover_report_survives_for_the_screen(db):
    """А вот отчёт о самой передаче хранится: без него нажатие кнопки остаётся без ответа."""
    collector.accept(report())

    last = collector.handover_report("pyaterochka")
    assert last is not None, "отчёт о передаче не сохранён — экран «Кабинеты» покажет пустоту"
    assert last["ok"] == 1
    assert last["at"] == "2026-09-16T23:40:00"
    assert last["failed"] == [{"sku": "77", "why": "нет в наличии"}],         "причина неудачи потеряна — человеку придётся пересобирать корзину вслепую"


# ---------- секреты ----------
def test_no_secret_is_ever_stored(db):
    """Ни токена, ни куки, ни пароля, ни кода из СМС — на любой глубине отчёта.

    Сторож грубый нарочно, по образцу такого же в tests/test_store_accounts.py:
    если однажды в расширении появится лишняя строка и чужой секрет поедет к нам,
    тест покраснеет раньше, чем это уедет на сервер. Отдельно проверяется ссылка:
    …?access_token=… осталась бы в базе цен навсегда и утекла бы с любой выгрузкой.
    """
    collector.accept(report(
        token="секрет-один",
        session={"cookie": "секрет-два"},
        account="Карта X5 •••1234",
        items=[{**ITEM, "password": "секрет-три",
                "url": "https://5ka.ru/product/azu-4306830?access_token=секрет-четыре&utm=ok"}],
        coupons=[{**COUPON, "otp": "секрет-пять", "meta": {"jwt": "секрет-шесть"}}]))

    dump = everything_written().lower()
    for secret in ("секрет-один", "секрет-два", "секрет-три", "секрет-четыре",
                   "секрет-пять", "секрет-шесть"):
        assert secret not in dump, f"в базе оказался секрет: {secret}"

    saved = json.loads(repo.get_setting(collector.COUPONS_KEY) or "{}")
    forbidden = {"token", "access_token", "refresh_token", "password", "code",
                 "jwt", "cookie", "otp", "secret"}
    for record in saved.values():
        for coupon in record["items"]:
            assert not (forbidden & set(coupon)), f"в купоне оказался секрет: {coupon}"

    assert "utm=ok" in dump, "чистится параметр с ключом, а не вся ссылка на товар"


def test_secret_field_names_go_to_the_journal_without_their_values(db, caplog):
    """След остаться обязан: иначе никто не узнает, что расширение начало слать лишнее.

    Но в журнал идут только имена полей. Секрет в логе хранится ровно столько же,
    сколько в базе, — только найти его там труднее.
    """
    with caplog.at_level(logging.WARNING, logger="app.collector"):
        summary = collector.accept(report(token="секрет-один"))

    written = "\n".join(record.getMessage() for record in caplog.records)
    assert "token" in written
    assert "секрет-один" not in written
    assert summary["dropped"] == ["token"]


def test_an_honest_field_with_code_in_its_name_survives(db):
    """Сторож не должен съедать store_code: без кода точки цены не к чему привязать."""
    summary = collector.accept(report(store_code="5N123"))

    assert summary["dropped"] == []
    assert summary["prices_saved"] == 1
