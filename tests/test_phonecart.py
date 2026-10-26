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


def test_a_position_without_the_networks_name_keeps_its_own(web, monkeypatch):
    """Поля `found_name` нет (старое приложение) — прежнее `name` из отчёта."""
    calls = _learned(web, monkeypatch, "dixy", [
        {"name": "Молоко 3,2% 930 мл", "sku": DIXY_ARTICLE, "ok": False,
         "url": DIXY_CARD},
    ])

    assert [row["name"] for row in calls[0][1]] == ["Молоко 3,2% 930 мл"]


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
