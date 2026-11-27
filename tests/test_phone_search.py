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
GRADLE = os.path.join(ROOT, "android", "app", "app", "build.gradle")


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


def test_nextline_opencart_readcart_start_with_superseded_check():
    """nextLine, openCart и readCart начинают с проверки superseded(r)."""
    clean = strip_comments(source())
    for method in ("private void nextLine(", "private void openCart(", "private void readCart("):
        body = method_body(clean, method)
        alive_idx = body.find("!alive(r)")
        super_idx = body.find("superseded(r)")
        assert alive_idx != -1, f"метод {method} не содержит проверки alive(r)"
        assert super_idx != -1, f"метод {method} не содержит проверки superseded(r)"
        assert super_idx > alive_idx, f"в {method} superseded(r) должна идти после alive(r)"
        assert "supersede(r)" in body[:super_idx + 60]


def test_nextline_calls_failed_recently_only_under_slot_check():
    """nextLine зовёт failedRecently только под r.slot != null."""
    clean = strip_comments(source())
    body = method_body(clean, "private void nextLine(")
    assert "failedRecently(r, line)" in body
    call_idx = body.find("failedRecently(r, line)")
    slot_idx = body.rfind("r.slot != null", 0, call_idx)
    assert slot_idx != -1, "вызов failedRecently не находится под проверкой r.slot != null"
    assert "laterLine(r, line, failed)" in body


def test_linedone_calls_notefail():
    """lineDone зовёт noteFail."""
    clean = strip_comments(source())
    body = method_body(clean, "private void lineDone(final CartRun r, boolean ok, String why, JSONArray calls, boolean taken)")
    assert "noteFail(r, line," in body


def test_lentacartlink_skips_in_background_slot():
    """В lentaCartLink условие пропуска содержит r.slot != null."""
    clean = strip_comments(source())
    body = method_body(clean, "private void lentaCartLink(")
    assert "r.slot != null" in body
    check_line = next(line for line in body.splitlines() if "r.linkAsked" in line)
    assert "r.slot != null" in check_line


def test_opencart_calls_apitotal():
    """openCart зовёт apiTotal(r, api)."""
    clean = strip_comments(source())
    body = method_body(clean, "private void openCart(")
    assert "apiTotal(r, api)" in body


def test_finishrun_nototal_counter_under_cartread():
    """В finishRun счётчик nototal стоит под r.cartRead."""
    clean = strip_comments(source())
    body = method_body(clean, "private void finishRun(")
    nototal_idx = body.find("nototalKey")
    assert nototal_idx != -1, "в finishRun нет nototalKey"
    check_idx = body.rfind("r.cartRead", 0, nototal_idx)
    assert check_idx != -1, "nototalKey не находится под проверкой r.cartRead"


def test_rest_loads_robots_txt():
    """rest грузит \"/robots.txt\"."""
    clean = strip_comments(source())
    body = method_body(clean, "private void rest(")
    assert '"/robots.txt"' in body
    assert "storedTpl(s.code)" in body


def test_askplan_background_and_search_later():
    """askPlan кладёт "background", а разбор наряда (applyPlan, APK 1.27) в цикле search пишет в r.later при slot."""
    clean = strip_comments(source())
    body = method_body(clean, "private void askPlan(")
    assert '"background"' in body
    assert "r.slot != null" in body
    assert "applyPlan(r, plan)" in body
    parse = method_body(clean, "private void applyPlan(")
    assert "r.slot != null ? r.later : r.lines" in parse


def test_begin_moves_later_to_lines_when_empty():
    """begin переносит later в lines при пустых lines."""
    clean = strip_comments(source())
    body = method_body(clean, "private void begin(")
    assert "r.lines.length() == 0 && r.later.length() > 0" in body
    assert "r.lines = r.later" in body
    assert "r.tail = true" in body


def test_taps_after_a_failed_request_wait_for_the_other_request_chains():
    """Замер 09.11.2026: касания Магнита после отказа запроса оборвали запрос Пятёрочки в общем процессе окон.
    С 1.59 так же, уже с нарядом, ждёт и сеть без запросов, а сборка, которой класть и снимать нечего, не ждёт:
    Впрок без знакомых товаров ждал 5,45 с ради «не пошла» (эмулятор, 15.11.2026)."""
    clean = strip_comments(source())
    begin = method_body(clean, "private void begin(")
    head = [line.strip() for line in begin.strip().splitlines()[:3]]
    assert head == ["if (endBlocked(r)) return;", "loadLedger(r);", "if (heldForApi(r)) return;"], \
        "«снять своё» (hasExcess) — по записи положенного; закрытый защитой адрес не ждёт ничего"
    held = method_body(clean, "private boolean heldForApi(")
    assert "r.cartApiDone || cartApiOrigin(r.code) == null" in held, "ждут только касания: после запросов и без них"
    assert "!work" in held, "сборка без дела не ждёт"
    assert "API_HEAD_MS" in held, "ожидание без предела задержит сеть навсегда"
    running = method_body(clean, "private boolean apiRunning(")
    assert "o == self" in running, "сеть не должна ждать сама себя"


def test_tail_lines_that_failed_recently_go_straight_into_the_report():
    """Замер 10.11.2026: хвост ради недавно не найденных шёл после каждой правки — ничего не клал,
    а слал второй отчёт и занимал окно сети. Такие строки уходят в первый отчёт, хвост — только
    ради тех, что пора пробовать."""
    clean = strip_comments(source())
    finish = method_body(clean, "private void finishRun(")
    assert 0 <= finish.find("settleLater(r)") < finish.find("final boolean early")
    settle = method_body(clean, "private void settleLater(")
    assert "failedRecently(r, line)" in settle and "r.later = due" in settle
    failed = method_body(clean, "private String failedSince(")
    assert "notFound ? FRESH_MS : FAIL_AGAIN_MS" in failed, "ненайденное поиском помним, как сервер, 6 часов"
    assert 'rec.optBoolean("wall")' in failed, "стена сети — не «товара нет»: её помним недолго"


def test_request_failures_and_leftovers_wait_for_the_report():
    """Замер 10.11.2026: после отказа запроса Пятёрочка 495 с клала касаниями — отчёт ждал, а
    витрина в её окне держала запросы соседних сетей. Касания идут хвостом, после отчёта."""
    clean = strip_comments(source())
    body = method_body(clean, "private void handleCartApiResult(")
    assert "apiGaveUp(r, why + r.healNote + peekNote(r.slot))" in body
    assert re.search(r"toTail\(r, rest\);\s+finishRun\(r, cartObj, \"\"\);", body)
    gave_up = method_body(clean, "private void apiGaveUp(")
    assert re.search(r'toTail\(r, r\.lines\);\s+finishRun\(r, new JSONObject\(\), ""\);', gave_up)
    assert "r.apiOver = true" in gave_up, "иначе окна с касаниями ждали бы эту сеть до API_HEAD_MS"
    # Адрес закрыт защитой (1.61): касания и поиск пошли бы на тот же адрес (эмулятор 15.11.2026: три минуты карточек
    # по /xpvnsulc/) — фоновая сборка кончается отказом раньше отчёта и хвоста; причина отказа — в журнале до конца.
    assert gave_up.index("Log.i(TAG, r.name + \": запросом не вышло (\" + why + \")\"") < gave_up.index("if (endBlocked(r)) return;") \
        < gave_up.index("toTail(r, r.lines);")
    ended = method_body(clean, "private boolean endBlocked(")
    assert "if (r.slot == null || !blocked(r.code)) return false;" in ended, "сборку человека («Собрать») не трогаем"
    assert re.search(r'endBackground\(r, "failed", r\.name \+ ": защита сети закрыла адрес, пауза ещё " \+ left \+ " мин"\);\s+'
                     r'return true;', ended)
    # Тот же конец — в начале сборки (сеть без запросов и поиск хвостом). Хвост, блок которому поставило лечение, просто
    # не идёт: отказ затёр бы на сервере строку уже отправленного отчёта головы.
    assert method_body(clean, "private void begin(").strip().startswith("if (endBlocked(r)) return;")
    tail = method_body(clean, "private void startTail(")
    assert re.search(r"if \(blocked\(s\.code\)\) \{\s+Log\.i\(TAG, .*?\);\s+rest\(s\);\s+return;\s+\}", tail)
    assert tail.index("if (blocked(s.code)) {") < tail.index("s.run = r;")
    assert "endBlocked" not in tail and "sendEnd" not in tail
    # Молчаливых уходов на касания нет: скрипт не запустился, сеть не ответила — тот же выход.
    assert "begin(r)" not in method_body(clean, "private void runCartApiPayload(")
    # Магнит и Самокат без входа: касания легли бы в корзину гостя — сборка кончается шагом человека.
    assert re.search(r'"auth"\.equals\(stage\) && \("magnit"\.equals\(r\.code\) \|\| "samokat"\.equals\(r\.code\)\)\) \{\s+'
                     r'endBackground\(r, "failed", why\);', body)
    tail = method_body(clean, "private static void toTail(")
    assert "r.lines = kept" in tail and "r.later = later" in tail


def test_a_stale_run_stopped_without_a_plan_forgets_the_old_done_mark():
    """Добавил товар и тут же убрал: отпечаток равен давно собранной корзине, и collect пропустил бы
    сборку — сеть застыла бы на «собираю» (замер 11.11.2026, Пятёрочка и Дикси)."""
    fast = method_body(strip_comments(source()), "private void fastChange(")
    assert re.search(r'stopBackground\(s, true\);\s+memory\(\)\.edit\(\)\.remove\("done\." \+ s\.code\)\.apply\(\);',
                     fast)


def test_a_touch_run_rechecks_one_old_refusal_and_opens_connections_early():
    """Замер 11.11.2026: раз в полчаса сеть перепроверяла все давние отказы разом — Дикси 8 запросов
    и 10,8 с при цели 5 с. По касанию — один за сборку, тронутая позиция первой. Соединения с узлами
    корзин открываются по касанию экрана: первое чтение Пятёрочки после простоя 0,7 с вместо 4,0 с."""
    clean = strip_comments(source())
    api = method_body(clean, "private void cartApi(")
    assert 'line.optInt("product_id", 0) == r.changedPid' in api
    assert "touched = ids.optInt(k) == r.changedPid" in api, "слитый дубль артикула: тронут второй товар строки"
    assert 'String failed = line == again ? "" : failedBefore(r, line, "apifail.");' in api
    assert "ever ||" in method_body(clean, "private String failedSince(")
    start = method_body(clean, "private void startBackground(")
    assert "r.changedPid = s.changedPid" in start
    pre = method_body(clean, "private void preconnect(")
    assert "PRECONNECT_MS" in pre and "l.rel='preconnect'" in pre and "s.run != null" in pre
    assert "XMLHttpRequest" not in pre and "fetch(" not in pre, "подсказка не шлёт запросов сети"
    touch = method_body(clean, "public boolean dispatchTouchEvent(")
    assert "ACTION_DOWN" in touch and "preconnect()" in touch


def test_session_refresh_does_not_exempt_a_run_from_its_deadline():
    clean = strip_comments(source())
    result = method_body(clean, "private void handleCartApiResult(")
    before_refresh, refresh = result.split("&& !r.cartApiRefreshed) {", 1)
    refresh = refresh.split('if ("address".equals(stage))', 1)[0]
    assert "r.apiOver = true;" in before_refresh and "saveLedger(r);" in before_refresh
    assert "r.apiOver = false;" not in refresh, "ожидание входа должно завершаться по дедлайну"

    launch = method_body(clean, "private void runCartApiPayload(")
    assert (launch.index("if (collectionBudgetExpired(r)) return;")
            < launch.index("r.apiOver = false;")
            < launch.index("r.view.evaluateJavascript(execCode")), "касание не должно обрывать начатую запись"


def test_a_chain_whose_cart_host_just_refused_goes_straight_to_taps():
    """Узел корзин Пятёрочки отвечал 403 (защита сети), а каждая правка повторяла запрос: 5–9 с впустую,
    общий процесс окон занят, и соседние сети выходили к пределу 5 с (замер 11.11.2026: Дикси 4,8 с
    вместо 1,9). После такого отказа сеть API_WALL_MS идёт сразу касаниями."""
    text = source()
    clean = strip_comments(text)
    api = method_body(clean, "private void cartApi(")
    wall = api.find('wallUp(r.code)')
    assert 0 < wall < api.find("status(r,"), "проверка стоит до запросов и до строки «кладу запросами сети»"
    assert 'apiGaveUp(r, (blocked(r.code) ? "защита сети закрыла адрес узла корзин" : "узел корзин сети недавно не отвечал") + peekNote(r.slot))' in api
    result = method_body(clean, "private void handleCartApiResult(")
    # Только Пятёрочке и только после обновления входа: у прочих сетей «обрыв» — обычно связь, и после
    # секундной потери интернета они три минуты шли бы касаниями.
    assert re.search(r'if \(pyatNet\) \{\s+memory\(\)\.edit\(\)\.putLong\("apiwall\." \+ r\.code, '
                     r'System\.currentTimeMillis\(\)\)\.apply\(\);', result)
    assert result.index('"apiwall." + r.code, System.currentTimeMillis())') \
        < result.index("apiGaveUp(r, why + r.healNote + peekNote(r.slot));")
    # Куку не лечили — журнал телефона скажет, почему: отказ не быстрый, повтор входа или лечили недавно (1.50).
    assert re.search(r'if \(r\.healNote\.isEmpty\(\)\) \{\s+r\.healNote = "; куку не лечил: "', result)
    assert '"отказ не быстрый или связи нет"' in result and 'res.optInt("requests", 0)' in result and "r.cartApiRefreshed ?" in result
    assert 2 <= int(re.search(r"API_WALL_MS = (\d+)L \* 60 \* 1000", text).group(1)) <= 10


def test_request_chain_windows_stay_and_the_kept_plan_lives_as_long_as_a_fresh_run():
    """Холодная правка (замер 10.11.2026): окно сети убрано через 10 минут, наряд в памяти старше
    получаса — и правка шла 30 с вместо 3. Окно сети с запросами не убираем, наряд годен, пока
    свежа сборка."""
    text = source()
    rest = method_body(strip_comments(text), "private void rest(")
    assert re.search(r"if \(cartApiOrigin\(s\.code\) != null\) return;\s+s\.sleep = \(\) ->", rest)
    hours = {name: int(re.search(name + r" = (\d+)L \* 3600 \* 1000", text).group(1))
             for name in ("PLAN_MS", "FRESH_MS")}
    assert hours["PLAN_MS"] >= hours["FRESH_MS"]
    start = method_body(strip_comments(text), "private void startBackground(")
    assert "keptPlan(s.code, r.stamp, r.basket)" in start


def test_a_missing_or_old_plan_is_fetched_when_the_basket_opens_not_on_the_first_tap():
    """После обновления приложения наряды в памяти сброшены (memory), а наряд старше PLAN_MS негоден:
    первая правка шла за ним на сервер (замер 11.11.2026: Дикси 4,3–6,0 с вместо 1,6). Сеть с запросами
    берёт наряд, когда корзина открылась и собирать нечего, — без сборки: окно, отчёт и запись в корзину
    сети тут лишние, а касание во время такой сборки оборвало бы её и пошло бы за нарядом второй раз."""
    clean = strip_comments(source())
    decide = method_body(clean, "private JSONObject decide(")
    skipped = decide[decide.index('(done.optBoolean("ok") && fresh)'):
                     decide.index('"уже собрано для этой корзины (свежее)"')]
    assert "if (order == null) fetchPlan(s, stamp, basket);" in skipped, \
        "после «Заказать» правка по касанию наряд не выводит"
    fetch = method_body(clean, "private void fetchPlan(")
    # Только сети с запросами, не чаще раза в минуту и только когда наряда нет или он старше PLAN_MS.
    assert "cartApiOrigin(s.code) == null" in fetch and "now - s.planAsked < 60000" in fetch
    assert fetch.count('memory().getLong("planAt." + s.code, 0) <= PLAN_MS') == 2, "и до запроса, и перед записью"
    assert 'call("/api/device/cartplan"' in fetch and '.put("background", true)' in fetch
    # Своё без адреса карточки наряд несёт и здесь, как в askPlan: при отказе запросов его снимают касаниями.
    assert 'item.optString("u", "").isEmpty()) dropSkus.put(sku);' in fetch and 'body.put("drop", dropSkus)' in fetch
    # Ответ про корзину, которую уже поправили, в память не идёт: затёр бы наряд новее.
    assert '!stamp.equals(lastCollect.optString("stamp", ""))' in fetch
    assert '!stamp.equals(new JSONObject(text).optString("stamp", "-"))' in fetch
    assert '.putLong("planAt." + s.code, System.currentTimeMillis())' in fetch
    for word in ("new CartRun", "hiddenView(", "soon(", "failRun(", '"done.'):
        assert word not in fetch, f"наряд заранее — не сборка: {word}"
    # Обновление сбрасывает возраст нарядов — его проверяет fetchPlan — и стену apiwall.* прежней версии: на телефоне
    # владельца первые минуты 1.51 прошли за стеной отказа 1.49 (14.11.2026). Блок защиты (apiblock.*) остаётся.
    memory = method_body(clean, "private SharedPreferences memory(")
    assert 'if (k.startsWith("planAt.") || k.startsWith("apiwall.")) e.remove(k);' in memory
    assert "apiblock" not in memory
    # Сборка, взявшая наряд у сервера, кладёт его в память сама: из него правка по касанию выводит следующий.
    ask = method_body(clean, "private void askPlan(")
    assert re.search(r'if \(r\.slot != null && r\.linesOverride == null\) \{\s+'
                     r'memory\(\)\.edit\(\)\.putString\("plan\." \+ r\.code, text\)\s+'
                     r'\.putLong\("planAt\." \+ r\.code, System\.currentTimeMillis\(\)\)\.apply\(\);', ask)


def test_opencart_checks_later_before_apitotal():
    """openCart проверяет r.later.length() > 0 раньше apiTotal(r, api)."""
    clean = strip_comments(source())
    body = method_body(clean, "private void openCart(")
    assert "r.later.length() > 0" in body
    later_idx = body.find("r.later.length() > 0")
    api_idx = body.find("apiTotal(r, api)")
    assert later_idx != -1 and api_idx != -1
    assert later_idx < api_idx, "r.later.length() должно проверяться раньше apiTotal"


def test_finishrun_early_and_starttail():
    """finishRun содержит "ищу в сети после знакомых", хвост через tailLater(r) и remember
    под !early. tailLater зовёт startTail(r) после TAIL_QUIET_MS, если правок за это время
    не было: хвост грузит тяжёлые страницы и задерживал правку в соседних сетях (1.25)."""
    clean = strip_comments(source())
    body = method_body(clean, "private void finishRun(")
    assert '"ищу в сети после знакомых"' in body
    assert "tailLater(r)" in body
    assert "!early" in body
    line_with_remember = next(line for line in body.splitlines() if "remember(r, " in line)
    assert "!early" in line_with_remember
    later = method_body(clean, "private void tailLater(")
    assert "startTail(r)" in later
    assert "TAIL_QUIET_MS" in later
    assert "s.pending.isEmpty()" in later


def test_an_edit_frees_the_tail_window_at_once_and_the_tail_waits_for_idle_fingers():
    """Замер 12.11.2026 на эмуляторе: правка при идущем хвосте — 4,6–5,6 с во всех сетях против
    1,2–2,0 с без него. Страницу поиска, брошенную хвостом, новая сборка уводила на robots.txt только
    через 1,1 с. Теперь её уводит сам supersede, новая сборка эту загрузку не начинает заново, а хвост
    не стартует, пока экрана касались меньше TAIL_QUIET_MS назад."""
    clean = strip_comments(source())
    drop = method_body(clean, "private void supersede(")
    assert re.search(r'go\(r, origin \+ "/robots\.txt"\);\s+s\.robotsAt = SystemClock\.uptimeMillis\(\);', drop)
    assert drop.index("go(r, origin") < drop.index("rest(s)"), "сначала окно, потом новая сборка"
    assert '!(origin + "/robots.txt").equals(r.view.getUrl())' in drop, "окно на robots.txt не перезагружаем"
    api = method_body(clean, "private void cartApi(")
    assert "if (!onTheWay) go(r, robots);" in api
    assert "robots.equals(r.view.getUrl())" in api, "окно за это время могли увести на пустую страницу"
    later = method_body(clean, "private void tailLater(")
    assert "long quiet = now - touchedAt;" in later
    assert "if (quiet < TAIL_QUIET_MS && now < until) handler.postDelayed(this, TAIL_QUIET_MS - quiet);" in later
    assert "3L * TAIL_QUIET_MS" in later, "касаний ждём не вечно: незнакомые нужны и тому, кто листает"
    assert "touchedAt = SystemClock.uptimeMillis();" in method_body(clean, "public boolean dispatchTouchEvent(")


def test_the_edit_time_is_counted_from_the_finger():
    """Замер 12.11.2026: при идущем хвосте обработчик страницы опаздывал на 1,1 с, и отчёт называл
    4,3–5,0 с там, где от касания прошло 5,6–6,2 с. Время правки идёт от последнего касания экрана;
    касание старше 10 с правку не начинало."""
    fast = method_body(strip_comments(source()), "private void fastChange(")
    assert re.search(r"long at = SystemClock\.uptimeMillis\(\);\s+if \(at - touchedAt < 10000\) at = touchedAt;", fast)
    assert "s.changedAt = at;" in fast and "s.changedAt = SystemClock" not in fast
    # Новый товар идёт без «правки по касанию»: время ему ставит decide, и только сети, которая эту корзину
    # соберёт; у остальных оно стирается, иначе ушло бы в отчёт другой правки.
    plan = method_body(strip_comments(source()), "private JSONObject decide(")
    assert plan.index("final boolean changed = lastCollect != null") < plan.index("lastCollect = payload;")
    assert 'lastCollect.optInt("basket", -1) == payload.optInt("basket", -2)' in plan, "другая корзина — не правка"
    assert re.search(r"if \(!started\.contains\(s\.code\)\) \{\s+if \(s\.soon == null\) s\.changedAt = 0;\s+"
                     r"\} else if \(changed && s\.changedAt == 0 && now - touchedAt < 10000\s+"
                     r"&& \(s\.run == null \|\| !stamp\.equals\(s\.run\.stamp\)\)\) \{\s+s\.changedAt = touchedAt;", plan)


def test_an_edit_of_a_product_the_chain_does_not_get_keeps_the_plan():
    """Правка товара, которого сеть не знает, ждала сервера: «−» у товара из поиска (приложение не знало,
    ушёл ли он) и любая правка товара, которого в наряде нет вовсе (у Перекрёстка нет адреса поиска, у
    Самоката поиск выключен). Замер 12.11.2026: 5,6–6,2 с во всех сетях. Остался ли товар, говорит страница
    (gone), чего сеть не получит — наряд (rest); со старой страницей и старым нарядом — через сервер."""
    plan = method_body(strip_comments(source()), "private JSONObject changedPlan(")
    # Товар с запасным (swapped) — всегда к серверу: в наряде вместо него строка запасного под другим номером.
    assert re.search(r'if \(seen \|\| s\.optBoolean\("swapped"\)\s+'
                     r'\|\| \("minus"\.equals\(verb\) && c\.optBoolean\("gone", true\)\)\) return null;', plan)
    assert 'if (!"remove".equals(verb)) left.put(s);' in plan
    assert re.search(r'JSONArray rest = plan\.optJSONArray\("rest"\);\s+for \(int i = 0; !seen && rest != null '
                     r'&& i < rest\.length\(\); i\+\+\) seen = rest\.optInt\(i\) == pid;\s+if \(!seen\) return null;',
                     plan)


def test_starttail_sets_fields_and_checks_pending():
    """startTail ставит r.tail = true, s.run = r, r.linkDone = false и проверяет s.pending."""
    clean = strip_comments(source())
    body = method_body(clean, "private void startTail(")
    assert "r.tail = true" in body
    assert "s.run = r" in body
    assert "r.linkDone = false" in body
    assert "s.pending" in body
    # Строки поиска дописываются в конец наряда: по r.lines снятие лишнего (wantOf) знает,
    # что оставить. Заменой хвост снял бы знакомые товары, положенные первым проходом.
    assert "r.lines.put(r.later.opt(i))" in body
    assert "r.at = from" in body
    assert "r.lines = r.later" not in body


def test_endofrun_keeps_excess_while_tail_ahead():
    """Пока впереди хвост поиска, найденное прошлым поиском не снимается: строка его товара
    ещё без артикула, и товар снялся бы, чтобы тут же лечь снова. С 1.25 это решает
    searchedAgain по product_id, а не запрет всего снятия: уменьшенное снимается и при
    ненайденных позициях."""
    clean = strip_comments(source())
    body = method_body(clean, "private void endOfRun(")
    assert "shouldDrop(r) && hasExcess(r)" in body
    again = method_body(clean, "private boolean searchedAgain(")
    assert "r.later" in again and "product_id" in again
    for header in ("private boolean hasExcess(", "private void dropExcess("):
        assert "!searchedAgain(r, sku)" in method_body(clean, header), header


def test_askcard_calls_superseded():
    """askCard зовёт superseded(r)."""
    clean = strip_comments(source())
    body = method_body(clean, "private void askCard(")
    assert "superseded(r)" in body


def test_build_gradle_version_1_23():
    """build.gradle — не ниже 1.23 (versionCode 27): карточка Пятёрочки с переадресацией."""
    with open(GRADLE, encoding="utf-8") as fh:
        text = fh.read()
    code = re.search(r"versionCode\s+(\d+)", text)
    assert code and int(code.group(1)) >= 27, "versionCode должен быть не ниже 27"


def test_same_path_handles_pyaterochka_redirect():
    """Карточка Пятёрочки с переадресацией не ждёт 30 с: whenReady и cardLine зовут samePath."""
    clean = strip_comments(source())
    when_ready = method_body(clean, "private void whenReady(")
    assert "samePath(target, path)" in when_ready, "тело whenReady не зовёт samePath(target, path)"
    assert "target.equals(path)" not in when_ready, "тело whenReady содержит target.equals(path)"

    same_path = method_body(clean, "private static boolean samePath(")
    assert r"--(\\d+)$" in same_path, "тело samePath не содержит регулярку хвоста «--» с цифрами"

    card_line = method_body(clean, "private void cardLine(")
    assert "samePath(want, pathOf(open))" in card_line, "тело cardLine не зовёт samePath(want, pathOf(open))"


def test_found_by_search_is_remembered_by_card_address():
    """Найденное поиском сети — в память телефона: артикул из адреса карточки по правилу наряда (1.43).

    Без записи приложение не считало товар своим и после удаления из корзины оставляло его в корзине сети.
    """
    clean = strip_comments(source())
    own = method_body(clean, "private static String ownSku(")
    assert 'r.rules.optString("sku_url"' in own and "Pattern.compile(rule)" in own
    assert 'line.has("search")' in own, "артикул из адреса — только у строки поиска"
    assert "isHostOf(r.chain, url)" in own, "чужой адрес артикулом этой сети не становится"
    done = method_body(clean, "private void lineDone(final CartRun r, boolean ok, String why, JSONArray calls, boolean taken)")
    assert "ownSku(r, line)" in done and "r.cart.put(own," in done
    assert 'line.put("sku"' not in done, "в строку наряда найденный артикул не пишем: по нему шла бы сверка запроса сети"
    tap = method_body(clean, "private void tapAdd(")
    assert "ownSku(r, line)" in tap, "лежащее в корзине сети — тоже в память"
    lying = tap[tap.index("ownSku(r, line)"):tap.index("r.cart.put(sku, r.have)")]
    same = method_body(clean, "private static boolean sameCard(")
    assert "sameCard(line, " in lying and "nameWhy(line, title)" in same and "packWhy(line, title)" in same, \
        "чужой товар, который человек положил сам, своим не числим: та же сверка названия и фасовки, что перед касанием"
    assert "syncCart(r, taken && r.put > 0 ? own" in done, "снимку корзины сети верим, только если в нём эта позиция"
    # Найденное не «лишнее» и не снимается, пока товар в корзине, — и без строки поиска в наряде (plan.pids).
    extra = method_body(clean, "private JSONArray extraOf(")
    assert "wantOf(r, sku)" in extra and "searchedAgain(r, sku)" in extra, "найденное поиском отчёт «лишним» не зовёт"
    assert "r.noSku.contains(pid)" in method_body(clean, "private boolean searchedAgain(")
    plan = method_body(clean, "private void applyPlan(")
    assert 'plan.optJSONArray("pids")' in plan and "r.noSku.remove(" in plan, "товар со строкой с артикулом — не в noSku"
    assert 'plan.put("pids"' in method_body(clean, "private JSONObject changedPlan("), \
        "убранный товар уходит из списка корзины: найденное для него снимается"
    with open(GRADLE, encoding="utf-8") as fh:
        code = re.search(r"versionCode\s+(\d+)", fh.read())
    assert code and int(code.group(1)) >= 47, "правка идёт с 1.43 (versionCode 47)"


def test_a_background_plan_is_asked_again_when_the_first_request_hangs():
    """Наряды шести сетей уходят разом, часть — по новым соединениям, и одно из них изредка открывается
    3–17 с, пока соседние отвечают за 0,2 с (замер 13.11.2026: Самокат ждал наряд 16,9 с при 0,7–1,1 с у
    остальных; с той же машины без приложения новое соединение трижды открывалось 16,5–16,8 с). Фоновый
    наряд сервер считает без живого поиска, его можно спросить дважды: берём первый удачный ответ."""
    clean = strip_comments(source())
    wait = int(re.search(r"private static final long HEDGE_MS = (\d+);", clean).group(1))
    assert 1500 <= wait <= 2500, "раньше — лишние запросы при обычном наряде (0,5–1,5 с), позже — не уложиться в 5 с"
    ask = method_body(clean, "private void askPlan(")
    # Только фоновая сборка: «Собрать» ведёт на сервере живой поиск, второй такой запрос — не повтор.
    assert re.search(r'if \(r\.slot != null\) callTwice\("/api/device/cartplan", body, got, \(\) -> alive\(r\)\);\s+'
                     r'else call\("/api/device/cartplan", body, got\);', ask)
    # Оба запроса несут один номер вопроса: по нему сервер считает наряд один раз (phonecart.plan).
    assert 'if (r.slot != null) body.put("ask", SystemClock.uptimeMillis());' in ask
    twice = method_body(clean, "private void callTwice(")
    assert twice.count("call(path, body, once);") == 2
    assert "handler.postDelayed(" in twice and "}, HEDGE_MS);" in twice
    # Ответ уходит дальше один раз; отказ одного запроса ждёт второй; оборванной сборке повтор не нужен.
    assert "if (done[0] || (status != 200 && flying[0] > 0)) return;" in twice
    assert "if (done[0] || !wanted.getAsBoolean()) return;" in twice
    # Отчёт несёт, сколько сборка ждала наряд: в журнале правок видно, сервер это или дорога к нему.
    report = clean[clean.index('body.put("lag_ms", lag);'):]
    assert re.match(r'body\.put\("lag_ms", lag\);\s+long planMs = r\.planAt - r\.startedAt;\s+'
                    r'if \(r\.planAt > 0 && planMs >= 0 && planMs < 120000\) body\.put\("plan_ms", planMs\);', report)
    with open(GRADLE, encoding="utf-8") as fh:
        code = re.search(r"versionCode\s+(\d+)", fh.read())
    assert code and int(code.group(1)) >= 48, "правка идёт с 1.44 (versionCode 48)"


def test_an_empty_plan_is_remembered_and_the_report_names_why_requests_failed():
    """Телефон владельца, 13.11.2026: корзину опустошили, и сборка с пустым нарядом (класть нечего, только
    снять своё) не запоминалась — collect каждой страницы запускал её снова: Лента 7 раз и Пятёрочка 4 раза
    за минуту. Своё не снялось — это «не вышло», повтор не раньше RETRY_MS. Причину отказа запросов сети
    отчёт не называл, и с телефона владельца её было не узнать: теперь она идёт пометкой позиции — причину
    (why) сервер разбирает по словам сети («войдите», «выберите магазин»), текст ответа сети туда нельзя."""
    clean = strip_comments(source())
    finish = method_body(clean, "private void finishRun(")
    assert "if (!early && stoppedWhy.isEmpty()) remember(r, r.items.length() > 0 || !hasExcess(r));" in finish
    assert "r.apiWhy = why;" in method_body(clean, "private void apiGaveUp(")
    assert re.search(r'\? "запросом не легла, положу касаниями" : "ищу в сети после знакомых"\)\s+'
                     r'\.put\("note", fitWhy\(r\.apiWhy\)\)', finish)
    # «Собрано» про другую корзину новая сборка снимает: оборвись она, прежний отпечаток не сочтётся собранным.
    start = method_body(clean, "private void startBackground(")
    assert re.search(r'if \(was != null && !was\.optBoolean\("wall"\) && !r\.stamp\.equals\(was\.optString\("stamp"\)\)\) \{\s+'
                     r'memory\(\)\.edit\(\)\.remove\("done\." \+ s\.code\)\.apply\(\);', start)
    with open(GRADLE, encoding="utf-8") as fh:
        code = re.search(r"versionCode\s+(\d+)", fh.read())
    assert code and int(code.group(1)) >= 49, "правка идёт с 1.45 (versionCode 49)"


def test_a_run_overtaken_by_the_next_edit_is_told_so_and_its_reckoned_cart_is_not_the_network():
    """Телефон владельца, 13.11.2026: 11 удалений подряд, отчёт Дикси через 6,2 с после последнего касания —
    каждая сборка тратила запрос на итог корзины, которую уже поправили снова. Идущей записи приложение
    называет её ключ (cartapi.js, newer), адаптер отдаёт корзину расчётом, без чтения. Расчёт — не ответ
    сети: своё по нему не убавляется, итога у него нет, и сборка идёт заново, даже если корзина вернулась к
    её отпечатку."""
    clean = strip_comments(source())
    assert "r.apiKey = key;" in method_body(clean, "private void runCartApiPayload(")
    change = method_body(clean, "private void fastChange(")
    assert re.search(r'else if \(run == null\) soon\(s\);\s+'
                     r'else if \("dixy"\.equals\(s\.code\) && !stamp\.equals\(run\.stamp\) && !run\.apiKey\.isEmpty\(\)\s+'
                     r'&& run\.view != null\) \{\s+'
                     r'run\.view\.evaluateJavascript\("window\.__kzNewer=" \+ JSONObject\.quote\(run\.apiKey\), null\);',
                     change)
    result = method_body(clean, "private void handleCartApiResult(")
    assert 'final boolean reckoned = res.optBoolean("stale", false);' in result
    assert "if (reckoned) q = Math.max(q, r.cart.optInt(s, 0));" in result
    # Расчёт без итога в отчёт не идёт: сборка начинается заново раньше, чем дойдёт до finishRun.
    again = re.search(r"if \(reckoned && r\.slot != null\) \{(.+?)supersede\(r\);\s+return;", result, re.S)
    assert again and "r.slot.pending = r.stamp;" in again.group(1)
    assert result.index("if (reckoned && r.slot != null)") < result.index("finishRun(r, cartObj")
    with open(os.path.join(os.path.dirname(GRADLE), "src", "main", "assets", "cartapi.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert "window.__kzNewer === input.key" in js and "stale: true" in js
    with open(GRADLE, encoding="utf-8") as fh:
        code = re.search(r"versionCode\s+(\d+)", fh.read())
    assert code and int(code.group(1)) >= 50, "правка идёт с 1.46 (versionCode 50)"


def test_a_searched_line_carries_the_pack_and_a_bound_report_drops_the_kept_plan():
    """Замер 13.11.2026, Пятёрочка, корзина из чека: «хлопья №3 350 г» легли карточкой «…№2 500 г» —
    строка поиска не несла фасовку, и packWhy её не сверял. С того же дня сервер связывает найденную
    и положенную карточку с товаром (bound в ответе на отчёт): наряд в памяти телефона ещё ищет товар
    касаниями, поэтому его срок сбрасывается, и fetchPlan берёт новый."""
    clean = strip_comments(source())
    plan = method_body(clean, "private void applyPlan(")
    assert plan.count('.put("pack_g", s.optDouble("pack_g", 0))') == 2, "фасовку несут и search, и search_recent"
    finish = method_body(clean, "private void finishRun(")
    assert re.search(r'if \(status == 200 && new JSONObject\(text\)\.optInt\("bound", 0\) > 0\) \{\s+'
                     r'memory\(\)\.edit\(\)\.remove\("planAt\." \+ r\.code\)\.apply\(\);', finish)
    # Сеть не знает ни одного товара: отчёт сразу, поиск — хвостом после него; своё лишнее снимает cartApi.
    begin = method_body(clean, "private void begin(")
    early = re.search(r'if \(r\.lines\.length\(\) == 0 && r\.later\.length\(\) > 0\) \{(.+?)\n        \}\n', begin, re.S)
    assert early, "пропал разбор сборки без знакомых товаров"
    assert re.search(r'if \(byApi && !\(shouldDrop\(r\) && hasExcess\(r\)\)\) \{\s+'
                     r'finishRun\(r, new JSONObject\(\), ""\);\s+return;', early.group(1))
    assert re.search(r'if \(!byApi\) \{\s+r\.lines = r\.later;', early.group(1)), "сети без запросов идут прежней дорогой"
    assert begin.index("finishRun(r, new JSONObject(), \"\");") < begin.index("cartApi(r);")
    # Карточка другой фасовки, уже лежащая в корзине сети, — не «лежит».
    tap = method_body(clean, "private void tapAdd(")
    lying = tap[tap.index("ownSku(r, line)"):tap.index("r.cart.put(sku, r.have)")]
    assert re.search(r'String different = strangerPack\(line, got\.optString\("title", ""\)\);\s+'
                     r'if \(!different\.isEmpty\(\)\) \{\s+lineDone\(r, false, different, new JSONArray\(\)\);\s+return;', lying)
    # Позиция, которая уже лежит, несёт адрес карточки: иначе положенное до 1.48 не получило бы артикул.
    kept = method_body(clean, "private void keptLine(")
    assert '.put("url", line == null ? "" : line.optString("url", ""))' in kept
    assert 'if (!foundName.isEmpty()) item.put("found_name", foundName);' in kept and "r.items.put(item);" in kept
    stranger = method_body(clean, "private static String strangerPack(")
    assert '!line.optString("sku", "").isEmpty()' in stranger and "packWhy(line, title)" in stranger, \
        "строку с артикулом не судим: её карточку назвал сервер"
    with open(GRADLE, encoding="utf-8") as fh:
        code = re.search(r"versionCode\s+(\d+)", fh.read())
    assert code and int(code.group(1)) >= 52, "правка идёт с 1.48 (versionCode 52)"


def test_a_product_the_phone_found_and_put_before_is_not_searched_again():
    """Стенд, 13.11.2026, корзина из чека: сервер связал один товар Пятёрочки из четырёх найденных, и
    остальные три после каждой правки искались бы касаниями заново — поиск и карточка на каждую лежащую
    позицию. С 1.49 строка такого товара берёт артикул и адрес карточки из памяти телефона и идёт как
    знакомая. Положенное до 1.49 (отметки о сверке фасовки нет) и сборка человека идут прежней дорогой."""
    clean = strip_comments(source())
    plan = method_body(clean, "private void applyPlan(")
    assert re.search(r"JSONObject found = ownFound\(r, s\);\s+if \(found != null\) \{\s+"
                     r"r\.lines\.put\(found\);\s+continue;\s+\}\s+"
                     r"\(r\.slot != null \? r\.later : r\.lines\)\.put\(new JSONObject\(\)", plan), \
        "строка поиска: сначала своё найденное"
    # Фоновой сборке сервер «искал недавно» не присылает (phonecart: background): вторая вставка была бы мёртвой.
    assert plan.count("ownFound(") == 1
    # Строки наряда уже разложены, память телефона уже прочитана: по ним ownFound и судит.
    assert plan.index("ownFound(r, s)") < plan.index("r.want.put(sku, r.want.optInt(sku, 0)")
    ask = method_body(clean, "private void askPlan(")
    assert ask.index("loadLedger(r);") < ask.index("applyPlan(r, plan);")
    fast = method_body(clean, "private void startBackground(")
    assert fast.index("loadLedger(r);") < fast.index("applyPlan(r, fast);")
    own = method_body(clean, "private JSONObject ownFound(")
    assert "if (r.slot == null || pid <= 0) return null;" in own, "«Собрать» человека кладёт всё, как раньше"
    assert re.search(r"if \(r\.cart\.optInt\(sku, 0\) <= 0 \|\| r\.pids\.optInt\(sku, 0\) != pid\) continue;\s+"
                     r"if \(!own\.isEmpty\(\)\) return null;\s+own = sku;", own), "два своих артикула — чей, не понять"
    assert "if (own.isEmpty() || url.isEmpty() || !isHostOf(r.chain, url)) return null;" in own
    assert 'if (known != null && own.equals(known.optString("sku", ""))) return null;' in own, \
        "артикул занят строкой наряда: вторая строка удвоила бы «сколько должно лежать»"
    assert ("if (packChecked(row) && Math.abs(r.packOk.optDouble(own, 0) - pack) > packTolerance(pack)) return null;"
            in own), "фасовку карточки сверяли не с этим весом (или до 1.49, без отметки)"
    line = own[own.index("new JSONObject()"):]
    assert '.put("clicks", 1).put("sku", own)' in line and '"search"' not in line and '"pack_g"' not in line, \
        "строка знакомая: по одной, без адреса поиска и без повторной сверки фасовки"
    assert 'noteFail(r, line, "");' in own and 'noteFail(r, line, "", "apifail.");' in own, \
        "прежние отказы не отправят лежащую в «попробую позже»"
    assert "r.adopted.add(own);" in own
    # Артикул взят из адреса карточки. Лишнее по нему не снимаем (в наряде одна штука, остальное решал человек),
    # запрос сети по нему не сверяем и образцу запроса по нему не учимся: у сети в запросе бывает другой номер.
    again = method_body(clean, "private boolean searchedAgain(")
    assert again.index("if (r.adopted.contains(sku)) return true;") < again.index("if (wantOf(r, sku) > 0) return false;")
    assert "if (sku.isEmpty() || r.adopted.contains(sku)) {" in method_body(clean, "private void verifyLine(")
    assert re.search(r"!r\.learned && !r\.learnTried && !sku\.isEmpty\(\)\s+&& !r\.adopted\.contains\(sku\)\) \{", clean)
    # Отметка «с каким весом сверяли фасовку» ставится там, где карточка стала своей, и живёт в памяти телефона.
    assert ('if (packChecked(line)) r.packOk.put(sku, line.optDouble("pack_g", 0));'
            in method_body(clean, "private static void ownLine("))
    assert ('if (it.optDouble("k", 0) > 0) r.packOk.put(sku, it.optDouble("k", 0));'
            in method_body(clean, "private void loadLedger("))
    assert ('if (r.packOk.optDouble(sku, 0) > 0) it.put("k", r.packOk.optDouble(sku, 0));'
            in method_body(clean, "private void saveLedger("))
    # Обе дороги к ownLine идут после сверки фасовки: лежащая карточка и касание «В корзину».
    tap = method_body(clean, "private void tapAdd(")
    assert tap.index("strangerPack(line, got.optString(\"title\", \"\"))") < tap.index("ownLine(r, line, sku);")
    with open(GRADLE, encoding="utf-8") as fh:
        code = re.search(r"versionCode\s+(\d+)", fh.read())
    assert code and int(code.group(1)) >= 53, "правка идёт с 1.49 (versionCode 53)"


def test_a_refused_pyaterochka_cart_node_is_healed_by_erasing_the_stale_protection_cookie():
    """Телефон владельца, 13.11.2026: узел корзин Пятёрочки отвечал «обрыв сети», «главная + 4 с» не лечила,
    сеть три минуты шла касаниями (правки «0 из 7» за 0,3 с, хвост 90 с). Стенд: без куки spsc и с мусором в ней
    узел отвечает за 60–350 мс статусом 0; главная выдаёт заново стёртую (6,9 с) и не заменяет протухшую.
    Теперь «обрыв» лечится стиранием куки в хранилище WebView; 401 и Перекрёсток идут прежним входом."""
    text = source()
    clean = strip_comments(text)
    result = method_body(clean, "private void handleCartApiResult(")
    # Лечение только на «обрыв» Пятёрочки; прежний вход (главная и 4 с) остаётся для 401 и для Перекрёстка.
    assert "healAllowed(r.slot)" in result, "лечат не чаще HEAL_GAP_MS: лечили недавно — стена и касания"
    # Кукой лечится только быстрый отказ защиты при живой связи (fast из cartapi.js), а не любой «обрыв».
    assert 'boolean pyatHeal = pyatNet && res.optBoolean("fast", false) && r.slot != null && healAllowed(r.slot);' in result
    assert re.search(r'if \(pyatHeal\) \{\s+healPyat\(r, origin, again\);\s+return;\s+\}'
                     r'\s+go\(r, origin \+ "/"\);\s+whenReady\(r, "/", null, 4000, again\);', result)
    # Правка, пришедшая за время ожидания, старый наряд не перебивает: новая сборка начнёт с нуля.
    assert re.search(r'if \(!alive\(r\)\) return;\s+if \(superseded\(r\)\) \{\s+supersede\(r\);\s+return;\s+\}'
                     r'\s+beat\(r, true\);[^\n]*\s+runCartApiPayload\(', result)
    assert 'apiGaveUp(r, why + r.healNote + peekNote(r.slot));' in result, "журнал должен сказать, что вышло с кукой"
    # Прочие «обрывы» (таймаут, обрыв посреди записи, телефон без сети) идут прежним входом 1.49: главная, 4 с, повтор;
    # быстрый отказ, которого уже нельзя лечить, сразу уходит в стену и касания.
    assert re.search(r'boolean pyatRefresh = "pyaterochka"\.equals\(r\.code\)\s+&& \("auth"\.equals\(stage\) \|\| pyatHeal'
                     r' \|\| \(pyatNet && !res\.optBoolean\("fast", false\)\)\);', result)
    heal = method_body(clean, "private void healPyat(")
    assert heal.index("dropCookie(origin, PYAT_COOKIE)") < heal.index("go(r, origin + \"/\")") \
        < heal.index("whenHealed(r.slot, origin")
    assert "if (!gone)" in heal and "whenReady(r, \"/\", null, 4000, then)" in heal, \
        "кука не стёрлась — лечить нечем, идём прежней дорогой"
    assert "r.healNote = " in heal
    assert heal.count("Log.i(TAG, r.name + ") == 2, "журнал приложения говорит, что лечили и что вышло"
    # Выдав куку, защита перезагружает страницу: повтор запроса идёт с robots.txt, а не с главной (стенд: 52 с).
    # Повтор — не раньше HEAL_QUIET_MS после отказа: узел до тех пор отказывает и настоящей куке (стенд, penalty_test).
    assert re.search(r'go\(r, origin \+ "/robots\.txt"\);.*?whenReady\(r, null, null, \(int\) wait, then\);', heal, re.S)
    assert re.search(r"hasCookie\(origin, PYAT_COOKIE\)\s+\? Math\.max\(0, t0 \+ HEAL_QUIET_MS - SystemClock\.uptimeMillis\(\)\) : 0;",
                     heal), "кука не выдана — паузу ждать незачем: повтор получит отказ, и сеть пойдёт касаниями"
    # Пауза узла держится окном и когда сборку закрыли посреди лечения (новая правка); закрытый защитой адрес повтора
    # запроса не получает: сразу отказ (apiGaveUp, касаний при блоке нет с 1.61).
    assert heal.index("holdQuiet(r.slot, origin, t0, miss);") < heal.index("if (!alive(r)) {")
    assert re.search(r'if \(miss\.startsWith\(HEAL_BLOCKED\)\) \{\s+apiGaveUp\(r, "узел корзин закрыт защитой сети" \+ r\.healNote\);'
                     r'\s+uploadTrace\(r\.code, r\.name \+ ": адрес закрыт защитой" \+ r\.healNote\);\s+return;\s+\}\s+long wait = ',
                     heal), "отчёта при блоке нет (endBlocked), хроника лечения уходит на сервер сама"
    # Узел молчит и перед новой кукой: стена на другие сборки и чтение заранее, лечить снова — не раньше HEAL_GAP_MS,
    # пульс — чтобы тишина до повтора не сошла за зависание (PHONE_STALE).
    assert '"apiwall." + r.code, System.currentTimeMillis() - API_WALL_MS + HEAL_QUIET_MS' in heal
    assert heal.index('"apiwall." + r.code') < heal.index("go(r, origin + \"/\")")
    assert "r.slot.healAt = t0;" in heal and heal.count("beat(r, true);") == 2
    assert "if (r.slot.run == null) rest(r.slot);" in heal, "сборку закрыли посреди лечения — окно на отдых"
    # Кука стирается и без домена, и с доменом на хосте узла и на каждом его суффиксе в две и больше метки; видна и
    # HttpOnly (CookieManager, а не document.cookie). Настоящая spsc живёт на .5ka.ru, а не на узле 5d.5ka.ru (CDP,
    # 13.11.2026): ключ куки — имя, домен и путь, и стирание «с доменом хоста» её не берёт (телефон владельца, 14.11).
    drop = method_body(clean, "private static void dropCookie(")
    assert 'name + "=; Max-Age=0; Path=/")' in drop and "flush()" in drop
    assert 'Path=/; Domain=" + d + "; Secure")' in drop and "Domain=\" + host" not in drop
    assert "d = Uri.parse(origin).getHost(); d != null && d.indexOf('.') > 0; d = d.substring(d.indexOf('.') + 1)" in drop, \
        "5d.5ka.ru → 5ka.ru, а «ru» (публичный суффикс) не трогаем"
    assert "CookieManager.getInstance().getCookie(origin" in method_body(clean, "private static boolean hasCookie(")
    # Новой куки мало: узел принимает её, когда главная страница дошла до конца и простояла на месте
    # (стенд, 13.11.2026: уход на robots.txt в миг выдачи куки оставлял узел отказывающим).
    wait = method_body(clean, "private void whenHealed(")
    assert "hasCookie(origin, PYAT_COOKIE)" in wait and "evaluateJavascript(HEALED_JS" in wait
    assert "boolean ready = cookie && shown;" in wait and "SystemClock.uptimeMillis() >= until" in wait
    assert "s.healUntil = until + 1000;" in wait and wait.count("s.healUntil = 0;") == 1, "окно освобождается при любом конце"
    # Конец один для ответа страницы и сторожа: ответ скрипта мог потеряться, когда защита перезагрузила страницу.
    assert "if (over[0]) return;" in wait and "over[0] = true;" in wait and "HEAL_WAIT_MS + 900" in wait
    assert "s.view != v" in wait and "v.evaluateJavascript(HEALED_JS" in wait, "лечим окно, где начали"
    # Опрос идёт по таймеру, а не из ответа скрипта: потерянный при перезагрузке страницы ответ его не обрывает.
    assert wait.index("handler.postDelayed(this, 500);") < wait.index("v.evaluateJavascript(HEALED_JS")
    assert "handler.postDelayed(again" not in wait and "last[0] = page;" in wait and '"страница: " + last[0]' in wait
    assert "s.healAt = t0;" in wait
    assert 'PYAT_COOKIE = "spsc"' in text and re.search(r"HEAL_WAIT_MS = \d+;", text)
    assert "HEAL_STABLE_MS = 500;" in text and "HEAL_LATE_MS = 8000;" in text
    page = re.search(r"HEALED_JS =(.*?);\n", clean, re.S).group(1)
    for part in ("document.readyState!=='complete'", "window.__kzStale", "location.pathname==='/robots.txt'",
                 "#__next,main,h1,header", "window.__kzHealSeen", "HEAL_STABLE_MS", "HEAL_LATE_MS"):
        assert part in page, part
    # Уход с главной, пока её запрос защиты gatekeeper/evaluate в полёте, оставлял узел отказывающим (стенд, 13.11.2026):
    # конец запроса виден странице в Resource Timing.
    assert "performance.getEntriesByType('resource')" in page and "/gatekeeper/" in page and "e.responseEnd>0" in page
    # Страница «Проблемы со связью» (защита закрыла адрес; стенд, 14.11.2026) не сходит за готовую главную: у неё есть h1,
    # и через HEAL_LATE_MS она прошла бы проверку. Свой экран ошибки сайта корень #__next имеет и блоком не считается.
    # К узлу корзин при блоке не ходим HEAL_BLOCK_MS: метка в часах телефона, её читает wallUp.
    assert "!document.getElementById('__next')" in page and "t.length<1500" in page and "HEAL_BLOCK_PAGE" in page
    assert r"/Проблемы\\s+со\\s+связью/.test(t)" in page, "пробелы в тексте страницы бывают неразрывными"
    # 15.11.2026 защита закрыла адрес эмулятора страницей другого вида: путь /xpvnsulc/, без h1, текст «дата Ваш IP … ID
    # запроса …». 1.57 её не узнала, лечила и стучалась дальше; 1.58 узнаёт по пути и «Ваш IP», длину текста не проверяет.
    # Путь без текста сразу блоком не считается: у Самоката /xpvnsulc/ — и проходящая проверка (20.10.2026).
    assert "location.pathname.indexOf('/xpvnsulc/')===0" in page and r"/Ваш\\s+IP/.test(t)" in page
    # Вечером 15.11 та же страница простояла всё лечение «loading без каркаса», без текста, и 1.60 пустила сеть касаниями:
    # застрявшую до конца ожидания проверку блоком называет whenHealed (1.61) — и по опросу, и по сторожу.
    assert re.search(r'final ValueCallback<String> late = miss -> \{\s+if \(!over\[0\]\) end\.onReceiveValue\('
                     r'last\[0\]\.startsWith\("/xpvnsulc/"\)\s+\? healBlock\(s, "проверка не отпустила за " \+ HEAL_WAIT_MS / 1000 \+ " с"\)'
                     r' : miss\);\s+\};', wait), "сторож после конца лечения метку блока не пишет"
    assert re.search(r"handler\.postDelayed\(\(\) -> late\.onReceiveValue\(", wait), "сторож тоже"
    assert re.search(r"if \(ready \|\| SystemClock\.uptimeMillis\(\) >= until\) \{\s+late\.onReceiveValue\(", wait), "конец опроса"
    assert "end.onReceiveValue(\"окно закрыто\")" in wait, "закрытое окно — не повод для блока"
    assert page.index("/xpvnsulc/") < page.index("#__next,main,h1,header")
    assert page.index("HEAL_BLOCK_PAGE") < page.index("#__next,main,h1,header")
    assert 'HEAL_BLOCK_PAGE = "#block"' in text and 'HEAL_BLOCKED = "блок защиты"' in text
    assert re.search(r"HEAL_BLOCK_MS = 30L \* 60 \* 1000;", text)
    assert wait.index("if (HEAL_BLOCK_PAGE.equals(page)) {") < wait.index("boolean cookie = hasCookie(")
    block = wait[wait.index("if (HEAL_BLOCK_PAGE.equals(page)) {"):wait.index("boolean cookie = hasCookie(")]
    assert 'end.onReceiveValue(healBlock(s, "«Проблемы со связью»"));' in block
    mark = method_body(clean, "private String healBlock(")
    assert '"apiblock." + s.code, System.currentTimeMillis() + HEAL_BLOCK_MS' in mark and "return HEAL_BLOCKED + " in mark
    assert "healAt" not in mark, "срок блока — в часах телефона, а не в аптайме окна: переживёт перезапуск и сон"
    blocked = method_body(clean, "private boolean blocked(")
    assert 'getLong("apiblock." + code, 0)' in blocked and "until - now <= HEAL_BLOCK_MS" in blocked
    assert 'remove("apiblock." + code)' in blocked and "until - now > HEAL_BLOCK_MS" in blocked, \
        "метка из «будущего» блок не держит и не оживает позже"
    assert "blocked(s.code)" in method_body(clean, "private long warmDue("), "закрытому адресу главную не подогреваем"
    # Пока окно лечит куку на главной странице, его никто не уводит: сборка ждёт, возврат на robots.txt тоже.
    begin = method_body(clean, "private void begin(")
    assert re.search(r"if \(healing\(r\.slot\)\) \{\s+handler\.postDelayed\(\(\) -> \{\s+if \(alive\(r\)\) begin\(r\);"
                     r"\s+\}, 250\);\s+return;\s+\}\s+cartApi\(r\);", begin)
    assert "!healing(s)" in method_body(clean, "private void supersede(")
    assert "!healing(s)" in method_body(clean, "private void rest(")
    assert "if (healing(s)) return;" in method_body(clean, "private void warm(")
    # Чтение заранее, не вышедшее «обрывом», лечит куку само — до первой правки, не чаще HEAL_GAP_MS, только у Пятёрочки.
    peek = method_body(clean, "private void peekAt(")
    assert 'if ("pyaterochka".equals(s.code)) handler.postDelayed(() -> peekCheck(s, 0), 1500);' in peek
    check = method_body(clean, "private void peekCheck(")
    assert "a.ok===false&&a.stage==='net'" in check
    assert "s.run != null || s.soon != null || away" in check, "сборка или правка идут — окну не мешаем"
    # Лечить уже нельзя — узел всё равно отказывает: стена на сборки и чтение заранее, правка сразу идёт касаниями.
    assert re.search(r'if \(!healAllowed\(s\)\) \{\s+notePeek\(s, "лечили недавно, не лечу"\);\s+memory\(\)\.edit\(\)\.putLong'
                     r'\("apiwall\." \+ s\.code, System\.currentTimeMillis\(\)\)\.apply\(\);\s+return;\s+\}', check)     # метку лечения ставит whenHealed
    # Чем кончилось лечение при чтении заранее — в журнал отказов (1.52): правки телефона до этого окно не видно.
    assert check.count("notePeek(s, ") == 3 and 'notePeek(s, PYAT_COOKIE + " не стёрлась");' in check
    note = method_body(clean, "private static String peekNote(")
    assert "s == null || s.peekNote.isEmpty()" in note and "s.peekNoteAt" in note, "давняя запись несёт возраст"
    assert "s.peekNote = note;" in method_body(clean, "private static void notePeek(")
    assert "a.fast===true" in check, "лечим быстрый отказ защиты при живой связи, а не обрыв связи"
    # Результата ещё нет — чтение идёт: смотрим снова (холодное соединение, темп), не дольше 8,5 с от начала:
    # быстрый отказ — до 8 с (cartapi.js, netFail).
    assert "tries < 14" in check and "peekCheck(s, tries + 1)" in check
    # Стереть куку не вышло (другой путь или домен): новой главная не выдаст, а whenHealed принял бы старую за выданную —
    # окно держало бы паузу зря. Как при !healAllowed: стена на 3 минуты, правка сразу касаниями, ни главной, ни паузы.
    assert re.search(r'dropCookie\(origin, PYAT_COOKIE\);\s+if \(hasCookie\(origin, PYAT_COOKIE\)\) \{[^}]*?'
                     r'putLong\("apiwall\." \+ s\.code, System\.currentTimeMillis\(\)\)\.apply\(\);\s+return;\s+\}', check)
    # Узел молчит до конца паузы: стена на сборки и чтение заранее ставится после стирания куки и до загрузки главной.
    assert check.index("dropCookie(origin, PYAT_COOKIE)") \
        < check.index('"apiwall." + s.code, System.currentTimeMillis() - API_WALL_MS + HEAL_QUIET_MS') \
        < check.index('s.view.loadUrl(origin + "/")')
    assert re.search(r"HEAL_QUIET_MS = 65000;", text) and "private static boolean healAllowed(" in clean
    assert check.index("dropCookie(origin, PYAT_COOKIE)") < check.index("s.view.loadUrl(origin + \"/\")") \
        < check.index("whenHealed(s, origin")
    assert 's.view.loadUrl(origin + "/robots.txt")' in check and "s.peekStartedAt = 0;" in check and "peekSoon(1500);" in check
    # Окно возвращается на robots.txt и тогда, когда правка уже ждёт в begin (на главной её запросы пропали бы вместе
    # со страницей); свёрнутое приложение страниц не грузит. Пауза узла держится окном (healing): правка ждёт её, а не
    # идёт касаниями по стене; адрес закрыт — ждать нечего.
    assert 'if (!away && s.view != null) s.view.loadUrl(origin + "/robots.txt");' in check
    assert "holdQuiet(s, origin, t0, miss);" in check
    hold = method_body(clean, "private static void holdQuiet(")
    assert "hasCookie(origin, PYAT_COOKIE) && !miss.startsWith(HEAL_BLOCKED)" in hold
    assert "s.healUntil = Math.max(s.healUntil, t0 + HEAL_QUIET_MS + 1000);" in hold
    # Главная только что открыта: подогрев не нужен, и rest/warm не остаются без таймеров, пока окно держит пауза.
    assert re.search(r'if \(hasCookie\(origin, PYAT_COOKIE\)\) \{\s+memory\(\)\.edit\(\)\.putLong\("warm\." \+ s\.code, '
                     r'System\.currentTimeMillis\(\)\)\.apply\(\);\s+\}', check)
    assert 15 == int(re.search(r"HEAL_GAP_MS = (\d+)L \* 60 \* 1000", text).group(1)), \
        "лечение чаще раза в 15 минут закрыло адрес стенда (шесть за час, 14.11.2026)"
    # «Обрыв» первых запросов Пятёрочки несёт время запроса: быстрый отказ защиты или таймаут.
    with open(os.path.join(ROOT, "android", "app", "app", "src", "main", "assets", "cartapi.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert js.count("function netFail(res, lastCart)") == 1 and "return netFail(actRes);" in js and "return netFail(initRes);" in js
    # Все семь «обрывов» Пятёрочки строит netFail: отказ первого запроса сборки — защита, позже — связь.
    for res in ("dRes", "pRes", "putRes", "rDel", "rPut"):
        assert f"if ({res}.status === 0) return netFail({res}, lastCart);" in js, res
    assert "'Пятёрочка: обрыв сети'" not in js
    assert "out.fast = stats.requests === 1 && res.ms < 8000 && (typeof navigator === 'undefined' || navigator.onLine !== false);" in js
    # Без разрешения на состояние сети navigator.onLine может не отличить телефон без связи от отказа защиты.
    with open(os.path.join(ROOT, "android", "app", "app", "src", "main", "AndroidManifest.xml"), encoding="utf-8") as fh:
        assert "android.permission.ACCESS_NETWORK_STATE" in fh.read()
    with open(GRADLE, encoding="utf-8") as fh:
        code = re.search(r"versionCode\s+(\d+)", fh.read())
    assert code and int(code.group(1)) >= 57, ("лечение куки — с 1.50 (54), раз в 15 минут — с 1.51 (55), стирание на .5ka.ru — "
                                               "с 1.52 (56), стена прежней версии стирается при обновлении — с 1.53 (57)")
    # Хвост поиска не уводит окно с главной, пока оно лечит куку: тогда защита недосказала бы, и лечение пропало.
    tail = method_body(clean, "private void tailLater(")
    assert re.search(r"if \(healing\(s\)\) \{\s+handler\.postDelayed\(this, 250\);\s+return;\s+\}", tail)


def test_pyaterochka_heal_timings_and_wall_readers_agree():
    """Лечение куки Пятёрочки держится на сроках: оно кончается раньше конца паузы, пауза короче стены, повторное
    лечение — после паузы. Стену apiwall читает одно место (wallUp) — по API_WALL_MS, без меток из «будущего»;
    зовут его чтение заранее и сборка."""
    text = source()
    wait = int(re.search(r"HEAL_WAIT_MS = (\d+);", text).group(1))
    quiet = int(re.search(r"HEAL_QUIET_MS = (\d+);", text).group(1))
    wall = int(re.search(r"API_WALL_MS = (\d+)L \* 60 \* 1000", text).group(1)) * 60 * 1000
    gap = int(re.search(r"HEAL_GAP_MS = (\d+)L \* 60 \* 1000", text).group(1)) * 60 * 1000
    assert wait + 1000 < quiet < wall and quiet < gap, (wait, quiet, wall, gap)
    clean = strip_comments(text)
    readers = re.findall(r'getLong\("apiwall\."[^;]*', clean)
    up = method_body(clean, "private boolean wallUp(")
    assert len(readers) == 1 and readers[0] in up and "d < API_WALL_MS" in up and "blocked(code)" in up, readers
    assert "if (d < 0)" in up and 'remove("apiwall." + code)' in up, "метка из «будущего» стену не держит и не оживает"
    assert "wallUp(s.code)" in method_body(clean, "private void peekAt(")
    assert "wallUp(r.code)" in method_body(clean, "private void cartApi(")
