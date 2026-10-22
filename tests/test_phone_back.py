"""«Назад» с экрана чеков после окна ФНС ведёт в корзину, а не закрывает приложение.

ЗАЧЕМ ЭТОТ СТОРОЖ. После задачи 20 приложение точечно чистит историю WebView, когда
человек уходит из окна ФНС (clearHistoryOnce) на `/phone/receipts`. История пуста — и
системная «назад» на экране чеков не находит, куда идти, и приложение закрывается.
Человек шёл за ключом кабинета, а не закрывать приложение: «назад» должно вести в
корзину — она и есть главный экран телефона (`/` в приложении переводит туда же).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Android-сборку в питоновских тестах не запустить (нет ни
gradle, ни эмулятора), поэтому проверка статическая — как в tests/test_fns_window.py:
в исходнике должно быть ровно то, на что опирается обещание.

- «назад», когда идти по истории некуда и открыта НАША страница, зовёт
  `openOurApp("/phone/basket")` и выходит из обработчика;
- путь берётся из `pageUrl` (или хелпера над ним), а не из `web.getUrl()`: WebView
  в этом файле трогают только в разрешённом потоке, а адрес уже записан колбэками;
- сама корзина (`/phone/basket`) и её корень (`/phone/`) исключены — иначе «назад»
  с корзины открывал бы корзину вечно;
- режим ФНС не тронут: «назад» в кабинете по-прежнему ведёт на `/phone/receipts`;
- обычный выход (`super.onBackPressed()`) остаётся, когда человек уже в корзине.

ЖИВОЕ ПОВЕДЕНИЕ ЗДЕСЬ НЕ ПРОВЕРЯЕТСЯ И НЕ МОЖЕТ: кнопку «назад» нажимает человек
на телефоне. Проверено только то, что код к этому готов.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "android", "app", "app", "src", "main", "java",
                    "ru", "moyakorzina", "app", "MainActivity.java")

BASKET = "/phone/basket"
PHONE_ROOT = "/phone/"
RECEIPTS = "/phone/receipts"
BACK = "public void onBackPressed("


def source() -> str:
    with open(JAVA, encoding="utf-8") as fh:
        return fh.read()


def strip_comments(src: str) -> str:
    """Вырезать // и /* */, не трогая строковые и символьные литералы.

    Длина и переводы строк сохраняются, чтобы позиции в вырезанном тексте
    оставались осмысленными. Строки важны: в них бывают и «//», и скобки.
    """
    out: list[str] = []
    i = 0
    while i < len(src):
        ch = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""
        if ch == "/" and nxt == "/":
            while i < len(src) and src[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < len(src):
                if src[i] == "*" and i + 1 < len(src) and src[i + 1] == "/":
                    out.append("  ")
                    i += 2
                    break
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            i += 1
            while i < len(src):
                c = src[i]
                out.append(c)
                if c == "\\" and i + 1 < len(src):
                    out.append(src[i + 1])
                    i += 2
                    continue
                i += 1
                if c == quote:
                    break
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def body_of(src: str, start: int) -> str:
    """Текст в фигурных скобках, начиная с первой «{» после start."""
    opening = src.find("{", start)
    assert opening >= 0, "у метода нет тела"
    depth = 0
    i = opening
    while i < len(src):
        ch = src[i]
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < len(src):
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[opening + 1:i]
        i += 1
    raise AssertionError("тело метода не закрылось")


def method_body(clean: str, marker: str) -> str:
    start = clean.find(marker)
    assert start >= 0, f"не найден метод: {marker}"
    return body_of(clean, start)


def back() -> str:
    return method_body(strip_comments(source()), BACK)


# ---------- корзина вместо закрытия ----------
def test_back_without_history_on_our_page_opens_the_basket():
    """Идти назад некуда, но страница наша — «назад» ведёт в корзину и выходит."""
    body = back()
    assert "pageOurs" in body, "onBackPressed не сверяется с тем, чья страница открыта"
    assert "!web.canGoBack()" in body, \
        "нет ветки «идти назад некуда»: приложение закроется на экране чеков"
    assert 'openOurApp("/phone/basket")' in body, \
        "«назад» без истории не ведёт в корзину"
    assert re.search(r'openOurApp\("/phone/basket"\)\s*;\s*return\s*;', body), \
        "после открытия корзины обработчик не выходит — сработает и обычный выход"
    assert body.index("!web.canGoBack()") < body.index('openOurApp("/phone/basket")'), \
        "корзина открывается раньше проверки истории"
    assert "super.onBackPressed()" in body, \
        "пропал обычный выход: из корзины приложение больше не закрыть"


def test_the_path_is_taken_from_pageurl_not_from_the_webview():
    """Адрес окна — только из pageUrl: getUrl() из этого блока трогает WebView."""
    clean = strip_comments(source())
    body = method_body(clean, BACK)
    assert "getUrl" not in body, \
        "onBackPressed читает web.getUrl(): адрес окна уже записан в pageUrl"
    assert "pathOfPage()" in body, "путь страницы берётся не хелпером"
    assert "pageUrl" in body or "pathOfPage()" in body, \
        "путь не берётся из pageUrl"

    helper = method_body(clean, "private String pathOfPage(")
    assert "pageUrl" in helper, "хелпер пути читает не pageUrl"
    assert "getUrl" not in helper, "хелпер пути трогает WebView"
    assert "new URL(" in helper or "new URI(" in helper, \
        "путь не разбирается ни URL, ни URI"


def test_the_basket_itself_is_not_where_back_goes():
    """С корзины «назад» — обычный выход, а не бесконечное «снова в корзину»."""
    body = back()
    assert f'"{BASKET}".equals(path)' in body, "корзина не исключена из цели «назад»"
    assert f'"{PHONE_ROOT}".equals(path)' in body, \
        "корень телефонных экранов не исключён — «назад» зациклится на корзине"
    assert body.index(f'"{BASKET}".equals(path)') < body.index('openOurApp("/phone/basket")'), \
        "исключение корзины стоит после открытия корзины"


# ---------- режим ФНС не тронут ----------
def test_back_in_the_cabinet_still_goes_to_phone_receipts():
    """В кабинете ФНС «назад» — наш экран чеков, и эта ветка по-прежнему первая."""
    clean = strip_comments(source())
    body = method_body(clean, BACK)
    assert "fnsMode" in body, "onBackPressed не знает про кабинет ФНС"
    assert "leaveFns()" in body, "onBackPressed уводит в историю налоговой"
    assert body.index("fnsMode") < body.index("canGoBack"), \
        "сначала история, потом режим ФНС"
    assert body.index("leaveFns()") < body.index('openOurApp("/phone/basket")'), \
        "ветка ФНС перестала быть первой: кабинет поведёт в корзину"

    leave = method_body(clean, "private void leaveFns(")
    assert f'openOurApp("{RECEIPTS}")' in leave, \
        "уход из кабинета ведёт не на экран чеков"
