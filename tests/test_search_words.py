"""Поиск товара находит то, что ищут: «сыр», «хлеб», «молоко» (автопилот 26.10.2026).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Знакомым владельца отдают телефонную «Корзину», и первое, что они
наберут, — «сыр», «хлеб», «молоко». Замер на каталоге в 275 тысяч товаров нашёл три дыры,
и каждая стережётся отдельно:

- «сыр» не находил ничего. Нормализация выбрасывает родовые слова (NOISE_WORDS: «сыр»,
  «мягкий», «пакет»), и в norm их нет. Поиск человека (by_name) ищет такие слова в сыром
  названии; сопоставление чеков и сетей зовёт search_items без by_name, и для него ничего
  не меняется;
- «хлеб» ставил «Хлебцы» наравне с «Хлебом» и терял настоящий хлеб, когда хлебцев много:
  порядок наводился в Python уже после SQL LIMIT, отсортированного по числу сетей. Теперь
  ранжирует сам SQL: целое слово в начале, потом начало слова, потом остальное;
- телефон показывал пять товаров каталога из сотен.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Сколько поиск занимает на живом каталоге: это замер на
data/catalog.db, а не тест на пустой временной базе.
"""
from __future__ import annotations

import pytest

from app import users
from app.catalog import refresh, store
from app.catalog.model import ChainProduct as P

PHONE = "79990000112"
THREE = ("lenta", "magnit", "vkusvill")


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """Временный каталог, как в tests/test_catalog.py, и пустая папка рабочих мест:
    match_all открывает личные базы на чтение, и настоящим из них в тесте делать нечего."""
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    store.init()
    yield


@pytest.fixture
def web(catalog, tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_phone_newcomer.py."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))

    from app.web import create_app
    from app.web.screens import result

    result._CALC.clear()
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result._CALC.clear()
    users.deactivate()


def seed(goods: dict[str, tuple[str, ...]]) -> None:
    """Товары в каталоге сетей: {название: в каких сетях оно есть}.

    Одно название в нескольких сетях match_all сводит в один единый товар, и число этих
    сетей — то самое chains_n, по которому поиск раньше строил порядок.
    """
    by_chain: dict[str, list[P]] = {}
    for n, (name, chains) in enumerate(goods.items()):
        for chain in chains:
            by_chain.setdefault(chain, []).append(P(str(n), name))
    for chain, products in by_chain.items():
        store.upsert_products(chain, products)
    refresh.match_all()


def names(query: str, limit: int = 20, **kw) -> list[str]:
    return [item["name"] for item in store.search_items(query, limit, **kw)]


# ---------- 1. родовые слова ищутся по названию — только в поиске человека ----------
def test_a_generic_word_finds_cheese_by_name_and_a_whole_word_goes_first(catalog):
    """«сыр» — родовое слово, в norm его нет; поиск человека находит «Сыр …» по названию.

    Все три написания регистра: «Сыр», «СЫР» (строка Дикси целиком заглавными) и «сыр»
    в середине названия. «Сырок» и «Творожный сыр» известны большему числу сетей, но
    «Сыр …» идёт первым, а «Сырок …» (приставка) — раньше «Творожного сыра» (слово
    где-то в середине).
    """
    seed({"Сыр Российский 200 г": ("lenta",),
          "СЫР ГОЛЛАНДСКИЙ 300 г": ("lenta",),
          "Сырок творожный Свитлогорье 45 г": THREE,
          "Творожный сыр Hochland 140 г": THREE,
          "Молоко Простоквашино 930 мл": THREE})

    found = names("сыр", by_name=True)

    assert set(found[:2]) == {"Сыр Российский 200 г", "СЫР ГОЛЛАНДСКИЙ 300 г"}
    assert found[2:] == ["Сырок творожный Свитлогорье 45 г", "Творожный сыр Hochland 140 г"]


def test_without_by_name_a_generic_word_still_finds_nothing(catalog):
    """Сопоставление чеков и сетей поиск по родовым словам не получает: ему это лишние кандидаты."""
    seed({"Сыр Российский 200 г": ("lenta",)})

    assert store.search_items("сыр") == []
    assert store.search_items("мягкий сыр") == []


def test_a_generic_word_next_to_other_words_is_required_only_in_the_persons_search(catalog):
    """«сыр российский» человек ищет сыр, а не шоколад «Российский»; сопоставление — как раньше."""
    seed({"Сыр Российский 200 г": ("lenta",), "Шоколад Российский 90 г": ("lenta",)})

    assert names("сыр российский", by_name=True) == ["Сыр Российский 200 г"]
    assert set(names("сыр российский")) == {"Сыр Российский 200 г", "Шоколад Российский 90 г"}


def test_every_generic_word_of_the_query_must_be_in_the_name(catalog):
    seed({"Сыр мягкий Бри 125 г": ("lenta",), "Сыр твердый Маасдам 200 г": ("lenta",)})

    assert names("мягкий сыр", by_name=True) == ["Сыр мягкий Бри 125 г"]


# ---------- 2. начало названия — целым словом и до LIMIT ----------
def test_bread_goes_before_crispbread_even_when_there_are_more_crispbreads_than_the_old_prelimit(catalog):
    """70 хлебцев в двух сетях против одного хлеба в одной — больше прежнего пред-лимита
    (max(limit*6, 60)): хлеб вылетал из выборки до того, как Python ставил его первым."""
    goods = {f"Хлебцы ржаные вкус{n}": ("lenta", "magnit") for n in range(70)}
    goods["Хлеб Дарницкий 700 г"] = ("lenta",)
    goods["Квас хлебный Очаковский 1,5 л"] = THREE
    seed(goods)

    assert names("хлеб", 5)[0] == "Хлеб Дарницкий 700 г"
    everything = names("хлеб", 100)
    assert everything[0] == "Хлеб Дарницкий 700 г"           # целое слово в начале
    assert everything[-1] == "Квас хлебный Очаковский 1,5 л"  # слово в середине — последним
    assert all(n.startswith("Хлебцы") for n in everything[1:-1])


def test_milk_still_comes_before_a_curd_with_milk_in_its_brand(catalog):
    """Прежнее правило цело: «молоко» — это молоко, а не творог марки «Лебедянь молоко»."""
    seed({"Молоко Простоквашино 930 мл": ("lenta",), "Творог Лебедянь молоко 200 г": THREE})

    assert names("молоко")[0] == "Молоко Простоквашино 930 мл"


# ---------- 3. телефону — больше строк ----------
def enter(client):
    from conftest import sign_in

    return sign_in(client, PHONE)


def found_block(page: str) -> str:
    """Кусок HTML с найденным: от карточки до конца её формы — без списка корзины."""
    start = page.index('class="card found"')
    return page[start:page.index("</form>", start)]


def test_the_phone_shows_more_than_five_catalog_goods_and_the_web_hint_keeps_five(web):
    """Телефон просит у поиска 30 строк (15 своих и 15 каталога), веб-подсказка — прежние 5 + 5."""
    enter(web)
    users.open_workspace(PHONE)
    seed({f"Сыр вкус{n}": ("lenta", "magnit") for n in range(20)})

    page = web.get("/phone/basket?q=сыр").data.decode("utf-8")
    assert found_block(page).count('value="catalog:') == 15

    hint = web.get("/basket/suggest?q=сыр").get_json()["items"]
    assert [item["do"].split(":")[0] for item in hint] == ["catalog"] * 5
