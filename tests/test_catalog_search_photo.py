import io

from PIL import Image

from app import config, users
from app.catalog import photos, refresh, store
from app.catalog.model import ChainProduct
from app.web import create_app


def test_background_thumbnail_survives_search_restart_and_source_change(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    monkeypatch.setattr(config, "ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(users, "PIN_FILE", str(tmp_path / "install.pin"))
    store.init()
    title = "Хлопья овсяные с черникой, 40 г"
    store.upsert_products("lenta", [ChainProduct("715975", title, price=29, in_stock=True)])
    refresh.match_all()
    first = "https://cdn.api.lenta.com/blueberry.png"
    calls = []
    def details(self, sku):
        calls.append(sku)
        return {"id":int(sku), "name":title, "image":first}, photos.lenta.OK
    def get(chain, url, pace, limit):
        calls.append(url)
        content = io.BytesIO()
        Image.new("RGB", (430,500), "blue" if url == first else "red").save(content, "PNG")
        return url, content.getvalue()
    monkeypatch.setattr(photos.lenta.LentaCrawler, "details", details)
    monkeypatch.setattr(photos, "_get", get)
    client = create_app().test_client()
    assert client.post("/api/auth/login", json={"phone":"79995550173"}).status_code == 200
    def result():
        return client.get("/api/search", query_string={"q":"Хлопья"}).get_json()["results"][0]["product"]
    assert result()["photo"] is None
    assert client.get("/api/catalog/photo/lenta/715975").get_json()["photo"] is None
    assert not calls
    assert photos._batch("lenta") == 0
    assert calls == ["715975",first]
    url = result()["photo"]
    assert url.startswith("http://localhost/api/catalog/images/")
    response = client.get(url)
    assert response.status_code == 200
    assert response.cache_control.immutable and response.mimetype == "image/webp"
    assert max(Image.open(io.BytesIO(response.data)).size) == 240
    assert client.get(url, headers={"If-None-Match":response.headers["ETag"]}).status_code == 304
    assert client.get("/api/catalog/photo/lenta/715975").get_json()["photo"] == url
    assert photos._batch("lenta") > 0
    assert calls == ["715975",first]
    new = "https://cdn.api.lenta.com/new-blueberry.png"
    store.upsert_products("lenta",[ChainProduct("715975",title,image=new)])
    assert photos._batch("lenta") == 0
    assert calls[-1] == new and result()["photo"] != url
    assert client.get(url).status_code == 200


def test_page_rejects_recommendations_and_generic_images():
    html = b"""<link rel="canonical" href="/product/11">
    <script type="application/ld+json">{"@type":"Product","sku":"22","image":"/wrong.png"}</script>
    <meta property="og:image" content="/favicon-120.png">"""
    assert photos._image_from_page(html,"https://metro-cc.ru/product/11","11") == ""
    assert photos._image_from_page(html+b'<meta property="og:image" content="/right.png">',
                                   "https://metro-cc.ru/product/11","11").endswith("/right.png")
    assert not photos._allowed("metro","https://metro-cc.ru.example.test/p.png")
