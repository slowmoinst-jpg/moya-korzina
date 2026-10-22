"""Успех телефонной сборки: терпение поиска, второй адрес и подсказки вместо молчания.

ЗАЧЕМ ЭТОТ СТОРОЖ. Живая фоновая сборка 20.10.2026 положила 0-1 позицию из 4, и
причины из отчётов были не про товар: «поиск сети ничего не нашёл за 12 с», «поиск
сети нашёл не тот товар», «на карточке не нашлось кнопки „в корзину“». Отдельно —
сеть просит выбрать адрес или войти, а приложение молчит и пишет общее «не вышло».

Сервер уже отдаёт строке наряда второй, короткий адрес поиска (`search_alt`,
app/phonecart.py `_search_lines`). Телефон должен: ждать карточку дольше, сделать
один повторный проход по той же странице, попробовать `search_alt` один раз, а
вместо молчания назвать человеку адрес или вход. Если кнопка не нашлась на уже
открытой карточке — один раз перечитать карточку.

Живой прогон 20.10.2026 добавил ещё две вещи: баннер согласия накрывал карточку
Самоката (его закрывают до проверок), а отказ «поиск ничего не нашёл» не называл
запрос — теперь называет и его, и второй запрос `search_alt`, если его пробовали.

Живой разбор 21.10.2026 добавил третью: у Магнита на нашей полке стоит другая
линейка того же бренда (Parmalat Natura Premium Comfort), у Ленты — Parmalat Edge.
Короткий запрос «Молоко Parmalat» приводит на любую из них, а `resembles()` видит
только общие слова — в корзину владельца лёг бы другой товар. Перед касанием
«В корзину» телефон сверяет вес упаковки из наряда (`pack_g`) с числом и единицей
в заголовке карточки; разошлось больше допуска — строка не кладётся.

ПОЧЕМУ ПРОВЕРКА СТАТИЧЕСКАЯ. Android-сборку в наборе питоновских тестов запускать
нечем: здесь нет ни эмулятора, ни gradle. Но обещания видно по исходнику, а
компиляцию подтвердит сборка APK.
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote_plus, urlsplit

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "android", "app", "app", "src", "main", "java",
                    "ru", "moyakorzina", "app", "MainActivity.java")


def source() -> str:
    with open(JAVA, encoding="utf-8") as fh:
        return fh.read()


def strip_comments(src: str) -> str:
    """Вырезать // и /* */, не трогая строковые и символьные литералы.

    Длина и переводы строк сохраняются, чтобы позиции оставались осмысленными.
    Строки важны: в них бывают и «//», и скобки, и слова, которые не должны
    засчитываться как код (например, упоминание search_alt в комментарии).
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

    Считаем скобки, а не ищем отступ: метод переживёт переформатирование.
    Скобки внутри строк не считаются.
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


def method_body(src: str, header: str) -> str:
    start = src.find(header)
    assert start >= 0, f"не найден метод: {header}"
    return body_of(src, start)


def test_the_java_file_is_where_we_think_it_is():
    assert os.path.exists(JAVA), f"нет MainActivity.java: {JAVA}"


def test_the_search_waits_at_least_twenty_seconds():
    """12 с не хватало: витрина рисует выдачу скриптами дольше."""
    clean = strip_comments(source())
    match = re.search(r"private\s+static\s+final\s+int\s+SEARCH_LIMIT_MS\s*=\s*(\d+)\s*;", clean)
    assert match, "пропала константа SEARCH_LIMIT_MS"
    limit = int(match.group(1))
    assert limit >= 20000, f"SEARCH_LIMIT_MS = {limit}, а живой прогон просил не меньше 20 000"


def test_the_search_is_polled_again_once_before_giving_up():
    """Срок истёк — один повторный проход, и только потом «ничего не нашёл»."""
    clean = strip_comments(source())
    body = method_body(clean, "private void askCard(")
    assert re.search(r"if\s*\(\s*!\s*retried\s*\)", body), \
        "нет отдельного повторного прохода: срок истекает — сразу отказ"
    assert body.count("SEARCH_LIMIT_MS") >= 2, \
        "повторный проход не берёт новый срок из SEARCH_LIMIT_MS"
    assert re.search(r"askCard\s*\(\s*r\s*,\s*line\s*,\s*SystemClock\.uptimeMillis\(\)\s*\+\s*SEARCH_LIMIT_MS",
                     body), "нет повторного запуска опроса по той же странице"


def test_the_plan_line_carries_the_second_address_to_ask_card():
    """Наряд приходит с сервера — второй адрес должен доехать до строки, а не потеряться."""
    clean = strip_comments(source())
    start = clean.find('JSONArray search = plan.optJSONArray("search");')
    assert start >= 0, "не найти разбор поисковых строк наряда"
    loop = body_of(clean, start)
    assert 's.optString("search_alt"' in loop, \
        "второй адрес не читается из строки наряда"
    assert '.put("search_alt"' in loop, \
        "второй адрес не кладётся в строку для askCard — попытка была бы всегда пустой"


def test_the_second_search_address_is_tried_once():
    """Первый запрос нашёл не тот товар — пробуем короткий search_alt, но только раз."""
    clean = strip_comments(source())
    body = method_body(clean, "private void askCard(")
    assert "search_alt" in body, "askCard не смотрит на второй адрес поиска"
    assert "search_alt_tried" in body, "нет пометки, что второй адрес уже пробовали"
    assert re.search(r'!\s*line\.optBoolean\("search_alt_tried"', body), \
        "второй адрес не защищён от повторов — можно ходить по нему бесконечно"
    assert "go(r, alt)" in body and "whenReady(r, alt" in body, \
        "по второму адресу не переходят — он бы не попробовался"
    assert "isHostOf(r.chain, alt)" in body, \
        "второй адрес не проверяется на свою сеть — ушли бы на чужой хост"
    # При повторной неудаче — прежний текст отказа, а не новый.
    assert "поиск сети нашёл не тот товар" in body


def test_a_short_query_success_is_marked_in_the_report():
    """Успех по второму адресу видно в отчёте строки, а не только в журнале."""
    clean = strip_comments(source())
    ask = method_body(clean, "private void askCard(")
    assert "нашёл по короткому запросу" in ask, "успех по короткому запросу не помечен"
    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert '.put("note"' in done, "пометка не попадает в строку отчёта"


def test_wall_js_recognizes_address_and_login():
    """Стена «выберите адрес» и «войдите» видна по тексту страницы, регистр не важен."""
    clean = strip_comments(source())
    start = clean.find("private static final String WALL_JS")
    assert start >= 0, "пропал WALL_JS"
    end = clean.find("private String wallText", start)
    assert end > start, "не найти конец WALL_JS"
    wall = clean[start:end]
    for phrase in ("выберите адрес", "введите адрес", "адрес доставки", "куда доставить",
                   "войдите", "авторизуйтесь", "личный кабинет"):
        assert phrase in wall, f"WALL_JS не распознаёт «{phrase}»"
    assert "address" in wall and "login" in wall, "нет пометок address/login для отчёта"


def test_wall_text_explains_address_and_login_in_words():
    """Человеку сказано, что делать, а не общее «не вышло»."""
    clean = strip_comments(source())
    body = method_body(clean, "private String wallText(")
    assert "сеть просит выбрать адрес" in body, "нет подсказки про адрес"
    assert "выберите его в окне сети" in body, "подсказка про адрес не говорит, что делать"
    assert "сеть просит войти" in body, "нет подсказки про вход"
    assert "войдите в окно сети" in body, "подсказка про вход не говорит, что делать"
    assert "«Собрать» снова" in body, "подсказки не говорят повторить сборку"


def test_the_not_found_message_takes_its_number_from_the_constant():
    """«ничего не нашёл за N с»: N — из SEARCH_LIMIT_MS, а не литерал 12."""
    clean = strip_comments(source())
    body = method_body(clean, "private void askCard(")
    assert "поиск сети ничего не нашёл за" in body, "пропал текст отказа поиска"
    assert re.search(r"SEARCH_LIMIT_MS\s*/\s*1000", body), \
        "в тексте отказа не SEARCH_LIMIT_MS / 1000"
    assert not re.search(r"ничего не нашёл за\s*\"?\s*\+\s*12\b", body), \
        "в тексте отказа захардкожено 12 с"
    assert not re.search(r"ничего не нашёл за\s+12\s+с", body), \
        "в тексте отказа захардкожено «12 с»"


def test_a_missing_button_reloads_the_card_once():
    """Карточка открылась, кнопки нет — один перезаход на карточку, потом отказ."""
    clean = strip_comments(source())
    body = method_body(clean, "private void tapAdd(")
    assert body.count("cardAgain") >= 2, \
        "tapAdd не отличает первый отказ от повторного: перезахода нет или он бесконечен"
    assert "whenReady(r, here" in body, "перезаход на карточку не ждёт её готовности"
    assert "onCard(r)" in body, "после перезахода карточка не проверяется заново"
    next_body = method_body(clean, "private void nextLine(")
    assert "cardAgain = false" in next_body, \
        "флаг перезахода не сбрасывается на следующей позиции — он сгорит на первой"


def test_consent_js_closes_only_the_consent_button():
    """Баннер согласия поверх карточки: жмём только слова согласия и ничего больше.

    Живой прогон 20.10.2026: у Самоката карточка открылась, но её накрыл баннер, и
    сборка сказала «страница не похожа на карточку товара: нет заголовка (рядом:
    «Соглашаюсь»)». Перед проверкой карточки баннер надо закрыть — но только его.
    """
    clean = strip_comments(source())
    start = clean.find("private static final String CONSENT_JS")
    assert start >= 0, "пропал CONSENT_JS — баннер согласия снова закроет карточку"
    end = clean.find("private String wallText", start)
    assert end > start, "не найти конец CONSENT_JS"
    raw = clean[start:end]
    js = raw.lower()
    for word in ("соглашаюсь", "согласен", "принять", "принимаю", "принять все",
                 "хорошо", "понятно", "разрешить", "accept", "i agree", "allow all"):
        assert word in js, f"CONSENT_JS не знает слова согласия «{word}»"
    # Защита: слова действия не трогаем, даже если рядом стоит «Принять».
    for guard in ("оплат", "войти", "подпис", "оформ"):
        assert guard in js, f"CONSENT_JS не защищён от «{guard}»"
    for attr in ("aria-label", "title", "innerText"):
        assert attr in raw, f"CONSENT_JS не смотрит на {attr}"
    assert "querySelectorAll('button,[role=button],a')" in raw, \
        "CONSENT_JS ищет не кнопки и ссылки"
    assert ".click()" in raw, "CONSENT_JS не нажимает найденную кнопку"
    assert "return t" in js, "CONSENT_JS не говорит, что баннер закрыт"


def test_consent_is_closed_before_the_card_is_checked():
    """Согласие закрывается до проверок карточки и попадает в отчёт строки."""
    clean = strip_comments(source())
    oncard = method_body(clean, "private void onCard(")
    assert "dismissConsent(r" in oncard, "onCard не закрывает баннер согласия"
    assert oncard.find("dismissConsent(r") < oncard.find("tapAdd(r"), \
        "согласие закрывается после проверки карточки — баннер уже помешал"
    assert "watchScript" in oncard, "сторож запросов ставится не на карточке"

    dismiss = method_body(clean, "private void dismissConsent(")
    assert "CONSENT_JS" in dismiss, "dismissConsent не выполняет CONSENT_JS"
    assert "CONSENT_WAIT_MS" in dismiss, \
        "после согласия карточка не ждёт перерисовки (handler.postDelayed)"
    assert "закрыл баннер согласия" in dismiss, \
        "нажатие согласия не отмечается для отчёта"
    match = re.search(r"CONSENT_WAIT_MS\s*=\s*(\d+)\s*;", clean)
    assert match, "пропала константа CONSENT_WAIT_MS"
    assert 1200 <= int(match.group(1)) <= 2500, \
        f"CONSENT_WAIT_MS = {match.group(1)}: живой прогон просил около 1.5 с"

    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert done.count("r.consent") >= 2, \
        "отметка о баннере не попадает ни в why отказа, ни в note успеха"
    assert '.put("why", say)' in done, "why отказа не несёт отметки о баннере"
    assert '.put("note", note)' in done, "note успеха не несёт отметки о баннере"
    next_body = method_body(clean, "private void nextLine(")
    assert 'r.consent = ""' in next_body, \
        "отметка о баннере не сбрасывается на следующей позиции"


def test_the_not_found_message_names_the_actual_query():
    """«Поиск ничего не нашёл» называет имя позиции и запрос, ушедший в сеть."""
    clean = strip_comments(source())
    ask = method_body(clean, "private void askCard(")
    assert "notFoundWhy(line," in ask, \
        "отказ поиска по-прежнему без запроса: не видно, что искали"
    why = method_body(clean, "private static String notFoundWhy(")
    assert 'line.optString("name"' in why, "в причине нет имени позиции"
    assert 'searchQuery(line' in why, "в причине нет запроса из адреса поиска"
    assert "искали «" in why, "в причине нет слов «искали»"
    assert "второй запрос" in why and "search_alt_tried" in why, \
        "не сказать, пробовали ли второй адрес поиска (search_alt)"
    assert "тоже пуст" in why, "вторая попытка не названа пустой"

    query = method_body(clean, "private static String searchQuery(")
    assert "new URL(url).getQuery()" in query, "запрос не берётся из адреса поиска"
    assert "URLDecoder.decode" in query, "запрос не декодируется — в отчёте были бы %20"
    for key in ('"text"', '"q"', '"search"', '"query"'):
        assert key in query, f"searchQuery не смотрит параметр {key}"


def test_add_button_search_reads_labels_titles_and_protects_the_card():
    """Дикси: кнопку ищем и по title/aria-label; чужой товар широким поиском не жмём."""
    clean = strip_comments(source())
    start = clean.find("private static final String PICK_JS")
    end = clean.find("private static final String VERIFY_JS", start)
    assert start >= 0 and end > start, "не найти PICK_JS"
    js = clean[start:end]
    assert "getAttribute('title')" in js, "поиск кнопки не смотрит title"
    assert "getAttribute('aria-label')" in js, "поиск кнопки не смотрит aria-label"
    for word in ("в корзину", "добавить", "купить"):
        assert word in js, f"широкий поиск кнопки не знает «{word}»"
    assert "wide=true" in js, "широкий поиск не отмечает, что кнопка найдена им"
    assert "title:" in js, "карточка не отдаёт заголовок для сверки с названием"

    body = method_body(clean, "private void tapAdd(")
    index = body.find('got.optBoolean("wide")')
    assert index >= 0, "tapAdd не различает широкий поиск кнопки"
    tail = body[index:]
    assert "resembles(name, card)" in tail, \
        "широкий поиск жмёт кнопку без сверки названия карточки — можно положить чужой товар"
    assert "товар с открытой страницы" in tail, \
        "проверка блокирует «Положить этот»: у такой строки имя не товарное"
    assert "на карточке: " in tail, \
        "при отказе не видно, какие кнопки были на карточке"
    assert "lineDone(r, false" in tail, "отказ широкого поиска не доходит до отчёта"


def test_page_dump_is_only_title_path_and_first_words():
    """Что видит телефон на странице: заголовок, путь и первые слова — и ничего больше.

    Живой прогон 21.10.2026 (APK 78 693): причины уже называют запрос и закрывают
    баннер, но по ним не понять, что на странице Магнита, Ленты, Самоката и Дикси.
    Дамп — короткий: 60 знаков заголовка, 60 пути, 120 текста; куки, поля форм и
    запросы не читаются.
    """
    clean = strip_comments(source())
    start = clean.find("private static final String PAGE_JS")
    assert start >= 0, "пропал PAGE_JS — причина отказа снова без вида страницы"
    end = clean.find("private String wallText", start)
    assert end > start, "не найти конец PAGE_JS"
    js = clean[start:end]
    assert "document.title" in js, "дамп не берёт заголовок страницы"
    assert "location.pathname" in js, "дамп не берёт путь страницы"
    assert "innerText" in js, "дамп не берёт первые слова текста"
    assert "slice(0,60)" in js, "у заголовка и пути нет предела 60 знаков"
    assert "slice(0,120)" in js, "у текста нет предела 120 знаков"
    assert r"replace(/\\s+/g" in js, "дамп не схлопывает переносы и лишние пробелы"
    assert "JSON.stringify" in js, "PAGE_JS не отдаёт объект"
    low = js.lower()
    for forbidden in ("cookie", "queryselector", "fetch(", "xmlhttprequest",
                      "sendbeacon", "localstorage"):
        assert forbidden not in low, f"дамп лезет не туда: {forbidden}"


def test_the_page_dump_is_embedded_in_the_three_failures():
    """Три причины отказа называют страницу: пустая выдача, «нет заголовка», «нет кнопки»."""
    clean = strip_comments(source())
    ask = method_body(clean, "private void askCard(")
    assert "pageDump(r," in ask, "отказ «поиск ничего не нашёл» не берёт дамп страницы"
    assert "searchWhy(notFoundWhy(line, base)" in ask, \
        "в отказе поиска страница не соединена с запросом"
    assert "wallText" in ask, "стена по-прежнему не отличается от пустой выдачи"

    tap = method_body(clean, "private void tapAdd(")
    assert "pageDump(r," in tap, "отказы карточки не берут дамп страницы"
    assert "cardWhy(" in tap, "дамп не встраивается в причину карточки"
    assert "нет заголовка" in tap and "не нашлось кнопки «в корзину»" in tap
    index = tap.find("pageDump(r,")
    assert index > tap.find('if (!got.optBoolean("found"))'), \
        "дамп берётся вне ветки отказа — успех бы тоже читал страницу"

    search = method_body(clean, "private static String searchWhy(")
    assert ", на странице " in search, \
        "в отказе поиска нет слов «на странице»"
    card = method_body(clean, "private static String cardWhy(")
    assert "(на странице " in card, \
        "в отказе карточки нет ссылки на страницу"
    assert "рядом: " in card, \
        "кнопки рядом с дампом не названы"

    words = method_body(clean, "private static String pageWords(")
    for key in ('"t"', '"p"', '"x"'):
        assert key in words, f"дамп не читает поле {key}"
    assert "«" in words and "»" in words, "дамп не обёрнут в кавычки для отчёта"
    assert "fitWhy(" in search and "fitWhy(" in card, \
        "причина с дампом не обрезается до предела why"


def test_a_long_reason_is_cut_to_two_hundred():
    """why длиннее 200 не отдаём: сервер режет так же (app/phonecart.py)."""
    clean = strip_comments(source())
    match = re.search(r"WHY_LIMIT\s*=\s*(\d+)\s*;", clean)
    assert match, "пропала константа WHY_LIMIT"
    assert int(match.group(1)) == 200, f"WHY_LIMIT = {match.group(1)}, а сервер режет до 200"
    body = method_body(clean, "private static String fitWhy(")
    assert "WHY_LIMIT" in body, "fitWhy не знает предела"
    assert "substring(0, WHY_LIMIT - 1)" in body, \
        "fitWhy не режет строку до предела"
    assert "…" in body, "обрезанная причина не показывает, что она обрезана"

    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert re.search(r"if\s*\(\s*!\s*ok\s*\)\s*say\s*=\s*fitWhy\(say\)", done), \
        "итоговый why отказа не обрезается (успех трогать нельзя)"


def test_the_dump_is_not_taken_when_the_line_is_laid():
    """Успех страницу не читает: дамп — только для причины отказа."""
    clean = strip_comments(source())
    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert "pageDump" not in done, "строка отчёта читает страницу даже у успеха"
    check = method_body(
        clean,
        "private void checkLine(final CartRun r, final String failure, "
        "final JSONArray before, final boolean waited)")
    assert "pageDump" not in check, "проверка легло/не легло читает страницу — это успех"
    verify = method_body(clean, "private void verifyLine(")
    assert "pageDump" not in verify, "сверка артикула читает страницу"
    assert clean.count("pageDump(r,") == 6, \
        "дамп подключён не ровно к шести отказам (поиск, карточка, предварительная проверка, " \
        "фасовка, чужое название, признак корзины)"


def test_the_card_is_checked_again_after_consent():
    """Самокат: согласие закрылось, а карточку проверяли сразу — падала на живой.

    После закрытого баннера один повтор проверки через CONSENT_WAIT_MS; отказ с
    дампом возможен только после него.
    """
    clean = strip_comments(source())
    oncard = method_body(clean, "private void onCard(")
    assert "consentRetry" in oncard, "повтор после согласия не однократный"
    assert "CONSENT_WAIT_MS" in oncard, "повтор не ждёт перерисовки карточки"
    assert "onCard(r)" in oncard, "карточка после согласия не проверяется повторно"
    assert oncard.find("dismissConsent(r") < oncard.find("onCard(r)"), \
        "повтор стоит до закрытия баннера согласия"
    assert "handler.postDelayed" in oncard, "повтор не отложен"
    assert "tapAdd(r" in oncard, "карточка после согласия вообще не проверяется"

    next_body = method_body(clean, "private void nextLine(")
    assert "consentRetry = false" in next_body, \
        "однократность повтора не сбрасывается на следующей позиции"


def test_the_short_search_is_tried_even_when_the_first_search_is_empty():
    """Магнит: полный запрос дал пустую выдачу — короткий search_alt пробуем и здесь.

    Живой прогон 21.10.2026 (APK 80 453, Z7d уже в коде): поиск Магнита открылся, а
    выдача пустая — страница «<полный запрос> — искать в каталоге». Короткий адрес
    телефон пробовал только при «нашёл не тот товар», и пустая выдача уходила в отказ
    без второй попытки. Теперь второй запрос — ровно один раз и только своей сети.
    """
    clean = strip_comments(source())
    ask = method_body(clean, "private void askCard(")
    start = ask.find("if (!retried)")
    end = ask.find("String full = href.startsWith(")
    assert start >= 0 and end > start, "не найти ветку пустой выдачи в askCard"
    empty = ask[start:end]
    assert "search_alt" in empty, "пустая выдача не смотрит на второй адрес поиска"
    assert re.search(r'!\s*line\.optBoolean\("search_alt_tried"', empty), \
        "второй адрес на пустой выдаче не защищён от повторов"
    assert "go(r, alt)" in empty, "по второму адресу с пустой выдачи не переходят"
    assert "whenReady(r, alt" in empty, "второй адрес не ждёт загрузки страницы"
    assert "firstCard(r, line)" in empty, "после второго адреса не ищется карточка"
    assert "isHostOf(r.chain, alt)" in empty, "второй адрес не проверяется на свою сеть"
    assert empty.count('line.put("search_alt_tried", true)') == 1, \
        "пометка «второй адрес пробовали» ставится не ровно один раз"
    assert empty.find("WALL_JS") < empty.find("search_alt"), \
        "стена проверяется после второго запроса: 403 снова стал бы «ничего не нашёл»"
    assert "notFoundWhy(line, base)" in empty, \
        "пустая выдача больше не называет запрос и второй запрос"


def test_a_403_or_a_blocked_page_is_named_as_blocked():
    """Лента и Дикси ответили 403 — это не пустой поиск и не «нет кнопки»."""
    clean = strip_comments(source())
    start = clean.find("private static final String WALL_JS")
    end = clean.find("private static final String CATALOG_JS", start)
    assert start >= 0 and end > start, "не найти WALL_JS"
    wall = clean[start:end]
    low = wall.lower()
    for phrase in ("403", "forbidden", "запрещ", "не удалось загрузить сайт",
                   "возможно, у вас включён впн"):
        assert phrase in low, f"WALL_JS не распознаёт «{phrase}»"
    assert "'blocked'" in wall, "403 не помечается отдельным wall=blocked"
    assert "document.title" in wall, "заголовок страницы (403 Error) не читается"

    text = method_body(clean, "private String wallText(")
    assert "сеть не пускает этот адрес (403)" in text, \
        "403 не назван по-человечески отдельно от пустого поиска"
    assert "с другого подключения" in text, "403 не говорит, что делать"


def test_the_catalog_offer_is_named_as_a_search_without_results():
    """Магнит: «— искать в каталоге» — сеть не нашла по запросу, а не пустой экран."""
    clean = strip_comments(source())
    start = clean.find("private static final String CATALOG_JS")
    assert start >= 0, "пропал CATALOG_JS — «искать в каталоге» снова станет пустым поиском"
    end = clean.find("private static final String CONSENT_JS", start)
    assert end > start, "не найти конец CATALOG_JS"
    js = clean[start:end].lower()
    assert "искать в каталоге" in js, "CATALOG_JS не знает слов «искать в каталоге»"
    assert "ничего не найдено" in js, "CATALOG_JS не знает «ничего не найдено»"
    assert "innertext" in js, "CATALOG_JS не читает текст страницы"

    ask = method_body(clean, "private void askCard(")
    assert "сеть не нашла по запросу и предлагает каталог" in ask, \
        "предложение каталога не названо причиной"
    assert "CATALOG_JS" in ask, "страница поиска не проверяется на предложение каталога"
    start = ask.find("if (!retried)")
    end = ask.find("String full = href.startsWith(")
    empty = ask[start:end]
    assert empty.find("search_alt_tried") < empty.find("CATALOG_JS"), \
        "каталог назван раньше попытки второго запроса — причина была бы неполной"


def test_the_blank_card_check_takes_a_dump_and_names_login_or_check():
    """Самокат: «показывает не карточки товара» — без дампа и без слов о входе.

    Живой прогон 21.10.2026 (APK 80 453): предварительная проверка Самоката отказала
    «показывает не карточки товара — возможно, просит войти или проверку» и не
    показала страницу. Теперь причина называет вход/проверку и берёт дамп.
    """
    clean = strip_comments(source())
    tap = method_body(clean, "private void tapAdd(")
    assert "показывает не карточки товара" in tap, "пропал отказ предварительной проверки"
    assert "pageDump(r," in tap, "предварительная проверка не берёт дамп страницы"
    index = tap.find("показывает не карточки товара")
    assert "pageDump(r," in tap[index:index + 600], \
        "дамп берётся не в отказе предварительной проверки"

    body = method_body(clean, "private void blankWall(")
    assert "просит войти" in body and "проверку" in body, \
        "вход и проверка не названы отдельно от общего отказа"
    assert "войдите в окне сети" in body, "не сказано, что войти надо в окне сети"
    assert "«Собрать» снова" in body, "не сказано повторить сборку"
    assert "pageWords(" in body and "dump" in body, "дамп не попадает в причину"
    assert "wallText(" in body, "стена (403, адрес) не отличается от входа"
    assert "endBackground(" in body, "отказ предварительной проверки не уходит в отчёт"


# ── Z7f: фасовка на карточке ─────────────────────────────────────────────────
#
# Магнит на нашей полке держит ДРУГУЮ линейку того же бренда (Parmalat Natura
# Premium Comfort), Лента — Parmalat Edge. Короткий запрос «Молоко Parmalat» может
# привести на чужую карточку, а resembles() сверяет только общие слова: в корзину
# владельца лёг бы другой товар. Поэтому перед касанием «В корзину» сверяем вес
# упаковки из наряда (pack_g) с фасовкой в заголовке карточки.
#
# Проверка статическая, как и весь файл: Android-сборку здесь запускать нечем.
# Разбор фасовки проверяется по тому же регулярному выражению и тому же правилу
# «последнее число с единицей», что исполняет Java; числа допуска вынимаются из
# исходника, а не повторяются в тесте.

def java_literal(text: str) -> str:
    """Содержимое строкового литерала Java без экранирования."""
    return text.replace("\\\\", "\\").replace('\\"', '"')


def pack_pattern(clean: str) -> re.Pattern:
    """Выражение фасовки — ровно то, что исполняет Java."""
    match = re.search(r'PACK_RE\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', clean)
    assert match, "в MainActivity нет разбора фасовки (PACK_RE)"
    return re.compile(java_literal(match.group(1)), re.IGNORECASE)


def java_number(clean: str, name: str) -> float:
    match = re.search(rf"{name}\s*=\s*([0-9.]+)\s*;", clean)
    assert match, f"нет константы {name}"
    return float(match.group(1))


def pack_grams(clean: str, title: str) -> float:
    """Граммы упаковки из заголовка карточки — по правилу Java.

    Берётся ПОСЛЕДНЕЕ число с единицей: «1,8% 1 л» — это литр, а не процент.
    «8 рулонов» единицы веса/объёма не имеет и фасовкой не считается.
    """
    found = list(pack_pattern(clean).finditer(title.replace("\u00a0", " ")))
    if not found:
        return 0.0
    value, unit = found[-1].group(1), found[-1].group(2).lower()
    factor = 1000.0 if unit in ("кг", "kg", "л", "l") else 1.0
    return float(value.replace(",", ".")) * factor


def pack_mismatch(clean: str, want: float, card: float) -> bool:
    """Расхождение фасовки по правилу Java: десятая часть, но не шире 40 г."""
    tolerance = min(want * java_number(clean, "PACK_PART"),
                    java_number(clean, "PACK_NOISE_G"))
    return abs(card - want) > tolerance


PACK_TITLES = [
    ("950 г", 950.0),
    ("1 кг", 1000.0),
    ("0,95 л", 950.0),
    ("1,8% 1 л", 1000.0),
    ("930г", 930.0),
    ("1л", 1000.0),
    ("Молоко 2,5% 930 мл", 930.0),
]


@pytest.mark.parametrize("title,grams", PACK_TITLES)
def test_the_phone_reads_the_pack_size_from_the_card_title(title, grams):
    """«1 кг» — 1000 г, «0,95 л» — 950, «1,8% 1 л» — литр, а не процент."""
    clean = strip_comments(source())
    body = method_body(clean, "private static double packGrams(")
    assert "PACK_RE" in clean, "нет общего выражения фасовки"
    assert "PACK.matcher(" in body, "разбор не берёт общее выражение фасовки"
    assert "replace(',', '.')" in body, \
        "запятая не читается как десятичный разделитель"
    assert "1000" in body, "килограмм и литр не переводятся в граммы"
    assert "toLowerCase" in body, "регистр единицы не учитывается"
    assert pack_grams(clean, title) == grams, f"«{title}» разобрано неверно"


def test_rolls_are_not_a_pack_weight():
    """«8 рулонов» — количество, а не вес: единицы фасовки у рулонов нет."""
    clean = strip_comments(source())
    method_body(clean, "private static double packGrams(")
    assert pack_grams(clean, "8 рулонов") == 0.0
    assert pack_grams(clean, "Туалетная бумага 8 рулонов") == 0.0


@pytest.mark.parametrize("want,card,mismatch", [
    (950.0, 1000.0, True),      # наряд 950 г — карточка 1 кг: разные пачки
    (1000.0, 1030.0, False),    # 30 г — округление, а не другая упаковка
    (1000.0, 1000.0, False),
    (930.0, 900.0, False),      # 3 % — та же фасовка
    (930.0, 1000.0, True),      # 70 г — другая
])
def test_the_pack_size_is_compared_with_the_plan_and_ten_percent(want, card, mismatch):
    """Расхождение сверх допуска — отказ; 1000 и 1030 проходят."""
    clean = strip_comments(source())
    body = method_body(clean, "private static String packWhy(")
    assert 'optDouble("pack_g"' in body, "сравнение не берёт вес из наряда"
    assert "packTolerance(" in body and "Math.abs(" in body, \
        "расхождение не сравнивается с допуском"
    assert pack_mismatch(clean, want, card) is mismatch


def test_the_pack_tolerance_is_ten_percent_but_not_wider_than_forty_grams():
    """10 % — общее правило; 40 г сверху, иначе 950 и 1000 (5 %) сошли бы за одну пачку."""
    clean = strip_comments(source())
    body = method_body(clean, "private static double packTolerance(")
    assert "PACK_PART" in body and "PACK_NOISE_G" in body and "Math.min(" in body, \
        "допуск не складывается из доли и предела"
    assert java_number(clean, "PACK_PART") == 0.10, "доля допуска не десятая часть"
    assert java_number(clean, "PACK_NOISE_G") == 40.0, "предел допуска не 40 г"


def test_a_card_with_another_pack_is_refused_before_the_button():
    """До касания «В корзину»: фасовка не сошлась — отказ строки, кнопку не жмём."""
    clean = strip_comments(source())
    tap = method_body(clean, "private void tapAdd(")
    check = tap.find("packWhy(")
    press = tap.find("tapMarked(r)")
    assert check >= 0, "карточка не сверяет фасовку"
    assert press > check, "фасовка проверяется после нажатия — товар уже лёг"
    tail = tap[check:check + 1000]
    assert "pageDump(r," in tail, "отказ по фасовке без дампа страницы"
    assert "lineDone(r, false" in tail, "отказ по фасовке не доходит до отчёта"

    why = method_body(clean, "private static String packWhy(")
    assert "карточка другого веса/объёма: в наряде " in why, "нет текста отказа"
    assert " г, на карточке " in why and ' г"' in why, \
        "в отказе не названы граммы наряда и карточки"
    assert "grams(want)" in why and "grams(card)" in why, \
        "числа в отказе не приводятся к целым граммам"


def test_a_card_without_a_pack_size_is_noted_but_not_blocked():
    """Фасовки в заголовке нет — не блокируем, но помечаем успешную строку."""
    clean = strip_comments(source())
    tap = method_body(clean, "private void tapAdd(")
    assert "вес на карточке не указан" in tap, "нет пометки об отсутствии веса"
    assert "noteLine(" in tap, "пометка не кладётся в строку"
    note = method_body(clean, "private static void noteLine(")
    assert 'line.put("note"' in note, "пометка не попадает в note строки"
    assert "old.isEmpty()" in note, "прежняя пометка не сохраняется"
    assert 'old + "; " + note' in note, \
        "новая пометка не добавляется через точку с запятой"


def test_weighted_goods_and_the_open_card_skip_the_pack_check():
    """Весовой товар и заглушка «товар с открытой страницы» фасовку не сверяют."""
    clean = strip_comments(source())
    body = method_body(clean, "private static boolean packChecked(")
    placeholder = body.find('"товар с открытой страницы"')
    kg = body.find('"kg"')
    weight = body.find('optDouble("pack_g"')
    assert placeholder >= 0, "заглушка не исключена из проверки"
    assert kg >= 0, "весовой товар (unit=kg) не исключён"
    assert weight >= 0, "проверка не смотрит на вес наряда"
    assert placeholder < weight and kg < weight, \
        "исключения стоят после проверки — они бы не сработали"
    assert re.search(r'"товар с открытой страницы"[^\n]*\breturn false\b', body), \
        "заглушка не пропускается"
    assert re.search(r'"kg"[^\n]*\breturn false\b', body), \
        "весовой товар не пропускается"


# ── Z7g: строгая сверка названия, фактический второй запрос, пауза, стена,
#          признак корзины и пустая карточка ─────────────────────────────────────
#
# Живой прогон 21.10.2026 (APK 83 401): Пятёрочка по «Сушке самокат со сгущённым
# молоком 300 г» привела на ЧУЖОЙ товар «Сушки Невская сушка Простые 300г» — вес
# совпал (300 г), поэтому проверка фасовки не поймала, а resembles() пропустил по
# общему слову «сушка». Магнит после серии поисков показал стену «Выключите VPN» —
# похоже на ограничение частоты запросов. Отчёт печатал ПЕРВЫЙ запрос как второй.
# У Самоката прямой адрес карточки открывался пустым, а причина говорила «просит
# войти». Проверка статическая: Android-сборку здесь запускать нечем.


def common_roots(clean: str) -> list[str]:
    """Общие продуктовые корни — ровно те, что перечислены в Java."""
    match = re.search(r"COMMON_WORDS\s*=\s*\{(.*?)\}\s*;", clean, re.S)
    assert match, "в MainActivity нет списка общих продуктовых слов (COMMON_WORDS)"
    return re.findall(r'"([^"]+)"', match.group(1))


def significant_words(clean: str, name: str) -> list[str]:
    """Значимые слова названия по правилу Java: длиной ≥5 и не общие продуктовые."""
    roots = common_roots(clean)
    out: list[str] = []
    for word in re.split(r"[^\w]+", name.lower()):
        if len(word) < 5:
            continue
        if any(word.startswith(root) for root in roots):
            continue
        if word not in out:
            out.append(word)
    return out


def query_of(url: str) -> str:
    """Запрос из адреса поиска по правилу Java: term/q/text/search/query/value."""
    for pair in urlsplit(url).query.split("&"):
        key, _, value = pair.partition("=")
        if key in ("text", "q", "search", "query", "term", "value"):
            return unquote_plus(value)
    return ""


def name_mismatch(clean: str, name: str, title: str) -> bool:
    """Отказ по названию по правилу Java: одного слова мало, если значимых два."""
    if not name or not title or name == "товар с открытой страницы":
        return False
    words = significant_words(clean, name)
    if not words:
        return False
    card = title.lower().replace("ё", "е")
    have = sum(1 for w in words if w in card or (len(w) > 5 and w[:5] in card))
    return have < (2 if len(words) >= 2 else 1)


def test_a_significant_word_is_long_and_not_the_product_itself():
    """«Сушка» и «молоко» — общие слова, «самокат» и «сгущенным» — значимые."""
    clean = strip_comments(source())
    assert significant_words(clean, "Сушка самокат со сгущенным молоком 300 г") == \
        ["самокат", "сгущенным"]
    assert significant_words(clean, "Молоко Parmalat 1,8% 1 л") == ["parmalat"]
    assert significant_words(clean, "Молоко 3,2% 1 л") == []


def test_a_card_without_the_significant_words_is_not_pressed():
    """«Сушки Невская» — чужой товар: ни одного значимого слова, кнопку не жмём."""
    clean = strip_comments(source())
    tap = method_body(clean, "private void tapAdd(")
    check = tap.find("nameWhy(")
    assert check >= 0, "перед касанием нет строгой сверки названия"
    assert tap.find("packWhy(") > check, \
        "строгая сверка названия стоит после фасовки — отказ был бы неполным"
    assert tap.find("tapMarked(") > check, \
        "строгая сверка стоит после нажатия — чужой товар уже лёг бы"
    tail = tap[check:check + 1200]
    assert "pageDump(r," in tail, "отказ по чужому названию без дампа страницы"
    assert "cardWhy(" in tail, "дамп не встраивается в причину карточки"
    assert "lineDone(r, false" in tail, "отказ не доходит до отчёта"

    why = method_body(clean, "private static String nameWhy(")
    assert "на карточке другой товар: в наряде «" in why, "нет текста отказа"
    assert ", на карточке «" in why, "в отказе не названа карточка"
    assert "significantWords(" in why, "отказ не считает значимые слова"
    assert "товар с открытой страницы" in why, "заглушка не исключена из сверки"
    words = method_body(clean, "private static List<String> significantWords(")
    assert "length() < 5" in words or "length() >= 5" in words, \
        "значимым словом не названо слово от пяти букв"
    assert "commonWord(" in words, "общие продуктовые слова не исключаются"

    assert name_mismatch(clean, "Сушка самокат со сгущенным молоком 300 г",
                         "Сушки Невская сушка Простые 300г"), \
        "«Невская сушка» прошла бы за наряд — чужой товар лёг бы в корзину"
    assert not name_mismatch(clean, "Молоко Parmalat 1,8% 1 л",
                             "Молоко Parmalat 1,8% 1 л"), \
        "своя карточка отказана из-за строгой сверки"
    assert not name_mismatch(clean, "товар с открытой страницы", ""), \
        "заглушка «товар с открытой страницы» не исключена из сверки"


def test_the_second_query_in_the_report_is_the_actual_alt():
    """Отчёт печатал первый запрос как второй: берём запрос из адреса search_alt."""
    clean = strip_comments(source())
    why = method_body(clean, "private static String notFoundWhy(")
    assert 'line.optString("search_alt"' in why, "второй запрос не читается из наряда"
    assert not re.search(r"alt\s*=\s*name\s*;", why), \
        "второй запрос подменяется именем позиции — в отчёте снова первый"
    assert "второй запрос «" in why, "второй запрос не назван"
    assert "тоже пуст" in why, "вторая попытка не названа пустой"

    query = method_body(clean, "private static String searchQuery(")
    for key in ('"term"', '"value"', '"text"', '"q"'):
        assert key in query, f"searchQuery не смотрит параметр {key}"

    asked = query_of("https://5ka.ru/search/?text=Сушка+самокат+со+сгущенным+молоком+300+г")
    alt = query_of("https://5ka.ru/search/?text=Сушка+самокат")
    assert alt == "Сушка самокат"
    assert f"второй запрос «{asked}» тоже пуст" != f"второй запрос «{alt}» тоже пуст", \
        "первый и второй запрос в отчёте снова одинаковы"


def test_search_failures_pause_before_the_next_line():
    """Магнит: после неудачи поиска — пауза 1,5–2 с, а не сразу следующая строка."""
    clean = strip_comments(source())
    match = re.search(r"SEARCH_PAUSE_MS\s*=\s*(\d+)\s*;", clean)
    assert match, "пропала константа SEARCH_PAUSE_MS"
    pause = int(match.group(1))
    assert 1500 <= pause <= 2000, f"SEARCH_PAUSE_MS = {pause}, живой прогон просил 1,5–2 с"

    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert "searchPause" in done, "неудача поиска не помечается для паузы"
    assert re.search(r"searchPause\s*=\s*!ok", done), \
        "пауза ставится и на успех, и на отказ карточки — не только на поиск"
    assert '"search"' in done, "признак поиска не берётся из строки наряда"
    assert "pauseNext(r)" in done, "пауза не удлиняется после неудачи поиска"
    nxt = method_body(clean, "private void nextLine(")
    assert "searchPause = false" in nxt, \
        "пометка о поиске не сбрасывается — пауза осталась бы навсегда"

    helper = method_body(clean, "private static int pauseNext(")
    assert "SEARCH_PAUSE_MS" in helper and "Math.max(" in helper and "pauseAfter(" in helper, \
        "пауза не складывается с обычной паузой до следующей строки"


def test_the_vpn_wall_in_search_means_a_rate_limit():
    """«Выключите VPN» после серии поисков — частота запросов, а не мобильный интернет."""
    clean = strip_comments(source())
    body = method_body(clean, "private String wallText(String name, String wall, boolean")
    assert "ограничивает частоту запросов" in body, \
        "стена в поиске не названа ограничением частоты"
    assert "мобильный интернет" in body, \
        "прежний текст для предпроверки сети потерян"
    assert re.search(r"frequent\s*\?", body), "контекст стены не различается"

    ask = method_body(clean, "private void askCard(")
    assert "wallText(r.name, wall, true)" in ask, \
        "стена поиска идёт общим текстом про мобильный интернет"
    blank = method_body(clean, "private void blankWall(")
    assert "wallText(r.name, wall)" in blank and \
        "wallText(r.name, wall, true)" not in blank, \
        "предпроверка сети потеряла прежний текст"


def test_the_cart_sign_is_checked_once_after_the_tap():
    """Магнит neo: кнопка нажалась, а корзина не показала товар — отказ с дампом."""
    clean = strip_comments(source())
    start = clean.find("private static final String CART_SIGN_JS")
    assert start >= 0, "пропал CART_SIGN_JS — признак корзины не проверяется"
    end = clean.find("private void checkCartSign", start)
    assert end > start, "не найти конец CART_SIGN_JS"
    js = clean[start:end].lower()
    for word in ("в корзине", "товар в корзине", "badge", "count", "innertext"):
        assert word in js, f"CART_SIGN_JS не видит признак «{word}»"
    assert "json.stringify" in js, "CART_SIGN_JS не отдаёт объект"

    body = method_body(clean, "private void checkCartSign(")
    assert "карточка открылась, кнопка нажата, но сеть не показала товар в корзине" in body, \
        "нет текста отказа по признаку корзины"
    assert "pageDump(r," in body and "lineDone(r, false" in body, \
        "отказ по признаку корзины без дампа или без отчёта"
    assert "badged" in body and 'optBoolean("known")' in body, \
        "признак не сравнивается с прежним счётчиком"
    assert "verifyLine(r, calls)" in body, "после проверки не сверяется артикул"

    check = method_body(
        clean,
        "private void checkLine(final CartRun r, final String failure, "
        "final JSONArray before, final boolean waited)")
    assert "checkCartSign(r, calls)" in check, "после «положено» признак не проверяется"
    assert "badgeChecked" in check, "проверка признака не однократна"
    nxt = method_body(clean, "private void nextLine(")
    assert "badgeChecked = false" in nxt and "badged = 0" in nxt, \
        "признак корзины не сбрасывается на следующей позиции"
    tap = method_body(clean, "private void tapAdd(")
    assert "r.badged =" in tap, "прежний счётчик корзины не запоминается до касания"


def test_a_blank_card_page_is_named_as_not_delivered():
    """Самокат: прямой адрес открылся пустым — это не «просит войти»."""
    clean = strip_comments(source())
    start = clean.find("private static final String PICK_JS")
    end = clean.find("private static final String VERIFY_JS", start)
    assert start >= 0 and end > start, "не найти PICK_JS"
    js = clean[start:end]
    assert "empty:" in js or '"empty"' in js, "PICK_JS не отмечает пустую страницу"
    assert "innerText" in js and "document.title" in js, \
        "пустота страницы не проверяется по тексту и заголовку"
    assert "<40" in js, "у пустой страницы нет порога длины текста"

    tap = method_body(clean, "private void tapAdd(")
    assert "страница карточки не отдалась (пусто/бот-защита)" in tap, \
        "пустая карточка не названа отдельно"
    assert 'optBoolean("empty")' in tap, "tapAdd не смотрит на признак пустой страницы"

    blank = method_body(clean, "private void blankWall(")
    assert "страница карточки не отдалась" in blank, \
        "предпроверка сети не отличает пустую страницу от входа"
    assert blank.find("страница карточки не отдалась") < blank.find("просит войти"), \
        "пустая страница проверяется после входа — снова «просит войти»"


# ── Z7h: цена и наличие с карточки ───────────────────────────────────────────
#
# Живой прогон 21.10.2026: Пятёрочка по «Сушке самокат…» привела на карточку, товар
# лёг, но в отчёте не было ни цены, ни наличия — в базе строка завелась с
# price = NULL, in_stock = NULL. Причина на стороне телефона: `lineDone` брал цену
# только из `r.prices` (цен, выученных из ответов корзины сети). У Пятёрочки,
# Самоката и Дикси телефон жмёт «В корзину» прямо на карточке, ответа с корзиной
# нет — `r.prices` пуст, и цена не уходила.
#
# Теперь цена и наличие читаются из текста карточки, которую PICK_JS и так
# открывает. Разбор цены — тот же, что у серверной `_CARD_PRICE`
# (app/phonecart.py): «205 00 ₽» — 205.00, «1 748,98 ₽» — 1748.98,
# «299.99 руб» — 299.99. Проверка статическая, как и весь файл: Android-сборку
# здесь запускать нечем, а правила вынимаются из исходника и исполняются в тесте.

def card_price_pattern(clean: str) -> re.Pattern:
    """Выражение цены карточки — ровно то, что исполняет Java."""
    match = re.search(r'CARD_PRICE_RE\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', clean)
    assert match, "в MainActivity нет разбора цены карточки (CARD_PRICE_RE)"
    return re.compile(java_literal(match.group(1)), re.IGNORECASE)


def card_price(clean: str, text: str) -> float | None:
    """Цена из текста карточки по правилу Java.

    Сначала берутся все денежные подписи, что знает серверная `_CARD_PRICE`.
    Внутри каждой: запятая/точка — десятичный разделитель, пробелы — тысячи;
    без десятичной части последняя группа из двух цифр — копейки
    («205 00» → 205.00), иначе пробелы — тысячи («1 748» → 1748).
    Берётся ПОСЛЕДНЯЯ цена: у товара со скидкой первая — до скидки.
    """
    found = list(card_price_pattern(clean).finditer(" ".join(str(text or "").split())))
    if not found:
        return None
    raw = found[-1].group(1).strip().replace("\u00a0", " ")
    if "," in raw or "." in raw:
        raw = raw.replace(" ", "").replace(",", ".")
    else:
        parts = raw.split()
        if len(parts) > 1 and len(parts[-1]) == 2:
            raw = "".join(parts[:-1]) + "." + parts[-1]
        else:
            raw = "".join(parts)
    return round(float(raw), 2)


@pytest.mark.parametrize("text,want", [
    ("205 00 ₽", 205.0),
    ("1 748,98 ₽", 1748.98),
    ("299.99 руб", 299.99),
    ("899 ₽", 899.0),
    ("8 рулонов", None),
    ("-22% 4,95 Томатная паста Помидорка 250мл 250 мл 205 00 ₽", 205.0),
    # У товара со скидкой две цены: платит человек последнюю (CARDS_JS, 19.10.2026).
    ("629 00 ₽ 299 99 ₽", 299.99),
])
def test_the_phone_reads_the_price_from_the_card_text(text, want):
    """«205 00 ₽» — 205.00, «1 748,98 ₽» — 1748.98, «8 рулонов» — не цена."""
    clean = strip_comments(source())
    body = method_body(clean, "private static double cardPrice(")
    assert "CARD_PRICE.matcher(" in body, "цена не ищется общим выражением карточки"
    assert "replace(',', '.')" in body, "запятая не читается как десятичная точка"
    assert 'replace(" ", "")' in body, "пробелы-разделители тысяч не убираются"
    assert "length() == 2" in body, "копейки без точки («205 00») не распознаются"
    assert "Math.round(" in body, "цена не округляется до копеек"
    assert "while (m.find())" in body, "из двух цен карточки не берётся последняя"
    assert card_price(clean, text) == want, f"«{text}» разобрано неверно"


def test_the_report_price_falls_back_to_the_card_and_is_not_multiplied():
    """Цена из ответа корзины главнее; ответа не было — берём цену карточки."""
    clean = strip_comments(source())
    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert 'r.prices.optDouble(sku, 0)' in done, \
        "цена из ответа корзины больше не читается"
    assert re.search(r'if\s*\(\s*one\s*<=\s*0\s*\)\s*one\s*=\s*r\.cardPrice\s*;', done), \
        "при пустых r.prices цена карточки не подставляется"
    assert re.search(r'if\s*\(\s*one\s*>\s*0\s*\)\s*item\.put\("price",\s*one\)\s*;', done), \
        "нулевая цена пишется в отчёт или поле price пропало"
    assert not re.search(r'put\("price",\s*one\s*\*', done), \
        "цена умножается на количество — сервер берёт цену за упаковку"


def no_stock_words(clean: str) -> list[str]:
    """Слова «товара нет» — ровно те, что перечислены в Java."""
    match = re.search(r'NO_STOCK_WORDS\s*=\s*\{(.*?)\}\s*;', clean, re.S)
    assert match, "в MainActivity нет слов «нет в наличии» (NO_STOCK_WORDS)"
    return re.findall(r'"([^"]+)"', match.group(1))


def card_gone(clean: str, text: str) -> bool:
    """Говорит ли текст карточки, что товара нет, по правилу Java."""
    low = str(text or "").lower().replace("ё", "е")
    return any(word in low for word in no_stock_words(clean))


def test_the_phone_reads_no_stock_from_the_card_text():
    """«Больше нет» и «нет в наличии» — false; обычный текст — не знаем."""
    clean = strip_comments(source())
    words = no_stock_words(clean)
    for word in ("нет в наличии", "больше нет", "отсутств"):
        assert word in words, f"NO_STOCK_WORDS не знает «{word}»"
    body = method_body(clean, "private static boolean cardGone(")
    assert "toLowerCase" in body and "contains(" in body, \
        "наличие не ищется по тексту без учёта регистра"
    assert card_gone(clean, "Больше нет") is True
    assert card_gone(clean, "Нет в наличии") is True
    assert card_gone(clean, "Товар отсутствует") is True
    assert card_gone(clean, "899 ₽") is False


def test_the_report_says_no_stock_only_when_it_is_known():
    """false — карточка сказала «нет»; true — позиция легла; иначе поля нет."""
    clean = strip_comments(source())
    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert re.search(
        r'if\s*\(\s*r\.cardGone\s*\)\s*item\.put\("in_stock",\s*false\)\s*;\s*'
        r'else\s+if\s*\(\s*ok\s*&&\s*r\.put\s*>\s*0\s*\)\s*item\.put\("in_stock",\s*true\)',
        done, re.S), "наличие пишется не по правилу «нет / легло / не знаем»"
    assert done.count('.put("in_stock"') == 2, \
        "наличие пишется где-то ещё — «не знаем» стало бы false"


def test_price_and_stock_reset_on_each_line():
    """Иначе цена и «нет в наличии» прошлой строки уедут в следующую."""
    clean = strip_comments(source())
    nxt = method_body(clean, "private void nextLine(")
    assert "cardPrice = 0" in nxt, "цена карточки не сбрасывается на следующей строке"
    assert "cardGone = false" in nxt, \
        "признак «нет в наличии» не сбрасывается на следующей строке"


def test_the_card_returns_its_text_for_price_and_stock():
    """PICK_JS уже читает карточку — пусть отдаёт её текст рядом с заголовком."""
    clean = strip_comments(source())
    start = clean.find("private static final String PICK_JS")
    end = clean.find("private static final String VERIFY_JS", start)
    assert start >= 0 and end > start, "не найти PICK_JS"
    js = clean[start:end]
    assert "text:ct" in js, "карточка не отдаёт текст для цены и наличия"
    assert js.count("text:ct") >= 2, \
        "текст карточки не отдаётся в отказе — «Больше нет» без кнопки не увидеть"
    text_at = js.find("var ct=''")
    fail_at = js.find("if(!best){var near=")
    assert 0 <= text_at < fail_at, \
        "текст считается внутри ветки отказа — на успехе text был бы undefined"
    assert "innerText" in js, "текст карточки не берётся из страницы"
    assert "slice(0,600)" in js, "текст карточки отдаётся без предела"

    tap = method_body(clean, "private void tapAdd(")
    assert "cardPrice(" in tap, "tapAdd не читает цену из карточки"
    assert 'optString("text"' in tap, "tapAdd не смотрит текст карточки"
    assert "r.cardGone" in tap, "tapAdd не отмечает «нет в наличии»"


# ── Z8: серверный поиск товара ────────────────────────────────────────────────
#
# Телефон упирается в стены сетей: Лента и Дикси отвечают 403, Магнит отдаёт
# пустую SPA и после серии запросов показывает «Выключите VPN» (замеры
# 20–21.10.2026). Те же сети сервер обходит рабочими дорогами (app/storefind.py,
# POST /api/device/find): Магнит — webgate, Лента — свой MCP, Дикси — движок
# подсказок. Поэтому строку наряда без подтверждённого артикула телефон сначала
# спрашивает у сервера, и только потом идёт на страницу поиска сети. Ответ
# сервера точный — открываем найденную карточку; отказ — Лента не открывает сайт
# вовсе, а Магнит и Дикси идут прежним резервом с честной пометкой в отчёте.
#
# Проверка статическая, как и весь файл: Android-сборку здесь запускать нечем.

def test_the_phone_asks_the_server_before_opening_the_store_search():
    """Вызов /api/device/find стоит в searchLine до перехода на страницу поиска."""
    clean = strip_comments(source())
    body = method_body(clean, "private void searchLine(")
    assert 'call("/api/device/find", body' in body, \
        "телефон не спрашивает серверный поиск перед страницей сети"
    assert "go(r, searchUrl)" in body, "пропал прежний переход на страницу поиска"
    assert body.find('call("/api/device/find", body') < body.find("go(r, searchUrl)"), \
        "страница поиска открывается раньше, чем ответит сервер"
    assert "serverFind(r.code)" in body, \
        "серверный поиск не ограничен сетями, у которых он есть"


def test_only_magnit_lenta_and_dixy_ask_the_server():
    """Пятёрочку и Самокат серверный find не спрашивает: их ищет только телефон."""
    clean = strip_comments(source())
    match = re.search(r"SERVER_FIND\s*=\s*\{(.*?)\}\s*;", clean, re.S)
    assert match, "нет списка сетей с серверным поиском (SERVER_FIND)"
    codes = re.findall(r'"([^"]+)"', match.group(1))
    assert codes == ["magnit", "lenta", "dixy"], \
        f"серверный поиск спрашивают не те сети: {codes}"
    helper = method_body(clean, "private static boolean serverFind(")
    assert "SERVER_FIND" in helper and "return true" in helper, \
        "проверка сети не смотрит на список SERVER_FIND"


def test_the_server_question_carries_store_name_and_pack():
    """Тело — {store, name, pack_g}: сеть, название позиции и фасовка из наряда."""
    clean = strip_comments(source())
    body = method_body(clean, "private void searchLine(")
    assert 'call("/api/device/find", body' in body, \
        "тело запроса не то, что уходит в /api/device/find"
    for field in ('"store"', '"name"', '"pack_g"'):
        assert f'.put({field}' in body, f"в теле серверного поиска нет поля {field}"
    assert 'body.put("store", r.code)' in body, "сеть уходит не кодом сети"
    assert 'line.optString("name", "")' in body, "название позиции не из строки наряда"
    assert 'line.optDouble("pack_g", 0)' in body, "фасовка не из строки наряда"
    assert re.search(r'if\s*\(\s*pack\s*>\s*0\s*\)\s*body\.put\("pack_g"', body), \
        "нулевая фасовка кладётся в тело, хотя её надо пропускать"


def test_a_confident_server_answer_opens_the_found_card_and_clears_search():
    """ok и непустой url: строка берёт адрес сервера, чистит search и идёт в карточку."""
    clean = strip_comments(source())
    body = method_body(clean, "private void searchLine(")
    assert 'answer.optJSONArray("found")' in body, "ответ сервера не разбирает found"
    assert 'answer.optBoolean("ok")' in body, "ответ сервера не смотрит на ok"
    assert 'first.optString("url", "")' in body, "адрес карточки не берётся из found[0]"
    assert 'line.put("url", url).put("search", "")' in body, \
        "найденный адрес не записывается в строку или search не очищается"
    assert 'noteLine(line, "нашёл через сервер (уверенность "' in body, \
        "успех серверного поиска не помечается в note"
    assert 'first.optDouble("confidence", 0)' in body, \
        "в пометке нет уверенности сервера"
    assert "cardLine(r, line)" in body, \
        "после ответа сервера не идёт обычная логика карточки"
    assert body.find('line.put("url", url).put("search", "")') < body.find("cardLine(r, line)"), \
        "карточка открывается раньше, чем очищен search — searchLine зациклился бы"


def test_the_server_answer_saves_the_network_article_in_the_line():
    """Сервер вернул настоящий артикул сети (`found[0].sku`) — телефон обязан его сохранить.

    ЗАЧЕМ (21.10.2026). Строка наряда-поиска приходит с пустым `sku` (сервер её завёл
    как «найти поиском», app/phonecart.py `_search_lines`), и артикул после отчёта
    достаётся из адреса карточки (`fromurl.sku_of`). Получается не всегда: у Дикси
    старый формат `dixy.ru/product/<слаг>` числа не несёт — строка не заводится в
    каталоге. Ответ серверного поиска этот артикул уже знает (`found[0].sku`,
    app/storefind.py `_candidate`), поэтому телефон кладёт его в свою строку рядом с
    `url`, и `lineDone` уносит его в отчёт — серверу не надо угадывать по адресу.

    Пустой `sku` в ответе строку не трогает: `url` и очистка `search` остаются как
    прежде, ветка успеха не падает.
    """
    clean = strip_comments(source())
    body = method_body(clean, "private void searchLine(")

    assert 'String foundSku' in body, "артикул не читается из ответа серверного поиска"
    assert 'first.optString("sku"' in body, \
        "артикул берётся не из found[0] — другого места в ответе сервера нет"
    assert 'line.put("sku", foundSku)' in body, \
        "артикул сервера не кладётся в строку наряда — сервер снова достанет его из адреса"
    assert re.search(r'if\s*\(\s*!\s*foundSku\.isEmpty\(\)\s*\)\s*line\.put\("sku",\s*foundSku\)',
                     body), \
        "пустой артикул перезаписывает строку (или проверка на пустоту потеряна)"

    # Прежнее поведение не тронуто: адрес карточки и очистка поиска — те же слова.
    assert 'line.put("url", url).put("search", "")' in body, \
        "адрес карточки или очистка search изменились"
    assert body.find('line.put("sku", foundSku)') < body.find("cardLine(r, line)"), \
        "артикул пишется после cardLine — в отчёт попал бы пустой sku"

    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert 'line.optString("sku", "")' in done, \
        "lineDone больше не берёт артикул из строки наряда"
    assert '.put("sku", sku)' in done, "отчёт строки не несёт артикул"


def test_the_server_answer_saves_the_networks_own_name_in_the_line():
    """Сервер вернул карточку сети (`found[0].name`) — её имя сохраняется отдельным полем.

    ЗАЧЕМ (21.10.2026). `_learn` заводит строку в каталог сети и берёт имя из отчёта.
    Если отчёт несёт название НАШЕЙ позиции из наряда, база решит, что сеть называет
    товар так же: Дикси «Туалетная бумага Papia Балийский цветок» против нашего
    «Papia Bali Flower». Поэтому телефон кладёт имя найденной карточки в `found_name`,
    а `name` оставляет нашим — для отчёта и сверки карточки. Пустое не пишем.
    """
    clean = strip_comments(source())
    body = method_body(clean, "private void searchLine(")

    assert 'String foundName' in body, "имя карточки не читается из ответа серверного поиска"
    assert 'first.optString("name"' in body, \
        "имя берётся не из found[0] — другого места в ответе сервера нет"
    assert 'line.put("found_name", foundName)' in body, \
        "имя сети не кладётся в строку наряда отдельным полем"
    assert re.search(r'if\s*\(\s*!\s*foundName\.isEmpty\(\)\s*\)'
                     r'\s*line\.put\("found_name",\s*foundName\)', body), \
        "пустое имя перезаписывает строку (или проверка на пустоту потеряна)"
    assert body.find('line.put("found_name", foundName)') < body.find("cardLine(r, line)"), \
        "имя пишется после cardLine — в отчёт оно не попало бы"
    # Наше имя из строки наряда не подменяется: `name` — для отчёта и сверки карточки.
    assert 'line.put("name"' not in body, "название нашей позиции подменяется именем сети"

    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert 'line.optString("found_name", "")' in done, \
        "lineDone больше не берёт имя сети из строки наряда"
    assert '.put("found_name", foundName)' in done, "отчёт строки не несёт имя сети"
    assert re.search(r'if\s*\(\s*!\s*foundName\.isEmpty\(\)\s*\)'
                     r'\s*item\.put\("found_name",\s*foundName\)', done), \
        "пустое имя пишется в отчёт пустым ключом"


def test_a_server_refusal_keeps_lenta_off_the_site_but_leaves_the_old_reserve():
    """Лента: ок=false — сразу отчёт, без страницы. Магнит/Дикси — прежний резерв."""
    clean = strip_comments(source())
    body = method_body(clean, "private void searchLine(")
    lenta_at = body.find('"lenta".equals(r.code)')
    assert lenta_at >= 0, "нет отдельного решения для Ленты"
    go_at = body.find("go(r, searchUrl)", lenta_at)
    assert go_at > lenta_at, "Лента доходит до страницы поиска — а она отвечает 403"
    between = body[lenta_at:go_at]
    assert "lineDone(r, false, why" in between, \
        "отказ сервера для Ленты не уходит в отчёт"
    assert "return;" in between, "ветка Ленты не прекращает поиск сайта"

    fallback = body[body.find("return;", lenta_at):]
    assert "noteLine(line, why)" in fallback, \
        "Магнит/Дикси не дописывают причину сервера в note"
    assert 'line.put("server_why", why)' in fallback, \
        "причина сервера не сохраняется для отчёта об отказе"
    assert "whenReady(r, searchUrl" in fallback and "firstCard(r, line)" in fallback, \
        "резерв Магнита/Дикси потерял ожидание и разбор страницы поиска"


def test_the_server_reason_is_named_and_reaches_the_report():
    """Причина сервера — why/difference, с пометкой «сервер: …», и видна в отчёте."""
    clean = strip_comments(source())
    why = method_body(clean, "private static String serverWhy(")
    assert 'optString("why"' in why, "причина отказа сервера не читается"
    assert '"difference"' in why, "отличие лучшего кандидата не читается"
    assert '"сервер: "' in why or '"сервер: " +' in why, \
        "причина сервера не помечена словом «сервер»"
    assert "сервер не нашёл точного совпадения" in why, \
        "у пустого ответа сервера нет причины"

    done = method_body(
        clean,
        "private void lineDone(final CartRun r, boolean ok, String why, "
        "JSONArray calls, boolean taken)")
    assert 'line.optString("server_why", "")' in done, \
        "отчёт отказа не читает причину серверного поиска"
    assert "say.contains(server)" in done, \
        "причина сервера не проверяется на повтор"
    assert '.put("why", say)' in done, "причина сервера не попадает в отчёт строки"


def test_the_server_hint_is_what_the_phone_says_first():
    """Готовая фраза сервера (`hint`) важнее склейки `why` + `difference`.

    `hint` — уже собранная из фактов строка для человека («марка совпала,
    фасовка неизвестна; … — проверьте на экране „Связи“», app/storefind.py
    `_hint`). Приклеивать к ней `why` и `difference` значит сказать одно и то
    же дважды. При точном совпадении сервер отдаёт `hint` пустым — тогда
    работает прежняя склейка.
    """
    clean = strip_comments(source())
    why = method_body(clean, "private static String serverWhy(")

    assert 'optString("hint"' in why, "готовая подсказка сервера (`hint`) не читается"
    hint_at = why.find('optString("hint"')

    # Непустой hint — сразу ответ, а не склейка. Скобки у if необязательны.
    returned = re.search(r'if\s*\(\s*!\s*hint\.isEmpty\(\)\s*\)\s*\{?\s*'
                         r'return\s+"сервер: "\s*\+\s*hint\s*;', why)
    assert returned, "непустой hint не возвращается человеку как «сервер: …»"
    assert hint_at < returned.start(), "hint проверяется после того, как он прочитан"

    # hint решает раньше why/difference — иначе прежняя склейка успела бы победить.
    why_at = why.find('optString("why"')
    assert why_at >= 0, "причина сервера не читается"
    assert hint_at < why_at, "hint читается после why — приоритета нет"
    assert why.find('"difference"') > hint_at, \
        "отличие читается раньше hint — приоритета нет"

    # Пустой hint — ровно прежняя логика: why + «(отличие: difference)».
    tail = why[returned.end():]
    assert 'optString("why"' in tail, "при пустом hint причина why не читается"
    assert '"difference"' in tail, "при пустом hint отличие не читается"
    assert '(отличие: "' in tail, "при пустом hint отличие не приклеивается к why"
    assert '"сервер не нашёл точного совпадения"' in tail, \
        "у пустого ответа сервера нет причины"


# ── Z9: ссылка на готовую корзину Ленты ───────────────────────────────────────
#
# Витрина Ленты отвечает телефону 403 (замеры 20–21.10.2026), и строка наряда в
# корзину не ложится. Сервер умеет собрать корзину своим MCP (app/storefind.cart_link)
# и отдать ссылку: на ней те же позиции с количеством, человеку остаётся нажать одну
# кнопку. Поэтому последним шагом наряда Ленты телефон спрашивает
# /api/device/cartlink и говорит в отчёте, что увидел: товары или стену. Шаг
# необязательный: не ответил сервер — наряд всё равно заканчивается чтением корзины.
#
# Проверка статическая, как и весь файл: Android-сборку здесь запускать нечем.

def test_the_lenta_link_is_the_last_step_of_the_run():
    """Ссылку спрашивают там, где раньше сразу открывалась корзина, — после всех строк."""
    clean = strip_comments(source())
    nxt = method_body(clean, "private void nextLine(")
    assert re.search(r"if\s*\(\s*r\.at\s*>=\s*r\.lines\.length\(\)\s*\)\s*\{\s*"
                     r"lentaCartLink\s*\(\s*r\s*\)", nxt), \
        "конец наряда не спрашивает ссылку на корзину Ленты"
    end = method_body(clean, "private void lentaCartLink(")
    assert 'call("/api/device/cartlink", body' in end, \
        "последний шаг наряда не зовёт /api/device/cartlink"


def test_the_link_request_carries_the_run_items():
    """Тело — {store, items:[{product_id, name, qty, pack_g?}]}: строки наряда как есть."""
    clean = strip_comments(source())
    body = method_body(clean, "private void lentaCartLink(")
    assert 'body.put("store", r.code)' in body, "сеть уходит не кодом сети"
    assert "for (int i = 0; i < r.lines.length(); i++)" in body, \
        "позиции берутся не из наряда"
    for field in ('"name"', '"qty"', '"pack_g"', '"product_id"'):
        assert f'.put({field}' in body, f"в теле ссылки нет поля {field}"
    assert 'line.optDouble("qty", 0)' in body, "количество не из строки наряда"
    assert re.search(r'if\s*\(\s*pack\s*>\s*0\s*\)\s*one\.put\("pack_g"', body), \
        "нулевая фасовка кладётся в тело, хотя её надо пропускать"


def test_a_built_link_is_opened_and_the_page_is_checked():
    """ok и непустой url: открыть ссылку и посмотреть, пустил ли сайт с этого подключения."""
    clean = strip_comments(source())
    body = method_body(clean, "private void lentaCartLink(")
    assert 'answer.optBoolean("ok")' in body, "ответ сервера не смотрит на ok"
    assert 'answer.optString("url", "")' in body, "адрес ссылки не берётся из ответа"
    opened = method_body(clean, "private void openCartLink(")
    assert "go(r, url)" in opened, "собранная ссылка не открывается"
    assert "whenReady(r, url" in opened, "не ждём загрузку страницы ссылки"
    assert "WALL_JS" in opened, "403/стена на странице ссылки не проверяется"
    assert "CART_LINK_JS" in opened, "товары на странице ссылки не проверяются"
    assert "CARTLINK_VISIBLE" in opened and "CARTLINK_BLOCKED" in opened, \
        "два исхода страницы не различаются"


def test_both_link_notes_are_the_words_the_owner_reads():
    """Слова пометок — ровно те, что человек увидит в отчёте строки Ленты."""
    clean = strip_comments(source())
    assert '"корзина собрана сервером, товары видны"' in clean, \
        "нет пометки «товары видны»"
    assert '"корзина собрана сервером, сайт не пустил с этого подключения"' in clean, \
        "нет пометки «сайт не пустил»"
    body = method_body(clean, "private void lentaCartLink(")
    assert '"Лента: сервер не собрал ссылку — "' in body, \
        "отказ сервера не назван в отчёте Ленты"
    assert 'answer.optString("why", "")' in body, "причина отказа не читается"
    assert 'answer.optString("note", "")' in body, \
        "пометка ответа (например «в ссылку не вошло N поз.») не читается"


def test_the_link_note_reaches_the_run_report():
    """Пометка ложится в note последней строки отчёта — её видит сервер и человек."""
    clean = strip_comments(source())
    note = method_body(clean, "private static void linkNote(")
    assert "r.items.optJSONObject(r.items.length() - 1)" in note, \
        "пометка пишется не в строку отчёта"
    assert '.put("note"' in note, "пометка не ложится в note строки"
    assert "r.linkNote" in note, "пометка не остаётся в наряде"


def test_the_link_step_never_derails_the_run():
    """Не ответил сервер — наряд всё равно кончается: таймер, отказы, прежняя корзина."""
    clean = strip_comments(source())
    body = method_body(clean, "private void lentaCartLink(")
    assert "CARTLINK_WAIT_MS" in body, "у последнего шага нет срока: наряд мог бы ждать сервер"
    assert "handler.removeCallbacks" in body, "срок не снимается, когда сервер ответил"
    assert body.count("endOfRun(r)") >= 3, \
        "хоть один путь отказа не возвращает наряд к завершению"
    assert "catch (Exception ignored)" in body, "тело запроса падает вместе с нарядом"

    end = method_body(clean, "private void endOfRun(")
    assert "r.linkDone = true" in end, "шаг не помечается сделанным — возможен второй"
    assert "openCart(r)" in end, "наряд не заканчивается прежним чтением корзины"


def test_only_lenta_asks_for_the_link():
    """Остальные сети идут прежним путём: openCart без лишнего запроса."""
    clean = strip_comments(source())
    body = method_body(clean, "private void lentaCartLink(")
    assert '"lenta".equals(r.code)' in body, "сеть не проверяется — ссылку просили бы все"
    assert body.find('"lenta".equals(r.code)') < body.find('call("/api/device/cartlink"'), \
        "запрос ссылки стоит раньше проверки сети"


def test_a_second_pass_asks_for_the_link_again():
    """Пустая корзина заставила класть заново — последний шаг тоже повторяется."""
    clean = strip_comments(source())
    read = method_body(clean, "private void readCart(")
    again = read[read.find("r.again = true"):]
    assert "r.linkAsked = false" in again, \
        "на повторном проходе ссылка не спросится — флаг остался с прошлого"
    assert "r.linkDone = false" in again, \
        "на повторном проходе наряд сразу завершится, не спросив ссылку"
