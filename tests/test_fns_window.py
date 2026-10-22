"""Окно кабинета ФНС внутри приложения: вход у человека, ключ — у приложения.

ЗАЧЕМ ЭТОТ СТОРОЖ. На телефонном экране «Мои чеки» человек видит состояние кабинета,
но получить ключ ему негде: закладок в WebView нет, а вход в кабинет по СМС закрыт
капчей. Поэтому в приложении появилось окно: человек сам входит в кабинет в том же
WebView и проходит капчу, а панель приложения по кнопке «Забрать чеки» инжектит на
страницу кабинета `static/fns-grab.js`. Тот забирает ключ ЕГО ЖЕ сессии и возвращает
окно на `/phone/receipts?keys=…`, где ключ принимает сервер.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Android-сборку в питоновских тестах не запустить (нет ни
gradle, ни эмулятора), поэтому проверка статическая — как в tests/test_bridge_origin.py:
в исходниках должно быть ровно то, на что опирается обещание.

- метод моста `openFns` есть и первым делом спрашивает, чья страница открыта;
- окно кабинета открывается в ГЛАВНОМ `web`, а не в скрытом окне сети;
- панель ФНС со «←» и «Забрать чеки» видна только в режиме ФНС и только когда
  `pageOurs == false` — на нашем сайте командует сайт;
- «Забрать чеки» кладёт в страницу `window.KORZINA_APP_URL` и исполняет сборщик,
  полученный существующим `call(...)` как GET (статика POST не принимает);
- кнопка «назад» ОС в режиме ФНС ведёт на `/phone/receipts`, а не в историю налоговой;
- возврат на наш адрес снимает режим и прячет панель;
- в классе моста нет `getUrl()`: он исполнился бы на потоке JavaBridge и упал;
- в сборщике есть запрос описи кабинета и `window.KORZINA_APP_URL`, а `document`-
  хаков закладки (панель, копирование, файл) нет — это не javascript:-ссылка;
- страница чеков содержит кнопку `data-open-fns` и честную подсказку «Откройте
  приложение» для случая, когда моста нет.

ЖИВОЙ ВХОД В КАБИНЕТ ЗДЕСЬ НЕ ПРОВЕРЯЕТСЯ И НЕ МОЖЕТ: капчу и код из СМС проходит
человек. Проверено только то, что код к этому готов.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "android", "app", "app", "src", "main", "java",
                    "ru", "moyakorzina", "app", "MainActivity.java")
GRAB = os.path.join(ROOT, "app", "web", "static", "fns-grab.js")
DEVICE = os.path.join(ROOT, "app", "web", "static", "korzina-device.js")
TEMPLATE = os.path.join(ROOT, "app", "web", "templates", "phone", "receipts.html")
PHONE = os.path.join(ROOT, "app", "web", "phone", "receipts.py")

# Первое действие метода моста: проверка адреса и пустой ответ по типу метода.
GUARD = re.compile(r'\s*if\s*\(\s*!bridgeAllowed\(\)\s*\)\s*return\b([^;]*);')

# Аннотация, за которой идёт сигнатура метода. Комментарии вырезаются до разбора,
# чтобы слово «@JavascriptInterface» в объяснении не стало методом.
SIGNATURE = re.compile(
    r"\s*(?:@\w+(?:\s*\([^)]*\))?\s*)*"
    r"public\s+[\w<>\[\],.]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[^{]+)?\{"
)


def source(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
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


def annotated_methods(src: str) -> list[tuple[str, str]]:
    """Пары «имя метода — его тело» для всех настоящих @JavascriptInterface."""
    clean = strip_comments(src)
    found: list[tuple[str, str]] = []
    for match in re.finditer(r"@JavascriptInterface\b", clean):
        signature = SIGNATURE.match(clean, match.end())
        assert signature, "у @JavascriptInterface не разобрать сигнатуру метода"
        found.append((signature.group(1), body_of(clean, signature.start())))
    return found


# ---------- файлы на месте ----------
def test_the_window_has_all_its_parts():
    for path in (JAVA, GRAB, DEVICE, TEMPLATE, PHONE):
        assert os.path.exists(path), f"нет файла: {path}"


# ---------- мост ----------
def test_open_fns_is_a_bridge_method_and_checks_the_page_first():
    """Чужой странице окно налоговой не нужно: первым делом — bridgeAllowed."""
    methods = dict(annotated_methods(source(JAVA)))
    assert "openFns" in methods, "в мосте нет openFns — кнопка на экране чеков мертва"
    guard = GUARD.match(methods["openFns"])
    assert guard, "openFns: первое действие — не проверка адреса"
    got = " ".join(("return" + guard.group(1) + ";").split())
    assert got == "return;", f"openFns на отказ отвечает «{got}», а ждёт JS «return;»"


def test_open_fns_window_opens_the_cabinet_in_the_main_webview():
    """Кабинет — в главном окне: человек должен видеть свой вход и капчу."""
    clean = strip_comments(source(JAVA))
    body = method_body(clean, "private void openFnsWindow(")
    for piece in ("fnsMode = true", "pageUrl = FNS_URL", "pageOurs = isOurs(pageUrl)",
                  "showFnsPanel()", "web.loadUrl(FNS_URL)"):
        assert piece in body, f"openFnsWindow не делает «{piece}»"
    assert 'https://lkdr.nalog.ru/' in clean, "нет адреса кабинета ФНС"


def test_the_fns_panel_has_back_and_grab_buttons():
    """Своя панель со своими кнопками: «←» и «Забрать чеки»."""
    clean = strip_comments(source(JAVA))
    body = method_body(clean, "private View buildFnsPanel(")
    assert '"←"' in body, "у панели ФНС нет кнопки «назад»"
    assert '"Забрать чеки"' in body, "у панели ФНС нет кнопки «Забрать чеки»"
    assert "leaveFns()" in body and "grabFns()" in body, "кнопки панели никуда не ведут"
    assert "fnsPanel.setVisibility(View.GONE)" in clean, \
        "панель ФНС не спрятана при запуске"


def test_the_fns_panel_shows_only_away_from_our_site():
    """Панель видна, только когда открыта через openFns и pageOurs == false."""
    clean = strip_comments(source(JAVA))
    body = method_body(clean, "private void showFnsPanel(")
    assert "fnsMode" in body, "панель не знает, открыт ли кабинет"
    assert "!pageOurs" in body, "панель не сверяется с адресом окна"
    assert "View.VISIBLE" in body and "View.GONE" in body, "у панели нет обоих исходов"


def test_the_cabinet_mode_follows_the_page_address():
    """Вернулись на наш адрес — режим снят; чужой — панель на месте."""
    clean = strip_comments(source(JAVA))
    body = method_body(clean, "private void followFnsPage(")
    assert "if (!fnsMode) return" in body
    assert "if (pageOurs) exitFnsMode()" in body
    assert "showFnsPanel()" in body

    for name in ("onPageStarted", "onPageFinished",
                 "doUpdateVisitedHistory", "onPageCommitVisible"):
        callback = method_body(clean, f"public void {name}(")
        assert "followFnsPage()" in callback, \
            f"{name}: адрес окна ведётся, а режим ФНС за ним не следит"


# ---------- инъекция сборщика ----------
def test_grab_hands_the_page_the_app_url_then_runs_the_script():
    """«Забрать чеки»: адрес приложения в window, затем текст сборщика на страницу."""
    clean = strip_comments(source(JAVA))
    body = method_body(clean, "private void grabFns(")
    assert "window.KORZINA_APP_URL" in body, "сборщик не узнает, куда вернуться"
    assert "/phone/receipts" in body, "возврат ведёт не на телефонный экран чеков"
    assert "web.evaluateJavascript" in body, "скрипт не исполняется на странице кабинета"
    assert "call(FNS_GRAB, null" in body, "сборщик берётся не существующим call(...)"
    assert '"/static/fns-grab.js"' in clean, "нет пути к сборщику"
    assert "FNS_LOG" in clean and '"korzina:"' in clean, \
        "ошибки сборщика не узнаются на панели"
    assert "FNS_ERR" in clean and '"korzina!"' in clean, \
        "у ошибок сборщика нет отдельной метки — панель не отличит их от хода работы"


def test_the_script_is_fetched_as_a_get():
    """Статику Flask отдаёт только GET: POST вернул бы 405 и HTML вместо JS."""
    clean = strip_comments(source(JAVA))
    body = method_body(clean, "private void call(")
    assert "body != null" in body, "call не различает GET и POST"
    assert 'post ? "POST" : "GET"' in body, "call всегда POST — статику так не забрать"
    assert "if (post)" in body, "GET пытается писать тело"


def test_back_in_the_cabinet_goes_to_phone_receipts_not_to_tax_history():
    """«Назад» ОС в кабинете — наш экран чеков: человек шёл за ключом."""
    clean = strip_comments(source(JAVA))
    leave = method_body(clean, "private void leaveFns(")
    assert "/phone/receipts" in leave and "openOurApp" in leave

    back = method_body(clean, "public void onBackPressed(")
    assert "fnsMode" in back, "onBackPressed не знает про кабинет ФНС"
    assert "leaveFns()" in back, "onBackPressed уводит в историю налоговой"
    assert back.index("fnsMode") < back.index("canGoBack"), \
        "сначала история налоговой, потом режим ФНС"


def test_leaving_a_foreign_window_orders_one_history_cleanup():
    """Уход из кабинета ФНС и со страницы сети заказывает очистку истории.

    Живой прогон 21.10.2026: `/phone/receipts` → «Войти в кабинет ФНС» → lkdr.nalog.ru
    → «←» → снова `/phone/receipts`, но в истории остались кабинет и страницы, куда
    с него ушли. «Назад» с экрана чеков вернул в кабинет, а оттуда — в App Store.

    Заказ ставится только там, где следом гарантированно грузится наш адрес:
    leaveFns — перед openOurApp, leaveChain — только в ветке без истории. Ветка
    goBack() ничего не заказывает: история уже помнит наш предыдущий экран, а
    goBack() может вернуться на страницу сети, где флаг остался бы взведённым и
    съел бы историю следующего нашего экрана.
    """
    clean = strip_comments(source(JAVA))
    assert re.search(r"private\s+boolean\s+clearHistoryOnce\s*=\s*false\s*;", clean), \
        "нет флага разовой очистки истории"

    leave_fns = method_body(clean, "private void leaveFns(")
    assert "orderHistoryClear()" in leave_fns, "leaveFns не заказывает очистку"
    assert leave_fns.index("orderHistoryClear()") < leave_fns.index("openOurApp"), \
        "флаг ставится после загрузки нашего адреса — наша страница его не увидит"

    exit_fns = method_body(clean, "private void exitFnsMode(")
    assert "if (fnsMode) orderHistoryClear()" in exit_fns, \
        "снятие режима ФНС (возврат на наш адрес) не заказывает очистку"

    leave_chain = method_body(clean, "private void leaveChain(")
    branches = re.search(
        r"if\s*\(\s*web\.canGoBack\(\)\s*\)\s*\{(?P<back>[^{}]*)\}\s*else\s*\{(?P<ours>[^{}]*)\}",
        leave_chain)
    assert branches, "в leaveChain нет веток «по истории» и «загрузить наш адрес»"
    back, ours = branches.group("back"), branches.group("ours")
    assert "web.goBack()" in back, "ветка истории не идёт назад"
    assert "orderHistoryClear" not in back and "clearHistoryOnce" not in back, \
        "ветка goBack() взводит флаг: вернувшись на страницу сети, он съест историю"
    assert "orderHistoryClear()" in ours, "ветка без истории не заказывает очистку"
    assert ours.index("orderHistoryClear()") < ours.index("openOurApp"), \
        "в ветке без истории флаг ставится после загрузки нашего адреса"

    exit_chain = method_body(clean, "private void exitChainMode(")
    assert "if (currentChain >= 0) orderHistoryClear()" in exit_chain, \
        "снятие режима сети не заказывает очистку"


def test_the_cleanup_order_expires_on_its_own():
    """Заказ не должен пережить переход: наш адрес может так и не открыться.

    goBack() возвращает на страницу сети, страница приходит из back-forward cache
    (onPageFinished не звонит) — флаг остался бы взведённым и стёр бы историю
    следующего нашего экрана. Поэтому у заказа есть срок: 2–4 секунды.
    """
    clean = strip_comments(source(JAVA))
    assert re.search(
        r"private\s+final\s+Runnable\s+clearHistoryReset\s*=\s*\(\)\s*->\s*"
        r"clearHistoryOnce\s*=\s*false\s*;", clean), \
        "нет самостраховки clearHistoryReset, снимающей флаг по времени"

    order = method_body(clean, "private void orderHistoryClear(")
    assert "clearHistoryOnce = true" in order, "заказ не взводит флаг"
    assert "handler.removeCallbacks(clearHistoryReset)" in order, \
        "новый заказ не снимает старый таймер"
    assert re.search(
        r"handler\.postDelayed\(\s*clearHistoryReset\s*,\s*CLEAR_HISTORY_MS\s*\)", order), \
        "флаг не снимается по таймеру"

    timeout = re.search(
        r"private\s+static\s+final\s+int\s+CLEAR_HISTORY_MS\s*=\s*(\d+)\s*;", clean)
    assert timeout, "нет срока жизни заказа"
    ms = int(timeout.group(1))
    assert 2000 <= ms <= 4000, f"срок жизни заказа {ms} мс — вне 2–4 с"


def test_the_history_is_cleared_on_the_next_our_page_and_only_once():
    """Чистка — на нашем событии страницы, под условием «заказ есть и страница наша».

    Не на каждой странице: иначе «назад» перестанет возвращать между экранами
    приложения. И не раньше снятия режимов в том же колбэке: exitChainMode и
    followFnsPage ставят заказ сами, и очистка обязана случиться уже в этом проходе,
    а не на следующей, случайной странице. Событий несколько: возврат из
    back-forward cache может не дать onPageFinished, поэтому заказ исполняет и
    onPageCommitVisible.
    """
    clean = strip_comments(source(JAVA))

    helper = method_body(clean, "private void clearHistoryIfOurs(")
    assert "clearHistoryOnce" in helper and "pageOurs" in helper, \
        "очистка идёт без проверки «наш адрес и разовый заказ»"
    assert "view.clearHistory()" in helper, "история не чистится"
    assert "clearHistoryOnce = false" in helper, \
        "заказ на очистку не снимается — история чистилась бы на каждой нашей странице"
    assert helper.index("view.clearHistory()") < helper.index("clearHistoryOnce = false"), \
        "флаг снимается до очистки"
    assert "handler.removeCallbacks(clearHistoryReset)" in helper, \
        "таймер самостраховки не снят после очистки"

    finished = method_body(clean, "public void onPageFinished(")
    assert "pageOurs = isOurs(pageUrl)" in finished, "onPageFinished больше не ведёт адрес"
    assert "clearHistoryIfOurs(view)" in finished, "onPageFinished не исполняет заказ"
    assert finished.index("exitChainMode()") < finished.index("clearHistoryIfOurs(view)"), \
        "очистка идёт раньше снятия режима сети — заказ от exitChainMode пропадёт"
    assert finished.index("followFnsPage()") < finished.index("clearHistoryIfOurs(view)"), \
        "очистка идёт раньше снятия режима ФНС — заказ от exitFnsMode пропадёт"

    committed = method_body(clean, "public void onPageCommitVisible(")
    assert "clearHistoryIfOurs(view)" in committed, \
        "заказ исполняется только в onPageFinished: из back-forward cache его не будет"

    # Обычная навигация «назад» между нашими экранами: вызов ровно один и под флагом.
    assert clean.count("clearHistory()") == 1, \
        "clearHistory зовут больше одного раза: «назад» между нашими экранами сломается"


def test_the_address_events_stay_current():
    """onPageStarted и onPageCommitVisible по-прежнему ведут pageUrl/pageOurs."""
    clean = strip_comments(source(JAVA))
    for name in ("onPageStarted", "onPageCommitVisible"):
        body = method_body(clean, f"public void {name}(")
        assert "pageUrl = url" in body, f"{name} больше не записывает адрес окна"
        assert "pageOurs = isOurs(pageUrl)" in body, \
            f"{name} больше не сверяет, чья страница открыта"


def test_the_bridge_class_does_not_read_the_webview_url():
    """Класс моста живёт на потоке JavaBridge, где WebView.getUrl() бросает."""
    clean = strip_comments(source(JAVA))
    start = clean.find("private final class KorzinaBridge")
    assert start >= 0, "пропал класс KorzinaBridge"
    body = body_of(clean, start)
    assert "getUrl" not in body, "в классе моста остался getUrl()"


# ---------- сборщик ----------
def test_the_grab_script_takes_the_session_and_returns_with_the_key():
    """Сборщик — это про вход: один запрос описи, ключ и возврат в приложение."""
    js = source(GRAB)
    for piece in ("/api/v1/receipt", "window.KORZINA_APP_URL", "location.replace",
                  "?keys=", "encodeURIComponent", "korzina:", "korzina!",
                  "AbortController"):
        assert piece in js, f"в сборщике нет «{piece}»"
    assert "document." not in js, \
        "сборщик трогает document: это не закладка, а обычный файл на странице"


def test_the_device_wrapper_opens_the_cabinet_only_through_the_bridge():
    js = source(DEVICE)
    assert "function openFns()" in js, "в обёртке моста нет openFns"
    assert "typeof k.openFns !== 'function'" in js, \
        "обёртка не проверяет, умеет ли сборка открывать кабинет"
    assert "return false;" in js
    assert "openFns: openFns" in js, "openFns не отдан странице"


# ---------- телефонный экран ----------
def test_the_phone_page_has_the_button_and_the_honest_hint():
    page = source(TEMPLATE)
    assert "data-open-fns" in page, "на экране чеков нет кнопки входа в кабинет"
    assert "Войти в кабинет ФНС" in page, "у кнопки нет подписи"
    assert "Откройте приложение" in page, \
        "вне приложения нет честной подсказки «откройте приложение»"
    assert "KorzinaDevice.openFns()" in page, "клик не зовёт мост"
    assert 'id="keys"' in page, "рядом с подсказкой нет поля ключа"


def test_the_phone_screen_passes_on_phone_to_the_template():
    py = source(PHONE)
    assert re.search(r"on_phone\s*=\s*device\.on_phone\(\)", py), \
        "receipts.py не передаёт on_phone — подпись не узнает приложение"
