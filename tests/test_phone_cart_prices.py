"""Цены позиций из ответа сети с корзиной — проверка того JS, что уезжает в телефоне.

ЗАЧЕМ ИМЕННО ТАК. Цены корзины читает скрипт в приложении (MainActivity, `whole()`);
проверить его на живом телефоне можно только с непустой корзиной нужной сети, а это
редкий случай. Поэтому берём НАСТОЯЩИЙ JS из исходника приложения и прогоняем в Node на
ответах, какими их отдают сети: цена за упаковку, цена строки целиком (делим на
количество), ответ без цены — и следим, что лишнего скрипт не берёт.

Цель (решение владельца 18.10.2026): наличие и цены приложение получает, формируя корзину
в фоне. У Перекрёстка, Vprok и Монетки обход отдаёт только названия — их цена видна
только в корзине.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

NODE = shutil.which("node")
SOURCE = os.path.join(ROOT, "android", "app", "app", "src", "main", "java",
                      "ru", "moyakorzina", "app", "MainActivity.java")


def _watcher_js() -> str:
    """Настоящий JS функции whole() из исходника приложения — до её закрывающей скобки.

    Кусок берём ровно один: дальше в том же скрипте идут другие функции (они трогают
    XMLHttpRequest и в Node не живут), а нам нужна только сборка корзины и цен.
    """
    with open(SOURCE, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = next(i for i, line in enumerate(lines) if "function whole(path,t){" in line)
    chunks = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("+"):
            # Внутри склейки бывают пояснения (// …) и пустые строки: это не конец куска.
            if chunks and not stripped.startswith("//") and stripped:
                break
            continue
        body = stripped[1:].strip()
        if body.startswith('"') and body.endswith('"'):
            body = body[1:-1]
        chunks.append(body)
        if "__kzCart={at:Date.now()" in body:
            break
    js = "".join(chunks)
    # Экранирование строк Java -> JavaScript: кавычки и обратные слэши.
    js = js.replace('\\"', '"')
    return re.sub(r"\\(?![dwsWbB])", "", js)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_prices_are_taken_from_the_cart_answer(tmp_path):
    js = _watcher_js()
    assert "prices" in js, "сторож корзины больше не собирает цены"

    harness = (
        "var window={};var location={href:'https://5ka.ru/cart'};"
        "function own(){return true;}function tpl(p){return String(p||'');}"
        "function keep(){}\n"
        + js + "\n"
        "var answer=JSON.stringify({items:["
        "{goodId:'111',qnty:2,price:99.9},"
        "{productId:'222',quantity:3,totalPrice:450},"
        "{sku:'333',qty:1},"
        "{plu:'444',qty:1,price:0},"
        "{id:'555',qty:1,price:10}"
        "]});\n"
        "whole('/cart', answer);\n"
        "process.stdout.write(JSON.stringify(window.__kzCart));\n")
    script = tmp_path / "watcher.js"
    script.write_text(harness, encoding="utf-8")

    done = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", check=False)
    assert done.returncode == 0, f"node не осилил сторож: {done.stderr}"
    cart = json.loads(done.stdout)

    # «id» без уточнения артикулом не считается: под ним у сети бывает что угодно.
    assert cart["items"] == {"111": 2, "222": 3, "333": 1, "444": 1}
    # Цена за упаковку — как названа; цена строки делится на количество.
    assert cart["prices"]["111"] == 99.9
    assert cart["prices"]["222"] == 150.0
    assert "333" not in cart["prices"], "без цены — цены нет"
    assert "444" not in cart["prices"], "ноль не цена"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_absurd_prices_are_not_taken(tmp_path):
    """Слишком большая «цена» — не цена упаковки: в единую базу такое не понесём."""
    js = _watcher_js()
    harness = (
        "var window={};var location={href:'https://5ka.ru/cart'};"
        "function own(){return true;}function tpl(p){return String(p||'');}"
        "function keep(){}\n"
        + js + "\n"
        "whole('/cart', JSON.stringify({items:[{goodId:'1',qnty:1,price:99999999}]}));\n"
        "process.stdout.write(JSON.stringify(window.__kzCart));\n")
    script = tmp_path / "watcher2.js"
    script.write_text(harness, encoding="utf-8")
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    cart = json.loads(done.stdout)
    assert cart["items"] == {"1": 1}
    assert cart["prices"] == {}
