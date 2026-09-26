"""Единый каталог: база, сопоставление между сетями, обход и дверь в базу человека.

Сети здесь не трогаются: сборщики поддельные, а разборщики ответов проверяются на
образцах, снятых разведкой 16.09.2026 с настоящих ответов сетей.
"""
from __future__ import annotations

from typing import Iterator

import pytest

from app import config, repo
from app.catalog import match, refresh, store, worker
from app.catalog.crawlers import dixy, lenta, magnit, vkusvill
from app.catalog.model import ChainProduct, CrawlBlocked, Crawler
from app.db import init_db


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    yield


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


class FakeCrawler(Crawler):
    code = "lenta"
    name = "Лента"

    def __init__(self, products: list[ChainProduct], blocked: bool = False) -> None:
        self.products = products
        self.blocked = blocked

    def crawl(self, progress=None) -> Iterator[ChainProduct]:
        if self.blocked:
            raise CrawlBlocked("Qrator")
        yield from self.products


P = ChainProduct


# ---------- база ----------
def test_upsert_counts_new_changed_and_seen_only(catalog):
    seen, added, updated = store.upsert_products("lenta", [
        P("1", "Молоко 930 мл"), P("2", "Хлеб 400 г")], seen_at="2026-09-16T00:00:00")
    assert (seen, added, updated) == (2, 2, 0)
    seen, added, updated = store.upsert_products("lenta", [
        P("1", "Молоко 930 мл", price=89.99),        # цена — не изменение
        P("2", "Хлеб Бородинский 400 г"),           # название — изменение, привязка сброшена
        P("3", "", seen_only=True),                  # незнакомый seen_only — ждёт очереди
    ], seen_at="2026-09-17T00:00:00")
    assert (seen, added, updated) == (3, 0, 1)
    rows = {r["sku"]: r for r in store.products_of_chain("lenta")}
    assert rows["1"]["price"] == 89.99 and rows["2"]["name"] == "Хлеб Бородинский 400 г"
    assert "3" not in rows


def test_unseen_products_retire_only_after_a_full_run(catalog):
    store.upsert_products("lenta", [P("1", "Молоко"), P("2", "Хлеб")], seen_at="2026-09-16T00:00:00")
    gone = store.retire_unseen("lenta", "2026-09-17T00:00:00")
    assert gone == 2
    assert store.products_of_chain("lenta") == []
    assert len(store.products_of_chain("lenta", active_only=False)) == 2


# ---------- сопоставление ----------
def test_same_product_across_chains_is_one_item(catalog):
    store.upsert_products("lenta", [P("80424", "Молоко пастеризованное ПРОСТОКВАШИНО 2,5%, без змж, 930мл",
                                      weight_g=930)])
    store.upsert_products("vkusvill", [P("36296", "Молоко Простоквашино 2,5% 930 мл", brand="Простоквашино",
                                         weight_g=930)])
    store.upsert_products("magnit", [P("1000", "Молоко Простоквашино 2,5% 930мл")])
    summary = refresh.match_all()
    assert summary["items"] == 1 and summary["multi"] == 1
    items = store.search_items("простоквашино")
    assert len(items) == 1 and items[0]["chains_n"] == 3


def test_pack_size_separates_products(catalog):
    store.upsert_products("lenta", [P("1", "Страчателла 200 г")])
    store.upsert_products("vkusvill", [P("2", "Страчателла 92 г")])
    assert refresh.match_all()["items"] == 2


def test_broader_name_is_another_product(catalog):
    store.upsert_products("lenta", [P("1", "Персики консервированные 500 г", unit="pcs")])
    store.upsert_products("magnit", [P("2", "Персики", unit="kg"), P("3", "Персики консервированные 500г")])
    assert refresh.match_all()["items"] == 2
    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["1"]["item_id"] == rows["3"]["item_id"] != rows["2"]["item_id"]


def test_extra_qualifiers_need_a_brand_and_a_pack_size(catalog):
    # без марки короткое название — более широкий товар, а не тот же
    store.upsert_products("lenta", [P("1", "Молоко пастеризованное 930 мл")])
    store.upsert_products("magnit", [P("2", "Молоко 930 мл")])
    assert refresh.match_all()["items"] == 2
    # с маркой в обеих и фасовкой — тот же
    store.upsert_products("lenta", [P("1", "Молоко пастеризованное ЛЕНТА 2,5%, без змж, 900мл")])
    store.upsert_products("magnit", [P("2", "Молоко Лента 2,5% 900мл")])
    assert refresh.match_all()["items"] == 1


def test_brand_conflict_separates_and_barcode_wins(catalog):
    store.upsert_products("lenta", [P("1", "Молоко 2,5% 930 мл", brand="Простоквашино")])
    store.upsert_products("magnit", [P("2", "Молоко 2,5% 930 мл", brand="Домик в деревне")])
    store.upsert_products("vkusvill", [P("3", "Молочко детское 200 мл", barcode="4600000000019"),
                                       P("4", "Совсем другой товар", barcode="4600000000026")])
    store.upsert_products("dixy", [P("5", "Молочко 200 мл", barcode="4600000000019")])
    summary = refresh.match_all()
    # марки спорят — два товара; штрихкод склеивает разные названия — один; плюс отдельный
    assert summary["items"] == 4
    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["3"]["item_id"] == rows["5"]["item_id"] and rows["3"]["link_method"] == "barcode"
    assert rows["1"]["item_id"] != rows["2"]["item_id"]


def test_two_skus_of_one_chain_do_not_merge(catalog):
    store.upsert_products("lenta", [P("1", "Вода 500 мл"), P("2", "Вода 500 мл")])
    assert refresh.match_all()["items"] == 2


def test_meaningful_words_drop_numbers_and_units():
    assert match.meaningful("Молоко 2,5% 930 мл шт") == frozenset({"молоко"})
    assert match.weights_agree(930, 900) and not match.weights_agree(200, 92)
    assert match.weights_agree(None, 900)


# ---------- обход ----------
def test_run_records_added_updated_and_gone(catalog):
    first = refresh.run_chain(FakeCrawler([P("1", "Молоко"), P("2", "Хлеб")]))
    assert first["status"] == "ok" and first["added"] == 2 and first["gone"] == 0
    second = refresh.run_chain(FakeCrawler([P("1", "Молоко 930 мл")]))
    assert second["updated"] == 1 and second["gone"] == 1
    counts = store.chain_counts()["lenta"]
    assert counts["products"] == 1 and counts["status"] == "ok"


def test_blocked_chain_keeps_its_catalog(catalog):
    refresh.run_chain(FakeCrawler([P("1", "Молоко")]))
    blocked = refresh.run_chain(FakeCrawler([], blocked=True))
    assert blocked["status"] == "blocked" and "Qrator" in blocked["note"]
    assert len(store.products_of_chain("lenta")) == 1
    assert store.chain_counts()["lenta"]["status"] == "blocked"


def test_adopt_puts_item_into_the_persons_base(catalog, user_db):
    store.upsert_products("lenta", [P("80424", "Молоко Простоквашино 2,5% 930 мл", price=89.99,
                                      url="https://lenta.com/product/x-80424/")])
    store.upsert_products("vkusvill", [P("36296", "Молоко Простоквашино 2,5% 930 мл", price=100.0)])
    refresh.match_all()
    item = store.search_items("молоко")[0]
    product_id = refresh.adopt(item["id"])
    assert refresh.adopt(item["id"]) == product_id           # повтор не плодит эталонов
    stores = {s.code: s.id for s in repo.list_stores()}
    lenta = repo.confirmed_mapping(product_id, stores["lenta"])
    vv = repo.confirmed_mapping(product_id, stores["vkusvill"])
    assert lenta["sku"] == "80424" and vv["sku"] == "36296"
    assert repo.latest_price_for(product_id, stores["lenta"])["price"] == 89.99


# ---------- разбор ответов сетей (образцы разведки) ----------
def test_magnit_tree_and_item():
    tree = [{"id": 4998, "name": "Рыба, морепродукты", "children": [
        {"id": 38559, "name": "Дары моря", "children": []},
        {"id": 5003, "name": "Крабовые палочки", "children": []}]}]
    assert magnit.leaves(tree) == [(38559, "Рыба, морепродукты / Дары моря"),
                                   (5003, "Рыба, морепродукты / Крабовые палочки")]
    item = {"id": "1000483001", "name": "Крабовые палочки Vici с мясом натурального краба 170г",
            "seoCode": "vici_krab", "price": 13999, "quantity": 25,
            "weighted": {"isWeighted": False}, "gallery": [{"type": "IMAGE", "url": "https://img/1.jpeg"}]}
    p = magnit.to_product(item, "Рыба / Крабовые палочки")
    assert p.sku == "1000483001" and p.price == 139.99 and p.in_stock and p.unit == "pcs"
    assert p.weight_g == 170.0
    assert p.url == "https://magnit.ru/product/1000483001-vici_krab" and p.image == "https://img/1.jpeg"
    weighed = magnit.to_product({"id": "7", "name": "Креветки Королевские", "price": 24999, "quantity": 0,
                                 "weighted": {"isWeighted": True, "shelfWeight": 500}}, None)
    assert weighed.unit == "kg" and weighed.in_stock is False and weighed.weight_g is None


def test_lenta_item():
    p = lenta.to_product({"id": 671969, "name": "Молоко пастеризованное ЛЕНТА 2,5%, без змж, 900мл",
                          "price": 91.99, "stock": 68, "package": "900мл", "slug": "moloko",
                          "url": "https://lenta.com/product/moloko-671969/"})
    assert p.sku == "671969" and p.weight_g == 900 and p.price == 91.99 and p.in_stock
    assert lenta.PRODUCT_URL.search("https://lenta.com/product/rk-gorbusha-240g-21/").group("id") == "21"


def test_vkusvill_item():
    p = vkusvill.to_product({"id": 36296, "xml_id": 36296, "name": "Молоко 2,5% в бутылке, 900&amp;nbsp;мл",
                             "brand": "ВкусВилл", "price": {"current": 100}, "unit": "шт",
                             "weight": {"value": 0.9, "unit": "кг"},
                             "url": "https://vkusvill.ru/goods/moloko-2-36296/",
                             "images": [{"small": "s", "medium": "m", "large": "l"}],
                             "category": [{"id": 50390, "name": "Молоко, сливки, сгущёнка"},
                                          {"id": 50388, "name": "Молочные продукты, яйцо"}]})
    assert p.name == "Молоко 2,5% в бутылке, 900 мл" and p.weight_g == 900 and p.brand == "ВкусВилл"
    assert p.category == "Молочные продукты, яйцо / Молоко, сливки, сгущёнка" and p.image == "m"
    assert vkusvill.PRODUCT_URL.search("https://vkusvill.ru/goods/moloko-2-36296/").group("id") == "36296"


def test_dixy_item():
    p = dixy.to_product({"id": "2000642072", "name": "Молоко Neo High Protein 0,5% 950г", "brand": "Neo",
                         "categories": [{"id": "13688", "name": "Молоко"}], "image_url": "https://d/1.webp"})
    assert p.sku == "2000642072" and p.brand == "Neo" and p.category == "Молоко"
    assert p.url == "https://dixy.ru/product/2000642072" and p.price is None


def test_worker_defaults():
    # METRO и Перекрёсток добавлены 19.09.2026: у METRO цена по точке, остаток и
    # штрихкоды одним ответом, у Перекрёстка — каталог без цен из карты сайта.
    # Fix Price добавлен 20.09.2026: цены и наличие, но только через наш браузер.
    assert set(worker.chains()) == {"magnit", "dixy", "vkusvill", "lenta", "metro",
                                    "perekrestok", "vprok", "monetka", "fixprice",
                                    "pyaterochka", "samokat"}
    # Порядок здесь не косметика. Обход идёт по очереди и обрывается любой выкладкой,
    # поэтому сети С ЦЕНАМИ обязаны стоять раньше бесценных. Замер 20.09.2026: vprok и
    # monetka стояли в хвосте и не собрались НИ РАЗУ, в базе у обоих было по нулям.
    order = worker.chains()
    withprices = max(order.index(c) for c in ("magnit", "metro", "fixprice"))
    nameonly = min(order.index(c) for c in ("perekrestok", "vprok", "monetka"))
    assert withprices < nameonly, "сети с ценами должны обходиться раньше бесценных"
    planned = worker._next_run("03:30")
    assert (planned.hour, planned.minute) == (3, 30)


@pytest.fixture
def quiet_run(catalog, monkeypatch):
    """Обход без сетей: две сети, поддельные сборщики, счёт обходов и сопоставлений."""
    seen: dict[str, list] = {"crawled": [], "matched": []}
    monkeypatch.setattr(worker, "chains", lambda: ["magnit", "metro"])
    monkeypatch.setattr(worker.places, "points", lambda code: [])
    monkeypatch.setattr(worker, "make", lambda code, spots: code)
    monkeypatch.setattr(worker.refresh, "run_chain", lambda crawler, progress=None: (
        seen["crawled"].append(crawler) or {"chain": crawler, "status": "ok", "seen": 1}))
    monkeypatch.setattr(worker.refresh, "match_all", lambda progress=None: (
        seen["matched"].append(1) or {"items": 1}))
    return seen


def _crawled(chain: str, status: str = "ok") -> None:
    store.finish_run(store.start_run(chain), status, seen=5)


def test_planned_run_skips_a_chain_crawled_this_evening(quiet_run):
    """Внеочередной обход вечером — плановый в 03:30 не идёт по той же сети второй раз."""
    _crawled("magnit")

    worker.run_all(fresh=12)

    assert quiet_run["crawled"] == ["metro"]


def test_manual_run_crawls_even_a_fresh_chain(quiet_run):
    """Человек попросил обойти сейчас — значит сейчас, свежесть тут не довод."""
    _crawled("magnit")

    worker.run_all()

    assert quiet_run["crawled"] == ["magnit", "metro"]


def test_a_failed_or_blocked_crawl_is_not_fresh(quiet_run):
    """Упавший обход оставил сеть с прежними ценами — плановый обязан попробовать снова."""
    _crawled("magnit", "failed")
    _crawled("metro", "blocked")

    worker.run_all(fresh=12)

    assert quiet_run["crawled"] == ["magnit", "metro"]


def test_matching_runs_once_per_crawled_chain_and_not_again_at_the_end(quiet_run):
    """Лишний match_all после цикла стоил десять минут и не менял ничего (22.09.2026)."""
    rows = worker.run_all()

    assert len(quiet_run["matched"]) == 2
    assert rows[-1] == {"chain": "match", "items": 1}


def test_nothing_crawled_means_nothing_to_match(quiet_run):
    _crawled("magnit")
    _crawled("metro")

    assert worker.run_all(fresh=12) == []
    assert quiet_run["matched"] == []


def test_a_second_crawler_waits_for_the_first(quiet_run, monkeypatch):
    """Обходы разных процессов не идут разом: второй ждёт замка, а потом отпускает его.

    fcntl на рабочей машине нет (Windows), поэтому ядро здесь подменено: оно
    отвечает «занято» на попытку без ожидания, как ответило бы при живом первом.
    """
    import sys

    calls: list[str] = []

    class Kernel:
        LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

        def flock(self, fd, op):
            if op == self.LOCK_EX | self.LOCK_NB:
                calls.append("попробовал")
                raise BlockingIOError
            calls.append({self.LOCK_EX: "дождался", self.LOCK_UN: "отпустил"}[op])

    monkeypatch.setitem(sys.modules, "fcntl", Kernel())
    monkeypatch.setattr(worker.refresh, "run_chain", lambda crawler, progress=None: (
        calls.append(f"обход {crawler}") or {"chain": crawler, "status": "ok"}))

    worker.run_all()

    assert calls == ["попробовал", "дождался", "обход magnit", "обход metro", "отпустил"]


def test_link_products_recognises_receipt_items(catalog, user_db):
    from app.models import Product

    store.upsert_products("lenta", [P("80424", "Молоко пастеризованное ПРОСТОКВАШИНО 2,5%, без змж, 930мл",
                                      weight_g=930)])
    store.upsert_products("magnit", [P("1", "Молоко Простоквашино 2,5% 930мл")])
    store.upsert_products("dixy", [P("2", "Персики", unit="kg")])
    refresh.match_all()

    milk = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 2,5% 930 мл", unit="pcs"))
    peaches = repo.upsert_product(Product(id=None, name="Персики консервированные 500 г", unit="pcs"))
    assert refresh.link_products() == {"tried": 2, "linked": 1}

    stores = {s.code: s.id for s in repo.list_stores()}
    assert repo.confirmed_mapping(milk, stores["lenta"])["sku"] == "80424"
    assert repo.confirmed_mapping(milk, stores["magnit"])["sku"] == "1"
    assert repo.confirmed_mapping(peaches, stores["dixy"]) is None
    assert refresh.resolve("Совсем неизвестный товар") is None


# ---------- сопоставление на большом каталоге ----------
def test_a_crowded_head_word_does_not_cost_a_pair_per_comparison(monkeypatch):
    """Сопоставление не должно помнить каждую сравнённую пару — на этом оно умирало.

    Замер 20.09.2026 на живом каталоге из 262 475 строк: головных слов 30 785, пар
    к сравнению 195 342 771, и множество этих пар требовало 10,9 ГБ при 7,9 ГБ на
    сервере. Процесс убивали сигналом, в журнале оставалось «Killed», а снаружи всё
    выглядело исправным — сопоставление молча не отработало три обхода подряд.

    Память берегла время: одно сравнение стоит 1,2 мкс, все 195 млн пар — четыре
    минуты. Взамен множества стоит проверка по самому объединению, и она отсекает
    БОЛЬШЕ работы: пара пропускается и тогда, когда строки склеены через третью.
    Сторож смотрит именно на это — на числе сравнений видно, что проверка жива.
    """
    real = match.same_product
    calls = {"n": 0}

    def counted(a, b, vocab):
        calls["n"] += 1
        return real(a, b, vocab)

    monkeypatch.setattr(match, "same_product", counted)

    # Восемьдесят строк одного товара в двух сетях: головное слово у всех общее,
    # значит наивный перебор дал бы 80*79/2 = 3160 сравнений.
    rows = []
    for i in range(40):
        rows.append({"id": i, "chain": "lenta", "sku": str(i),
                     "name": "Вино Киндзмараули красное полусладкое 0,75 л"})
        rows.append({"id": 100 + i, "chain": "magnit", "sku": str(i),
                     "name": "Вино Киндзмараули красное полусладкое 0,75 л"})

    groups = match.cluster(rows)

    assert len(groups) == 1, "восемьдесят строк одного вина — один товар"
    assert len(groups[0]) == 80
    assert calls["n"] < 3160, "пары внутри уже склеенной группы сравнивать незачем"


# ---------- кириллица и чужая латиница как одно слово ----------
def test_fold_brings_both_scripts_to_one_form():
    """Настоящее название из адреса карточки Перекрёстка против кириллицы Ленты.

    Перекрёсток и Впрок берут названия прямо из адреса товара, а там латиница без
    диакритики: /p/maslo-slivocnoe-icalki-krestanskoe-72-5-500g. Схема у них СВОЯ,
    без вторых букв — «сливочное» это slivocnoe, а не slivochnoe, как написала бы
    паспортная транслитерация. Поэтому сводим обе стороны к общей краткой форме.
    """
    assert match.fold("сливочное") == match.fold("slivocnoe") == "slivocnoe"
    assert match.fold("Ичалки") == match.fold("icalki") == "icalki"
    assert match.fold("крестьянское") == match.fold("krestanskoe") == "krestanskoe"
    assert match.fold("Простоквашино") == match.fold("prostokvasino") == "prostokvasino"
    assert match.fold("сыр") == match.fold("syr") == "sir"


def test_latin_and_cyrillic_names_become_one_item(catalog):
    """Замер 20.09.2026: пару в другой сети находили 0,2 % строк Перекрёстка.

    186 тысяч строк — две трети каталога — лежали отдельным островом только потому,
    что написаны другим письмом. Доступ тут ни при чём: страницы у нас есть.
    """
    store.upsert_products("lenta", [
        P("1", "Масло сливочное Ичалки Крестьянское 72,5% 500 г", weight_g=500)])
    store.upsert_products("perekrestok", [
        P("2", "maslo slivocnoe icalki krestanskoe 72 5 500g", weight_g=500)])
    summary = refresh.match_all()

    assert summary["items"] == 1 and summary["multi"] == 1
    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["1"]["item_id"] == rows["2"]["item_id"]
    assert rows["2"]["link_method"] == "translit"


def test_the_strict_reading_still_wins_when_both_are_cyrillic(catalog):
    """Свод — второй заход, а не замена: одинаковое письмо решается как раньше."""
    store.upsert_products("lenta", [P("1", "Молоко Простоквашино 2,5% 930 мл", weight_g=930)])
    store.upsert_products("magnit", [P("2", "Молоко Простоквашино 2,5% 930мл", weight_g=930)])
    refresh.match_all()

    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["2"]["link_method"] == "words", "строгое сравнение должно срабатывать первым"


def test_fold_does_not_glue_different_pack_sizes(catalog):
    """Огрубление письма не отменяет фасовку — она осталась жёстким условием."""
    store.upsert_products("lenta", [P("1", "Страчателла 200 г", weight_g=200)])
    store.upsert_products("perekrestok", [P("2", "stracatella 92 g", weight_g=92)])

    assert refresh.match_all()["items"] == 2


def test_fold_does_not_glue_different_brands(catalog):
    """И не отменяет спор марок: он тоже считается по своду, а не по буквам."""
    store.upsert_products("lenta", [
        P("1", "Молоко 2,5% 930 мл", brand="Простоквашино", weight_g=930)])
    store.upsert_products("perekrestok", [
        P("2", "moloko 2 5 930 ml", brand="domik v derevne", weight_g=930)])

    assert refresh.match_all()["items"] == 2


def test_fold_does_not_glue_a_broader_name(catalog):
    """«Персики» против «персики консервированные» — разные товары и в своде тоже."""
    store.upsert_products("lenta", [P("1", "Персики консервированные 500 г", weight_g=500)])
    store.upsert_products("perekrestok", [P("2", "persiki", unit="kg")])

    assert refresh.match_all()["items"] == 2


# ---------- разновидности: процент, метка, цепочка ----------
def test_different_fat_is_a_different_product(catalog):
    """Живой промах 20.09.2026: «Сметана Большая кружка 15 %» и она же 20 % — один товар.

    Числа выброшены из слов как шум, поэтому наборы слов у них совпадали до буквы.
    Для покупателя это разные товары и разные деньги.
    """
    store.upsert_products("dixy", [P("1", "Сметана Большая кружка 15% 300г", weight_g=300)])
    store.upsert_products("metro", [P("2", "Сметана Большая кружка 20%, 300г", weight_g=300)])

    assert refresh.match_all()["items"] == 2


def test_the_same_fat_still_meets(catalog):
    """Проверка обратной стороны: процент не должен мешать там, где он совпал."""
    store.upsert_products("dixy", [P("1", "Сметана Большая кружка 15% 300г", weight_g=300)])
    store.upsert_products("magnit", [P("2", "Сметана Большая кружка 15% 300г", weight_g=300)])

    assert refresh.match_all()["items"] == 1


def test_percent_on_one_side_only_is_not_evidence():
    """Молчание — не улика, то же правило, что у фасовки."""
    assert match.percents_agree(15.0, None) and match.percents_agree(None, None)
    assert not match.percents_agree(15.0, 20.0)
    assert match.parse_percent("Сметана Большая кружка 15% 300г") == 15.0
    assert match.parse_percent("Пудинг Grand Dessert 5.2% 200г") == 5.2
    assert match.parse_percent("Молоко 930 мл") is None


def test_a_variant_mark_separates(catalog):
    """«Спагетти Barilla 450 г» и «Спагетти Barilla без глютена 400 г» — разные покупки."""
    store.upsert_products("vkusvill", [
        P("1", "Макаронные изделия Barilla №5 Спагетти 450 г", weight_g=450)])
    store.upsert_products("dixy", [
        P("2", "Макаронные изделия Barilla №5 Спагетти без глютена 400 г", weight_g=400)])

    assert refresh.match_all()["items"] == 2


def test_variant_marks_are_read_in_both_scripts():
    """Метка считается по своду письма, иначе она сама ломала бы межписьменные пары."""
    assert match.variant_marks("Хлопья овсяные без глютена") == \
           match.variant_marks("hlopya ovsyanye bez glutena")
    assert match.variant_marks("Резинка жевательная без сахара") == \
           match.variant_marks("zhevatelnaya rezinka bez sahara")
    assert match.variant_marks("Сметана 15%") == frozenset()


def test_the_dairy_label_is_not_a_variant():
    """«без ЗМЖ» стоит на половине молочного отдела — это надпись, а не разновидность.

    Живой каталог 20.09.2026 полон строк «jjogurt pitevojj … 25 bez zmzh 500g». Одна
    сеть её пишет, другая нет; взяв её меткой, мы развели бы один йогурт на два товара.
    """
    assert match.variant_marks("Йогурт питьевой Ирбитский 2,5% без ЗМЖ 500г") == frozenset()
    assert match.variant_marks("jjogurt pitevojj irbitskijj 25 bez zmzh 500g") == frozenset()


def test_a_chain_of_two_similar_pairs_is_not_one_product(catalog):
    """Живой промах 20.09.2026, самый показательный.

    В одной группе оказались куриный бульон, говяжий бульон и фруктовое пюре — все
    по 90 г. Ни одна пара из трёх не прошла бы сравнение сама: их свёл общий сосед,
    потому что объединение групп транзитивно, а «тот же товар» — нет.
    """
    store.upsert_products("lenta", [
        P("1", "Бульон куриный Роллтон Домашний 90г", weight_g=90),
        P("2", "Бульон Роллтон говяжий Домашний 90г", weight_g=90)])
    store.upsert_products("magnit", [
        P("3", "Пюре Дары Кубани Яблоко банан клубника 90г", weight_g=90)])
    refresh.match_all()

    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["3"]["item_id"] not in (rows["1"]["item_id"], rows["2"]["item_id"]), \
        "пюре не должно попасть к бульонам через общего соседа"


def test_splitting_a_chain_does_not_break_an_honest_group(catalog):
    """Разбор цепочек не должен рвать группы, где все совпадают с эталоном напрямую."""
    store.upsert_products("lenta", [
        P("1", "Молоко пастеризованное ПРОСТОКВАШИНО 2,5% 930мл", weight_g=930)])
    store.upsert_products("magnit", [P("2", "Молоко Простоквашино 2,5% 930мл", weight_g=930)])
    store.upsert_products("metro", [P("3", "Молоко Простоквашино 2,5%, 930мл", weight_g=930)])

    summary = refresh.match_all()
    assert summary["items"] == 1 and summary["multi"] == 1


def test_a_silent_row_does_not_shelter_two_conflicting_ones(catalog):
    """Дыра, найденная замером ПОСЛЕ первой правки, и оттого особенно ценная.

    Сверки только с эталоном мало. Молчание уликой не считается: у строки без
    процента он неизвестен, и такая строка пропускает к себе и 15 %, и 20 %. В
    живом каталоге 20.09.2026 так и вышло — «Сметана Домик в деревне 300 г» без
    процента держала при себе обе жирности, и 57 групп остались спорными при
    работающем запрете. Теперь новичок сверяется СО ВСЕМИ уже оставленными.
    """
    store.upsert_products("lenta", [P("0", "Сметана Домик в деревне 300г", weight_g=300)])
    store.upsert_products("magnit", [P("1", "Сметана Домик в Деревне 15% 300г", weight_g=300)])
    store.upsert_products("metro", [P("2", "Сметана Домик в деревне 15% 300г", weight_g=300)])
    store.upsert_products("dixy", [P("3", "Сметана Домик в деревне 20% 300г", weight_g=300)])
    refresh.match_all()

    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["1"]["item_id"] == rows["2"]["item_id"], "две пятнадцатипроцентных — один товар"
    assert rows["3"]["item_id"] != rows["1"]["item_id"], "двадцать процентов — другой товар"


# ---------- Перекрёсток: цены под пройденной человеком проверкой ----------
def test_perekrestok_without_a_passed_check_still_gives_names(monkeypatch):
    """Проверку никто не проходил — сборщик работает ровно как раньше.

    Это главное свойство новой дороги: она может только ДОБАВИТЬ цены. Сломайся
    она молча — и каталог Перекрёстка исчез бы целиком, а он у нас самый большой.
    """
    from app.catalog.crawlers import perekrestok

    crawler = perekrestok.PerekrestokCrawler()
    monkeypatch.setattr("app.shopbrowser.store.any_saved", lambda chain: None)
    said = []

    assert crawler._prices(said.append) == {}
    assert any("не пройдена" in s for s in said), "молчание тут читается как поломка"


def test_perekrestok_prices_never_break_the_crawl(monkeypatch):
    """Сеть ответила отказом или чем угодно ещё — остаются названия, а не пустота."""
    from app.catalog.crawlers import perekrestok

    crawler = perekrestok.PerekrestokCrawler()
    monkeypatch.setattr("app.shopbrowser.store.any_saved",
                        lambda chain: {"cookies": [{"name": "session", "value": "x"}]})
    monkeypatch.setattr(crawler, "_ask_api",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("403")))
    said = []

    assert crawler._prices(said.append) == {}
    assert any("не взялись" in s for s in said), "провал должен быть назван вслух"


def test_perekrestok_reads_a_price_from_the_feed():
    """Поля описи живьём не видены, поэтому имена берутся с запасом."""
    from app.catalog.crawlers.perekrestok import _price_of

    assert _price_of({"plu": "3636734", "price": 219.9, "inStock": True}) == \
           ("3636734", {"price": 219.9, "in_stock": True})
    # Копейки целым числом — обычная манера витрин.
    assert _price_of({"plu": "1", "price": 21990})[1]["price"] == 219.9
    # Нет артикула, нет цены, ноль — товар молча пропускается: выдуманная цена
    # хуже отсутствующей, приложение обещает считать по настоящим деньгам.
    assert _price_of({"price": 100}) is None
    assert _price_of({"plu": "1"}) is None
    assert _price_of({"plu": "1", "price": 0}) is None
