"""Телефон нашёл товар сам — он попадает в единую базу, а не пропадает.

ЗАЧЕМ. Пятёрочку и Самокат сервер не пускает ни с серверного адреса, ни с домашнего
(замер 18.10.2026: 5ka.ru — «Проверьте настройки интернета и VPN», samokat.ru —
«Forbidden», оба называют IP отправителя). Их каталога в единой базе нет, и сопоставлять
их строки не с чем. Человек со своего телефона витрину видит: приложение присылает адрес
карточки, сервер достаёт из него артикул сети (app/catalog/fromurl.py) и заводит товар в
общей базе (refresh.learn_device_rows). Здесь проверяется вся эта дорога.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import fromurl, refresh, store  # noqa: E402
from app.catalog.crawlers import pyaterochka, samokat  # noqa: E402

# Адреса взяты из самих сборщиков и их тестов, а не выдуманы. Самокат — из живого
# замера 19.10.2026 в окне телефона: артикула в адресе у него нет вовсе, ключ — слаг.
CASES = [
    ("pyaterochka", "https://5ka.ru/product/moloko-prostokvashino--78001656/", "78001656"),
    ("pyaterochka", "https://5ka.ru/product/baton-nareznoi--4306830", "4306830"),
    ("samokat", "https://samokat.ru/product/123456", "123456"),
    ("samokat", "https://samokat.ru/product/khurma-korolyok-500-g", "khurma-korolyok-500-g"),
    ("samokat", "https://samokat.ru/product/omlet-roll-creative-kitchen-s-file-"
                "polosatogo-tuntsa-200-g",
     "omlet-roll-creative-kitchen-s-file-polosatogo-tuntsa-200-g"),
    ("magnit", "https://magnit.ru/product/1000227406-ogurcy-korotkoplodnye", "1000227406"),
    ("lenta", "https://lenta.com/product/rk-gorbusha-naturalnaya-rossiya-240g-21/", "21"),
    ("dixy", "https://dixy.ru/product/draje-mms-s-molochnym-shokoladom-2000650171/", "2000650171"),
    ("monetka", "https://monetka.ru/product/sok-dobryjj-yabloko-1l-rossiya-810000639/", "810000639"),
    ("fixprice", "https://fix-price.com/catalog/produkty-i-napitki/p-1660028-sahar-1-kg", "1660028"),
]


@pytest.mark.parametrize("chain,url,sku", CASES)
def test_the_article_is_taken_from_a_real_card_address(chain, url, sku):
    assert fromurl.sku_of(chain, url) == sku


def test_the_two_blocked_chains_use_the_crawlers_own_rule():
    """У Пятёрочки и Самоката правило одно с их сборщиками — расходиться ему нельзя."""
    for url in ("https://5ka.ru/product/moloko-prostokvashino--78001656/",
                "https://5ka.ru/product/baton-nareznoi--4306830"):
        assert fromurl.sku_of("pyaterochka", url) == pyaterochka.sku_from_url(url)
    for url in ("https://samokat.ru/product/123456",
                "https://samokat.ru/product/khurma-korolyok-500-g"):
        assert fromurl.sku_of("samokat", url) == samokat.sku_from_url(url)


def test_a_samokat_slug_is_not_cut_down_to_its_last_chunk():
    """Замер 19.10.2026: прежнее правило доставало из слага «g», «l», «rulona».

    По такому ключу в корзину сети ничего не положить, а в базе он значил бы товар,
    которого нет. Ключ — весь слаг, как и в закладке сбора цен.
    """
    for url in ("https://samokat.ru/product/khurma-korolyok-500-g",
                "https://samokat.ru/product/bumaga-tualetnaya-zemma-ultra-soft-4-rulona",
                "https://samokat.ru/product/energetik-adrenaline-rush-0-449-l"):
        sku = fromurl.sku_of("samokat", url)
        assert sku == url.rsplit("/", 1)[1], sku
        assert sku not in ("g", "l", "rulona", "sht")


def test_a_stranger_address_gives_nothing():
    """Не узнали — не пишем: чужой номер в базе положил бы в корзину не тот товар."""
    assert fromurl.sku_of("pyaterochka", "https://example.com/product/x--1/") is None
    assert fromurl.sku_of("magnit", "https://magnit.ru/catalog/") is None
    assert fromurl.sku_of("samokat", "") is None


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def test_a_phone_found_product_lands_in_the_unified_base(base):
    name = "Молоко Простоквашино 2,5% 930 мл"
    learned = refresh.learn_device_rows("samokat", [
        {"sku": "78001656", "name": name,
         "url": "https://samokat.ru/product/moloko-prostokvashino-78001656"}])

    assert learned == 1
    rows = base.all_products(active_only=True)
    assert [(r["chain"], r["sku"]) for r in rows] == [("samokat", "78001656")]
    assert rows[0]["name"] == name


def test_the_same_product_twice_is_not_added_twice(base):
    row = {"sku": "42", "name": "Хлеб Бородинский 400 г", "url": "https://samokat.ru/product/42"}
    assert refresh.learn_device_rows("samokat", [row]) == 1
    assert refresh.learn_device_rows("samokat", [row]) == 0, "известную строку не трогаем"
    assert len(base.all_products(active_only=True)) == 1


def test_placeholders_and_empty_names_are_not_written(base):
    learned = refresh.learn_device_rows("samokat", [
        {"sku": "hist-1", "name": "Молоко 1 л"},
        {"sku": "samokat-2", "name": "Кефир 1 л"},
        {"sku": "3", "name": ""},
    ])

    assert learned == 0
    assert base.all_products(active_only=True) == []


def test_one_report_does_not_flood_the_base(base):
    rows = [{"sku": str(n), "name": f"Товар {n}"} for n in range(50)]

    assert refresh.learn_device_rows("samokat", rows) == refresh.DEVICE_LEARN_LIMIT


def test_the_report_teaches_the_base_through_the_card_address(base):
    """Дорога целиком: отчёт телефона → артикул из адреса → строка в единой базе.

    Учат только адреса этой сети: чужой адрес в базу не идёт. Легла позиция или нет —
    не важно: с 21.10.2026 телефон присылает настоящий артикул и у не легших позиций
    (Дикси, карточка 403). Без адреса строку пропускаем даже с артикулом — связи не
    проверить, а имя без неё ничего не значит.
    """
    from app import phonecart

    phonecart._learn({"store": "samokat", "items": [
        {"name": "Молоко Простоквашино 2,5% 930 мл", "ok": True,
         "url": "https://samokat.ru/product/moloko-prostokvashino-930-ml"},
        {"name": "Чужой адрес", "ok": True, "url": "https://example.com/product/x-99"},
        {"name": "Не легло, но адрес свой", "ok": False,
         "url": "https://samokat.ru/product/hleb-77"},
        {"name": "Не легло и адреса нет", "ok": False},
    ]})

    rows = base.all_products(active_only=True)
    assert [(r["chain"], r["sku"]) for r in rows] == [
        ("samokat", "moloko-prostokvashino-930-ml"), ("samokat", "hleb-77")]


def test_the_plan_offers_search_only_where_the_chain_is_unknown():
    """Позиции, которых сеть не знает, наряд отдаёт телефону поиском этой же сети."""
    from urllib.parse import quote_plus

    from app import config, phonecart

    template = config.get("search_url.pyaterochka")
    assert isinstance(template, str) and "{q}" in template

    lines = phonecart._search_lines(
        [{"product_id": 7, "name": "Молоко 1 л"}, {"product_id": 8, "name": ""}], "pyaterochka")

    assert lines == [{"product_id": 7, "name": "Молоко 1 л",
                      "url": template.replace("{q}", quote_plus("Молоко 1 л"))}]
    # У METRO адреса поиска в config.yaml нет — телефону искать нечего, и списка нет.
    assert phonecart._search_lines([{"product_id": 7, "name": "Молоко 1 л"}], "metro") == []


def test_a_chain_whose_search_shows_nothing_gets_no_search_order(monkeypatch):
    """Сеть из `search_off` поиска не получает: иначе телефон ждёт 12 с впустую.

    Замер 19.10.2026: Самокат по верному параметру `value` отдал страницу поиска без
    единой ссылки /product/ и не сделал поискового запроса вовсе — телефон честно ждал
    карточку все 12 с на позицию и не находил ни одной.
    """
    from app import config, phonecart

    assert "samokat" in (config.get("search_off") or [])
    unresolved = [{"product_id": 7, "name": "Молоко 1 л"}]
    assert phonecart._search_lines(unresolved, "samokat") == []

    # Список — настройка, а не свойство кода: убрали сеть из него, и поиск вернулся.
    real_get = config.get
    monkeypatch.setattr(config, "get",
                        lambda key, *a, **k: [] if key == "search_off" else real_get(key, *a, **k),
                        raising=False)
    assert len(phonecart._search_lines(unresolved, "samokat")) == 1


def test_the_search_uses_the_full_name_from_the_unified_base(monkeypatch):
    """Чек пишет обрубки — искать ими сеть нечего: берём полное имя единого товара.

    Замер 19.10.2026: «Dirol жев рез арб дын кок 13.6 г» — по такой строке поиск сети не
    находит ничего, а по имени единого товара находит. Из 301 купленного и не привязанного
    товара Самоката и Пятёрочки единый товар есть у 43.
    """
    from app import config, phonecart
    from app.catalog import store

    assert "pyaterochka" not in (config.get("search_off") or [])
    monkeypatch.setattr(phonecart.repo, "master_of",
                        lambda pid: {"master_id": 5} if pid == 7 else None)
    monkeypatch.setattr(store, "item", lambda item_id: {"name": "Жевательная резинка Dirol"})

    lines = phonecart._search_lines([{"product_id": 7, "name": "Dirol жев рез 13.6 г"}],
                                    "pyaterochka")

    assert lines[0]["name"] == "Dirol жев рез 13.6 г", "человеку показываем его строку"
    from urllib.parse import quote_plus
    assert quote_plus("Жевательная резинка Dirol") in lines[0]["url"]


def test_without_a_unified_item_the_search_keeps_the_receipt_name(monkeypatch):
    """Нет привязки — ищем как написан товар: выдуманное имя вернуло бы чужой товар."""
    from urllib.parse import quote_plus

    from app import phonecart

    monkeypatch.setattr(phonecart.repo, "master_of", lambda pid: None)

    lines = phonecart._search_lines([{"product_id": 9, "name": "Сараф мор зеф 75 г"}], "pyaterochka")

    assert quote_plus("Сараф мор зеф 75 г") in lines[0]["url"]
