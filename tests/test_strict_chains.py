"""Строгая сверка названий по каталогам сетей вне X5 (app/catalog/x5.fill_chains).

ЗАЧЕМ (замер 15.11.2026 по живым привычкам владельца). Единая база сводит строки сетей в один товар
попарным сравнением названий, и тот же товар нередко остаётся в соседнем едином товаре: «Lay's»
против «Lays», «Шоколадный батончик» против «Шоколадные батончики». Корзина из истории ложилась
в сеть на 1–3 товара из 6. Здесь сторожа того, что пишется и что нет:

1. пара пишется, когда в сети ровно одна живая строка сходится с товаром словами в обе стороны;
2. неверная пара хуже отсутствующей: лишний вкус, чужая фасовка, «в ассортименте», потерянное
   слово марки, отрицание из чека («Н/ФИЛЬТР», «Б/А»), которого нет на карточке, спорящие между
   собой чеки, чужой штрихкод строки, две подходящие строки — связи нет;
3. сеть, где связь уже есть или которую человек убрал, не трогается;
4. своя связь, чья строка ушла с витрины, снимается;
5. такая связь не мост к единому товару и не сомнение на «Результате».
"""
from __future__ import annotations

import pytest

from app import config, repo, service
from app.catalog import link, refresh, store, x5
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.models import Product


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN, x5._DONE):
        memo.clear()
    yield
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN, x5._DONE):
        memo.clear()


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def chains() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def shelf(chain: str, *rows: P) -> None:
    store.upsert_products(chain, list(rows))
    refresh.match_all()


def chips(**kw) -> int:
    """Товар человека из чека Перекрёстка: в корзине, марка известна."""
    pid = repo.upsert_product(Product(id=None, name="Чипсы lay солью 140 г", brand="Lay's",
                                      weight_g=140.0, unit="pcs", **kw))
    repo.add_history_row("2026-10-10", chains()["perekrestok"], pid, "Чипсы LAY'S с солью 140г",
                         1, 119.0, 119.0, "k-1")
    return pid


def pair(pid: int, chain: str) -> tuple[str, str | None] | None:
    found = repo.confirmed_mapping(pid, chains()[chain], real_only=True)
    return (found["sku"], repo.mapping_method(pid, found["id"])) if found else None


def refill() -> dict:
    x5._DONE.clear()
    return x5.fill()


def test_the_only_matching_row_of_a_chain_is_linked(catalog, user_db):
    shelf("magnit", P("1000422638", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs", price=129.0),
          P("1000422634", "Чипсы Lays Рифленые Сметана и лук 140г", weight_g=140, unit="pcs"))
    shelf("monetka", P("810004270", "chipsy kartofelnye lays s solyu 140g rossiya", weight_g=140, unit="pcs"))
    pid = chips()

    res = refill()

    assert res["chains"] == {"magnit": 1}
    assert pair(pid, "magnit") == ("1000422638", link.STRICT)
    assert pair(pid, "monetka") is None        # «картофельные» у нас нет: слово карточки лишнее


@pytest.mark.parametrize("name, stored, grams", [
    ("voda pitevaya svyatoy istochnik negazirovannaya 05l", None, 500),     # Дикси: веса в каталоге нет
    ("napitok lyubimaya kola gazirovannyjj 033l rossiya", 33000.0, 330),    # Монетка: в каталоге 33 л
    ("vodka cistye rosy 40 0 05l", None, 50),                               # дробь через пробел — как была
    ("obodok ch001l 1st", 1000.0, 1000),                                    # артикул в названии, не объём
])
def test_a_slug_volume_without_a_separator_is_a_fraction(name, stored, grams):
    """Прогон 16.11.2026: «Вода Святой Источник 5 л» сходилась со строкой Дикси «…negazirovannaya 05l»."""
    assert x5.weight_of(name, stored) == pytest.approx(grams)


@pytest.mark.parametrize("card, weight", [
    ("Чипсы Lays Краб 140г", 140),                              # другой вкус
    ("Чипсы Lays С солью 150г", 150),                           # фасовка дальше 3 %
    ("Чипсы Lays С солью 140г в ассортименте", 140),            # вкус выбирает сборщик
    ("Чипсы картофельные с солью 140г", 140),                   # марки на карточке нет
])
def test_a_wrong_pair_is_worse_than_none(catalog, user_db, card, weight):
    shelf("magnit", P("1", card, weight_g=weight, unit="pcs"))
    pid = chips()

    assert refill()["chains"] == {}
    assert pair(pid, "magnit") is None


def test_two_matching_rows_leave_the_chain_alone(catalog, user_db):
    shelf("magnit", P("1", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs"),
          P("2", "Чипсы Lays с солью 140 г", weight_g=140, unit="pcs"))
    pid = chips()

    assert refill()["chains"] == {}
    assert pair(pid, "magnit") is None


def test_every_brand_word_of_the_product_must_be_on_the_card(catalog, user_db):
    """«Тульский» и «пряник» — тоже чьи-то марки: одного такого слова для пары мало (прогон 16.11.2026)."""
    shelf("dixy", P("2000145592", "Пряник Тульский с фруктовой начинкой 130г", brand="Тульский пряник",
                    weight_g=130, unit="pcs"))
    shelf("vkusvill", P("112187", "Пряник Ясная Поляна Тульский фруктовая начинка 130 г",
                        brand="Ясная Поляна", weight_g=130, unit="pcs"))
    pid = repo.upsert_product(Product(id=None, name="Тульский пряник ясная поляна фруктовой начинкой 130 г",
                                      weight_g=130.0, unit="pcs"))
    repo.set_basket_item(repo.create_basket("проба"), pid, 1)

    refill()

    assert pair(pid, "vkusvill") == ("112187", link.STRICT)
    assert pair(pid, "dixy") is None


def test_a_foreign_barcode_is_not_the_same_product(catalog, user_db):
    shelf("magnit", P("1", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs", barcode="4601234567893"))
    pid = chips(barcode="4607001770015")

    assert refill()["chains"] == {}
    assert pair(pid, "magnit") is None


def test_a_foreign_barcode_of_a_neighbour_row_does_not_stop_the_pair(catalog, user_db):
    """Штрихкод METRO не наш, а у строки Магнита в том же едином товаре кода нет: Магнит связывается.

    У одного товара бывает несколько кодов, и METRO хранит код коробки: на копии 16.11.2026 запрет
    по кодам единого товара снял бы 34 пары владельца с тем же названием, числами и фасовкой."""
    shelf("metro", P("9", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs", barcode="4601234567893"))
    shelf("magnit", P("1", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs"))
    pid = chips(barcode="4607001770015")

    assert refill()["chains"] == {"magnit": 1}
    assert pair(pid, "magnit") == ("1", link.STRICT) and pair(pid, "metro") is None


def beer(*receipts: str) -> int:
    """Товар, заведённый импортом чека Перекрёстка: наше название «Н/» и «Б/» уже потеряло."""
    pid = repo.upsert_product(Product(id=None, name=link.display_name(receipts[0]), weight_g=450.0, unit="pcs"))
    for i, receipt in enumerate(receipts):
        repo.add_history_row("2026-10-10", chains()["perekrestok"], pid, receipt, 1, 99.0, 99.0, f"k-{i}")
    return pid


@pytest.mark.parametrize("receipt, opposite, same", [
    ("ПИВО ЖИГУЛИ БАРНОЕ Н/ФИЛЬТР 0,45Л", "Пиво ЖИГУЛИ БАРНОЕ фильтрованное 0,45л",
     "Пиво ЖИГУЛИ БАРНОЕ нефильтрованное 0,45л"),
    ("ПИВО БАЛТИКА СВЕТЛОЕ Б/А 0,45Л", "Пиво БАЛТИКА светлое 0,45л",
     "Пиво БАЛТИКА светлое безалкогольное 0,45л"),
])
def test_a_negation_of_the_receipt_never_finds_the_opposite(catalog, user_db, receipt, opposite, same):
    """Прогон 16.11.2026: по нашему названию «Пиво жигули барное фильтр» пара с фильтрованным сходилась."""
    shelf("magnit", P("1", opposite, weight_g=450, unit="pcs"))
    shelf("lenta", P("2", same, weight_g=450, unit="pcs"))
    pid = beer(receipt)

    refill()

    assert pair(pid, "magnit") is None
    assert pair(pid, "lenta") == ("2", link.STRICT)


def test_receipts_that_contradict_each_other_link_nothing(catalog, user_db):
    """В одном товаре чеки «Б/А» и обычного пива: какую разновидность искать, неизвестно."""
    shelf("lenta", P("2", "Пиво БАЛТИКА светлое безалкогольное 0,45л", weight_g=450, unit="pcs"))
    pid = beer("ПИВО БАЛТИКА СВЕТЛОЕ Б/А 0,45Л", "ПИВО БАЛТИКА СВЕТЛОЕ 0,45Л")

    assert refill()["chains"] == {}
    assert pair(pid, "lenta") is None


def test_a_chain_with_a_link_or_a_refusal_is_not_touched(catalog, user_db):
    shelf("magnit", P("1", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs"))
    shelf("lenta", P("7", "Чипсы LAYS с солью, 140г", weight_g=140, unit="pcs"))
    pid = chips()
    mine = repo.upsert_store_product(chains()["magnit"], "555", "Чипсы Lays Краб 140г")
    repo.confirm_mapping(pid, mine)                             # выбор человека
    repo.drop_mapping(pid, chains()["lenta"])                   # отказ человека от сети

    assert refill()["chains"] == {}
    assert pair(pid, "magnit") == ("555", None)
    assert pair(pid, "lenta") is None


def test_own_pair_is_dropped_when_the_row_left_the_shelf(catalog, user_db):
    shelf("magnit", P("1", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs"))
    pid = chips()
    refill()
    assert pair(pid, "magnit") == ("1", link.STRICT)
    with store.connect() as conn:
        conn.execute("UPDATE chain_products SET active=0 WHERE chain='magnit' AND sku='1'")
        conn.commit()

    res = refill()

    assert res["gone"] == 1 and res["chains"] == {}
    assert pair(pid, "magnit") is None


def test_the_pair_is_no_bridge_and_no_doubt_and_survives_the_passes(catalog, user_db):
    """Полный проход дважды: связь стоит, единого товара у товара по ней не появилось."""
    shelf("magnit", P("1", "Чипсы Lays С солью 140г", weight_g=140, unit="pcs"))
    pid = chips()
    basket = repo.create_basket("проба")
    repo.set_basket_item(basket, pid, 1)
    refill()
    repo.set_master(pid, None, None)

    for _ in range(2):
        link.link_products()
        for memo in (link._SEEN, link._SYNCED, link._WEAK, x5._DONE):
            memo.clear()

    assert pair(pid, "magnit") == ("1", link.STRICT)
    assert link._human(repo.product_mappings(pid)) == []
    assert service.basket_doubts(basket) == []                  # «lay» против «Lays» дало бы сомнение «бренд»
