"""Корзина METRO: сеть не трогаем — подменяем запрос.

Сторожа стоят на том, что сломается тихо и дорого. Таких мест здесь четыре, и все
четыре уже однажды были бы ошибкой в живой корзине человека:

    ЧЕЙ ХЕШ. Наполнить не ту корзину — это не «не сработало», это «человек пришёл
        к пустой корзине, а где-то в сети лежит наша». Хеш берётся ТОЛЬКО из куки
        нужного имени с хоста сети и только правильной формы.
    ОКРУГЛЕНИЕ. Корзина сети считает штуками. 0,4 кг, превращённые в int, дают
        ноль — и позиция исчезает молча.
    ЧТО СЧИТАЕТСЯ УСПЕХОМ. Не код 200, а позиция, ВИДНАЯ в перечитанной корзине.
    ТОКЕН В АДРЕСЕ. Тот же сторож, что у цен (tests/test_metro.py): сегмент токена
        обязан уходить в адрес, когда он задан.
"""
from __future__ import annotations

import pytest

from app import config
from app.connectors import metro_cart

HASH = "5502aed9f4501b12da05daf6f5347e7b"


class Line:
    """Строка наряда: ровно те поля, которые читает fill."""

    def __init__(self, sku: str, qty: float = 1, unit: str | None = None) -> None:
        self.sku, self.qty, self.unit = sku, qty, unit
        self.name = f"товар {sku}"


def state(name: str = metro_cart.HASH_COOKIE, value: str = HASH,
          domain: str = "api.metro-cc.ru") -> dict:
    """Сохранённый вход человека в том виде, в каком его кладёт Playwright."""
    return {"cookies": [{"name": "prochee", "value": "xx", "domain": "online.metro-cc.ru"},
                        {"name": name, "value": value, "domain": domain}],
            "origins": []}


def reply(articles=(), unavailable=(), user_hash: str = HASH, total: float = 0.0) -> dict:
    return {"success": True, "data": {
        "eshop_basket_id": 7, "user_hash": user_hash, "minimal_cost": 1500,
        "articles": list(articles), "unavailable_articles": list(unavailable),
        "total_cost": total, "total_discount": 0}}


class Sent:
    """Что ушло в сеть. Подменяет requests.request и ничего никуда не шлёт."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or reply()
        self.calls: list[dict] = []

    def __call__(self, method, url, params=None, json=None, timeout=None, headers=None):
        self.calls.append({"method": method, "url": url, "params": list(params or []),
                           "body": json})
        return _Response(self.payload)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload, self.text, self.status_code = payload, "", 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


TOKEN = "ABC123"


def settings(**values):
    """Настройки для замера. Токен здесь есть НАРОЧНО.

    Без него `fill` отказывается до запроса (замер 19.09.2026: сеть отвечает на
    запись «Basket not found»), и все сторожа формы запроса проверяли бы один и
    тот же отказ вместо того, ради чего написаны. Сам отказ сторожится отдельно —
    test_without_a_token_nothing_is_sent_at_all.
    """
    table = {"connectors.metro_api_token": TOKEN, **values}
    return lambda key, default=None: table.get(key, default)


@pytest.fixture
def sent(monkeypatch):
    out = Sent()
    monkeypatch.setattr(metro_cart.requests, "request", out)
    monkeypatch.setattr(config, "get", settings())
    return out


# ---------- чей хеш ----------

def test_the_hash_comes_from_the_cookie_the_network_sets():
    """Замер 19.09.2026: metro_user_id побайтно равна полю user_hash в ответе."""
    assert metro_cart.hash_of(state()) == HASH


def test_a_cookie_of_another_name_is_not_a_hash():
    """Похожее значение с чужим именем — не хеш. Иначе наполним неизвестно чью корзину."""
    assert metro_cart.hash_of(state(name="metro_session")) is None


def test_a_cookie_from_a_foreign_host_is_refused():
    """Тридцать два знака с чужого узла — совпадение, а не хозяин корзины."""
    assert metro_cart.hash_of(state(domain="example.com")) is None


def test_a_cookie_of_the_wrong_shape_is_refused():
    """Имя то, значение не то: уехать с мусором в адресе хуже, чем сказать «нет»."""
    assert metro_cart.hash_of(state(value="ne-hash")) is None
    assert metro_cart.hash_of(None) is None


def test_the_hash_is_read_from_any_host_of_the_network():
    """Куку сеть вправе поставить на весь свой домен, а не только на узел интерфейса."""
    assert metro_cart.hash_of(state(domain=".metro-cc.ru")) == HASH


# ---------- что уходит в сеть ----------

def test_the_whole_cart_leaves_in_one_request(sent):
    """Шестнадцать позиций — один запрос. Ради этого модуль и написан."""
    metro_cart.fill("16", HASH, [Line(str(100 + i)) for i in range(16)])

    assert len(sent.calls) == 1, "позиции ушли по одной — потерян весь смысл канала"
    call = sent.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/16/eshop/basket/")
    assert ("user_hash", HASH) in call["params"]
    assert len(call["body"]["articles"]) == 16


def test_a_part_of_a_kilogram_becomes_one_pack_not_zero(sent):
    """0,4 кг в корзине сети — одна упаковка. int(0.4) дал бы ноль, и позиция исчезла бы."""
    metro_cart.fill("16", HASH, [Line("117189", qty=0.4, unit="кг")])

    assert sent.calls[0]["body"]["articles"] == [{"article": 117189, "count": 1}]


def test_one_and_a_half_packs_round_up(sent):
    """Полторы штуки — это две: недоложить человеку хуже, чем доложить."""
    metro_cart.fill("16", HASH, [Line("117189", qty=1.5)])

    assert sent.calls[0]["body"]["articles"] == [{"article": 117189, "count": 2}]


def test_a_non_numeric_article_does_not_travel_as_zero(sent):
    """Артикул METRO — число. Из hist-42 нельзя делать 0: это чужой товар."""
    metro_cart.fill("16", HASH, [Line("hist-42"), Line("117189")])

    assert sent.calls[0]["body"]["articles"] == [{"article": 117189, "count": 1}]


def test_a_cart_of_only_foreign_articles_is_refused_loudly(sent):
    """Пустой POST сеть приняла бы молча, и человек прочитал бы «передано»."""
    with pytest.raises(ValueError):
        metro_cart.fill("16", HASH, [Line("hist-42")])
    assert not sent.calls


def test_filling_without_a_hash_is_refused(sent):
    """Без хозяина корзина наполнилась бы наша, а не его."""
    with pytest.raises(ValueError):
        metro_cart.fill("16", "", [Line("117189")])
    assert not sent.calls


def test_too_long_a_cart_is_refused(sent):
    """Столько позиций — ошибка вызывающего, а не большая покупка."""
    with pytest.raises(ValueError):
        metro_cart.fill("16", HASH, [Line(str(i)) for i in range(metro_cart.LINE_LIMIT + 1)])
    assert not sent.calls


# ---------- что приходит обратно ----------

def test_the_refusal_of_the_network_is_loud_not_an_empty_cart(monkeypatch):
    """success:false — это отказ. Тихо вернуть пустую корзину значит соврать «передано»."""
    monkeypatch.setattr(config, "get", settings())
    monkeypatch.setattr(metro_cart.requests, "request",
                        Sent({"success": False, "errors": ["нет такого артикула"]}))

    with pytest.raises(RuntimeError, match="отказала"):
        metro_cart.fill("16", HASH, [Line("117189")])


def test_the_basket_is_parsed_into_lines_and_a_total(sent):
    sent.payload = reply(articles=[{"article": 117189, "count": 2, "eshop_product_id": 55}],
                         unavailable=[{"article": 900}], total=141.82)

    basket = metro_cart.read("16", HASH)

    assert basket.count == 1 and basket.total == 141.82
    assert basket.unavailable[0]["article"] == 900
    assert basket.user_hash == HASH


def test_reading_without_a_hash_lets_the_network_issue_one(sent):
    """Замер 19.09.2026: первый GET без всего отдаёт корзину и её свежий хеш."""
    metro_cart.read("16")

    assert all(name != "user_hash" for name, _ in sent.calls[0]["params"])


# ---------- токен ----------

def test_the_token_goes_into_the_path_when_it_is_configured(sent):
    """Сегмент токена — честная дорога. Тот же сторож, что у цен."""
    metro_cart.fill("16", HASH, [Line("117189")])

    assert f"/{TOKEN}/16/eshop/basket/" in sent.calls[0]["url"]


def test_without_a_token_the_path_has_no_empty_segment(monkeypatch):
    """Чтение без токена идёт — и адрес не должен получить пустой сегмент."""
    out = Sent()
    monkeypatch.setattr(metro_cart.requests, "request", out)
    monkeypatch.setattr(config, "get", lambda key, default=None: None)

    metro_cart.read("16", HASH)

    assert "//16/" not in out.calls[0]["url"].replace("https://", "")


def test_without_a_token_nothing_is_sent_at_all(monkeypatch):
    """Запись без именного токена не уходит в сеть вовсе — отказ раньше запроса.

    ЗАМЕР 19.09.2026, ради которого этот сторож и стоит: POST с настоящим кодом
    точки (store_id=10), настоящим артикулом в наличии (117189) и с хешем, который
    сеть выдала нашему же окну, получил 400 «Basket not found». Метода «создать
    корзину» у сети нет, а её собственная витрина ходит адресом с сегментом токена.
    Значит без токена посылать запрос незачем: человек ждал бы ответа чужой сети
    вместо понятной фразы о том, чего не хватает у нас.
    """
    out = Sent()
    monkeypatch.setattr(metro_cart.requests, "request", out)
    monkeypatch.setattr(config, "get", lambda key, default=None: None)

    assert metro_cart.writable() is False
    with pytest.raises(ValueError, match="токен"):
        metro_cart.fill("16", HASH, [Line("117189")])
    assert not out.calls, "запрос ушёл в сеть, хотя ответ известен заранее"


# ---------- удаление ----------

def test_removing_needs_the_line_id_not_the_article(sent):
    """DELETE у сети принимает идентификатор СТРОКИ корзины — перепутать легко."""
    metro_cart.drop("16", HASH, [55, 56])

    call = sent.calls[0]
    assert call["method"] == "DELETE"
    assert ("eshop_products_id[][eshop_product_id]", "55") in call["params"]
    assert ("eshop_products_id[][eshop_product_id]", "56") in call["params"]


def test_removing_nothing_is_refused(sent):
    with pytest.raises(ValueError):
        metro_cart.drop("16", HASH, [])
    assert not sent.calls
