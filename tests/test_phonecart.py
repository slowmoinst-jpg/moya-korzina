"""Корзина руками телефона: наряд для приложения и приём его отчёта.

Стережётся три вещи. Телефон получает ВСЮ корзину на артикулах сети и те же правила
кнопок, что у серверной передачи, а не свои. Отчёт хранит только суммы и имена
полей: адрес, телефон и имя из корзины сети не доезжают до базы, даже если их
прислали. И без входа в «Мою корзину» нет ни наряда, ни приёма.
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import phonecart, repo, users  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import cart, signals  # noqa: E402

PHONE = "79990000088"


@pytest.fixture(autouse=True)
def clean_tried():
    phonecart._TRIED.clear()
    phonecart._ASKING.clear()
    yield
    phonecart._TRIED.clear()
    phonecart._ASKING.clear()


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Рабочее место с корзиной «молоко + сыр»: Магниту знакомо всё, Пятёрочке ничего."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    milk = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 930 мл", unit="pcs"))
    cheese = repo.upsert_product(Product(id=None, name="Сыр Российский 45%", unit="kg"))
    shop = repo.get_store("magnit")
    milk_sp = repo.upsert_store_product(shop.id, "m-1", "Молоко Простоквашино 930 мл",
                                        url="https://magnit.ru/product/1")
    cheese_sp = repo.upsert_store_product(shop.id, "m-2", "Сыр Российский 45%",
                                          url="https://magnit.ru/product/2")
    repo.confirm_mapping(milk, milk_sp, confirmed=True)
    repo.confirm_mapping(cheese, cheese_sp, confirmed=True)
    repo.save_price(milk_sp, 89.0)
    repo.save_price(cheese_sp, 240.0)
    basket_id = repo.create_basket("Неделя 42")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, cheese, 0.4)
    users.deactivate()

    # Поиск самой сети — запрос в сеть; здесь он ничего не находит, если тест не скажет иного.
    from app import matcher
    monkeypatch.setattr(matcher, "auto_match", lambda ids, codes, **kw: {"auto": 0, "need_review": []})

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application
    users.deactivate()


def logged_in(web):
    client = web.test_client()
    from conftest import sign_in

    sign_in(client, PHONE)
    return client


def test_phone_gets_the_whole_cart_on_the_chains_own_cards(web):
    answer = logged_in(web).post("/api/device/cartplan", json={"store": "magnit"})

    assert answer.status_code == 200, answer.get_json()
    data = answer.get_json()
    urls = {line["url"] for line in data["lines"]}
    assert urls == {"https://magnit.ru/product/1", "https://magnit.ru/product/2"}
    milk = next(line for line in data["lines"] if line["sku"] == "m-1")
    assert milk["clicks"] == 2, "две пачки молока — два нажатия"
    assert all(line["clicks"] >= 1 for line in data["lines"])
    assert data["total"] > 0, "наш расчёт рядом с итогом сети — чтобы было с чем сравнить"


def test_the_buttons_rules_are_the_servers_own(web):
    data = logged_in(web).post("/api/device/cartplan", json={"store": "dixy"}).get_json()

    rules = data["rules"]
    assert rules["add_name"] == signals.ADD_NAME
    assert rules["plus_name"] == signals.PLUS_NAME
    assert rules["gone"] == signals.GONE
    assert rules["selectors"] == list(signals.ADD_TO_CART["dixy"])
    assert rules["below"] == cart.BUYBOX_BELOW
    assert rules["hosts"] == ["dixy.ru"]
    assert rules["request_pause_ms"] >= 300, "повтор запроса сети — не чаще rate_limit_rps"
    assert rules["check_signal"] == signals.CHECK and "я не робот" in rules["check_signal"]
    assert isinstance(rules["check_pause_ms"], int) and rules["check_pause_ms"] >= 1000, "пауза проверки мала"


def test_a_chain_without_our_cards_says_so_instead_of_guessing(web):
    data = logged_in(web).post("/api/device/cartplan", json={"store": "pyaterochka"}).get_json()

    assert data["lines"] == []
    assert len(data["unknown"]) == 2
    assert data["note"], "пустой наряд без слов выглядит поломкой"


def test_unknown_chain_is_refused(web):
    answer = logged_in(web).post("/api/device/cartplan", json={"store": "kuper"})
    assert answer.status_code == 400


def test_stranger_gets_neither_plan_nor_report(web):
    client = web.test_client()
    assert client.post("/api/device/cartplan", json={"store": "magnit"}).status_code in (401, 403)
    assert client.post("/api/device/cartreport", json={"store": "magnit"}).status_code in (401, 403)


REPORT = {
    "store": "magnit",
    "app_version": "1.4",
    "items": [
        {"name": "Молоко Простоквашино 930 мл", "sku": "m-1", "ok": True, "put": 2, "times": 2},
        {"name": "Сыр Российский 45%", "sku": "m-2", "ok": False,
         "why": "на карточке не нашлось кнопки «в корзину»", "put": 0, "times": 2},
    ],
    "cart_count": 1,
    "cart_page": "/cart/5502aed9e3b63cd2e222822c",
    "our_total": 341.0,
    "page_totals": [
        {"kind": "total", "label": "Итого", "amount": "1 178,90"},
        {"kind": "discount", "label": "Скидка", "amount": 62.5},
        {"kind": "delivery", "label": "Доставка на Невский 28", "amount": 0},
        {"kind": "address", "label": "Невский 28", "amount": 28},
        {"kind": "total", "label": "Итого", "amount": "не число"},
    ],
    "api_totals": {
        "/webgate/v1/carts/lite": {
            "totalPrice": 1178.9, "totalDiscount": 62.5, "deliveryPrice": 0,
            "customerPhone": 79991234567, "address.house": 28, "comment": "у двери",
        },
    },
}


def test_report_keeps_sums_and_drops_what_points_to_a_person(web):
    answer = logged_in(web).post("/api/device/cartreport", json=REPORT)

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == {"ok": True, "store": "magnit", "landed": 1, "planned": 2,
                                 "total": 1178.9, "network": None, "bound": 0}
    users.open_workspace(PHONE)
    saved = phonecart.last("magnit")
    text = str(saved)
    for leaked in ("79991234567", "Невский", "у двери"):
        assert leaked not in text, f"в отчёт попало «{leaked}»"
    assert saved["api_totals"] == {"/webgate/v1/carts/lite": {
        "totalPrice": 1178.9, "totalDiscount": 62.5, "deliveryPrice": 0.0}}
    kinds = [row["kind"] for row in saved["page_totals"]]
    assert kinds == ["total", "discount", "delivery"], "незнакомый вид и не-число отброшены"
    assert saved["page_totals"][2]["label"] == "", "подпись с цифрами похожа на адрес — не храним"
    assert saved["cart_page"] == "/cart/{id}"
    assert saved["items"][1]["why"].startswith("на карточке не нашлось")


def test_a_short_search_note_survives_the_report(web):
    """Пометка «нашёл по короткому запросу» доезжает до сохранённого пункта отчёта.

    Телефон кладёт её в item (MainActivity.lineDone), но приём собирает пункт по
    белому списку полей и раньше её отбрасывал. Пустое не пишем, длинное режем:
    поле не должно ни теряться, ни растить отчёт пустыми ключами.
    """
    note = "нашёл по короткому запросу"
    long_note = "нашёл по короткому запросу " * 20          # 540 символов
    report = dict(REPORT, items=[
        dict(REPORT["items"][0], note=note),
        {"name": "Сыр Российский 45%", "sku": "m-2", "ok": False, "why": "нет в наличии",
         "put": 0, "times": 1, "note": ""},
        {"name": "Кефир Простоквашино 1%", "sku": "m-3", "ok": True, "note": "   "},
        {"name": "Хлеб Бородинский 400 г", "sku": "m-4", "ok": True, "note": long_note},
        {"name": "Молоко 2,5%", "sku": "m-5", "ok": True},
    ])

    answer = logged_in(web).post("/api/device/cartreport", json=report)
    assert answer.status_code == 200, answer.get_json()

    users.open_workspace(PHONE)
    saved = phonecart.last("magnit")
    items = saved["items"]

    first = items[0]
    assert first["note"] == note, "пометка «нашёл по короткому запросу» потерялась"
    assert (first["name"], first["sku"], first["put"]) == \
        ("Молоко Простоквашино 930 мл", "m-1", 2), "другие поля пункта не изменились"
    assert "note" not in items[1], "пустая пометка записана пустым ключом"
    assert "note" not in items[2], "пометка из пробелов записана"
    assert items[3]["note"] == long_note.strip()[:200], \
        "длинная пометка не обрезана до 200 символов"
    assert "note" not in items[4], "пункт без пометки обзавёлся пустым полем"


def test_the_stock_phone_saw_reaches_the_saved_report(web):
    """Наличие с карточки (Z7h) — в сохранённый пункт отчёта, и только настоящим bool.

    Телефон присылает `in_stock` отдельно от `ok`: «Нет в наличии/Больше нет» на карточке
    даёт false и тогда, когда позиция не легла по другой причине («кнопки не нашлось»).
    Строка «true», число 1 и null наличием не считаются: иначе сервер выдумал бы то, чего
    телефон не говорил. У старых отчётов поля нет — и ключа быть не должно.
    """
    report = dict(REPORT, items=[
        {"name": "Молоко Простоквашино 930 мл", "sku": "m-1", "ok": True, "in_stock": True},
        {"name": "Сыр Российский 45%", "sku": "m-2", "ok": False, "why": "кнопки не нашлось",
         "in_stock": False},
        {"name": "Кефир Простоквашино 1%", "sku": "m-3", "ok": True, "in_stock": "true"},
        {"name": "Хлеб Бородинский 400 г", "sku": "m-4", "ok": True, "in_stock": 1},
        {"name": "Молоко 2,5%", "sku": "m-5", "ok": True, "in_stock": None},
        {"name": "Чай чёрный 100 г", "sku": "m-6", "ok": True},
    ])

    answer = logged_in(web).post("/api/device/cartreport", json=report)
    assert answer.status_code == 200, answer.get_json()

    users.open_workspace(PHONE)
    items = phonecart.last("magnit")["items"]
    users.deactivate()

    assert items[0]["in_stock"] is True
    assert items[1]["in_stock"] is False
    assert items[1]["ok"] is False and items[1]["why"] == "кнопки не нашлось"
    for item in items[2:]:
        assert "in_stock" not in item, f"не bool стало наличием: {item.get('in_stock')!r}"


def test_magnit_answer_is_shown_but_not_called_the_network_total(web):
    """Ответ carts/lite несёт сумму в копейках, но со страницей корзины она не сверена.

    Второй замер 17.10.2026: товаров стало 11, а сумма осталась той же, что при пяти.
    Пока сверки нет, число отдаётся как есть, а итогом сети не называется.
    """
    report = dict(REPORT, page_totals=[], api_totals={"/webgate/v1/carts/lite": {
        "totalBasePrice": 41999, "totalFinalPrice": 37999, "totalGoodsCount": 5}})

    answer = logged_in(web).post("/api/device/cartreport", json=report).get_json()

    assert answer["total"] is None
    assert answer["network"] == {"total": 379.99, "base": 419.99, "discount": 40.0, "count": 5,
                                 "verified": False}


def test_magnit_checkout_preview_is_the_verified_network_total(web):
    """Замер 17.10.2026: checkout/preview совпал со страницей корзины до копейки."""
    from app import cartrun

    report = dict(REPORT, page_totals=[], cart_count=3, api_totals={
        "/webgate/v1/carts/lite": {"totalBasePrice": 37999, "totalFinalPrice": 37999},
        "/webgate/v1/checkout/preview": {
            "deliveryInfo.fee.fee": 288, "summary.itemsCount": 3,
            "summary.itemsTotalSalePrice": 41297, "summary.totalBasePrice": 49297,
            "summary.totalFinalPrice": 51097},
    })
    answer = logged_in(web).post("/api/device/cartreport", json=report).get_json()

    assert answer["total"] == 510.97
    assert answer["network"] == {"total": 510.97, "base": 492.97, "count": 3,
                                 "verified": True, "discount": 80.0}
    users.open_workspace(PHONE)
    row = cartrun.get("magnit")
    assert (row.total.sum, row.total.goods, row.total.discount, row.total.count) == \
        (510.97, 492.97, 80.0, 3)


def test_pyaterochka_page_sums_with_glued_kopecks_are_not_a_total(web):
    """Замер 11.11.2026: страница корзины Пятёрочки пишет «1 541» и «87» слитно — телефон прочитал 54187.

    Ответ сайта о заказе сторож приложения хранит между сборками: его свежесть не проверить, итогом не берём.
    """
    from app import cartrun

    report = dict(REPORT, store="pyaterochka", cart_page="/cart/", cart_count=6,
                  page_totals=[{"kind": "total", "label": "Итого", "amount": 54187.0},
                               {"kind": "delivery", "label": "Доставка", "amount": 9900.0}],
                  api_totals={"/api/orders/v11/orders/12345678/": {
                      "basket.total_sum": 1541.87, "basket.final_sum": 1555.87, "basket.items_count": 6}})
    answer = logged_in(web).post("/api/device/cartreport", json=report).get_json()

    assert (answer["total"], answer["network"]) == (None, None)
    users.open_workspace(PHONE)
    assert phonecart.last("pyaterochka")["page_totals"] == []
    assert cartrun.get("pyaterochka").total is None

    # Старое приложение адреса страницы не шлёт — это тоже разбор страницы.
    old = {key: value for key, value in report.items() if key != "cart_page"}
    assert logged_in(web).post("/api/device/cartreport", json=old).get_json()["total"] is None

    # Отчёт запросами cartapi.js несёт адрес ответа: его суммы — числа сети, а не разбор страницы.
    by_requests = dict(report, cart_page="/api/orders/v10/orders",
                       page_totals=[{"kind": "total", "label": "Итого", "amount": 1410.88},
                                    {"kind": "goods", "label": "Товары", "amount": 1351.88, "base": 1396.88}],
                       api_totals={"/api/orders/v10/orders": {
                           "amount": 1410.88, "goods": 1351.88, "base": 1396.88, "discount": 45,
                           "delivery": 0, "count": 5}})
    answer = logged_in(web).post("/api/device/cartreport", json=by_requests).get_json()
    assert (answer["total"], answer["network"]) == (1410.88, None)
    users.open_workspace(PHONE)
    total = cartrun.get("pyaterochka").total
    assert (total.sum, total.goods, total.source) == (1410.88, 1396.88, cartrun.NETWORK)

    # Другим сетям страница корзины по-прежнему даёт итог.
    magnit = dict(REPORT, api_totals={}, page_totals=[{"kind": "total", "label": "Итого", "amount": 1600.4}])
    assert logged_in(web).post("/api/device/cartreport", json=magnit).get_json()["total"] == 1600.4


def test_empty_plan_says_which_basket_was_looked_up(web, monkeypatch):
    """«Класть нечего» без слов не читается: наряд называет корзину и что искал."""
    from app.catalog import refresh

    users.open_workspace(PHONE)
    tea = repo.upsert_product(Product(id=None, name="ЧАЙ ЧЁРН.ЛИСТ.100Г", unit="pcs"))
    basket_id = repo.create_basket("Как в прошлый раз")
    repo.set_basket_item(basket_id, tea, 1.0)
    users.deactivate()
    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)

    data = logged_in(web).post("/api/device/cartplan",
                               json={"store": "magnit", "basket": basket_id}).get_json()

    assert data["lines"] == []
    assert "Корзина «Как в прошлый раз», позиций 1" in data["note"]
    assert "незнакомы 1, в её каталоге нашлось 0, поиском самой сети — 0 из 1" in data["note"]


def test_a_receipt_line_missing_in_the_catalog_is_found_by_the_chains_search(web, monkeypatch):
    """Строку чека каталог не узнал — её находит поиск самой сети тем же автосопоставлением."""
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    tea = repo.upsert_product(Product(id=None, name="ЧАЙ ЧЁРН.ЛИСТ.100Г", unit="pcs"))
    basket_id = repo.create_basket("Из чека")
    repo.set_basket_item(basket_id, tea, 1.0)
    users.deactivate()
    asked = []

    def auto_match(ids, codes, **kw):
        asked.append((list(ids), list(codes)))
        shop = repo.get_store("magnit")
        sp = repo.upsert_store_product(shop.id, "m-9", "Чай чёрный листовой 100 г",
                                       url="https://magnit.ru/product/9")
        repo.confirm_mapping(ids[0], sp, confirmed=True)
        return {"auto": 1, "need_review": []}

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(matcher, "auto_match", auto_match)

    data = logged_in(web).post("/api/device/cartplan",
                               json={"store": "magnit", "basket": basket_id}).get_json()

    assert asked == [([tea], ["magnit"])], "искали поиском только эту сеть и только эту позицию"
    assert [line["url"] for line in data["lines"]] == ["https://magnit.ru/product/9"]
    assert data["linked"] == 1


def test_phone_report_lands_in_the_carts_screen_row(web):
    """Строка сети на «Сборке корзин» одна для сервера и телефона — отчёт 1.4 ложится в неё."""
    from app import cartrun

    client = logged_in(web)
    unverified = dict(REPORT, page_totals=[], api_totals={"/webgate/v1/carts/lite": {
        "totalBasePrice": 41999, "totalFinalPrice": 37999, "totalGoodsCount": 5}})
    client.post("/api/device/cartreport", json=unverified)

    users.open_workspace(PHONE)
    row = cartrun.get("magnit")
    assert row.by == cartrun.DEVICE
    assert row.landed == ["Молоко Простоквашино 930 мл"]
    assert [item["name"] for item in row.missing] == ["Сыр Российский 45%"]
    assert row.total is None, "несверенное число из ответа сети итогом не подаётся"
    users.deactivate()

    client.post("/api/device/cartreport", json=REPORT)      # со строками «Итого» страницы
    users.open_workspace(PHONE)
    row = cartrun.get("magnit")
    assert row.total.source == cartrun.NETWORK
    assert (row.total.sum, row.total.discount, row.total.delivery) == (1178.9, 62.5, 0.0)


def test_items_known_only_from_receipts_are_found_in_the_catalog(web, monkeypatch):
    """Товар из чека не связан с Магнитом — наряд ищет его в едином каталоге, а не молчит."""
    from app.catalog import refresh

    users.open_workspace(PHONE)
    kefir = repo.upsert_product(Product(id=None, name="Кефир Простоквашино 1% 930 мл", unit="pcs"))
    bread = repo.upsert_product(Product(id=None, name="Хлеб Бородинский 400 г", unit="pcs"))
    basket_id = repo.create_basket("Из чеков")
    repo.set_basket_item(basket_id, kefir, 1.0)
    repo.set_basket_item(basket_id, bread, 1.0)
    users.deactivate()

    items = {"Кефир Простоквашино 1% 930 мл": {"id": 7, "chains": [{"chain": "magnit"}]},
             "Хлеб Бородинский 400 г": {"id": 8, "chains": [{"chain": "pyaterochka"}]}}
    adopted = []

    def adopt(item_id, product_id=None):
        adopted.append(item_id)
        shop = repo.get_store("magnit")
        sp = repo.upsert_store_product(shop.id, "m-7", "Кефир Простоквашино 1% 930 мл",
                                       url="https://magnit.ru/product/7")
        repo.confirm_mapping(product_id, sp, confirmed=True)
        return product_id

    monkeypatch.setattr(refresh, "resolve", lambda name, *a, **kw: items.get(name))
    monkeypatch.setattr(refresh, "adopt", adopt)

    # Корзины созданы в одну секунду, поэтому «последняя» здесь названа номером.
    data = logged_in(web).post("/api/device/cartplan",
                               json={"store": "magnit", "basket": basket_id}).get_json()

    assert adopted == [7], "хлеб в каталоге Магнита не найден — его и не привязываем"
    assert data["linked"] == 1
    assert [line["url"] for line in data["lines"]] == ["https://magnit.ru/product/7"]
    assert data["unknown"] == ["Хлеб Бородинский 400 г"]


def test_magnit_cart_page_gives_the_network_total_with_discount(web):
    """Страница корзины Магнита, замер 17.10.2026: «Товары на сумму 1 748,98 ₽ 1 561,4 ₽»."""
    from app import cartrun

    report = dict(REPORT, api_totals={}, cart_count=11, page_totals=[
        {"kind": "goods", "label": "Товары на сумму", "amount": 1561.4, "base": 1748.98},
        {"kind": "delivery", "label": "Доставка", "amount": 0},
        {"kind": "service", "label": "Сборка и упаковка", "amount": 39},
        {"kind": "total", "label": "Итого", "amount": 1600.4},
    ])
    answer = logged_in(web).post("/api/device/cartreport", json=report).get_json()

    assert answer["total"] == 1600.4
    users.open_workspace(PHONE)
    row = cartrun.get("magnit")
    assert row.total.source == cartrun.NETWORK
    assert (row.total.sum, row.total.goods, row.total.discount, row.total.delivery, row.total.count) \
        == (1600.4, 1748.98, 187.58, 0.0, 11)


def test_a_goods_only_page_is_not_a_network_total(web):
    """Строки итога нет, есть «Товары»: это сумма товаров без скидок и сборки, а не «к оплате».

    Итогом сети она не подаётся: по ней app/netprice.py назначал бы цену места, и место
    выглядело бы дороже или дешевле настоящего итога на скидку и сборку.
    """
    from app import cartrun

    report = dict(REPORT, api_totals={}, cart_count=4, page_totals=[
        {"kind": "goods", "label": "4 товара", "amount": 862.35},
        {"kind": "discount", "label": "Скидка", "amount": 85},
        {"kind": "service", "label": "Сборка и упаковка", "amount": 49},
    ])
    logged_in(web).post("/api/device/cartreport", json=report)

    users.open_workspace(PHONE)
    total = cartrun.get("magnit").total
    assert total.sum == 862.35 and total.source == cartrun.OURS


@pytest.mark.parametrize("flipped", [False, True])
def test_the_order_button_is_not_the_delivery_line(web, flipped):
    """Перекрёсток, замер на эмуляторе 23.10.2026: итог стоит на кнопке «Оформить заказ».

    Её строка «826,35 ₽ Доставка сегодня Оформить заказ» страница читает ещё и как
    доставку с суммой итога. Доставка не бывает не меньше итога, а порядок строк у каждой
    сети свой, поэтому берётся та строка доставки, что меньше итога, в любом порядке.
    """
    from app import cartrun

    rows = [
        {"kind": "goods", "label": "4 товара", "amount": 862.35},
        {"kind": "discount", "label": "Скидка", "amount": 85},
        {"kind": "delivery", "label": "Доставка", "amount": 0},
        {"kind": "service", "label": "Сборка и упаковка", "amount": 49},
        {"kind": "delivery", "label": "Доставка сегодня", "amount": 826.35},
        {"kind": "total", "label": "Оформить заказ", "amount": 826.35},
    ]
    report = dict(REPORT, api_totals={}, cart_count=4,
                  page_totals=rows[::-1] if flipped else rows)
    answer = logged_in(web).post("/api/device/cartreport", json=report).get_json()

    assert answer["total"] == 826.35
    users.open_workspace(PHONE)
    total = cartrun.get("magnit").total
    assert (total.sum, total.delivery, total.source) == (826.35, 0.0, cartrun.NETWORK)


def test_a_new_report_replaces_the_old_one(web):
    client = logged_in(web)
    client.post("/api/device/cartreport", json=REPORT)
    client.post("/api/device/cartreport", json=dict(REPORT, items=REPORT["items"][:1],
                                                     page_totals=[]))

    users.open_workspace(PHONE)
    saved = phonecart.last("magnit")
    assert saved["planned"] == 1 and saved["landed"] == 1
    assert phonecart.total_of(saved) is None


def test_the_report_keeps_the_time_since_the_touch(web, caplog):
    """Цель владельца — правка во всех сетях за 5 с: время от касания едет в отчёте (приложение 1.29)."""
    client = logged_in(web)
    with caplog.at_level("INFO", logger=phonecart.log.name):
        client.post("/api/device/cartreport", json=dict(REPORT, lag_ms=3400))
    users.open_workspace(PHONE)
    assert phonecart.last("magnit")["lag_ms"] == 3400
    assert "от касания 3.4 с" in caplog.text
    client.post("/api/device/cartreport", json=REPORT)     # прежние версии времени не шлют
    users.open_workspace(PHONE)
    assert phonecart.last("magnit")["lag_ms"] is None


def test_edit_times_are_kept_in_a_journal(web, monkeypatch):
    """Время правки по каждой сети переживает выкладку: журнал лежит в рабочем месте, а строки
    журнала сервера стирает пересоздание контейнера. Устройство — чтобы отличить телефон от эмулятора."""
    client = logged_in(web)
    phone_ua = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/UQ1A.240205; wv) KorzinaApp/1.33"}
    client.post("/api/device/cartreport", json=dict(REPORT, lag_ms=3400, app_version="1.35"), headers=phone_ua)
    client.post("/api/device/cartreport", json=REPORT)                     # без времени — не в журнал
    client.post("/api/device/cartreport", json=dict(REPORT, store="dixy", lag_ms=1200))
    users.open_workspace(PHONE)
    rows = phonecart.lags()
    assert [row[1:] for row in rows] == [["magnit", 1, 2, 3400, "1.35", "Pixel 8"],
                                         ["dixy", 1, 2, 1200, "1.4", ""]]
    assert all(row[0][:2] == "20" for row in rows), "первым идёт время отчёта"

    monkeypatch.setattr(phonecart, "LAGS_KEEP", 2)
    client.post("/api/device/cartreport", json=dict(REPORT, lag_ms=900))
    users.open_workspace(PHONE)
    assert [row[4] for row in phonecart.lags()] == [1200, 900], "журнал держит только последние строки"

    # Испорченный журнал и число из сотен цифр приём отчёта не ломают: журнал начинается заново.
    repo.set_setting(phonecart.LAGS_KEY, "{не журнал")
    assert phonecart.lags() == []
    answer = client.post("/api/device/cartreport", json=dict(REPORT, lag_ms=700, basket=10 ** 400))
    assert answer.status_code == 200
    users.open_workspace(PHONE)
    assert [row[4] for row in phonecart.lags()] == [700]
    assert phonecart.last("magnit")["basket"] is None


def test_a_placeholder_is_not_a_known_article(web, monkeypatch):
    """Связь-заглушка не значит «сеть знает товар»: позиция идёт в поиск, а не пропадает.

    Замер 18.10.2026 на demo: 12 позиций из 16 связаны только заглушками
    (`magnit-sliva-kg` и подобными). Прежнее правило «есть любое подтверждение —
    пропустить» оставляло их и без наряда, и без поиска: наряд выходил из четырёх
    строк, а 75 % корзины молчало. Заглушка — источник цены, а не артикул сети
    (app/repo.real_sku_sql).
    """
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    plum = repo.upsert_product(Product(id=None, name="Слива", unit="kg"))
    shop = repo.get_store("magnit")
    stub = repo.upsert_store_product(shop.id, "magnit-sliva-kg", "Слива")
    repo.confirm_mapping(plum, stub, confirmed=True)
    basket_id = repo.create_basket("Как в жизни")
    repo.set_basket_item(basket_id, plum, 1.0)
    users.deactivate()

    adopted = []

    def adopt(item_id, product_id=None):
        adopted.append((item_id, product_id))
        sp = repo.upsert_store_product(shop.id, "1000218414", "Слива",
                                       url="https://magnit.ru/product/1000218414")
        repo.confirm_mapping(product_id, sp, confirmed=True)
        return product_id

    monkeypatch.setattr(refresh, "resolve",
                        lambda name, *a, **kw: {"id": 11, "chains": [{"chain": "magnit"}]})
    monkeypatch.setattr(refresh, "adopt", adopt)
    monkeypatch.setattr(matcher, "auto_match",
                        lambda *a, **kw: pytest.fail("каталог нашёл — поиск сети не нужен"))

    users.activate(PHONE)
    found = phonecart._link({"basket": basket_id}, "magnit")
    real = repo.confirmed_mapping(plum, shop.id, real_only=True)
    users.deactivate()

    assert found["tried"] == 1, "позицию с одной заглушкой обязаны спросить"
    assert found["in_catalog"] == 1 and found["linked"] == 1
    assert adopted == [(11, plum)], "найденное в каталоге подтверждается"
    assert real, "связь стала настоящей — по ней позиция и попадёт в наряд"


def test_a_real_article_is_not_asked_again(web, monkeypatch):
    """Настоящий артикул сети — это «сеть уже знает»: ни каталог, ни поиск не зовём.

    В рабочем месте молоко и сыр уже связаны настоящими артикулами (m-1, m-2).
    """
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    basket_id = int(repo.list_baskets()[0]["id"])
    users.deactivate()

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: pytest.fail("спрашивать нечего"))
    monkeypatch.setattr(matcher, "auto_match", lambda *a, **kw: pytest.fail("искать нечего"))

    users.activate(PHONE)
    found = phonecart._link({"basket": basket_id}, "magnit")
    users.deactivate()

    assert found["tried"] == 0 and found["linked"] == 0
    assert found["size"] == 2, "в корзине ровно те две позиции с настоящими артикулами"


def test_a_position_with_no_mapping_is_asked_as_before(web, monkeypatch):
    """Позиция без связей идёт тем же путём, что раньше: каталог, потом живой поиск."""
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    tea = repo.upsert_product(Product(id=None, name="ЧАЙ ЧЁРН.ЛИСТ.100Г", unit="pcs"))
    basket_id = repo.create_basket("Из чека")
    repo.set_basket_item(basket_id, tea, 1.0)
    users.deactivate()
    asked = []

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(matcher, "auto_match",
                        lambda ids, codes, **kw: asked.append((list(ids), list(codes)))
                        or {"auto": 0})

    users.activate(PHONE)
    found = phonecart._link({"basket": basket_id}, "magnit")
    users.deactivate()

    assert asked == [([tea], ["magnit"])]
    assert found["tried"] == 1 and found["searched"] == 1 and found["linked"] == 0


def test_the_live_search_stops_at_the_limit_and_says_how_many_were_left(web, monkeypatch):
    """Больше LIVE_LIMIT незнакомых за наряд: лишние не спрашиваются, и это сказано вслух.

    Молчание читалось бы как «поискали всё»: на замере 18.10.2026 незнакомых было
    двенадцать при пределе десять.
    """
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    shop = repo.get_store("magnit")
    basket_id = repo.create_basket("Двенадцать незнакомых")
    for index in range(phonecart.LIVE_LIMIT + 2):
        item = repo.upsert_product(Product(id=None, name=f"Товар {index}", unit="pcs"))
        stub = repo.upsert_store_product(shop.id, f"magnit-tovar-{index}", f"Товар {index}")
        repo.confirm_mapping(item, stub, confirmed=True)      # только заглушка
        repo.set_basket_item(basket_id, item, 1.0)
    users.deactivate()
    asked = []

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(matcher, "auto_match",
                        lambda ids, codes, **kw: asked.append(ids[0]) or {"auto": 0})

    users.activate(PHONE)
    found = phonecart._link({"basket": basket_id}, "magnit")
    users.deactivate()

    assert found["tried"] == phonecart.LIVE_LIMIT + 2
    assert found["searched"] == phonecart.LIVE_LIMIT and len(asked) == phonecart.LIVE_LIMIT
    assert found["unasked"] == 2
    note = phonecart._link_note(found)
    assert f"ещё 2 не спрошены — предел живого поиска {phonecart.LIVE_LIMIT} за наряд" in note
    assert "в каталоге нашлось 0, живым поиском сети — 0 из 10 спрошенных" in note


def test_a_placeholder_reconfirmed_by_the_search_is_not_a_find(web, monkeypatch):
    """Поиск сети подтвердил ту же заглушку — это не находка: в наряд по ней не положить.

    Замер 18.10.2026 (app/matcher): у Пятёрочки и Самоката живой поиск отдаёт hist-строки,
    и auto_match спокойно подтверждает такую же заглушку снова. Считать это находкой
    значило бы обещать наряд, которого не будет: cartplan берёт только настоящий артикул.
    """
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    plum = repo.upsert_product(Product(id=None, name="Слива", unit="kg"))
    shop = repo.get_store("magnit")
    stub = repo.upsert_store_product(shop.id, "magnit-sliva-kg", "Слива")
    repo.confirm_mapping(plum, stub, confirmed=True)
    basket_id = repo.create_basket("Заглушка и заглушка")
    repo.set_basket_item(basket_id, plum, 1.0)
    users.deactivate()

    def auto_match(ids, codes, **kw):
        # Так ведёт себя настоящий поиск у сетей с hist-: подтверждает ещё одну заглушку.
        again = repo.upsert_store_product(shop.id, "hist-77", "Слива")
        repo.confirm_mapping(ids[0], again, confirmed=True)
        return {"auto": 1, "need_review": []}

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(matcher, "auto_match", auto_match)

    users.activate(PHONE)
    found = phonecart._link({"basket": basket_id}, "magnit")
    users.deactivate()

    assert found["searched"] == 1, "позицию спросили"
    assert found["by_search"] == 0 and found["linked"] == 0, "но находки нет — артикул не настоящий"


def test_a_placeholder_position_reaches_the_plan(web, monkeypatch):
    """Наряд по корзине, где позиция связана только заглушкой: она попадает в строки.

    Это и есть приёмка задачи в миниатюре: до правки такая позиция не попадала ни в
    наряд, ни в поиск — наряд молча оставался без неё.
    """
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    fish = repo.upsert_product(Product(id=None, name="Форель Меридиан 200 г", unit="pcs"))
    shop = repo.get_store("magnit")
    stub = repo.upsert_store_product(shop.id, "magnit-forel-200", "Форель Меридиан 200 г")
    repo.confirm_mapping(fish, stub, confirmed=True)
    basket_id = repo.create_basket("Одна заглушка")
    repo.set_basket_item(basket_id, fish, 1.0)
    users.deactivate()

    def adopt(item_id, product_id=None):
        sp = repo.upsert_store_product(shop.id, "5555559952", "Форель Меридиан слабосоленая 200г",
                                       url="https://magnit.ru/product/5555559952")
        repo.confirm_mapping(product_id, sp, confirmed=True)
        return product_id

    def auto_match(ids, codes, **kw):
        # Настоящий matcher.auto_match не только отвечает, но и подтверждает найденное
        # (app/matcher); подставной обязан делать то же, иначе наряд не увидит связь.
        asked.append(ids[0])
        adopt(0, ids[0])
        return {"auto": 1, "need_review": []}

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(refresh, "adopt", adopt)
    asked = []
    monkeypatch.setattr(matcher, "auto_match", auto_match)

    data = logged_in(web).post("/api/device/cartplan",
                               json={"store": "magnit", "basket": basket_id}).get_json()

    assert asked == [fish], "позицию с заглушкой спросили живым поиском"
    assert [line["url"] for line in data["lines"]] == ["https://magnit.ru/product/5555559952"]
    assert data["unknown"] == [] and data["linked"] == 1


# --- Чему учит единая база: не легло — не значит «ничего не узнали» -----------------

DIXY_CARD = "https://dixy.ru/product/moloko-3-2-930-ml/"
DIXY_ARTICLE = "2000642072"


def _learned(web, monkeypatch, chain, items):
    """Прогнать отчёт приёмом и вернуть то, что `_learn` отдал единой базе.

    Подставной `learn_device_rows` вместо базы: проверяется, ЧТО ушло в каталог сети.
    Сама запись строк проверена в tests/test_phone_learns_skus.py.
    """
    from app.catalog import refresh

    calls = []

    def learn_device_rows(name, rows, limit=None):
        calls.append((name, rows))
        return len(rows)

    monkeypatch.setattr(refresh, "learn_device_rows", learn_device_rows)
    answer = logged_in(web).post("/api/device/cartreport",
                                 json={"store": chain, "items": items})
    assert answer.status_code == 200, answer.get_json()
    return calls


def test_a_failed_position_with_the_chains_real_article_is_learned(web, monkeypatch):
    """Дикси 21.10.2026: карточка отвечает 403, но артикул из поиска сети в отчёте есть.

    Телефон (Z11) присылает настоящий `sku` из серверного поиска и у НЕ легших позиций.
    Прежний `_learn` пропускал всё, где `ok=false`, — за час ни одной строки Дикси в
    каталоге не появилось, хотя артикул 2000642072 в отчёте был.
    """
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "why": "карточка отвечает 403", "url": DIXY_CARD},
    ])

    assert [chain for chain, _ in calls] == ["dixy"]
    assert [row["sku"] for row in calls[0][1]] == [DIXY_ARTICLE], \
        "артикул сети из отчёта — а не пусто из-за неудачной кладки"
    assert calls[0][1][0]["url"] == DIXY_CARD


def test_a_failed_position_without_an_article_uses_the_card_address(web, monkeypatch):
    """`sku` не прислали — прежний путь: артикул из адреса карточки этой сети."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Кефир 1% 500 мл", "ok": False, "why": "кнопки не нашлось",
         "url": "https://dixy.ru/product/kefir-1-500-ml-2000328870/"},
    ])

    assert [row["sku"] for row in calls[0][1]] == ["2000328870"]


@pytest.mark.parametrize("url", ["https://magnit.ru/product/2000642072", "", None],
                         ids=["чужой адрес", "пустой адрес", "без адреса"])
def test_a_stranger_or_missing_address_is_not_learned_even_with_an_article(
        web, monkeypatch, url):
    """Артикул есть, но адреса своей сети нет — не учим: связь с товаром не проверить."""
    item = {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False}
    if url is not None:
        item["url"] = url

    assert _learned(web, monkeypatch, "dixy", [item]) == []


@pytest.mark.parametrize("sku", ["hist-77", "list-77"], ids=["hist-", "list-"])
def test_a_placeholder_article_is_not_learned(web, monkeypatch, sku):
    """Заглушка — не артикул сети: по ней в корзину сети ничего не положить."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Слива", "sku": sku, "ok": False, "url": DIXY_CARD},
    ])

    assert calls == []


def test_a_landed_position_is_learned_as_before(web, monkeypatch):
    """Легшая позиция без `sku` в отчёте учится адресом — прежняя дорога цела."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "ok": True, "put": 1,
         "url": "https://dixy.ru/product/moloko-3-2-930-ml-2000328870/"},
    ])

    assert [row["sku"] for row in calls[0][1]] == ["2000328870"]


def test_a_failed_position_is_learned_under_the_networks_own_name(web, monkeypatch):
    """В каталог сети уходит имя с её карточки (`found_name`), а не наше из наряда.

    Дикси, 21.10.2026: сеть называет товар «Туалетная бумага Papia Балийский цветок»,
    а в наряде он наш — «Papia Bali Flower». Телефон присылает имя найденной карточки
    отдельным полем, и `_learn` обязан предпочесть его: иначе база решит, что сеть
    называет товар так же, как мы.
    """
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Papia Bali Flower",
         "found_name": "Туалетная бумага Papia Балийский цветок",
         "sku": DIXY_ARTICLE, "ok": False, "why": "карточка отвечает 403", "url": DIXY_CARD},
    ])

    assert [row["name"] for row in calls[0][1]] == \
        ["Туалетная бумага Papia Балийский цветок"], \
        "в каталог сети ушло наше название позиции, а не имя с карточки"
    assert calls[0][1][0]["sku"] == DIXY_ARTICLE


def test_a_position_without_the_networks_name_takes_the_card_address(web, monkeypatch):
    """Поля `found_name` нет — имя берётся из адреса карточки."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "url": DIXY_CARD},
    ])

    assert [row["name"] for row in calls[0][1]] == ["moloko 3 2 930 ml"]


def test_a_position_without_found_name_and_without_slug_is_not_learned(web, monkeypatch):
    """Позиция без found_name с адресом без названия не учится."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": "2000328870", "ok": False,
         "url": "https://dixy.ru/catalog/molochnye-produkty-yaytsa/moloko/2000328870/"},
    ])

    assert calls == []


# --- Цена и наличие с карточки — в ту же строку каталога, что артикул сети -----------

def test_a_learned_row_carries_the_price_and_stock_from_the_card(web, monkeypatch):
    """Цена и наличие с открытой карточки уходят в каталог вместе с артикулом сети.

    Телефон кладёт их в пункт отчёта: `price` — цена за упаковку, `in_stock` — bool с
    карточки. Раньше `_learn` терял оба поля, и заведённая строка оставалась без цены.
    """
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "why": "карточка отвечает 403", "url": DIXY_CARD,
         "price": 129.99, "in_stock": True},
    ])

    assert [chain for chain, _ in calls] == ["dixy"]
    row = calls[0][1][0]
    assert row["price"] == 129.99
    assert row["in_stock"] is True
    assert (row["sku"], row["url"]) == (DIXY_ARTICLE, DIXY_CARD)


@pytest.mark.parametrize("price", [0, "0", -10, "abc", ""],
                         ids=["ноль", "ноль строкой", "минус", "мусор", "пустая строка"])
def test_a_price_that_is_not_positive_is_not_learned(web, monkeypatch, price):
    """Ноль, минус и мусор — не цена: ключа в строке каталога быть не должно."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "url": DIXY_CARD, "price": price},
    ])

    assert "price" not in calls[0][1][0]


@pytest.mark.parametrize("stock", ["true", "false", 1, 0],
                         ids=["строка true", "строка false", "число 1", "число 0"])
def test_stock_that_is_not_a_bool_is_not_learned(web, monkeypatch, stock):
    """Наличие — только настоящий bool: строка и число наличием не считаются."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "url": DIXY_CARD, "in_stock": stock},
    ])

    assert "in_stock" not in calls[0][1][0]


def test_a_row_without_price_and_stock_is_learned_as_before(web, monkeypatch):
    """Старый отчёт без цены и наличия учится прежней строкой — без пустых ключей."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Кефир 1% 500 мл", "sku": DIXY_ARTICLE, "ok": False,
         "url": DIXY_CARD},
    ])

    row = calls[0][1][0]
    assert set(row) == {"sku", "name", "url"}
    assert row["sku"] == DIXY_ARTICLE


def test_learn_reads_a_price_written_as_a_string(monkeypatch):
    """Цена строкой тоже принимается: база не зависит от того, как её прислал отчёт."""
    from app.catalog import refresh
    from app.catalog import store as catalog_store

    calls = []

    def learn_device_rows(chain, rows, limit=None):
        calls.append((chain, rows))
        return len(rows)

    # У теста нет временного рабочего места: живой `touch_seen` открыл бы НАСТОЯЩУЮ
    # data/catalog.db и обновил в ней строку. Наблюдение здесь не проверяется — только
    # разбор цены, поэтому база подменена.
    monkeypatch.setattr(catalog_store, "touch_seen", lambda chain, skus: 0)
    monkeypatch.setattr(refresh, "learn_device_rows", learn_device_rows)
    phonecart._learn({"store": "dixy", "items": [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "url": DIXY_CARD,
         "price": "129,99", "in_stock": True},
    ]})

    assert calls[0][1][0]["price"] == 129.99
    assert calls[0][1][0]["in_stock"] is True


# --- Товар, который телефон нашёл поиском сети и положил, получает её артикул --------


FIVE_BRAN = "https://5ka.ru/product/otrubi-vkus-polza-khrustyashchie-ovsyanye-230g--4377613"
FIVE_FLAKES = "https://5ka.ru/product/khlopya-ovsyanye-yasno-solnyshko-3-350g--2122914/"


def _from_receipt(name, raw, weight):
    """Товар из чека Пятёрочки в своей корзине: у сети только заглушка строки чека (hist-)."""
    users.open_workspace(PHONE)
    shop = repo.get_store("pyaterochka")
    pid = repo.upsert_product(Product(id=None, name=name, weight_g=weight, unit="pcs"))
    repo.add_history_row("2026-11-01", shop.id, pid, raw, 1, 62.99, 62.99, f"k-{pid}")
    repo.confirm_mapping(pid, repo.upsert_store_product(shop.id, f"hist-{pid}", name), confirmed=True)
    basket_id = repo.create_basket("Из заказа 1 ноября")
    repo.set_basket_item(basket_id, pid, 1.0)
    users.deactivate()
    return pid, basket_id, shop.id


def _real_sku(pid, shop_id):
    users.open_workspace(PHONE)
    try:
        return (repo.confirmed_mapping(pid, shop_id, real_only=True) or {}).get("sku")
    finally:
        users.deactivate()


def test_a_card_the_phone_found_and_put_gives_the_product_the_chains_article(web):
    """Замер 13.11.2026: корзина из чека Пятёрочки, 7 товаров, настоящих артикулов 0.

    Каждая правка искала товары касаниями заново (96–135 с), хотя адрес найденной карточки был в
    отчёте: каталог сети строку получал, а товар корзины с ней никто не связывал.
    """
    pid, basket_id, shop_id = _from_receipt("Отруби хруст овсян 230 г", "ВК/ПОЛ.Отруби хруст.овсян.230г", 230.0)
    client = logged_in(web)
    ask = {"store": "pyaterochka", "basket": basket_id}
    assert client.post("/api/device/cartplan", json=ask).get_json()["lines"] == []
    report = {**ask, "items": [{"name": "Отруби хруст овсян 230 г", "ok": True, "how": "tap", "put": 1,
                                "price": 62.99, "url": FIVE_BRAN}]}

    answer = client.post("/api/device/cartreport", json=report)

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["bound"] == 1, "телефон узнаёт, что наряд в его памяти устарел"
    assert _real_sku(pid, shop_id) == "4377613"
    users.open_workspace(PHONE)
    sp = repo.store_product_by_sku(shop_id, "4377613")["id"]
    assert repo.mapping_method(pid, sp) == phonecart.BOUND, "связь помечена: её записал не человек"
    assert (repo.latest_price(sp) or {}).get("price") == 62.99, "цена карточки — из этого же отчёта"
    users.deactivate()
    after = client.post("/api/device/cartplan", json=ask).get_json()
    assert [line["sku"] for line in after["lines"]] == ["4377613"], "следующую правку сеть принимает запросом"
    asked = [s.get("product_id") for key in ("search", "search_recent") for s in after.get(key) or []]
    assert pid not in asked + list(after.get("rest") or []), "и телефон товар больше не ищет"
    again = client.post("/api/device/cartreport", json=report).get_json()
    assert again["bound"] == 0, "повторный отчёт наряд в памяти телефона не сбрасывает"
    assert _real_sku(pid, shop_id) == "4377613"


@pytest.mark.parametrize("item, basket, sku", [
    ({"ok": True, "url": FIVE_FLAKES}, True, "2122914"),
    ({"ok": True, "how": "kept", "put": 0, "url": FIVE_FLAKES}, True, "2122914"),
    ({"ok": True, "url": "https://5ka.ru/product/khlopya-ovsyanye-yasno-solnyshko-2-500g--2122915/"}, True, None),
    ({"ok": False, "why": "на карточке не нашлось кнопки", "url": FIVE_FLAKES}, True, None),
    ({"ok": True, "url": FIVE_FLAKES.replace("5ka.ru", "magnit.ru"), "sku": "2122914"}, True, None),
    ({"ok": True, "url": FIVE_FLAKES}, False, None),
    ({"ok": True, "url": FIVE_FLAKES, "name": "Хлопья"}, True, None),
], ids=["легла своей карточкой", "уже лежала в корзине сети (приложение 1.48 шлёт её адрес)",
        "легла карточкой другой фасовки", "не легла", "адрес чужой сети",
        "отчёт без корзины", "название не из корзины"])
def test_the_article_is_taken_only_from_a_card_that_landed_and_is_the_same_product(web, item, basket, sku):
    """Замер 13.11.2026: «…хлопья овсян №3 350 г» телефон положил карточкой «…solnyshko-2-500g»."""
    pid, basket_id, shop_id = _from_receipt("Ясно солн хлопья овсян 3 350 г", "ЯСНО СОЛН.Хлопья ОВСЯН.№3  350г", 350.0)
    body = {"store": "pyaterochka", "items": [{"name": "Ясно солн хлопья овсян 3 350 г", "how": "tap", **item}]}
    if basket:
        body["basket"] = basket_id

    assert logged_in(web).post("/api/device/cartreport", json=body).status_code == 200

    assert _real_sku(pid, shop_id) == sku


def test_a_dropped_receipt_stub_keeps_the_article_away_and_the_answer_honest(web):
    """Человек убрал у товара заглушку чека: запись связи молча не проходит — и ответ её не считает."""
    pid, basket_id, shop_id = _from_receipt("Отруби хруст овсян 230 г", "ВК/ПОЛ.Отруби хруст.овсян.230г", 230.0)
    users.open_workspace(PHONE)
    repo.drop_mapping(pid, shop_id)
    assert not repo.chain_rejected(pid, shop_id), "отказ от заглушки — не отказ от сети"
    users.deactivate()

    answer = logged_in(web).post("/api/device/cartreport", json={
        "store": "pyaterochka", "basket": basket_id,
        "items": [{"name": "Отруби хруст овсян 230 г", "ok": True, "how": "tap", "url": FIVE_BRAN}]})

    assert answer.get_json()["bound"] == 0
    assert _real_sku(pid, shop_id) is None


def test_a_search_line_carries_the_pack_so_the_phone_checks_the_card(web):
    """Замер 13.11.2026: «хлопья №3 350 г» легли карточкой «…№2 500 г» — у строки поиска веса не было.

    Весовому товару фасовка не нужна: он продаётся на развес, и сверка только мешала бы.
    """
    flakes, basket_id, _ = _from_receipt("Ясно солн хлопья овсян 3 350 г", "ЯСНО СОЛН.Хлопья ОВСЯН.№3  350г", 350.0)
    users.open_workspace(PHONE)
    carrot = repo.upsert_product(Product(id=None, name="Морковь весовая", weight_g=1000.0, unit="kg"))
    repo.set_basket_item(basket_id, carrot, 1.0)
    users.deactivate()

    plan = logged_in(web).post("/api/device/cartplan", json={"store": "pyaterochka", "basket": basket_id}).get_json()

    assert {s["product_id"]: s.get("pack_g") for s in plan["search"]} == {flakes: 350.0, carrot: None}


def test_the_phones_find_does_not_argue_with_the_persons_choice(web):
    """Свой артикул человека находка не заменяет, а в сеть, где он связь убрал, не возвращается."""
    pid, basket_id, shop_id = _from_receipt("Отруби хруст овсян 230 г", "ВК/ПОЛ.Отруби хруст.овсян.230г", 230.0)
    users.open_workspace(PHONE)
    repo.confirm_mapping(pid, repo.upsert_store_product(shop_id, "9000001", "Отруби овсяные 230 г"), confirmed=True)
    users.deactivate()
    client = logged_in(web)
    report = {"store": "pyaterochka", "basket": basket_id, "items": [
        {"name": "Отруби хруст овсян 230 г", "ok": True, "how": "tap", "url": FIVE_BRAN}]}

    assert client.post("/api/device/cartreport", json=report).status_code == 200
    assert _real_sku(pid, shop_id) == "9000001"

    users.open_workspace(PHONE)
    repo.drop_mapping(pid, shop_id)
    users.deactivate()
    assert client.post("/api/device/cartreport", json=report).status_code == 200
    assert _real_sku(pid, shop_id) is None


# --- Наблюдение телефона оживляет УЖЕ ИЗВЕСТНЫЕ строки каталога ---------------------


def _touched(web, monkeypatch, chain, items):
    """Прогнать отчёт приёмом и вернуть (ответ, вызовы `store.touch_seen`).

    Подставной `touch_seen` вместо базы: проверяется, с какими артикулами его позвали.
    Сама запись `last_seen`/`active=1` живёт в app/catalog/store.py и проверяется там.
    """
    from app.catalog import store as catalog_store

    calls = []

    def touch_seen(chain_code, skus):
        calls.append((chain_code, list(skus)))
        return len(skus)

    monkeypatch.setattr(catalog_store, "touch_seen", touch_seen)
    answer = logged_in(web).post("/api/device/cartreport",
                                 json={"store": chain, "items": items})
    assert answer.status_code == 200, answer.get_json()
    return answer, calls


def test_a_card_the_phone_saw_revives_its_known_row(web, monkeypatch):
    """Карточка отрисовалась (price>0) — телефон видел строку: она оживает.

    `learn_device_rows` известные артикулы пропускает, поэтому наблюдение идёт
    отдельным шагом: обход мог погасить строку (`active=0`), и без этого шага товар,
    который человек только что видел в сети, оставался бы мёртвым в каталоге.
    """
    _, calls = _touched(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "why": "карточка отвечает 403", "url": DIXY_CARD, "price": 149.9},
    ])

    assert calls == [("dixy", [DIXY_ARTICLE])], \
        "наблюдение телефона не дошло до известной строки каталога"


@pytest.mark.parametrize("price", ["", 0, "0", None],
                         ids=["пустая строка", "ноль", "ноль строкой", "без цены"])
def test_a_card_without_a_price_is_not_a_sighting(web, monkeypatch, price):
    """Цены нет (403, бот-защита) — карточка не отрисовалась: last_seen не трогаем."""
    item = {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
            "why": "карточка отвечает 403", "url": DIXY_CARD}
    if price is not None:
        item["price"] = price

    _, calls = _touched(web, monkeypatch, "dixy", [item])

    assert calls == [], "пустая цена принята за наблюдение"


def test_a_card_at_a_stranger_address_is_not_a_sighting(web, monkeypatch):
    """Карточка чужой сети — не доказательство жизни строки этой сети."""
    _, calls = _touched(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "url": "https://magnit.ru/product/2000642072", "price": 149.9},
    ])

    assert calls == [], "чужой адрес принят за наблюдение"


def test_a_missing_touch_seen_does_not_break_the_report(web, monkeypatch):
    """`touch_seen` ещё нет в store.py (правит другая сессия) — отчёт принимается.

    Отсутствие функции — не ошибка приёма: карточка — прибыль каталога, а не условие
    сохранения отчёта о корзине.
    """
    from app.catalog import store as catalog_store

    monkeypatch.delattr(catalog_store, "touch_seen", raising=False)
    answer = logged_in(web).post("/api/device/cartreport", json={
        "store": "dixy",
        "items": [{"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
                   "url": DIXY_CARD, "price": 149.9}],
    })

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["ok"] is True


def test_link_skips_recent_and_counts_it(web, monkeypatch):
    """(а) второй _link той же позиции в течение срока не зовёт ни resolve, ни auto_match и считает её в recent."""
    from app.catalog import refresh
    from app import matcher

    users.open_workspace(PHONE)
    coffee = repo.upsert_product(Product(id=None, name="КОФЕ ЖОКЕЙ 250Г", unit="pcs"))
    basket_id = repo.create_basket("Кофе")
    repo.set_basket_item(basket_id, coffee, 1.0)
    users.deactivate()

    resolve_calls = []

    def fake_resolve(*args, **kwargs):
        resolve_calls.append(args)
        return None

    auto_match_calls = []

    def fake_auto_match(ids, codes, **kwargs):
        auto_match_calls.append((ids, codes))
        return {"auto": 0, "need_review": []}

    monkeypatch.setattr(refresh, "resolve", fake_resolve)
    monkeypatch.setattr(matcher, "auto_match", fake_auto_match)

    users.open_workspace(PHONE)
    try:
        first = phonecart._link({"basket": basket_id}, "magnit")
        assert len(resolve_calls) == 1
        assert len(auto_match_calls) == 1
        assert first["tried"] == 1
        assert first["recent"] == 0

        # Второй вызов _link в течение срока
        second = phonecart._link({"basket": basket_id}, "magnit")
        assert len(resolve_calls) == 1, "resolve не должен вызываться второй раз"
        assert len(auto_match_calls) == 1, "auto_match не должен вызываться второй раз"
        assert second["tried"] == 0
        assert second["recent"] == 1
    finally:
        users.deactivate()


def test_link_asks_the_catalog_once_for_all_chains(web, monkeypatch):
    """Наряды сетей после правки спрашивают каталог об одном товаре разом: вопрос один, ответ общий."""
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    tea = repo.upsert_product(Product(id=None, name="ЧАЙ ГРИНФИЛД 100ПАК", unit="pcs"))
    basket_id = repo.create_basket("Чай")
    repo.set_basket_item(basket_id, tea, 1.0)
    users.deactivate()

    asked = []
    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: asked.append(a))
    monkeypatch.setattr(matcher, "auto_match", lambda *a, **kw: {"auto": 0, "need_review": []})

    users.open_workspace(PHONE)
    try:
        for store in ("magnit", "dixy", "perekrestok"):
            assert phonecart._link({"basket": basket_id}, store)["tried"] == 1, store
    finally:
        users.deactivate()
    assert len(asked) == 1, "каждая сеть спросила каталог об одном и том же товаре сама"


def test_asked_once_shares_one_answer_and_forgets_a_failure():
    """Память ответов каталога: шесть потоков — один вопрос; срок вышел — спрашиваем снова; отказ не помним."""
    import threading
    import time

    calls = []

    def slow():
        calls.append("ask")
        time.sleep(0.05)
        return "ответ"

    got = []
    threads = [threading.Thread(target=lambda: got.append(phonecart._asked_once(("k",), 60, slow))) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert got == ["ответ"] * 6 and len(calls) == 1, "каждый поток спросил каталог сам"
    assert phonecart._asked_once(("k",), 0, slow) == "ответ" and len(calls) == 2, "срок вышел, а ответ взят из памяти"

    def boom():
        calls.append("boom")
        raise RuntimeError("каталог не ответил")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            phonecart._asked_once(("сбой",), 60, boom)
    assert calls.count("boom") == 2, "отказ каталога запомнился как ответ"


def test_asked_once_does_not_wait_for_a_stuck_catalog(monkeypatch):
    """Каталог завис у первого потока — второй не стоит за ним в очереди, а спрашивает сам."""
    import threading

    monkeypatch.setattr(phonecart, "ANSWER_WAIT_SECONDS", 0.05)
    stuck, release = threading.Event(), threading.Event()

    def hang():
        stuck.set()
        release.wait(5)
        return "поздно"

    first = threading.Thread(target=lambda: phonecart._asked_once(("завис",), 60, hang))
    first.start()
    try:
        assert stuck.wait(5)
        assert phonecart._asked_once(("завис",), 60, lambda: "сам") == "сам"
    finally:
        release.set()
        first.join()


def test_link_budget_exhausted_leaves_positions_unasked(web, monkeypatch):
    """(б) бюджет: LINK_BUDGET_SECONDS = 0 через monkeypatch — auto_match не зовётся, позиции в unasked."""
    from app import config, matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    sugar = repo.upsert_product(Product(id=None, name="САХАР ПЕСОК 1КГ", unit="pcs"))
    basket_id = repo.create_basket("Сахар")
    repo.set_basket_item(basket_id, sugar, 1.0)
    users.deactivate()

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    auto_called = []
    monkeypatch.setattr(matcher, "auto_match", lambda *a, **kw: auto_called.append(a))
    monkeypatch.setattr(phonecart, "LINK_BUDGET_SECONDS", 0)

    users.open_workspace(PHONE)
    try:
        res = phonecart._link({"basket": basket_id}, "magnit")
        assert auto_called == [], "при исчерпанном бюджете auto_match не зовётся"
        assert res["unasked"] == 1
        # Позиция не получила отметку link
        assert not phonecart._is_tried("link", str(config.db_path()), "magnit", sugar)
    finally:
        users.deactivate()


def test_second_plan_gives_search_recent(web, monkeypatch):
    """(в) второй plan той же корзины отдаёт позицию в search_recent, а не в search."""
    from app.catalog import refresh

    users.open_workspace(PHONE)
    tea = repo.upsert_product(Product(id=None, name="ЧАЙ ЧЁРН.ЛИСТ.100Г", unit="pcs"))
    basket_id = repo.create_basket("Чай")
    repo.set_basket_item(basket_id, tea, 1.0)
    users.deactivate()
    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)

    client = logged_in(web)
    first_res = client.post("/api/device/cartplan", json={"store": "magnit", "basket": basket_id}).get_json()
    assert len(first_res.get("search", [])) == 1
    assert first_res.get("search_recent", []) == []

    second_res = client.post("/api/device/cartplan", json={"store": "magnit", "basket": basket_id}).get_json()
    assert second_res.get("search", []) == []
    assert len(second_res.get("search_recent", [])) == 1
    assert second_res["search_recent"][0]["name"] == "ЧАЙ ЧЁРН.ЛИСТ.100Г"


def test_rules_api_total_verified():
    """Сверенное правило итога из ответа сети (API_TOTALS, verified) отдаётся телефону."""
    magnit_rules = phonecart.rules("magnit")
    assert magnit_rules["api_total"] == {
        "path": "/webgate/v1/checkout/preview",
        "total": "summary.totalFinalPrice",
        "base": "summary.totalBasePrice",
        "sale": "summary.itemsTotalSalePrice",
        "count": "summary.itemsCount",
        "scale": 0.01,
    }
    vkusvill_rules = phonecart.rules("vkusvill")
    assert vkusvill_rules["api_total"] is None


def test_rules_carry_the_card_address_rule():
    """Правило «адрес карточки → артикул» телефон получает то же, каким артикул достаёт сервер."""
    import re

    from app.catalog import fromurl

    def as_the_app(rule: str, url: str) -> str:            # ownSku приложения: первая непустая группа
        found = re.search(rule, url)
        return next((group for group in found.groups() if group), "") if found else ""

    cards = {
        "magnit": "https://magnit.ru/product/1000204366-testo-dlya-cheburekov?shopCode=652",
        "pyaterochka": "https://5ka.ru/product/sushki-prostye--4359341/",
        "perekrestok": "https://www.perekrestok.ru/cat/58/p/bliny-s-masom-morozko-370g-3342904",
        "samokat": "https://samokat.ru/product/khurma-korolyok-500-g",
        "dixy": "https://dixy.ru/catalog/molochnye-produkty-yaytsa/moloko/2000328870/",
        "lenta": "https://lenta.com/product/rk-gorbusha-naturalnaya-rossiya-240g-21/",
        "monetka": "https://monetka.ru/product/sok-dobryjj-yabloko-1l-rossiya-810000639/",
        "fixprice": "https://fix-price.com/catalog/produkty-i-napitki/p-1660028-sahar-1-kg",
        "vprok": "https://www.vprok.ru/product/linakva-linakva-bebi-0-9-aerozol-150-ml--1284993",
    }
    for store, url in cards.items():
        sku = fromurl.sku_of(store, url)
        assert sku and as_the_app(phonecart.rules(store)["sku_url"], url) == sku, store
    capital = "https://dixy.ru/product/Moloko-AB12CD34/"    # правило Дикси не смотрит на регистр
    assert as_the_app(phonecart.rules("dixy")["sku_url"], capital) == fromurl.sku_of("dixy", capital) == "AB12CD34"
    assert phonecart.rules("vkusvill")["sku_url"] == "", "адреса карточки ВкусВилла мы не знаем"
    for store, rule in fromurl.RULES.items():               # правило читает и Java: без (?P<…>) и чужих флагов
        assert "(?P" not in rule.pattern and not rule.flags & ~(re.IGNORECASE | re.UNICODE), store


def test_link_live_false_defers_search_to_ask_later(web, monkeypatch):
    """(а) _link(..., live=False) не зовёт auto_match, зовёт _ask_later и кладёт later == 1, повтор — recent."""
    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    cocoa = repo.upsert_product(Product(id=None, name="КАКАО ЗОЛОТОЙ ЯРЛЫК 100Г", unit="pcs"))
    basket_id = repo.create_basket("Какао")
    repo.set_basket_item(basket_id, cocoa, 1.0)
    users.deactivate()

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(matcher, "auto_match", lambda *a, **kw: pytest.fail("auto_match не должен вызываться при live=False"))

    ask_later_calls = []

    def fake_ask_later(pids, store):
        ask_later_calls.append((pids, store))

    monkeypatch.setattr(phonecart, "_ask_later", fake_ask_later)

    users.open_workspace(PHONE)
    try:
        first = phonecart._link({"basket": basket_id}, "magnit", live=False)
        assert ask_later_calls == [([cocoa], "magnit")]
        assert first["later"] == 1
        assert first["tried"] == 1
        assert first["recent"] == 0
        assert any(u.get("product_id") == cocoa for u in first.get("unresolved", []))

        # Второй вызов _link в течение срока
        second = phonecart._link({"basket": basket_id}, "magnit", live=False)
        assert len(ask_later_calls) == 1, "_ask_later больше не должен вызываться"
        assert second["later"] == 0
        assert second["tried"] == 0
        assert second["recent"] == 1
    finally:
        users.deactivate()


def test_a_background_plan_orders_no_search_by_name(web, monkeypatch):
    """Фоновой сборке (background=true) поиск по названию не заказывается: ни search, ни search_recent. Замер
    15.11.2026: хвост поиска держал строку сети 60–137 с и почти ничего не находил. Без background наряд
    кнопки «Собрать» несёт search, а второй такой наряд — search_recent."""
    from app import config
    from app.catalog import refresh

    users.open_workspace(PHONE)
    juice = repo.upsert_product(Product(id=None, name="СОК ЯБЛОЧНЫЙ 1Л", unit="pcs"))
    basket_id = repo.create_basket("Сок")
    repo.set_basket_item(basket_id, juice, 1.0)
    users.deactivate()

    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(phonecart, "_ask_later", lambda *a, **kw: None)

    real_get = config.get
    monkeypatch.setattr(config, "get",
                        lambda key, *a, **k: "https://magnit.ru/search?q={q}" if key == "search_url.magnit" else real_get(key, *a, **k),
                        raising=False)

    client = logged_in(web)

    # 1. background=True дважды подряд: искать телефону нечего, товар остаётся в pids
    res1 = client.post("/api/device/cartplan", json={"store": "magnit", "basket": basket_id, "background": True}).get_json()
    res2 = client.post("/api/device/cartplan", json={"store": "magnit", "basket": basket_id, "background": True}).get_json()
    for res in (res1, res2):
        assert res["search"] == [] and res["search_recent"] == []
        assert res["lines"] == [] and res["pids"] == [juice]

    # 2. Без background: второй наряд кладёт его в search_recent
    users.open_workspace(PHONE)
    water = repo.upsert_product(Product(id=None, name="ВОДА МИНЕРАЛЬНАЯ 1Л", unit="pcs"))
    basket2_id = repo.create_basket("Вода")
    repo.set_basket_item(basket2_id, water, 1.0)
    users.deactivate()

    r1 = client.post("/api/device/cartplan", json={"store": "magnit", "basket": basket2_id}).get_json()
    assert len(r1.get("search", [])) == 1
    assert r1.get("search_recent", []) == []

    r2 = client.post("/api/device/cartplan", json={"store": "magnit", "basket": basket2_id}).get_json()
    assert r2.get("search", []) == []
    assert len(r2.get("search_recent", [])) == 1
    assert r2["search_recent"][0]["name"] == "ВОДА МИНЕРАЛЬНАЯ 1Л"
    # Что ищется поиском телефона, в rest не идёт: такую правку телефон выводит по списку поиска.
    assert res1["rest"] == [] and r2["rest"] == []


def test_the_plan_names_products_the_chain_will_not_get_only_when_the_server_is_done(web, monkeypatch):
    """rest — товары, чья правка наряд сети не меняет: телефон (APK 1.37) выводит его сам, не дожидаясь
    сервера. Пока фоновый поиск сети идёт, товара там нет: через секунды сеть может его узнать, а телефон
    держал бы наряд без него до 6 ч (находка проверки 12.11.2026)."""
    import threading

    from app import matcher
    from app.catalog import refresh

    users.open_workspace(PHONE)
    water = repo.upsert_product(Product(id=None, name="ВОДА МИНЕРАЛЬНАЯ 1Л", unit="pcs"))
    basket_id = repo.create_basket("Вода")
    repo.set_basket_item(basket_id, water, 1.0)
    users.deactivate()
    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    found_nothing = threading.Event()
    monkeypatch.setattr(matcher, "auto_match", lambda ids, codes, **kw: found_nothing.wait(10) and {})
    client = logged_in(web)
    ask = {"store": "perekrestok", "basket": basket_id, "background": True}   # адреса поиска у сети нет

    first = client.post("/api/device/cartplan", json=ask).get_json()
    assert first["lines"] == [] and first.get("search", []) == []
    assert first["rest"] == [], "фоновый поиск сети ещё идёт"
    assert first["pids"] == [water], "товар в корзине есть и без строки в наряде: найденное для него телефон бережёт"
    found_nothing.set()
    for worker in threading.enumerate():
        if worker.name == "link-perekrestok":
            worker.join(10)
    assert phonecart._ASKING == {}
    assert client.post("/api/device/cartplan", json=ask).get_json()["rest"] == [water]


def test_a_product_with_a_stand_in_is_marked_in_the_search_list(web, monkeypatch):
    """У товара с запасным строка наряда несёт номер запасного, а сам товар остаётся в поиске. По «+» у
    такого товара телефон оставил бы строку запасного с прежним числом, поэтому его правку он не выводит."""
    from app import config
    from app.catalog import refresh

    users.open_workspace(PHONE)
    shop = repo.get_store("magnit")
    kefir = repo.upsert_product(Product(id=None, name="Кефир Домик 1 л", unit="pcs"))
    spare = repo.upsert_product(Product(id=None, name="Кефир Простоквашино 1 л", unit="pcs"))
    spare_sp = repo.upsert_store_product(shop.id, "m-9", "Кефир Простоквашино 1 л",
                                         url="https://magnit.ru/product/9")
    repo.confirm_mapping(spare, spare_sp, confirmed=True)
    repo.save_price(spare_sp, 99.0, in_stock=1)
    juice = repo.upsert_product(Product(id=None, name="СОК ЯБЛОЧНЫЙ 1Л", unit="pcs"))
    basket_id = repo.create_basket("Кефир")
    repo.set_basket_item(basket_id, kefir, 2.0)
    repo.set_basket_item(basket_id, juice, 1.0)
    repo.set_basket_fallback(basket_id, kefir, spare)
    users.deactivate()
    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: None)
    monkeypatch.setattr(phonecart, "_ask_later", lambda *a, **kw: None)
    real_get = config.get
    monkeypatch.setattr(config, "get", lambda key, *a, **k: "https://magnit.ru/search?q={q}"
                        if key == "search_url.magnit" else real_get(key, *a, **k), raising=False)

    # Наряд кнопки «Собрать»: фоновому поиск по названию не заказывают.
    plan = logged_in(web).post("/api/device/cartplan", json={"store": "magnit", "basket": basket_id}).get_json()
    assert [line["product_id"] for line in plan["lines"]] == [spare], "вместо кефира в наряде запасной"
    assert {s["product_id"]: s.get("swapped", False) for s in plan["search"]} == {kefir: True, juice: False}
    assert plan["rest"] == []
    assert plan["pids"] == sorted([spare, juice]), "товары корзины, о которой наряд: вместо кефира запасной"



def test_the_plan_carries_the_person_place_for_samokat_and_magnit(tmp_path, monkeypatch):
    """Наряд несёт точку человека: Самокату — координаты адреса, Магниту — магазин расчёта и магазины вокруг.

    Подбор ходит в сеть, поэтому ответ помнится в настройках человека: тот же адрес геокодер второй
    раз не зовёт, новый — зовёт, а когда геокодер молчит, остаётся прежняя точка этого адреса.
    """
    from app import geo, location
    from app.connectors import magnit
    from app.models import Location

    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    try:
        assert phonecart.rules("samokat")["place"] is None, "адреса нет — точки нет"
        asked = []
        monkeypatch.setattr(geo, "coords", lambda addr: asked.append(addr) or (59.123456789, 30.5))
        monkeypatch.setattr(location, "for_store",
                            lambda code: Location(store_id="652", shop_type="DARKSTORE", delivery=True))
        monkeypatch.setattr(magnit, "stores_near",
                            lambda lat, lon, **kw: [{"code": "652", "delivery": True}, {"code": 700, "delivery": False}])
        repo.set_setting(location.KEY_ADDRESS, "Город, улица 1")
        assert phonecart.rules("samokat")["place"] == {"lat": 59.123457, "lon": 30.5}
        assert phonecart.rules("magnit")["place"] == {"shop": "652", "type": "DARKSTORE", "delivery": True,
                                                      "near": ["652", "700"], "deliver": ["652"]}
        assert phonecart.rules("pyaterochka")["place"] == {"lat": 59.123457, "lon": 30.5}, "Пятёрочка сверяет адрес корзины (1.63)"
        assert phonecart.rules("dixy")["place"] is None, "остальные сети берут адрес со своего сайта"
        calls = len(asked)
        assert phonecart.rules("samokat")["place"] and phonecart.rules("magnit")["place"]
        assert len(asked) == calls, "точка того же адреса — из памяти, без геокодера"
        repo.set_setting(location.KEY_ADDRESS, "Город, улица 2")
        phonecart.rules("samokat")
        assert len(asked) == calls + 1, "новый адрес — точка подбирается заново"

        def silent(addr):
            raise OSError("геокодер молчит")

        monkeypatch.setattr(phonecart, "PLACE_TTL_SEC", 0)       # память точки истекла
        monkeypatch.setattr(geo, "coords", silent)
        assert phonecart.rules("samokat")["place"] == {"lat": 59.123457, "lon": 30.5}, "прежняя точка лучше никакой"
        monkeypatch.setattr(phonecart, "PLACE_TTL_SEC", -1000)   # и отсрочка повтора тоже прошла
        monkeypatch.setattr(phonecart, "PLACE_KEEP_SEC", -1)
        assert phonecart.rules("samokat")["place"] is None, "без подтверждения прежняя точка живёт не вечно"

        monkeypatch.setattr(geo, "coords", lambda addr: (59.0, 30.0))
        monkeypatch.setattr(magnit, "stores_near", lambda lat, lon, **kw: [])
        assert phonecart.rules("magnit")["place"] is None, "справочник молчит: без перечня магазинов точку не отдаём"
    finally:
        users.deactivate()


def test_the_journal_keeps_how_long_the_phone_waited_for_the_plan(web, caplog):
    """Правка дольше 5 с: по времени ожидания наряда видно, сервер это или дорога к нему (приложение 1.44)."""
    client = logged_in(web)
    with caplog.at_level("INFO", logger=phonecart.log.name):
        client.post("/api/device/cartreport", json=dict(REPORT, lag_ms=17748, plan_ms=16906))
    client.post("/api/device/cartreport", json=dict(REPORT, lag_ms=1200))      # прежние версии его не шлют
    users.open_workspace(PHONE)
    assert [row[4:] for row in phonecart.lags()] == [[17748, "1.4", "", 16906], [1200, "1.4", ""]]
    assert "от касания 17.7 с (наряд ждали 16.9 с)" in caplog.text
    assert phonecart.last("magnit")["plan_ms"] is None


def test_a_repeated_plan_request_waits_for_the_first_answer(monkeypatch):
    """Приложение 1.44 повторяет запрос наряда, не дождавшись ответа за 1,8 с, и помечает оба одним ask.
    Медлит сервер, а не дорога — повтор ждёт ответ первого: наряд не считается второй раз рядом с первым,
    и новый товар не ищется в сети дважды."""
    import threading
    import time

    inside, release = threading.Event(), threading.Event()
    calls: list = []
    ask = 5_000_000_000         # время работы телефона в мс: за два месяца без перезагрузки больше 1e9

    def slow(body):
        calls.append(body["store"])
        inside.set()
        release.wait(5)
        if body.get("fail") and len(calls) == 1:
            raise RuntimeError("первый запрос упал")
        return {"ok": True, "n": len(calls)}

    def together(counted: int, *bodies) -> list:
        """Первый запрос стоит в расчёте, остальные приходят следом; counted — сколько из них считают сами."""
        got: list = [None] * len(bodies)

        def one(i):
            try:
                got[i] = phonecart.plan(bodies[i])
            except RuntimeError:
                got[i] = "упал"

        inside.clear(), release.clear(), calls.clear()
        threads = [threading.Thread(target=one, args=(i,)) for i in range(len(bodies))]
        threads[0].start()
        assert inside.wait(5)
        for thread in threads[1:]:
            thread.start()
        end = time.monotonic() + 5      # все дошли до расчёта или ожидания: «now» растёт под замком близнецов
        while (phonecart._PLANS["now"], len(calls)) != (len(bodies), counted) and time.monotonic() < end:
            time.sleep(0.01)
        assert (phonecart._PLANS["now"], len(calls)) == (len(bodies), counted)
        release.set()
        for thread in threads:
            thread.join(5)
        assert not phonecart._TWINS and phonecart._PLANS["now"] == 0
        return got

    monkeypatch.setattr(phonecart, "_plan", slow)
    magnit = {"store": "magnit", "ask": ask}
    assert together(1, magnit, dict(magnit)) == [{"ok": True, "n": 1}] * 2, "повтор наряд не считал"
    # Первый упал — повтор считает сам.
    assert together(1, dict(magnit, fail=True), dict(magnit, fail=True)) == ["упал", {"ok": True, "n": 2}]
    # Другая сеть, другой вопрос и запрос без номера (прежние версии, «Собрать») первого не ждут.
    together(4, magnit, {"store": "dixy", "ask": ask}, {"store": "magnit", "ask": ask + 1}, {"store": "magnit"})
    # Первый завис дольше TWIN_WAIT_SEC — повтор считает сам.
    monkeypatch.setattr(phonecart, "TWIN_WAIT_SEC", 0.05)
    together(2, magnit, dict(magnit))


def test_the_fail_journal_keeps_why_a_report_did_not_land(web):
    """Телефон владельца отладчиком не посмотреть, а следующий отчёт стирает прошлый за секунды: причины
    отказов копятся отдельно (13.11.2026: Дикси и Пятёрочка «легло 0 из 11», причины ушли с отчётами)."""
    client = logged_in(web)

    def send(store: str, items: list, **more) -> None:
        assert client.post("/api/device/cartreport", json=dict(REPORT, store=store, items=items, **more)).status_code == 200

    wall = [{"name": f"Товар {n}", "sku": f"p-{n}", "ok": False, "why": "обрыв сети", "how": "request",
             "calls": ["PUT /api/cart?x=1 403"] if n else []} for n in range(3)]
    send("pyaterochka", wall, seconds=5)
    send("pyaterochka", wall[:2], lag_ms=300)
    send("magnit", REPORT["items"])                       # легло 1 из 2
    send("magnit", REPORT["items"][:1])                   # легло всё — не отказ
    # Ранний отчёт: отложенная позиция — не отказ; почему запросами не вышло, приходит пометкой позиции,
    # и с ней в журнал идёт и отложенная (иначе отказ сети при корзине из одних незнакомых пропал бы).
    later = {"name": "Товар 9", "ok": False, "why": phonecart.LATER_WHY, "how": "tap"}
    send("dixy", [later])
    send("dixy", [{"name": "Товар 8", "ok": False, "why": "запросом не легла, положу касаниями",
                   "note": "сеть ответила 403", "how": "tap"}, dict(later, note="сеть ответила 403")])
    # Причин больше, чем хранит строка: одинаковые отчёты всё равно сливаются.
    many = [{"name": f"Товар {n}", "ok": False, "why": f"причина {n}", "how": "tap"} for n in range(6)]
    send("perekrestok", many)
    send("perekrestok", many)
    users.open_workspace(PHONE)
    first, second, third, fourth = phonecart.fails()
    assert (first["store"], first["landed"], first["planned"], first["times"]) == ("pyaterochka", 0, 2, 2), \
        "тот же отказ той же сети строку не плодит: серия касаний не вытеснит первый"
    assert first["whys"] == [["обрыв сети", 2]] and first["calls"] == ["PUT /api/cart 403"] and first["first"]
    assert first["lag_ms"] == 300 and first["app_version"] == "1.4"
    assert (second["store"], second["landed"], second["planned"], second["times"]) == ("magnit", 1, 2, 1)
    assert second["whys"] == [["на карточке не нашлось кнопки «в корзину»", 1]] and "first" not in second
    assert (third["store"], third["planned"], third["times"]) == ("dixy", 2, 1)
    assert third["whys"] == [["запросом не легла, положу касаниями (сеть ответила 403)", 1],
                             [phonecart.LATER_WHY + " (сеть ответила 403)", 1]]
    assert (fourth["store"], fourth["times"], len(fourth["whys"])) == ("perekrestok", 2, 4)
    # Чужое в журнале (не строка) приём отчёта не ломает и само уходит.
    repo.set_setting(phonecart.FAILS_KEY, json.dumps(["мусор", first]))
    users.deactivate()
    send("lenta", wall[:1])
    users.open_workspace(PHONE)
    assert [row["store"] for row in phonecart.fails()] == ["pyaterochka", "lenta"]


def test_the_apps_trace_outlives_the_next_report_of_the_same_chain(web):
    """Хроника приложения (1.54): что решало окно сети перед отчётом, отчёт другой правки не стирает."""
    client = logged_in(web)

    def send(store: str, **more) -> None:
        assert client.post("/api/device/cartreport", json=dict(REPORT, store=store, **more)).status_code == 200

    send("pyaterochka", trace=["23:43:10.120 Пятёрочка: читаю корзину заранее", "", "  23:43:11.500   Пятёрочка:  отказ  ",
                               "я" * 500])
    send("pyaterochka", trace="не список")      # чужое в поле приём не ломает и строки не даёт
    send("magnit")                              # без хроники строки нет
    send("pyaterochka", trace=[f"строка {n}" for n in range(500)])
    users.open_workspace(PHONE)
    first, second = phonecart.traces()
    assert (first["store"], second["store"]) == ("pyaterochka", "pyaterochka") and first["app_version"] == "1.4"
    assert first["trace"][:2] == ["23:43:10.120 Пятёрочка: читаю корзину заранее", "23:43:11.500 Пятёрочка: отказ"], \
        "пустые строки уходят, пробелы сжимаются"
    assert len(first["trace"]) == 3 and len(first["trace"][2]) == phonecart.TRACE_WIDTH
    assert len(second["trace"]) == phonecart.TRACE_LINES and second["trace"][-1] == "строка 499", "берём последние"
    # Последний отчёт сети хроники в себе не носит: он нужен приложению для расчёта, а не для разбора.
    assert "trace" not in phonecart.last("pyaterochka")
    # Журнал держит последние TRACES_KEEP хроник.
    users.deactivate()
    for n in range(phonecart.TRACES_KEEP + 2):
        send("dixy", trace=[f"хроника {n}"])
    users.open_workspace(PHONE)
    kept = phonecart.traces()
    assert len(kept) == phonecart.TRACES_KEEP and kept[-1]["trace"] == [f"хроника {phonecart.TRACES_KEEP + 1}"]
    # Мусор в журнале (не строка) приём отчёта не ломает и сам уходит.
    repo.set_setting(phonecart.TRACES_KEY, json.dumps(["мусор", kept[0]]))
    users.deactivate()
    send("lenta", trace=["после мусора"])
    users.open_workspace(PHONE)
    assert [row["trace"] for row in phonecart.traces()] == [kept[0]["trace"], ["после мусора"]]


def test_the_magnit_place_remembered_before_1_55_is_picked_again(tmp_path, monkeypatch):
    """Точка Магнита без перечня магазинов с доставкой (deliver) — запомнена старой версией: срока в шесть часов
    не ждём, подбираем заново, иначе телефон принимал бы самовывозный магазин как экспресс до вечера."""
    import time

    from app import geo, location
    from app.connectors import magnit
    from app.models import Location

    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    try:
        monkeypatch.setattr(geo, "coords", lambda addr: (59.0, 30.0))
        monkeypatch.setattr(location, "for_store",
                            lambda code: Location(store_id="652", shop_type="DARKSTORE", delivery=True))
        monkeypatch.setattr(magnit, "stores_near",
                            lambda lat, lon, **kw: [{"code": "652", "delivery": True}, {"code": "700"}])
        repo.set_setting(location.KEY_ADDRESS, "Город, улица 1")
        old = {"shop": "652", "type": "DARKSTORE", "delivery": True, "near": ["652", "700"]}
        import hashlib
        mark = hashlib.sha1("город, улица 1".encode("utf-8")).hexdigest()[:12]
        repo.set_setting(phonecart.PLACE_KEY + "magnit",
                         json.dumps({"for": mark, "at": time.time(), "ok": time.time(), "place": old}))
        assert phonecart.rules("magnit")["place"]["deliver"] == ["652"], "старая запись подобрана заново"
    finally:
        users.deactivate()


def test_the_app_sends_a_trace_without_a_report_when_a_run_drags(web):
    """Хроника без отчёта (1.55): сборка идёт долго, отчёта нет — приложение шлёт журнал само, и он лежит
    отдельно от хроник отчётов. Пустая хроника и сеть без названия — отказ, чужое в полях не ломает приём."""
    client = logged_in(web)

    def send(**body):
        return client.post("/api/device/trace", json=body)

    ok = send(store="magnit", app_version="1.55", reason="идёт 30 с: ищу в сети незнакомые: 3",
              trace=["10:00:01.000 отчёт; часы телефона UTC+03:00", "", "  10:00:02.500   Магнит:  хвост  ", "я" * 500])
    assert ok.status_code == 200 and ok.get_json() == {"ok": True, "lines": 3}
    assert send(store="magnit", trace=[]).status_code == 400, "хроники нет — нечего хранить"
    assert send(store="", trace=["строка"]).status_code == 400, "сеть не названа"
    assert send(store="dixy", trace="не список").status_code == 400
    for n in range(phonecart.SLOW_KEEP + 2):
        assert send(store="pyaterochka", trace=[f"строка {n}"]).status_code == 200
    users.open_workspace(PHONE)
    try:
        assert phonecart.traces() == [], "хроники отчётов не тронуты"
        kept = phonecart.traces(phonecart.SLOW_KEY)
        assert len(kept) == phonecart.SLOW_KEEP and kept[-1]["trace"] == [f"строка {phonecart.SLOW_KEEP + 1}"]
        assert kept[-1]["store"] == "pyaterochka" and "reason" not in kept[-1], "причина — только если названа"
    finally:
        users.deactivate()
    # Приложение называет в хронике сеть своей причиной и укладывается в то, что хранит сервер.
    java = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "android", "app", "app",
                        "src", "main", "java", "ru", "moyakorzina", "app", "MainActivity.java")
    with open(java, encoding="utf-8") as fh:
        text = fh.read()
    assert '"/api/device/trace"' in text and "watchTrace(r);" in text
    sent = re.search(r'\.put\("trace", Log\.tail\("", "", 0, (\d+)\)\)', text)
    assert sent and int(sent.group(1)) + 1 <= phonecart.TRACE_LINES, "первая строка — часы телефона"


def test_the_app_sends_its_trace_in_a_size_the_server_keeps():
    """Хроника (1.54): приложение шлёт её с отчётом Пятёрочки, и ключ и размер — те, что принимает сервер. Значения
    куки в ней нет: только длина и очертание."""
    java = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "android", "app", "app",
                        "src", "main", "java", "ru", "moyakorzina", "app", "MainActivity.java")
    with open(java, encoding="utf-8") as fh:
        text = fh.read()
    # Журнал приложения — свой класс с тем же именем, что у android.util.Log: вызовы по файлу остались прежними.
    assert "private static final class Log {" in text and "import android.util.Log;" not in text
    sent = re.search(r'if \("pyaterochka"\.equals\(r\.code\)\) body\.put\("trace", Log\.tail\(r\.name, r\.code, (\d+), (\d+)\)\);', text)
    assert sent, "отчёт Пятёрочки несёт хронику"
    own, anywhere = int(sent.group(1)), int(sent.group(2))
    assert own + anywhere + 1 <= phonecart.TRACE_LINES, "первая строка — часы телефона; сервер хранит последние строки"
    assert "Character.isDigit(ch) ? 'd' : Character.isLetter(ch) ? 'x' : ch" in text and "shape.length() < 40" in text
    assert 'return name + " длина " + value.length() + " вид " + shape + age;' in text, "значение куки в хронику не идёт"


def test_later_why_is_the_text_the_app_sends():
    """LATER_WHY — слово в слово строка приложения (finishRun, ранний отчёт): разойдутся — и журнал отказов
    молча начнёт писать отложенные позиции как отказы."""
    java = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "android", "app", "app",
                        "src", "main", "java", "ru", "moyakorzina", "app", "MainActivity.java")
    with open(java, encoding="utf-8") as fh:
        assert f'"{phonecart.LATER_WHY}"' in fh.read()
