"""Адрес клиента: хранение, разрешение в точку и то, как он доезжает до коннектора.

Главное, что здесь защищается, — правило «сменил адрес, забудь точку». Точка,
найденная по старому адресу, к новому отношения не имеет, а на вид цены из неё
неотличимы от настоящих: это ровно тот сорт ошибки, который не находится глазами.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, location, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Location  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "loc.db"))
    init_db()


# ---------- хранение ----------
def test_address_survives_restart(db):
    """Адрес — ответ пользователя, а не настройка запуска: он обязан лежать в базе."""
    location.save_address("Москва, Ходынский бульвар 4")
    assert location.address() == "Москва, Ходынский бульвар 4"
    assert location.is_set()


def test_empty_address_is_the_same_as_no_address(db):
    location.save_address("Москва")
    location.save_address("   ")
    assert location.address() is None
    assert not location.is_set()


def test_without_address_store_gets_nothing_and_falls_back(db):
    """Нет адреса — None, чтобы коннектор взял запасное значение из config.yaml."""
    assert location.for_store("lenta") is None


# ---------- чем спрашиваем ----------
def _lenta_mcp(monkeypatch, hub=203, calls=None):
    """Подменяет MCP Ленты: resolve_store называет хаб доставки, остальное молчит.

    hub=None — сервер адрес не разобрал (или лежит): так проверяется запасной путь.
    calls — сюда складываются (инструмент, аргументы, ключ кэша) каждого вызова.
    """
    from app.connectors import mcp_client

    def call(url, store_code, tool, arguments, cache_key=None):
        if calls is not None:
            calls.append((tool, arguments, cache_key))
        if tool == "storefront_resolve_store" and hub is not None:
            return {"ok": True, "data": {
                "suggested": {"delivery": {"id": 261, "aliasId": hub, "name": f"ТК{hub}", "shopType": "HM"},
                              "pickup": {"id": 445, "aliasId": 907, "name": "ТК907", "shopType": "SM"}},
                "hubs": [{"id": 1278, "aliasId": 202138, "name": "ТК202138", "shopType": "MM", "distance": 394},
                         {"id": 261, "aliasId": hub, "name": f"ТК{hub}", "shopType": "HM", "distance": 5331}],
            }}
        return None

    monkeypatch.setattr(mcp_client, "call_tool", call)


def test_address_resolves_once_into_delivery_hub(db, monkeypatch):
    """Спрашиваем кодом хаба доставки, который Лента сама назвала для адреса.

    Проверено 16.09.2026: suggested.delivery.aliasId из storefront_resolve_store
    (203 для Щербакова 4 в Екатеринбурге) витрина принимает как storeId и отвечает
    тем же, что по адресу. Ответ resolve_store уходит в кэш под ключом с адресом —
    поэтому «один раз»: следующий запрос по тому же адресу в сеть не ходит.
    """
    from app.connectors.lenta import _where

    calls: list = []
    _lenta_mcp(monkeypatch, hub=203, calls=calls)
    location.save_address("Екатеринбург, улица Щербакова 4")

    where = _where(location.for_store("lenta"))

    assert where == {"storeId": 203, "channel": "lo"}
    assert [c[0] for c in calls] == ["storefront_resolve_store"]
    assert calls[0][2] == "stores:Екатеринбург, улица Щербакова 4", "без ключа кэша адрес разрешался бы каждый раз"


def test_address_beats_store_code(monkeypatch):
    """Есть и адрес, и код — уходит хаб, в который разрешился адрес, а не код.

    Код в Location может оказаться чем угодно: до 16.09 сюда попадал id ближайшего
    физического магазина (1278 — ТК202138), по которому витрина отвечает пустотой,
    а чужой aliasId отвечает чужими ценами. Ошибка тихая: неверный код не падает.
    Адрес же разрешает сама Лента, поэтому при наличии адреса верим ему.
    """
    from app.connectors.lenta import _where

    _lenta_mcp(monkeypatch, hub=291)

    where = _where(Location(address="Москва, Ходынский бульвар 4", store_id="1278"))

    assert where == {"storeId": 291, "channel": "lo"}


def test_unresolved_address_is_sent_as_it_is(monkeypatch):
    """Хаб не добыт (сервер молчит, город не обслуживается) — уходит сам адрес.

    Витрина принимает и адрес и подбирает хаб сама; терять цены из-за одного
    неотвеченного вызова незачем.
    """
    from app.connectors.lenta import _where

    _lenta_mcp(monkeypatch, hub=None)

    where = _where(Location(address="Москва, Ходынский бульвар 4"))

    assert where == {"address": "Москва, Ходынский бульвар 4", "channel": "lo"}


def test_store_code_alone_goes_without_the_network(monkeypatch):
    """Код без адреса — уже разрешённое место (lenta_store_id в config.yaml): в сеть не ходим."""
    from app.connectors.lenta import _where

    calls: list = []
    _lenta_mcp(monkeypatch, hub=291, calls=calls)

    assert _where(Location(store_id="291")) == {"storeId": 291, "channel": "lo"}
    assert calls == []


def test_nearest_stores_and_hub_share_one_answer(monkeypatch):
    """Проверка адреса на экране и код для цен читают один и тот же ответ resolve_store."""
    from app.connectors.lenta import delivery_hub, nearest_stores

    calls: list = []
    _lenta_mcp(monkeypatch, hub=203, calls=calls)

    hubs = nearest_stores("Екатеринбург, улица Щербакова 4")
    hub = delivery_hub("Екатеринбург, улица Щербакова 4")

    assert [h["aliasId"] for h in hubs] == [202138, 203]
    assert hub == 203
    assert {c[2] for c in calls} == {"stores:Екатеринбург, улица Щербакова 4"}, "ключ кэша один — ответ один"


# ---------- магазин Магнита по адресу ----------
def _magnit_stores(monkeypatch, stores, point=(56.83, 60.61), calls=None):
    """Подменяет обе ступени Магнита: адрес → точка и точка → справочник магазинов.

    point=None — адрес в точку не перевёлся (опечатка, город не найден).
    """
    from app import geo
    from app.connectors import magnit

    def fake_coords(address):
        if calls is not None:
            calls.append(("coords", address))
        return point

    def fake_stores(lat, lon, radius_km=magnit.SEARCH_RADIUS_KM, limit=20):
        if calls is not None:
            calls.append(("stores", round(lat, 2), round(lon, 2)))
        return list(stores)

    monkeypatch.setattr(geo, "coords", fake_coords)
    monkeypatch.setattr(magnit, "stores_near", fake_stores)


MM = {"code": "668596", "format": "MM", "address": "Екатеринбург, Мамина-Сибиряка, 70",
      "distance": 578.0, "delivery": False}
FAR = {"code": "567691", "format": "MM", "address": "Екатеринбург, Малышева, 114",
       "distance": 965.0, "delivery": False}


def test_magnit_gets_the_shop_nearest_to_the_address(db, monkeypatch):
    """Код магазина — ответ на адрес, а не настройка установки.

    Пока код лежал в config.yaml, он был один на всех: человек из Москвы видел цены
    Краснодара и заметить этого не мог — чужая цена выглядит ровно как своя.
    """
    _magnit_stores(monkeypatch, [MM, FAR])
    location.save_address("Екатеринбург, улица Малышева 51")

    place = location.for_store("magnit")

    assert isinstance(place, Location)
    assert place.store_id == "668596"
    assert place.shop_type == "MM"
    assert place.address is None, "Магнит адресов не понимает — в место кладём только код"


def test_magnit_without_a_shop_nearby_says_so_instead_of_staying_silent(db, monkeypatch):
    """Рядом магазинов нет — это ОТВЕТ, и он обязан отличаться от «адрес не задан».

    Смолчать было бы тихой ошибкой: коннектор увидел бы пустое место, взял бы
    запасной магазин из config.yaml — краснодарский — и человек в городе без
    Магнита получил бы краснодарские цены с пометкой «живые».
    """
    _magnit_stores(monkeypatch, [])
    location.save_address("Тикси, улица Морская 1")

    place = location.for_store("magnit")

    assert isinstance(place, Location)
    assert place.store_id is None
    assert place.address == "Тикси, улица Морская 1", "адрес и есть отличка от «адреса нет»"


def test_magnit_in_a_city_without_shops_returns_no_prices(db, monkeypatch):
    """Цена чужого города не должна дойти до расчёта — ни живая, ни справочная."""
    from app.connectors import get_connector

    _magnit_stores(monkeypatch, [])
    location.save_address("Тикси, улица Морская 1")
    conn = get_connector("magnit", location.for_store("magnit"))

    assert conn.search("молоко") == [], "выдумывать нечего: сети здесь нет"
    snaps = conn.get_prices(["1899800733"])
    assert all(not s.in_stock for s in snaps), "всё, что отдаём, помечено отсутствующим"


def test_magnit_without_an_address_still_uses_the_configured_shop(db, monkeypatch):
    """Установка «для себя» и разработка работают как раньше: адреса нет — берём config."""
    from app.connectors.base import configured_location
    from app.connectors.magnit import DEFAULT_STORE, MagnitConnector

    assert location.for_store("magnit") is None, "адреса нет — коннектор решает сам"
    conn = MagnitConnector(location=configured_location("magnit"))
    assert conn._store_code() == DEFAULT_STORE


def test_magnit_unparsed_address_does_not_reach_the_directory(db, monkeypatch):
    """Адрес не перевёлся в точку — в справочник магазинов не идём вовсе.

    И цен не показываем: непонятый адрес — это «не знаю, где человек», а не
    разрешение подставить чужой город. Исправит адрес — появятся и цены.
    """
    calls: list = []
    _magnit_stores(monkeypatch, [MM], point=None, calls=calls)
    location.save_address("кувырк")

    place = location.for_store("magnit")

    assert place.store_id is None, "точки нет — и кода магазина быть не должно"
    assert [c[0] for c in calls] == ["coords"], "без точки спрашивать справочник нечем"


def test_magnit_shop_changes_with_the_address(db, monkeypatch):
    """Сменил адрес — сменился и магазин. Старый код к новому адресу отношения не имеет."""
    from app.connectors import magnit

    by_city = {
        (55.79, 37.53): [{"code": "264856", "format": "MM", "address": "Москва, Микояна, 12",
                          "distance": 369.0, "delivery": False}],
        (56.83, 60.61): [MM],
    }
    monkeypatch.setattr(magnit, "stores_near",
                        lambda lat, lon, **kw: by_city[(round(lat, 2), round(lon, 2))])
    monkeypatch.setattr("app.geo.coords",
                        lambda address: (55.79, 37.53) if "Москва" in address else (56.83, 60.61))

    location.save_address("Москва, Ходынский бульвар 4")
    assert location.for_store("magnit").store_id == "264856"

    location.save_address("Екатеринбург, улица Малышева 51")
    assert location.for_store("magnit").store_id == "668596"


def test_magnit_prefers_a_shop_over_a_closer_darkstore(monkeypatch):
    """Склад доставки бывает ближе магазина, но полка у него уже.

    Молча считать корзину по узкой полке склада значило бы показать человеку, что
    половины его покупок у Магнита «нет». Поэтому магазины идут первыми.
    """
    from app.connectors import magnit

    answer = [
        {"externalId": {"owner": "OWNER_MAGNIT", "storeCode": "730884"},
         "storeTypeV2": "DARKSTORE", "address": "Москва, Хорошёвское шоссе, 38",
         "coordinates": {"latitude": 55.7900, "longitude": 37.5326}},
        {"externalId": {"owner": "OWNER_MAGNIT", "storeCode": "264856"},
         "storeTypeV2": "MM", "address": "Москва, Микояна, 12",
         "coordinates": {"latitude": 55.7924, "longitude": 37.5270}},
        {"externalId": {"owner": "OWNER_MAGNIT", "storeCode": "904035"},
         "storeTypeV2": "MA", "address": "Москва, Хорошёвское шоссе, 34А",
         "coordinates": {"latitude": 55.7901, "longitude": 37.5327}},
        {"externalId": {"owner": "OWNER_RTE", "storeCode": "abc"},
         "storeTypeV2": "RTE_BURGER_KING", "address": "Москва, Ходынский бульвар, 4",
         "coordinates": {"latitude": 55.7899, "longitude": 37.5325}},
    ]
    monkeypatch.setattr(magnit, "_fetch_stores", lambda lat, lon, radius_km: answer)
    found = magnit.stores_near(55.7899, 37.5325)

    assert [s["code"] for s in found] == ["264856", "730884"], (
        "аптека и ресторан-партнёр не продуктовые точки, а склад идёт после магазина")
    assert found[0]["distance"] > found[1]["distance"], "склад и правда ближе — и всё равно второй"


# ---------- доставка до коннектора ----------
def test_saved_address_reaches_the_connector(db, monkeypatch):
    """Адрес из базы должен доехать до магазина сам, без передачи его руками."""
    from app import compare

    seen: dict = {}

    class Silent:
        code = "lenta"

        def search(self, query, limit=3):
            return []

    def fake_get_connector(code, location=None):
        seen[code] = location
        return Silent()

    monkeypatch.setattr("app.connectors.get_connector", fake_get_connector)
    location.save_address("Екатеринбург, улица Щербакова 4")

    compare.compare_query("молоко", store_codes=["lenta"])

    assert isinstance(seen["lenta"], Location)
    assert seen["lenta"].address == "Екатеринбург, улица Щербакова 4"


# ---------- проверка адреса ----------
def test_check_names_the_nearest_shop():
    """Проверка адреса должна назвать БЛИЖАЙШИЙ магазин, а не первый попавшийся.

    Лента отдаёт магазины не по расстоянию: ответ по Екатеринбургу начинался
    с 9110 м, а 394 м лежали третьими.
    """
    from app.web.screens.home import _distance

    hubs = [{"id": "1", "distance": 9110}, {"id": "2", "distance": 394}, {"id": "3"}]

    assert min(hubs, key=_distance)["id"] == "2"
    assert [h["id"] for h in sorted(hubs, key=_distance)] == ["2", "1", "3"],         "магазин без расстояния должен уйти в конец, а не в начало"


def test_distance_reads_like_a_person_wrote_it():
    from app.web.screens.home import _distance_text

    # Разделитель « · » теперь ставит шаблон, а не помощник: одно и то же
    # расстояние показывается в разных местах с разной обвязкой.
    assert _distance_text({"distance": 394}) == "394 м"
    assert _distance_text({"distance": 9110}) == "9,1 км"
    assert _distance_text({}) == ""


def test_topbar_button_says_what_is_set(db):
    """Надпись на кнопке адреса обязана работать, а не молчать.

    main.py рисует шапку в общем try и на любой ошибке показывает пустоту — то есть
    сломанная надпись выглядит как «адреса нет». Ровно так и вышло, когда из
    location убрали выбор точки, а обращение к нему в сводке осталось.
    """
    from app.web import create_app

    client = create_app().test_client()
    client.post("/login", data={"phone": "79990000001", "next": "/"})

    assert "Укажите адрес" in client.get("/").data.decode("utf-8")
    client.post("/address", data={"address": "Екатеринбург, улица Щербакова 4"})
    assert "Екатеринбург, улица Щербакова 4" in client.get("/").data.decode("utf-8")


def test_long_address_does_not_stretch_the_header(db):
    """Шапка одна на все экраны — длинный адрес не должен её распирать.

    Обрезка переехала из кода в стиль: раньше подпись кнопки резалась в Python и
    кончалась многоточием, теперь адрес выводится целиком, а обрезает его
    таблица стилей. Так лучше — на широком экране адрес виден весь, — но правило
    осталось правилом, и сторожить его надо там, где оно теперь живёт.
    """
    import os

    from app import config

    css = os.path.join(config.ROOT, "app", "web", "static", "korzina.css")
    with open(css, encoding="utf-8") as fh:
        rules = fh.read()

    block = rules[rules.index(".addr{"):rules.index("}", rules.index(".addr{"))]
    assert "max-width" in block, "без потолка ширины длинный адрес распирает шапку"
    assert "text-overflow:ellipsis" in block
    assert "white-space:nowrap" in block
