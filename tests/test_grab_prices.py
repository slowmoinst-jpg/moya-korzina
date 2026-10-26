"""Закладка «Забрать цены»: собранная версия и границы, которые она не переходит.

ПОЧЕМУ У ЗАКЛАДКИ ВООБЩЕ ЕСТЬ СТОРОЖ. Её исходник читаемый, а в браузер уходит
собранная строка, и разойтись они могут молча: правка в исходнике есть, а в
закладке у человека — прошлая версия, и никто этого не заметит, потому что
закладка продолжает работать. Поэтому проверяется не «файл существует», а
совпадение собранного с исходником по опорным местам.

ВТОРАЯ ПОЛОВИНА ЗДЕСЬ ВАЖНЕЕ ПЕРВОЙ. Закладка живёт на чужой странице, где рядом
лежат куки, токены и кабинет человека. Сторожа ниже стоят на том, чего она делать
не должна: секрет в адресной строке, отправка без нажатия, чтение хранилищ.

ДВА ФОРМАТА АДРЕСА, И ОБА ЖИВЫЕ (19.10.2026). У Пятёрочки артикул напечатан в
адресе карточки цифрами, у Самоката его в адресе нет вовсе — только слаг. Пока
разбор знал один формат, витрина Самоката не собиралась совсем: карточка
пропускалась, пакет уходил пустым. Поэтому здесь три сторожа на разбор: собранная
закладка обязана быть свежей сборкой исходника, слаг и цифры разбираются (прогон
skuOf в node), а на выдуманном слепке витрины закладка действительно приносит
пакет с обоими видами артикула — в настоящем Chrome.

ФАСОВКА И ЕДИНИЦА — ПРО ДЕНЬГИ (19.10.2026). Вес мерился по названию, из которого
хвост фасовки уже срезали: в пакете он выходил пустым у всех позиций, а единицы не
было вовсе. `app/cartplan.py` по весу упаковки считает, сколько упаковок положить
(0,4 кг сыра по 200 г — две, а не одна), и по `unit` отличает весовой товар от
штучного. Поэтому у веса и единицы свои сторожа, и стоят они на РАЗНЫХ ВИДАХ
товара, а не на одном удачном примере: фасовка в хвосте, штучная фасовка, развес и
приписка «Цена за 100 г» (она не фасовка пачки).

МИШУРА ВИТРИНЫ В ИМЕНИ — ПРО СОПОСТАВЛЕНИЕ (19.10.2026). Живой сбор телефоном дал
имена «-52% Нет оценок Сыр Galbani … 250г», и с ними matcher не склеивал строку
Пятёрочки с чистыми названиями тех же товаров у других сетей: связи не
подтверждались, наряд оставался пустым. Сторож поэтому смотрит на ИМЯ, а не на факт
приёма пакета, — и отдельно на то, что чистка не съела название: процент в середине
(«Сыр Российский 50%»), «Комбо» без числа и карточка вовсе без мишуры.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "grab-prices.src.js")
PLAIN = os.path.join(ROOT, "docs", "grab-prices.min.txt")
PAGE = os.path.join(ROOT, "docs", "grab-prices.html")
SHELF = os.path.join(ROOT, "tests", "fixtures", "grab_prices_shelf.html")

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def built() -> str:
    with open(PLAIN, encoding="utf-8") as fh:
        raw = fh.read()
    assert raw.startswith("javascript:"), "закладка должна быть javascript:-ссылкой"
    return urllib.parse.unquote(raw[len("javascript:"):])


@pytest.fixture(scope="module")
def source() -> str:
    with open(SRC, encoding="utf-8") as fh:
        return fh.read()


def test_the_built_one_is_one_line_and_not_empty(built):
    """Адрес закладки однострочный: перевод строки рвёт её молча."""
    assert "\n" not in built and len(built) > 500


def test_the_built_one_matches_the_source(built, source):
    """Опорные места исходника обязаны быть в собранном — иначе он отстал.

    Проверяются не все строки подряд, а те, без которых закладка меняет смысл:
    какие сети она обслуживает и в какую дверь стучится.
    """
    for anchor in ("pyaterochka", "samokat", "/api/prices"):
        assert anchor in source, f"опора «{anchor}» пропала из исходника"
        assert anchor in built, ("собранная закладка отстала от исходника — "
                                 "пересобрать: python tools/build_bookmarklet.py tseny")


def test_it_serves_exactly_the_two_chains_the_door_accepts(built):
    """Дверь принимает пакеты только от Пятёрочки и Самоката — закладка тоже.

    Разойдись они, человек собрал бы витрину третьей сети и получил отказ уже
    после всей работы.
    """
    from app.pricebundle import BROWSER_STORES

    for chain in BROWSER_STORES:
        assert chain in built, f"закладка не знает сеть «{chain}», а дверь её принимает"
    assert "lenta" not in built and "magnit" not in built, \
        "закладка обслуживает сеть, пакеты от которой дверь не примет"


def test_the_secret_never_goes_into_the_address(built):
    """Секрет уезжает ТЕЛОМ запроса, и никогда параметром адреса.

    В адресной строке он осел бы в истории браузера и в журнале сервера навсегда,
    и дверь приложения такой запрос отвергает со словами «смените секрет»
    (app/api.py). Закладка обязана не доводить до этого.
    """
    assert "secret=" not in built, "секрет собрался в параметр адреса"
    assert re.search(r'method:\s*"POST"', built), "пакет уходит не телом запроса"
    # Секрет спрашивается и тут же используется — в хранилище страницы его нет.
    assert not re.search(r'setItem\(\s*"[^"]*secret', built, re.I), \
        "секрет сохраняется в хранилище чужой страницы"


def test_it_does_not_read_other_peoples_keys(built):
    """Из чужой страницы берётся витрина, а не её хранилища и куки."""
    for forbidden in ("document.cookie", "sessionStorage", "auth.token", "Authorization"):
        assert forbidden not in built, f"закладка трогает «{forbidden}»"


def test_nothing_is_sent_without_a_press(built, source):
    """Отправка висит на кнопке, а не на загрузке страницы.

    Закладка, которая шлёт пакет сама, — это уже не инструмент человека, а
    сборщик, работающий за его спиной.
    """
    fetch_at = built.find("/api/prices")
    assert fetch_at > 0
    # Отправка живёт внутри обработчика кнопки «Отправить в приложение».
    assert "Отправить в приложение" in source
    assert built.index("Отправить в приложение") < fetch_at, \
        "запрос к двери стоит раньше кнопки, которая его запускает"


def test_the_page_carries_the_built_link():
    """Страница несёт ТУ ЖЕ ссылку, что лежит в собранном файле, а не прошлую.

    Сборщик вставляет закладку в страницу между метками, и страница может
    отстать от собранного файла: человек тащит в закладки ссылку со страницы, а
    приложение на экране показывает строку из файла — и это уже две разные
    закладки. Поэтому сверяем их буквально.
    """
    with open(PAGE, encoding="utf-8") as fh:
        page = fh.read()
    with open(PLAIN, encoding="utf-8") as fh:
        ready = fh.read().strip()
    assert "Забрать цены" in page, "ссылка «Забрать цены» пропала со страницы"
    found = re.search(r'href="(javascript:[^"]+)"', page)
    assert found, "закладка не собрана — python tools/build_bookmarklet.py tseny"
    assert found.group(1) == ready, "страница отстала от собранного файла — пересобрать"


def test_the_builder_knows_every_bookmarklet():
    """Сборщик один на все закладки: два разошлись бы в первый же месяц.

    Третья, «Передать вход» (vhod), появилась 20.09.2026 из стены, а не из
    удобства: окно магазина на сервере не смогло войти в Магнит — сначала
    проверка Yandex SmartCaptcha, потом «Аккаунт заблокирован», — тогда как с
    телефона владельца тот же аккаунт открывается как обычно.
    """
    import tools.build_bookmarklet as builder

    assert set(builder.BOOKMARKLETS) == {"cheki", "tseny", "vhod"}
    for spec in builder.BOOKMARKLETS.values():
        assert os.path.exists(spec["src"]), f"исходник {spec['src']} пропал"
        assert os.path.exists(spec["page"]), f"страница {spec['page']} пропала"


def test_the_image_carries_the_built_bookmarklet():
    """Закладку надо положить В ОБРАЗ, иначе на сервере её нет вовсе.

    Замер 19.09.2026: в образе лежал только grab.min.txt — Dockerfile копировал
    ровно один файл. Собранная закладка цен была в репозитории и отсутствовала на
    сервере, и заметить это можно было только заглянув в контейнер.
    """
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
        docker = fh.read()
    assert "docs/grab-prices.min.txt" in docker, \
        "собранная закладка цен не копируется в образ — на сервере её не будет"
    assert "docs/grab.min.txt" in docker, "закладка чеков пропала из образа"
    assert "docs/hand.min.txt" in docker, \
        "собранная закладка «Передать вход» не копируется в образ"


# ---------- два формата адреса карточки: Пятёрочка и Самокат ----------

def test_the_built_one_is_freshly_built_from_the_source():
    """Собранный файл — ровно сборка нынешнего исходника.

    Тот же сборщик, что и у людей: если исходник поправили, а файл не пересобрали,
    человек получит прошлую закладку, и ни один сторож на «опорные слова» этого не
    поймает — слова-то на месте. Поэтому здесь не слова, а вся строка целиком.
    """
    import tools.build_bookmarklet as builder

    with open(PLAIN, encoding="utf-8") as fh:
        ready = fh.read().strip()
    assert ready == builder.build(SRC), \
        "собранная закладка отстала от docs/grab-prices.src.js — " \
        "пересобрать: python tools/build_bookmarklet.py tseny"


def _function_source(code: str, name: str) -> str:
    """Вырезать из собранного кода функцию целиком — по балансу скобок.

    Нужна именно функция ИЗ СОБРАННОГО: проверять разбор надо там, где он уезжает
    к человеку, а не в читаемом исходнике, который мог разойтись со сборкой.
    """
    start = code.index("function " + name + "(")
    opened = code.index("{", start)
    depth = 0
    for i in range(opened, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[start:i + 1]
    raise AssertionError(f"в собранной закладке не нашлась целая функция {name}")


@pytest.mark.skipif(NODE is None, reason="node не установлен; разбор проверит прогон в Chrome")
def test_the_second_address_format_of_samokat_is_understood(built, tmp_path):
    """Слаг Самоката и цифры Пятёрочки разбираются одинаково уверенно.

    Прогоняем НАСТОЯЩИЙ skuOf из собранной закладки, а не его пересказ на Python:
    пересказ разошёлся бы с закладкой молча и зеленел бы сам по себе.
    """
    cases = [
        # Самокат: артикула в адресе нет, ключ — слаг. Три слага взяты из живого
        # замера 19.10.2026 на витрине Самоката (WebView телефона).
        ["/product/khurma-korolyok-500-g", "khurma-korolyok-500-g"],
        ["/product/energetik-adrenaline-rush-0-449-l", "energetik-adrenaline-rush-0-449-l"],
        ["https://samokat.ru/product/omlet-roll-creative-kitchen-s-file-polosatogo-tuntsa-200-g",
         "omlet-roll-creative-kitchen-s-file-polosatogo-tuntsa-200-g"],
        # хвосты адреса к товару не относятся и ключ портить не должны
        ["/product/khurma-korolyok-500-g?utm_source=shelf", "khurma-korolyok-500-g"],
        ["/product/khurma-korolyok-500-g#otzyvy", "khurma-korolyok-500-g"],
        # Пятёрочка: прежний формат обязан остаться цифрами
        ["/product/хлеб--78001656/", "78001656"],
        ["/product/moloko-3-2--78001657/?utm_source=shelf", "78001657"],
        ["https://5ka.ru/product/khleb--78001656/", "78001656"],
        # ни карточки, ни слага — ключа нет
        ["/product/", None],
        ["/catalog/moloko/", None],
    ]
    script = tmp_path / "sku_of.js"
    script.write_text(
        _function_source(built, "skuOf") + "\n"
        "var cases = " + json.dumps(cases, ensure_ascii=False) + ";\n"
        "process.stdout.write(JSON.stringify(cases.map(function (c) { return skuOf(c[0]); })));\n",
        encoding="utf-8")
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", check=False)
    assert done.returncode == 0, f"node не осилил разбор: {done.stderr}"
    got = json.loads(done.stdout)
    for (href, want), have in zip(cases, got):
        assert have == want, f"{href}: получено {have!r}, ждали {want!r}"


def _run_in_chrome(built: str) -> dict:
    """Прогнать собранную закладку на слепке витрины в настоящем Chrome.

    ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ПРОГОНА skuOf. Тот проверяет разбор адреса, а здесь
    работает вся закладка: она читает карточки со страницы, складывает пакет и
    отправляет его нажатием. Именно так ловится ошибка, которой не видно в
    отдельной функции, — например, что карточка без цены всё-таки попала в пакет.

    СЕТЬ НЕ ТРОГАЕМ: страница витрины подменяется ответом на лету (route), чужие
    запросы Chrome обрываются, а отправка пакета перехватывается — до приложения
    не доходит ничего. Таймер, prompt и fetch закладка получает параметрами
    обёртки: подмену видит только она, а собственный механизм playwright (он сам
    зовёт setTimeout) остаётся цел.

    Chrome берём системный (channel="chrome"): он есть у человека, и это тот же
    браузер, в котором закладка живёт. Нет Chrome или playwright — сторож честно
    пропускается, а не зеленеет впустую.
    """
    playwright = pytest.importorskip("playwright.sync_api",
                                     reason="playwright не установлен")
    with open(SHELF, encoding="utf-8") as fh:
        shelf = fh.read()

    answers = ["http://app.example", "79990000031", "test-secret-123"]
    wrapped = ("(function () {\n"
               "  var answers = " + json.dumps(answers, ensure_ascii=False) + ";\n"
               "  window.__packet = null;\n"
               "  window.__fetchUrl = null;\n"
               "  (function (setTimeout, prompt, fetch) {\n" + built + "\n"
               "  })(\n"
               "    function (fn) { fn(); return 0; },\n"
               "    function () { return answers.shift() || null; },\n"
               "    function (url, init) {\n"
               "      window.__fetchUrl = String(url);\n"
               "      window.__packet = JSON.parse(init.body);\n"
               "      return Promise.resolve({ ok: true, json: function () {\n"
               "        return Promise.resolve({ prices_saved: window.__packet.items.length });\n"
               "      } });\n"
               "    });\n"
               "})();\n")

    def serve(route):
        """Слепок вместо живой витрины; всё остальное — обрыв, в сеть не ходим."""
        if urllib.parse.urlparse(route.request.url).hostname == "5ka.ru":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=shelf)
        else:
            route.abort()

    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001 — нет Chrome: это не поломка закладки
            pytest.skip(f"в системе нет Chrome для playwright (channel=chrome): {exc}")
        try:
            context = browser.new_context()
            context.route("**/*", serve)
            page = context.new_page()
            page.goto("https://5ka.ru/catalog/slepok-vitriny/")
            page.evaluate(wrapped)
            text = page.evaluate("document.body.innerText")
            page.locator("button", has_text="Отправить в приложение").click()
            page.wait_for_function("window.__packet !== null", timeout=10000)
            return {"page_text": text,
                    "url": page.evaluate("window.__fetchUrl"),
                    "packet": page.evaluate("window.__packet")}
        finally:
            browser.close()


@pytest.fixture(scope="module")
def shelf_run(built) -> dict:
    """Один прогон закладки в Chrome на слепке — на все сторожа слепка.

    Chrome поднимается один раз: проверок на слепке несколько, а состояние
    страницы у них общее, и три запуска браузера стоили бы трёх секунд на каждый
    набор. Нет Chrome — сторожа честно пропускаются (pytest.skip внутри прогона).
    """
    return _run_in_chrome(built)


def test_the_harvested_packet_carries_both_kinds_of_article(shelf_run):
    """На слепке витрины закладка приносит и цифры Пятёрочки, и слаг Самоката.

    Это и есть приёмка: до правки разбора слаг не находился, карточки Самоката
    пропускались, и пакет уходил пустым — при полностью здоровой на вид закладке.
    """
    run = shelf_run
    packet = run["packet"]
    items = {item["sku"]: item for item in packet["items"]}

    assert packet["store"] == "pyaterochka", "название сети берётся из адреса страницы"
    assert "Готово: 11 позиций с ценами." in run["page_text"], \
        f"панель сказала не то: {run['page_text'][:200]}"

    # Пятёрочка: прежний формат — цифрами, и адрес карточки цел вместе с query.
    assert "78001656" in items, "числовой артикул Пятёрочки больше не разбирается"
    assert items["78001656"]["name"] == "Хлеб Бородинский нарезной"
    assert items["78001656"]["price"] == 42.99
    assert items["78001657"]["url"].endswith("/?utm_source=shelf"), \
        "адрес карточки потерял query — по нему её и открывают"

    # Самокат: ключ — слаг из адреса, без query и #.
    assert items["khurma-korolyok-500-g"]["name"] == "Хурма Королёк"
    assert items["khurma-korolyok-500-g"]["price"] == 189
    assert items["energetik-adrenaline-rush-0-449-l"]["sku"] == \
        "energetik-adrenaline-rush-0-449-l", "query и # попали в ключ"
    assert "omlet-roll-creative-kitchen-s-file-polosatogo-tuntsa-200-g" in items

    # Обманки: карточка без цены и карточка с пустым адресом в пакет не идут.
    assert "bez-ceny-123-g" not in items, "карточка без цены попала в пакет"
    assert len(items) == 11, f"в пакете лишние позиции: {sorted(items)}"

    # Секрет уходит телом запроса и нигде не светится в адресе.
    assert packet["secret"] == "test-secret-123", "секрет не доехал телом запроса"
    assert packet["workplace"] == "79990000031"
    assert run["url"] == "http://app.example/api/prices"
    assert "test-secret-123" not in run["url"], "секрет собрался в параметр адреса"


def test_the_harvested_names_are_clean(shelf_run):
    """На слепке имена позиций приходят без мишуры витрины.

    Живой сбор 19.10.2026 (телефон владельца) принёс строки, начинающиеся со
    скидки, оценки и метки «Комбо». В базе они не ломали показ — они ломали
    СОПОСТАВЛЕНИЕ: строка «-52% Нет оценок Сыр Galbani … 250г» не склеивалась с
    чистой «Сыр Galbani … 250г» у Ленты и METRO, связь не подтверждалась, и наряд
    оставался пустым. Поэтому смотрим именно имя, а не факт приёма пакета.
    """
    items = {item["sku"]: item for item in shelf_run["packet"]["items"]}

    assert items["78001660"]["name"] == "Сыр Galbani Маскарпоне 80% БЗМЖ 250г"
    assert items["78001661"]["name"] == "Суп гороховый с копченостями Пятёрочка Кафе 250г"
    assert items["78001662"]["name"] == "Томатная паста Помидорка 250мл"
    for sku in ("78001660", "78001661", "78001662"):
        for tinsel in ("Нет оценок", "оценок", "Комбо", "-52%", "-22%", "4,95", "4,84"):
            assert tinsel not in items[sku]["name"], f"{sku}: в имени осталось «{tinsel}»"

    # Цена и фасовка при чистке не пострадали: мишура ушла, товар остался.
    assert items["78001660"]["price"] == 629
    assert items["78001660"]["weight_g"] == 250

    # Карточка без мишуры обязана остаться прежней — иначе чистка ест название.
    assert items["78001656"]["name"] == "Хлеб Бородинский нарезной"


def test_the_clean_names_reach_the_database(shelf_run, monkeypatch, tmp_path):
    """Чистое имя нужно не в пакете, а в базе: по нему matcher ищет тот же товар.

    Проверяем ровно то, на чём встал живой сбор: в store_products должно лежать
    «Сыр Galbani Маскарпоне 80% БЗМЖ 250г», а не «-52% Нет оценок Сыр Galbani …».
    Именно эту строку обход втягивает в единый каталог, и по ней refresh.resolve
    ищет товар вместе с другими сетями.
    """
    stored = _stored_in_the_database(monkeypatch, tmp_path, shelf_run["packet"])

    assert stored["78001660"]["raw_name"] == "Сыр Galbani Маскарпоне 80% БЗМЖ 250г"
    assert stored["78001661"]["raw_name"] == "Суп гороховый с копченостями Пятёрочка Кафе 250г"
    assert stored["78001662"]["raw_name"] == "Томатная паста Помидорка 250мл"
    for sku in ("78001660", "78001661", "78001662"):
        assert "оценок" not in stored[sku]["raw_name"], f"{sku}: мишура уехала в базу"


def test_the_packet_carries_the_packaging_and_the_unit(shelf_run, monkeypatch, tmp_path):
    """На слепке в пакете есть вес и единица — от них зависит число упаковок.

    До правки вес приходил пустым у ВСЕХ позиций, хотя фасовка стояла в тексте
    карточки прямо: наряд положил бы одну упаковку там, где нужно две.
    """
    packet = shelf_run["packet"]
    items = {item["sku"]: item for item in packet["items"]}

    # Фасовка в хвосте названия — вес в граммах, товар штучный.
    assert items["78001656"]["weight_g"] == 400
    assert items["78001657"]["weight_g"] == 900, "миллилитры обязаны стать граммами"
    assert items["khurma-korolyok-500-g"]["weight_g"] == 500
    assert items["energetik-adrenaline-rush-0-449-l"]["weight_g"] == 449, \
        "0,449 л — это 449 г, а не 0,449"
    assert items["omlet-roll-creative-kitchen-s-file-polosatogo-tuntsa-200-g"]["weight_g"] == 200
    for sku in ("78001656", "78001657", "khurma-korolyok-500-g",
                "energetik-adrenaline-rush-0-449-l",
                "omlet-roll-creative-kitchen-s-file-polosatogo-tuntsa-200-g"):
        assert items[sku]["unit"] == "pcs", f"{sku}: фасовка названа, значит штучный"

    # «Цена за 100 г» — не фасовка пачки: у сыра вес 200 г, а не 100.
    assert items["78001659"]["weight_g"] == 200, "приписка о цене за 100 г уехала в вес"
    assert items["78001659"]["name"] == "Сыр Страчателла"

    # Штучная фасовка веса не даёт — и это правильный ответ, а не потеря.
    assert items["78001658"]["weight_g"] is None, "«10 шт» стало граммами"
    assert items["78001658"]["unit"] == "pcs"

    # Развес: веса нет, единица ведомая — иначе расчёт возьмёт цену за упаковку.
    assert items["sliva-kg"]["weight_g"] is None
    assert items["sliva-kg"]["unit"] == "kg", "товар на развес уехал штучным"

    # И то же самое доезжает до БАЗЫ: именно из store_products.weight_g расчёт и
    # берёт, сколько упаковок положить. Ответ двери «принято 11» выглядел бы
    # одинаково и при пустом весе, поэтому смотрим строку, а не ответ.
    stored = _stored_in_the_database(monkeypatch, tmp_path, packet)
    assert (stored["78001656"]["weight_g"], stored["78001656"]["unit"]) == (400, "pcs")
    assert (stored["khurma-korolyok-500-g"]["weight_g"],
            stored["khurma-korolyok-500-g"]["unit"]) == (500, "pcs")
    assert (stored["78001659"]["weight_g"], stored["78001659"]["unit"]) == (200, "pcs"), \
        "в базу уехал вес из приписки «за 100 г»"
    assert (stored["78001658"]["weight_g"], stored["78001658"]["unit"]) == (None, "pcs")
    assert (stored["sliva-kg"]["weight_g"], stored["sliva-kg"]["unit"]) == (None, "kg"), \
        "весовой товар лёг в базу штучным"


def _stored_in_the_database(monkeypatch, tmp_path, packet: dict) -> dict:
    """Отправить пакет закладки в дверь приложения и вернуть строки из store_products.

    Тем же путём, что и в жизни: POST /api/prices с рабочим местом и секретом, потом
    импорт. Рабочее место временное — свой корень и своя папка пользователей.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    from app import api, repo, users

    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    web = application.test_client()

    phone = "79990000031"
    from conftest import sign_in

    sign_in(web, phone)
    users.open_workspace(phone)
    body = dict(packet, workplace=phone, secret=api.workplace_secret())
    answer = web.post("/api/prices", json=body)
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]

    # Рабочее место открываем СНОВА: запрос живёт в своём и закрывает его за собой,
    # а личная база у каждого места своя — без этого чтение попало бы в пустую общую.
    users.open_workspace(phone)
    store = repo.get_store(packet["store"])
    with repo.get_conn() as conn:
        rows = conn.execute(
            "SELECT sku, weight_g, unit, raw_name FROM store_products WHERE store_id=?",
            (store.id,)).fetchall()
    users.deactivate()
    return {row["sku"]: {"weight_g": row["weight_g"], "unit": row["unit"],
                         "raw_name": row["raw_name"]} for row in rows}


def _run_card_js(built: str, tmp_path, script_name: str, cases: list, body: str,
                 functions: tuple = ("cleanName", "weightOf", "soldByWeight", "readCard")) -> list:
    """Прогнать НАСТОЯЩИЕ функции из собранной закладки в node на списке случаев.

    Функции вырезаются из собранной строки, а не берутся из читаемого исходника:
    проверять надо то, что уезжает к человеку. Пересказ правил на Python разошёлся
    бы с закладкой молча и зеленел бы сам по себе.
    """
    script = tmp_path / script_name
    script.write_text(
        "\n".join(_function_source(built, name) for name in functions) + "\n"
        "var cases = " + json.dumps(cases, ensure_ascii=False) + ";\n"
        "process.stdout.write(JSON.stringify(cases.map(" + body + ")));\n",
        encoding="utf-8")
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", check=False)
    assert done.returncode == 0, f"node не осилил разбор: {done.stderr}"
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None,
                    reason="node не установлен; вес и единицу проверит прогон в Chrome")
def test_the_packaging_and_the_unit_are_read_from_the_card_text(built, tmp_path):
    """Вес и единица считаются по ЦЕЛОМУ тексту карточки, а не по срезанному имени.

    Прогон в Chrome проверяет весь путь, но пропускается там, где Chrome нет, — а
    эта ошибка (#17) и была в одной строке порядка вызовов. Поэтому тот же вопрос
    задаётся точечно: настоящие readCard, weightOf и soldByWeight из собранной
    закладки, на текстах карточек всех четырёх видов.
    """
    cases = [
        # текст карточки, имя, вес, единица
        ["4,85 Хлеб Бородинский нарезной 400 г 42 99 ₽", "Хлеб Бородинский нарезной", 400, "pcs"],
        ["4,7 Молоко 3,2% питьевое 900 мл 89 90 ₽", "Молоко 3,2% питьевое", 900, "pcs"],
        ["Хурма Королёк 500 г 189 00 ₽", "Хурма Королёк", 500, "pcs"],
        ["5 Энергетик Adrenaline Rush 0,449 л 99 00 ₽", "Энергетик Adrenaline Rush", 449, "pcs"],
        # приписка «Цена за 100 г» снимается ДО измерения, иначе вес был бы 100
        ["4,6 Сыр Страчателла 200 г Цена за 100 г 46 99 ₽", "Сыр Страчателла", 200, "pcs"],
        # нет фасовки — это НЕ развес: у буханки вес пуст, но товар штучный
        ["Хлеб нарезной 45 00 ₽", "Хлеб нарезной", None, "pcs"],
        # штучная фасовка веса не даёт
        ["Яйцо С1 10 шт 129 90 ₽", "Яйцо С1", None, "pcs"],
        # развес: метка словом, а не отсутствием фасовки
        ["Слива, кг 249 90 ₽", "Слива, кг", None, "kg"],
        ["Бананы весовые 89 90 ₽", "Бананы весовые", None, "kg"],
        # «1 кг» на витрине — фасовка, а не развес (правило app/matcher/normalize.py)
        ["Сахар-песок 1 кг 89 90 ₽", "Сахар-песок", 1000, "pcs"],
    ]
    got = _run_card_js(
        built, tmp_path, "card.js", cases,
        "function (c) { var card = readCard(c[0]); if (!card) return null;\n"
        "  return [card.name, card.weight_g, soldByWeight(card.name) ? \"kg\" : \"pcs\"]; }")
    for (text, name, grams, unit), have in zip(cases, got):
        assert have == [name, grams, unit], f"{text}: получено {have}, ждали {[name, grams, unit]}"


@pytest.mark.skipif(NODE is None,
                    reason="node не установлен; имена проверит прогон в Chrome")
def test_the_showcase_tinsel_is_cleaned_from_the_name(built, tmp_path):
    """Мишура витрины снимается в НАЧАЛЕ имени — и только там.

    Три первые строки — живые, из сбора 19.10.2026 (телефон владельца, 28 карточек
    Пятёрочки). Остальные — границы правила: процент в середине названия это часть
    товара, «Комбо» без числа бывает настоящим набором, а имя без мишуры обязано
    остаться ровно таким, каким пришло.
    """
    cases = [
        # живой сбор: скидка, «Нет оценок», метка «Комбо», голая оценка
        ["-52% Нет оценок Сыр Galbani Маскарпоне 80% БЗМЖ 250г 250 г 629 00 ₽",
         "Сыр Galbani Маскарпоне 80% БЗМЖ 250г"],
        ["Комбо 4,84 Суп гороховый с копченостями Пятёрочка Кафе 250г 250 г 129 00 ₽",
         "Суп гороховый с копченостями Пятёрочка Кафе 250г"],
        ["-22% 4,95 Томатная паста Помидорка 250мл 250 мл 205 00 ₽",
         "Томатная паста Помидорка 250мл"],
        # минус другим начертанием и пробел перед процентом — та же скидка
        ["−52 % Нет оценок Творог 5% 200 г 129 00 ₽", "Творог 5%"],
        # «N оценок»; одинокое «оценок» остаётся, когда число съел разбор цены
        ["12 оценок Молоко 3,2% 900 мл 89 90 ₽", "Молоко 3,2%"],
        # процент В СЕРЕДИНЕ названия — часть товара, его не срезаем
        ["Сыр Российский 50% 200 г 199 00 ₽", "Сыр Российский 50%"],
        ["Молоко 3,2% питьевое 900 мл 89 90 ₽", "Молоко 3,2% питьевое"],
        # «Комбо» без числа не снимаем: под этим словом бывает настоящий набор
        ["Комбо-набор для двоих 250 г 129 00 ₽", "Комбо-набор для двоих"],
        # карточка без мишуры обязана остаться прежней
        ["4,85 Хлеб Бородинский нарезной 400 г 42 99 ₽", "Хлеб Бородинский нарезной"],
    ]
    got = _run_card_js(
        built, tmp_path, "name.js", cases,
        "function (c) { var card = readCard(c[0]); return card ? card.name : null; }")
    for (text, name), have in zip(cases, got):
        assert have == name, f"{text}: получено {have!r}, ждали {name!r}"
