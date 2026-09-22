"""Каталог и Связи на новом интерфейсе: адрес вместо памяти и сеть только по нажатию.

Здесь проверяется не вёрстка, а два обещания переезда, которые ломаются молча.

ПЕРВОЕ: ЭКРАН ОПИСАН АДРЕСОМ. В Streamlit поиск и выбранный товар жили в session_state,
и это стоило ссылки, кнопки «назад» и второй вкладки. Сломать это обратно легко —
достаточно один раз положить фильтр в сессию, и никакой тест вёрстки этого не заметит:
страница выглядит правильно, просто ссылка на неё у соседа показывает другое. Поэтому
проверяется именно то, что ответ зависит ТОЛЬКО от адреса.

ВТОРОЕ: СЕТЬ ДЁРГАЕТСЯ ТОЛЬКО НАЖАТИЕМ. «Цены», «Сравнение» и «Связи» ходят к магазинам,
и опрос, случайно оказавшийся в открытии страницы, выглядит просто как «приложение
задумалось»: минута на каждое обновление, шесть сетей на каждую кнопку «назад». Поэтому
матчер и сравнение здесь подменяются счётчиками вызовов: тест видит не «страница
открылась», а «страница никого не спросила».

Общий запрет живых запросов стоит в tests/conftest.py и ловит любую настоящую сеть.
Здесь запрет уточняется до «не спросила даже через кэш».
"""
from __future__ import annotations

import io
import os
import sys
from urllib.parse import urlencode

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402

PHONE = "79990000077"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временной базе — настоящие рабочие места трогать нельзя.

    config.db_path не подменяется нарочно, как и в tests/test_web.py: подмена вернула
    бы одну базу всем и отключила выбор базы по человеку, а он тут работает.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    client.post("/login", data={"phone": PHONE, "next": "/"})
    yield client
    users.deactivate()


def workspace():
    """Открыть базу этого человека вне запроса — чтобы завести в ней данные."""
    users.open_workspace(PHONE)


def add(name: str, category: str | None = None, unit: str = "pcs", weight_g=None) -> int:
    from app.models import Product

    workspace()
    try:
        return repo.upsert_product(Product(id=None, name=name, category=category,
                                           unit=unit, weight_g=weight_g))
    finally:
        users.deactivate()


def link(product_id: int, store_code: str, sku: str, raw_name: str, price: float | None = None):
    """Подтверждённая связь и, если надо, снимок цены — как их оставил бы матчер."""
    workspace()
    try:
        store = repo.get_store(store_code)
        sp_id = repo.upsert_store_product(store.id, sku, raw_name)
        repo.confirm_mapping(product_id, sp_id, confirmed=True)
        if price is not None:
            repo.save_price(sp_id, price)
        return sp_id
    finally:
        users.deactivate()


def text(answer) -> str:
    return answer.data.decode("utf-8")


def stub(monkeypatch, name: str, replacement) -> None:
    """Подменить функцию матчера ОБОИМИ именами, которыми её зовут.

    Ловушка, на которую тест уже попался: app/matcher/__init__.py делает
    `from app.matcher.matcher import refresh_prices`, поэтому у функции два
    независимых имени. Подменить только внутреннее — значит подменить не то, что
    зовёт экран, и тест молча уйдёт в настоящую сеть.
    """
    from app import matcher as package
    from app.matcher import matcher as module

    monkeypatch.setattr(module, name, replacement)
    monkeypatch.setattr(package, name, replacement)


# ---------- экраны открылись и перестали быть заглушками ----------
@pytest.mark.parametrize("path", ["/products", "/prices", "/compare", "/links"])
def test_the_screen_moved(web, path):
    answer = web.get(path)
    assert answer.status_code == 200
    assert "не переехал" not in text(answer), f"{path} всё ещё отвечает заглушкой"


@pytest.mark.parametrize("path", ["/products", "/prices", "/compare", "/links"])
def test_no_wide_tables(web, path):
    """Длинных таблиц на этих экранах нет: они и уводили страницу вбок на телефоне.

    Проверка структурная нарочно. «Посмотреть на телефоне» тест не умеет, а вот
    вернувшийся <table> с датой, магазином, ценой и наличием в четыре колонки —
    это ровно тот случай, когда страница перестаёт помещаться в ширину экрана.
    """
    assert "<table" not in text(web.get(path)).lower()


# ---------- товары ----------
def test_products_shows_goods_by_category(web):
    add("Творог 5% 200 г", category="Молочное")
    add("Хлеб бородинский", category="Хлеб")
    page = text(web.get("/products"))
    assert "Творог 5% 200 г" in page and "Хлеб бородинский" in page
    assert "Молочное" in page and "Хлеб" in page


def test_search_lives_in_the_address(web):
    """Отфильтрованный список — это адрес, а не память экрана.

    Ровно за этим затевался переезд: на найденное можно дать ссылку, вернуться
    «назад» и открыть две вкладки рядом, не мешая себе же.
    """
    add("Творог 5% 200 г")
    add("Хлеб бородинский")

    found = text(web.get("/products?q=творог"))
    assert "Творог 5% 200 г" in found and "Хлеб бородинский" not in found

    # Тот же адрес у того же человека — тот же ответ. Если фильтр протёк в сессию,
    # второй запрос без q продолжил бы показывать отфильтрованное.
    assert "Хлеб бородинский" in text(web.get("/products"))
    assert "Хлеб бородинский" not in text(web.get("/products?q=творог"))


def test_a_new_product_is_saved_and_the_search_survives(web):
    answer = web.post("/products?q=творог", data={
        "name": "Творог 9% 200 г", "brand": "Простоквашино", "category": "Молочное",
        "weight_g": "200", "unit": "pcs", "barcode": "46 0700-1", "active": "on"})
    assert answer.status_code == 302
    # Поиск переживает запись: человека возвращают туда, откуда он её начал. В адресе
    # он закодирован процентами — иначе поиск вида «чай&edit=new» дописал бы в адрес
    # чужой параметр.
    assert urlencode({"q": "творог"}) in answer.headers["Location"]

    workspace()
    try:
        saved = [p for p in repo.list_products(active_only=False) if p.name == "Творог 9% 200 г"]
    finally:
        users.deactivate()
    assert saved, "товар не записался"
    # Штрихкод из чека приходит с пробелами и дефисами, а искать по нему будут как по числу.
    assert saved[0].barcode == "4607001"


def test_a_product_without_a_name_is_refused_with_words(web):
    answer = web.post("/products", data={"name": "   ", "unit": "pcs"})
    assert answer.status_code == 200          # не перевод: человеку есть что исправить
    assert "Напишите название" in text(answer)


def test_the_cheapest_price_names_its_chain(web):
    """У цены стоит цвет сети, которая её даёт, — это и есть «Цвет магазина»."""
    pid = add("Творог 5% 200 г")
    link(pid, "lenta", "L-1", "Творог Лента 5% 200 г", price=89.9)
    link(pid, "magnit", "M-1", "Творог Магнит 5% 200 г", price=99.9)
    page = text(web.get("/products"))
    assert "dot s-lenta" in page, "дешёвая цена не помечена цветом своей сети"
    assert "89,90" in page


def test_products_asks_nobody(web, monkeypatch):
    """Каталог открывается по базе. Один поход в сеть здесь — минута на строку списка."""
    calls = _count_network(monkeypatch)
    add("Творог 5% 200 г")
    web.get("/products")
    web.get("/products?q=творог")
    assert calls == {}, f"экран «Товары» сходил в сеть: {calls}"


# ---------- цены ----------
def test_prices_picks_the_product_from_the_address(web):
    first = add("Творог 5% 200 г")
    second = add("Хлеб бородинский")
    link(second, "lenta", "L-9", "Хлеб Лента бородинский", price=54.0)

    page = text(web.get(f"/prices?product={second}"))
    assert "Хлеб Лента бородинский" in page or "54,00" in page
    assert f'value="{first}"' in page          # второй товар остаётся в выборе


def test_prices_does_not_ask_the_shops_on_open(web, monkeypatch):
    """Открытие «Цен» не должно спрашивать магазины: это минута на каждое обновление."""
    calls = _count_network(monkeypatch)
    pid = add("Творог 5% 200 г")
    link(pid, "lenta", "L-1", "Творог Лента 5% 200 г", price=89.9)
    web.get("/prices")
    web.get(f"/prices?product={pid}")
    assert calls == {}, f"«Цены» сходили в сеть на открытии: {calls}"


def test_prices_asks_the_shops_only_on_a_press(web, monkeypatch):
    calls = _count_network(monkeypatch)
    add("Творог 5% 200 г")
    answer = web.post("/prices", data={"action": "refresh"})
    assert answer.status_code == 302, "после действия должен идти перевод, иначе обновление повторит его"
    assert calls.get("refresh_prices") == 1


def test_a_pricelist_is_loaded_shown_and_dropped(web, tmp_path, monkeypatch):
    """Прайс вручную — единственный источник цен для сетей с закрытым каталогом.

    Товар заводится не для красоты: блок прайсов, как и на прежнем экране, показан
    только когда список товаров не пуст — «Цены» целиком про товары человека.

    pricelist.DIR подменяется отдельно от config.ROOT нарочно: он посчитан на импорте
    модуля, поэтому сдвинутый корень до него не доходит и тест писал бы прайс в
    настоящую папку data/ репозитория.
    """
    from app import pricelist

    monkeypatch.setattr(pricelist, "DIR", str(tmp_path / "прайсы"))
    add("Молоко 1 л")
    csv = "Название;Цена;Единица\nМолоко 1 л;79,90;шт\nЯблоки;149;кг\n"
    answer = web.post("/prices", data={
        "action": "pricelist_save", "store": "pyaterochka",
        "file": (io.BytesIO(csv.encode("utf-8")), "price.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    page = text(answer)
    assert "Загружено 2 позиций" in page

    assert "Молоко 1 л" in text(web.get("/prices?pl=pyaterochka"))
    assert len(pricelist.load("pyaterochka")) == 2

    web.post("/prices", data={"action": "pricelist_drop", "store": "pyaterochka"})
    assert pricelist.load("pyaterochka") == []


def test_a_made_up_chain_cannot_reach_the_file_system(web, tmp_path, monkeypatch):
    """Код сети уходит в имя файла прайса — значит из формы его брать нельзя.

    Без проверки по справочнику присланное «../../что-нибудь» писало бы файл мимо
    папки прайсов, а «Удалить прайс» — удаляло бы чужой. Снаружи это выглядит как
    обычная форма загрузки, и заметить подмену было бы некому.
    """
    from app import pricelist

    home = tmp_path / "прайсы"
    monkeypatch.setattr(pricelist, "DIR", str(home))
    csv = "Название;Цена\nМолоко;79,90\n"
    answer = web.post("/prices", data={
        "action": "pricelist_save", "store": "../../../побег",
        "file": (io.BytesIO(csv.encode("utf-8")), "price.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    assert "Такой сети нет" in text(answer)
    assert not list(tmp_path.rglob("*побег*")), "файл уехал мимо папки прайсов"
    assert not home.exists(), "отказанная загрузка не должна ничего создавать"


def test_a_second_snapshot_draws_the_line(web):
    """Один снимок — это не график, и врать прямой линией нельзя."""
    pid = add("Творог 5% 200 г")
    sp_id = link(pid, "lenta", "L-1", "Творог Лента 5% 200 г", price=89.9)

    assert "Пока это один снимок" in text(web.get(f"/prices?product={pid}"))

    workspace()
    try:
        repo.save_price(sp_id, 94.9, fetched_at="2026-09-18T10:00:00")
    finally:
        users.deactivate()
    page = text(web.get(f"/prices?product={pid}"))
    assert "<polyline" in page, "второй снимок есть, а линии нет"
    assert "Пока это один снимок" not in page


def test_three_chains_with_one_snapshot_each_are_not_a_chart(web):
    """Три сети по одному снимку — это три точки и ни одной линии.

    Ловушка, на которую экран уже попался: «точек хотя бы две» считалось по всем сетям
    сразу, и график рисовался пустой рамкой с тремя кружками. Выглядит как сломанный
    график, а на деле рисовать просто нечего — цена ещё ни у кого не менялась.
    """
    pid = add("Творог 5% 200 г")
    link(pid, "lenta", "L-1", "Творог Лента 5% 200 г", price=89.9)
    link(pid, "magnit", "M-1", "Творог Магнит 5% 200 г", price=99.9)
    link(pid, "vkusvill", "V-1", "Творог ВкусВилл 5% 200 г", price=109.9)

    page = text(web.get(f"/prices?product={pid}"))
    assert "<polyline" not in page, "линия нарисована там, где менять цену было нечему"
    assert "Пока это один снимок" in page
    # Сами цены при этом показаны — сравнить сети можно и без графика.
    assert "89,90" in page and "109,90" in page


# ---------- сравнение ----------
def test_compare_asks_nobody_until_asked(web, monkeypatch):
    calls = _count_network(monkeypatch)
    page = text(web.get("/compare"))
    assert calls == {}, f"пустое «Сравнение» сходило в сеть: {calls}"
    assert "Введите название товара" in page


def test_compare_runs_on_the_address(web, monkeypatch):
    """Запрос живёт в адресе: ссылкой на сравнение можно поделиться."""
    calls = _count_network(monkeypatch)
    page = text(web.get("/compare?q=молоко"))
    assert calls.get("compare_query") == 1
    assert "Лента" in page and "молоко" in page.lower()
    # Приведённая цена — главное число: 930 мл за 149 ₽ дороже литра за 155 ₽.
    assert "за кг/л" in page


def test_compare_survives_a_shop_that_fell(web, monkeypatch):
    from app import compare as compare_module

    def boom(*_a, **_k):
        raise RuntimeError("магазин не ответил")

    monkeypatch.setattr(compare_module, "compare_query", boom)
    answer = web.get("/compare?q=молоко")
    assert answer.status_code == 200, "упавший магазин не должен ронять страницу"
    assert "Сравнение не получилось" in text(answer)


# ---------- связи ----------
def test_links_shows_the_queue(web):
    """Очередь — главное приобретение экрана: видно, сколько работы осталось."""
    pid = add("Творог 5% 200 г")
    link(pid, "lenta", "L-1", "Творог Лента 5% 200 г")
    page = text(web.get("/links"))
    assert "Творог 5% 200 г" in page
    # Сетей семь с 19.09.2026: добавилась METRO.
    assert "1 из 8" in page, "очередь не показывает, сколько сетей опознано"


def test_links_names_what_is_confirmed(web):
    """Подтверждённое сопоставление должно называть себя целиком, а не галочкой."""
    pid = add("Творог 5% 200 г")
    link(pid, "lenta", "L-1", "Творог Лента 5% 200 г", price=89.9)
    page = text(web.get(f"/links?product={pid}&store=lenta"))
    assert "Творог Лента 5% 200 г" in page and "L-1" in page
    assert "89,90" in page


def test_dropping_a_link_explains_what_it_costs(web):
    """Снятая связь тихо выкидывает товар из расчёта — об этом надо сказать заранее."""
    pid = add("Творог 5% 200 г")
    link(pid, "lenta", "L-1", "Творог Лента 5% 200 г")
    page = text(web.get(f"/links?product={pid}&store=lenta"))
    assert "перестанет участвовать в расчёте" in page

    answer = web.post("/links", data={"action": "drop", "product": str(pid), "store": "lenta"})
    assert answer.status_code == 302
    workspace()
    try:
        store = repo.get_store("lenta")
        assert repo.confirmed_mapping(pid, store.id) is None
    finally:
        users.deactivate()


def test_candidates_are_found_by_a_press_and_confirmed_by_name(web, monkeypatch):
    """Кандидатов ищут нажатием, а подтверждают по имени, а не по номеру строки.

    Самая дорогая ошибка подбора выглядела так: «Страчателла 200 г» сопоставилась с
    мороженым «страчателла 92 г». Кнопка, на которой написано, ЧТО подтверждается,
    единственное, что стоит между этой ошибкой и корзиной человека.
    """
    from app.models import Candidate

    pid = add("Страчателла 200 г", weight_g=200)
    found = [Candidate(store_code="lenta", sku="L-77", name="Мороженое страчателла 92 г",
                       price=79.0, weight_g=92, score=0.81)]
    stub(monkeypatch, "find_candidates", lambda *a, **k: found)

    assert web.post("/links", data={"action": "find", "product": str(pid),
                                    "store": "lenta"}).status_code == 302
    page = text(web.get(f"/links?product={pid}&store=lenta"))
    assert "Мороженое страчателла 92 г" in page and "L-77" in page
    # Матчер возражает по весу — и сказать об этом надо ДО подтверждения, а не после.
    assert "Матчер возражает" in page

    confirmed = {}
    stub(monkeypatch, "confirm", lambda p, s, sku: confirmed.update(product=p, store=s, sku=sku))
    answer = web.post("/links", data={"action": "confirm", "product": str(pid),
                                      "store": "lenta", "sku": "L-77"})
    assert answer.status_code == 302
    assert confirmed == {"product": pid, "store": "lenta", "sku": "L-77"}


def test_links_asks_nobody_on_open(web, monkeypatch):
    calls = _count_network(monkeypatch)
    pid = add("Творог 5% 200 г")
    web.get("/links")
    web.get(f"/links?product={pid}")
    web.get(f"/links?product={pid}&store=lenta")
    assert calls == {}, f"«Связи» сходили в сеть на открытии: {calls}"


# ---------- кто ходит в сеть ----------
def _count_network(monkeypatch) -> dict:
    """Подменить всё, что спрашивает магазины, счётчиком вызовов.

    Подменяется не сокет, а сами входы в сеть: сокет ловит только живой запрос, а
    ответ из кэша коннектора — это тоже поход в магазин с точки зрения экрана, просто
    удачный. Нам важно, что экран СПРОСИЛ, а не что запрос долетел.
    """
    calls: dict[str, int] = {}

    def count(name, result):
        def fake(*_a, **_k):
            calls[name] = calls.get(name, 0) + 1
            return result
        return fake

    from app import compare as compare_module
    from app.catalog import refresh as catalog_refresh

    monkeypatch.setattr(catalog_refresh, "link_products", count("link_products", {"tried": 0, "linked": 0}))
    stub(monkeypatch, "refresh_prices", count("refresh_prices", {"updated": 0}))
    stub(monkeypatch, "auto_match", count("auto_match", {"auto": 0, "need_review": []}))
    stub(monkeypatch, "find_candidates", count("find_candidates", []))

    lenta = compare_module.StoreOffer(
        store_code="lenta", store_name="Лента", sku="L-1", name="Молоко Лента 1 л",
        price=99.0, unit="pcs", weight_g=1000, per_unit=99.0,
        per_unit_label=compare_module.PER_KG)
    monkeypatch.setattr(compare_module, "compare_query", count("compare_query", [lenta]))
    return calls
