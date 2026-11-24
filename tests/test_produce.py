r"""Овощи и фрукты без бренда: в каждой сети — самый дешёвый на развес вариант вида (решение владельца 15.11.2026).

ЗАЧЕМ. Корзина владельца держит «Морковь 1 кг» × 3. Сопоставление по названию клало в Магнит и Дикси
«Морковь мытая 1кг» (83 ₽ и 59,99 ₽ без наличия), а весовая «Морковь» лежала там по 29,4 и 25,99 ₽/кг: морковь
серела в «Где дешевле». Что здесь проверяется:

1. что считается видом (корень, цвет, мытый, молодой, сорт) и что нет (марка, лишнее слово, переработанное);
2. какую строку берёт app/catalog/produce.py и когда стоящую связь оставляет;
3. что связь с методом «cheapest» не служит мостом к единому товару;
4. что наряд переводит штучный товар («Морковь 1 кг» × 3) в килограммы строки на развес, а проверка цены
   перед оформлением считает так же.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import cartplan, config, repo, service
from app.catalog import link, produce, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.models import Product


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN):
        memo.clear()
    yield
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN):
        memo.clear()


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def kind(name: str, *, unit="pcs", weight_g=1000.0, brand=None):
    """Вид товара человека; None — это конкретная карточка."""
    return produce.spec_of(SimpleNamespace(name=name, brand=brand, unit=unit, weight_g=weight_g))


def fits(product_name: str, row_name: str) -> bool:
    spec = kind(product_name)
    return spec is not None and produce._fits(spec, produce._parse(row_name))


def chains() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def sku(pid: int, chain: str) -> str | None:
    found = repo.confirmed_mapping(pid, chains()[chain], real_only=True)
    return found["sku"] if found else None


def method(pid: int, chain: str) -> str | None:
    found = repo.confirmed_mapping(pid, chains()[chain], real_only=True)
    return repo.mapping_method(pid, found["id"]) if found else None


def pick(pid: int, chain: str, number: str, name: str, **kw) -> None:
    """Выбор человека (или живой поиск): артикул подтверждён без метода."""
    repo.confirm_mapping(pid, repo.upsert_store_product(chains()[chain], number, name, **kw))


def person_product(name="Морковь 1 кг", unit="pcs", weight_g=1000.0) -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=unit, weight_g=weight_g))


def stock(chain: str, *rows: P) -> None:
    store.upsert_products(chain, list(rows))


def carrots() -> None:
    """Как на боевой базе 15.11.2026: у Магнита и Дикси есть мытая в пакете и весовая."""
    stock("magnit", P("1", "Морковь мытая 1кг", unit="pcs", weight_g=1000, price=83.0, in_stock=True),
          P("2", "Морковь", unit="kg", price=29.4, in_stock=True))
    stock("dixy", P("3", "Морковь мытая 1кг", unit="pcs", weight_g=1000, price=59.99, in_stock=False),
          P("4", "Морковь", unit="kg", weight_g=1000, price=25.99, in_stock=True))
    stock("lenta", P("5", "Морковь, весовая", unit="kg", price=27.29, in_stock=True))
    refresh.match_all()


# ---------- 1. что считается видом ----------
@pytest.mark.parametrize("name", [
    "Морковь 1 кг", "Картофель белый грязный 1.5 кг", "Огурцы короткоплодные пупырчатые", "Лук репчатый",
    "Яблоки гренни смит", "Капуста белокочанная ранняя", "Картофель крупный отборный 1 кг", "Бананы 1 кг"])
def test_a_plain_vegetable_or_fruit_is_a_kind(name):
    assert kind(name, unit="kg", weight_g=None) is not None


@pytest.mark.parametrize("name", [
    "Морковь витаминная 1 кг",            # у моркови нет сортов: лишнее слово — уже карточка
    "Огурцы атлет 1 кг", "Картофель беби мытый 2 кг", "Биотворог груша тема 4 95 г",
    "Йогурт активиа с яблоком", "Сок яблочный", "Салат морковь свекла", "Морковь по-корейски 300 г",
    "Огурцы маринованные", "Картофель фри замороженный", "Свекла вареная", "Картофель очищенный",
    "Огурцы корнишоны", "Помидоры протертые", "Перец чили красный", "Перец острый"])
def test_a_card_or_processed_food_is_not_a_kind(name):
    assert kind(name, unit="kg", weight_g=None) is None


def test_a_branded_product_is_a_card_and_not_a_kind():
    assert kind("Морковь", unit="kg", brand="Лукошкино") is None


def test_a_piece_product_needs_a_known_weight_from_a_kilogram():
    assert kind("Морковь 1 кг", weight_g=1000.0) is not None
    assert kind("Морковь", weight_g=None) is None             # штука неизвестного веса: цену на развес не посчитать
    assert kind("Огурцы грунтовые 500 г", weight_g=500.0) is None


def test_potato_asks_for_the_colour_the_wash_and_the_age_the_person_named():
    name = "Картофель белый грязный 1.5 кг"
    assert fits(name, "Картофель")                             # без слов — белый и немытый
    assert fits(name, "Картофель белый")
    assert not fits(name, "Картофель белый мытый")
    assert not fits(name, "Картофель красный")
    assert not fits(name, "Картофель молодой")
    assert not fits(name, "Картофель Гала")                    # сорт без слова о цвете: цвет неизвестен
    assert fits("Картофель", "Картофель") and fits("Картофель", "Картофель белый")   # молчит — белый, немытый
    assert not fits("Картофель", "Картофель красный")          # владелец: белый и красный — разные товары
    assert not fits("Картофель", "Картофель мытый")            # и мытый с немытым — разные
    assert not fits("Картофель", "Картофель молодой")          # молодой — тоже отдельный товар
    assert fits("Картофель красный", "Картофель красный") and not fits("Картофель красный", "Картофель")
    assert fits("Картофель молодой", "Картофель молодой") and not fits("Картофель молодой", "Картофель")


def test_carrots_and_cucumbers_take_the_washed_and_the_unwashed_and_every_subtype():
    assert fits("Морковь 1 кг", "Морковь") and fits("Морковь 1 кг", "Морковь мытая")
    assert fits("Огурцы короткоплодные пупырчатые", "Огурцы среднеплодные")
    assert fits("Огурцы короткоплодные пупырчатые", "Огурцы гладкие")
    assert not fits("Морковь 1 кг", "Свекла")


def test_apples_keep_the_variety_the_person_named():
    assert fits("Яблоки гренни смит", "Яблоки Гренни Смит")
    assert not fits("Яблоки гренни смит", "Яблоки Голден")
    assert not fits("Яблоки гренни смит", "Яблоки")             # сорт не назван — не подходит
    assert fits("Яблоки", "Яблоки Голден")                       # человек не назвал сорт — любой


def test_cabbage_without_a_word_is_the_white_one():
    assert fits("Капуста", "Капуста белокочанная") and fits("Капуста", "Капуста")
    assert not fits("Капуста", "Капуста цветная")


def test_the_colour_of_a_sorted_kind_is_part_of_the_variety():
    assert not fits("Перец красный", "Перец грунтовой")          # боевая база 15.11.2026: 47,70 вместо 192,49 ₽/кг
    assert fits("Перец красный", "Перец красный") and fits("Перец красный", "Перец болгарский красный")
    assert not fits("Капуста красная", "Капуста белокочанная")
    assert fits("Капуста красная", "Капуста краснокочанная")
    assert fits("Капуста белая", "Капуста белокочанная") and fits("Капуста белая", "Капуста")
    assert not fits("Яблоки красные", "Яблоки Гренни Смит") and not fits("Яблоки зелёные", "Яблоки красные")
    assert not fits("Томаты красные", "Томаты желтые")
    assert not fits("Помидоры красные", "Томаты")                 # цвет назван, у строки его нет: не берём


def test_one_shelf_under_different_words_of_the_same_root():
    assert fits("Помидоры 1 кг", "Томаты") and fits("Помидоры 1 кг", "Помидор")
    assert fits("Лимоны 1 кг", "Лимон") and fits("Огурцы 1 кг", "Огурец") and fits("Яблоко 1 кг", "Яблоки")
    assert fits("Картошка 2 кг", "Картофель") and not fits("Картошка 2 кг", "Картофель мытый")


def test_onion_row_with_extra_words_is_another_vegetable():
    assert fits("Лук репчатый", "Лук репчатый") and fits("Лук репчатый", "Лук")
    assert not fits("Лук репчатый", "Лук красный")               # без слова о цвете лук жёлтый, красный дороже и другой
    assert not fits("Лук репчатый", "Лук-порей")
    assert fits("Лук красный", "Лук красный") and not fits("Лук красный", "Лук репчатый")


# ---------- 2. какую строку берём ----------
def test_the_cheapest_weighed_row_replaces_the_pack_that_was_out_of_stock_or_dearer(catalog, user_db):
    carrots()
    pid = person_product()
    pick(pid, "magnit", "1", "Морковь мытая 1кг", weight_g=1000, unit="pcs")      # живой поиск взял первую карточку
    pick(pid, "dixy", "3", "Морковь мытая 1кг", weight_g=1000, unit="pcs")

    link.link_products([pid])

    assert [sku(pid, c) for c in ("magnit", "dixy", "lenta")] == ["2", "4", "5"]
    assert {method(pid, c) for c in ("magnit", "dixy", "lenta")} == {link.PRODUCE}
    held = {m["sku"] for m in repo.product_mappings(pid)}
    assert not held & {"1", "3"}                                 # старые карточки не подтверждены


def test_the_pack_that_is_cheaper_per_kilogram_stays(catalog, user_db):
    stock("magnit", P("1", "Морковь мытая 1кг", unit="pcs", weight_g=1000, price=20.0, in_stock=True),
          P("2", "Морковь", unit="kg", price=29.4, in_stock=True))
    refresh.match_all()
    pid = person_product()
    pick(pid, "magnit", "1", "Морковь мытая 1кг", weight_g=1000, unit="pcs")

    link.link_products([pid])

    assert sku(pid, "magnit") == "1" and method(pid, "magnit") is None


def test_a_row_without_stock_or_with_an_old_price_is_not_taken(catalog, user_db):
    stock("magnit", P("2", "Морковь", unit="kg", price=10.0, in_stock=False))
    stock("dixy", P("4", "Морковь", unit="kg", weight_g=1000, price=11.0, in_stock=True))
    stock("lenta", P("5", "Морковь, весовая", unit="kg", price=12.0, in_stock=True))
    stock("metro", P("6", "Морковь весовая", unit="kg", price=13.0, in_stock=True))
    refresh.match_all()
    with store.connect() as conn:                                # цена Дикси двухнедельной давности
        conn.execute("UPDATE chain_products SET price_seen = '2026-11-01T03:30:00' WHERE chain = 'dixy'")
        conn.commit()
    pid = person_product()

    link.link_products([pid])

    assert sku(pid, "magnit") is None and sku(pid, "dixy") is None
    assert sku(pid, "lenta") == "5" and sku(pid, "metro") == "6"


def test_only_a_weighed_row_is_taken_and_a_pack_alone_changes_nothing(catalog, user_db):
    stock("magnit", P("1", "Морковь мытая 1кг", unit="pcs", weight_g=1000, price=83.0, in_stock=True))
    refresh.match_all()
    pid = person_product()
    pick(pid, "magnit", "1", "Морковь мытая 1кг", weight_g=1000, unit="pcs")

    link.link_products([pid])

    assert sku(pid, "magnit") == "1" and method(pid, "magnit") is None


def test_the_choice_goes_when_the_weighed_row_goes_and_the_chain_returns_to_the_usual_matching(catalog, user_db):
    carrots()
    pid = person_product("Морковь мытая 1 кг")                  # этот товар единая база связывает с мытой в пакете
    link.link_products([pid])
    assert sku(pid, "magnit") == "2"

    with store.connect() as conn:
        conn.execute("UPDATE chain_products SET in_stock = 0 WHERE chain = 'magnit' AND sku = '2'")
        conn.commit()
    link.link_products([pid])        # доводы товара не изменились: память прохода сама связь не вернула бы

    assert (sku(pid, "magnit"), method(pid, "magnit")) == ("1", link.MASTER)
    assert sku(pid, "lenta") == "5"                              # другие сети не тронуты


def test_a_slightly_cheaper_row_does_not_move_the_choice(catalog, user_db):
    carrots()
    pid = person_product()
    link.link_products([pid])
    assert sku(pid, "magnit") == "2"                             # 29,4 ₽/кг

    stock("magnit", P("7", "Морковь свежая", unit="kg", price=29.0, in_stock=True))     # дешевле на 1,4 %
    refresh.match_all()
    link.link_products([pid])
    assert sku(pid, "magnit") == "2"

    stock("magnit", P("8", "Морковь отборная", unit="kg", price=20.0, in_stock=True))   # дешевле на треть
    refresh.match_all()
    link.link_products([pid])
    assert sku(pid, "magnit") == "8"


def test_a_weighed_row_written_in_another_form_of_the_word_is_found(catalog, user_db):
    stock("magnit", P("9", "Лимон", unit="kg", price=150.0, in_stock=True))
    stock("lenta", P("8", "Томаты", unit="kg", price=120.0, in_stock=True))
    refresh.match_all()
    lemons = person_product("Лимоны 1 кг")
    tomatoes = person_product("Помидоры 1 кг")

    link.link_products([lemons, tomatoes])

    assert sku(lemons, "magnit") == "9" and sku(tomatoes, "lenta") == "8"


def test_the_cheapest_row_is_not_a_doubt_on_the_result_screen(catalog, user_db):
    carrots()
    pid = person_product()
    link.link_products([pid])
    basket = repo.create_basket("проба")
    repo.set_basket_item(basket, pid, 3)

    assert method(pid, "magnit") == link.PRODUCE
    assert service.basket_doubts(basket) == []                   # «Морковь 1 кг» против «Морковь»: сходство 0,74


def test_a_chain_the_person_removed_is_left_alone(catalog, user_db):
    carrots()
    pid = person_product()
    pick(pid, "magnit", "1", "Морковь мытая 1кг", weight_g=1000, unit="pcs")
    repo.drop_mapping(pid, chains()["magnit"])

    link.link_products([pid])

    assert sku(pid, "magnit") is None
    assert sku(pid, "dixy") == "4"


def test_a_branded_product_is_left_to_the_usual_matching(catalog, user_db):
    carrots()
    pid = repo.upsert_product(Product(id=None, name="Морковь", brand="Лукошкино", unit="kg"))

    link.link_products([pid])

    assert method(pid, "magnit") != link.PRODUCE and method(pid, "lenta") != link.PRODUCE


def test_the_cheapest_choice_is_the_decision_of_the_app_and_not_a_bridge_to_the_catalog():
    mappings = [{"method": link.PRODUCE, "sku": "2"}, {"method": link.MASTER, "sku": "3"},
                {"method": None, "sku": "4"}]

    assert link._human(mappings) == [{"method": None, "sku": "4"}]


# ---------- 4. наряд и проверка цены: штуки → килограммы ----------
def basket_of(pid: int, qty: float) -> list:
    basket = repo.create_basket("проба")
    repo.set_basket_item(basket, pid, qty)
    return service.build_basket_lines(basket)


def test_three_kilograms_of_carrots_by_the_piece_become_three_kilograms_on_the_weighed_row(catalog, user_db, monkeypatch):
    monkeypatch.setattr(cartplan, "available", lambda code: True)
    carrots()
    pid = person_product("Морковь 1 кг", weight_g=1000.0)
    link.link_products([pid])

    lines = basket_of(pid, 3)
    plan = cartplan.build("magnit", lines)

    assert lines[0].prices["magnit"] == 88.2                     # 3 кг по 29,4
    line = plan.lines[0]
    assert (line.sku, line.qty, line.per, line.unit, line.pack_g) == ("2", 3.0, "kg", "kg", None)
    from app.shopbrowser import cart
    assert cart.pieces_of(line)[0] == 3


def test_two_packs_of_one_and_a_half_kilograms_are_three_kilograms(catalog, user_db, monkeypatch):
    monkeypatch.setattr(cartplan, "available", lambda code: True)
    carrots()
    pid = person_product("Морковь 1.5 кг", weight_g=1500.0)
    link.link_products([pid])

    lines = basket_of(pid, 2)
    plan = cartplan.build("magnit", lines)

    assert lines[0].prices["magnit"] == 88.2 and plan.lines[0].qty == 3.0
    checked = cartplan.verify(plan, refresh=lambda ids, stores: None)   # проверка считает тем же правилом
    assert checked.changes == [] and checked.total == 88.2 and checked.lines[0].qty == 3.0


def test_a_weighed_product_keeps_its_kilograms_and_a_piece_product_without_weight_keeps_its_count(
        catalog, user_db, monkeypatch):
    monkeypatch.setattr(cartplan, "available", lambda code: True)
    carrots()
    weighed = person_product("Морковь", unit="kg", weight_g=None)
    bare = person_product("Морковь штучная", unit="pcs", weight_g=None)
    for pid in (weighed, bare):
        pick(pid, "magnit", "2", "Морковь", unit="kg")

    kilo = cartplan.build("magnit", basket_of(weighed, 2.5)).lines[0]
    count = cartplan.build("magnit", basket_of(bare, 3)).lines[0]

    assert (kilo.qty, kilo.per) == (2.5, "kg")
    assert (count.qty, count.per) == (3.0, "pcs")                 # перевести нечем: количество как было


def test_a_piece_product_on_a_packed_row_is_not_touched(catalog, user_db, monkeypatch):
    monkeypatch.setattr(cartplan, "available", lambda code: True)
    stock("magnit", P("1", "Морковь мытая 1кг", unit="pcs", weight_g=1000, price=20.0, in_stock=True))
    refresh.match_all()
    pid = person_product()
    pick(pid, "magnit", "1", "Морковь мытая 1кг", weight_g=1000, unit="pcs")
    repo.save_price(repo.confirmed_mapping(pid, chains()["magnit"])["id"], 20.0)

    line = cartplan.build("magnit", basket_of(pid, 3)).lines[0]

    assert (line.qty, line.per, line.unit) == (3.0, "pcs", "pcs")
