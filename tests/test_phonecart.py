"""Корзина руками телефона: наряд для приложения и приём его отчёта.

Стережётся три вещи. Телефон получает ВСЮ корзину на артикулах сети и те же правила
кнопок, что у серверной передачи, а не свои. Отчёт хранит только суммы и имена
полей: адрес, телефон и имя из корзины сети не доезжают до базы, даже если их
прислали. И без входа в «Мою корзину» нет ни наряда, ни приёма.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import phonecart, repo, users  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import cart, signals  # noqa: E402

PHONE = "79990000088"


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
    client.post("/login", data={"phone": PHONE, "next": "/"})
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
                                 "total": 1178.9, "network": None}
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


def test_a_new_report_replaces_the_old_one(web):
    client = logged_in(web)
    client.post("/api/device/cartreport", json=REPORT)
    client.post("/api/device/cartreport", json=dict(REPORT, items=REPORT["items"][:1],
                                                     page_totals=[]))

    users.open_workspace(PHONE)
    saved = phonecart.last("magnit")
    assert saved["planned"] == 1 and saved["landed"] == 1
    assert phonecart.total_of(saved) is None
