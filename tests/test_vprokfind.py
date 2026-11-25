"""Артикулы Впрока поиском его витрины (app/catalog/vprokfind.py).

ЗАЧЕМ (замер 16.11.2026). У владельца было 0 связей с Впроком: каталог сети собирается из карты
сайта, а она неполна и называет товар латинским слагом. Настоящие карточки отдаёт поиск самой
витрины. Здесь сторожа того, что пишется и что нет (названия карточек — настоящие ответы витрины):

1. связь пишется, когда поиск отдал ровно одну доступную карточку, которая тот же товар;
2. неверная связь хуже отсутствующей: лишняя линейка («Natura Premium»), другой процент, другая
   фасовка, другой вид («Напиток молочный»), перевод марки («Bali Flower» ≠ «Балийский цветок»),
   две подходящие карточки, карточка не в наличии — связи нет;
3. сеть, где связь уже есть или которую человек убрал, не ищется; ищется раз в TTL_DAYS;
4. сбой сети останавливает поиск и не оставляет отметок «искали»; сухой прогон ничего не пишет;
5. связь не мост, не сомнение на «Результате» и переживает проходы единой базы.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
import requests

from app import config, repo, service, users
from app.catalog import link, store, vprokfind, x5
from app.catalog.model import CrawlBlocked
from app.db import init_db
from app.models import Product


REAL_SOON = link.link_soon            # conftest подменяет его на пустышку; здесь нужен настоящий


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN, x5._DONE, vprokfind._VOCAB):
        memo.clear()
    yield
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN, x5._DONE, vprokfind._VOCAB):
        memo.clear()


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def stores() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def card(sku: str, name: str, brand: str, status: str = "available") -> dict:
    """Карточка в том виде, как её отдаёт POST /web/api/v1/catalog/search."""
    return {"productId": int(sku), "name": name, "url": f"/product/x--{sku}", "price": 135.99,
            "activityStatus": status, "brand": {"name": brand}, "category": {"id": 1, "name": "Молоко"}}


class Shelf:
    """Подмена витрины: на любой запрос отдаёт заданные карточки и помнит, о чём спрашивали."""

    def __init__(self, *cards: dict) -> None:
        self.cards, self.asked = list(cards), []

    def search(self, text: str, page: int = 1) -> dict:
        self.asked.append(text)
        return {"products": self.cards, "total": len(self.cards), "pages": 1}


def bought(name: str, brand: str, grams: float, raw: str, unit: str = "pcs") -> int:
    pid = repo.upsert_product(Product(id=None, name=name, brand=brand, weight_g=grams, unit=unit))
    repo.add_history_row("2026-10-10", stores()["perekrestok"], pid, raw, 1, 119.0, 119.0, f"k-{pid}")
    return pid


def yasno() -> int:
    return bought("Хлопья Ясно солнышко овсяные №3 350 г", "Ясно солнышко", 350.0,
                  "ЯСНО СОЛН.хлопья овсян.№3 350г")


def parmalat() -> int:
    return bought("Молоко Parmalat 1,8% ультрапастеризованное 1 л", "Parmalat", 1000.0,
                  "Молоко Parmalat, 1,8%, ультрапастеризованное, 1 л")


def pair(pid: int) -> tuple[str, str | None] | None:
    found = repo.confirmed_mapping(pid, stores()["vprok"], real_only=True)
    return (found["sku"], repo.mapping_method(pid, found["id"])) if found else None


YASNO3 = ("314341", "Хлопья Ясно солнышко Овсяные №3 350г")


def test_the_only_matching_card_is_linked(catalog, user_db):
    pid = yasno()
    shelf = Shelf(card(*YASNO3, "Ясно солнышко"),
                  card("314339", "Хлопья Ясно солнышко Овсяные №2 500г", "Ясно солнышко"),
                  card("314343", "Хлопья Ясно солнышко Овсяные с клюквой и яблоком 300г", "Ясно солнышко"))

    res = vprokfind.fill(shop=shelf)

    assert res["found"] == 1 and [p[2:] for p in res["picks"]] == [YASNO3]
    assert pair(pid) == ("314341", vprokfind.METHOD)
    mapping = repo.confirmed_mapping(pid, stores()["vprok"], real_only=True)
    assert mapping["url"] == "https://www.vprok.ru/product/x--314341"
    assert mapping["weight_g"] == 350


def test_a_plain_card_of_the_same_milk_is_linked(catalog, user_db):
    pid = parmalat()

    vprokfind.fill(shop=Shelf(card("306690", "Молоко Parmalat ультрапастеризованное 1.8% 1л", "Parmalat")))

    assert pair(pid) == ("306690", vprokfind.METHOD)


@pytest.mark.parametrize("make, cards", [
    # у витрины молоко с линейкой «Natura Premium»: слова линейки у нас нет, лишнее слово карточки
    (parmalat, [("306635", "Молоко Parmalat Natura Premium ультрапастеризованное 1.8% 1л"),
                ("306634", "Молоко Parmalat ультрапастеризованное 3.5% 1л"),
                ("306650", "Молоко Parmalat Natura Premium Comfort безлактозное 0.05% 1л"),
                ("306660", "Молоко Parmalat безлактозное 1.8% 200мл"),
                ("306670", "Напиток молочный Parmalat Protein безлактозный ультрапастеризованный 1.8% 1л")]),
    # «Bali Flower» у чека и «Балийский цветок» на витрине — один товар, но слова судья не сведёт
    (lambda: bought("Туалетная бумага Papia Bali Flower 3 слоя 8 рулонов", "Papia", 0,
                    "Туалетная бумага Papia Bali Flower, 3 слоя, 8 рулонов"),
     [("331445", "Туалетная бумага Papia Балийский цветок 8 рулонов 3 слоя"),
      ("331446", "Туалетная бумага Papia 8 рулонов 3 слоя"),
      ("331447", "Туалетная бумага Papia Клубничная мечта 8 рулонов 3 слоя")]),
    # сырок 45 г в чеке, 50 г на витрине: фасовка вне допуска
    (lambda: bought("Сырок творожный глазированный Свитлогорье с ванилью 45 г", "Свитлогорье", 45.0,
                    "СВИТЛ.Сырок твор.гл.с ван.45г"),
     [("301234", "Сырок глазированный Свитлогорье с ванилином 26% 50г")]),
])
def test_a_wrong_card_is_worse_than_none(catalog, user_db, make, cards):
    pid = make()
    brand = repo.get_product(pid).brand

    res = vprokfind.fill(shop=Shelf(*(card(sku, name, brand) for sku, name in cards)))

    assert res["none"] == 1 and res["found"] == 0
    assert pair(pid) is None


def test_two_fitting_cards_leave_the_chain_alone(catalog, user_db):
    pid = yasno()

    res = vprokfind.fill(shop=Shelf(card(*YASNO3, "Ясно солнышко"),
                                    card("314999", YASNO3[1], "Ясно солнышко")))

    assert res["ambiguous"] == 1 and pair(pid) is None


def test_a_card_that_is_not_on_sale_is_not_linked(catalog, user_db):
    pid = yasno()

    res = vprokfind.fill(shop=Shelf(card(*YASNO3, "Ясно солнышко", status="outOfStock")))

    assert res["out"] == 1 and pair(pid) is None


def test_a_product_without_brand_or_weight_is_not_even_searched(catalog, user_db):
    bought("Молоко", "", 0, "Молоко")
    shelf = Shelf(card(*YASNO3, "Ясно солнышко"))

    res = vprokfind.fill(shop=shelf)

    assert res["skipped"] == 1 and shelf.asked == []
    assert vprokfind.fill(shop=shelf)["skipped"] == 0             # отметка на неделю: не перебирать каждый проход


def test_a_refused_chain_and_a_real_link_are_not_searched(catalog, user_db):
    refused, linked = yasno(), parmalat()
    repo.drop_mapping(refused, stores()["vprok"])
    sp = repo.upsert_store_product(stores()["vprok"], "306635", "Молоко Parmalat Natura Premium 1.8% 1л")
    repo.confirm_mapping(linked, sp)                              # выбор человека
    shelf = Shelf(card(*YASNO3, "Ясно солнышко"))

    res = vprokfind.fill(shop=shelf)

    assert res["tried"] == 0 and shelf.asked == []
    assert pair(refused) is None and pair(linked) == ("306635", None)


def test_a_searched_product_waits_for_the_ttl(catalog, user_db):
    pid = yasno()
    shelf = Shelf()
    vprokfind.fill(shop=shelf)
    first = len(shelf.asked)

    vprokfind.fill(shop=shelf)
    assert len(shelf.asked) == first                              # неделя не прошла
    vprokfind.fill(shop=shelf, force=True)
    assert len(shelf.asked) == 2 * first
    repo.set_setting(vprokfind.SETTING, json.dumps({str(pid): "2020-01-01"}))
    shelf.cards = [card(*YASNO3, "Ясно солнышко")]
    vprokfind.fill(shop=shelf)

    assert pair(pid) == ("314341", vprokfind.METHOD)               # старая отметка — ищем снова


def test_a_dry_run_writes_nothing(catalog, user_db):
    pid = yasno()

    res = vprokfind.fill(write=False, shop=Shelf(card(*YASNO3, "Ясно солнышко")))

    assert res["found"] == 1 and len(res["picks"]) == 1
    assert pair(pid) is None and repo.get_setting(vprokfind.SETTING) is None


def test_basket_products_go_first_and_limit_counts_them(catalog, user_db):
    bought_only = parmalat()
    in_basket = yasno()
    repo.set_basket_item(repo.create_basket("проба"), in_basket, 1)
    shelf = Shelf(card(*YASNO3, "Ясно солнышко"))

    vprokfind.fill(shop=shelf, limit=1)

    assert pair(in_basket) is not None and shelf.asked and "Ясно" in shelf.asked[0]
    assert pair(bought_only) is None


def test_a_block_stops_the_run_and_leaves_no_marks(catalog, user_db):
    yasno(), parmalat()

    class Blocked(Shelf):
        def search(self, text, page=1):
            super().search(text, page)
            raise CrawlBlocked("vprok.ru: ответ 403 после 4 попыток с паузами")

    shelf = Blocked()

    res = vprokfind.fill(shop=shelf)

    assert res["blocked"] and len(shelf.asked) == 1 and res["tried"] == 0
    assert repo.get_setting(vprokfind.SETTING) is None


def test_three_failures_in_a_row_stop_the_run(catalog, user_db):
    bought("Молоко Parmalat 1 л", "Parmalat", 1000.0, "Молоко Parmalat 1 л")
    bought("Кефир Простоквашино 1 л", "Простоквашино", 1000.0, "Кефир Простоквашино 1 л")
    bought("Сок Добрый 1 л", "Добрый", 1000.0, "Сок Добрый 1 л")
    bought("Чай Greenfield 100 г", "Greenfield", 100.0, "Чай Greenfield 100 г")

    class Broken(Shelf):
        def search(self, text, page=1):
            super().search(text, page)
            raise requests.ConnectionError("нет связи")

    shelf = Broken()

    res = vprokfind.fill(shop=shelf)

    assert "три сбоя подряд" in res["blocked"] and len(shelf.asked) == 3
    assert repo.get_setting(vprokfind.SETTING) is None


def test_cards_of_reads_the_real_answer():
    data = {"products": [
        {"productId": 306635, "name": "Молоко Parmalat Natura Premium ультрапастеризованное 1.8% 1л",
         "url": "/product/parmalat-parmal-moloko-1-8-ster-1l--306635", "price": 135.99,
         "activityStatus": "available", "brand": {"name": "Parmalat", "id": 197057}},
        {"productId": 1, "name": ""}, {"name": "без артикула"}, {"productId": 5, "name": "Без марки", "brand": None}],
        "total": 4}

    got = vprokfind.cards_of(data)

    assert got[0] == vprokfind.Card("306635", "Молоко Parmalat Natura Premium ультрапастеризованное 1.8% 1л",
                                    "https://www.vprok.ru/product/parmalat-parmal-moloko-1-8-ster-1l--306635",
                                    "Parmalat", "available")
    assert [c.sku for c in got] == ["306635", "5"] and got[1].brand is None and got[1].url is None


def test_queries_drop_numbers_and_widen_on_the_brand(catalog, user_db):
    pid = yasno()
    product = repo.get_product(pid)
    _, pbrand, _ = x5._evidence(product, set())

    assert vprokfind.queries(product, pbrand) == ["Хлопья Ясно солнышко овсяные", "Ясно солнышко Хлопья"]


def test_the_session_is_reopened_once_when_the_shop_says_401(monkeypatch):
    opened: list[int] = []

    class Answer:
        history: list = []
        cookies = requests.cookies.RequestsCookieJar()

        def json(self):
            return {"products": []}

    Answer.cookies.set("XSRF-TOKEN", "t%3D1")
    monkeypatch.setattr(vprokfind, "http_get", lambda *a, **k: opened.append(1) or Answer())
    posts = [CrawlBlocked("https://www.vprok.ru/web/api/v1/catalog/search: ответ 401 — нужен ключ"), Answer()]

    def post(*args, **kwargs):
        got = posts.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(vprokfind, "http_post", post)
    shop = vprokfind.Shop()

    assert shop.search("молоко") == {"products": []}
    assert len(opened) == 2 and shop.xsrf == "t=1"


def test_the_search_goes_slower_than_the_common_pace():
    """При 1 запросе в секунду витрина отвечала 429 на каждый 14-й: поиск идёт раз в 2 с."""
    assert vprokfind.Shop().pace.gap == 2.0


def test_the_link_survives_the_passes_of_the_unified_base(catalog, user_db):
    """Полный проход единой базы дважды: связь стоит, единого товара по ней не появилось."""
    pid = bought("Ясно солн хлопья овсян №3 350 г", "Ясно солнышко", 350.0, "ЯСНО СОЛН.хлопья овсян.№3 350г")
    vprokfind.fill(shop=Shelf(card(*YASNO3, "Ясно солнышко")))
    assert pair(pid) == ("314341", vprokfind.METHOD)

    for _ in range(2):
        link.link_products()
        for memo in (link._SEEN, link._SYNCED, link._WEAK, x5._DONE):
            memo.clear()

    assert pair(pid) == ("314341", vprokfind.METHOD)
    assert repo.master_of(pid) is None or repo.master_of(pid)["method"] != "master"


def test_the_link_is_no_doubt_on_the_result_screen(catalog, user_db):
    """«Сыр творожный Hochland» против «Hochland творожный»: похожесть букв дала бы сомнение «тип»."""
    pid = bought("Hochland зеленью творожный 60 бзмж 220 г", "Hochland", 220.0,
                 "Сыр Hochland С зеленью творожный 60% БЗМЖ 220г")
    basket = repo.create_basket("проба")
    repo.set_basket_item(basket, pid, 1)
    vprokfind.fill(shop=Shelf(card("308018", "Сыр творожный Hochland с зеленью 60% 220г", "Hochland")))
    assert pair(pid) == ("308018", vprokfind.METHOD)

    assert service.basket_doubts(basket) == []
    with repo.get_conn() as c:                      # то же сопоставление без метки: так его видел бы человек
        c.execute("UPDATE product_mapping SET method=NULL WHERE product_id=?", (pid,))
        c.commit()
    assert [d["flags"] for d in service.basket_doubts(basket)] == [["тип: творог ≠ сыр"]]


def _soon(phone: str, monkeypatch, fill=None) -> list[str]:
    """Настоящий link_soon с подменёнными проходом, поиском и рабочим местом; что и в каком порядке позвали."""
    calls: list[str] = []
    monkeypatch.setattr(users, "open_workspace", lambda p: calls.append("open"))
    monkeypatch.setattr(users, "deactivate", lambda: None)
    monkeypatch.setattr(link, "link_products", lambda **kw: calls.append("link"))
    monkeypatch.setattr(vprokfind, "fill", fill or (lambda **kw: calls.append("vprok")))
    assert REAL_SOON(phone) is True
    for thread in [t for t in threading.enumerate() if t.name.startswith("catalog-link-")]:
        thread.join(10)
    deadline = time.time() + 10
    while phone in link._RUNNING and time.time() < deadline:
        time.sleep(0.05)
    assert phone not in link._RUNNING
    return calls


def test_the_pass_of_the_unified_base_is_followed_by_the_search_in_vprok(monkeypatch):
    assert _soon("79990000020", monkeypatch) == ["open", "link", "vprok"]


def test_the_search_in_vprok_can_be_switched_off_by_the_config(monkeypatch):
    real = config.get
    monkeypatch.setattr(config, "get", lambda path, default=None:
                        False if path == "catalog.vprok_search" else real(path, default))

    assert _soon("79990000021", monkeypatch) == ["open", "link"]


def test_a_failed_search_does_not_leave_the_workplace_busy(monkeypatch):
    def broken(**kw):
        raise RuntimeError("витрина упала")

    assert _soon("79990000022", monkeypatch, fill=broken) == ["open", "link"]
