"""METRO: коннектор цен и сборщик каталога. Сеть не трогаем — подменяем HTTP.

Сторожа стоят на том, что ломается тихо и дорого: на сегменте токена в адресе,
на разнице между ценой витрины и ценой полки, на остатке и на штрихкоде. Каждая
из этих четырёх вещей, сломавшись, выглядит как обычная работа: цена придёт, но
не та, наличие окажется выдуманным, а сшивка с чеком развалится молча.
"""
from __future__ import annotations

import pytest

from app import config
from app.catalog.crawlers.metro import MetroCrawler
from app.connectors import cache, metro
from app.connectors.metro import MetroConnector
from app.models import Location


@pytest.fixture(autouse=True)
def clean_state():
    cache.cache_clear()
    cache.reset_throttle()
    yield
    cache.cache_clear()
    cache.reset_throttle()


def card(article: int = 117189, price: float = 70.91, offline: float = 70.91,
         stock: float | None = 12176.0, promo: bool = False) -> dict:
    """Карточка ровно того вида, какой пришёл с боевого сервера 19.09.2026."""
    return {
        "id": 597274, "article": article, "mgb_article": 105414,
        "name": "aro Сахар-песок ТС2 белый кристаллический, 1кг",
        "slug": "aro-sahar-pesok", "category_id": 414583,
        "barcodes": ["4610109184844", "4610020700215"],
        "manufacturer": {"id": 8553, "name": "ARO"},
        "prices": {"price": price, "source": "movie",
                   "offline": {"price": offline}, "is_promo": promo},
        "stock": None if stock is None else {"value": stock, "scale": 100, "text": "Товара много"},
    }


def answer(rows: list[dict], **page) -> dict:
    return {"success": True, "errors": [], "data": {"data": rows, **page}}


# ---------- адрес запроса: где живёт честная дорога ----------
def test_token_goes_into_the_path_when_it_is_configured(monkeypatch):
    """Именной токен — ПРАВИЛЬНАЯ дорога, и она должна включаться одной настройкой.

    Спецификация METRO требует токен сегментом адреса. Пока его нет, мы ходим без
    него, и это запасной путь. Если настройка перестанет действовать, переход на
    законный доступ окажется незаметно сломан — а заметить это можно будет только
    тогда, когда сеть закроет запасную дорогу.
    """
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        "СЕКРЕТ" if key == "connectors.metro_api_token" else default)
    assert metro._url("15/products") == "https://api.metro-cc.ru/api/v1/СЕКРЕТ/15/products"


def test_without_a_token_the_path_has_no_empty_segment(monkeypatch):
    """Пустая настройка не должна давать двойной слэш: это другой адрес, и он 404."""
    monkeypatch.setattr(config, "get", lambda key, default=None: None)
    assert metro._url("15/products") == "https://api.metro-cc.ru/api/v1/15/products"
    assert "//15" not in metro._url("15/products").replace("https://", "")


# ---------- разбор карточки ----------
def test_shelf_price_is_kept_apart_from_the_counter_price():
    """Цена полки и цена витрины — разные числа, и смешивать их нельзя.

    METRO единственная называет обе сразу. Именно на этой паре строится обещание
    считать честно: у Ленты та же разница доходит до четырнадцати процентов.
    """
    price, offline, promo = metro._price(card(price=88.0, offline=70.91, promo=True))
    assert (price, offline, promo) == (88.0, 70.91, True)


def test_stock_number_becomes_availability():
    assert metro._stock(card(stock=12176.0)) == (True, 12176.0)
    assert metro._stock(card(stock=0.0)) == (False, 0.0)
    # Остатка нет вовсе — это «не сказали», а не «нет в наличии».
    assert metro._stock(card(stock=None)) == (True, None)


def test_barcode_is_taken_and_only_a_digital_one():
    assert metro._barcode(card()) == "4610109184844"
    assert metro._barcode({"barcodes": ["не-число", "4610020700215"]}) == "4610020700215"
    assert metro._barcode({"barcodes": []}) is None


# ---------- цены ----------
def test_prices_are_asked_by_articles_in_one_batch(monkeypatch):
    """Артикулы уходят массивом, а не по одному походу на штуку.

    Пачка вдесятеро дешевле, и на корзине в полсотни позиций это разница между
    одним запросом и полусотней.
    """
    seen: list[list[tuple]] = []

    def fake_get(tail, params=None, timeout=45.0):
        seen.append(list(params or []))
        return answer([card(article=117189), card(article=222222, price=10.0)])

    monkeypatch.setattr(metro, "_get", fake_get)
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        "15" if key == "connectors.metro_store_id" else default)

    snapshots = MetroConnector().get_prices(["117189", "222222"])

    assert len(seen) == 1, "артикулы ушли не одной пачкой"
    assert seen[0] == [("articles[]", "117189"), ("articles[]", "222222")]
    assert {s.sku: s.price for s in snapshots} == {"117189": 70.91, "222222": 10.0}
    assert all(s.store_code == "metro" for s in snapshots)


def test_a_product_out_of_stock_is_marked_so(monkeypatch):
    monkeypatch.setattr(metro, "_get", lambda *a, **k: answer([card(stock=0.0)]))
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        "15" if key == "connectors.metro_store_id" else default)
    assert MetroConnector().get_prices(["117189"])[0].in_stock is False


def test_without_a_point_prices_are_not_invented(monkeypatch):
    """Ни адреса, ни запасного кода — честный пустой ответ, а не цена чужого города."""
    monkeypatch.setattr(config, "get", lambda key, default=None: None)
    called = []
    monkeypatch.setattr(metro, "_get", lambda *a, **k: called.append(1) or answer([]))
    assert MetroConnector()._get_prices(["117189"]) == []
    assert not called, "без точки коннектор всё-таки сходил в сеть"


def test_shelf_prices_return_the_offline_number(monkeypatch):
    monkeypatch.setattr(metro, "_get", lambda *a, **k: answer([card(price=88.0, offline=70.91)]))
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        "15" if key == "connectors.metro_store_id" else default)
    assert MetroConnector().shelf_prices(["117189"]) == {"117189": 70.91}


# ---------- подбор точки ----------
def test_coordinates_live_in_a_nested_object():
    """Координаты у METRO лежат ВЛОЖЕННЫМ объектом, и на этом уже один раз обожглись.

    19.09.2026 подбор ближайшего центра молча отвечал «не нашёл» при полном списке
    из 91 центра: искали latitude на верхнем уровне, а сеть кладёт их в
    `coordinates`. Форма ответа взята с боевого сервера дословно.
    """
    real = {"id": 35, "store_id": 15, "city": "Санкт-Петербург",
            "coordinates": {"latitude": 60.002202, "longitude": 30.26868}}
    assert metro._center_point(real) == (60.002202, 30.26868)
    # Верхний уровень остаётся запасным путём на случай смены формы ответа.
    assert metro._center_point({"latitude": 1.0, "longitude": 2.0}) == (1.0, 2.0)
    assert metro._center_point({"city": "без координат"}) is None


def test_nearest_centre_is_the_nearest_one(monkeypatch):
    """Ближайший центр выбирается по расстоянию, а не по порядку в ответе сети."""
    centres = [
        {"store_id": 20, "address": "Пулковское шоссе, 23",
         "coordinates": {"latitude": 59.80, "longitude": 30.32}},
        {"store_id": 15, "address": "Комендантский пр-т, 3",
         "coordinates": {"latitude": 60.01, "longitude": 30.25}},
    ]
    monkeypatch.setattr(metro, "tradecenters", lambda: centres)
    import app.geo as geo
    monkeypatch.setattr(geo, "coords", lambda addr: (60.00, 30.26))

    found = metro.nearest_store("Санкт-Петербург, Комендантский проспект, 5")
    assert found["code"] == "15"
    assert "Комендантский" in found["address"]


def test_an_address_that_did_not_resolve_gives_no_point(monkeypatch):
    monkeypatch.setattr(metro, "tradecenters", lambda: [{"store_id": 15, "latitude": 60.0,
                                                         "longitude": 30.2}])
    import app.geo as geo
    monkeypatch.setattr(geo, "coords", lambda addr: None)
    assert metro.nearest_store("белиберда") is None


def test_a_centre_hundreds_of_km_away_is_not_yours(monkeypatch):
    """В городе без METRO ближайший центр — за сотни километров, и его цены не наши."""
    monkeypatch.setattr(metro, "tradecenters", lambda: [
        {"store_id": 15, "coordinates": {"latitude": 60.01, "longitude": 30.25}}])
    import app.geo as geo
    monkeypatch.setattr(geo, "coords", lambda addr: (56.83, 60.61))     # Екатеринбург
    assert metro.nearest_store("Екатеринбург, Малышева 51") is None


def test_known_address_without_a_centre_does_not_take_the_spare_one(monkeypatch):
    """Адрес известен, центра рядом нет — это «METRO здесь нет», а не запасной Петербург."""
    from app.models import Location

    monkeypatch.setattr(metro, "nearest_store", lambda address: None)
    monkeypatch.setattr(metro.config, "get",
                        lambda key, default=None: "15" if key == "connectors.metro_store_id" else default)
    assert metro._store_id(Location(address="Тикси, Морская 1")) is None
    assert metro._store_id(Location()) == "15", "адреса нет вовсе — запасной центр, как раньше"


def test_metro_follows_the_persons_address(monkeypatch):
    """Живые цены и корзина METRO идут по адресу человека, как и ночной обход.

    Раньше место METRO не отдавалось вовсе: живые цены и корзина брали пустой
    connectors.metro_store_id и отвечали «не задана точка».
    """
    from app import location
    from app.models import Location

    monkeypatch.setattr(location, "address", lambda: "Санкт-Петербург, Комендантский 5")
    assert location.for_store("metro") == Location(address="Санкт-Петербург, Комендантский 5")


def test_metro_is_a_chain_with_points():
    """Цена и остаток у METRO свои по точкам — значит она обязана быть в BY_POINT.

    Выпади она оттуда, приложение молча показало бы цены запасного центра всем
    людям, в каком бы городе они ни жили.
    """
    from app import places
    assert "metro" in places.BY_POINT
    assert "metro" in places._RESOLVERS


# ---------- сборщик каталога ----------
def test_crawler_reads_the_card_whole():
    product = MetroCrawler(["15"])._product(card())
    assert product.sku == "117189"
    assert product.barcode == "4610109184844"
    assert product.brand == "ARO"
    assert product.price == 70.91
    assert product.in_stock is True
    assert product.url.endswith("/products/aro-sahar-pesok")


def test_crawler_walks_every_page_and_stops_at_the_last(monkeypatch):
    """Обход идёт до последней страницы и останавливается ровно на ней.

    Не остановится — будет крутить последнюю страницу вечно; остановится рано —
    каталог окажется обрезанным, и это не видно никак, кроме числа строк.
    """
    pages = {1: answer([card(article=1)], last_page=3, total=3),
             2: answer([card(article=2)], last_page=3, total=3),
             3: answer([card(article=3)], last_page=3, total=3)}
    asked: list[int] = []

    class Reply:
        def __init__(self, payload): self._payload = payload
        headers = {"Content-Type": "application/json"}
        def json(self): return self._payload

    def fake_http_get(url, pace=None, **kwargs):
        page = int((kwargs.get("params") or {}).get("page", 1))
        asked.append(page)
        return Reply(pages[page])

    import app.catalog.crawlers.metro as mod
    monkeypatch.setattr(mod, "http_get", fake_http_get)

    got = list(MetroCrawler(["15"]).crawl())
    assert asked == [1, 2, 3]
    assert [p.sku for p in got] == ["1", "2", "3"]


def test_crawler_without_points_says_so_instead_of_guessing(monkeypatch):
    """Ни одной точки — это отказ с объяснением, а не тихий пустой каталог."""
    from app.catalog.model import CrawlBlocked
    monkeypatch.setattr(config, "get", lambda key, default=None: None)
    with pytest.raises(CrawlBlocked):
        list(MetroCrawler([]).crawl())


def test_crawler_is_registered_and_takes_every_point():
    """METRO должна собираться по КАЖДОЙ точке, как Магнит: остаток у них разный."""
    from app.catalog import crawlers
    from app.catalog.model import Crawler

    assert "metro" in crawlers.registry()

    class P:
        def __init__(self, code): self.code = code

    made = crawlers.make("metro", [P("15"), P("16")])
    assert isinstance(made, Crawler)
    assert made.stores == ["15", "16"]
