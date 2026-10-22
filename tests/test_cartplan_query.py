"""Второй адрес поиска в наряде: длинное имя из чека получает короткую пару.

ЗАЧЕМ (живая сборка 20.10.2026 на боевом сервере). Из четырёх позиций легло 0-1, и
отчёты телефона говорят, что дорога обрывалась ДО нажатия: «поиск сети ничего не
нашёл за 12 с», «поиск сети нашёл не тот товар», «на карточке не нашлось кнопки».
Первое лечит чистка обрубка (`_clean_query`: «0 5» — это «0,5»). Против второго у
строки наряда не было ничего: адрес поиска был ОДИН, и, получив по нему чужую
карточку, телефон сдавался. Теперь у строки есть второй адрес — короткий, из
двух-трёх первых смысловых слов имени: сеть, не понявшая длинное имя целиком,
отвечает по нему нужной полкой.

Здесь стережётся четыре вещи. Длинное имя получает `search_alt` из 2-3 слов.
Короткое — не получает: второй адрес был бы повтором и лишним запросом в сеть.
Чистка не склеивает количество («2 500 г» остаётся граммами, а не «2,500»), но
склеивает жирность и объём («0 5» → «0,5», «0 95 л» → «0,95 л»). Сеть без адреса
поиска не получает ни одного адреса — ни основного, ни второго: выдуманный адрес
дороже отсутствующего.

ПЯТОЕ (21.10.2026): имя из единой базы не должно уводить русскую витрину в латиницу.
У молочки владельца каноническим именем стала транслитерация из sitemap-сетей
(«moloko ultrapasterizovannoe parmalat …»), и Магнит, Лента и Пятёрочка отвечали на
неё пустой выдачей на всех позициях. Если обрубок чека русский, а каноническое имя
без кириллицы — в поиск идёт очищенный обрубок; товар, у которого и чек латиницей,
остаётся с латинским именем.

ШЕСТОЕ (21.10.2026, task-27): служебные строки чеков и приложения — «Аренда
транспортного средства», «Перевозка пассажиров багажа», «Штраф за нарушение условий
договора», «Сборка» — не товар. Телефону по ним не даётся ни адреса поиска, ни места
в наряде: в магазине такого товара нет, и каждый такой адрес — это заведомо пустой
поиск, на который телефон в живом прогоне тратил по 20 секунд на позицию.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import parse_qs, unquote_plus, urlsplit

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, phonecart  # noqa: E402

# Та же строка, что пришла в живом отчёте 20.10.2026: «0 5» — это жирность 0,5 %.
MILK = "Молоко neo high protein 0 5 безлактозное ультрапастеризованное 950 г"
# Второй обычный товар из той же корзины: он обязан идти как раньше.
PAPIA = "Туалетная бумага Papia Bali Flower 3 слоя, 8 рулонов"


@pytest.fixture
def no_catalog(monkeypatch):
    """Каталог этой строки не знает — запрос строится чисткой обрубка чека.

    Без подмены тест зависел бы от data/catalog.db рабочего места: на одном
    компьютере имя нашлось бы, на другом нет, и проверка адреса стала бы плавающей.
    """
    monkeypatch.setattr(phonecart.repo, "master_of", lambda product_id: None)
    monkeypatch.setattr(phonecart, "_catalog_name", lambda text: None)


def _query(url: str) -> str:
    """Запрос из адреса поиска Пятёрочки: у неё параметр `text` (config.yaml)."""
    return parse_qs(urlsplit(url).query)["text"][0]


def test_a_long_receipt_name_gets_a_second_short_address(no_catalog):
    """Длинное имя — основной запрос целиком плюс второй из 2-3 первых слов.

    Основной запрос не меняется (чистка в нём уже есть), а второй обязан быть
    ДРУГИМ адресом: иначе телефон сделал бы тот же запрос второй раз и получил бы
    ту же чужую карточку, только потратив ещё 12 секунд.
    """
    lines = phonecart._search_lines([{"product_id": 7, "name": MILK}], "pyaterochka")

    assert len(lines) == 1
    assert "search_alt" in lines[0], "второго адреса поиска нет"

    assert _query(lines[0]["url"]) == (
        "Молоко neo high protein 0,5 безлактозное ультрапастеризованное 950 г"), \
        "основной запрос изменился — его чистит `_query_name`, а не второй адрес"

    alt = _query(lines[0]["search_alt"])
    assert alt == "Молоко neo high"
    assert 2 <= len(alt.split()) <= phonecart.SHORT_QUERY_WORDS, \
        f"во втором запросе {len(alt.split())} слов — это уже не короткая пара"
    assert lines[0]["search_alt"] != lines[0]["url"], "второй адрес повторяет первый"
    assert lines[0]["search_alt"].startswith("https://5ka.ru/search/?text="), \
        "второй адрес собран не тем шаблоном, что основной"


def test_a_short_name_gets_no_second_address(no_catalog):
    """Короткое имя — один адрес: второй был бы повтором и лишним запросом в сеть."""
    for name in ("Молоко 1 л", "Хлеб Бородинский нарезка"):
        lines = phonecart._search_lines([{"product_id": 8, "name": name}], "pyaterochka")
        assert "search_alt" not in lines[0], f"«{name}»: второй адрес без надобности"
        assert _query(lines[0]["url"]) == name


def test_a_line_without_product_id_searches_the_cleaned_name():
    """Строка без `product_id` тоже идёт в сеть чищеной: «0 5» — это «0,5».

    Такие строки приходят в наряд не из каталога, а из корзины без привязки, и до
    21.10.2026 в поиск уходило СЫРОЕ имя: сеть видела два числа «0 5» и не находила
    ничего. Чистка уже есть в `_query_name`, но её звали только при `product_id`.
    """
    lines = phonecart._search_lines([{"name": MILK}], "pyaterochka")

    assert len(lines) == 1
    url = lines[0]["url"]
    assert "0+5" not in url, "в основной адрес поиска ушёл сырой обрубок «0 5»"
    assert "0%2C5" in url or "0,5" in url, "в адресе нет жирности «0,5»"
    assert _query(url) == (
        "Молоко neo high protein 0,5 безлактозное ультрапастеризованное 950 г")


def test_a_line_without_product_id_gets_the_second_address_from_the_cleaned_query():
    """Второй адрес строится от уже очищенного основного запроса, а не от сырого имени."""
    lines = phonecart._search_lines([{"name": MILK}], "pyaterochka")

    assert "search_alt" in lines[0], "второго адреса поиска нет"
    assert _query(lines[0]["search_alt"]) == "Молоко neo high"
    assert lines[0]["search_alt"].startswith("https://5ka.ru/search/?text=")


def test_a_line_with_product_id_still_takes_the_name_from_the_catalog(monkeypatch):
    """Ветка с `product_id` не меняется: имя по-прежнему выбирает `_query_name`."""
    called = []

    def fake_query_name(product_id, fallback):
        called.append((product_id, fallback))
        return "Молоко «Домик в деревне» 0,5 %, 950 г"

    monkeypatch.setattr(phonecart, "_query_name", fake_query_name)
    lines = phonecart._search_lines([{"product_id": 7, "name": MILK}], "pyaterochka")

    assert called == [(7, MILK)], "строку с product_id повели не через `_query_name`"
    assert _query(lines[0]["url"]) == "Молоко «Домик в деревне» 0,5 %, 950 г"


def test_a_name_without_numeric_stubs_is_searched_as_is():
    """Чистка не трогает обычное имя: без обрубков запрос остаётся прежним."""
    name = "Хлеб Бородинский нарезка 400 г"

    lines = phonecart._search_lines([{"name": name}], "pyaterochka")

    assert _query(lines[0]["url"]) == name


@pytest.mark.parametrize("raw, expected", [
    # количество не склеиваем: «2 500 г» — это два с половиной кило, а не «2,500»
    ("Мука пшеничная 2 500 г", "Мука пшеничная 2 500 г"),
    # а вот объём и жирность — склеиваем: без запятой сеть их не понимает
    ("Молоко 0 95 л", "Молоко 0,95 л"),
    ("Молоко neo high protein 0 5 безлактозное", "Молоко neo high protein 0,5 безлактозное"),
])
def test_cleaning_does_not_glue_a_quantity(raw, expected):
    """«0 5» — это 0,5, а «2 500» — это 2 500: правило чинит числа, но не всё подряд."""
    assert phonecart._clean_query(raw) == expected


def test_a_chain_without_a_search_address_gets_no_address_at_all(monkeypatch):
    """Нет шаблона `search_url.<сеть>` — нет ни `search`, ни `search_alt`.

    Адрес поиска у каждой сети свой и живёт в config.yaml; у METRO его нет вовсе
    (замер 19.10.2026). Придумать его в коде значит послать телефон на чужой адрес.
    """
    real_get = config.get
    monkeypatch.setattr(
        config, "get",
        lambda key, *a, **k: None if key == "search_url.pyaterochka" else real_get(key, *a, **k),
        raising=False)

    assert phonecart._search_lines([{"product_id": 7, "name": MILK}], "pyaterochka") == []


# ---------- имя единого товара: латиница не должна уводить русскую витрину ----------
#
# Боевая база владельца, замер 21.10.2026: каноническим именем молочки стала
# транслитерация из sitemap-сетей (Перекрёсток, Vprok, Монетка пишут имена
# URL-слагами). Магнит, Лента и Пятёрочка на такую строку отвечают пустой выдачей.
LATIN_MASTER = ("moloko ultrapasterizovannoe parmalat pitevoe proteinovoe bezlaktoznoe "
                "18 bez zmzh 1l rossiya")
# Русское имя того же товара после пересопоставления — поведение до правки.
RUSSIAN_MASTER = "Молоко Parmalat 1,8 %, 1 л, безлактозное ультрапастеризованное"
# Смешанное имя: марка латиницей первой, дальше по-русски. Именно на таком старый
# `_catalog_name` пропускал латинского кандидата через проверку первого слова.
RUSSIAN_MIXED_MASTER = ("Parmalat Молоко 1,8 %, 1 л, безлактозное "
                        "ультрапастеризованное")
# Чек иностранного товара целиком латиницей: его латинское имя трогать нельзя.
LATIN_STUB = "moloko parmalat 1 8 1 l"


def _master_with(monkeypatch, name):
    """Привязка к единому товару с заданным каноническим именем, без похода в базу."""
    monkeypatch.setattr(phonecart.repo, "master_of",
                        lambda product_id: {"master_id": 49848437, "method": "auto", "at": None})
    monkeypatch.setattr("app.catalog.store.item",
                        lambda item_id: {"id": item_id, "name": name})
    monkeypatch.setattr(phonecart, "_catalog_name", lambda text: None)


def test_a_latin_name_from_the_unified_base_gives_way_to_the_russian_receipt(monkeypatch):
    """Каноническое имя латиницей, чек русский — ищем очищенным русским обрубком.

    Это и была причина «поиск Магнита и Ленты пуст на всех позициях»: в наряд
    уходила транслитерация, а не «Молоко …».
    """
    _master_with(monkeypatch, LATIN_MASTER)

    answer = phonecart._query_name(7, MILK)

    assert answer == ("Молоко neo high protein 0,5 безлактозное "
                      "ультрапастеризованное 950 г")
    assert "Молоко" in answer
    assert "moloko" not in answer.lower(), "в поиск ушла латинская транслитерация"


def test_search_addresses_stay_russian_when_the_unified_name_is_latin(monkeypatch):
    """Оба адреса поиска — русские, латиницы в них нет: телефон получит «Молоко …»."""
    _master_with(monkeypatch, LATIN_MASTER)

    lines = phonecart._search_lines([{"product_id": 7, "name": MILK}], "pyaterochka")

    assert len(lines) == 1
    url = lines[0]["url"]
    assert "Молоко" in unquote_plus(url), "в адресе поиска нет русского «Молоко»"
    assert "ultrapasterizovannoe" not in url.lower(), "в поиск ушла латинская транслитерация"
    assert LATIN_MASTER not in url
    assert _query(url) == ("Молоко neo high protein 0,5 безлактозное "
                           "ультрапастеризованное 950 г")
    assert "search_alt" in lines[0]
    assert _query(lines[0]["search_alt"]) == "Молоко neo high"
    assert "Молоко" in unquote_plus(lines[0]["search_alt"])


def test_a_russian_name_from_the_unified_base_still_wins(monkeypatch):
    """Русское каноническое имя — прежнее поведение: оно и уходит в поиск."""
    _master_with(monkeypatch, RUSSIAN_MASTER)

    assert phonecart._query_name(7, "Молоко parmalat 1 8 1 л") == RUSSIAN_MASTER


def test_an_all_latin_product_keeps_the_latin_name(monkeypatch):
    """И чек, и каноническое имя латиницей — имя не подменяем: русской строки нет.

    У иностранных товаров латинское имя — единственное, что сеть знает; откат на
    «почищенный обрубок» сделал бы поиск хуже, а не лучше.
    """
    _master_with(monkeypatch, LATIN_MASTER)

    answer = phonecart._query_name(7, LATIN_STUB)

    assert answer == LATIN_MASTER
    assert answer != phonecart._clean_query(LATIN_STUB)


def test_the_catalog_does_not_give_a_latin_name_for_a_russian_receipt(monkeypatch):
    """Похожее имя из каталога латиницей при русском обрубке — не ответ."""
    raw = "Parmalat молоко 1 8 1 л"
    monkeypatch.setattr("app.catalog.store.search_items",
                        lambda query, limit=3: [{"name": LATIN_MASTER}])

    assert phonecart._catalog_name(raw) is None


def test_the_catalog_skips_a_latin_candidate_and_takes_the_russian_one(monkeypatch):
    """Латинского кандидата пропускаем и смотрим дальше: русское имя в базе есть."""
    raw = "Parmalat молоко 1 8 1 л"
    monkeypatch.setattr("app.catalog.store.search_items",
                        lambda query, limit=3: [{"name": LATIN_MASTER},
                                                {"name": RUSSIAN_MIXED_MASTER}])

    assert phonecart._catalog_name(raw) == RUSSIAN_MIXED_MASTER


# ---------- служебные строки: не товар, ни адреса поиска, ни места в наряде ----------
#
# Живые отчёты третьего прогона 21.10.2026: в корзинах из чеков лежат служебные
# строки, и приложение строило по ним поисковый адрес в магазине. Сеть такого
# товара не знает, поиск заведомо пуст, а телефон честно ждёт карточку все 20 с на
# каждую позицию. Здесь стережётся граница: служебная строка не получает адреса и
# не попадает в наряд, а обычный товар со словом «услуга»/«доставка» в середине
# названия остаётся товаром.
SERVICE_LINES = (
    "Аренда транспортного средства",
    "Перевозка пассажиров багажа",
    "Штраф за нарушение условий договора",
    "Сборка",
)


def test_service_lines_get_no_search_address(no_catalog, monkeypatch):
    """Служебная строка не получает ни `search`, ни `search_alt`.

    И до `_query_name` дело не доходит: имя для поиска — это ещё один поход в
    каталог, а спрашивать там про аренду и штраф нечего.
    """
    monkeypatch.setattr(phonecart, "_query_name",
                        lambda *a, **kw: pytest.fail("служебную строку повели искать"))
    for name in SERVICE_LINES:
        assert phonecart._search_lines([{"product_id": 7, "name": name}], "pyaterochka") == [], \
            f"«{name}»: телефону заказан заведомо пустой поиск"
        assert phonecart._search_lines([{"name": name}], "pyaterochka") == []


def test_service_lines_are_dropped_from_the_search_queue_around_ordinary_goods(no_catalog):
    """В очереди на поиск остаются только товары: рядом со служебными строками тоже."""
    queue = ([{"product_id": 7, "name": MILK}, {"product_id": 8, "name": PAPIA}]
             + [{"product_id": 10 + i, "name": name} for i, name in enumerate(SERVICE_LINES)])

    lines = phonecart._search_lines(queue, "pyaterochka")

    assert [_query(line["url"]) for line in lines] == [
        "Молоко neo high protein 0,5 безлактозное ультрапастеризованное 950 г", PAPIA
    ]


def test_ordinary_goods_with_service_words_in_the_middle_are_not_cut(no_catalog):
    """«Салфетки услуга…», «Молоко доставка…» — товары: слово в середине не служебное."""
    names = ("Салфетки услуга влажные 100 шт", "Молоко доставка на дом 1 л")

    lines = phonecart._search_lines([{"name": name} for name in names], "pyaterochka")

    assert [_query(line["url"]) for line in lines] == list(names)


@pytest.mark.parametrize("name", [
    "АРЕНДА ТРАНСПОРТНОГО СРЕДСТВА",
    "  Перевозка   пассажиров багажа ",
    "Пассажиры и багаж",
    "Услуга",
    "Услуги связи",
    "Сервисный сбор",
    "Чаевые",
    "ЧАЁВЫЕ",
    "Комиссия за перевод",
    "Такси",
    "Taxi до аэропорта",
    "Плата   за доставку",
    "Сборка",
    "СБОРКА И УПАКОВКА",
])
def test_service_roots_are_recognised(name):
    """Корни узнаются без учёта регистра, лишних пробелов и с «ё» вместо «е»."""
    assert phonecart._service_line(name) is True


@pytest.mark.parametrize("name", [
    "Салфетки услуга влажные 100 шт",      # «услуга» в середине названия
    "Молоко доставка на дом 1 л",          # «доставка» — не служебное слово
    "Сборная солянка 300 г",               # «сбор» в начале, но это не «сборка»
    "Вода питьевая плата за доставку",     # «плата за» не в начале строки
    "Крем для рук «Такси» 75 мл",          # «такси» в середине
])
def test_ordinary_goods_are_not_service_lines(name):
    assert phonecart._service_line(name) is False


PHONE = "79990000091"


@pytest.fixture
def basket(tmp_path, monkeypatch):
    """Рабочее место: два обычных товара, знакомых Пятёрочке, и четыре служебные строки.

    Товары связаны с Пятёрочкой настоящими артикулами, а у служебных строк связи нет
    и быть не может: в корзине владельца они приходят из чека как «позиция», но
    товара в магазине у них нет.
    """
    from app import repo, users
    from app.models import Product

    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    # Каталог единой базы в этом тесте не нужен: имя для поиска строится чисткой
    # обрубка чека, а не походом в настоящую data/catalog.db.
    monkeypatch.setattr(phonecart.repo, "master_of", lambda product_id: None)
    monkeypatch.setattr(phonecart, "_catalog_name", lambda text: None)

    users.open_workspace(PHONE)
    shop = repo.get_store("pyaterochka")
    basket_id = repo.create_basket("Корзина из чека")
    for name, sku in ((MILK, "5ka-1"), (PAPIA, "5ka-2")):
        product = repo.upsert_product(Product(id=None, name=name, unit="pcs"))
        article = repo.upsert_store_product(shop.id, sku, name,
                                            url=f"https://5ka.ru/product/{sku}")
        repo.confirm_mapping(product, article, confirmed=True)
        repo.set_basket_item(basket_id, product, 1.0)
    for name in SERVICE_LINES:
        product = repo.upsert_product(Product(id=None, name=name, unit="pcs"))
        repo.set_basket_item(basket_id, product, 1.0)
    yield basket_id
    users.deactivate()


def test_service_lines_do_not_reach_the_plan_or_its_search(basket, monkeypatch):
    """Служебные строки не спрошены ни у каталога, ни у сети и не попали в наряд.

    В наряде их нет ни в строках, ни в «незнакомых»: сеть такого товара не знает, и
    человеку по ним нечего искать. Зато в `note` сказано, сколько строк пропущено, —
    иначе пропажа четырёх позиций из корзины выглядела бы поломкой.
    """
    from app import matcher
    from app.catalog import refresh

    asked: list[str] = []
    monkeypatch.setattr(refresh, "resolve", lambda *a, **kw: asked.append("catalog") or None)
    monkeypatch.setattr(matcher, "auto_match",
                        lambda *a, **kw: asked.append("search") or {"auto": 0, "need_review": []})

    answer = phonecart.plan({"store": "pyaterochka", "basket": basket})

    assert asked == [], "по служебной строке спросили каталог или живой поиск сети"
    names = [line["name"] for line in answer["lines"]]
    assert set(names) == {MILK, PAPIA}, "обычные товары должны остаться в наряде"
    assert not any(phonecart._service_line(name) for name in names), \
        "служебная строка попала в строки наряда"
    assert answer["unknown"] == [], "служебную строку назвали незнакомой позицией"
    assert answer["search"] == [], "по служебной строке заказан поиск в сети"
    assert "Служебных строк пропущено: 4" in answer["note"], \
        "человеку не сказали, что четыре строки пропущены"
