"""Наполнение корзины магазина под сохранённым входом человека.

ЧТО ЭТО ДЕЛАЕТ. Берёт наряд (app/cartplan.py: артикул, сколько, адрес карточки),
поднимает браузер с сохранённым входом человека (app/shopbrowser/store.py) и
нажимает «в корзину» на карточках — ровно так, как нажимал бы он сам. В конце
отчитывается обеими сторонами: что легло и что нет, с причиной.

ПОЧЕМУ ПО КАРТОЧКАМ, А НЕ ОДНИМ ЗАПРОСОМ. У корзины сетей нет открытого способа
положить в неё товар со стороны: у Магнита весь checkout закрыт ключом, у
остальных публичного способа нет вовсе. Нажатие на карточке — то же самое, что
делает покупатель, и оно не требует ни разбора чужого протокола, ни обхода чего
бы то ни было.

ПОЧЕМУ У КАЖДОЙ ПОЗИЦИИ СВОЙ АДРЕС. Карточки на одной странице не лежат: наряд из
шестнадцати позиций — это шестнадцать разных страниц. Адрес берётся из
ПОДТВЕРЖДЁННОГО сопоставления (app/cartplan.build), и позиция без адреса
пропускается со словами, а не угадывается: положить человеку не тот творог хуже,
чем не положить ничего, — второе он увидит, первое нет.

ТЕМП — ЧЕЛОВЕЧЕСКИЙ, и это не украшение. Пауза между карточками та же, по которой
живут серверные сборщики каталога: чужая витрина не наша, и выгребать её в сто
потоков мы не будем. Заодно это единственное, что делает наряд из двадцати
позиций отличимым от наплыва.

ОТМЕТКА О ХОДЕ — ЧАСТЬ РАБОТЫ, А НЕ УКРАШЕНИЕ. Передача идёт минуту и больше.
Пока экран стоит неподвижно, человек уверен, что нажатие не сработало, и нажимает
ещё раз — то есть кладёт себе всё в корзину дважды. Поэтому здесь пишется не
только «идёт», но и КАЖДАЯ позиция поимённо: что легло, что нет и почему. Из этой
же записи растут две вещи, которых иначе не сделать, — живая строка «кладу 7 из
16: творог» и повтор ТОЛЬКО непроложенного.

ПОВТОР ПОВТОРЯЕТ НЕ ВСЁ. Прогнать наряд заново целиком — значит положить удавшееся
второй раз, то есть своими руками испортить человеку корзину. Поэтому у пускателя
есть `only`: список артикулов, которые надо доложить. Всё, что уже лежит, из этого
захода исключается — но НЕ из отчёта: оно переносится туда отметкой «лежит с
прошлого раза», иначе экран показал бы «легло 2» там, где в корзине четырнадцать
позиций, и человек пошёл бы собирать всё заново руками.

ПОЧЕМУ ОТБОР ДЕЛАЕТ ПУСКАТЕЛЬ, А НЕ САМА ПЕРЕДАЧА. deliver продолжает ту запись о
ходе, которую пускатель для него приготовил: очищенную — для полного наряда, с
перенесённым — для повтора. Так решение «что повторяем» принимается в одном месте,
а не разъезжается по двум.

ВТОРАЯ ПЕРЕДАЧА, ПОКА ИДЁТ ПЕРВАЯ, НЕ ПУСКАЕТСЯ. Проверка стоит здесь, в пускателе,
а не только на экране: экран человек мог открыть до нажатия, а кнопку нажать в двух
вкладках. Отметка «идёт» ставится в потоке ЗАПРОСА, до создания фонового потока, —
иначе между «пустили» и «поток дошёл до записи» остаётся щель в доли секунды, и
второе нажатие успевает в неё пролезть.

ЧЕГО ЗДЕСЬ НЕТ. Оформления, оплаты, кнопки «заказать». Наряд кончается
наполненной корзиной — дальше решает человек. Это его деньги, и любая наша ошибка
в расчёте должна остаться видимой ошибкой, а не списанием.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import urlsplit

from app import purchase
from app.shopbrowser import driver, point, signals
from app.shopbrowser import store as shopstore

log = logging.getLogger(__name__)

STEP_PAUSE = 1.3          # между карточками: темп покупателя, а не выгребания
CLICK_PAUSE = 0.7         # между нажатиями «ещё одну» на той же карточке

# Блок покупки товара — не дальше этого ниже заголовка. Замер 19.09.2026: у
# Магнита кнопка товара на 167 точек ниже h1, у METRO на 192, а карусель «с этим
# покупают» начинается на 803. Всё, что ниже потолка, — чужие товары: их кнопки
# «в корзину» и их «нет в наличии» к этой карточке отношения не имеют.
BUYBOX_BELOW = 700

# Сколько держать повторную ПОЛНУЮ передачу после удачной, часов. Сеть кладёт
# товар поверх лежащего, и второе нажатие «Передать» за вечер удваивало корзину.
# Повтор непроложенного (only) этим сроком не ограничен — он и сделан, чтобы
# докладывать.
RESEND_HOURS = 12
QTY_LIMIT = purchase.QTY_LIMIT   # больше тридцати штук одного товара — ошибка, а не корзина
LINE_LIMIT = 60           # длиннее наряда у корзины не бывает
LOAD_TIMEOUT = 45000

# Дольше этого одну карточку не ждём, даже если человек просит тридцать штук.
# Потолок упирается не в терпение, а в STALE_AFTER ниже: пульс отметки о ходе
# пишется МЕЖДУ позициями, и карточка, молчащая дольше пяти минут, объявила бы
# всю передачу мёртвой — то есть открыла бы дорогу второй поверх идущей.
CARD_LIMIT = 240

# Сколько тишины делает отметку о ходе мёртвой. Наряд из шестидесяти позиций идёт
# минуты три; пять минут без движения означают не «идёт медленно», а «сервер
# перезапустили посреди передачи». Без этого срока незакрытая отметка запрещала бы
# передачу НАВСЕГДА: человек остался бы с полупустой корзиной и кнопкой, которая
# больше никогда не нажимается.
STALE_AFTER = 300


# Правило количества живёт в app/purchase.py: им же считает цену расчёт и
# количество ссылки на корзину. Здесь — прежние имена, ими пользуются экраны и тесты.
_qty_text = purchase.qty_text
_measure = purchase.measure
_packs = purchase.packs_word
_pieces = purchase.pieces


def _field(line, name: str, default=None):
    """Поле строки наряда: строкой бывает и PlanLine, и словарь позиции."""
    if isinstance(line, dict):
        return line.get(name, default)
    return getattr(line, name, default)


def pieces_of(line) -> tuple[int, str]:
    """Сколько нажатий у строки наряда и что об этом сказать — по всем её полям."""
    return _pieces(_field(line, "qty", 1), _field(line, "unit"),
                   _field(line, "pack_g"), _field(line, "per"))


def rounding(qty, unit: str | None = None, pack_g: float | None = None,
             per: str | None = None) -> str:
    """Что случится с дробным количеством на витрине. Нечему случиться — пусто.

    Открыта наружу для экранов: округление надо показывать ДО нажатия, а не только
    в отчёте. Человек, увидевший «0,7 кг → 1 упаковка» заранее, поправит количество
    сам; узнавший об этом из чека — уже нет.
    """
    return _pieces(qty, unit, pack_g, per)[1]


def rounding_of(line) -> str:
    """То же, что rounding, но по строке наряда целиком — с фасовкой и единицей корзины."""
    return pieces_of(line)[1]


def _step_pause() -> float:
    """Пауза между карточками: не чаще connectors.rate_limit_rps и не быстрее покупателя."""
    if STEP_PAUSE <= 0:                    # тесты и отладка выключают паузу целиком
        return 0.0
    try:
        from app import config

        rps = float(config.get("connectors.rate_limit_rps", 1.0) or 1.0)
    except (TypeError, ValueError):
        rps = 1.0
    return max(STEP_PAUSE, 1.0 / rps if rps > 0 else 0.0)


def _budget(qty, unit=None, pack_g=None, per=None) -> float:
    """Сколько ждать поручения по одной карточке — считая от количества.

    ПОЧЕМУ НЕ ОБЩИЙ СРОК driver.CALL_TIMEOUT. Он равен 90 секундам, а бюджет одной
    карточки больше уже при двух штуках: открыть страницу до 45 с, дать ей улечься
    около 28 с и на КАЖДОЕ нажатие ещё до 14 с. При qty=1 это 89 против 90 — то
    есть впритык, при qty=3 — сто с лишним.

    ЧЕМ ПЛАТИТ ЧЕЛОВЕК ЗА КОРОТКИЙ СРОК. Поручение по таймауту не отменяется: оно
    остаётся в очереди потока браузера и дощёлкивает товар в корзину. А передача в
    это время объявляет эту позицию непроложенной и предлагает её повторить — и
    человек кладёт себе второй раз то, что уже лежит.
    """
    times, _ = _pieces(qty, unit, pack_g, per)
    return min(CARD_LIMIT, LOAD_TIMEOUT / 1000 + 30 + times * (5 + 8 + CLICK_PAUSE))


# Метка, которой страница помечает найденную кнопку, чтобы мы взяли её одним
# запросом. Имя нарочно наше и длинное: столкнуться с чужим атрибутом тут значило
# бы нажать не туда.
PICKED = "data-korzina-add"

# КАК НАЙТИ КНОПКУ ИМЕННО ЭТОГО ТОВАРА. ЗАМЕР 19.09.2026, И ОН ОБЪЯСНЯЕТ БАГ.
#
# Прежний разбор брал ПЕРВУЮ подходящую кнопку в порядке разметки. Вот что на
# карточке Магнита (одна страница, 4017 точек высоты, 28 подходящих кнопок):
#
#     y=3      «В корзину»            ← плавающая панель, её и жали
#     y=356    заголовок товара (h1)
#     y=523    «Добавить в корзину»   ← ВОТ ОНА, кнопка товара
#     y=1159   «В корзину» ×20        ← карусель «с этим покупают»
#
# То есть жали элемент шапки, он перерисовывался, и передача честно сообщала
# «карточка перерисовалась под пальцем» — при живой, рабочей кнопке в 500 точках
# ниже. На METRO та же картина: h1 на 265, кнопка товара на 457, а первой в
# разметке идёт невидимая на y=0.
#
# Правило, которое обе сети подтверждают: кнопка товара — ПЕРВАЯ ВИДИМАЯ НИЖЕ
# ЗАГОЛОВКА. Карусели живут ниже, панели и меню — выше. Заголовка нет (редко, но
# бывает) — берём самую верхнюю видимую: это всё равно ближе к товару, чем
# двадцатая по счёту из карусели.
#
# И НЕ ДАЛЬШЕ БЛОКА ПОКУПКИ (BUYBOX_BELOW). Когда самого товара нет в наличии,
# его кнопки нет вовсе, и «первой видимой ниже заголовка» оказывалась кнопка
# карусели — то есть в корзину ложился ЧУЖОЙ товар. Кнопка ниже потолка не
# берётся никогда: лучше «кнопки не нашлось», чем не тот творог.
#
# `selectors` — говорящие признаки сети (signals.ADD_TO_CART). По ним кандидаты
# отбираются вместо имени, но правило места то же: у Дикси кнопка
# `card-line__cartBnt` есть и у каждой плитки карусели, и первая в разметке
# бывает не той.
_PICK_JS = """([pattern, mark, below, window_px, selectors]) => {
    const want = pattern ? new RegExp(pattern, 'i') : null;
    for (const e of document.querySelectorAll('[' + mark + ']')) e.removeAttribute(mark);
    const h1 = document.querySelector('h1');
    const top = h1 ? h1.getBoundingClientRect().top + window.scrollY : null;
    let nodes = [];
    if (selectors && selectors.length) {
        for (const sel of selectors) {
            try { nodes = nodes.concat(Array.from(document.querySelectorAll(sel))); }
            catch (e) { /* селектор сети устарел — просто не участвует */ }
        }
    } else {
        nodes = Array.from(document.querySelectorAll('button,[role=button]'));
    }
    let best = null, bestY = Infinity;
    for (const e of nodes) {
        if (e.disabled) continue;
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (want) {
            const name = ((e.getAttribute('aria-label') || '') + ' ' +
                          (e.innerText || '')).replace(/\\s+/g, ' ').trim();
            if (!want.test(name)) continue;
        }
        const y = r.top + window.scrollY;
        if (below && top !== null && y < top) continue;
        if (top !== null && window_px && y > top + window_px) continue;
        if (y < bestY) { best = e; bestY = y; }
    }
    if (!best) return null;
    best.setAttribute(mark, '1');
    return Math.round(bestY);
}"""


def _add_button(page, chain: str, again: bool):
    """Кнопка «в корзину» ИМЕННО ЭТОГО товара на открытой карточке.

    Сначала говорящий признак сети, если он у неё есть (у Дикси это data-testid
    из настоящего слепка), потом — доступное имя. По классам не ищем: у всех сетей
    они хэшированные и меняются с каждой сборкой.

    Выбор делает сама страница (_PICK_JS выше): ей видно, где заголовок и где
    что нарисовано, а нам — нет. Перебирать узлы отсюда значило бы спросить у
    браузера четыреста раз «ты видимый?» по одному, и всё равно не узнать, какой
    из них принадлежит товару. Правило места одно для признаков сети и для
    имени: ниже заголовка и не дальше блока покупки.
    """
    selectors = [] if again else list(signals.ADD_TO_CART.get(chain) or ())
    if selectors:
        try:
            if page.evaluate(_PICK_JS, ["", PICKED, True, BUYBOX_BELOW, selectors]) is not None:
                node = page.query_selector(f"[{PICKED}]")
                if node is not None:
                    return node
        except Exception:  # noqa: BLE001 — выбор страницей запрещён: ниже прежний путь
            log.info("%s: выбор кнопки по признаку сети не удался", chain, exc_info=True)
            for selector in selectors:
                try:
                    node = page.query_selector(selector)
                except Exception:  # noqa: BLE001 — селектор мог стать невалидным
                    node = None
                if node and node.is_enabled() and node.is_visible():
                    return node

    pattern = signals.PLUS_NAME if again else signals.ADD_NAME
    # «Ещё одну» ищем БЕЗ привязки к заголовку снизу: после первого нажатия витрина
    # часто подменяет кнопку счётчиком «− 1 +», и плюс оказывается там же, где
    # была кнопка, — но бывает и выше, в прилипшей панели покупки. Сверху потолок
    # тот же: плюс карусели — это чужой товар.
    try:
        if page.evaluate(_PICK_JS, [pattern, PICKED, not again, BUYBOX_BELOW, []]) is None:
            return None
        return page.query_selector(f"[{PICKED}]")
    except Exception:  # noqa: BLE001 — страница могла уехать прямо сейчас
        log.info("%s: выбор кнопки страницей не удался, иду перебором", chain, exc_info=True)

    # Запасной перебор: тот самый, прежний. Оставлен на случай, когда evaluate
    # запрещён политикой страницы, — но он снова может взять чужую кнопку, и
    # молчать об этом нельзя.
    want = re.compile(pattern, re.I)
    try:
        buttons = page.query_selector_all("button, [role=button]")
    except Exception:  # noqa: BLE001
        return None
    for node in buttons[:400]:
        try:
            if not node.is_enabled() or not node.is_visible():
                continue
            name = (node.get_attribute("aria-label") or node.inner_text() or "").strip()
        except Exception:  # noqa: BLE001 — узел мог исчезнуть между проверками
            continue
        if want.search(" ".join(name.split())):
            return node
    return None


# «Нет в наличии» — только в блоке покупки. Прежняя проверка искала слова по
# первым 6000 знакам всей страницы, а там и карусель («Закончился» у соседа), и
# баннеры («акция закончилась»): позиция пропускалась при товаре в наличии.
_GONE_JS = """([pattern, window_px]) => {
    const want = new RegExp(pattern, 'i');
    const h1 = document.querySelector('h1');
    if (!h1) return null;
    const top = h1.getBoundingClientRect().top + window.scrollY;
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walk.nextNode())) {
        const text = (node.nodeValue || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        if (!text || !want.test(text)) continue;
        const holder = node.parentElement;
        if (!holder) continue;
        const r = holder.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const y = r.top + window.scrollY;
        if (y >= top - 200 && y <= top + window_px) return true;
    }
    return false;
}"""


def _gone(page, text: str) -> bool:
    """Говорит ли КАРТОЧКА, что товара нет. Слова в карусели и баннерах не в счёт.

    Страница без заголовка (редко) или с запретом на скрипт — прежняя проверка по
    тексту: лучше лишний раз не положить, чем положить не то.
    """
    try:
        found = page.evaluate(_GONE_JS, [signals.GONE, BUYBOX_BELOW])
    except Exception:  # noqa: BLE001 — страница не дала скрипт: судим по тексту
        found = None
    if isinstance(found, bool):
        return found
    return bool(re.search(signals.GONE, (text or "").lower()))


# СЕТИ, У КОТОРЫХ ЗАМЕРЕНО, ЧТО НАЖАТИЕ ОБЯЗАНО УЙТИ ЗАПРОСОМ К КОРЗИНЕ.
#
# ЗАМЕР 20.09.2026, И ОН ЗАКРЫВАЕТ САМУЮ ДОРОГУЮ ИЗ ОСТАВШИХСЯ ДЫР. Сквозной
# наряд на Магните нашёл правильную кнопку («Добавить в корзину», не панель и не
# карусель), нажал её, отчитался «легло» — а корзина осталась пуста. Подсмотр
# изнутри самой страницы показал почему: после нажатия витрина отправила ДВА
# запроса, и оба к счётчикам посещений (mc.yandex.com, sync.bumlam.com). К
# корзине — НИ ОДНОГО. То есть кнопка нажалась, а сеть ничего не сделала.
#
# Ни один прежний признак этого не ловил: кнопка нашлась, нажатие не упало,
# счётчик корзины у Магнита не показывает числа никогда (проверено и на пустой,
# и после добавления), страницы /cart у него нет вовсе — она отвечает «Здесь
# ничего не нашлось». Человек прочитал бы «легло 16» и пришёл к пустой корзине.
#
# ОТВЕТ ОДНОСТОРОННИЙ, И ЭТО НАРОЧНО. «Запросов к корзине не было» доказывает,
# что не легло. Обратное — «запрос ушёл» — НЕ доказывает, что легло, и успехом
# здесь не объявляется: сеть могла ответить отказом. Поэтому проверка только
# опровергает, а подтверждать по-прежнему нечем.
#
# И только там, где замерено. Сеть, чьих запросов никто не видел, сюда не
# вписана: выдуманный список слов дал бы ЛОЖНОЕ «не легло» на работающей
# передаче — а это хуже молчания, потому что человек полез бы чинить целое.
#
# И СЧИТАТЬ НАДО ТОЛЬКО СВОИ ЗАПРОСЫ СЕТИ — ЭТО НЕ ПРИДИРКА, А ЦЕНА ПЕРВОЙ
# ВЕРСИИ ЭТОЙ ПРОВЕРКИ. Она отбирала по одному слову в адресе, и первым же
# замером «единственным запросом к корзине» оказался пиксель Яндекс.Метрики:
#     POST mc.yandex.ru/watch/56708149/1?page-url=goal://magnit.ru/
#          product_productPage_buyBox_toCart_click   →   200, картинка GIF
# В его адресе есть toCart, слово «cart» совпало — и сторож, поставленный ловить
# молчание сети, начал считать ЧУЖОЙ счётчик посещений. Он летит при каждом
# нажатии, поэтому ноль не наступал никогда: проверка выглядела рабочей и не
# защищала ни от чего.
#
# Поэтому пара: хозяин адреса и слова. Чужой хозяин не в счёт, кто бы что ни
# написал у себя в параметрах.
SENDS_CART = {"magnit": ("magnit.ru", ("cart", "basket", "korzin"))}


# Обёртка в самой странице только ЗАПИСЫВАЕТ, а решает питон, и разделено это
# нарочно. Первая версия отбирала свои запросы прямо в JS, и правило оказалось
# там, где его не проверить ни одним тестом, — а оно сразу же и оказалось
# неверным (см. SENDS_CART выше про пиксель Метрики). Теперь страница отдаёт
# сырой список «адрес, код», а отбор живёт в _cart_calls рядом со сторожами.
#
# Обёртка сквозная: настоящий fetch зовётся как был и его же обещание
# возвращается, код ответа снимается побочной веткой. Обработчик отказа у неё
# пустой нарочно — без него отвергнутое обещание чужой страницы превратилось бы
# в необработанную ошибку по нашей вине. Картинки и стили сюда не попадают:
# перехвачены только fetch и XMLHttpRequest, то есть обращения за данными.
WATCH_LIMIT = 60

_WATCH_JS = """(limit) => {
    if (window.__kzWatch) { window.__kzSeen = []; return 'уже стоит'; }
    window.__kzWatch = true;
    window.__kzSeen = [];
    const note = (url, code) => {
        if (window.__kzSeen.length < limit) window.__kzSeen.push([String(url || ''), code]);
    };
    const full = (u) => {
        try { return new URL(String(u || ''), document.location.href).href; }
        catch (e) { return String(u || ''); }
    };
    const real = window.fetch;
    window.fetch = function (...args) {
        let where = '';
        try {
            const first = args[0];
            where = full((typeof first === 'string') ? first : (first && first.url) || '');
        } catch (e) { /* чужая страница: записать не вышло, мешать ей нельзя */ }
        const answer = real.apply(this, args);
        if (where && answer && typeof answer.then === 'function') {
            answer.then(
                (res) => { try { note(where, res.status); } catch (e) { } },
                () => { try { note(where, 0); } catch (e) { } });
        }
        return answer;
    };
    const open = window.XMLHttpRequest.prototype.open;
    window.XMLHttpRequest.prototype.open = function (method, url) {
        try {
            const where = full(url);
            this.addEventListener('loadend', () => {
                try { note(where, this.status); } catch (e) { }
            });
        } catch (e) { }
        return open.apply(this, arguments);
    };
    return 'поставлено';
}"""


def _watch_cart(page, chain: str) -> bool:
    """Начать записывать запросы страницы. False — этой сети мы не судим."""
    known = SENDS_CART.get(chain)
    if not known or not known[0] or not known[1]:
        return False
    try:
        page.evaluate(_WATCH_JS, WATCH_LIMIT)
        return True
    except Exception:  # noqa: BLE001 — не поставилось: просто не будет проверки
        log.info("%s: запись запросов не поставилась", chain, exc_info=True)
        return False


def own_cart_calls(seen, chain: str) -> list[int]:
    """Коды ответов на запросы К КОРЗИНЕ ЭТОЙ СЕТИ. Чужие адреса не в счёт.

    ЗАЧЕМ ОТДЕЛЬНАЯ ФУНКЦИЯ И ПОЧЕМУ ОНА ПУБЛИЧНАЯ. Это то самое правило, на
    котором проверка уже один раз сломалась: отбор шёл по слову в адресе, и
    «единственным запросом к корзине» оказался счётчик посещений —
    mc.yandex.ru/watch/...?page-url=goal://magnit.ru/..._toCart_click. Слово
    «cart» совпало, ноль не наступал никогда, и сторож молча не работал.
    Правило вынесено сюда, чтобы его проверяли тесты, а не живая сеть.

    Хозяин адреса сверяется целиком, а слова ищутся в пути и параметрах — но
    НЕ в имени хозяина: иначе «cart» в чужом домене снова сошло бы за своё.
    """
    known = SENDS_CART.get(chain)
    if not known:
        return []
    host, words = known
    out: list[int] = []
    for row in seen or []:
        try:
            address, code = row[0], row[1]
        except (TypeError, IndexError, KeyError):
            continue
        parts = urlsplit(str(address))
        where = (parts.hostname or "").lower()
        if where != host and not where.endswith("." + host):
            continue
        tail = f"{parts.path}?{parts.query}".lower()
        if not any(word in tail for word in words):
            continue
        if isinstance(code, (int, float)) and not isinstance(code, bool):
            out.append(int(code))
    return out


def _cart_calls(page, chain: str) -> list[int] | None:
    """Ответы сети на её собственные запросы к корзине. None — прочитать не вышло."""
    try:
        seen = page.evaluate("() => window.__kzSeen")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(seen, list):
        return None
    return own_cart_calls(seen, chain)


# Как назвать отказ сети. Общая фраза «сеть ответила отказом» верна, но не
# говорит человеку, что делать, — а по коду это как раз известно.
_REFUSAL = {
    401: "сеть не считает вас вошедшим",
    403: "сеть не пустила запрос",
    404: "сеть не нашла у себя эту корзину",
}


def _refused(codes: list[int]) -> str:
    """Отказала ли сеть на запрос к корзине. Пусто — не отказала или не знаем.

    ОТКАЗ ЗАСЧИТЫВАЕТСЯ ТОЛЬКО ТОГДА, КОГДА УДАЧНЫХ ОТВЕТОВ НЕ БЫЛО НИ ОДНОГО.
    Витрина дёргает корзину не только ради нашей кнопки — она её и перечитывает,
    и пересчитывает; один отказ среди удачных ответов ничего не доказывает, а
    объявить по нему передачу провалившейся значило бы завести новую ложь вместо
    той, которую мы только что убрали.
    """
    if not codes:
        return ""
    if any(200 <= code < 400 for code in codes):
        return ""
    bad = [code for code in codes if code >= 400]
    if not bad:
        return ""
    return _REFUSAL.get(bad[0], f"сеть ответила отказом {bad[0]}")

def _put_one(page, chain: str, line: dict) -> tuple[bool, str, str, int]:
    """Открыть карточку и положить одну позицию.

    Возвращает (легло, причина, заметка об округлении, сколько штук легло) —
    врозь, а не склейкой. Заметка нужна и у удавшейся позиции, а причина есть
    только у неудавшейся, и смешивать их значит терять первую.

    ЧИСЛО ПОЛОЖЕННОГО ЗДЕСЬ — САМОЕ ВАЖНОЕ ИЗ ЧЕТЫРЁХ, И ВОТ ПОЧЕМУ.
    Раньше оно жило только внутри человеческой фразы «положено 2 из 3» и дальше
    никуда не ехало: в записи оставалось одно «не легло». Повтор брал такую
    позицию целиком и щёлкал её с нуля — в корзине оказывалось пять штук вместо
    трёх, и платил за это человек на кассе. Обрыв на середине не экзотика: после
    первого нажатия витрина перерисовывает карточку, узел отваливается, а кнопку
    «ещё одну» у пяти сетей из шести живьём никто не видел.
    """
    times, rounded = pieces_of(line)

    url = line.get("url")
    if not url:
        return False, "у этой позиции нет адреса карточки — открыть нечего", rounded, 0
    if not driver._same_chain(chain, url):
        return False, "адрес карточки принадлежит не этой сети", rounded, 0

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        # Хвост чужой ошибки — в журнал, человеку — причину и следующий шаг.
        # «Timeout 45000ms exceeded» в отчёте у полки не объясняет ничего: это и
        # англицизм в интерфейсе, и ошибка без ответа на вопрос «что мне делать».
        log.info("%s: карточка %s не открылась (%s)", chain, url, exc)
        return False, ("карточка не открылась — сеть не отдала её страницу. Повторите "
                       "передачу позже; если повторится, положите этот товар руками"), rounded, 0
    driver._settle(page, 0.6)

    try:
        text = page.inner_text("body")[:6000]
    except Exception:  # noqa: BLE001
        text = ""
    stop = signals.guard_kind(text)
    if stop == "check":
        return False, ("сеть встретила проверкой «я не робот» — пройдите её в кабинете "
                       "этой сети и повторите передачу"), rounded, 0
    if stop:
        # Не проверка, а глухая страница: проходить там нечего, и звать человека
        # «пройти её» значило бы послать его нажимать в пустоту.
        return False, ("сеть не отдала страницу карточки — вместо неё «Не удалось загрузить "
                       "сайт». Повторите позже"), rounded, 0
    if signals.no_showcase(text):
        # Не поломка вёрстки и не сбой сети: витрина не держит карточек в этом
        # магазине. Без этой ветки разбор не находил кнопку и винил сеть в том,
        # чего она не делала, — «похоже, вёрстку переделали», шестнадцать раз
        # подряд (см. NO_SHOWCASE в signals.py).
        return False, ("витрина не открывает карточки в выбранном магазине — из него "
                       "нельзя заказать вовсе. Выберите другую точку"), rounded, 0
    if _gone(page, text):
        return False, "карточка говорит, что товара нет в наличии", rounded, 0

    watching = _watch_cart(page, chain)
    put = 0
    why = ""
    for i in range(times):
        node = _add_button(page, chain, again=i > 0)
        if node is None:
            why = ("на карточке не нашлось кнопки «в корзину» — приложение ищет её по "
                   "доступному имени, похоже, вёрстку переделали") if i == 0 else \
                  f"положено {put} из {times}: кнопки «ещё одну» не нашлось"
            break
        try:
            node.scroll_into_view_if_needed(timeout=5000)
            node.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            log.info("%s: кнопка на %s не нажалась (%s)", chain, url, exc)
            why = ("кнопка «в корзину» не нажалась — карточка перерисовалась под пальцем"
                   if i == 0 else f"положено {put} из {times}: карточка перерисовалась")
            break
        put += 1
        time.sleep(CLICK_PAUSE)

    # ПОСЛЕДНЕЕ СЛОВО — НЕ ЗА НАЖАТИЕМ, А ЗА ТЕМ, ЧТО СЕТЬ ИЗ-ЗА НЕГО СДЕЛАЛА.
    # Ноль запросов к корзине при нажатых кнопках значит, что не легло ничего, —
    # см. SENDS_CART выше. Проверка односторонняя: она опровергает, но не
    # подтверждает, поэтому ветка одна и только отрицательная.
    if watching and put:
        answers = _cart_calls(page, chain)
        if answers == []:
            return False, ("кнопка нажалась, а сеть не обратилась к своей корзине ни "
                           "разу — значит не легло ничего. Откройте кабинет этой сети "
                           "в окне магазина и посмотрите, чего она ждёт: чаще всего "
                           "это выбор магазина или повторный вход"), rounded, 0
        refusal = _refused(answers or [])
        if refusal:
            return False, (f"сеть приняла нажатие и отказала на запрос к корзине: "
                           f"{refusal}. Откройте её кабинет в окне магазина"), rounded, 0

    if put >= times:
        return True, "", rounded, put
    return False, why or f"положено {put} из {times}", rounded, put


# Ход передачи. Она идёт минуту и больше, и без этой отметки экран показывал бы
# человеку неподвижную страницу: он решит, что нажатие не сработало, и нажмёт ещё
# раз — то есть положит всё в корзину дважды.
#
# ЧТО В ЗАПИСИ: started_at/finished_at — когда началась и кончилась; total — сколько
# позиций в ЭТОМ заходе; at — которую кладём прямо сейчас; done — сколько уже
# обработано; now — её название; note — итоговое слово; items — отчёт по позициям;
# beat — когда отметку последний раз трогали (по ней видно умершую передачу).
PROGRESS_KEY = "handover.progress"

# Столько строк отчёта держим, и ни одной больше. Наряд ограничен LINE_LIMIT, но в
# отчёт к нему добавляется перенесённое с прошлого захода, и без потолка запись в
# настройке росла бы от повтора к повтору.
ITEMS_LIMIT = LINE_LIMIT * 2

# Замок пускателя. Стоит вокруг пары «посмотреть, не идёт ли» + «отметить, что
# пошла»: без него два одновременных нажатия оба увидели бы «не идёт».
_START = threading.Lock()

# Замок самой записи. Отметка о ходе — ОДНА строка настройки на все шесть сетей, и
# пишется она чтением, слиянием и записью целиком. Передачи в две разные сети у
# одного человека пускаются с «Результата» по каждому магазину и идут параллельно:
# без замка второй поток читает запись до правки первого и возвращает её обратно.
# Обычно это самолечится следующей записью, но не последняя: потерянный finished_at
# оставляет «идёт» и погашенную кнопку на все пять минут STALE_AFTER.
#
# ПОЧЕМУ ОТДЕЛЬНЫЙ, А НЕ _START. Пускатель держит _START и внутри зовёт _progress;
# обычный threading.Lock не повторный, и один замок на двоих встал бы намертво.
# Порядок захвата всегда один — сначала _START, потом _WRITE, — поэтому и так не
# встанут. Сервер один (waitress раздаёт потоками одного процесса), так что этого
# замка достаточно.
_WRITE = threading.Lock()


def _progress(chain: str, **fields) -> None:
    """Записать, где мы сейчас. Зовётся из потока, у которого база уже открыта."""
    import json

    from app import repo

    try:
        with _WRITE:
            raw = repo.get_setting(PROGRESS_KEY)
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}
            fresh = {**(data.get(chain) or {}), **fields, "beat": time.time()}
            if isinstance(fresh.get("items"), list):
                # Обрезаем НАЧАЛО, а не конец. Перенесённое с прошлых заходов лежит
                # впереди, сегодняшнее дописывается в хвост — и потолок, снятый с
                # головы списка, отрезал бы именно то, что случилось сейчас.
                fresh["items"] = fresh["items"][-ITEMS_LIMIT:]
            data[chain] = fresh
            repo.set_setting(PROGRESS_KEY, json.dumps(data, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — отметка о ходе не повод ронять передачу
        log.warning("%s: ход передачи не записался", chain, exc_info=True)


def progress(chain: str) -> dict | None:
    """Где сейчас передача в эту сеть. Не шла — None."""
    import json

    from app import repo

    raw = repo.get_setting(PROGRESS_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    got = data.get(chain) if isinstance(data, dict) else None
    return got if isinstance(got, dict) else None


def running(chain: str) -> bool:
    """Идёт ли передача в эту сеть прямо сейчас — с поправкой на умершую отметку.

    Незакрытая отметка бывает не только у живой передачи: сервер мог
    перезапуститься посреди наряда, и тогда «идёт» осталось бы навсегда. Живой
    считаем ту, которую трогали не позже STALE_AFTER назад.
    """
    got = progress(chain)
    if not got or got.get("finished_at"):
        return False
    try:
        beat = float(got.get("beat"))
    except (TypeError, ValueError):
        # Отметка без пульса старше этого кода. Считать её живой нельзя: кнопка
        # «Передать» тогда не нажмётся уже никогда, а исправить это человеку нечем.
        return False
    return (time.time() - beat) < STALE_AFTER


def already_sent(chain: str, hours: float | None = None) -> dict | None:
    """Недавняя передача в эту сеть, после которой в корзине что-то лежит. Нет — None.

    ЗАЧЕМ. Сеть кладёт товар ПОВЕРХ лежащего: вторая полная передача за вечер
    удваивала корзину — у METRO одним запросом, у остальных нажатиями. От двойного
    нажатия защищала только отметка «идёт», а после конца передачи кнопка снова
    была живой. Возвращает {finished_at, landed} — чтобы экран сказал, когда и
    сколько уже легло, и спросил, класть ли ещё раз.
    """
    from datetime import datetime

    got = progress(chain)
    if not got or not got.get("finished_at") or running(chain):
        return None
    try:
        when = datetime.fromisoformat(str(got["finished_at"]))
    except ValueError:
        return None
    window = float(RESEND_HOURS if hours is None else hours) * 3600.0
    if (datetime.now() - when).total_seconds() > window:
        return None
    # «Неизвестно» (браузер не ответил вовремя) — тоже в счёт: поручение осталось в
    # очереди и, скорее всего, дощёлкало товар. Считать его неположенным значило бы
    # пустить полную передачу поверх него.
    landed = [i for i in (got.get("items") or [])
              if isinstance(i, dict) and (i.get("ok") or i.get("unknown"))]
    if not landed:
        return None
    return {"finished_at": str(got["finished_at"]), "landed": len(landed)}


def report(chain: str) -> list[dict]:
    """Отчёт по позициям последней передачи: что легло, что нет и почему."""
    got = progress(chain) or {}
    return [item for item in (got.get("items") or []) if isinstance(item, dict)]


def failed_skus(chain: str) -> list[str]:
    """Артикулы, которые в прошлый раз не легли. Пока передача идёт — пусто.

    Это и есть наряд на повтор. Брать для повтора весь прежний наряд нельзя:
    удавшееся легло бы второй раз, и человек получил бы двойную корзину.

    ПОЗИЦИЯ СО ЗНАКОМ «НЕИЗВЕСТНО» СЮДА НЕ ПОПАДАЕТ. Это та, про которую браузер
    не ответил вовремя, а поручение осталось в очереди и, возможно, дощёлкало её
    в корзину. «Не знаю» — не то же, что «не легло»: повторить такое значит с
    доброй половиной вероятности положить второй раз. Человеку про неё сказано
    словами в отчёте, и решает он, глядя в свою корзину.
    """
    if running(chain):
        return []
    out: list[str] = []
    for item in report(chain):
        sku = item.get("sku")
        if sku and not item.get("ok") and not item.get("unknown"):
            out.append(str(sku))
    return out


def _left_to_put(chain: str, sku: str) -> int | None:
    """Сколько штук этой позиции ещё НЕ положено. Нечего уточнять — None.

    Позиция может лечь наполовину: человек просил три, витрина перерисовалась
    после второго нажатия, и в корзине две. Повтор обязан доложить одну, а не три.
    """
    for item in report(chain):
        if str(item.get("sku") or "") != str(sku):
            continue
        if item.get("ok"):
            return None
        try:
            put, times = int(item.get("put") or 0), int(item.get("times") or 0)
        except (TypeError, ValueError):
            return None
        return times - put if 0 < put < times else None
    return None


def _with_qty(line, qty: float):
    """Та же строка наряда, но с другим количеством. Не вышло — None.

    Наряд состоит из неизменяемых строк (app/cartplan.PlanLine), и подправить
    количество на месте нельзя — это и к лучшему: ту же строку читает экран.
    Если строка окажется не наряда, а чужого вида, лучше не доложить, чем
    положить целиком то, что лежит наполовину.
    """
    import dataclasses

    # Недостающее — уже в НАЖАТИЯХ, а не в килограммах корзины: перевод в
    # упаковки сделан при первом заходе, и второй раз его делать нельзя.
    try:
        if dataclasses.is_dataclass(line) and hasattr(line, "pack_g"):
            return dataclasses.replace(line, qty=qty, pack_g=None, per=None)
        return dataclasses.replace(line, qty=qty)
    except Exception:  # noqa: BLE001
        log.warning("строку наряда %s не удалось урезать до %s",
                    getattr(line, "sku", "?"), qty, exc_info=True)
        return None


def _shortlist(chain: str, plan, only) -> tuple[list, dict]:
    """Позиции этого захода и то, сколько их уже лежит в корзине.

    Полный наряд идёт как есть. Повтор — только названные в `only`, и у тех из
    них, что легли наполовину, количество урезано до недостающего: иначе повтор
    кладёт всю позицию заново поверх лежащего.
    """
    lines = [ln for ln in (getattr(plan, "lines", None) or []) if getattr(ln, "sku", None)]
    if only is None:
        return lines, {}

    wanted = {str(sku) for sku in only}
    out, already = [], {}
    for line in lines:
        sku = str(getattr(line, "sku", ""))
        if sku not in wanted:
            continue
        left = _left_to_put(chain, sku)
        if left is None:
            out.append(line)
            continue
        short = _with_qty(line, left)
        if short is None:
            continue
        out.append(short)
        already[sku] = pieces_of(line)[0] - left
    return out, already


def _carry(chain: str, attempted: set[str]) -> list[dict]:
    """Что уже разобрано прошлым заходом и в этот раз не трогается.

    Без переноса повтор двух позиций показал бы «легло 2» там, где в корзине
    лежит четырнадцать: человек решил бы, что передача развалилась, и пошёл бы
    собирать всё заново руками.
    """
    out: list[dict] = []
    for item in report(chain):
        if str(item.get("sku") or "") in attempted:
            continue
        out.append({**item, "earlier": True})
    return out


def _row(line, ok: bool, why: str, note: str, earlier: bool = False,
         put: int = 0, unknown: bool = False) -> dict:
    """Строка отчёта. Название и количество — те же, что человек видел в наряде.

    `put` и `times` здесь не для экрана, а для повтора: по ним он узнаёт, что у
    позиции легла только часть, и докладывает недостающее вместо целого.
    `unknown` — «браузер не ответил, легло или нет»: такое не повторяют.
    """
    times, _ = pieces_of(line)
    return {"sku": str(getattr(line, "sku", "") or ""),
            "name": getattr(line, "name", "") or str(getattr(line, "sku", "") or ""),
            "qty": getattr(line, "qty", None),
            # Количество считано в единице КОРЗИНЫ (per), если она известна: «0,4 кг»,
            # а не «0,4 шт» у сыра, который сеть продаёт упаковками.
            "unit": getattr(line, "per", None) or getattr(line, "unit", None),
            "ok": bool(ok), "why": why or "", "note": note or "", "earlier": earlier,
            "put": int(put), "times": int(times), "unknown": bool(unknown)}


class _Naryad:
    """Наряд из одних только повторяемых позиций.

    Передаче от наряда нужны одни `lines`, и подменять ради этого настоящий
    CartPlan (app/cartplan.py) значило бы тащить его сюда целиком со всеми полями,
    которые здесь не используются ни разу.

    `already` — сколько штук по каждому артикулу УЖЕ лежит в корзине с прошлого
    захода. Отчёту это нужно, чтобы сказать «доложено 1 к двум лежащим», а не
    «легло 1» там, где человек просил три и в корзине теперь все три.
    """

    __slots__ = ("lines", "already")

    def __init__(self, lines: list, already: dict | None = None) -> None:
        self.lines = lines
        self.already = already or {}


# Сколько позиций сеть показывает в своей корзине. Читаем ЕЁ счётчик, а не свой.
#
# ЗАЧЕМ ЭТО ЕСТЬ. Наряд до 19.09.2026 считал позицию положенной по нажатию:
# нашлась кнопка, нажалась — «легло». В тот день выяснилось, чего это стоит. На
# METRO нажатие прошло, витрина товар приняла (её собственный рекламный пиксель
# ушёл с product_add_to_cart), а счётчик корзины как показывал ноль, так и остался
# нулём. То есть отчёт сказал бы «передано», а человек пришёл бы к пустой корзине.
#
# Счётчик подделать нажатием нельзя: его рисует сама сеть по своей корзине. Он не
# заменяет отчёт по позициям — он его ПРОВЕРЯЕТ, и расхождение важнее любой нашей
# уверенности.
# СЧЁТЧИК БРАЛ ЧИСЛА ИЗ ТОВАРНЫХ ПЛИТОК, И ЭТО ВЫЯСНИЛОСЬ ТОЛЬКО ЖИВЬЁМ.
#
# Замер 20.09.2026 на боевом сервере: сквозной наряд положил ОДИН товар, а отчёт
# сказал «Корзина магазина показывает 208 позиц.». Причина в том, что под «узел,
# в тексте которого есть „корзин"» попадает каждая плитка товара — у неё внутри
# своя кнопка «В корзину», — и первым числом в её тексте оказывается цена или
# число отзывов: «Финальная цена 249.99 ₽ … 9738 отзывов В корзину».
#
# Число в отчёте — последнее слово перед тем, как человек пойдёт оформлять
# заказ, и соврать им хуже, чем промолчать: «208 позиц.» выглядит как настоящий
# счётчик и не вызывает никаких подозрений.
#
# Отличаем по РАЗМЕРУ УЗЛА, а не по словам. Замер того же дня: настоящий счётчик
# Магнита — кнопка с aria-label «Перейти в корзину», текстом «Корзина» и ТРЕМЯ
# потомками; плитка товара несёт тридцать пять и больше. Ровно та же ошибка уже
# ловилась 19.09.2026 у сборщика ссылок на корзину — он тоже брал плитку за
# корзину, потому что внутри неё написано «В корзину».
_COUNT_MAX_KIDS = 8
_COUNT_MAX_CHARS = 40

_COUNT_JS = """() => {
    let best = null;
    for (const e of document.querySelectorAll('a,button,[role=button]')) {
        if (e.querySelectorAll('*').length > %d) continue;
        const t = ((e.getAttribute('aria-label') || '') + ' ' +
                   (e.innerText || '')).replace(/\\s+/g, ' ').trim();
        if (t.length > %d) continue;
        if (!/корзин/i.test(t)) continue;
        const m = t.match(/(\\d+)/);
        if (m) { const n = parseInt(m[1], 10); if (best === null || n > best) best = n; }
    }
    return best;
}""" % (_COUNT_MAX_KIDS, _COUNT_MAX_CHARS)


def in_cart(page) -> int | None:
    """Сколько позиций в корзине сети по её собственному счётчику. None — «не видно».

    None и ноль — РАЗНЫЕ ответы, и путать их нельзя ни в какую сторону. У части
    сетей счётчик появляется только когда корзина не пуста (так у Магнита: пустую
    он подписывает «Перейти в корзину» без числа). Прочитать отсутствие числа как
    ноль значило бы объявить провалившейся удавшуюся передачу.
    """
    try:
        value = page.evaluate(_COUNT_JS)
    except Exception:  # noqa: BLE001 — чужая страница: не прочиталось, и ладно
        return None
    return int(value) if isinstance(value, (int, float)) else None


def _checked(chain: str, phone: str, ok: list, note: str) -> str:
    """Дописать к итогу то, что показывает счётчик корзины самой сети.

    Молчание здесь было бы худшим вариантом: человек прочитал бы «легло 16» и
    пошёл оформлять заказ. Поэтому расхождение называется прямо, а «не видно» —
    так и говорится, без домыслов в любую сторону.
    """
    if not ok:
        return note
    try:
        count = driver.run(chain, phone, in_cart, timeout=45)
    except Exception:  # noqa: BLE001 — проверка не должна ронять передачу
        log.info("%s: счётчик корзины не прочитался", chain, exc_info=True)
        return note
    if count is None:
        return note
    if count == 0:
        return ((note + " ") if note else "") + (
            f"ВНИМАНИЕ: приложение положило {len(ok)} позиц., а корзина магазина "
            "показывает ноль. Откройте её в окне магазина и посмотрите сами — "
            "возможно, сеть не приняла товары.")
    if count < len(ok):
        # Меньше, чем положено, — часть нажатий сеть не засчитала. Больше — не
        # улика: в корзине могло лежать своё, и счётчик у части сетей считает штуки.
        return ((note + " ") if note else "") + (
            f"ВНИМАНИЕ: приложение положило {len(ok)} позиц., а корзина магазина "
            f"показывает {count}. Откройте её в окне магазина и сверьте с отчётом ниже.")
    return ((note + " ") if note else "") + f"Корзина магазина показывает {count} позиц."


def _keep_point(page) -> bool:
    """Закрыть окно выбора магазина, НИЧЕГО НЕ ВЫБРАВ. Ответ — ушло ли оно.

    Зовётся только там, где точка расчёта уже передана кукой: тогда за окном
    стоит ИМЕННО ТОТ магазин, по которому человек видел сумму, и «Не сейчас»
    означает «оставить его», а не «выбрать какой-нибудь». Без переданной точки
    это нажатие согласилось бы на магазин витрины — см. KEEP_POINT_NAME.

    Ищем по доступному имени, а не по разметке: классы у сети хэшированные и
    меняются с каждой сборкой, а подпись кнопки — это то, что видит человек.
    """
    want = re.compile(signals.KEEP_POINT_NAME, re.I)
    try:
        buttons = page.query_selector_all("button, [role=button]")
    except Exception:  # noqa: BLE001 — окна может уже не быть
        return False
    for node in buttons[:200]:
        try:
            if not (node.is_enabled() and node.is_visible()):
                continue
            name = (node.get_attribute("aria-label") or node.inner_text() or "").strip()
        except Exception:  # noqa: BLE001 — узел мог исчезнуть между проверками
            continue
        if not want.search(" ".join(name.split())):
            continue
        try:
            node.click(timeout=8000)
        except Exception:  # noqa: BLE001 — не нажалось: скажем «не ушло», и наряд встанет
            log.info("%s: «Не сейчас» не нажалось", "окно выбора", exc_info=True)
            return False
        driver._settle(page, 1.2)
        try:
            return not signals.needs_store(page.inner_text("body")[:20000])
        except Exception:  # noqa: BLE001
            return False
    return False


def _which_point(chain: str) -> str:
    """Какую точку сети приложение считало. Пусто — не знаем, и молчим.

    ЗАЧЕМ ЭТО ПРИСТЁГНУТО К ПРОСЬБЕ ВЫБРАТЬ МАГАЗИН. Выбрать точку за человека мы
    не станем: цена, наличие и доставка у сетей свои в каждой, и это решение про
    его деньги. Но отправить его выбирать ВСЛЕПУЮ — плохая половина честности: он
    выберет соседнюю, корзина соберётся по другим ценам, и расчёт, который он
    видел, окажется не про его покупку. Та же беда уже случалась со ссылкой
    Ленты — она открылась в чужой точке (app/handover.NOTE_BY_STORE).

    Поэтому называем ту самую точку, по которой считали, и пусть он выберет её.
    """
    try:
        from app import places

        found = places.points(chain)
    except Exception:  # noqa: BLE001 — подсказка не повод ронять передачу
        return ""
    labels = [p.label or p.address for p in found if (p.label or p.address)]
    if not labels:
        return ""
    if len(labels) == 1:
        return f" Приложение считало по точке: {labels[0]}."
    return " Приложение считало по точкам: " + "; ".join(labels[:3]) + "."


def _metro(chain: str, lines: list, items: list, state: dict) -> dict:
    """Корзина METRO: не нажатия по карточкам, а её собственный способ наполнения.

    ПОЧЕМУ ЭТА СЕТЬ ИДЁТ МИМО БРАУЗЕРА. METRO единственная описала наполнение
    корзины публично (api.metro-cc.ru/docs), и шестнадцать позиций уходят ОДНИМ
    запросом вместо шестнадцати загрузок страниц. Водить браузер там, где сеть
    сама сказала «вот метод», значит выбрать худший способ из двух: медленнее и
    зависимо от чужой вёрстки.

    ОКНО ВСЁ РАВНО НУЖНО, и не для нажатий. По спецификации корзина находится по
    `user_hash`, и хеш человека лежит в его же сохранённом входе — сеть выдала
    его кукой, когда человек открыл METRO в окне. Своего, серверного хеша мы не
    подставляем: наполненная им корзина осталась бы нашей и человеку бесполезной.

    ЧТО СЧИТАЕТСЯ УСПЕХОМ. Не «сеть ответила 200», а «позиция лежит в корзине»:
    после записи корзина перечитывается, и отчёт строится по тому, что в ней
    ВИДНО. Иначе человек прочитал бы «передано 16» и пришёл бы к пустой корзине.
    """
    from app import location
    from app.connectors import metro, metro_cart

    # Токен спрашиваем ПЕРВЫМ и до всякой сети. Без него запись отвечает 400
    # «Basket not found» (замер 19.09.2026), и человек, нажавший «Передать», ждал
    # бы ответа чужой сети вместо понятной фразы о том, чего не хватает у нас.
    if not metro_cart.writable():
        return _done(chain, [], [], items,
                     "METRO принимает корзину только по именному токену: без него сеть "
                     "отвечает «Basket not found». Токен получают через форму METRO для "
                     "бизнеса, кладут в настройку connectors.metro_api_token — и передача "
                     "заработает без единой правки. Пока его нет, соберите корзину по "
                     "ссылкам на товары: они рядом.")

    user_hash = metro_cart.hash_of(state)
    if not user_hash:
        return _done(chain, [], [], items,
                     "METRO ещё не признала корзину вашей. Откройте её кабинет в окне "
                     "магазина — сеть выдаст корзине хозяина, и передача заработает.")
    store_id = metro._store_id(location.for_store(chain))
    if not store_id:
        return _done(chain, [], [], items,
                     "Не выбран торговый центр METRO: цены, наличие и корзина у неё свои "
                     "в каждом. Укажите адрес — приложение подберёт ближайший.")

    # Шапку отметки о ходе ставим и здесь: пускатель ставит её до потока, но
    # deliver переписывает её у остальных сетей, и без этой строки у METRO
    # остались бы чужие «всего» и «сейчас» от прошлой передачи.
    _progress(chain, started_at=time.strftime("%Y-%m-%dT%H:%M:%S"), finished_at=None,
              done=0, at=1, total=len(lines), note="", items=items,
              now=f"передаю {len(lines)} позиций одним разом")
    # Сколько штук просим — то же число, что покажет заметка об округлении:
    # раньше METRO округляла вверх сама (1,3 → 2), а заметка считала через round
    # и писала «взяли 1 упаковку». Человек читал одно, а в корзине было другое.
    from types import SimpleNamespace

    counts = {str(ln.sku): pieces_of(ln)[0] for ln in lines}
    # Что лежало ДО записи. Сеть кладёт поверх лежащего, и по одному итогу не
    # понять, сколько положили сейчас: повтор «доложить одну» при двух лежащих
    # видел бы в корзине две и отчитывался «легло», ничего не положив.
    try:
        had = _metro_counts(metro_cart.read(store_id, user_hash))
    except Exception:  # noqa: BLE001 — не прочиталось: считаем от пустой, как раньше
        log.info("%s: корзину до записи прочитать не вышло", chain, exc_info=True)
        had = {}
    try:
        metro_cart.fill(store_id, user_hash,
                        [SimpleNamespace(sku=sku, qty=count) for sku, count in counts.items()])
        basket = metro_cart.read(store_id, user_hash)
    except Exception as exc:  # noqa: BLE001 — чужая сеть: причина важнее типа
        log.warning("%s: корзину наполнить не вышло", chain, exc_info=True)
        why = f"{type(exc).__name__}: {str(exc)[:200]}"
        for line in lines:
            items.append(_row(line, False, why, rounding_of(line)))
        return _done(chain, [], [{"sku": str(ln.sku), "why": why} for ln in lines], items,
                     "METRO не приняла корзину. " + why)

    # Что сеть засчитала, видно по самой корзине: прибавилось — значит легло.
    inside = _metro_counts(basket)
    missing = {str(r.get("article") or "").strip() for r in basket.unavailable}

    ok: list[str] = []
    failed: list[dict] = []
    for line in lines:
        sku = str(line.sku)
        mark = rounding_of(line)
        put = max(0, inside.get(sku, 0) - had.get(sku, 0))
        wanted = counts.get(sku, 1)
        if put >= wanted:
            ok.append(sku)
            items.append(_row(line, True, "", mark, put=wanted))
        elif put:
            # Легла часть: в центре меньше, чем просили. Засчитать позицию целой
            # значило бы спрятать недостачу — повтор её бы не доложил, а человек
            # узнал бы на кассе. Сколько легло, помним: повтор доложит остальное.
            why = f"положено {put} из {wanted}: в торговом центре METRO не хватило"
            failed.append({"sku": sku, "why": why})
            items.append(_row(line, False, why, mark, put=put))
        else:
            why = ("METRO ответила, что этого товара нет в выбранном торговом центре"
                   if sku in missing else "в корзине METRO этой позиции не видно")
            failed.append({"sku": sku, "why": why})
            items.append(_row(line, False, why, mark))
        _progress(chain, done=len(ok) + len(failed), items=items)

    verdict = "" if not failed else (
        "Ни одна позиция не легла в корзину METRO."
        if not ok else "Причина у каждой позиции ниже. Повторить можно только непроложенное.")
    log.info("%s: корзина наполнена одним запросом, легло %d, не легло %d",
             chain, len(ok), len(failed))
    return _done(chain, ok, failed, items, verdict)


def _metro_counts(basket) -> dict[str, int]:
    """Сколько штук каждого артикула лежит в корзине METRO."""
    inside: dict[str, int] = {}
    for row in getattr(basket, "lines", None) or []:
        key = str(row.get("article") or row.get("id") or "").strip()
        if key:
            inside[key] = inside.get(key, 0) + int(row.get("count") or row.get("quantity") or 1)
    return inside


def deliver(chain: str, phone: str, plan) -> dict:
    """Исполнить наряд в кабинете человека. Отчёт — обеими сторонами.

    Вход обязателен и проверяется здесь: без него корзина у сети своя на каждое
    устройство и живёт до закрытия вкладки, то есть наполнять было бы нечего —
    и человек не понял бы, куда делись его товары.

    ОТЧЁТ ПРОДОЛЖАЕТСЯ, А НЕ НАЧИНАЕТСЯ ЗАНОВО. Строки, уже лежащие в отметке о
    ходе, остаются: при повторе там перенесённое с прошлого захода — то, что уже
    в корзине человека. Кто и что туда положил, решает пускатель (start).
    """
    lines = [ln for ln in (getattr(plan, "lines", None) or []) if getattr(ln, "sku", None)]
    items: list[dict] = report(chain)

    if not lines:
        return _done(chain, [], [], items, "В наряде нет ни одной позиции с артикулом.")
    if len(lines) > LINE_LIMIT:
        return _done(chain, [], [], items,
                     f"В наряде {len(lines)} позиций — столько в корзине не бывает.")

    state = shopstore.load(chain)
    if not state:
        return _done(chain, [], [], items,
                     "Вход в эту сеть не сохранён: откройте её кабинет и войдите.")

    # Сеть, у которой есть свой способ наполнить корзину, идёт мимо браузера — и
    # мимо всего, что ниже: ни открывать окно, ни искать «я не робот», ни ходить
    # по карточкам ей не нужно. Проверка входа тоже своя: METRO признаёт корзину
    # по хешу, а не по тому, написано ли на витрине «Выйти».
    if chain == "metro":
        return _metro(chain, lines, items, state)

    # ТОЧКА РАСЧЁТА УХОДИТ В ОКНО ДО ПЕРВОГО ПЕРЕХОДА, и порядок здесь не
    # косметика. Витрина Магнита приходит с уже выбранным СВОИМ магазином
    # (замер 20.09.2026: shopCode=%22992301%22 на чистом окне), и всё, что
    # открылось до подстановки, открылось бы по его ценам. Подставленная после
    # первой страницы кука опоздала бы ровно так же, как опаздывает вход.
    hitch = point.trouble(chain)
    if hitch:
        # Точка известна, но заказать из неё нельзя. Молча взять вместо неё
        # магазин витрины значило бы собрать человеку корзину по ценам, которых
        # он не видел, — та самая беда, ради которой всё это и писалось.
        return _done(chain, [], [], items, hitch)
    # Передана ли точка — помним отдельно: ниже от этого зависит, вправе ли мы
    # закрыть окно выбора магазина. Без точки закрыть его значило бы согласиться
    # на магазин витрины, то есть на цены, которых человек не видел.
    carried = bool(point.cookies_for(chain))
    state = point.with_point(state, chain)

    ok: list[str] = []
    failed: list[dict] = []
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    _progress(chain, started_at=started, finished_at=None, done=0, at=0,
              total=len(lines), now="открываю магазин", note="", items=items)

    driver.open_store(chain, phone, state=state)
    seen = driver.look(chain, phone)
    if seen.get("guarded"):
        # ЧТО ИМЕННО ВСТРЕТИЛО, ТО И ГОВОРИМ. Здесь стояла одна фраза на все случаи —
        # «пройдите проверку „я не робот“», — и она врала ровно там, где помощь
        # нужнее всего. Замер 19.09.2026 настоящим браузером с нашего сервера:
        # Дикси отвечает «Не удалось загрузить сайт», а Лента — «403 Forbidden.
        # Доступ к сайту lenta.com запрещен. IP: <адрес сервера>». Ни на той, ни на
        # другой странице проходить нечего, и человек, посланный «пройти проверку»,
        # искал бы капчу, которой нет, а потом решил бы, что сломались мы.
        kind = seen.get("guard")
        if kind == "forbidden":
            note = signals.FORBIDDEN_NOTE
        elif kind == "blocked":
            note = ("Сеть не отдала страницу нашему серверу, и проходить там нечего. "
                    "Попробуйте позже, а надёжнее — соберите эту корзину со своего "
                    "телефона: оттуда сеть работает как обычно.")
        else:
            note = ("Сеть встретила проверкой «я не робот». Откройте её кабинет, "
                    "пройдите проверку и повторите передачу.")
        return _done(chain, [], [], items, note)
    if seen.get("logged_in") is False:
        return _done(chain, [], [], items,
                     "Сеть больше не считает вас вошедшим — сохранённый вход устарел. "
                     "Откройте кабинет и войдите заново.")

    # ТРЕТЬЯ ПРИЧИНА ПУСТОЙ КОРЗИНЫ, И СПРАШИВАЕМ ПРО НЕЁ ЗДЕСЬ, ДО ПЕРВОЙ КАРТОЧКИ.
    #
    # Замер 19.09.2026 на Магните: пока магазин не выбран, витрина держит поверх
    # страницы окно «Выберите магазин или адрес», кнопку «в корзину» нажать даёт,
    # а в корзину не кладёт ничего; /cart показывает то же окно. То есть наряд
    # прошёл бы все шестнадцать карточек, отчитался «легло 16» и оставил человека
    # с пустой корзиной. Одна проверка до начала дешевле шестнадцати загрузок
    # страниц и честнее любого отчёта после них.
    try:
        waiting = driver.run(chain, phone,
                             lambda page: signals.needs_store(page.inner_text("body")[:20000]),
                             timeout=40)
    except Exception:  # noqa: BLE001 — не прочиталось: не повод отменять передачу
        waiting = False
    if waiting and carried:
        # ОКНО МОЖНО ЗАКРЫТЬ, НЕ ВЫБИРАЯ МАГАЗИН, — И ТОЛЬКО ЗДЕСЬ ЭТО ЧЕСТНО.
        #
        # Замер 20.09.2026 с боевого сервера: с домашнего подключения окна нет
        # вовсе, а нашему серверу Магнит показывает его даже при верной куке
        # точки. То есть передача упиралась не в невыбранный магазин, а в адрес,
        # с которого мы стучимся, — и останавливалась там, где останавливаться
        # было не за чем: магазин УЖЕ наш, он в куке выше.
        #
        # После «Не сейчас» окно уходит, кука остаётся той же, кнопки «В корзину»
        # на месте, шапка пишет «Доставка • Экспресс». Это отказ выбирать, а не
        # выбор, и потому «carried» в условии обязателен: без переданной точки за
        # окном стоял бы магазин витрины, и мы согласились бы на ЕГО цены.
        try:
            waiting = not driver.run(chain, phone, _keep_point, timeout=40)
        except Exception:  # noqa: BLE001 — не вышло: остаётся прежняя честная остановка
            log.info("%s: окно выбора магазина не закрылось", chain, exc_info=True)
    if waiting:
        return _done(chain, [], [], items, signals.NEEDS_STORE_NOTE + _which_point(chain))

    already = getattr(plan, "already", None) or {}
    for number, line in enumerate(lines, start=1):
        item = {"sku": str(line.sku), "qty": getattr(line, "qty", 1),
                "url": getattr(line, "url", None), "name": getattr(line, "name", ""),
                "unit": getattr(line, "unit", None),
                "pack_g": getattr(line, "pack_g", None), "per": getattr(line, "per", None)}
        # Имя пишем ДО нажатия: экран должен говорить «кладу творог», пока творог
        # кладётся, а не после того, как всё кончилось.
        _progress(chain, at=number, now=item["name"] or item["sku"])
        try:
            done, why, mark, put = driver.run(
                chain, phone, lambda page, it=item: _put_one(page, chain, it),
                # Свой срок на каждую карточку: общий (90 с) короче бюджета одной
                # карточки уже при двух штуках — см. _budget.
                timeout=_budget(item["qty"], item["unit"], item["pack_g"], item["per"]))
        except driver.BrowserTimeout:
            # САМЫЙ ДОРОГОЙ СЛУЧАЙ, И ОН НЕ «НЕ ЛЕГЛО». Поручение по таймауту не
            # отменяется: оно осталось в очереди потока браузера и, скорее всего,
            # дощёлкивает товар прямо сейчас. Объявить позицию непроложенной и
            # подставить её под «Повторить» значит положить человеку второй раз
            # то, что уже лежит. Поэтому знак — «неизвестно», и в повтор эта
            # позиция не идёт (failed_skus её пропускает).
            items.append(_row(line, False,
                              "браузер не ответил вовремя — легло это или нет, приложение "
                              "не знает. Посмотрите корзину в магазине: повтор мог бы "
                              "положить второй раз, поэтому сам он её не тронет",
                              rounding_of(item), unknown=True))
            failed.append({"sku": item["sku"], "why": "браузер не ответил вовремя"})
            # А вот до остальных дело не дошло вовсе — они честно не легли.
            for rest in lines[number:]:
                failed.append({"sku": str(rest.sku), "why": "до этой позиции передача не дошла"})
                items.append(_row(rest, False, "до этой позиции передача не дошла",
                                  rounding_of(rest)))
            break
        except driver.BrowserUnavailable as err:
            # Браузер отвалился на середине. Недошедшие позиции не «пропали», а не
            # легли, и сказать это надо про КАЖДУЮ: молча оборванный отчёт даст
            # повтору одну позицию, а остальные четырнадцать останутся вне корзины
            # и вне глаз человека.
            for rest in lines[number - 1:]:
                failed.append({"sku": str(rest.sku), "why": str(err)})
                items.append(_row(rest, False, str(err), rounding_of(rest)))
            break
        if done:
            ok.append(item["sku"])
        else:
            failed.append({"sku": item["sku"], "why": why})
        # Что лежало до этого захода, дописываем к заметке: иначе повтор покажет
        # «Творог 1 шт ✓» там, где человек просил три, — и тот решит, что двух
        # не хватает, хотя в корзине ровно столько, сколько он просил.
        lay = already.get(item["sku"])
        if lay:
            mark = ((mark + "; ") if mark else "") + f"доложено к тем {lay}, что уже лежали"
        items.append(_row(line, done, why, mark, put=put))
        _progress(chain, done=len(ok) + len(failed), items=items)
        time.sleep(_step_pause())

    verdict = ""
    if not ok and failed:
        verdict = ("Ни одна позиция не легла в корзину. " +
                   (failed[0].get("why") or "Причина не разобрана."))
    elif failed:
        # Числа «легло 13 из 16» экран печатает сам; повторять их здесь значило бы
        # написать человеку одно и то же дважды подряд.
        verdict = "Причина у каждой позиции ниже. Повторить можно только непроложенное."
    # Последнее слово не за нами, а за корзиной магазина: см. in_cart выше.
    verdict = _checked(chain, phone, ok, verdict)
    # И назвать точку, в которой корзина собралась. Не из вежливости: цены у
    # сетей свои в каждой, и человек должен видеть, что корзина собралась ТАМ
    # ЖЕ, где считали, — иначе проверить это ему нечем.
    where = point.described(chain) if ok else ""
    if where:
        verdict = ((verdict + " ") if verdict else "") + f"Корзина собрана в точке: {where}."
    log.info("%s: наряд исполнен, легло %d, не легло %d", chain, len(ok), len(failed))
    return _done(chain, ok, failed, items, verdict)


def _done(chain: str, ok: list, failed: list, items: list, note: str) -> dict:
    """Закрыть отметку о ходе. Незакрытая означала бы «идёт» навсегда."""
    _progress(chain, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
              done=len(ok) + len(failed), at=0, now="", note=note, items=list(items))
    return {"ok": ok, "failed": failed, "note": note}


def start(chain: str, phone: str, plan, *, only=None, wait: bool = False,
          again: bool = False) -> bool:
    """Пустить передачу фоном. Ждать её в запросе нельзя: она идёт минутами.

    `wait` — для пачки (app/cartfill.py), а не для экрана: ей возвращаться некуда,
    пока корзина не наполнится, а фоновый поток умер бы вместе с её процессом.
    Путь при этом тот же самый — та же отметка о ходе, тот же отчёт, — и
    рабочее место после передачи так же закрыто, как в фоновом потоке.

    Отвечает, пошла ли передача. `False` значит «в эту сеть уже идёт другая» или
    «полная передача уже была недавно» (already_sent), — и это НЕ мелочь: второй
    заход положил бы всё в корзину человека повторно. Повторить целиком можно,
    только явно сказав `again=True`: это решение человека, а не второе нажатие.

    ОТМЕТКА СТАВИТСЯ ЗДЕСЬ, а не в фоновом потоке. Между «пустили поток» и «поток
    дошёл до первой записи» проходят доли секунды, и второе нажатие в эту щель
    успевает: оба захода видят «не идёт» и оба начинают класть.

    ПОТОК САМ ОТКРЫВАЕТ РАБОЧЕЕ МЕСТО. Новый поток в Python начинает с ЧИСТЫХ
    contextvars, а не с копии родительских, и путь к базе человека в нём пуст. Не
    открой он базу сам — и отчёт о передаче, и отметка о ходе уехали бы в общую
    базу из config.yaml, молча и правдоподобно (та же ловушка описана в
    app/web/auth.py). Снимаем базу в finally: поток обязан убрать за собой, даже
    когда падает.
    """
    from datetime import datetime

    from app import collector, users

    if not phone:
        return False

    naryad = plan
    with _START:
        if running(chain):
            return False
        if only is None and not again and already_sent(chain):
            return False
        if only is None:
            # Полный наряд начинает отчёт с чистого листа: прежние строки говорят
            # о прошлой корзине, и оставить их значило бы показать чужой итог.
            _progress(chain, items=[])
        else:
            lines, already = _shortlist(chain, plan, only)
            naryad = _Naryad(lines, already)
            _progress(chain, items=_carry(chain, {str(ln.sku) for ln in lines}))
        _progress(chain, started_at=time.strftime("%Y-%m-%dT%H:%M:%S"), finished_at=None,
                  done=0, at=0, total=len(getattr(naryad, "lines", None) or []),
                  now="открываю магазин", note="", retry=only is not None)

    def work() -> None:
        # open_workspace стоит ВНУТРИ try нарочно: упади он снаружи — finally не
        # выполнится, отметка о ходе останется открытой на все пять минут
        # STALE_AFTER, и кнопка «Передать» всё это время не нажмётся.
        opened = False
        try:
            users.open_workspace(phone)
            opened = True
            got = deliver(chain, phone, naryad)
            # Отчёт идёт через ту же дверь, что и прежде (app/collector.accept):
            # она же кладёт его в «прошлую передачу» на экране «Кабинеты». В нём
            # ровно этот заход: повтор двух позиций — событие «положили две», а не
            # «в корзине две». Что лежит в корзине целиком, показывает отчёт по
            # позициям выше, и путать эти два ответа нельзя.
            collector.accept({"store": chain,
                              "collected_at": datetime.now().isoformat(timespec="seconds"),
                              "cart_result": {"ok": got["ok"], "failed": got["failed"]}})
        except Exception:  # noqa: BLE001 — фоновая работа не должна ронять сервер
            log.exception("%s: передача корзины не удалась", chain)
        finally:
            # Отметка обязана закрыться, даже когда передача упала на полуслове:
            # открытая, она запрещает следующую попытку до самого STALE_AFTER, а
            # человек в это время смотрит на «идёт», которое никуда не идёт.
            try:
                # Только при открытом рабочем месте: без него и чтение, и запись
                # ушли бы в общую базу из config.yaml — молча и правдоподобно.
                if opened and running(chain):
                    _progress(chain, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                              at=0, now="",
                              note="Передача оборвалась на полуслове — посмотрите, что легло, "
                                   "и повторите остальное.")
            except Exception:  # noqa: BLE001
                log.warning("%s: отметку о ходе не закрыть", chain, exc_info=True)
            users.deactivate()

    if wait:
        work()
    else:
        threading.Thread(target=work, name=f"handover-{chain}", daemon=True).start()
    return True


__all__ = ["deliver", "start", "progress", "running", "report", "failed_skus", "rounding",
           "rounding_of", "pieces_of", "already_sent",
           "PROGRESS_KEY", "QTY_LIMIT", "LINE_LIMIT", "STALE_AFTER", "RESEND_HOURS"]
