"""Контрактные тесты единого React SPA и его JSON API поверх реального SQLite-бэкенда."""
from __future__ import annotations

import os
import pytest

from app import cartrun, config, repo, users
from app.models import Product
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

    missing_api = client.get("/api/does-not-exist")
    assert missing_api.status_code == 404
    assert missing_api.is_json
    assert missing_api.get_json()["ok"] is False


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
        "point": {"lat": 55.758, "lon": 37.611},
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
    assert abs(saved["point"]["lat"] - 55.758) < 0.001
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


def test_runs_and_receipt_paste(spa_client):
    client, _ = spa_client
    login = client.post("/api/auth/login", json={"phone": "79990001122"}).get_json()
    headers = {"Authorization": f"Bearer {login['token']}"}

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
    assert len(pdata["snapshot"]["orders"]) == 1
    assert pdata["snapshot"]["orders"][0]["total"] == 403.0

    # Записываем живой отчёт сборки через app.cartrun и проверяем /api/runs
    users.open_workspace("79990001122")
    try:
        cartrun.accept(
            {
                "store": "vkusvill",
                "landed": [{"name": "Творог 5%", "qty": 1, "price": 145}],
                "missing": [],
                "total": 145,
                "delivery": 0,
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
    assert runs_data["storeQuotes"]["vkusvill"]["itemsTotal"] == 145.0


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
