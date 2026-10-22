"""Мост приложения отвечает только своему сайту, а не чужой странице.

ЗАЧЕМ ЭТО СТОРОЖ. В WebView одно окно, и window.Korzina виден всякой странице,
которая в нём открыта: и нашему сайту, и странице сети, и любому счётчику на ней.
Методы моста умеют отдать адрес сервера, версию, то, что собирается в фоне, и
открыть страницу по просьбе — этого довольно, чтобы чужой скрипт узнал установку
и поговорил с ней от её имени. До 21.10.2026 единственной защитой был режим сети
(currentChain), а он снимается по факту загрузки и на мгновение расходится с
адресом окна. Теперь каждый метод начинается с проверки адреса (bridgeAllowed).

ПОЧЕМУ ПРОВЕРКА СТАТИЧЕСКАЯ, А НЕ ЖИВАЯ. Запускать Android-сборку в наборе
питоновских тестов нечем: здесь нет ни эмулятора, ни gradle. Но обещание у моста
проверяемое и без запуска: в файле должно быть так, чтобы каждый метод с
@JavascriptInterface сначала спрашивал адрес, а уж потом делал своё.

ВТОРАЯ ПОЛОВИНА, 21.10.2026. Методы @JavascriptInterface исполняются на потоке
JavaBridge, а WebView.getUrl() с API 18 проверяет поток и бросает RuntimeException.
Поэтому bridgeAllowed() не имеет права звать getUrl(): адрес запоминается на
главном потоке в колбэках WebViewClient (pageUrl/pageOurs), а мост читает только
эти volatile-поля. Иначе каждый вызов моста падал бы, а обёртки на странице молча
возвращали бы null/false. Тесты ниже держат обе половины: и «сначала проверка», и
«проверка не трогает WebView».
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "android", "app", "app", "src", "main", "java",
                    "ru", "moyakorzina", "app", "MainActivity.java")

# Что метод моста отвечает, когда спрашивает чужой. Пустое значение, а не отказ
# исключением: JS на странице ждёт объект или строку и разбирает JSON.parse,
# поэтому «{}» — это «ничего не знаю», а пустая строка сломала бы разбор.
SAFE = {
    "info": 'return "{}";',
    "openStore": "return false;",
    "openApp": "return;",
    "openFns": "return;",
    "collect": 'return "{}";',
    "busy": 'return "{}";',
}

# Первое действие тела: проверка адреса и пустой ответ по типу метода.
GUARD = re.compile(r'\s*if\s*\(\s*!bridgeAllowed\(\)\s*\)\s*return\b([^;]*);')

# Аннотация, за которой идёт сигнатура метода. Другие аннотации между ними не
# мешают (вдруг появится @SuppressWarnings), а упоминание в комментарии не
# засчитывается: комментарии вырезаются до разбора.
SIGNATURE = re.compile(
    r"\s*(?:@\w+(?:\s*\([^)]*\))?\s*)*"
    r"public\s+[\w<>\[\],.]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[^{]+)?\{"
)

CALLBACK = re.compile(
    r"public\s+void\s+"
    r"(onPageStarted|onPageFinished|doUpdateVisitedHistory|onPageCommitVisible)"
    r"\s*\([^)]*\)\s*\{"
)


def source() -> str:
    with open(JAVA, encoding="utf-8") as fh:
        return fh.read()


def strip_comments(src: str) -> str:
    """Вырезать // и /* */, не трогая строковые и символьные литералы.

    Длина и переводы строк сохраняются, чтобы позиции в вырезанном тексте
    оставались осмысленными. Строки важны: в них бывают и «//», и фигурные
    скобки, которые не должны считаться ни комментарием, ни телом.
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
    """Текст в фигурных скобках, начиная с первой «{» после start.

    Считаем скобки, а не ищем отступ: метод переживёт переформатирование, а
    сломанная проверка не должна зеленеть из-за пробелов. Скобки внутри строк
    не считаются.
    """
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


def annotated_methods(src: str) -> list[tuple[str, str]]:
    """Пары «имя метода — его тело» для всех настоящих @JavascriptInterface.

    Разбираем по аннотациям, а не по подстрокам: комментарий с текстом
    «@JavascriptInterface» или «bridgeAllowed» не должен ни добавлять метод,
    ни засчитывать охрану.
    """
    clean = strip_comments(src)
    found: list[tuple[str, str]] = []
    for match in re.finditer(r"@JavascriptInterface\b", clean):
        signature = SIGNATURE.match(clean, match.end())
        assert signature, "у @JavascriptInterface не разобрать сигнатуру метода"
        found.append((signature.group(1), body_of(clean, signature.start())))
    return found


def call_arguments(src: str, callee: str) -> list[str]:
    """Аргументы всех вызовов callee (callee — с открывающей скобкой)."""
    result: list[str] = []
    i = 0
    while True:
        i = src.find(callee, i)
        if i < 0:
            return result
        opening = i + len(callee) - 1
        depth = 0
        j = opening
        while j < len(src):
            ch = src[j]
            if ch in ('"', "'"):
                quote = ch
                j += 1
                while j < len(src):
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == quote:
                        j += 1
                        break
                    j += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    result.append(src[opening + 1:j])
                    i = j + 1
                    break
            j += 1
        else:
            return result


def test_the_bridge_file_is_where_we_think_it_is():
    assert os.path.exists(JAVA), f"нет файла моста: {JAVA}"


def test_the_parser_does_not_count_comments_as_annotations_or_guards():
    """Самопроверка сторожа: комментарий с охраной — не охрана."""
    fake = """
    class X {
        // @JavascriptInterface
        @JavascriptInterface
        public String info() {
            // if (!bridgeAllowed()) return "{}";
            return "{}";
        }
    }
    """
    methods = annotated_methods(fake)
    assert [name for name, _ in methods] == ["info"], "комментарий стал методом"
    assert not GUARD.match(methods[0][1]), "комментарий с bridgeAllowed засчитан"


def test_the_page_state_is_volatile_and_starts_empty():
    """Адрес окна и признак «наш» — volatile: пишет главный поток, читает мост."""
    clean = strip_comments(source())
    assert re.search(r'private\s+volatile\s+String\s+pageUrl\s*=\s*""\s*;', clean), \
        "pageUrl не заведён volatile-полем с пустым началом"
    assert re.search(r"private\s+volatile\s+boolean\s+pageOurs\s*=\s*false\s*;", clean), \
        "pageOurs не заведён volatile-полем с ложным началом"


def test_every_bridge_method_asks_whose_page_is_open_first():
    """В каждом методе с @JavascriptInterface проверка адреса — ПЕРВЫМ делом."""
    methods = annotated_methods(source())
    names = [name for name, _ in methods]
    assert set(names) == set(SAFE), f"набор методов моста изменился: {names}"
    for name, body in methods:
        assert GUARD.match(body), \
            f"{name}: первое действие — не проверка адреса (комментарии не в счёт)"


def test_a_blocked_method_returns_an_empty_value_of_its_own_type():
    """Чужому — пустое значение по типу метода, а не исключение и не правда."""
    for name, body in annotated_methods(source()):
        guard = GUARD.match(body)
        assert guard, f"{name}: нет возврата на отказ моста первым действием"
        got = " ".join(("return" + guard.group(1) + ";").split())
        want = SAFE[name]
        assert got == want, f"{name}: на отказ возвращается «{got}», а ждёт JS «{want}»"


def test_bridge_allowed_reads_remembered_page_and_never_touches_the_webview():
    """bridgeAllowed — только pageOurs/pageUrl: getUrl() с потока моста бросает."""
    clean = strip_comments(source())
    start = clean.find("private boolean bridgeAllowed()")
    assert start >= 0, "нет bridgeAllowed(): мост не проверяет, чья страница открыта"

    body = body_of(clean, start)
    assert "pageOurs" in body and "pageUrl" in body, \
        "bridgeAllowed не читает запомненный адрес окна"
    assert "getUrl" not in body, \
        "bridgeAllowed зовёт WebView.getUrl() — на потоке JavaBridge это RuntimeException"
    assert "web." not in body, \
        "bridgeAllowed трогает WebView с потока моста"
    assert "Log.w(TAG" in body and "чужая страница" in body, \
        "чужой адрес не записан в журнал — поломка осталась бы невидимой"
    assert "hostOf(pageUrl)" in body, \
        "в журнал идёт не хост, а полный адрес — в query бывают токены"
    assert "return true;" in body and "return false;" in body, "нет обоих исходов"


def test_the_bridge_class_itself_never_asks_the_webview_for_a_url():
    """Весь код, исполняемый на потоке моста, свободен от WebView.getUrl()."""
    clean = strip_comments(source())
    start = clean.find("private final class KorzinaBridge")
    assert start >= 0, "пропал класс KorzinaBridge"
    body = body_of(clean, start)
    assert "getUrl" not in body, \
        "в классе моста остался getUrl() — он исполняется на потоке JavaBridge"


def test_the_initial_page_state_is_set_before_the_first_load():
    """Стартовый адрес запоминается до loadUrl, иначе мост спросит старый/пустой."""
    clean = strip_comments(source())
    start = clean.find("private void openOurApp(")
    assert start >= 0, "пропал openOurApp"
    body = body_of(clean, start)
    assert "pageOurs = isOurs(pageUrl)" in body, "стартовый адрес не запоминается"
    set_at = body.find("pageOurs =")
    load_at = body.find("web.loadUrl")
    assert 0 <= set_at < load_at, "адрес запоминается после loadUrl — окно расходится"


def test_main_client_remembers_the_page_in_at_least_two_callbacks():
    """Адрес ведут колбэки главного WebViewClient, а не запрос из моста."""
    clean = strip_comments(source())
    marker = "web.setWebViewClient(new WebViewClient()"
    start = clean.find(marker)
    assert start >= 0, "у главного окна нет своего WebViewClient"
    client = body_of(clean, start)

    callbacks = {}
    for match in CALLBACK.finditer(client):
        callbacks[match.group(1)] = body_of(client, match.start())

    # Колбэки берут url из своего аргумента, а не view.getUrl(): документация
    # допускает в onPageStarted ещё старый адрес, а collect зовут по
    # DOMContentLoaded — тогда мост остался бы закрыт на нашей же странице.
    updated = {
        name for name, body in callbacks.items()
        if "pageOurs = isOurs(pageUrl)" in body and "pageUrl = url" in body
    }
    assert "onPageStarted" in updated and "onPageFinished" in updated, \
        f"адрес не ведётся в начале и конце загрузки: обновлены {sorted(updated)}"
    assert len(updated & {"onPageStarted", "onPageFinished", "doUpdateVisitedHistory"}) >= 2, \
        f"меньше двух колбэков обновляют pageOurs: {sorted(updated)}"


def test_hidden_store_windows_do_not_touch_the_main_page_state():
    """У скрытых окон свой WebView и свой клиент — мост и его адрес их не касаются."""
    clean = strip_comments(source())
    marker = "v.setWebViewClient(new WebViewClient()"
    start = clean.find(marker)
    assert start >= 0, "у скрытого окна сети нет своего WebViewClient"
    hidden = body_of(clean, start)
    assert "pageOurs" not in hidden and "pageUrl" not in hidden, \
        "скрытое окно переписывает адрес главного окна"


def test_no_log_line_prints_a_whole_url():
    """В журнал — только хост: query чужой страницы может нести токены."""
    clean = strip_comments(source())
    for args in call_arguments(clean, "Log.w("):
        assert "getUrl" not in args, \
            f"Log.w печатает полный адрес окна: {args.strip()[:80]}"

    start = clean.find("private boolean bridgeAllowed()")
    body = body_of(clean, start)
    assert "hostOf(pageUrl)" in body, "мост пишет в журнал адрес без hostOf"


def test_is_ours_is_a_pure_string_check():
    """isOurs не трогает WebView: обычная строка, безопасна с любого потока."""
    clean = strip_comments(source())
    start = clean.find("private static boolean isOurs(")
    assert start >= 0, "пропал isOurs — проверка адреса потеряла смысл"
    body = body_of(clean, start)
    assert "SERVER" in body and "startsWith" in body and "equals" in body
    assert "url != null" in body
    assert "getUrl" not in body and "WebView" not in body and "web." not in body, \
        "isOurs зовёт WebView — с потока моста это RuntimeException"


def test_there_are_at_least_as_many_checks_as_bridge_methods():
    """Счётчик защиты: вызовов bridgeAllowed не меньше, чем самих методов моста."""
    clean = strip_comments(source())
    checks = clean.count("bridgeAllowed()")
    methods = len(annotated_methods(source()))
    assert methods >= 5, f"методов моста стало меньше: {methods}"
    assert checks >= methods + 1, \
        f"мост: {methods} методов и всего {checks} упоминаний bridgeAllowed"
