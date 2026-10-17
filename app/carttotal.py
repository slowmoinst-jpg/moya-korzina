"""Итог корзины, который посчитала сама сеть: со страницы её корзины и из ответа её API.

ЗАЧЕМ. Расчёт приложения знает цену полки, личные купоны, снятые телефоном, и кэшбэк
банковской карты (app/optimizer/calc.py). Чего он не знает — что насчитает человеку
сама сеть: скидку по его карте лояльности, акцию «три по цене двух», доставку по его
адресу. Итог, который показала корзина сети, ближе к деньгам, которые человек
заплатит, поэтому на экране «Сборка корзин» (app/cartrun.py) он стоит первым и
подписан «посчитала сеть». Наш расчёт подписан «наш расчёт», и одно с другим не
смешивается.

ДВА ИСТОЧНИКА, И ОБА ЧИТАЮТ ТОЛЬКО ТО, ЧТО СЕТЬ НАПИСАЛА САМА.

    METRO    поле total_cost ответа корзины (app/connectors/metro_cart.py). Оно
             приходит тем же запросом, которым передача перечитывает корзину после
             записи (app/shopbrowser/cart._metro), и второго запроса ради итога нет.
    Магнит   страница корзины в окне сервера после передачи. Корзина у Магнита
             открывается не ссылкой, а кнопкой «Перейти в корзину» (замер 20.09.2026,
             docs/design/store-apis.md), её и нажимаем — по доступному имени.

НИЧЕГО НЕ ДОСЧИТЫВАЕМ. Не нашлось строки «Итого» с суммой — ответ «не прочиталось», а
не сумма видимых цен. Сложить цены позиций значило бы выдать за итог сети число,
которого сеть не показывала, без её скидок и доставки, то есть наш расчёт под чужой
подписью. Скидка и доставка тоже только те, что сеть написала сама.

ПО ДОСТУПНЫМ ИМЕНАМ, А НЕ ПО КЛАССАМ. Классы у сетей хэшированные и меняются с каждой
сборкой. Подписи «Итого», «Скидка», «Доставка» читает человек, и живут они дольше. Тот
же приём у кнопок наряда (app/shopbrowser/cart._keep_point).

ЧТО ПРОВЕРЕНО, А ЧТО НЕТ. Страницу корзины вошедшего в Магнит никто из нас не видел:
гостю Магнит корзину не наполняет (store-apis.md), а с 16.10.2026 его вход у нас не
сохраняется. Подписи ниже — общие для корзин интернет-магазинов, разбор проверен
тестами на образце (tests/fixtures/magnit_cart_page.txt), а не на живой странице.
Всё, чего разбор не узнал, остаётся «не прочиталось», и строка сети так и говорит.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime

log = logging.getLogger(__name__)

NETWORK = "network"        # посчитала сеть
OURS = "ours"              # наш расчёт

# Сети, у которых итог читается со страницы корзины в окне сервера. Только там, где
# окно вообще кладёт корзину (store_accounts.ABILITIES с CART и не ссылкой): у Ленты
# и ВкусВилла корзина уезжает ссылкой, и до входа человека её итога не видит никто.
READS = ("magnit",)

TEXT_LIMIT = 60000         # столько видимого текста страницы читаем, не больше
READ_TIMEOUT = 150         # открыть главную, нажать «Корзину», дождаться итога
WAIT_LIMIT = 10.0          # сколько ждём, пока на странице корзины появится итог
WAIT_STEP = 0.5
MARK = "data-korzina-cart"  # метка кнопки корзины, найденной страницей

# Сколько строк вокруг «Итого» считаются блоком итога. Скидка и доставка дальше этого
# — уже не про заказ: у позиций в списке бывают свои «Скидка 20 %» и цены рядом.
BLOCK_ABOVE = 12
BLOCK_BELOW = 6


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None   # NaN — не число


@dataclass
class Total:
    """Итог корзины в одной сети: сколько, чей это счёт и когда он получен.

    `sum` — итог к оплате. Остальные поля — только то, что отдала сеть: у «нашего
    расчёта» их нет вовсе, потому что скидку и доставку сети мы не знаем.
    """

    sum: float
    source: str = OURS
    at: str = ""
    goods: float | None = None        # товары до скидки, если сеть их показала
    discount: float | None = None     # скидка, положительным числом
    delivery: float | None = None     # доставка; 0 — «бесплатно»
    minimal: float | None = None      # минимальная сумма заказа по ответу сети
    count: int | None = None          # сколько позиций лежит в корзине сети

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def of(cls, raw) -> Total | None:
        """Итог из словаря — сохранённого у нас или присланного исполнителем.

        Негодное — None, а не ноль: ноль в итоге читается как «бесплатно» и выглядит
        ответом. Источник без явного «network» считается нашим расчётом: объявить
        итог посчитанным сетью можно только словом того, кто его прочитал.
        """
        if isinstance(raw, Total):
            return raw
        if not isinstance(raw, dict):
            return None
        amount = _number(raw.get("sum"))
        if amount is None or amount < 0 or amount > 10_000_000:
            return None

        def part(name: str) -> float | None:
            value = _number(raw.get(name))
            return round(abs(value), 2) if value is not None else None

        count = _number(raw.get("count"))
        return cls(sum=round(amount, 2),
                   source=NETWORK if raw.get("source") == NETWORK else OURS,
                   at=str(raw.get("at") or "")[:19] or _now(),
                   goods=part("goods"), discount=part("discount"),
                   delivery=part("delivery"), minimal=part("minimal"),
                   count=int(count) if count is not None and 0 <= count < 1000 else None)


# ---------- METRO: итог из ответа корзины ----------

def from_metro(basket, at: str | None = None) -> Total | None:
    """Итог корзины METRO из её же ответа. total_cost нет — итога нет.

    total_discount взят из формы ответа в спецификации METRO (её же повторяет
    tests/test_metro_cart.py); ноль там значит «скидки нет», и строки о скидке тогда
    не будет. Итог — всей корзины сети: если в ней лежало своё, оно в нём тоже.
    """
    total = _number(getattr(basket, "total", None))
    if total is None:
        return None
    raw = getattr(basket, "raw", None) or {}
    discount = _number(raw.get("total_discount")) if isinstance(raw, dict) else None
    minimal = _number(getattr(basket, "minimal", None))
    return Total(sum=round(total, 2), source=NETWORK, at=at or _now(),
                 discount=round(abs(discount), 2) if discount else None,
                 minimal=round(minimal, 2) if minimal else None,
                 count=len(getattr(basket, "lines", None) or []))


# ---------- страница корзины: разбор видимого текста ----------

# Сумма в рублях: «1 234,56 ₽», «1234.5 руб.», «−120 ₽». Без знака рубля число не
# берём: на странице корзины полно чисел, которые не деньги, — штуки, граммы, отзывы.
_MONEY = re.compile(
    r"(?P<sign>[−–—-]\s*)?(?P<int>\d{1,3}(?: \d{3})+|\d+)(?:[.,](?P<frac>\d{1,2}))?"
    r"\s*(?:₽|руб|р\.)", re.I)
# Строка, где кроме суммы нет ничего: только такую берём со следующей строки.
_MONEY_ONLY = re.compile(
    r"^[−–—-]?\s*(?:\d{1,3}(?: \d{3})+|\d+)(?:[.,]\d{1,2})?\s*(?:₽|руб\.?|р\.)$", re.I)
_FREE = re.compile(r"^бесплатн")

# Подписи блока итога — то, что видит человек. Сначала итог: «Итого к оплате»
# должно попасть сюда, а не в «к оплате» чего-нибудь ещё.
_LABELS = (
    ("sum", re.compile(r"^(?:итого(?:\s+к\s+оплате)?|к\s+оплате|всего\s+к\s+оплате|"
                       r"сумма\s+к\s+оплате|сумма\s+заказа|итоговая\s+сумма|общая\s+сумма)\b")),
    ("goods", re.compile(r"^(?:товары|товаров\s+на\s+сумму|стоимость\s+товаров|"
                         r"сумма\s+товаров)\b")),
    ("discount", re.compile(r"^(?:скидк[аи]|ваша\s+скидка|экономия|ваша\s+экономия|"
                            r"выгода|ваша\s+выгода|промокод|оплата\s+баллами|"
                            r"списано\s+баллов)\b")),
    ("delivery", re.compile(r"^(?:доставка|стоимость\s+доставки|сборка\s+и\s+доставка|"
                            r"доставка\s+и\s+сборка)\b")),
)
_COUNT = re.compile(r"\((\d{1,3})\)|(\d{1,3})\s*(?:шт|товар|позици)")
_EMPTY = re.compile(r"корзина пуста|в корзине пока пусто|в корзине пока ничего нет|"
                    r"в корзине ничего нет|корзина пока пуста")


def _low(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def money(text: str) -> float | None:
    """Первая сумма в рублях в строке. Нет — None."""
    found = _MONEY.search(text or "")
    if not found:
        return None
    value = float(found.group("int").replace(" ", "") + "." + (found.group("frac") or "0"))
    return -value if found.group("sign") else value


def _value(kind: str, tail: str, following: str | None) -> float | None:
    """Сумма у подписи: в той же строке, а нет — в следующей, если там только она."""
    if kind == "discount" and "%" in tail:
        # «Скидка 20 %» — плашка у позиции, а не скидка заказа.
        return None
    value = money(tail)
    if value is None and kind == "delivery" and _FREE.match(_low(tail).strip()):
        return 0.0
    if value is not None or following is None:
        return value
    nxt = following.strip()
    if _MONEY_ONLY.match(nxt):
        return money(nxt)
    if kind == "delivery" and _FREE.match(_low(nxt)):
        return 0.0
    return None


def parse(text: str, at: str | None = None) -> Total | None:
    """Итог со страницы корзины по видимому тексту. Не нашли «Итого» с суммой — None.

    Итог — последняя строка «Итого» с суммой: блок итога у корзин стоит после списка
    позиций, а прилипшая панель внизу повторяет ту же сумму. Скидка, доставка и
    товары берутся только из блока вокруг неё (BLOCK_ABOVE/BLOCK_BELOW) и только
    если подписаны: у позиций в списке бывают свои «Скидка» и цены рядом.
    """
    lines = [" ".join(raw.split()) for raw in (text or "").splitlines()]
    lines = [line for line in lines if line]
    seen: dict[str, list[tuple[int, float, str]]] = {}
    for index, line in enumerate(lines):
        low = _low(line)
        for kind, label in _LABELS:
            found = label.match(low)
            if not found:
                continue
            following = lines[index + 1] if index + 1 < len(lines) else None
            value = _value(kind, line[found.end():], following)
            if value is not None:
                seen.setdefault(kind, []).append((index, value, line))
            break
    if not seen.get("sum"):
        return None
    where, amount, _line = seen["sum"][-1]
    if amount < 0:
        return None

    def near(kind: str) -> tuple[float, str] | None:
        best = None
        for index, value, line in seen.get(kind, []):
            if not (where - BLOCK_ABOVE <= index <= where + BLOCK_BELOW) or index == where:
                continue
            if best is None or abs(index - where) < abs(best[0] - where):
                best = (index, value, line)
        return (best[1], best[2]) if best else None

    goods = near("goods")
    discount = near("discount")
    delivery = near("delivery")
    count = None
    if goods:
        hit = _COUNT.search(_low(goods[1]))
        if hit:
            count = int(hit.group(1) or hit.group(2))
    return Total(sum=round(amount, 2), source=NETWORK, at=at or _now(),
                 goods=round(abs(goods[0]), 2) if goods else None,
                 discount=round(abs(discount[0]), 2) if discount else None,
                 delivery=round(abs(delivery[0]), 2) if delivery else None,
                 count=count)


def empty_cart(text: str) -> bool:
    """Говорит ли страница, что корзина пуста."""
    return bool(_EMPTY.search(_low(text)))


# ---------- страница корзины: открыть и прочитать в окне сервера ----------

# Кнопка корзины в шапке — по доступному имени и по размеру узла. Правило размера то
# же, что у счётчика корзины (app/shopbrowser/cart._COUNT_JS): у кнопки шапки три
# потомка, у плитки товара с её «В корзину» — тридцать пять и больше. «В корзину»
# под образец и не подходит: имя кнопки должно НАЧИНАТЬСЯ с «Перейти в корзину» или
# «Корзина». Невидимую не берём — человек бы её тоже не нажал: на карточке Магнита
# кнопка шапки скрыта (store-apis.md), поэтому тогда идём на главную, где она видна.
_OPEN_JS = """([mark, maxKids, maxChars]) => {
    for (const e of document.querySelectorAll('[' + mark + ']')) e.removeAttribute(mark);
    const want = /^(перейти в корзину|корзина)/i;
    for (const e of document.querySelectorAll('a,button,[role=button]')) {
        if (e.querySelectorAll('*').length > maxKids) continue;
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const name = ((e.getAttribute('aria-label') || '') + ' ' +
                      (e.innerText || '')).replace(/\\s+/g, ' ').trim();
        if (!name || name.length > maxChars || !want.test(name)) continue;
        e.setAttribute(mark, '1');
        return name;
    }
    return null;
}"""

# Подписи блока итога, спрятанные в aria-label: их нет в видимом тексте, но это тоже
# доступные имена, и человек со скринридером слышит именно их.
_LABELS_JS = """() => {
    const want = /итого|к оплате|сумма|скидк|доставк|товар/i;
    const out = [];
    for (const e of document.querySelectorAll('[aria-label]')) {
        const t = (e.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
        if (t && t.length <= 80 && want.test(t)) out.push(t);
        if (out.length >= 40) break;
    }
    return out;
}"""


def _press_cart(page) -> bool:
    """Нажать кнопку корзины, если она видна. Ответ — нажали ли."""
    from app.shopbrowser import cart

    try:
        hit = page.evaluate(_OPEN_JS, [MARK, cart._COUNT_MAX_KIDS, cart._COUNT_MAX_CHARS])
    except Exception:  # noqa: BLE001 — страница не дала скрипт: кнопки не нашли
        return False
    if not hit:
        return False
    try:
        node = page.query_selector(f"[{MARK}]")
        if node is None:
            return False
        node.click(timeout=8000)
    except Exception:  # noqa: BLE001 — узел перерисовался под пальцем
        log.info("кнопка корзины не нажалась", exc_info=True)
        return False
    return True


def _page_text(page) -> str:
    try:
        text = page.inner_text("body")[:TEXT_LIMIT]
    except Exception:  # noqa: BLE001 — страница уезжает прямо сейчас
        text = ""
    try:
        extra = page.evaluate(_LABELS_JS)
    except Exception:  # noqa: BLE001
        extra = None
    if isinstance(extra, list):
        text += "\n" + "\n".join(str(item) for item in extra[:40])
    return text


def _wait_cart(page) -> str:
    """Текст корзины, когда в нём появился итог или слова о пустой корзине."""
    until = time.time() + WAIT_LIMIT
    while True:
        text = _page_text(page)
        if parse(text) is not None or empty_cart(text) or time.time() >= until:
            return text
        time.sleep(WAIT_STEP)


def read_page(page, chain: str) -> tuple[Total | None, str]:
    """Открыть корзину сети на этой странице и прочитать итог. Второе — почему нет."""
    from app.shopbrowser import driver, signals

    if not _press_cart(page):
        # На карточке товара кнопка шапки бывает скрыта — идём туда, где её видит
        # человек: на главную той же сети.
        try:
            page.goto(driver.HOME_URL[chain], wait_until="domcontentloaded", timeout=45000)
        except Exception:  # noqa: BLE001
            log.info("%s: главная не открылась для чтения итога", chain, exc_info=True)
            return None, "главная страница сети не открылась"
        driver._settle(page, 0.8)
        if not _press_cart(page):
            return None, "на странице сети не нашлось кнопки «Корзина»"
    driver._settle(page, 1.0)
    text = _wait_cart(page)
    if signals.guard_kind(text):
        return None, "вместо корзины сеть показала проверку или отказ"
    if signals.needs_store(text):
        return None, "сеть просит выбрать магазин, а корзину не показывает"
    total = parse(text)
    if total is not None:
        return total, ""
    if empty_cart(text):
        return None, "страница корзины показывает пустую корзину"
    return None, "на странице корзины не нашлось строки «Итого» с суммой"


def read(chain: str, phone: str) -> tuple[Total | None, str]:
    """Итог корзины сети из окна сервера, открытого передачей. Второе — почему нет."""
    if chain not in READS:
        return None, "итог этой сети со страницы приложение не читает"
    from app.shopbrowser import driver

    try:
        return driver.run(chain, phone, lambda page: read_page(page, chain),
                          timeout=READ_TIMEOUT)
    except driver.BrowserTimeout:
        return None, "браузер не ответил вовремя"
    except driver.BrowserUnavailable as exc:
        return None, f"окно магазина закрыто: {exc}"
    except Exception:  # noqa: BLE001 — чтение итога не повод ронять сборку
        log.warning("%s: итог корзины не прочитался", chain, exc_info=True)
        return None, "страница корзины не прочиталась"


__all__ = ["Total", "NETWORK", "OURS", "READS", "from_metro", "parse", "money",
           "empty_cart", "read_page", "read"]
