"""Контрактные тесты единого React SPA и его JSON API поверх реального SQLite-бэкенда."""
from __future__ import annotations

import pytest
import base64
import json

from app import cartrun, config, repo, users
from app.web import create_app, spa


@pytest.fixture()
def spa_client(tmp_path, monkeypatch):
    """Изолированное Flask-приложение с временной папкой data/ и тестовым index.html SPA."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(data_dir / "users"))
    monkeypatch.setattr(users, "PIN_FILE", str(data_dir / "install.pin"))

    spa_dir = tmp_path / "dist" / "client"
    assets_dir = spa_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (spa_dir / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>korzina-spa</div></body></html>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('korzina');", encoding="utf-8")
    monkeypatch.setattr(spa, "_spa_dir", lambda: str(spa_dir))

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client(), app


def test_search_prioritizes_a_single_purchase_in_this_workspace(spa_client, tmp_path, monkeypatch):
    from app.catalog import refresh, store
    from app.catalog.model import ChainProduct
    from app.models import Product

    client, _ = spa_client
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "ranking.db"))
    store.init()
    store.upsert_products("magnit", [
        ChainProduct("801", "Кефир Утро", price=85, in_stock=True),
        ChainProduct("802", "Кефир Вечер", price=120, in_stock=True),
        ChainProduct("803", "Кефир Полдень", price=65, in_stock=False)])
    refresh.match_all()
    mid = next(r["item_id"] for r in store.products_of_chain("magnit") if r["sku"] == "802")
    phone = "79995550123"
    users.open_workspace(phone)
    try:
        pid = repo.upsert_product(Product(id=None, name="КЕФ. ВЧР"))
        repo.set_master(pid, mid, "catalog")
        repo.add_history_row("2026-01-01", None, pid, "КЕФ. ВЧР", 1, 120, 120, "sample")
    finally:
        users.deactivate()
    for who, first in [(phone, "Кефир Вечер"), ("79995550124", "Кефир Утро")]:
        auth = client.post("/api/auth/login", json={"phone": who}).get_json()
        rows = client.get("/api/search", query_string={"q":"кефир"},
                          headers={"Authorization":"Bearer " + auth["token"]}).get_json()["results"]
        assert rows[0]["product"]["name"] == first
        assert rows[0]["product"]["previouslyBought"] == (who == phone)
        assert rows[-1]["product"]["name"] == "Кефир Полдень"
        if who == phone:
            assert rows[0]["product"]["id"] == pid


def test_spa_routes_serve_unified_html_and_assets(spa_client):
    client, _ = spa_client
    for path in ("/", "/login", "/basket", "/cheaper", "/orders", "/prices", "/settings", "/phone/basket"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "korzina-spa" in resp.get_data(as_text=True)
        assert "no-store" in (resp.headers.get("Cache-Control") or "")

    js_resp = client.get("/assets/app.js")
    assert js_resp.status_code == 200
    assert "immutable" in (js_resp.headers.get("Cache-Control") or "")

    assert client.get("/assets/missing.js").status_code == 404

    missing_api = client.get("/api/does-not-exist")
    assert missing_api.status_code == 404
    assert missing_api.is_json
    assert missing_api.get_json()["ok"] is False


def test_replace_only_from_plan_at_my_address_and_recheck_on_tap(spa_client, tmp_path, monkeypatch):
    from app import location
    from app.catalog import refresh, store
    from app.catalog.model import ChainProduct
    from app.models import Product
    from app.places import Point

    client, _ = spa_client
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "eggs.db"))
    store.init()
    def eggs(sku, name, price, stock=True, point="my", size=10):
        return ChainProduct(sku, f"Яйца {name} С1 {size}шт", price=price, in_stock=stock, point=point)
    store.upsert_products("magnit", [eggs("11","Белые",120), eggs("22","Рябушка",85),
                                      eggs("33","Отборные",10,point="elsewhere"),
                                      eggs("44","Семейные",20,stock=None), eggs("55","Утро",50,size=6)])
    store.upsert_products("magnit", [eggs("22","Рябушка",1,point="elsewhere")])
    store.upsert_products("lenta", [eggs("66","Фермерские",90)])
    store.upsert_products("dixy", [eggs("77","Ко-Коль-Чики",89.99)])
    store.upsert_products("magnit", [eggs("88","Давние",40)],seen_at="2020-01-01T00:00:00")
    refresh.match_all()
    phone, address = "79995550125", "Тестовый адрес"
    users.open_workspace(phone)
    try:
        repo.set_setting(location.KEY_ADDRESS, address)
        store.remember_points([Point(code,"my","",address) for code in ("magnit","lenta","dixy")])
        mid = next(r["item_id"] for r in store.products_of_chain("magnit") if r["sku"] == "11")
        bought = repo.upsert_product(Product(id=None, name="ЯЙЦ БЕЛ"))
        repo.set_master(bought,mid,"catalog")
        repo.add_history_row("2026-01-01",None,bought,"ЯЙЦ БЕЛ",1,120,120,"sample")
        original = repo.upsert_product(Product(id=None, name="Яйцо Синявино С1 10шт"))
    finally:
        users.deactivate()
    client.post("/api/auth/login", json={"phone": phone})
    params = {"store":["lenta","magnit"], "unit":"pcs", "q":"Яйца C1 10 шт", "exclude":original}
    rows = client.get("/api/replacements",query_string=params).get_json()["results"]
    assert [(r["product"]["previouslyBought"], min(v["price"] for v in r["prices"].values()))
            for r in rows] == [(True,120),(False,85),(False,90)]
    assert rows[0]["product"]["id"] == bought
    only = client.get("/api/replacements",query_string={**params,"store":"lenta"}).get_json()["results"]
    assert [list(r["prices"]) for r in only] == [["lenta"]]
    choice = {**params, "store":"magnit", "sku":"22", "product":rows[1]["product"]["id"]}
    accepted = client.post("/api/replacements",query_string=choice).get_json()["item"]
    pid = accepted["product"]["id"]
    assert pid > 0 and accepted["prices"]["magnit"]["price"] == 85
    assert client.post("/api/replacements",query_string=choice).get_json()["item"]["product"]["id"] == pid
    snapshot = client.get("/api/sync").get_json()["snapshot"]
    assert snapshot["prices"][str(pid)]["magnit"]["price"] == 85
    store.upsert_products("magnit", [eggs("22","Рябушка",85,stock=False)])
    assert client.post("/api/replacements",query_string={**choice,"product":pid}).status_code == 409
    assert len(client.get("/api/replacements",query_string=params).get_json()["results"]) == 2


def test_receipt_file_is_private_and_reimport_is_idempotent(spa_client):
    client, _ = spa_client
    path = "/api/receipts/file"
    receipt = {"dateTime": "2026-11-20T12:00:00", "retailPlace": "Магнит", "totalSum": 10000,
               "items": [{"name": "Яйца С1, 10 шт.", "quantity": 1, "price": 10000, "sum": 10000}]}
    data = {"receipts": [{"key": "sample-only", "store": "Магнит", "fiscalData": receipt}]}
    body = {"content": base64.b64encode(json.dumps(data).encode()).decode()}
    assert client.post(path, json=body).status_code == 401
    assert client.get("/api/fns/status").status_code == 401
    client.post("/api/auth/login", json={"phone": "79995550101"})
    assert client.post("/api/receipts/needed", json={"keys": ["sample-only"]}).get_json()["needed"] == ["sample-only"]
    first = client.post(path, json=body).get_json()
    assert first["receipts"] == first["rows"] == 1 and first["skipped"] == 0
    assert client.post("/api/receipts/needed", json={"keys": ["sample-only"]}).get_json()["needed"] == []
    again = client.post(path, json=body).get_json()
    assert again["receipts"] == 0 and again["skipped"] == 1
    assert client.post(path, json={"content": "!!!"}).status_code == 400
    assert client.get("/api/fns/status").get_json()["running"] is False
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"phone": "79995550102"})
    assert client.post(path, json=body).get_json()["receipts"] == 1


def test_auth_login_bearer_sync_roundtrip(spa_client):
    client, app = spa_client

    # Без входа
    me0 = client.get("/api/auth/me")
    assert me0.status_code == 200
    assert me0.get_json()["authenticated"] is False

    # Вход по телефону
    login_resp = client.post("/api/auth/login", json={"phone": "+7 (999) 123-45-67"})
    assert login_resp.status_code == 200
    login_data = login_resp.get_json()
    assert login_data["ok"] is True
    assert login_data["auth"]["authenticated"] is True
    assert login_data["auth"]["phone"] == "79991234567"
    token = login_data["token"]
    assert isinstance(token, str) and len(token) > 20
    snap = login_data["snapshot"]
    assert len(snap["stores"]) >= 7

    # Доступ с другого клиента (без кук) только по Bearer-токену
    bearer_client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}
    me_bearer = bearer_client.get("/api/auth/me", headers=headers)
    assert me_bearer.status_code == 200
    assert me_bearer.get_json()["phone"] == "79991234567"

    # Сохраняем корзину, новый товар, адрес, карту, кэшбэк, подключение сети и бонусы
    pyat_store = next(s for s in snap["stores"] if s["code"] == "pyaterochka")
    sync_payload = {
        "products": [
            {"id": -501, "name": "Молоко Простоквашино 3.2% 930 мл", "brand": "Простоквашино", "weight_g": 930, "unit": "pcs", "active": True},
        ],
        "baskets": [{"id": 1, "name": "Основная", "created_at": "2026-11-20", "source": "manual"}],
        "basketItems": {"1": [{"product_id": -501, "qty": 2}]},
        "lastBasketId": 1,
        "repeated": [],
        "address": "Москва, Тверская улица, 7",
        "point": [55.758, 37.611],
        "connections": {
            "pyaterochka": {
                "connected": True,
                "account": "+7 999 123-45-67",
                "points": 150,
            }
        },
        "storePerks": {
            "pyaterochka": {"bonusBalanceRub": 150, "maxBonusRedeemPct": 50, "promoDiscountRub": 100}
        },
        "applyBonuses": True,
        "cards": [{"id": 1, "bank": "Т-Банк", "name": "Black", "base_cashback_pct": 1.5}],
        "offers": [{"id": 1, "card_id": 1, "store_id": pyat_store["id"], "percent": 5, "cap_rub": 500, "min_check_rub": 0, "activated": True}],
    }
    post_sync = bearer_client.post("/api/sync", json=sync_payload, headers=headers)
    assert post_sync.status_code == 200
    assert post_sync.get_json()["ok"] is True

    # Перечитываем снимок и проверяем, что всё легло в реальную SQLite-базу
    get_sync = bearer_client.get("/api/sync", headers=headers)
    assert get_sync.status_code == 200
    saved = get_sync.get_json()["snapshot"]
    assert saved["address"] == "Москва, Тверская улица, 7"
    assert saved["point"] is not None
    assert saved["point"] == [55.758, 37.611]
    assert saved["connections"]["pyaterochka"]["connected"] is True
    assert saved["storePerks"]["pyaterochka"]["bonusBalanceRub"] == 150
    assert saved["applyBonuses"] is True
    assert len(saved["cards"]) == 1
    assert saved["cards"][0]["bank"] == "Т-Банк"
    assert len(saved["offers"]) == 1
    assert saved["offers"][0]["percent"] == 5.0

    # Отрицательный временный ID импортировался как реальный положительный product_id в basket.db
    milk = next(p for p in saved["products"] if "Простоквашино" in p["name"])
    assert milk["id"] > 0
    items_b1 = saved["basketItems"][str(saved["baskets"][0]["id"])]
    assert any(it["product_id"] == milk["id"] and it["qty"] == 2 for it in items_b1)

    # Поиск по базе
    search_resp = bearer_client.get("/api/search?q=Простоквашино", headers=headers)
    assert search_resp.status_code == 200
    hits = search_resp.get_json()["results"]
    assert any("Простоквашино" in h["product"]["name"] for h in hits)

    # /api/device/connected также принимает Bearer-токен SPA
    dev_resp = bearer_client.post(
        "/api/device/connected",
        json={"store": "vkusvill", "connected": True, "points": 220},
        headers=headers,
    )
    assert dev_resp.status_code == 200
    assert dev_resp.get_json()["connected"] is True


def test_archived_receipt_product_cannot_come_back_from_an_old_phone_snapshot(spa_client):
    from app.importers.receipt_products import clean_legacy
    from app.models import Product

    client, _ = spa_client
    phone = "79990001125"
    users.open_workspace(phone)
    name = "Анализ крови лабораторный"
    try:
        pid = repo.upsert_product(Product(id=None, name=name))
        repo.add_history_row("2026-11-19", None, pid, name, 1, 100, 100, "old")
        bid = repo.create_basket("Корзина")
        with repo.get_conn() as conn:
            assert clean_legacy(conn, apply=True)["archived"] == 1
            conn.commit()
    finally:
        users.deactivate()

    login = client.post("/api/auth/login", json={"phone": phone}).get_json()
    snapshot = login["snapshot"]
    assert pid not in {product["id"] for product in snapshot["products"]}
    headers = {"Authorization": f"Bearer {login['token']}"}
    hits = client.get("/api/search", query_string={"q": "анализ крови"}, headers=headers).get_json()["results"]
    assert all(hit["product"]["id"] != pid for hit in hits)
    sync = client.post("/api/sync", headers=headers, json={
        "products": [{"id": pid, "name": name, "active": True}],
        "baskets": snapshot["baskets"],
        "basketItems": {str(bid): [{"product_id": pid, "qty": 1}]},
    })
    assert sync.status_code == 200
    current = client.get("/api/sync", headers=headers).get_json()["snapshot"]
    assert current["basketItems"][str(bid)] == []
    assert current["history"][0]["product_id"] is None


def test_runs_and_receipt_paste(spa_client):
    client, _ = spa_client
    login = client.post("/api/auth/login", json={"phone": "79990001122"}).get_json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    unknown = client.post("/api/receipts/paste", json={"text": "Хлеб; 1; 100"}, headers=headers)
    assert unknown.status_code == 400

    # Вставляем чек текстом
    paste = client.post(
        "/api/receipts/paste",
        json={"store_code": "vkusvill", "text": "Яблоки сезонные; 2; 129\nТворог 5%; 1; 145"},
        headers=headers,
    )
    assert paste.status_code == 200
    pdata = paste.get_json()
    assert pdata["ok"] is True
    assert pdata["imported"] == 2
    assert pdata["total"] == 403.0
    assert len(pdata["snapshot"]["receipts"]) == 1
    assert pdata["snapshot"]["receipts"][0]["total"] == 403.0

    duplicate = client.post(
        "/api/receipts/paste",
        json={"store_code": "vkusvill", "text": "Яблоки сезонные; 2; 129\nТворог 5%; 1; 145"},
        headers=headers,
    ).get_json()
    assert duplicate["imported"] == 0
    assert len(duplicate["snapshot"]["history"]) == 2

    service = client.post(
        "/api/receipts/paste",
        json={"store_code": "vkusvill", "text": "Доставка; 1; 90\nСборка; 1; 39"},
        headers=headers,
    )
    assert service.status_code == 200
    rows = service.get_json()["snapshot"]["history"]
    assert {row["raw_name"] for row in rows if row["is_service"]} == {"Доставка", "Сборка"}
    assert len(rows) == 4

    # Записываем живой отчёт сборки через app.cartrun и проверяем /api/runs
    users.open_workspace("79990001122")
    try:
        cartrun.accept(
            {
                "store": "vkusvill",
                "landed": ["Творог 5%"],
                "missing": [],
                "total": {"sum": 145, "source": "network", "delivery": 0},
            },
            by=cartrun.DEVICE,
        )
    finally:
        users.deactivate()

    runs_resp = client.get("/api/runs", headers=headers)
    assert runs_resp.status_code == 200
    runs_data = runs_resp.get_json()
    assert runs_data["ok"] is True
    assert "vkusvill" in runs_data["storeQuotes"]
    assert runs_data["storeQuotes"]["vkusvill"]["store_total"] == 145.0


def test_demo_is_read_only_but_allows_logout_and_relogin(spa_client):
    client, _ = spa_client

    demo = client.post("/api/auth/demo", json={})
    assert demo.status_code == 200
    ddata = demo.get_json()
    assert ddata["auth"]["isDemo"] is True

    # Запись в демо запрещена (403)
    blocked = client.post("/api/sync", json={"address": "Чужой адрес"})
    assert blocked.status_code == 403

    blocked_paste = client.post("/api/receipts/paste", json={"store_code": "vkusvill", "text": "Хлеб; 1; 50"})
    assert blocked_paste.status_code == 403

    # Но выйти из демо и войти под своим номером разрешено (200)
    out = client.post("/api/auth/logout", json={})
    assert out.status_code == 200
    assert out.get_json()["ok"] is True

    me_after = client.get("/api/auth/me")
    assert me_after.get_json()["authenticated"] is False

    # Также прямой вход со своего номера поверх активной демо-сессии должен сработать
    client.post("/api/auth/demo", json={})
    real_login = client.post("/api/auth/login", json={"phone": "79997778899"})
    assert real_login.status_code == 200
    assert real_login.get_json()["auth"]["isDemo"] is False
    assert real_login.get_json()["auth"]["phone"] == "79997778899"


def test_habits_use_only_goods_from_configured_receipt_chains(spa_client, monkeypatch):
    from app import baskets
    from app.models import Product

    client, _ = spa_client
    monkeypatch.setattr("app.chains.orderable", lambda: {"pyaterochka"})
    phone = "79990001124"
    users.open_workspace(phone)
    try:
        with repo.get_conn() as conn:
            conn.execute("INSERT INTO stores (code, name) VALUES ('ozon', 'Ozon'), ('cafe', 'Кафе')")
            conn.commit()
        milk_id = repo.upsert_product(Product(id=None, name="Молоко"))
        rows = [
            ("pyaterochka", "Молоко", milk_id, True),
            ("ozon", "Молоко", milk_id, False),
            ("ozon", "Сковорода", None, False),
            ("cafe", "Готовый обед", None, False),
            (None, "Поездка на метро", None, False),
            ("metro", "Сыр", None, False),
            ("pyaterochka", "Доставка", None, False),
            ("pyaterochka", "Сборка", None, False),
            ("pyaterochka", "Пакет ПЯТЕРОЧКА 65х40см", None, False),
            ("pyaterochka", "Пакет Мигом", None, False),
            ("pyaterochka", "Вок сковорода 28 см", None, False),
            ("pyaterochka", "Поводок-рулетка для собак 50 кг", None, False),
            ("pyaterochka", "Шлейка для собак", None, False),
            ("pyaterochka", "Пакеты для заморозки 20 шт", None, True),
            ("pyaterochka", "Корм для собак 1 кг", None, True),
        ]
        for i, (code, name, pid, _) in enumerate(rows):
            if pid is None:
                pid = repo.upsert_product(Product(id=None, name=name))
            store = repo.get_store(code) if code else None
            repo.add_history_row("2026-11-19", store.id if store else None, pid, name, 1, 100, 100,
                                 receipt_key=f"mixed-receipt-{i}")
        _, items = baskets.last_purchase_items(repo.get_store("pyaterochka").id)
        names = {item["name"] for item in items}
        assert names == {name for code, name, _, allowed in rows if code == "pyaterochka" and allowed}
    finally:
        users.deactivate()

    response = client.post("/api/auth/login", json={"phone": phone})
    assert response.status_code == 200
    history = response.get_json()["snapshot"]["history"]
    assert len(history) == len(rows)
    assert {r["receipt_key"]: r["recommendable"] for r in history} == {
        f"mixed-receipt-{i}": allowed for i, (_, _, _, allowed) in enumerate(rows)
    }

def test_store_choice_survives_old_client_and_stale_login_state(spa_client):
    client, _ = spa_client
    auth = client.post("/api/auth/login", json={"phone": "79991234567"}).get_json()
    headers = {"Authorization": f"Bearer {auth['token']}"}
    product = {"id": -123456789, "name": "Товар для проверки магазина", "unit": "pcs"}
    body = {
        "products": [product],
        "baskets": [{"id": 1, "name": "Корзина", "source": "manual"}],
        "basketItems": {"1": [{"product_id": product["id"], "qty": 2, "preferred_store": "dixy"}]},
        "lastBasketId": 1,
        "connections": {"dixy": {"connected": True, "mode": "session"}},
    }
    assert client.post("/api/sync", json=body, headers=headers).status_code == 200
    snap = client.get("/api/sync", headers=headers).get_json()["snapshot"]
    line = snap["basketItems"]["1"][0]
    assert line.pop("preferred_store") == "dixy"
    assert snap["connections"]["dixy"]["connected"]
    body.update(products=[], basketItems={"1": [line]}, connections={"dixy": {"connected": False}})
    assert client.post("/api/sync", json=body, headers=headers).status_code == 200
    fresh = client.get("/api/sync", headers=headers).get_json()["snapshot"]
    assert fresh["basketItems"]["1"][0]["preferred_store"] == "dixy"
    assert fresh["connections"]["dixy"]["connected"]
    body["connections"]["dixy"]["disconnectSeen"] = fresh["connections"]["dixy"]["connectedAt"]
    assert client.post("/api/sync", json=body, headers=headers).status_code == 200
    assert not client.get("/api/sync", headers=headers).get_json()["snapshot"]["connections"]["dixy"]["connected"]
