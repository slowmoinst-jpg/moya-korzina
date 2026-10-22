"""Карточки витрины с телефона: единственная дорога каталога для Пятёрочки и Самоката.

Замеры 18–19.10.2026: сервер не пускают ни 5ka.ru, ни samokat.ru — ни с серверного
адреса, ни с домашнего. Витрину видит телефон человека, и приложение присылает
прочитанные карточки: из адреса берётся настоящий артикул сети, товар заводится в
единой базе, а ночное сопоставление получает строку, которой ему не хватало.
"""
from __future__ import annotations

import pytest

from app import phonecart
from app.catalog import store


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """Пустая единая база на время проверки: считаем только то, что завёл телефон."""
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    yield store


def test_cards_of_the_chain_land_in_the_unified_base(base):
    """Карточка Пятёрочки с её адресом заводит товар с настоящим артикулом."""
    answer = phonecart.accept_cards({
        "store": "pyaterochka",
        "cards": [
            {"url": "https://5ka.ru/product/moloko-prostokvashino--1234567/",
             "name": "Молоко Простоквашино 2,5% 930 мл", "price": 99.9, "in_stock": True},
            {"url": "https://5ka.ru/product/1234567/",
             "name": "Молоко Простоквашино 2,5% 930 мл", "price": 99.9, "in_stock": True},
        ],
    })

    assert answer["ok"] and answer["store"] == "pyaterochka"
    assert answer["got"] == 2 and answer["with_sku"] == 2
    assert answer["learned"] == 1, "второй раз тот же артикул не заводим"
    rows = base.products_of_chain("pyaterochka")
    assert [(r["sku"], r["name"]) for r in rows] == [
        ("1234567", "Молоко Простоквашино 2,5% 930 мл")]
    assert rows[0]["price"] == 99.9
    assert rows[0]["in_stock"] == 1, "наличие с витрины записано"


def test_the_card_must_be_that_chain_and_have_a_readable_sku(base):
    """Чужой адрес и адрес без артикула в базу не попадают — и это видно в ответе."""
    answer = phonecart.accept_cards({
        "store": "pyaterochka",
        "cards": [
            {"url": "https://samokat.ru/product/moloko-78001656", "name": "Чужой товар"},
            {"url": "https://5ka.ru/catalog/", "name": "Страница без карточки"},
            {"url": "https://5ka.ru/product/7654321/", "name": ""},
            "не карточка",
        ],
    })

    assert answer["got"] == 3, "не-объект в списке не считается карточкой"
    assert answer["with_sku"] == 0 and answer["learned"] == 0
    assert answer["no_sku"] == 2 and answer["no_name"] == 1
    assert base.products_of_chain("pyaterochka") == []


def test_a_chain_whose_card_url_is_unknown_takes_nothing(base):
    """У сетей без правила адреса (metro, vkusvill) артикул не выдумываем."""
    answer = phonecart.accept_cards({
        "store": "vkusvill",
        "cards": [{"url": "https://vkusvill.ru/goods/moloko-12345.html", "name": "Молоко 1 л"}],
    })

    assert answer["sku_rule"] is False
    assert answer["with_sku"] == 0 and base.products_of_chain("vkusvill") == []


def test_known_rows_keep_what_the_crawler_wrote(base):
    """Уже известную сборщику строку телефон не обедняет названием, но цену обновляет.

    Название остаётся сборщика (его полнее), а цена берётся та, что человек видит
    сейчас: у Перекрёстка, Vprok и Монетки она вообще единственная, а у остальных
    свежее ночного обхода.
    """
    phonecart.accept_cards({
        "store": "samokat",
        "cards": [{"url": "https://samokat.ru/product/moloko-prostokvashino-78001656",
                   "name": "Молоко", "price": 10}],
    })
    first = base.products_of_chain("samokat")[0]

    phonecart.accept_cards({
        "store": "samokat",
        "cards": [{"url": "https://samokat.ru/product/moloko-prostokvashino-78001656",
                   "name": "Молоко, 930 мл", "price": 120}],
    })

    row = base.products_of_chain("samokat")[0]
    assert row["name"] == first["name"] == "Молоко", "переименования от телефона не ждём"
    assert row["price"] == 120, "цену берём ту, что человек видит на витрине"


def test_the_card_name_goes_into_the_base_without_shop_badges():
    """Название чистит и сервер: база не должна зависеть от версии приложения.

    Замер 18–19.10.2026: строки Пятёрочки пришли как «-22% 4,95 Томатная паста
    Помидорка 250мл 250 мл 205 00 ₽» — по такому имени ни показать товар, ни
    сопоставить. Наклейки витрины снимает и приложение, но старые сборки шлют как есть.
    """
    assert phonecart._card_name(
        "-22% 4,95 Томатная паста Помидорка 250мл 250 мл 205 00 ₽"
    ) == "Томатная паста Помидорка 250мл 250 мл"
    assert phonecart._card_name(
        "Сыр Galbani Маскарпоне 80% БЗМЖ 250г 250 г 629 00 ₽"
    ) == "Сыр Galbani Маскарпоне 80% БЗМЖ 250г 250 г", "процент внутри имени — часть товара"
    assert phonecart._card_name(
        "Нет оценок Комбо 4,84 Суп гороховый Пятёрочка Кафе 250г 139 99 ₽"
    ) == "Суп гороховый Пятёрочка Кафе 250г"
    assert phonecart._card_name("от 2 шт 4,28 Томаты Черри маринованные") == \
        "Томаты Черри маринованные"
    assert phonecart._card_name("Выгодно 4,91 Имбирь маринованный 200г") == \
        "Имбирь маринованный 200г"
    # Наклейки Самоката: переключатель страниц и слова витрины стоят внутри карточки.
    assert phonecart._card_name(
        "−13% 1 2 3 4 5 6 7 8 Больше нет Омлет-ролл Creative Kitchen, "
        "с филе полосатого тунца 200 г·Много белка 299259 ₽"
    ) == "Омлет-ролл Creative Kitchen, с филе полосатого тунца 200 г·Много белка"
    assert phonecart._card_name("   ") == ""


def test_the_cleaner_does_not_touch_a_clean_name():
    """Чистое имя проходит насквозь: правила не должны съедать товар."""
    for name in ("Молоко Простоквашино 2,5% 930 мл", "Хлеб Бородинский 400 г",
                 "Шоколад Бабаевский Элитный горький 75% 75г"):
        assert phonecart._card_name(name) == name


def test_cards_without_a_list_are_refused(base):
    """Запрос без списка карточек — понятный отказ, а не пустая работа."""
    with pytest.raises(ValueError):
        phonecart.accept_cards({"store": "samokat"})
    with pytest.raises(ValueError):
        phonecart.accept_cards({"store": "нет такой сети", "cards": []})


def test_no_more_than_the_limit_is_taken_in_one_go(base):
    """Предел за заход: одна страница витрины, а не выгрузка каталога."""
    cards = [{"url": f"https://samokat.ru/product/tovar-{i}", "name": f"Товар {i}"}
             for i in range(phonecart.CARDS_LIMIT + 25)]

    answer = phonecart.accept_cards({"store": "samokat", "cards": cards})

    assert answer["got"] == phonecart.CARDS_LIMIT
    assert len(base.products_of_chain("samokat")) == phonecart.CARDS_LIMIT


def test_a_card_updates_the_price_of_a_row_we_already_know(base):
    """Цена из карточки доходит и до знакомой строки — у трёх сетей это единственный источник.

    У Перекрёстка (158 698 строк), Vprok (27 551) и Монетки (7 772) цен нет ни у одной:
    их обход отдаёт только названия. Человек открывает раздел — и цены того, что он
    видит, приходят в базу. Без этого шага чтение раздела этим сетям не давало ничего:
    все артикулы уже известны, и строки пропускались.
    """
    with base.connect() as conn:
        conn.execute("insert into chain_products (chain, sku, name, active, first_seen, last_seen)"
                     " values ('perekrestok','3342904','Блины с мясом, 370 г','1',?,?)",
                     (store.now(), store.now()))
        conn.commit()

    answer = phonecart.accept_cards({
        "store": "perekrestok",
        "cards": [{"url": "https://www.perekrestok.ru/cat/58/p/"
                          "bliny-s-masom-zamorozennye-morozko-370g-3342904",
                   "name": "Блины с мясом, 370 г", "price": 249.9}],
    })

    assert answer["with_sku"] == 1 and answer["learned"] == 0, "строка уже была в базе"
    assert answer["repriced"] == 1
    row = base.products_of_chain("perekrestok")[0]
    assert row["price"] == 249.9 and row["price_seen"]


def test_the_card_urls_of_the_names_only_chains_are_understood():
    """Правила взяты с адресов самой базы (замер 19.10.2026), а не выдуманы."""
    from app.catalog import fromurl

    assert fromurl.sku_of("perekrestok", "https://www.perekrestok.ru/cat/58/p/"
                           "bliny-s-masom-zamorozennye-morozko-370g-3342904") == "3342904"
    assert fromurl.sku_of("vprok", "https://www.vprok.ru/product/"
                           "linakva-linakva-bebi-0-9-aerozol-150-ml--1284993") == "1284993"
    assert fromurl.sku_of("monetka", "https://monetka.ru/product/"
                           "shokolad-molochnyjj-milka-80g-rossiya-810002331/") == "810002331"
    # Чужой адрес по-прежнему артикула не даёт.
    assert fromurl.sku_of("perekrestok", "https://example.com/cat/58/p/x-3342904") is None
