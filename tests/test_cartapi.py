"""Тесты сборки корзины запросами сети (cartapi.js и правила сервера).

APK 1.24 (09.11.2026): корзина Перекрёстка, Пятёрочки и Дикси собственными
запросами вместо касаний карточек.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.phonecart import rules

NODE = shutil.which("node")
CARTAPI_JS = os.path.join(ROOT, "android", "app", "app", "src", "main", "assets", "cartapi.js")


def test_rules_api_flags():
    """Правила api для телефона: Перекрёсток, Дикси, Магнит — включены, Лента — выключена."""
    assert rules("perekrestok")["api"] is True
    assert rules("dixy")["api"] is True
    assert rules("magnit")["api"] is True
    assert rules("metro")["api"] is True
    # Самокат из 1.28 писал не в ту витрину и считал копейки рублями: запросы — только с 1.29.
    assert rules("samokat", "1.29")["api"] is True and rules("samokat", "1.30.1")["api"] is True
    assert rules("samokat", "1.28")["api"] is False and rules("samokat")["api"] is False
    assert rules("samokat", "1.²")["api"] is False, "версия приходит с телефона: мусор — не версия"
    # Впрок кладётся запросами с 1.62: окно сети прежних версий запросов к его адресу не знает.
    assert rules("vprok", "1.62")["api"] is True and rules("vprok", "1.70")["api"] is True
    assert rules("vprok", "1.61")["api"] is False and rules("vprok")["api"] is False
    assert rules("lenta", "1.29")["api"] is False
    assert rules("perekrestok")["api_burst"] == 3


def _run_node_script(js_code: str) -> dict:
    """Выполнить короткий фрагмент в Node и вернуть разобранный JSON из stdout."""
    script = (
        "const { readFileSync } = require('node:fs');\n"
        "const { createRequire } = require('node:module');\n"
        f"const path = {json.dumps(CARTAPI_JS)};\n"
        "const cartModule = {exports: {}};\n"
        "new Function('module', 'exports', 'require', readFileSync(path, 'utf8'))"
        "(cartModule, cartModule.exports, createRequire(path));\n"
        "const api = cartModule.exports;\n"
        f"{js_code}\n"
    )
    # Предел по времени: бесконечный повтор в адаптере должен ронять тест, а не вешать прогон.
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", check=False, timeout=60)
    assert done.returncode == 0, f"Ошибка Node: {done.stderr}\nКод:\n{script}"
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_cartapi_ops_all_examples():
    """Все 7 примеров ops(live, lines, mine, want, drop) из раздела 1.1 спецификации."""
    cases = [
        # 1. live {}, lines [{a,2}] -> set [{a,0,2}]
        {"live": {}, "lines": [{"sku": "a", "target": 2}], "mine": {}, "want": {}, "drop": False,
         "expected": {"set": [{"sku": "a", "from": 0, "to": 2}], "kept": [], "drops": []}},
        # 2. live {a:1}, lines [{a,2}] -> set [{a,1,2}]
        {"live": {"a": 1}, "lines": [{"sku": "a", "target": 2}], "mine": {}, "want": {}, "drop": False,
         "expected": {"set": [{"sku": "a", "from": 1, "to": 2}], "kept": [], "drops": []}},
        # 3. live {a:3}, lines [{a,2}], mine {}, drop true -> kept [a], drops []
        {"live": {"a": 3}, "lines": [{"sku": "a", "target": 2}], "mine": {}, "want": {}, "drop": True,
         "expected": {"set": [], "kept": ["a"], "drops": []}},
        # 4. live {a:3}, lines [{a,2}], mine {a:3}, want {a:2}, drop true -> kept [a], drops [{a,3,2}]
        {"live": {"a": 3}, "lines": [{"sku": "a", "target": 2}], "mine": {"a": 3}, "want": {"a": 2}, "drop": True,
         "expected": {"set": [], "kept": ["a"], "drops": [{"sku": "a", "from": 3, "to": 2}]}},
        # 5. live {b:2}, mine {b:2}, want {}, drop true -> drops [{b,2,0}]
        {"live": {"b": 2}, "lines": [], "mine": {"b": 2}, "want": {}, "drop": True,
         "expected": {"set": [], "kept": [], "drops": [{"sku": "b", "from": 2, "to": 0}]}},
        # 6. live {b:3}, mine {b:1}, want {}, drop true -> drops [{b,3,2}]
        {"live": {"b": 3}, "lines": [], "mine": {"b": 1}, "want": {}, "drop": True,
         "expected": {"set": [], "kept": [], "drops": [{"sku": "b", "from": 3, "to": 2}]}},
        # 7. то же с drop false -> drops []
        {"live": {"b": 3}, "lines": [], "mine": {"b": 1}, "want": {}, "drop": False,
         "expected": {"set": [], "kept": [], "drops": []}},
    ]

    js_code = (
        f"const cases = {json.dumps(cases)};\n"
        "const results = cases.map(c => api.ops(c.live, c.lines, c.mine, c.want, c.drop));\n"
        "process.stdout.write(JSON.stringify(results));"
    )
    results = _run_node_script(js_code)
    for i, res in enumerate(results):
        exp = cases[i]["expected"]
        assert res == exp, f"Пример {i + 1} не совпал: получили {res}, ожидали {exp}"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_parse_perekrestok():
    """Разбор ответа Перекрёстка: q, price, amount, base."""
    fixture = {
        "content": {
            "items": [
                {
                    "amount": 1000,
                    "cost": 18399,
                    "state": "ok",
                    "product": {"masterData": {"plu": "1001", "quantum": 1000}},
                },
                {
                    "amount": 1200,
                    "cost": 22799,
                    "state": "ok",
                    "product": {"masterData": {"plu": "1002", "quantum": 300}},
                },
            ],
            "invoice": {
                "grossCost": 112794,
                "cost": 104894,
                "discountValue": 7900,
                "deliveryCost": 0,
                "paymentAmount": 109794,
                "itemNumber": 5,
            },
        }
    }
    js_code = (
        f"const fix = {json.dumps(fixture)};\n"
        "const res = api.parse.perekrestok(fix);\n"
        "process.stdout.write(JSON.stringify(res));"
    )
    res = _run_node_script(js_code)
    assert res["items"]["1001"]["q"] == 1
    assert res["items"]["1001"]["price"] == 183.99
    assert res["items"]["1002"]["q"] == 4
    assert res["items"]["1002"]["price"] == 56.9975
    assert res["total"]["amount"] == 1097.94
    assert res["total"]["base"] == 1127.94


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_parse_pyaterochka():
    """Разбор ответа Пятёрочки: q, price, amount, goods, base и пустая корзина."""
    fixture = {
        "basket": {
            "items": [
                {
                    "product_plu": "2001",
                    "quantity": "2",
                    "total": "339.98",
                    "uom": "шт",
                    "unavailability": None,
                }
            ],
            "total_sum": "399.98",
            "total_discount": "60.00",
            "final_sum": "398.98",
            "delivery_cost": "0.00",
            "items_count": 1,
        }
    }
    js_code = (
        f"const fix = {json.dumps(fixture)};\n"
        "const res = api.parse.pyaterochka(fix);\n"
        "process.stdout.write(JSON.stringify(res));"
    )
    res = _run_node_script(js_code)
    assert res["items"]["2001"]["q"] == 2
    assert res["items"]["2001"]["price"] == 169.99
    assert res["total"]["amount"] == 398.98
    assert res["total"]["goods"] == 339.98
    assert res["total"]["base"] == 399.98

    # Пустая корзина -> total null
    empty_fixture = {
        "basket": {
            "items": [],
            "total_sum": "0.00",
            "total_discount": "0.00",
            "final_sum": "59.00",
            "delivery_cost": "0.00",
            "items_count": 0,
        }
    }
    js_code_empty = (
        f"const fix = {json.dumps(empty_fixture)};\n"
        "const res = api.parse.pyaterochka(fix);\n"
        "process.stdout.write(JSON.stringify(res));"
    )
    res_empty = _run_node_script(js_code_empty)
    assert res_empty["total"] is None


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_parse_dixy():
    """Разбор ответа Дикси: q, goods, amount, base."""
    fixture = {
        "template_data": {
            "items": [
                {
                    "xml_id": "3001",
                    "id": 12345,
                    "quantity": 2,
                    "ratio": 1,
                    "priceSimple": 144.99,
                    "canBuy": True,
                }
            ],
            "toPay": "318.98",
            "baseBskSumm": "319.98",
            "count": 1,
        }
    }
    js_code = (
        f"const fix = {json.dumps(fixture)};\n"
        "const res = api.parse.dixy(fix);\n"
        "process.stdout.write(JSON.stringify(res));"
    )
    res = _run_node_script(js_code)
    assert res["items"]["3001"]["q"] == 2
    assert res["total"]["goods"] == 289.98
    assert res["total"]["amount"] == 318.98
    assert res["total"]["base"] == 319.98

    # Тысячи сеть шлёт через пробел: «1 448.71» читалось как 1, и итог сети выходил 1 ₽.
    fixture["template_data"].update(toPay="1 448.71", baseBskSumm="1 614.44")
    fixture["template_data"]["items"][0]["priceSimple"] = "1 144.99"
    big = _run_node_script(f"process.stdout.write(JSON.stringify(api.parse.dixy({json.dumps(fixture)})));")
    assert (big["total"]["amount"], big["total"]["base"], big["total"]["goods"]) == (1448.71, 1614.44, 2289.98)


# Поддельный XMLHttpRequest для run(): отвечает телом по адресу и записывает, куда ходили
# (seen) и что отправили (sent: заголовки и тело). Код ответа — из поля __status тела.
FAKE_XHR = """
global.window = {};
const seen = [];
const sent = [];
function fakeXhr(answer) {
  global.XMLHttpRequest = function () {
    const x = this;
    x.h = {};
    x.open = function (m, u) { x.m = m; x.u = u; seen.push(m + ' ' + u); };
    x.setRequestHeader = function (k, v) { x.h[k] = v; };
    x.send = function (b) {
      if (b && b.get) { seen[seen.length - 1] += ' ' + b.get('action'); }
      sent.push({h: x.h, b: b});
      const body = answer(x.u, b, x.m);
      x.status = body === null ? 404 : (body.__status || 200);
      x.responseText = body === null ? '' : typeof body === 'string' ? body : JSON.stringify(body);
      setTimeout(function () { x.onload(); }, 0);
    };
  };
}
"""


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_a_slow_cart_peek_does_not_start_requests_after_the_edit_deadline():
    got = _run_node_script("""
global.window = {__kzPeek: new Promise(() => {})};
let requests = 0;
global.XMLHttpRequest = function () { requests++; };
api.run({chain: 'pyaterochka', lines: [{sku: '123', target: 1}], budget_ms: 40})
  .then(result => process.stdout.write(JSON.stringify({stage: result.stage, requests})));
""")
    assert got == {"stage": "deadline", "requests": 0}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_takes_the_live_cart_when_the_site_lost_its_order_id():
    """Сайт потерял orderId из cart-store: берём действующую корзину, оплаченный заказ не трогаем."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {deviceId: 'dev1'};
fakeXhr(function (u) {
  if (u.indexOf('/v3/orders/') >= 0) {
    return {items: [{id: 'paid', is_active: true, payed_time: '2026-11-01T10:00:00', status: 0},
                    {id: 'live', is_active: true, payed_time: null, status: 0}]};
  }
  return null;
});
api.run({chain: 'pyaterochka', lines: [], mine: {}, want: {}, drop: false, pause_ms: 300, burst: 3})
  .then(function (r) { console.log(JSON.stringify({seen: seen, stage: r.stage, requests: r.requests})); });
""")
    assert out["seen"][1].endswith("/api/orders/v10/orders/live/")
    assert not any("paid" in call for call in out["seen"])
    assert out["requests"] == 2


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_without_a_live_cart_asks_for_the_address():
    """Действующей корзины нет (только оплаченные): ничего не кладём, просим выбрать адрес."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {deviceId: 'dev1'};
fakeXhr(function () { return {items: [{id: 'paid', is_active: true, payed_time: '2026-11-01', status: 0}]}; });
api.run({chain: 'pyaterochka', lines: [{sku: '123', target: 1}], mine: {}, want: {}, drop: false})
  .then(function (r) { console.log(JSON.stringify({seen: seen, ok: r.ok, stage: r.stage})); });
""")
    assert out == {"seen": ["GET https://5d.5ka.ru/api/orders/v3/orders/?in_action=true"],
                   "ok": False, "stage": "address"}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
@pytest.mark.parametrize("where, stage", [
    ({"lat": "55.757", "lon": "37.615"}, "address"),     # Тверская, 1 в Москве — адрес сайта по умолчанию
    ({"lat": 60.0505, "lon": 30.3301}, None),            # рядом с адресом из приложения
    (None, None),                                        # адреса в заказе нет — сверять не с чем
])
def test_pyaterochka_does_not_write_into_a_cart_at_another_address(where, stage):
    """Корзина сети по чужому адресу (1.63): владелец 16.11.2026 открыл окно Пятёрочки, а там «Тверская улица, 1»
    вместо его адреса в Петербурге. В такую корзину не пишем и просим выбрать свой адрес; сам адрес в причину не идёт."""
    order = {"id": "live", "is_active": True, "payed_time": None, "status": 0,
             "basket": {"items": [], "items_count": 0, "total_sum": "0", "total_discount": "0"}}
    if where:
        order["address"] = dict(where, city="Москва", street="Тверская улица", house="1")
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {deviceId: 'dev1', 'cart-store': JSON.stringify({orderId: 'live'})};
fakeXhr(function (u, b, m) { return m === 'GET' ? %s : {}; });
api.run({chain: 'pyaterochka', lines: [{sku: '123', target: 1}], mine: {}, want: {}, drop: false,
         place: {lat: 60.05, lon: 30.33}})
  .then(function (r) { console.log(JSON.stringify({seen: seen, stage: r.stage || null, why: r.why || r.error || ''})); });
""" % json.dumps(order))
    assert out["stage"] == stage
    writes = [call for call in out["seen"] if not call.startswith("GET ")]
    if stage == "address":
        assert not writes, "в корзину по чужому адресу писали"
        assert "Тверская" not in json.dumps(out, ensure_ascii=False), "адрес попал в причину отказа"
    else:
        assert writes, "в корзину по своему адресу не писали"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_refusal_of_the_first_request_is_fast_only_when_quick_and_online():
    """«Обрыв» узла корзин Пятёрочки — это и отказ её защиты, и обрыв связи. Куку приложение лечит только в первом
    случае (1.50): быстро (меньше 8 с при таймауте запроса 15 с) и при живой связи. Таймаут и телефон без сети кукой не
    лечатся."""
    out = _run_node_script("""
global.window = {};
global.localStorage = {deviceId: 'dev1', 'kz.5ka.order': JSON.stringify({id: 'live', at: Date.now()})};
const realNow = Date.now.bind(Date);
let skew = 0, slow = 0;
Date.now = function () { return realNow() + skew; };
global.XMLHttpRequest = function () {
  const x = this;
  x.open = function () {};
  x.setRequestHeader = function () {};
  x.send = function () { skew += slow; x.status = 0; setTimeout(function () { x.onerror(); }, 0); };
};
async function once(slowMs, online) {
  slow = slowMs;
  Object.defineProperty(global, 'navigator', {value: online === undefined ? {} : {onLine: online}, configurable: true});
  const r = await api.run({chain: 'pyaterochka', lines: [], mine: {}, want: {}, drop: false, pause_ms: 300, burst: 3,
                           peek: true});
  return [r.ok, r.stage, r.fast];
}
(async function () {
  console.log(JSON.stringify({quick: await once(0), slow: await once(4000), timeout: await once(9000),
                              offline: await once(0, false), online: await once(0, true)}));
})();
""")
    assert out == {"quick": [False, "net", True], "slow": [False, "net", True], "timeout": [False, "net", False],
                   "offline": [False, "net", False], "online": [False, "net", True]}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
@pytest.mark.parametrize(("event", "detail"), [
    ("onerror", "ошибка связи"), ("ontimeout", "истекло время ожидания ответа"),
    ("onabort", "запрос отменён"),
])
def test_pyaterochka_distinguishes_xhr_failures(event, detail):
    out = _run_node_script("""
global.localStorage = {deviceId: 'test'};
global.XMLHttpRequest = class {
  open() {}
  setRequestHeader() {}
  send() { this[EVENT](); }
};
api.run({chain: 'pyaterochka', lines: [], peek: true}).then(r => console.log(JSON.stringify(r)));
""".replace("EVENT", json.dumps(event)))
    assert not out["ok"] and detail in out["why"]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_write_refusal_is_fast_only_as_the_first_request_of_the_run():
    """Протухшая кука отказывает на самый первый запрос сборки. Запись, оборвавшаяся после принятого чтения, — не она:
    куку не стираем. Правка по памяти начинает прямо с записи — её отказ первый, а значит, быстрый (1.50)."""
    out = _run_node_script(PYAT_SHOP + """
const Shop = global.XMLHttpRequest;
function failWrites(all) {
  global.XMLHttpRequest = function () {
    const x = new Shop();
    const send = x.send;
    x.send = function (b) {
      if (all || x.m === 'PUT') { x.status = 0; setTimeout(function () { x.onerror(); }, 0); } else { send(b); }
    };
    return x;
  };
}
(async function () {
  failWrites(false);
  const cold = await api.run(edit(3, {touch: false}));          // чтение приняли, запись оборвалась
  const coldCalls = calls();
  global.XMLHttpRequest = Shop;
  await api.run(edit(2, {touch: false}));                        // читает и запоминает корзину
  failWrites(true);
  const m = seen.length;
  const memo = await api.run(edit(3));                           // по памяти: первый запрос — запись
  console.log(JSON.stringify({cold: [cold.stage, cold.fast, cold.requests], coldCalls: coldCalls,
                              memo: [memo.stage, memo.fast, memo.requests], memoCalls: calls(m)}));
})();
""")
    assert out["cold"] == ["net", False, 2] and out["coldCalls"] == ["GET v10/orders/live/", "PUT v8/orders/live/item/123/"]
    assert out["memo"] == ["net", True, 1] and out["memoCalls"] == ["PUT v8/orders/live/item/123/"]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_remembers_the_found_order_and_skips_the_search():
    """Найденный номер заказа помним: вторая правка не тратит запрос на поиск действующего заказа."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {deviceId: 'dev1'};
fakeXhr(function (u) {
  if (u.indexOf('/v3/orders/') >= 0) return {items: [{id: 'live', is_active: true, payed_time: null, status: 0}]};
  return {id: 'live', is_active: true, payed_time: null, status: 0, basket: {items: [], items_count: 0}};
});
(async function () {
  const input = {chain: 'pyaterochka', lines: [], mine: {}, want: {}, drop: false, pause_ms: 300, burst: 10};
  const first = await api.run(input);
  const n = seen.length;
  const second = await api.run(input);
  console.log(JSON.stringify({ok: [first.ok, second.ok], first: seen.slice(0, n), second: seen.slice(n),
                              kept: JSON.parse(localStorage['kz.5ka.order']).id}));
})();
""")
    assert out["ok"] == [True, True]
    assert len(out["first"]) == 2 and "in_action" in out["first"][0]
    assert out["second"] == ["GET https://5d.5ka.ru/api/orders/v10/orders/live/"]
    assert out["kept"] == "live"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_forgets_an_order_that_is_no_longer_a_cart():
    """Запомненный заказ оформили: его не трогаем, ищем действующий и запоминаем уже его."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {deviceId: 'dev1', 'kz.5ka.order': JSON.stringify({id: 'old', at: Date.now()})};
fakeXhr(function (u, b, m) {
  if (u.indexOf('/v3/orders/') >= 0) return {items: [{id: 'new', is_active: true, payed_time: null, status: 0}]};
  if (u.indexOf('/orders/old/') >= 0) {
    return {id: 'old', is_active: true, payed_time: '2026-11-11T10:00:00', status: 2, basket: {items: [], items_count: 0}};
  }
  if (m === 'POST') {
    return {id: 'new', is_active: true, payed_time: null, status: 0,
            basket: {items: [{product_plu: 123, quantity: 1, uom: 'шт', total: 50}], items_count: 1}};
  }
  return {id: 'new', is_active: true, payed_time: null, status: 0, basket: {items: [], items_count: 0}};
});
api.run({chain: 'pyaterochka', lines: [{sku: '123', target: 1}], mine: {}, want: {}, drop: false, pause_ms: 300, burst: 10})
  .then(function (r) {
    console.log(JSON.stringify({seen: seen, ok: r.ok, line: r.lines['123'].ok,
                                kept: JSON.parse(localStorage['kz.5ka.order']).id}));
  });
""")
    assert out["seen"] == ["GET https://5d.5ka.ru/api/orders/v10/orders/old/",
                           "GET https://5d.5ka.ru/api/orders/v3/orders/?in_action=true",
                           "GET https://5d.5ka.ru/api/orders/v10/orders/new/",
                           "POST https://5d.5ka.ru/api/orders/v8/orders/new/item/"]
    assert out["ok"] is True and out["line"] is True
    assert out["kept"] == "new"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_never_writes_into_a_found_order_that_is_not_a_cart():
    """Найденный заказ оформили между списком и чтением: в него не пишем. Отказ списка — не «выберите адрес»."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {deviceId: 'dev1'};
const input = {chain: 'pyaterochka', lines: [{sku: '123', target: 1}], mine: {}, want: {}, drop: false, burst: 10};
(async function () {
  fakeXhr(function (u) {
    if (u.indexOf('/v3/orders/') >= 0) return {items: [{id: 'live', is_active: true, payed_time: null, status: 0}]};
    return {id: 'live', is_active: true, payed_time: '2026-11-11T10:00:00', status: 2, basket: {items: [], items_count: 0}};
  });
  const placed = await api.run(input);
  const n = seen.length;
  fakeXhr(function () { return {__status: 500}; });
  const down = await api.run(input);
  fakeXhr(function () { return {__status: 401}; });
  const out401 = await api.run(input);
  console.log(JSON.stringify({placed: [placed.ok, placed.stage], calls: seen.slice(0, n),
                              down: [down.ok, down.stage], auth: [out401.ok, out401.stage],
                              kept: localStorage['kz.5ka.order'] || null}));
})();
""")
    assert out["placed"] == [False, "read"]
    assert out["calls"] == ["GET https://5d.5ka.ru/api/orders/v3/orders/?in_action=true",
                            "GET https://5d.5ka.ru/api/orders/v10/orders/live/"], "записи нет"
    assert out["down"] == [False, "read"] and out["kept"] is None
    assert out["auth"] == [False, "auth"], "401 списка заказов — вход истёк, а не «не читается»"


# Корзина Пятёрочки в сети: 123 — две штуки, 456 — одна; 999 сеть не кладёт. edit(q) — правка по касанию:
# товара 123 нужно q штук, 456 — одна.
PYAT_SHOP = FAKE_XHR + """
global.localStorage = {deviceId: 'dev1', 'kz.5ka.order': JSON.stringify({id: 'live', at: Date.now()})};
const cart = {123: 2, 456: 1};
const shop = {placed: false, bareDelete: false, barePut: false, putStatus: 0, getStatus: 0, thenGet: 0};
function order() {
  const items = Object.keys(cart).map(function (k) {
    return {product_plu: Number(k), quantity: cart[k], uom: 'шт', total: 50 * cart[k]};
  });
  return {id: 'live', is_active: true, payed_time: shop.placed ? '2026-11-13T00:00:00' : null,
          status: shop.placed ? 2 : 0, basket: {items: items, items_count: items.length}};
}
fakeXhr(function (u, b, m) {
  const sku = (u.split('/item/')[1] || '').replace('/', '');
  if (m === 'GET' && shop.getStatus) return {__status: shop.getStatus, status: shop.getStatus};
  if (m === 'PUT') {
    if (shop.putStatus) return {__status: shop.putStatus};
    if (!(sku in cart)) return {__status: 404};
    cart[sku] = JSON.parse(b).qty;
    if (shop.barePut) { shop.getStatus = shop.thenGet; return {}; }   // thenGet — чем ответит чтение после записи
  }
  if (m === 'POST') {
    const p = JSON.parse(b);
    if (p.plu === 999) return {__status: 400};
    cart[p.plu] = p.qty;
  }
  if (m === 'DELETE') {
    delete cart[sku];
    if (shop.bareDelete) return {};
  }
  return order();
});
function calls(from) {
  return seen.slice(from || 0).map(function (s) { return s.replace('https://5d.5ka.ru/api/orders/', ''); });
}
function edit(q, more) {
  return Object.assign({chain: 'pyaterochka', lines: [{sku: '123', target: q}, {sku: '456', target: 1}],
                        mine: {}, want: {}, drop: false, pause_ms: 300, burst: 10, touch: true}, more || {});
}
"""


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_touch_edit_is_one_write_by_the_remembered_cart():
    """Корзину видели меньше минуты назад: правка по касанию её не перечитывает, сверка — по ответу записи."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));                 // обычная сборка читает и запоминает
  const n = seen.length;
  const plus = await api.run(edit(3));
  const m = seen.length;
  const next = await api.run(edit(4));
  const k = seen.length;
  await api.run(edit(4, {touch: false}));                 // не по касанию — читаем, как раньше
  console.log(JSON.stringify({first: calls().slice(0, n), plus: calls().slice(n, m), next: calls().slice(m, k),
                              full: calls(k), line: plus.lines['123'], other: plus.lines['456'].how,
                              requests: [plus.requests, next.requests], count: plus.total.count}));
})();
""")
    assert out["first"] == ["GET v10/orders/live/"]
    assert out["plus"] == ["PUT v8/orders/live/item/123/"], "чтения нет"
    assert out["next"] == ["PUT v8/orders/live/item/123/"], "память обновил ответ записи"
    assert out["full"] == ["GET v10/orders/live/"]
    assert (out["line"]["ok"], out["line"]["q"], out["line"]["put"]) == (True, 3, 1)
    assert out["other"] == "kept" and out["requests"] == [1, 1] and out["count"] == 2


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_does_not_trust_an_old_or_foreign_memo():
    """Память старше минуты, старше возвращения человека (в окне сети заказ могли оформить) или от другого
    заказа — корзину читаем."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  const spoil = {old: function (m) { m.at -= 61000; }, foreign: function (m) { m.oid = 'other'; }};
  const out = {};
  for (const name of ['old', 'away', 'foreign', 'fresh']) {
    await api.run(edit(cart[123], {touch: false}));
    const m = JSON.parse(localStorage['kz.5ka.cart']);
    if (spoil[name]) spoil[name](m);
    localStorage['kz.5ka.cart'] = JSON.stringify(m);
    const n = seen.length;
    await api.run(edit(cart[123] + 1, {since: name === 'away' ? m.at + 1000 : m.at}));
    out[name] = calls(n).map(function (c) { return c.split(' ')[0]; });
  }
  console.log(JSON.stringify(out));
})();
""")
    assert out == {"old": ["GET", "PUT"], "away": ["GET", "PUT"], "foreign": ["GET", "PUT"], "fresh": ["PUT"]}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_rereads_when_the_network_contradicts_the_memo():
    """Память устарела: сеть отказала записи или в её ответе нет позиции, которая «лежала», — та же сборка
    читает корзину и докладывает. Давний отказ (again) откажет и так: из-за него не перечитываем."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));
  delete cart[123];                                       // человек убрал товар в приложении сети
  let n = seen.length;
  const gone = await api.run(edit(3));
  const goneCalls = calls(n);

  delete cart[456];
  n = seen.length;
  const miss = await api.run(edit(4));
  const missCalls = calls(n);

  n = seen.length;
  const refused = await api.run(edit(5, {lines: [{sku: '999', target: 1, again: true}, {sku: '123', target: 5},
                                                 {sku: '456', target: 1}]}));
  console.log(JSON.stringify({gone: goneCalls, goneLine: [gone.lines['123'].ok, gone.lines['123'].q],
                              miss: missCalls, missLines: [miss.lines['123'].q, miss.lines['456'].ok, miss.lines['456'].how],
                              missPut: [miss.lines['123'].put, miss.lines['123'].how],
                              refused: calls(n), refusedLines: [refused.ok, refused.lines['999'].ok, refused.lines['123'].q]}));
})();
""")
    assert out["gone"] == ["PUT v8/orders/live/item/123/", "GET v10/orders/live/", "POST v8/orders/live/item/"]
    assert out["goneLine"] == [True, 3]
    assert out["miss"] == ["PUT v8/orders/live/item/123/", "GET v10/orders/live/", "POST v8/orders/live/item/"]
    assert out["missLines"] == [4, True, "request"]
    assert out["missPut"] == [1, "request"], "положенное до перечитывания — в отчёте наше"
    assert out["refused"] == ["POST v8/orders/live/item/", "PUT v8/orders/live/item/123/"], "чтения нет"
    assert out["refusedLines"] == [True, False, 5]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_reads_when_there_is_no_write_to_check_the_memo_by():
    """Писать нечего — свериться с сетью не по чему, корзину читаем. Запись одна и та — давний отказ: пишем по
    памяти без чтения (его новый отказ память не опровергает), и память с прежним сроком остаётся следующей
    правке (1.60)."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));
  let n = seen.length;
  await api.run(edit(2));
  const idle = calls(n);
  const refusedEdit = function () {
    return edit(2, {lines: [{sku: '999', target: 1, again: true}, {sku: '123', target: 2}]});
  };
  const memo = localStorage['kz.5ka.cart'];
  n = seen.length;
  const r = await api.run(refusedEdit());
  const refusedOnly = calls(n);
  const kept = localStorage['kz.5ka.cart'] === memo;
  n = seen.length;
  await api.run(refusedEdit());
  const again = calls(n);
  n = seen.length;
  await api.run(edit(3));
  console.log(JSON.stringify({idle: idle, refusedOnly: refusedOnly, kept: kept, again: again, after: calls(n),
                              lines: [r.ok, r.lines['999'].ok, r.lines['123'].how, r.lines['123'].q]}));
})();
""")
    assert out["idle"] == ["GET v10/orders/live/"]
    assert out["refusedOnly"] == ["POST v8/orders/live/item/"], "чтения перед давним отказом нет"
    assert out["kept"] is True, "память та же и со своим сроком"
    assert out["again"] == ["POST v8/orders/live/item/"]
    assert out["after"] == ["PUT v8/orders/live/item/123/"]
    assert out["lines"] == [True, False, "kept", 2]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_old_refusal_trusts_the_memo_only_on_its_400():
    """Корзина «та же, что в памяти» — только при 400 давней позиции. 404 (товар убрали в другом месте) — память
    устарела: читаем и кладём заново. 403 — сеть отказывает: без чтения, память не остаётся. Легла — память
    по ответу записи, со свежим сроком (1.60)."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  const out = {};
  const old = function (q) { return edit(q, {lines: [{sku: '123', target: q, again: true}, {sku: '456', target: 1}]}); };
  await api.run(edit(2, {touch: false}));
  delete cart[123];
  let n = seen.length;
  const gone = await api.run(old(3));
  out.gone = calls(n);
  out.goneLine = [gone.lines['123'].ok, gone.lines['123'].q];

  await api.run(edit(3, {touch: false}));
  shop.putStatus = 403;
  n = seen.length;
  await api.run(old(4));
  out.walled = calls(n);
  out.walledMemo = localStorage['kz.5ka.cart'] || null;
  shop.putStatus = 0;

  await api.run(edit(3, {touch: false}));
  const before = localStorage['kz.5ka.cart'];
  n = seen.length;
  const back = await api.run(old(4));
  out.back = calls(n);
  out.backLine = [back.lines['123'].ok, back.lines['123'].q];
  out.renewed = localStorage['kz.5ka.cart'] !== before
    && JSON.parse(localStorage['kz.5ka.cart']).parsed.cart['123'] === 4;
  console.log(JSON.stringify(out));
})();
""")
    assert out["gone"] == ["PUT v8/orders/live/item/123/", "GET v10/orders/live/", "POST v8/orders/live/item/"]
    assert out["goneLine"] == [True, 3]
    assert out["walled"] == ["PUT v8/orders/live/item/123/"] and out["walledMemo"] is None
    assert out["back"] == ["PUT v8/orders/live/item/123/"] and out["backLine"] == [True, 4]
    assert out["renewed"] is True


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_stops_writing_once_the_answer_says_the_order_is_placed():
    """Корзину оформили в заказ, пока правка шла по памяти: ответ первой записи это говорит — вторая не
    уходит, память и номер заказа забыты."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));
  shop.placed = true;
  const n = seen.length;
  const r = await api.run(edit(3, {lines: [{sku: '123', target: 3}, {sku: '456', target: 2}]}));
  console.log(JSON.stringify({calls: calls(n), ok: r.ok, stage: r.stage,
                              memo: localStorage['kz.5ka.cart'] || null, order: localStorage['kz.5ka.order'] || null}));
})();
""")
    assert out == {"calls": ["PUT v8/orders/live/item/123/"], "ok": False, "stage": "read", "memo": None, "order": None}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_rereads_after_a_write_that_answers_without_the_cart():
    """Каждая запись по памяти обязана вернуть корзину: снятие ответило без неё — читаем, а не докладываем
    по памяти. Положенное и снятое до перечитывания остаются в отчёте."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  cart[777] = 1;
  await api.run(edit(2, {touch: false}));
  shop.bareDelete = true;
  const n = seen.length;
  const r = await api.run(edit(3, {mine: {777: 1}, want: {123: 3, 456: 1}, drop: true}));
  console.log(JSON.stringify({calls: calls(n), cart: r.cart, dropped: r.dropped,
                              line: [r.lines['123'].how, r.lines['123'].put, r.lines['123'].q]}));
})();
""")
    assert out["calls"] == ["PUT v8/orders/live/item/123/", "DELETE v8/orders/live/item/777/", "GET v10/orders/live/"]
    assert out["cart"] == {"123": 3, "456": 1} and out["dropped"] == 1
    assert out["line"] == ["request", 1, 3]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_does_not_reread_or_repeat_when_the_refusal_is_not_about_the_memo():
    """Отказ, который мог быть и от памяти (400), проверяем чтением, но ту же запись второй раз не шлём.
    403 записи — сеть отказывает, а не память устарела: лишним чтением её не дёргаем, память не продлеваем."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));
  let n = seen.length;
  const first = await api.run(edit(3, {lines: [{sku: '999', target: 1}, {sku: '123', target: 3},
                                               {sku: '456', target: 1}]}));
  const firstCalls = calls(n);

  shop.putStatus = 403;
  n = seen.length;
  const walled = await api.run(edit(4));
  console.log(JSON.stringify({first: firstCalls,
                              firstLines: [first.lines['999'].ok, first.lines['123'].q, first.lines['123'].ok],
                              walled: calls(n), walledOk: [walled.ok, walled.lines['123'].ok],
                              memo: localStorage['kz.5ka.cart'] || null}));
})();
""")
    assert out["first"] == ["POST v8/orders/live/item/", "GET v10/orders/live/", "PUT v8/orders/live/item/123/"]
    assert out["firstLines"] == [False, 3, True]
    assert out["walled"] == ["PUT v8/orders/live/item/123/"] and out["walledOk"] == [True, False]
    assert out["memo"] is None


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_failed_peek_leaves_the_memo_it_had():
    """Чтение заранее не вышло: прежняя, ещё годная память остаётся, и правка идёт по ней одной записью."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));
  shop.getStatus = 500;
  const peek = await api.run({chain: 'pyaterochka', lines: [], mine: {}, want: {}, drop: false, pause_ms: 300,
                              burst: 10, peek: true});
  shop.getStatus = 0;
  const n = seen.length;
  const plus = await api.run(edit(3));
  console.log(JSON.stringify({peek: [peek.ok, peek.stage], calls: calls(n), q: plus.lines['123'].q,
                              busy: window.__kzPeek || null}));
})();
""")
    assert out == {"peek": [False, "read"], "calls": ["PUT v8/orders/live/item/123/"], "q": 3, "busy": None}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_peek_that_finds_the_order_placed_erases_the_memo():
    """Заказ оплатили с другого устройства, а чтение заранее это увидело: память о его корзине стёрта, и
    правка по касанию в оплаченный заказ не пишет (номер заказа сайт ещё помнит)."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  localStorage['cart-store'] = JSON.stringify({orderId: 'live'});
  await api.run(edit(2, {touch: false}));
  shop.placed = true;
  const peek = await api.run({chain: 'pyaterochka', lines: [], mine: {}, want: {}, drop: false, pause_ms: 300,
                              burst: 10, peek: true});
  const n = seen.length;
  const r = await api.run(edit(3));
  console.log(JSON.stringify({peek: [peek.ok, peek.stage], memo: localStorage['kz.5ka.cart'] || null,
                              writes: calls(n).filter(function (c) { return c.indexOf('GET') !== 0; }),
                              ok: r.ok, stage: r.stage, q: cart[123], mark: window.__kzPeekFailAt > 0}));
})();
""")
    assert out == {"peek": [False, "address"], "memo": None, "writes": [], "ok": False, "stage": "address", "q": 2,
                   "mark": True}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_reread_into_another_order_does_not_carry_the_old_refusal():
    """Писали по памяти в заказ, который успели оплатить: сеть отказала, перечитывание нашло новую корзину —
    отказ прежнего заказа к ней не относится, позиция в неё кладётся."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {deviceId: 'dev1', 'kz.5ka.order': JSON.stringify({id: 'A', at: Date.now()})};
const carts = {A: {123: 2}, B: {123: 2}};
let paidA = false;
function order(id) {
  const c = carts[id];
  const items = Object.keys(c).map(function (k) { return {product_plu: Number(k), quantity: c[k], uom: 'шт', total: 50 * c[k]}; });
  const paid = id === 'A' && paidA;
  return {id: id, is_active: true, payed_time: paid ? '2026-11-13T00:00:00' : null, status: paid ? 2 : 0,
          basket: {items: items, items_count: items.length}};
}
fakeXhr(function (u, b, m) {
  if (u.indexOf('/v3/orders/') >= 0) return {items: [order('A'), order('B')]};
  const id = /\\/orders\\/v\\d+\\/orders\\/([^/]+)\\//.exec(u)[1];
  if (m === 'GET') return order(id);
  if (id === 'A' && paidA) return {__status: 409};
  if (m === 'POST') { const p = JSON.parse(b); carts[id][p.plu] = p.qty; }
  if (m === 'PUT') { carts[id][u.split('/item/')[1].replace('/', '')] = JSON.parse(b).qty; }
  return order(id);
});
(async function () {
  const lines = [{sku: '123', target: 2}, {sku: '777', target: 1}];
  const base = {chain: 'pyaterochka', mine: {}, want: {}, drop: false, pause_ms: 300, burst: 10};
  await api.run(Object.assign({lines: [lines[0]]}, base));            // читает заказ A и запоминает корзину
  paidA = true;
  const n = seen.length;
  const r = await api.run(Object.assign({lines: lines, touch: true}, base));
  console.log(JSON.stringify({calls: seen.slice(n).map(function (s) { return s.replace('https://5d.5ka.ru/api/orders/', ''); }),
                              line: [r.lines['777'].ok, r.lines['777'].q], a: carts.A[777] || 0, b: carts.B[777] || 0}));
})();
""")
    assert out["calls"] == ["POST v8/orders/A/item/", "GET v10/orders/A/", "GET v3/orders/?in_action=true",
                            "GET v10/orders/B/", "POST v8/orders/B/item/"]
    assert out["line"] == [True, 1] and (out["a"], out["b"]) == (0, 1)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_reads_the_cart_again_after_a_write_that_answers_without_it():
    """Запись ответила без корзины (обычная сборка, с чтением): читаем корзину ещё раз, отчёт и память — по
    ней, а не по корзине до записи. Не прочиталась — не запоминаем, следующая правка по касанию читает сама;
    тело отказа (в нём бывает своё поле status) за оформленный заказ не принимаем."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  shop.barePut = true;
  const r = await api.run(edit(3, {touch: false}));
  const first = calls();
  const memo = !!localStorage['kz.5ka.cart'];
  shop.thenGet = 429;                                     // и прочитать после записи не вышло
  let n = seen.length;
  const b = await api.run(edit(4, {touch: false}));
  const blind = calls(n);
  const kept = localStorage['kz.5ka.cart'] || null;
  const order = !!localStorage['kz.5ka.order'];
  shop.barePut = false;
  shop.getStatus = 0;
  n = seen.length;
  await api.run(edit(4));
  console.log(JSON.stringify({first: first, line: [r.lines['123'].q, r.lines['123'].put], cart: r.cart['123'],
                              memo: memo, blind: blind, blindOk: [b.ok, order], kept: kept, next: calls(n)}));
})();
""")
    wrote = ["GET v10/orders/live/", "PUT v8/orders/live/item/123/", "GET v10/orders/live/"]
    assert out["first"] == wrote and out["line"] == [3, 1] and out["cart"] == 3 and out["memo"] is True
    assert out["blind"] == wrote and out["blindOk"] == [True, True], "отказ чтения — не оформленный заказ"
    assert out["kept"] is None and out["next"] == ["GET v10/orders/live/"]


def test_the_app_and_the_adapter_agree_on_the_cart_memo():
    """Приложение и адаптер называют память корзины и поля наряда одинаково, а читает приложение заранее
    чаще, чем память устаревает."""
    import re

    java_path = os.path.join(ROOT, "android", "app", "app", "src", "main", "java", "ru", "moyakorzina", "app",
                             "MainActivity.java")
    with open(java_path, encoding="utf-8") as f:
        java = f.read()
    with open(CARTAPI_JS, encoding="utf-8") as f:
        js = f.read()
    assert "var PYAT_CART = 'kz.5ka.cart';" in js and '{"pyaterochka", "kz.5ka.cart", "1"}' in java
    assert "var DIXY_CART = 'kz.dixy.cart';" in js and '{"dixy", "kz.dixy.cart", ""}' in java
    assert "var DIXY_CART_MS = PYAT_CART_MS;" in js, "срок памяти у сетей один: пороги приложения сверены с ним"
    assert "w.__kzPeekFailAt = Date.now()" in js and "window.__kzPeekFailAt" in java, "метка неудачного чтения заранее"
    assert 'rules.optBoolean("api", false)) return;' in java, "чтение заранее — только где сервер разрешил запросы сети"
    assert java.count("s.peekOwed = byTouch;") == 2, "касание не теряется, пока окно сети не готово"
    for field in ("touch", "since", "again", "peek"):
        assert f'"{field}"' in java, field
        assert re.search(rf"\.{field}\b", js), field
    ttl = int(re.search(r"PYAT_CART_MS = (\d+) \* 1000;", js).group(1)) * 1000
    ages = [int(v) for v in re.findall(r"PEEK_(?:TOUCH_)?AGE_MS = (\d+);", java)]
    assert len(ages) == 2 and max(ages) < ttl, "память должна обновляться раньше, чем устареет"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_pyaterochka_edit_waits_for_the_peek_and_writes_by_its_memo():
    """Приложение читает корзину заранее (peek): записи в нём нет, а правка, заставшая его в полёте, ждёт
    его конца и идёт одной записью."""
    out = _run_node_script(PYAT_SHOP + """
(async function () {
  const peek = api.run({chain: 'pyaterochka', lines: [], mine: {}, want: {}, drop: false, pause_ms: 300, burst: 10,
                        peek: true});
  const busy = !!window.__kzPeek;
  const plus = await api.run(edit(3));
  const seenByPeek = await peek;
  console.log(JSON.stringify({calls: calls(), busy: busy, after: window.__kzPeek, peek: [seenByPeek.ok, seenByPeek.requests],
                              plus: [plus.requests, plus.lines['123'].q]}));
})();
""")
    assert out == {"calls": ["GET v10/orders/live/", "PUT v8/orders/live/item/123/"], "busy": True, "after": None,
                   "peek": [True, 1], "plus": [1, 3]}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_asks_the_store_once_and_rereads_only_after_a_change():
    """Магазин Дикси спрашиваем раз в 6 часов, а не при каждом запуске приложения (метка в localStorage:
    первая правка после запуска шла шестью запросами, 4,4 с), а корзину перечитываем, только если меняли."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {};
let q = 2;
let cartSays = null;
fakeXhr(function (u, b) {
  const action = b.get('action');
  if (action === 'isStoreDefined') { return {storeDefined: true}; }
  if (action === 'update') { q = Number(b.get('quantity')); return {changedProducts: [{quantity: q, amount: 10}]}; }
  if (cartSays) { return cartSays; }
  return {template_data: {items: [{xml_id: '3001', id: 12345, quantity: q, ratio: 1, priceSimple: 100, canBuy: true}],
                          toPay: String(100 * q), baseBskSumm: String(100 * q), count: 1}};
});
(async function () {
  const kept = await api.run({chain: 'dixy', lines: [{sku: '3001', target: 2}], mine: {}, want: {'3001': 2},
                              drop: false, pause_ms: 300});
  const first = seen.splice(0);
  const less = await api.run({chain: 'dixy', lines: [{sku: '3001', target: 1}], mine: {'3001': 2},
                              want: {'3001': 1}, drop: true, pause_ms: 300});
  const second = seen.splice(0);
  const marked = Number(localStorage['kz.dixy.store']) > 0;
  // Метке больше шести часов — магазин спрашиваем снова; часы ушли назад — тоже.
  localStorage['kz.dixy.store'] = String(Date.now() - 7 * 3600 * 1000);
  await api.run({chain: 'dixy', lines: [{sku: '3001', target: 1}], mine: {}, want: {'3001': 1}, drop: false, pause_ms: 300});
  const old = seen.splice(0);
  localStorage['kz.dixy.store'] = String(Date.now() + 3600 * 1000);
  await api.run({chain: 'dixy', lines: [{sku: '3001', target: 1}], mine: {}, want: {'3001': 1}, drop: false, pause_ms: 300});
  const ahead = seen.splice(0);
  const rewritten = Math.abs(Date.now() - Number(localStorage['kz.dixy.store'])) < 60000;
  // Магазин на сайте сбросили при свежей метке: чтение корзины это выдаёт, записи нет, метка стёрта.
  const resets = [];
  for (const says of [{action: 'showMap'}, {}]) {
    localStorage['kz.dixy.store'] = String(Date.now());
    cartSays = says;
    const r = await api.run({chain: 'dixy', lines: [{sku: '3001', target: 3}], mine: {}, want: {'3001': 3},
                             drop: false, pause_ms: 300});
    resets.push([r.ok, r.stage, seen.splice(0).join(), 'kz.dixy.store' in localStorage]);
  }
  cartSays = null;
  localStorage['kz.dixy.store'] = 'abc';
  await api.run({chain: 'dixy', lines: [{sku: '3001', target: 1}], mine: {}, want: {'3001': 1}, drop: false, pause_ms: 300});
  console.log(JSON.stringify({first: first, second: second, kept: kept.lines['3001'].how,
                              dropped: less.dropped, q: q, marked: marked, old: old[0], ahead: ahead[0],
                              rewritten: rewritten, resets: resets, junk: seen[0]}));
})();
""")
    assert out["first"] == ["POST /ajax/ajax.php isStoreDefined", "POST /ajax/ajax.php calculateDiscounts"]
    assert out["second"] == ["POST /ajax/ajax.php calculateDiscounts", "POST /ajax/ajax.php update",
                             "POST /ajax/ajax.php calculateDiscounts"]
    assert (out["kept"], out["dropped"], out["q"]) == ("kept", 1, 1)
    assert out["marked"] is True
    assert out["old"] == out["ahead"] == out["junk"] == "POST /ajax/ajax.php isStoreDefined"
    assert out["rewritten"] is True, "после повторного вопроса метка должна стать свежей"
    assert out["resets"] == [[False, "address", "POST /ajax/ajax.php calculateDiscounts", False],
                             [False, "read", "POST /ajax/ajax.php calculateDiscounts", False]]


DIXY_SHOP = FAKE_XHR + """
global.localStorage = {'kz.dixy.store': String(Date.now()),
                       'kz.dixy.ids': JSON.stringify({'3002': {id: 12, ratio: 1}})};
const cart = {'3001': 2, '3002': 1};
const ids = {'3001': 11, '3002': 12};
const shop = {failReads: 0};
fakeXhr(function (u, b) {
  const action = b.get('action');
  const sku = Object.keys(ids).filter(function (k) { return String(ids[k]) === b.get('id'); })[0];
  if (action === 'isStoreDefined') { return {storeDefined: true}; }
  if (action === 'update') { cart[sku] = Number(b.get('quantity')); return {changedProducts: [{quantity: cart[sku], amount: 10}]}; }
  if (action === 'add') { cart[sku] = (cart[sku] || 0) + 1; return {changedProducts: [{quantity: cart[sku], amount: 10}]}; }
  if (action === 'remove') { delete cart[sku]; return {changedProducts: []}; }
  if (shop.failReads > 0) { shop.failReads--; return {__status: 500}; }
  const items = Object.keys(cart).map(function (k) {
    return {xml_id: k, id: ids[k], quantity: cart[k], ratio: 1, priceSimple: 100, canBuy: true};
  });
  const sum = items.reduce(function (a, i) { return a + 100 * i.quantity; }, 0);
  return {template_data: {items: items, toPay: String(sum), baseBskSumm: String(sum), count: items.length}};
});
function calls(from) {
  return seen.slice(from || 0).map(function (s) { return s.replace('POST /ajax/ajax.php ', ''); });
}
function edit(q, more) {
  return Object.assign({chain: 'dixy', lines: [{sku: '3001', target: q}, {sku: '3002', target: 1}], mine: {},
                        want: {'3001': q, '3002': 1}, drop: false, pause_ms: 300, burst: 10, touch: true}, more || {});
}
"""


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_touch_edit_skips_the_first_read_by_the_remembered_cart():
    """Корзину Дикси видели меньше минуты назад: правка по касанию пишет сразу и сверяется чтением после
    записи — одно чтение со скидками вместо двух. Снятие лишнего идёт так же."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));                 // обычная сборка читает и запоминает
  const n = seen.length;
  const plus = await api.run(edit(3));
  const m = seen.length;
  const less = await api.run(edit(2, {mine: {'3001': 3}, drop: true}));
  const k = seen.length;
  await api.run(edit(2, {touch: false}));                 // не по касанию — читаем, как раньше
  const j = seen.length;
  await api.run(edit(2));                                 // писать нечего — сверить память не с чем: читаем
  console.log(JSON.stringify({plus: calls(n).slice(0, m - n), less: calls(m).slice(0, k - m),
                              plain: calls(k).slice(0, j - k), idle: calls(j),
                              line: [plus.lines['3001'].how, plus.lines['3001'].put, plus.lines['3001'].q],
                              kept: plus.lines['3002'].how, total: plus.total.amount, dropped: less.dropped,
                              cart: less.cart}));
})();
""")
    assert out["plus"] == ["update", "calculateDiscounts"], "по памяти — запись и одно чтение"
    assert out["line"] == ["request", 1, 3] and out["kept"] == "kept" and out["total"] == 400
    assert out["less"] == ["update", "calculateDiscounts"] and out["dropped"] == 1
    assert out["cart"] == {"3001": 2, "3002": 1}
    assert out["plain"] == ["calculateDiscounts"] and out["idle"] == ["calculateDiscounts"]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_does_not_trust_an_old_memo_and_rereads_when_the_cart_differs():
    """Старой памяти и памяти до возвращения человека веры нет. Память разошлась с сетью (товар сняли на
    сайте) или чтение после записи не вышло — та же сборка заново, с чтением."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  function age(ms) {
    const memo = JSON.parse(localStorage['kz.dixy.cart']);
    memo.at -= ms;
    localStorage['kz.dixy.cart'] = JSON.stringify(memo);
  }
  await api.run(edit(2, {touch: false}));
  age(61 * 1000);
  let n = seen.length;
  await api.run(edit(3));
  const old = calls(n);

  n = seen.length;
  await api.run(edit(4, {since: Date.now() + 1000}));      // человек вернулся позже, чем мы видели корзину
  const away = calls(n);

  delete cart['3002'];                                     // сняли на сайте сети, память об этом не знает
  n = seen.length;
  const gone = await api.run(edit(5));
  const goneCalls = calls(n);

  shop.failReads = 1;                                      // чтение после записи не вышло
  n = seen.length;
  const blind = await api.run(edit(6));
  console.log(JSON.stringify({old: old, away: away, gone: goneCalls, goneCart: gone.cart,
                              back: [gone.lines['3002'].ok, gone.lines['3002'].how], blind: calls(n),
                              blindOk: [blind.ok, blind.lines['3001'].q], memo: !!localStorage['kz.dixy.cart']}));
})();
""")
    assert out["old"] == out["away"] == ["calculateDiscounts", "update", "calculateDiscounts"]
    assert out["gone"] == ["update", "calculateDiscounts", "calculateDiscounts", "add", "calculateDiscounts"]
    assert out["goneCart"] == {"3001": 5, "3002": 1} and out["back"] == [True, "request"]
    assert out["blind"] == ["update", "calculateDiscounts", "calculateDiscounts"]
    assert out["blindOk"] == [True, 6] and out["memo"] is True


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_add_by_an_old_memo_does_not_double_the_item():
    """Запрос add у Дикси кладёт сверх лежащего. Память отстала (товар за эту минуту положили с другого
    устройства): после add сеть показывает больше, чем просили, и число ставится целиком. Память верна —
    лишней записи нет."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  ids['3003'] = 13;
  ids['3004'] = 14;
  localStorage['kz.dixy.ids'] = JSON.stringify({'3003': {id: 13, ratio: 1}, '3004': {id: 14, ratio: 1}});
  function plus(sku) {
    const lines = [{sku: '3001', target: 2}, {sku: '3002', target: 1}, {sku: sku, target: 1}];
    const want = {'3001': 2, '3002': 1};
    want[sku] = 1;
    return edit(2, {lines: lines, want: want});
  }
  await api.run(edit(2, {touch: false}));                 // память: ни 3003, ни 3004 в корзине нет
  cart['3003'] = 1;                                        // 3003 положили с другого устройства
  let n = seen.length;
  const r = await api.run(plus('3003'));
  const stale = calls(n);
  n = seen.length;
  await api.run(plus('3004'));                             // память верна: add и чтение
  console.log(JSON.stringify({stale: stale, q: cart['3003'], line: [r.lines['3003'].ok, r.lines['3003'].q],
                              cart: r.cart, right: calls(n), q4: cart['3004']}));
})();
""")
    assert out["stale"] == ["add", "update", "calculateDiscounts"]
    assert out["q"] == 1 and out["line"] == [True, 1] and out["cart"] == {"3001": 2, "3002": 1, "3003": 1}
    assert out["right"] == ["add", "calculateDiscounts"] and out["q4"] == 1


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_edit_waits_for_the_peek_and_writes_by_its_memo():
    """Чтение заранее по касанию: записи в нём нет, правка ждёт его конца и не читает корзину второй раз.
    Корзина заранее не прочиталась (так сеть выдаёт и сброшенный магазин) — памяти веры нет: правка
    спрашивает магазин и читает сама, а приложению остаётся метка, чтобы не повторять чтение сразу."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  const look = {chain: 'dixy', lines: [], mine: {}, want: {}, drop: false, pause_ms: 300, burst: 10, peek: true};
  const peek = api.run(look);
  const busy = !!window.__kzPeek;
  const plus = await api.run(edit(3));
  const first = calls();
  const clean = window.__kzPeekFailAt === undefined;
  shop.failReads = 1;
  const failed = await api.run(look);
  const n = seen.length;
  await api.run(edit(4));
  console.log(JSON.stringify({first: first, busy: busy, peekOk: (await peek).ok, q: plus.lines['3001'].q,
                              failed: failed.ok, after: calls(n), idle: window.__kzPeek,
                              marks: [clean, window.__kzPeekFailAt > 0]}));
})();
""")
    assert out["first"] == ["calculateDiscounts", "update", "calculateDiscounts"] and out["busy"] is True
    assert out["peekOk"] is True and out["q"] == 3
    assert out["failed"] is False and out["idle"] is None and out["marks"] == [True, True]
    assert out["after"] == ["isStoreDefined", "calculateDiscounts", "update", "calculateDiscounts"]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_run_overtaken_by_the_next_edit_skips_the_total_and_leaves_the_cart_by_reckoning():
    """Серия правок (1.46): сборку догнала следующая правка — итог её корзины не читается, запрос не занимает
    место в темпе. Корзина остаётся расчётом (что лежало плюс принятые сетью записи), следующая сборка пишет
    по ней и читает итог. Замер с телефона 13.11.2026: 11 удалений подряд — отчёт Дикси через 6,2 с."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  await api.run(edit(2, {touch: false}));                 // обычная сборка читает и запоминает: 3001 ×2, 3002 ×1
  const n = seen.length;
  window.__kzNewer = 'k1';                                // приложение: пока шла сборка k1, корзину поправили снова
  const first = await api.run(edit(3, {key: 'k1'}));
  const m = seen.length;
  const memo = JSON.parse(localStorage['kz.dixy.cart']).parsed;
  const last = await api.run(edit(4, {key: 'k2'}));       // её ключ приложение не называло: итог читается
  console.log(JSON.stringify({first: calls(n).slice(0, m - n), last: calls(m),
                              stale: [first.stale === true, !!last.stale], reckoned: first.cart,
                              noTotal: first.total === null, memo: [memo.cart, memo.items['3001'].id, memo.total],
                              q: last.lines['3001'].q, total: last.total.amount, cart: last.cart}));
})();
""")
    assert out["first"] == ["update"], "итог догнанной сборки не читается"
    assert out["stale"] == [True, False] and out["noTotal"] is True
    assert out["reckoned"] == {"3001": 3, "3002": 1} and out["memo"] == [{"3001": 3, "3002": 1}, 11, None]
    assert out["last"] == ["update", "calculateDiscounts"], "следующая пишет по расчёту и читает итог"
    assert out["q"] == 4 and out["total"] == 500 and out["cart"] == {"3001": 4, "3002": 1}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_reckoned_cart_that_the_network_contradicts_is_reread_and_finished():
    """Сеть ответила на снятие «принято» и не сняла, а итог догнанной сборки не читался: расчёт разошёлся с
    сетью. Следующая сборка по нему писать нечего не находит, читает корзину и снимает сама — своё при этом
    приложение не убавляло (MainActivity.handleCartApiResult: расчёту убавлять не даём)."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  let deaf = 1;
  fakeXhr(function (u, b) {
    if (b.get('action') === 'remove') { if (deaf-- <= 0) { delete cart['3001']; } return {changedProducts: []}; }
    const items = Object.keys(cart).map(function (k) {
      return {xml_id: k, id: ids[k], quantity: cart[k], ratio: 1, priceSimple: 100, canBuy: true};
    });
    const sum = items.reduce(function (a, i) { return a + 100 * i.quantity; }, 0);
    return {template_data: {items: items, toPay: String(sum), baseBskSumm: String(sum), count: items.length}};
  });
  const gone = {chain: 'dixy', lines: [{sku: '3002', target: 1}], mine: {'3001': 2}, want: {'3002': 1},
                drop: true, pause_ms: 300, burst: 10, touch: true};
  await api.run(edit(2, {touch: false}));                 // читает и запоминает: 3001 ×2, 3002 ×1
  const n = seen.length;
  window.__kzNewer = 'k1';
  const first = await api.run(Object.assign({key: 'k1'}, gone));
  const m = seen.length;
  const last = await api.run(Object.assign({key: 'k2'}, gone));
  console.log(JSON.stringify({first: calls(n).slice(0, m - n), reckoned: first.cart, last: calls(m),
                              cart: last.cart, dropped: last.dropped, shop: cart, total: last.total.amount}));
})();
""")
    assert out["first"] == ["remove"] and out["reckoned"] == {"3002": 1}
    assert out["last"] == ["calculateDiscounts", "remove", "calculateDiscounts"], "расчёт не сошёлся — чтение и снятие"
    assert out["cart"] == {"3002": 1} and out["shop"] == {"3002": 1} and out["dropped"] == 2 and out["total"] == 100


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_dixy_series_of_overtaken_runs_reckons_a_new_item_and_ends_with_a_read():
    """Три сборки подряд (1.46). Первую догнали: новый товар кладётся, его номер в расчёт идёт из памяти
    номеров сети. Вторую тоже догнали: она пишет по расчёту первой и снимает товар одним запросом. Третья
    по расчёту писать нечего не находит и читает сеть — серия кончается корзиной и итогом из ответа сети."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  ids['3003'] = 13;
  localStorage['kz.dixy.ids'] = JSON.stringify({'3002': {id: 12, ratio: 1}, '3003': {id: 13, ratio: 1}});
  function put(q, more) {
    const lines = [{sku: '3001', target: 2}, {sku: '3002', target: 1}].concat(q ? [{sku: '3003', target: q}] : []);
    const want = Object.assign({'3001': 2, '3002': 1}, q ? {'3003': q} : {});
    return Object.assign({chain: 'dixy', lines: lines, mine: {}, want: want, drop: false, pause_ms: 300,
                          burst: 10, touch: true}, more || {});
  }
  await api.run(put(0, {touch: false}));                  // читает и запоминает: 3001 ×2, 3002 ×1
  const n = seen.length;
  window.__kzNewer = 'k1';
  const first = await api.run(put(3, {key: 'k1'}));       // новый товар ×3, сборку догнали
  const m = seen.length;
  const memo = JSON.parse(localStorage['kz.dixy.cart']).parsed;
  window.__kzNewer = 'k2';
  const second = await api.run(put(0, {key: 'k2', mine: {'3003': 3}, drop: true}));   // товар убрали, снова догнали
  const k = seen.length;
  const last = await api.run(put(0, {key: 'k3', mine: {'3003': 3}, drop: true}));
  console.log(JSON.stringify({first: calls(n).slice(0, m - n), second: calls(m).slice(0, k - m), last: calls(k),
                              item: [memo.items['3003'].id, memo.items['3003'].q, memo.reckoned],
                              stale: [first.stale === true, second.stale === true, !!last.stale],
                              carts: [first.cart, second.cart, last.cart], shop: cart, total: last.total.amount}));
})();
""")
    assert out["first"] == ["add", "update"] and out["item"] == [13, 3, True]
    assert out["second"] == ["remove"], "по расчёту первой — одно снятие, без чтения"
    assert out["last"] == ["calculateDiscounts"], "писать нечего — серия кончается чтением сети"
    assert out["stale"] == [True, True, False]
    assert out["carts"] == [{"3001": 2, "3002": 1, "3003": 3}, {"3001": 2, "3002": 1}, {"3001": 2, "3002": 1}]
    assert out["shop"] == {"3001": 2, "3002": 1} and out["total"] == 300


@pytest.mark.skipif(NODE is None, reason="node не установлен")
@pytest.mark.parametrize("late, again, in_fail", [
    ("false", ["update", "calculateDiscounts", "update"], {"3001": 2, "3002": 1}),   # повтор с чтением
    ("true", ["update"], None),                               # сборке больше 20 с — повтора нет
])
def test_dixy_break_after_a_reckoned_cart_does_not_pass_the_reckoning_for_the_network(late, again, in_fail):
    """Сеть ответила на снятие «принято» и не сняла, итог догнанной сборки не читался, а на записи следующей
    связь оборвалась. Сборка повторяется с чтением, и отказ несёт корзину из ответа сети, а не расчёт: по
    расчёту приложение убавило бы своё, и товар, который человек убрал, остался бы в корзине сети чужим.
    Повтор идёт после паузы темпа. Сборка старше 20 с не повторяется (приложение ждёт её 45 с), её отказ
    уходит без корзины. Память-расчёт в обоих случаях стёрта: следующая сборка читает сеть."""
    out = _run_node_script(DIXY_SHOP + """
(async function () {
  const realNow = Date.now;
  let skew = 0;
  Date.now = function () { return realNow() + skew; };
  let cut = false;
  let deaf = 1;
  const times = [];                                        // когда пришёл каждый запрос: строка в строку с seen
  fakeXhr(function (u, b) {
    const action = b.get('action');
    times.push(performance.now());
    if (action === 'remove') { if (deaf-- <= 0) { delete cart['3001']; } return {changedProducts: []}; }
    if (action === 'update') {
      if (cut) { if (LATE) { skew = 40000; } throw new Error('обрыв'); }
      cart['3002'] = Number(b.get('quantity'));
      return {changedProducts: [{quantity: cart['3002'], amount: 10}]};
    }
    const items = Object.keys(cart).map(function (k) {
      return {xml_id: k, id: ids[k], quantity: cart[k], ratio: 1, priceSimple: 100, canBuy: true};
    });
    const sum = items.reduce(function (a, i) { return a + 100 * i.quantity; }, 0);
    return {template_data: {items: items, toPay: String(sum), baseBskSumm: String(sum), count: items.length}};
  });
  function gone(q, key) {
    return {chain: 'dixy', lines: [{sku: '3002', target: q}], mine: {'3001': 2}, want: {'3002': q}, drop: true,
            pause_ms: 300, burst: 10, touch: true, key: key};
  }
  await api.run(edit(2, {touch: false}));                 // читает и запоминает: 3001 ×2, 3002 ×1
  window.__kzNewer = 'k1';
  const first = await api.run(gone(1, 'k1'));             // 3001 убрали; сборку догнали — корзина расчётом
  cut = true;
  const m = seen.length;
  const broke = await api.run(gone(2, 'k2'));             // пишет по расчёту, связь рвётся — повтор с чтением
  cut = false;
  const kept = 'kz.dixy.cart' in localStorage;
  const n = seen.length;
  const last = await api.run(gone(2, 'k3'));
  console.log(JSON.stringify({reckoned: first.cart, broke: [broke.ok, broke.stage, broke.cart || null],
                              again: calls(m).slice(0, n - m), kept: kept, last: calls(n), cart: last.cart,
                              shop: cart, dropped: last.dropped,
                              pause: n - m > 1 ? Math.round(times[m + 1] - times[m]) : null}));
})();
""".replace("LATE", late))
    assert out["reckoned"] == {"3002": 1}, "расчёт верит сети: 3001 снят"
    assert out["again"] == again, "после обрыва — один повтор с чтением, а старой сборке — ни одного"
    assert out["pause"] is None if in_fail is None else out["pause"] >= 250, "повтор — после паузы темпа (300 мс)"
    assert out["broke"] == [False, "net", in_fail], "в отказе корзина из чтения сети или никакой, но не расчёт"
    assert out["kept"] is False
    assert out["last"] == ["calculateDiscounts", "update", "remove", "calculateDiscounts"], "чтение показало 3001"
    assert out["cart"] == {"3002": 2} and out["shop"] == {"3002": 2} and out["dropped"] == 2


# Куки сайта Магнита: из них адаптер собирает заголовки сети (магазин, устройство, тип магазина,
# способ получения). Часть кук сайт пишет в кавычках JSON.
MAGNIT_COOKIE = "shopCode=%22777%22; mg_udi=dev9; x_shop_type=GM; nmg_dt=%22DELIVERY_TYPE_PICKUP%22"

# Корзина Магнита в памяти: PUT ставит число названным позициям (0 — убирает), ответ — вся корзина.
MAGNIT_CART = """
global.document = {cookie: COOKIE};
function cartAnswer(u, b, m) {
  if (u === '/webgate/v1/carts/lite' && m === 'PUT') {
    JSON.parse(b).items.forEach(function (it) {
      if (it.qnty) cart[it.goodId] = it.qnty; else delete cart[it.goodId];
    });
  }
  return {items: Object.keys(cart).map(function (k) { return {goodId: k, offerId: k, qnty: cart[k]}; })};
}
"""


def _magnit(body: str, cookie: str = MAGNIT_COOKIE) -> dict:
    return _run_node_script(FAKE_XHR + MAGNIT_CART.replace("COOKIE", json.dumps(cookie)) + body)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_puts_every_line_with_one_request():
    """Магнит: чтение, ОДНА запись на все позиции, итог и наличие из оформления."""
    out = _magnit("""
let cart = {'1001': 1, '1003': 3};
fakeXhr(function (u, b, m) {
  if (u === '/webgate/v1/checkout/preview') {
    return {summary: {totalFinalPrice: 51097, totalBasePrice: 49297, itemsTotalSalePrice: 41297, itemsCount: 3,
                      prices: [{title: 'Доставка', value: 5900}, {title: 'Сборка и упаковка', value: 3900}]},
            suborders: [{items: [{goodId: '1001', qnty: 2, available: true},
                                 {goodId: '1003', qnty: 3, available: false}]}],
            unavailableItems: [{goodId: '1002', qnty: 1}]};
  }
  return cartAnswer(u, b, m);
});
api.run({chain: 'magnit', mine: {}, want: {}, drop: false, burst: 10,
         lines: [{sku: '1001', target: 2}, {sku: '1002', target: 1}, {sku: '1003', target: 1}]})
  .then(function (r) { console.log(JSON.stringify({seen: seen, put: JSON.parse(sent[1].b), heads: sent[1].h, r: r})); });
""")
    assert out["seen"] == ["GET /webgate/v1/carts/lite", "PUT /webgate/v1/carts/lite",
                           "GET /webgate/v1/checkout/preview", "GET /webgate/v1/carts/lite"]
    assert out["put"] == {"items": [{"goodId": "1001", "offerId": "1001", "qnty": 2},
                                    {"goodId": "1002", "offerId": "1002", "qnty": 1}]}
    heads = out["heads"]
    # Всё из кук: магазин и устройство (без кавычек JSON), служба — по типу магазина
    # (гипермаркет — dostavka), способ получения; x-permissions-jwt просит приложить вход.
    assert (heads["x-store-code"], heads["x-device-id"]) == ("777", "dev9")
    assert (heads["x-service"], heads["x-delivery-type"]) == ("dostavka", "pickup")
    assert (heads["x-permissions-jwt"], heads["x-magnit-bff"]) == ("true", "1")
    lines = out["r"]["lines"]
    assert (lines["1001"]["ok"], lines["1001"]["put"], lines["1001"]["q"]) == (True, 1, 2)
    assert (lines["1002"]["ok"], lines["1002"]["gone"]) == (False, True)   # unavailableItems
    assert (lines["1003"]["how"], lines["1003"]["gone"]) == ("kept", True)  # available: false
    assert out["r"]["total"] == {"amount": 510.97, "goods": 412.97, "base": 492.97, "discount": 80,
                                 "delivery": 59, "count": 3, "path": "/webgate/v1/checkout/preview"}
    assert out["r"]["tpl"] is False


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_believes_the_cart_after_checkout_not_the_write_answer():
    """Расчёт оформления обнуляет то, чего в магазине нет, и урезает до остатка, а ответ на запись
    показывает записанное. Замер 13.11.2026: «легло 11 из 11» при четырёх нулях в корзине сети."""
    out = _magnit("""
let cart = {'1001': 1};
fakeXhr(function (u, b, m) {
  if (u === '/webgate/v1/checkout/preview') {
    cart['1002'] = 0;   // в магазине нет: строка остаётся с qnty 0
    cart['1003'] = 1;   // остаток — одна упаковка
    return {summary: {totalFinalPrice: 20000, totalBasePrice: 20000, itemsTotalSalePrice: 20000, itemsCount: 2}};
  }
  return cartAnswer(u, b, m);
});
api.run({chain: 'magnit', mine: {}, want: {}, drop: false, burst: 10,
         lines: [{sku: '1001', target: 1}, {sku: '1002', target: 2}, {sku: '1003', target: 4}]})
  .then(function (r) {
    const first = seen.slice();
    // Прибавлять нечего (лежит сколько надо) — второго чтения нет: корзину оформление уже правило.
    api.run({chain: 'magnit', mine: {}, want: {}, drop: false, burst: 10, lines: [{sku: '1001', target: 1}]})
      .then(function () { console.log(JSON.stringify({first: first, second: seen.slice(first.length), r: r})); });
  });
""")
    assert out["first"] == ["GET /webgate/v1/carts/lite", "PUT /webgate/v1/carts/lite",
                            "GET /webgate/v1/checkout/preview", "GET /webgate/v1/carts/lite"]
    assert out["second"] == ["GET /webgate/v1/carts/lite", "GET /webgate/v1/checkout/preview"]
    lines = out["r"]["lines"]
    assert (lines["1001"]["ok"], lines["1001"]["how"]) == (True, "kept")
    assert (lines["1002"]["ok"], lines["1002"]["gone"], lines["1002"]["put"], lines["1002"]["q"]) == (False, True, 0, 0)
    assert lines["1002"]["why"] == "нет в наличии у сети"
    assert (lines["1003"]["ok"], lines["1003"]["gone"], lines["1003"]["put"], lines["1003"]["q"]) == (False, False, 1, 1)
    assert lines["1003"]["why"] == "сеть оставила 1 из 4"
    assert [c.split(" ")[0] for c in lines["1003"]["calls"]] == ["PUT", "GET"], "в отчёте видно, откуда приговор"
    assert out["r"]["cart"] == {"1001": 1, "1002": 0, "1003": 1}, "память телефона получает то, что лежит на деле"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
@pytest.mark.parametrize("reread, verdict", [
    ("return {__status: 500};", [True, False, "", 2]),
    ("return {items: []};", [False, False, "сеть обнулила корзину при расчёте заказа", 0]),
])
def test_magnit_bad_reread_does_not_call_goods_out_of_stock(reread, verdict):
    """Чтение после оформления отказало — отчёт прежний. Пришло без единой позиции с количеством (пустой
    ответ, закрытый магазин) — позиции не легли, но «нет в наличии» по такому ответу в базу не уходит."""
    out = _magnit("""
let cart = {};
let reads = 0;
fakeXhr(function (u, b, m) {
  if (u === '/webgate/v1/checkout/preview') {
    return {summary: {totalFinalPrice: 20000, totalBasePrice: 20000, itemsTotalSalePrice: 20000, itemsCount: 1}};
  }
  if (m === 'GET' && ++reads === 2) { REREAD }
  return cartAnswer(u, b, m);
});
api.run({chain: 'magnit', mine: {}, want: {}, drop: false, burst: 10, lines: [{sku: '1001', target: 2}]})
  .then(function (r) { const l = r.lines['1001']; console.log(JSON.stringify([l.ok, l.gone, l.why, l.q])); });
""".replace("REREAD", reread))
    assert out == verdict


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_refreshes_the_login_once_on_401():
    """Вход истёк (401): один POST /_auth/refresh, как у самого сайта, и повтор чтения."""
    out = _magnit("""
let cart = {};
let fresh = false;
fakeXhr(function (u, b, m) {
  if (u === '/_auth/refresh') { fresh = true; return {authorized: true}; }
  if (u === '/webgate/v1/carts/lite' && !fresh) return {__status: 401};
  return u === '/webgate/v1/carts/lite' ? cartAnswer(u, b, m) : null;
});
api.run({chain: 'magnit', lines: [], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, ok: r.ok})); });
""")
    assert out["seen"][:3] == ["GET /webgate/v1/carts/lite", "POST /_auth/refresh",
                               "GET /webgate/v1/carts/lite"]
    assert out["ok"] is True


@pytest.mark.skipif(NODE is None, reason="node не установлен")
@pytest.mark.parametrize("cookie", ["mg_udi=dev9", "shopCode=777; x_shop_type=GM"])
def test_magnit_without_a_store_or_delivery_is_the_human_step(cookie):
    """Магазин или способ получения не выбраны — сайт и сам не кладёт: ни одного запроса, шаг человека."""
    out = _magnit("""
let cart = {};
fakeXhr(cartAnswer);
api.run({chain: 'magnit', lines: [{sku: '1001', target: 1}], mine: {}, want: {}, drop: false})
  .then(function (r) { console.log(JSON.stringify({seen: seen, ok: r.ok, stage: r.stage})); });
""", cookie)
    assert out == {"seen": [], "ok": False, "stage": "address"}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_expired_login_does_not_fill_a_guest_cart():
    """Вход истёк и не обновился: сеть отвечает 401 — записи нет, в корзину гостя не кладём."""
    out = _magnit("""
let cart = {};
fakeXhr(function (u, b, m) {
  if (u === '/_auth/refresh') return {authorized: false};
  return {__status: 401};
});
api.run({chain: 'magnit', lines: [{sku: '1001', target: 1}], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, ok: r.ok, stage: r.stage, why: r.why})); });
""")
    assert out == {"seen": ["GET /webgate/v1/carts/lite", "POST /_auth/refresh"], "ok": False,
                   "stage": "auth", "why": "Магнит: вход истёк — войдите в магазин заново"}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_drops_its_own_surplus_without_clearing_the_cart():
    """Своё снимается отдельным PUT с qnty 0: DELETE carts/lite очистил бы корзину целиком."""
    out = _magnit("""
let cart = {'1001': 1, '2002': 2};
fakeXhr(function (u, b, m) { return u === '/webgate/v1/carts/lite' ? cartAnswer(u, b, m) : null; });
api.run({chain: 'magnit', lines: [{sku: '1001', target: 2}], mine: {'2002': 2},
         want: {'1001': 2}, drop: true, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, last: sent[2].b, dropped: r.dropped, cart: r.cart})); });
""")
    assert out["seen"] == ["GET /webgate/v1/carts/lite", "PUT /webgate/v1/carts/lite",
                           "PUT /webgate/v1/carts/lite", "GET /webgate/v1/checkout/preview"]
    assert json.loads(out["last"]) == {"items": [{"goodId": "2002", "offerId": "2002", "qnty": 0}]}
    assert (out["dropped"], out["cart"]) == (2, {"1001": 2})


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_refused_write_goes_to_taps():
    """Запись не принята: отказ с ответом сети — приложение положит касаниями после отчёта."""
    out = _magnit("""
let cart = {};
fakeXhr(function (u, b, m) {
  if (u !== '/webgate/v1/carts/lite') return null;
  return m === 'PUT' ? {__status: 400, message: 'Устаревшая версия'} : cartAnswer(u, b, m);
});
api.run({chain: 'magnit', lines: [{sku: '1001', target: 1}, {sku: '1002', target: 1}],
         mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, r: r})); });
""")
    assert out["seen"] == ["GET /webgate/v1/carts/lite", "PUT /webgate/v1/carts/lite"]
    assert (out["r"]["ok"], out["r"]["stage"]) == (False, "write")
    assert out["r"]["why"] == "Магнит: сеть ответила 400: Устаревшая версия"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_weighed_goods_go_to_taps():
    """Весовой лежит долями (0,4 кг): запрос ставит упаковки, поэтому такую строку кладут касания."""
    out = _magnit("""
let cart = {'1444282012': 0.4};
fakeXhr(function (u, b, m) { return u === '/webgate/v1/carts/lite' ? cartAnswer(u, b, m) : null; });
api.run({chain: 'magnit', lines: [{sku: '1444282012', target: 1}, {sku: '1001', target: 1}],
         mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({put: JSON.parse(sent[1].b), lines: r.lines, cart: cart})); });
""")
    assert out["put"] == {"items": [{"goodId": "1001", "offerId": "1001", "qnty": 1}]}
    assert out["lines"]["1444282012"] == {"retry": True}
    assert out["cart"]["1444282012"] == 0.4


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_magnit_rereads_the_cart_when_the_write_answer_is_unclear():
    """Запись ответила 200 без корзины: не верим на слово, перечитываем корзину."""
    out = _magnit("""
let cart = {};
fakeXhr(function (u, b, m) {
  if (u !== '/webgate/v1/carts/lite') return null;
  if (m === 'PUT') { cartAnswer(u, b, m); return {}; }
  return cartAnswer(u, b, m);
});
api.run({chain: 'magnit', lines: [{sku: '1001', target: 2}], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, line: r.lines['1001']})); });
""")
    assert out["seen"][:3] == ["GET /webgate/v1/carts/lite", "PUT /webgate/v1/carts/lite",
                               "GET /webgate/v1/carts/lite"]
    assert (out["line"]["ok"], out["line"]["q"]) == (True, 2)


# METRO: черновик заказа на supergraph, суммы в копейках. Токен сайта — в странице (__NUXT__), магазин —
# кука metroStoreId.
METRO = """
global.document = {cookie: 'metroStoreId=10; other=1'};
const store = {};
global.localStorage = {getItem: function (k) { return k in store ? store[k] : null; },
                       setItem: function (k, v) { store[k] = String(v); },
                       removeItem: function (k) { delete store[k]; }};
const payload = Buffer.from(JSON.stringify({exp: 1900000000})).toString('base64').replace(/=+$/, '');
const TOKEN = 'eyJhbGciOiJSUzI1NiJ9.' + payload + '.c2ln';
function draft(cart, extra) {
  return Object.assign({order_type: 'pickup', out_of_stock: [],
    basket: {products: Object.keys(cart).map(function (k) { return {article: Number(k), quantity: cart[k], price: 12345}; })},
    totals: {total_amount: 77750, products_price: 74750, delivery_price: 3000, products_count: 3}}, extra || {});
}
function metroAnswer(cart, saveAnswer) {
  return function (u, b, m) {
    if (u === '/kz-metro-config') return '<script>window.__NUXT__={app:{marketplaceConfigToken:"' + TOKEN + '",x:1}}</script>';
    if (u !== 'https://supergraph.metro-cc.ru/graphql' || m !== 'POST') return null;
    const q = JSON.parse(b);
    if (q.query.indexOf('saveOrderDraft') < 0) return {data: {orderDraft: cart === null ? null : draft(cart)}};
    if (saveAnswer) return saveAnswer(q);
    q.variables.input.products.forEach(function (p) { if (p.quantity) cart[p.article] = p.quantity; else delete cart[p.article]; });
    return {data: {saveOrderDraft: draft(cart)}};
  };
}
"""


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_metro_puts_every_line_and_drops_its_own_with_one_mutation():
    """METRO: токен со страницы сайта, чтение черновика, ОДНА запись: число ставится, 0 убирает своё."""
    out = _run_node_script(FAKE_XHR + METRO + """
const cart = {'111': 1, '999': 2};
fakeXhr(metroAnswer(cart));
api.run({chain: 'metro', lines: [{sku: '111', target: 2}, {sku: '222', target: 1}], mine: {'999': 2}, want: {},
         drop: true, burst: 10})
  .then(function (r) {
    const writes = sent.filter(function (s) { return s.b && String(s.b).indexOf('saveOrderDraft') >= 0; });
    console.log(JSON.stringify({seen: seen, heads: sent[1].h, put: JSON.parse(writes[0].b).variables.input.products,
      writes: writes.length, lines: r.lines, dropped: r.dropped, total: r.total, cart: r.cart, saved: store['kz.metro.token']}));
  });
""")
    assert out["seen"] == ["GET /kz-metro-config", "POST https://supergraph.metro-cc.ru/graphql",
                           "POST https://supergraph.metro-cc.ru/graphql"]
    assert out["heads"]["x-store-id"] == "10" and out["heads"]["x-config-token"].startswith("eyJ")
    assert out["writes"] == 1, "все позиции и снятие своего — одним запросом, как пишет сам сайт"
    assert out["put"] == [{"article": 111, "quantity": 2}, {"article": 222, "quantity": 1},
                          {"article": 999, "quantity": 0}]
    assert (out["lines"]["111"]["ok"], out["lines"]["111"]["put"]) == (True, 1)
    assert out["lines"]["222"]["ok"] is True and out["dropped"] == 2
    assert out["cart"] == {"111": 2, "222": 1}
    assert out["lines"]["111"]["price"] == 123.45, "цена штуки из корзины сети, в копейках"
    assert (out["total"]["amount"], out["total"]["goods"], out["total"]["delivery"]) == (777.5, 747.5, 30.0)
    assert '"exp":1900000000' in out["saved"], "токен живёт в окне сети до своего срока"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_metro_without_pickup_or_delivery_asks_the_human_and_keeps_the_token():
    """Самовывоз или доставка на сайте не выбраны — сеть не сохраняет черновик: это шаг человека."""
    out = _run_node_script(FAKE_XHR + METRO + """
store['kz.metro.token'] = JSON.stringify({t: TOKEN, exp: 1900000000});
fakeXhr(metroAnswer(null, function () {
  return {errors: [{message: "Failed to fetch from Subgraph 'marketplace-production'.",
                    extensions: {errors: [{message: 'The order_type cannot be empty.',
                                           extensions: {code: 'order_type_empty'}}]}}],
          data: {saveOrderDraft: null}};
}));
api.run({chain: 'metro', lines: [{sku: '111', target: 1}], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, stage: r.stage, why: r.why, saved: !!store['kz.metro.token']})); });
""")
    assert out["seen"][0] == "POST https://supergraph.metro-cc.ru/graphql", "токен из памяти окна, страницу не тянем"
    assert out["stage"] == "address" and "самовывоз или доставка" in out["why"]
    assert out["saved"] is True


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_parse_samokat():
    """Разбор корзины Самоката: суммы сети в копейках, у нас рубли; ключ позиции — слаг."""
    fixture = {
        "status": "ACTUAL",
        "cart": {
            "cartId": "cart-123",
            "version": 5,
            "totalPrice": 61900,
            # Доплата до минимального заказа стоит в перечне, а в totalPrice не входит (замер 12.11.2026).
            "services": [{"id": "s1", "name": "Сборка и упаковка", "type": "PACKING", "totalPrice": 4900},
                         {"id": "s2", "type": "FLEXIBLE_DELIVERY", "totalPrice": 18100}],
            "products": [
                {
                    "productId": "5ee7883d", "quantity": 2, "type": "PRODUCT",
                    "price": 30000, "totalPrice": 60000, "totalDiscountAmount": 57000,
                    "product": {"uuid": "5ee7883d", "name": "Чипсы", "slug": "chipsy-225-g", "quantity": 14,
                                "prices": {"current": 30000}},
                },
                {
                    "productId": "gone-1", "quantity": 1, "type": "PRODUCT",
                    "price": 0, "totalPrice": 0, "totalDiscountAmount": 0,
                    "product": {"uuid": "gone-1", "name": "Кончился", "slug": "net-tovara", "quantity": 0,
                                "prices": {"current": 9900}},
                },
            ],
        },
    }
    js_code = (
        f"const res = api.parse.samokat({json.dumps(fixture)});\n"
        "process.stdout.write(JSON.stringify(res));"
    )
    res = _run_node_script(js_code)
    assert (res["cartId"], res["version"]) == ("cart-123", 5)
    assert res["cart"] == {"chipsy-225-g": 2, "net-tovara": 1}
    it = res["items"]["chipsy-225-g"]
    assert (it["uuid"], it["price"], it["q"], it["gone"], it["left"]) == ("5ee7883d", 300.0, 2, False, 14)
    assert res["items"]["net-tovara"]["gone"] is True, "остаток витрины — ноль"
    assert res["total"] == {"amount": 619.0, "goods": 570.0, "base": 600.0, "discount": 30.0, "delivery": 49.0,
                            "count": 3, "path": "/v2/carts/get"}


# Куки как в браузере: присваивание document.cookie ставит одну куку, чтение отдаёт все.
COOKIE_JAR = """
function cookieJar(text) {
  const jar = {};
  function put(v) { const p = String(v).split(';')[0]; const i = p.indexOf('='); jar[p.slice(0, i).trim()] = p.slice(i + 1); }
  String(text || '').split('; ').filter(Boolean).forEach(put);
  global.document = {get cookie() { return Object.keys(jar).map(function (k) { return k + '=' + jar[k]; }).join('; '); },
                     set cookie(v) { put(v); }};
  return jar;
}
"""

# Самокат: вход — тип пользователя в токене (токен сайт даёт и гостю, поле user сессии пусто и у
# вошедшего: замер 12.11.2026), витрина — по адресу из куки сайта, корзина пишется целиком с номером версии.
SAMOKAT = """
global.document = {cookie: 'a=1; SELECTED_ADDRESS_KEY=' + encodeURIComponent(JSON.stringify({lat: 55.5, lon: 37.5}))};
global.localStorage = {'X-Device-Uid': '"dev-1"'};
function token(type) {
  return 'h.' + Buffer.from(JSON.stringify({user: {userType: type}})).toString('base64url') + '.s';
}
function entry(id, slug, q, price, extra) {
  return Object.assign({productId: id, quantity: q, type: 'PRODUCT', price: price, totalPrice: price * q,
    totalDiscountAmount: price * q,
    product: {uuid: id, slug: slug, name: slug, quantity: 9, prices: {current: price}}}, extra || {});
}
function cartOf(list, version, status) {
  const sum = list.reduce(function (s, e) { return s + e.totalDiscountAmount; }, 0);
  return {status: status || 'ACTUAL', cart: {cartId: 'cart-9', version: version, totalPrice: sum + 4900,
    services: [{type: 'PACKING', totalPrice: 4900}], products: list}};
}
// state: {cart, version, shelf: {слаг: uuid}, guest, addresses, lag — первое чтение отдаёт старую версию,
//         unserved — по адресу нет магазина, stock: {слаг: остаток витрины} — больше него сеть не кладёт}
function samokatAnswer(state) {
  state.puts = [];
  return function (u, b, m) {
    if (u === '/api/auth/session') return {user: {}, accessToken: token(state.guest ? 'ANONYMOUS' : 'SAMOKAT')};
    if (u.indexOf('/showcases/list') >= 0) {
      return state.unserved ? [] : [{showcaseId: 'big', type: 'SUPERMARKET'}, {showcaseId: 'near', type: 'MINIMARKET'}];
    }
    if (u.indexOf('/users/profile') >= 0) return {addresses: state.addresses || []};
    if (u.indexOf('/v2/carts/get') >= 0) {
      const v = state.lag ? state.version - 1 : state.version;
      state.lag = false;
      return cartOf(state.cart, v);
    }
    if (u.indexOf('/products/') >= 0) {
      const slug = u.split('/products/')[1];
      return state.shelf[slug] ? {uuid: state.shelf[slug], slug: slug, quantity: 5} : {__status: 400};
    }
    if (m === 'PUT') {
      const put = JSON.parse(b);
      state.puts.push(put);
      if (put.version !== state.version) {
        return {status: 'VERSION_MISMATCH', cart: {cartId: 'cart-9', version: state.version}};
      }
      state.version += 1;
      state.cart = put.products.map(function (p) {
        const old = state.cart.filter(function (e) { return e.productId === p.productId; })[0];
        const slug = old ? old.product.slug
          : Object.keys(state.shelf).filter(function (k) { return state.shelf[k] === p.productId; })[0];
        const left = (state.stock || {})[slug];
        const made = entry(p.productId, slug, left == null ? p.quantity : Math.min(p.quantity, left), old ? old.price : 12000);
        if (left != null) made.product.quantity = left;
        return made;
      });
      return cartOf(state.cart, state.version);
    }
    return null;
  };
}
"""


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_writes_the_whole_cart_once_and_keeps_foreign_items():
    """Самокат: одна запись на всё — чужое остаётся, своё лишнее уходит, новое ищется по слагу."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
const state = {version: 5, shelf: {novoe: 'u-new'},
  cart: [entry('u-foreign', 'chuzhoe', 1, 10000), entry('u-drop', 'lishnee', 2, 5000), entry('u-have', 'est', 1, 20000)]};
fakeXhr(samokatAnswer(state));
(async function () {
  const first = await api.run({chain: 'samokat', mine: {lishnee: 2}, want: {}, drop: true, burst: 10,
    lines: [{sku: 'novoe', target: 2}, {sku: 'est', target: 3}, {sku: 'net-takogo', target: 1}]});
  const n = seen.length;
  const again = await api.run({chain: 'samokat', mine: {}, want: {}, drop: false, burst: 10,
    lines: [{sku: 'novoe', target: 3}]});
  console.log(JSON.stringify({first: seen.slice(0, n), second: seen.slice(n), heads: sent[2].h, put: state.puts[0],
    ok: first.ok, lines: first.lines, dropped: first.dropped, total: first.total, cart: first.cart,
    again: again.lines.novoe, shop: JSON.parse(localStorage['kz.samokat.showcase']).sid}));
})();
""")
    api = "https://api-web.samokat.ru"
    assert out["first"] == ["GET /api/auth/session", f"GET {api}/showcases/list?lat=55.5&lon=37.5",
                            f"POST {api}/v2/carts/get", f"GET {api}/v2/showcases/near/products/novoe",
                            f"GET {api}/v2/showcases/near/products/net-takogo", f"PUT {api}/v2/carts/cart-9"]
    assert out["shop"] == "near", "ближний магазин раньше большого, как выбирает сайт"
    assert out["heads"]["authorization"].startswith("Bearer h.") and out["heads"]["deviceid"] == "dev-1"
    assert out["heads"]["x-application-platform"] == "mWeb"
    assert out["put"] == {"version": 5, "showcaseId": "near", "offerCode": "BASIC_OFFER",
                          "embedProductsToResponse": True,
                          "products": [{"productId": "u-foreign", "quantity": 1, "type": "PRODUCT"},
                                       {"productId": "u-have", "quantity": 3, "type": "PRODUCT"},
                                       {"productId": "u-new", "quantity": 2, "type": "PRODUCT"}]}
    assert out["ok"] is True and out["dropped"] == 2
    new, have, none = out["lines"]["novoe"], out["lines"]["est"], out["lines"]["net-takogo"]
    assert (new["ok"], new["put"], new["q"], new["price"], new["how"]) == (True, 2, 2, 120.0, "request")
    assert (have["ok"], have["put"], have["q"], have["price"]) == (True, 2, 3, 200.0)
    assert (none["ok"], none["gone"], none["why"]) == (False, True, "нет в наличии у сети")
    assert out["total"]["amount"] == 989.0 and out["total"]["delivery"] == 49.0, "копейки сети — в рубли"
    assert out["cart"]["novoe"] == 2 and "lishnee" not in out["cart"]
    assert out["second"] == [f"POST {api}/v2/carts/get", f"PUT {api}/v2/carts/cart-9"], \
        "сессия и витрина — из памяти окна, uuid — из корзины"
    assert (out["again"]["ok"], out["again"]["q"]) == (True, 3)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_guest_session_is_not_a_login():
    """Токен сайт даёт и гостю: без входа корзину не трогаем, это шаг человека."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
fakeXhr(samokatAnswer({guest: true, version: 1, cart: [], shelf: {}}));
api.run({chain: 'samokat', lines: [{sku: 'foo', target: 1}]})
  .then(function (r) { console.log(JSON.stringify({seen: seen, ok: r.ok, stage: r.stage, status: r.status, why: r.why})); });
""")
    assert out["seen"] == ["GET /api/auth/session"]
    assert (out["ok"], out["stage"], out["status"]) == (False, "auth", 401)
    assert "под своим номером" in out["why"]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_rereads_the_cart_when_the_version_is_stale():
    """На устаревшую версию сеть отвечает 200 и ничего не пишет: читаем корзину и пишем ещё раз."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
const state = {version: 5, lag: true, shelf: {}, cart: [entry('u-have', 'est', 1, 20000)]};
fakeXhr(samokatAnswer(state));
api.run({chain: 'samokat', lines: [{sku: 'est', target: 2}], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) {
    console.log(JSON.stringify({versions: state.puts.map(function (p) { return p.version; }),
      reads: seen.filter(function (s) { return s.indexOf('/carts/get') >= 0; }).length, line: r.lines.est}));
  });
""")
    assert out["versions"] == [4, 5] and out["reads"] == 2
    assert (out["line"]["ok"], out["line"]["q"]) == (True, 2)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_does_not_rewrite_a_cart_with_a_combo_set():
    """Набор запросом не пересобрать, а запись идёт всем списком: такую корзину отдаём касаниям."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
const state = {version: 5, shelf: {novoe: 'u-new'},
  cart: [entry('u-have', 'est', 1, 20000), entry('u-set', 'nabor', 1, 50000, {type: 'COMBO_SET'})]};
fakeXhr(samokatAnswer(state));
(async function () {
  const kept = await api.run({chain: 'samokat', lines: [{sku: 'est', target: 1}], mine: {}, want: {}, drop: false, burst: 10});
  const more = await api.run({chain: 'samokat', lines: [{sku: 'novoe', target: 1}], mine: {}, want: {}, drop: false, burst: 10});
  console.log(JSON.stringify({kept: kept.lines.est.how, ok: more.ok, stage: more.stage, puts: state.puts.length}));
})();
""")
    assert out == {"kept": "kept", "ok": False, "stage": "write", "puts": 0}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_does_not_rewrite_a_cart_where_a_product_lies_twice():
    """Один товар двумя записями: количество по слагу занижено, перезаписали бы не то — отдаём касаниям."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
const state = {version: 5, shelf: {novoe: 'u-new'},
  cart: [entry('u-have', 'est', 1, 20000), entry('u-have', 'est', 1, 20000, {type: 'IMPULSE_PRODUCT'})]};
fakeXhr(samokatAnswer(state));
const input = {chain: 'samokat', lines: [{sku: 'novoe', target: 1}], mine: {}, want: {}, drop: false, burst: 10};
api.run(input).then(function (r) {
  // Количество не числом: сколько лежит, не знаем — тоже не переписываем.
  state.cart = [entry('u-have', 'est', '1', 20000)];
  return api.run(input).then(function (odd) {
    console.log(JSON.stringify({ok: r.ok, stage: r.stage, odd: [odd.ok, odd.stage], puts: state.puts.length}));
  });
});
""")
    assert out == {"ok": False, "stage": "write", "odd": [False, "write"], "puts": 0}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_does_not_take_credit_for_what_the_human_added_meanwhile():
    """Между отказом записи и перечитыванием человек добавил штуку сам: в «положили мы» она не идёт."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
const state = {version: 5, lag: true, shelf: {}, cart: [entry('u-have', 'est', 1, 20000)]};
const answer = samokatAnswer(state);
fakeXhr(function (u, b, m) {
  const res = answer(u, b, m);
  if (m === 'PUT' && state.puts.length === 1) state.cart = [entry('u-have', 'est', 2, 20000)];
  return res;
});
api.run({chain: 'samokat', lines: [{sku: 'est', target: 3}], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({puts: state.puts.length, line: r.lines.est})); });
""")
    assert out["puts"] == 2
    assert (out["line"]["ok"], out["line"]["q"], out["line"]["put"]) == (True, 3, 1)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_takes_the_address_from_the_profile_or_asks_the_human():
    """Адрес на устройстве не выбран: берём последний из профиля, как сайт; нет и там — шаг человека."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
global.document = {cookie: ''};
const state = {version: 5, shelf: {}, cart: [entry('u-have', 'est', 1, 20000)], addresses: []};
fakeXhr(samokatAnswer(state));
(async function () {
  const input = {chain: 'samokat', lines: [{sku: 'est', target: 1}], mine: {}, want: {}, drop: false, burst: 10};
  const none = await api.run(input);
  const n = seen.length;
  state.addresses = [{id: 3, lat: 1, lon: 2}, {id: 7, lat: 55, lon: 37}, {id: 5}];
  const found = await api.run(input);
  console.log(JSON.stringify({first: seen.slice(0, n), stage: none.stage, second: seen.slice(n), ok: found.ok}));
})();
""")
    api = "https://api-web.samokat.ru"
    assert out["first"] == ["GET /api/auth/session", f"GET {api}/users/profile"] and out["stage"] == "address"
    assert out["second"] == [f"GET {api}/users/profile", f"GET {api}/showcases/list?lat=55&lon=37",
                             f"POST {api}/v2/carts/get"]
    assert out["ok"] is True


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_selects_the_person_address_from_the_profile():
    """На сайте выбран адрес в другом городе (так было у владельца 12.11.2026): из профиля берём
    ближайший к адресу человека из наряда и выбираем его в сети кукой, как сайт. Нет такого — шаг человека."""
    out = _run_node_script(FAKE_XHR + COOKIE_JAR + SAMOKAT + """
const far = {id: 1, city: 'Москва', street: 'Мясницкая улица', lat: 55.76, lon: 37.63};
const jar = cookieJar('a=1; SELECTED_ADDRESS_KEY=' + encodeURIComponent(JSON.stringify(far)));
const state = {version: 5, shelf: {}, cart: [entry('u-have', 'est', 1, 20000)],
  addresses: [Object.assign({purposeName: 'работа'}, far),
              {id: 3, city: 'Санкт-Петербург', street: 'другая', lat: 60.02, lon: 30.0},
              {id: 2, city: 'Санкт-Петербург', street: 'проспект', house: '1', flat: null, lat: 60.0001, lon: 30.0001,
               purposeName: 'дом', isSilentDelivery: false}]};
fakeXhr(samokatAnswer(state));
(async function () {
  const input = {chain: 'samokat', lines: [{sku: 'est', target: 1}], mine: {}, want: {}, drop: false, burst: 10,
                 place: {lat: 60, lon: 30}};
  const first = await api.run(input);
  const n = seen.length;
  const raw = jar.SELECTED_ADDRESS_KEY;
  const again = await api.run(input);
  const m = seen.length;
  state.addresses = [far];
  delete localStorage['kz.samokat.showcase'];
  const none = await api.run(input);
  console.log(JSON.stringify({first: seen.slice(0, n), second: seen.slice(n, m), third: seen.slice(m),
    ok: first.ok && again.ok, picked: JSON.parse(decodeURIComponent(raw)), start: raw.slice(0, 3),
    stage: none.stage, why: none.why, kept: jar.SELECTED_ADDRESS_KEY === raw}));
})();
""")
    api = "https://api-web.samokat.ru"
    assert out["first"] == ["GET /api/auth/session", f"GET {api}/users/profile",
                            f"GET {api}/showcases/list?lat=60.0001&lon=30.0001", f"POST {api}/v2/carts/get"]
    # Перечень полей — как у сайта: служебные поля профиля в куку не идут, пустые подробности — строкой.
    assert out["picked"] == {"id": 2, "city": "Санкт-Петербург", "street": "проспект", "house": "1", "flat": "",
                             "entrance": "", "intercom": "", "floor": "", "comment": "",
                             "lat": 60.0001, "lon": 30.0001}
    assert out["start"] == "{%2", "значение куки — как пишет js-cookie сайта"
    assert out["second"] == [f"POST {api}/v2/carts/get"], "час адрес и витрина — из памяти окна"
    assert out["ok"] is True
    assert out["third"] == [f"GET {api}/users/profile"] and out["stage"] == "address"
    assert "нет адреса" in out["why"] and out["kept"] is True, "чужой адрес не выбираем и куку не трогаем"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_does_not_select_an_address_the_chain_does_not_serve():
    """По адресу человека сеть не доставляет: говорим об этом, а адрес, выбранный на сайте, не меняем."""
    out = _run_node_script(FAKE_XHR + COOKIE_JAR + SAMOKAT + """
const far = {id: 1, city: 'Москва', lat: 55.76, lon: 37.63};
const jar = cookieJar('SELECTED_ADDRESS_KEY=' + encodeURIComponent(JSON.stringify(far)));
const raw = jar.SELECTED_ADDRESS_KEY;
fakeXhr(samokatAnswer({version: 5, shelf: {}, cart: [], unserved: true,
  addresses: [far, {id: 2, city: 'Санкт-Петербург', lat: 60.0001, lon: 30.0001}]}));
api.run({chain: 'samokat', lines: [{sku: 'est', target: 1}], mine: {}, want: {}, drop: false, burst: 10,
         place: {lat: 60, lon: 30}})
  .then(function (r) { console.log(JSON.stringify({stage: r.stage, why: r.why, kept: jar.SELECTED_ADDRESS_KEY === raw})); });
""")
    assert out["stage"] == "address" and "по адресу из приложения сеть не доставляет" in out["why"]
    assert out["kept"] is True


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_samokat_names_the_stock_when_the_chain_puts_less():
    """Больше остатка витрины сеть не кладёт и отвечает как обычно (замер 12.11.2026: просили 4, легло 3):
    причина — остаток, а не «сеть не положила»."""
    out = _run_node_script(FAKE_XHR + SAMOKAT + """
const milk = entry('u-milk', 'moloko', 3, 13300);
milk.product.quantity = 3;
const state = {version: 5, shelf: {}, cart: [milk], stock: {moloko: 3}};
fakeXhr(samokatAnswer(state));
api.run({chain: 'samokat', lines: [{sku: 'moloko', target: 4}], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({asked: state.puts[0].products[0].quantity, line: r.lines.moloko})); });
""")
    line = out["line"]
    assert out["asked"] == 4, "просим, сколько нужно человеку: остаток витрины мог устареть"
    assert (line["ok"], line["q"], line["put"], line["gone"], line["why"]) == (False, 3, 0, False, "у сети осталось 3")


# Точка из наряда: магазин расчёта — склад доставки 652; у адреса человека есть ещё магазин 700.
MAGNIT_PLACE = {"shop": "652", "type": "DARKSTORE", "delivery": True, "near": ["652", "700"]}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
@pytest.mark.parametrize("cookie, deliver, moved", [
    ("mg_udi=dev9", None, True),                                                          # магазин на сайте не выбран
    ("shopCode=%22777%22; x_shop_type=GM; nmg_dt=DELIVERY_TYPE_DELIVERY", None, True),    # магазин не у адреса человека
    ("shopCode=%22700%22; x_shop_type=MM; nmg_dt=DELIVERY_TYPE_PICKUP", None, True),      # рядом, но самовывоз
    ("shopCode=%22700%22; x_shop_type=MM; nmg_dt=DELIVERY_TYPE_DELIVERY", None, False),   # рядом и с доставкой
    # Перечень магазинов с доставкой пришёл (1.55): магазин «рядом», но без доставки, экспрессом не считается.
    ("shopCode=%22700%22; x_shop_type=MM; nmg_dt=DELIVERY_TYPE_DELIVERY", ["652"], True),
    ("shopCode=%22700%22; x_shop_type=MM; nmg_dt=DELIVERY_TYPE_DELIVERY", ["652", "700"], False),
])
def test_magnit_takes_the_person_store_and_delivery_from_the_plan(cookie, deliver, moved):
    """Магазин сайта не у адреса человека или самовывоз там, где считаем доставку: переносим точку расчёта
    теми же куками, что пишет сайт. Свой выбор человека рядом с адресом и с доставкой не трогаем — если этот
    магазин доставку и правда отдаёт. Что и как взято, адаптер называет в ответе (place) — для хроники."""
    place = dict(MAGNIT_PLACE, deliver=deliver) if deliver else MAGNIT_PLACE
    body = """
const jar = cookieJar(TEXT);
let cart = {};
fakeXhr(cartAnswer);
api.run({chain: 'magnit', lines: [], mine: {}, want: {}, drop: false, burst: 10, place: PLACE})
  .then(function (r) { console.log(JSON.stringify({ok: r.ok, place: r.place, heads: sent[0].h, jar: jar})); });
""".replace("TEXT", json.dumps(cookie)).replace("PLACE", json.dumps(place))
    out = _run_node_script(FAKE_XHR + COOKIE_JAR + MAGNIT_CART.replace("COOKIE", json.dumps(cookie)) + body)
    heads, jar = out["heads"], out["jar"]
    assert out["ok"] is True
    if moved:
        assert (heads["x-store-code"], heads["x-service"], heads["x-delivery-type"]) == ("652", "express", "delivery")
        assert (jar["shopCode"], jar["x_shop_type"], jar["nmg_dt"]) == ("%22652%22", "DARKSTORE", "DELIVERY_TYPE_DELIVERY")
        assert out["place"] == "express/DARKSTORE/652/delivery"
    else:
        assert (heads["x-store-code"], heads["x-service"], heads["x-delivery-type"]) == ("700", "express", "delivery")
        assert jar["shopCode"] == "%22700%22" and jar["x_shop_type"] == "MM"
        assert out["place"] == "express/MM/700/delivery"


# Впрок: чтение корзины GET dropDownV2 (positions), запись PUT positionV2 (в ответе вся корзина, items). Куки сайта:
# XSRF-TOKEN уходит заголовком X-XSRF-TOKEN, deliveryTypeId — в заголовок и в тело записи. unknown — артикулы, на которые
# сайт отвечает 429 и не пишет ничего; extra — свои поля позиции (isFractional, isInStock, ...).
VPROK = """
global.document = {cookie: 'deliveryTypeId=8; XSRF-TOKEN=tok%3D%3D; other=1'};
const READ = '/web/api/v1/checkout/dropDownV2';
const WRITE = '/web/api/v1/checkout/positionV2';
function vprokAnswer(cart, opts) {
  opts = opts || {};
  const extra = opts.extra || {};
  function rows(shape) {
    return Object.keys(cart).map(function (k) {
      const row = shape === 'read'
        ? {count: cart[k], amount: cart[k], price: 99.99, isFractional: false, isNotAvailable: false, product: {id: Number(k)}}
        : {productId: Number(k), count: cart[k], price: 99.99, isFractional: false, isInStock: true};
      return Object.assign(row, extra[k]);
    });
  }
  return function (u, b, m) {
    if (u === READ && m === 'GET') {
      if (opts.read) return opts.read();
      return Object.keys(cart).length ? {totalRub: 100, totalDime: '00', options: {minSum: 700}, positions: rows('read')}
                                      : {totalRub: 0, totalDime: '00', options: {minSum: 700}};
    }
    if (u === WRITE && m === 'PUT') {
      const items = JSON.parse(b).items;
      if (items.some(function (i) { return (opts.unknown || []).indexOf(i.productId) >= 0; })) return {__status: 429};
      items.forEach(function (i) { if (i.count) cart[i.productId] = i.count; else delete cart[i.productId]; });
      return {count: Object.keys(cart).length, totalRub: 100, totalDime: '00', changes: [], items: rows('write')};
    }
    return null;
  };
}
"""
VPROK_GET = "GET /web/api/v1/checkout/dropDownV2"
VPROK_PUT = "PUT /web/api/v1/checkout/positionV2"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_parse_vprok():
    """Разбор ответов Впрока: чтение (positions) и запись (items); цена за штуку, весовое и недоступное — отдельно."""
    out = _run_node_script("""
const read = {totalRub: 3659, totalDime: '00', options: {}, positions: [
  {count: 2, price: 179.99, isNotAvailable: false, isFractional: false, product: {id: 1408997}},
  {count: 1.5, price: 50, isNotAvailable: false, isFractional: true, product: {id: 5}},
  {count: 1, price: 10, isNotAvailable: true, isFractional: false, product: {id: 6}}]};
const write = {count: 1, totalRub: 359, totalDime: '00', changes: [2],
  items: [{productId: 7, count: 3, price: 10, isInStock: false, isFractional: false}]};
console.log(JSON.stringify({read: api.parse.vprok(read), write: api.parse.vprok(write),
  empty: api.parse.vprok({totalRub: 0, totalDime: '00', options: {}}), page: api.parse.vprok({}), none: api.parse.vprok(null)}));
""")
    assert out["read"] == {"cart": {"1408997": 2, "5": 2, "6": 1}, "frac": {"5": True},
                           "prices": {"1408997": 179.99, "5": 50, "6": 10}, "gone": {"6": True}}
    assert out["write"] == {"cart": {"7": 3}, "frac": {}, "prices": {"7": 10}, "gone": {"7": True}}
    assert out["empty"]["cart"] == {} and out["page"] is None and out["none"] is None, "страница защиты — не корзина"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_vprok_puts_every_line_and_drops_its_own_with_one_write():
    """Впрок: чтение корзины и ОДНА запись на все позиции и снятие своего (число ставится, 0 убирает), заголовки как у сайта."""
    out = _run_node_script(FAKE_XHR + VPROK + """
const cart = {'111': 1, '999': 2};
fakeXhr(vprokAnswer(cart));
api.run({chain: 'vprok', lines: [{sku: '111', target: 2}, {sku: '222', target: 1}], mine: {'999': 2}, want: {},
         drop: true, burst: 10})
  .then(function (r) {
    console.log(JSON.stringify({seen: seen, read: sent[0].h, write: sent[1].h, body: JSON.parse(sent[1].b),
      lines: r.lines, dropped: r.dropped, total: r.total, cart: r.cart, ok: r.ok}));
  });
""")
    assert out["ok"] is True and out["seen"] == [VPROK_GET, VPROK_PUT]
    assert out["write"]["X-XSRF-TOKEN"] == "tok==", "кука XSRF-TOKEN — декодированной, как её читает сайт"
    assert out["write"]["x-api-context-delivery-type-id"] == "8" and out["write"]["Content-Type"] == "application/json"
    assert "Content-Type" not in out["read"]
    assert out["body"] == {"items": [{"productId": 111, "count": 2, "isPackage": False},
                                     {"productId": 222, "count": 1, "isPackage": False},
                                     {"productId": 999, "count": 0, "isPackage": False}], "lastMileTypeId": 8}
    assert (out["lines"]["111"]["ok"], out["lines"]["111"]["put"], out["lines"]["111"]["price"]) == (True, 1, 99.99)
    assert out["lines"]["222"]["ok"] is True and out["dropped"] == 2
    assert out["cart"] == {"111": 2, "222": 1}
    assert out["total"] is None, "итога с доставкой в ответах Впрока нет: сервер возьмёт цены позиций"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_vprok_touch_edit_is_one_write_by_the_remembered_cart():
    """Правка по касанию через минуту-другую после сборки не читает корзину: ответ записи — вся корзина. Человек вернулся
    позже памяти (since) или касания не было — читаем."""
    out = _run_node_script(FAKE_XHR + VPROK + """
const cart = {};
fakeXhr(vprokAnswer(cart));
const one = function (target, extra) {
  return Object.assign({chain: 'vprok', lines: [{sku: '111', target: target}], mine: {}, want: {}, drop: false, burst: 10}, extra);
};
(async function () {
  await api.run(one(1, {}));
  const afterFirst = seen.length;
  await api.run(one(2, {touch: true, since: 0}));
  const afterTouch = seen.length;
  await api.run(one(3, {touch: true, since: Date.now() + 5000}));
  const afterSince = seen.length;
  await api.run(one(4, {touch: false}));
  console.log(JSON.stringify({seen: seen, afterFirst: afterFirst, afterTouch: afterTouch, afterSince: afterSince, cart: cart}));
})();
""")
    assert out["afterFirst"] == 2, "первая сборка: чтение и запись"
    assert out["seen"][2] == VPROK_PUT and out["afterTouch"] == 3, "касание: одна запись без чтения"
    assert out["seen"][3:5] == [VPROK_GET, VPROK_PUT] and out["afterSince"] == 5, "память старше возвращения человека — читаем"
    assert out["seen"][5:] == [VPROK_GET, VPROK_PUT], "без касания — читаем всегда"
    assert out["cart"] == {"111": 4}


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_vprok_refused_write_is_repeated_one_line_at_a_time():
    """Сайт отвечает 429 на запись с неизвестным артикулом и не пишет в ней ничего: пишем по одной позиции, негодная
    получает свой отказ, остальные ложатся."""
    out = _run_node_script(FAKE_XHR + VPROK + """
const cart = {};
fakeXhr(vprokAnswer(cart, {unknown: [777]}));
api.run({chain: 'vprok', lines: [{sku: '111', target: 1}, {sku: '777', target: 1}, {sku: '222', target: 2}],
         mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, ok: r.ok, lines: r.lines, cart: r.cart})); });
""")
    assert out["ok"] is True
    assert out["seen"] == [VPROK_GET, VPROK_PUT, VPROK_PUT, VPROK_PUT, VPROK_PUT], "пачка и три одиночные записи"
    assert out["cart"] == {"111": 1, "222": 2}
    assert out["lines"]["111"]["ok"] is True and out["lines"]["222"]["ok"] is True
    bad = out["lines"]["777"]
    assert bad["ok"] is False and bad["retry"] is False and "не принял" in bad["why"]


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_vprok_429_on_the_read_is_a_wall_and_nothing_is_written():
    """429 на чтении — стена защиты, а не неизвестный товар: запись не идёт, приложение получает stage wall."""
    out = _run_node_script(FAKE_XHR + VPROK + """
fakeXhr(vprokAnswer({}, {read: function () { return {__status: 429}; }}));
api.run({chain: 'vprok', lines: [{sku: '111', target: 1}], mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({seen: seen, ok: r.ok, stage: r.stage, status: r.status})); });
""")
    assert out["seen"] == [VPROK_GET]
    assert (out["ok"], out["stage"], out["status"]) == (False, "wall", 429)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_vprok_weighed_and_unknown_goods_go_to_taps_and_a_missing_one_is_named():
    """Весовое и не артикул — касаниями (retry); товар, которого сайт не отдаёт, в отчёте назван отсутствующим."""
    out = _run_node_script(FAKE_XHR + VPROK + """
const cart = {'333': 1};
fakeXhr(vprokAnswer(cart, {extra: {'333': {isFractional: true}, '444': {isInStock: false}}}));
api.run({chain: 'vprok', lines: [{sku: '333', target: 2}, {sku: 'abc', target: 1}, {sku: '444', target: 1}],
         mine: {}, want: {}, drop: false, burst: 10})
  .then(function (r) { console.log(JSON.stringify({ok: r.ok, lines: r.lines, body: JSON.parse(sent[1].b).items})); });
""")
    assert out["lines"]["333"] == {"retry": True} and out["lines"]["abc"] == {"retry": True}
    assert out["body"] == [{"productId": 444, "count": 1, "isPackage": False}]
    gone = out["lines"]["444"]
    assert (gone["ok"], gone["gone"], gone["why"]) == (False, True, "нет в наличии у сети")
