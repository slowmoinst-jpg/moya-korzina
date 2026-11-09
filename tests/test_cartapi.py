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
    """Правила api для телефона: Перекрёсток, Дикси — включены, Магнит — выключен."""
    assert rules("perekrestok")["api"] is True
    assert rules("dixy")["api"] is True
    assert rules("magnit")["api"] is False
    assert rules("perekrestok")["api_burst"] == 3


def _run_node_script(js_code: str) -> dict:
    """Выполнить короткий фрагмент в Node и вернуть разобранный JSON из stdout."""
    script = (
        f"const api = require({json.dumps(CARTAPI_JS.replace(os.sep, '/'))});\n"
        f"{js_code}\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", check=False)
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


# Поддельный XMLHttpRequest для run(): отвечает телом по адресу и записывает, куда ходили.
FAKE_XHR = """
global.window = {};
const seen = [];
function fakeXhr(answer) {
  global.XMLHttpRequest = function () {
    const x = this;
    x.open = function (m, u) { x.u = u; seen.push(m + ' ' + u); };
    x.setRequestHeader = function () {};
    x.send = function (b) {
      if (b && b.get) { seen[seen.length - 1] += ' ' + b.get('action'); }
      const body = answer(x.u, b);
      x.status = body === null ? 404 : 200;
      x.responseText = body === null ? '' : JSON.stringify(body);
      setTimeout(function () { x.onload(); }, 0);
    };
  };
}
"""


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
def test_dixy_asks_the_store_once_and_rereads_only_after_a_change():
    """Магазин Дикси спрашиваем раз за жизнь окна, а корзину перечитываем, только если меняли."""
    out = _run_node_script(FAKE_XHR + """
global.localStorage = {};
global.sessionStorage = {};
let q = 2;
fakeXhr(function (u, b) {
  const action = b.get('action');
  if (action === 'isStoreDefined') { return {storeDefined: true}; }
  if (action === 'update') { q = Number(b.get('quantity')); return {changedProducts: [{quantity: q, amount: 10}]}; }
  return {template_data: {items: [{xml_id: '3001', id: 12345, quantity: q, ratio: 1, priceSimple: 100, canBuy: true}],
                          toPay: String(100 * q), baseBskSumm: String(100 * q), count: 1}};
});
(async function () {
  const kept = await api.run({chain: 'dixy', lines: [{sku: '3001', target: 2}], mine: {}, want: {'3001': 2},
                              drop: false, pause_ms: 300});
  const first = seen.splice(0);
  const less = await api.run({chain: 'dixy', lines: [{sku: '3001', target: 1}], mine: {'3001': 2},
                              want: {'3001': 1}, drop: true, pause_ms: 300});
  console.log(JSON.stringify({first: first, second: seen, kept: kept.lines['3001'].how,
                              dropped: less.dropped, q: q}));
})();
""")
    assert out["first"] == ["POST /ajax/ajax.php isStoreDefined", "POST /ajax/ajax.php calculateDiscounts"]
    assert out["second"] == ["POST /ajax/ajax.php calculateDiscounts", "POST /ajax/ajax.php update",
                             "POST /ajax/ajax.php calculateDiscounts"]
    assert (out["kept"], out["dropped"], out["q"]) == ("kept", 1, 1)
