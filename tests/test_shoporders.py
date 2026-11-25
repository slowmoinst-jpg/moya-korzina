"""Заказы из кабинета сети: приём от телефона (app/importers/shoporders.py).

Заказ ложится в историю и даёт товару настоящий артикул сети; повторная пачка, чек ФНС той
же покупки и выбор человека ничего не ломают; сервер сам отмеряет, сколько читать у сети.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import api, config, repo, users
from app.catalog.link import PRODUCE
from app.db import init_db
from app.importers import shoporders
from app.importers.bundle import store_receipts
from app.importers.ofd_pdf import Receipt, ReceiptRow
from app.matcher.normalize import display_name
from app.models import Product
from app.web import create_app

PHONE = "79991112233"
MILK = "Молоко Домик в деревне 2,5% 930 мл"
BREAD = "Хлеб Бородинский нарезка 400 г"
KEFIR = "Кефир Простоквашино 1% 930 мл"
POTATO = "Картофель мытый"
MILK_SKU = "moloko-domik-v-derevne-2-5-930-ml"
BREAD_SKU = "khleb-borodinskiy-narezka-400-g"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "orders.db"))
    init_db()


@pytest.fixture
def door(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    secret = api.workplace_secret()
    users.deactivate()
    application = create_app()
    application.config.update(TESTING=True)
    yield application, secret
    users.deactivate()


def order(oid: str = "A1", day: str = "2026-09-08", total: float = 328.0, lines: bool = True) -> dict:
    """Заказ, как его шлёт телефон: с составом или строкой описи."""
    body: dict = {"id": oid, "date": day, "total": total}
    if lines:
        body["lines"] = [
            {"sku": MILK_SKU, "name": MILK, "qty": 1, "price": 139.0,
             "url": "https://samokat.ru/product/" + MILK_SKU},
            {"sku": BREAD_SKU, "name": BREAD, "qty": 3, "price": 63.0}]
    return body


def cheque(key: str, total: float = 328.0) -> dict:
    """Чек ФНС той же покупки: те же названия, что в заказе, артикулов нет."""
    return {"key": key, "receipt": Receipt(date="2026-09-08", store_name="Самокат", total=total, rows=[
        ReceiptRow(raw_name=MILK, qty=1, unit_price=139.0, total=139.0),
        ReceiptRow(raw_name=BREAD, qty=3, unit_price=63.0, total=189.0)])}


def count(sql: str) -> int:
    with repo.get_conn() as c:
        return c.execute(sql).fetchone()[0]


def sku_of(name: str, chain: str = "samokat") -> tuple[str | None, str | None]:
    """Настоящий артикул товара в сети и откуда связь."""
    product = repo.find_product_by_name(display_name(name))
    held = repo.confirmed_mapping(product.id, repo.get_store(chain).id, real_only=True)
    return (held["sku"], repo.mapping_method(product.id, held["id"])) if held else (None, None)


def test_door_takes_orders_only_from_a_signed_in_phone(door):
    application, secret = door
    signed = {"workplace": PHONE, "secret": secret}
    with application.test_client() as client:
        assert client.post("/api/device/orders", json={"store": "samokat"}).status_code in (401, 403)
        res = client.post("/api/device/orders", json={**signed, "store": "samokat", "orders": [order()]})
        assert res.status_code == 200, res.data
        assert res.get_json()["imported"] == 1
        assert client.post("/api/device/orders", json={**signed, "store": "apteka"}).status_code == 400


def test_order_becomes_history_with_the_chain_sku(db):
    got = shoporders.accept({"store": "samokat", "orders": [order()]})

    assert (got["imported"], got["twins"], got["linked"], got["skipped"]) == (1, 0, 2, 0)
    assert count("SELECT COUNT(*) FROM purchase_history h JOIN stores s ON s.id = h.store_id"
                 " WHERE s.code = 'samokat' AND h.receipt_key = 'order:samokat:A1'") == 2
    assert sku_of(MILK) == (MILK_SKU, "order")
    assert sku_of(BREAD) == (BREAD_SKU, "order")


def test_repeated_batch_adds_nothing(db):
    body = {"store": "samokat", "orders": [order()]}
    shoporders.accept(body)
    before = (count("SELECT COUNT(*) FROM purchase_history"), count("SELECT COUNT(*) FROM product_mapping"),
              count("SELECT COUNT(*) FROM receipts"))

    again = shoporders.accept(body)

    assert (again["imported"], again["twins"]) == (0, 0)
    assert (count("SELECT COUNT(*) FROM purchase_history"), count("SELECT COUNT(*) FROM product_mapping"),
            count("SELECT COUNT(*) FROM receipts")) == before


def test_order_known_by_its_receipt_gives_skus_only(db):
    # ФНС хранит заказ доставки двумя чеками; заказ около полуночи приходит соседним днём
    store_receipts([cheque("fns-1"), cheque("fns-2")], store_code="samokat", source="lkdr")
    body = {"store": "samokat", "orders": [order(day="2026-09-09")]}

    got = shoporders.accept(body)

    assert (got["imported"], got["twins"], got["linked"]) == (0, 1, 2)
    assert count("SELECT COUNT(*) FROM purchase_history") == 4, "строк чеков хватает, заказ своих не добавил"
    assert sku_of(BREAD) == (BREAD_SKU, "order")
    assert len(repo.imported_receipts()) == 2, "заказ-двойник чеком не считается"
    assert shoporders.accept(body)["twins"] == 0
    listed = shoporders.accept({"store": "samokat", "end": True,
                                "orders": [order(day="2026-09-09", lines=False)]})
    assert listed["need"] == [], "состав разобранного заказа второй раз не спрашиваем"


def test_receipt_after_the_order_adds_nothing(db):
    shoporders.accept({"store": "samokat", "orders": [order()]})
    first = cheque("fns-1")
    first["receipt"].rows[0].barcode = "4607025390015"

    got = store_receipts([first, cheque("fns-2"), cheque("fns-3", total=500.0)],
                         store_code="samokat", source="lkdr")

    assert (got["receipts"], got["skipped"]) == (1, 2), "чеки заказа учтены без строк, другая покупка легла"
    assert count("SELECT COUNT(*) FROM purchase_history") == 4
    assert repo.pending_receipts() == [], "учтённый чек у ФНС больше не спрашиваем"
    assert repo.find_product_by_name(display_name(MILK)).barcode == "4607025390015", "штрихкод чека не пропал"
    assert store_receipts([cheque("fns-1")], store_code="samokat", source="lkdr")["skipped"] == 1


def test_same_total_two_days_apart_is_another_purchase(db):
    store_receipts([cheque("fns-1")], store_code="samokat", source="lkdr")

    got = shoporders.accept({"store": "samokat", "orders": [order(day="2026-09-10")]})

    assert (got["imported"], got["twins"]) == (1, 0)
    assert count("SELECT COUNT(*) FROM purchase_history") == 4


def test_rows_of_a_broken_attempt_are_not_doubled(db):
    shoporders.accept({"store": "samokat", "orders": [order()]})
    with repo.get_conn() as c:          # сбой до отметки заказа: строки легли, заказ не числится
        c.execute("DELETE FROM receipts WHERE key = 'order:samokat:A1'")
        c.commit()

    got = shoporders.accept({"store": "samokat", "orders": [order()]})

    assert got["imported"] == 1
    assert count("SELECT COUNT(*) FROM purchase_history") == 2


def test_human_choice_and_refusal_stay_and_a_guess_gives_way(db):
    shop = repo.get_store("samokat")

    def held(name: str, sku: str, method: str | None = None) -> int:
        pid = repo.upsert_product(Product(id=None, name=display_name(name), unit="pcs"))
        repo.confirm_mapping(pid, repo.upsert_store_product(shop.id, sku, name), confirmed=True,
                             method=method)
        return pid

    held(MILK, "moloko-drugoe")                                 # карточку выбрал человек
    held(BREAD, "khleb-po-nazvaniyu", "x5")                     # догадка по названию из чека
    repo.drop_mapping(held(KEFIR, "kefir-staryy", "x5"), shop.id)   # связь с сетью человек убрал
    lines = order()["lines"] + [{"sku": "kefir-novyy", "name": KEFIR, "qty": 1, "price": 90.0}]

    got = shoporders.accept({"store": "samokat", "orders": [{**order(total=418.0), "lines": lines}]})

    assert got["linked"] == 1
    assert sku_of(MILK) == ("moloko-drugoe", None)
    assert sku_of(BREAD) == (BREAD_SKU, "order")
    assert sku_of(KEFIR) == (None, None)
    assert count("SELECT COUNT(*) FROM product_mapping WHERE confirmed = 1") == 2, "догадка снята"


def test_cheapest_produce_row_stays(db):
    # у овощей без бренда связь ведёт app/catalog/produce.py: самая дешёвая за кг строка сети
    shop = repo.get_store("samokat")
    pid = repo.upsert_product(Product(id=None, name=display_name(POTATO), unit="kg"))
    repo.confirm_mapping(pid, repo.upsert_store_product(shop.id, "kartofel-deshevyy", POTATO),
                         confirmed=True, method=PRODUCE)
    bought = {**order(total=60.0), "lines": [{"sku": "kartofel-mytyy", "name": POTATO, "qty": 1.5, "price": 40.0}]}

    got = shoporders.accept({"store": "samokat", "orders": [bought]})

    assert (got["imported"], got["linked"]) == (1, 0)
    assert sku_of(POTATO) == ("kartofel-deshevyy", PRODUCE)


def test_dixy_sku_comes_in_the_case_the_base_keeps(db):
    # в базе артикул Дикси заглавными (DI…, с ценой); строчный из заказа завёл бы вторую строку
    shop = repo.get_store("dixy")
    pid = repo.upsert_product(Product(id=None, name=display_name(MILK), unit="pcs"))
    repo.confirm_mapping(pid, repo.upsert_store_product(shop.id, "DI00078791", MILK), confirmed=True,
                         method="master")
    bought = {**order(total=139.0), "lines": [{"sku": "di00078791", "name": MILK, "qty": 1, "price": 139.0}]}

    got = shoporders.accept({"store": "dixy", "orders": [bought]})

    assert got["linked"] == 1
    assert sku_of(MILK, "dixy")[0] == "DI00078791"
    assert count("SELECT COUNT(*) FROM store_products WHERE upper(sku) = 'DI00078791'") == 1


def test_listing_asks_for_details_within_the_budget(db, monkeypatch):
    monkeypatch.setattr(config, "get", lambda path, default=None:
                        2 if path == "orders.details_per_sync" else default)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(shoporders, "time", SimpleNamespace(time=lambda: clock[0]))
    listing = [order(oid, day, lines=False) for oid, day in
               (("A3", "2026-09-08"), ("A2", "2026-08-14"), ("A1", "2026-08-12"))]

    assert shoporders.accept({"store": "samokat"})["due"] is True
    page = shoporders.accept({"store": "samokat", "orders": listing[:2]})
    assert (page["more"], page["need"]) == (True, []), "опись ещё не дочитана до конца"
    last = shoporders.accept({"store": "samokat", "orders": listing[2:], "end": True})
    assert (last["more"], last["need"], last["due"]) == (False, ["A3", "A2"], False)

    sent = shoporders.accept({"store": "samokat",
                              "orders": [order("A3"), order("A2", "2026-08-14")]})
    assert (sent["imported"], sent["need"]) == (2, []), "за заход — два состава, третий подождёт"

    clock[0] += 25 * 3600
    assert shoporders.accept({"store": "samokat"})["due"] is True
    tomorrow = shoporders.accept({"store": "samokat", "orders": listing[:2]})
    assert (tomorrow["more"], tomorrow["need"]) == (False, ["A1"]), "новых заказов нет — дальше не читаем"


def test_empty_last_page_ends_the_listing(db):
    assert shoporders.accept({"store": "samokat", "orders": [order(lines=False)]})["more"] is True

    last = shoporders.accept({"store": "samokat", "orders": [], "end": True})

    assert (last["more"], last["need"], last["due"]) == (False, ["A1"], False)
    assert shoporders.accept({"store": "samokat"})["due"] is False, "конец описи запомнен"


def test_repeated_page_does_not_cut_the_listing_short(db):
    old = [order(f"A{n}", f"2026-08-0{n}", lines=False) for n in (2, 1)]
    new = [order(f"N{n}", f"2026-09-0{n}", lines=False) for n in (4, 3, 2, 1)]
    shoporders.accept({"store": "samokat", "orders": old, "end": True})     # прошлый заход: опись дочитана

    first = shoporders.accept({"store": "samokat", "orders": new[:2]})
    again = shoporders.accept({"store": "samokat", "orders": new[:2]})      # ответ потерялся, страница пришла снова
    second = shoporders.accept({"store": "samokat", "orders": new[2:]})
    last = shoporders.accept({"store": "samokat", "orders": old})

    assert (first["more"], again["more"], second["more"], last["more"]) == (True, True, True, False)
    assert last["need"][:4] == ["N4", "N3", "N2", "N1"], "заказы второй страницы не потерялись"


def test_bad_details_spend_the_budget_and_wait_for_the_next_visit(db, monkeypatch):
    monkeypatch.setattr(config, "get", lambda path, default=None:
                        1 if path == "orders.details_per_sync" else default)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(shoporders, "time", SimpleNamespace(time=lambda: clock[0]))
    listing = {"store": "samokat", "end": True, "orders": [order(lines=False)]}
    assert shoporders.accept(listing)["need"] == ["A1"]
    bad = {**order(), "lines": order()["lines"] + [{"sku": "x", "name": KEFIR, "qty": 1, "price": "дорого"}]}

    got = shoporders.accept({"store": "samokat", "orders": [bad]})

    assert (got["skipped"], got["imported"], got["need"]) == (1, 0, []), "за негодным составом по кругу не ходим"
    assert count("SELECT COUNT(*) FROM purchase_history") == 0, "ползаказа в историю не кладём"
    clock[0] += 25 * 3600
    assert shoporders.accept(listing)["need"] == ["A1"], "в следующий заход спросим снова"


def test_one_bad_order_does_not_eat_the_whole_budget(db, monkeypatch):
    monkeypatch.setattr(config, "get", lambda path, default=None:
                        3 if path == "orders.details_per_sync" else default)
    days = {f"A{n}": f"2026-09-0{n}" for n in (1, 2, 3, 4, 5)}
    listing = [order(oid, day, lines=False) for oid, day in days.items()]
    assert shoporders.accept({"store": "samokat", "end": True, "orders": listing})["need"] == ["A5", "A4", "A3"]
    bad = {**order("A5", days["A5"]), "lines": [{"sku": "x", "name": KEFIR, "qty": "много", "price": 90.0}]}

    asked = [shoporders.accept({"store": "samokat", "orders": [one]})["need"]
             for one in (bad, order("A4", days["A4"]), order("A3", days["A3"]))]

    assert asked == [["A4", "A3"], ["A3"], []], "негодный заказ спрошен один раз, соседи прочитаны"
    assert count("SELECT COUNT(DISTINCT receipt_key) FROM purchase_history") == 2


def test_rubbish_is_refused_or_skipped(db):
    for body in ({"store": "apteka"}, {"store": "metro"}, {"store": "samokat", "orders": "все"}):
        with pytest.raises(ValueError):
            shoporders.accept(body)
    bad = [order("плохой номер!"), order(day="08.09.2026"), order(day="2999-01-01"),
           order(total=-1), "мусор", order("A1\n"),
           {**order("B2"), "lines": [{"sku": "x", "name": 5, "qty": 1, "price": 139.0}]}]
    never = {**order("B1"), "lines": [{"sku": "x", "name": MILK, "qty": 0, "price": 139.0}]}

    got = shoporders.accept({"store": "samokat", "orders": bad + [never]})

    assert (got["skipped"], got["imported"], got["linked"]) == (7, 0, 0)
    assert count("SELECT COUNT(*) FROM purchase_history") == 0, "строку с количеством 0 не привезли"
