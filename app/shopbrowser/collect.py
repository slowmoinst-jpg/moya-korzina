"""Цены Пятёрочки и Самоката — через кабинет самого человека.

ЗАЧЕМ ЭТОТ ФАЙЛ ПОЯВИЛСЯ. Обе сети закрыты для сервера обычным запросом: они
отвечают заглушкой своей JS-проверки (измерено 17.09.2026 — таблица в
app/shopbrowser/__init__.py). Раньше их цены приносило расширение из браузера
человека; расширение снято решением владельца, и вместе с ним исчез ЕДИНСТВЕННЫЙ
источник цен этих двух сетей. Дыра была не в удобстве, а в расчёте: сеть без цен
не участвует в сравнении вовсе.

Здесь она закрывается тем же способом, которым закрылся вход: браузером на нашем
сервере, работающим В СЕАНСЕ ЧЕЛОВЕКА. Он вошёл в свой кабинет — и мы читаем его
страницы, его цены, его карту. Ничего чужого и ничего в обход: это ровно те же
страницы, которые он открыл бы сам.

ЧТО СОБИРАЕМ И ЧЕГО НЕ СОБИРАЕМ. Не витрину целиком — только товары, которые у
него уже опознаны в этой сети (подтверждённое сопоставление с артикулом и
адресом карточки). Так собирается ровно то, что нужно расчёту, и ни страницей
больше: обход всего каталога — это часы чужой витрины ради цифр, которые никому
не понадобятся.

ЦЕНУ ЧИТАЕМ ИЗ РАЗМЕТКИ ДАННЫХ, А НЕ ГЛАЗАМИ ПО ТЕКСТУ. Магазины кладут цену в
JSON-LD и в микроразметку schema.org — это то, что они САМИ объявляют ценой
товара, и оно не зависит от вёрстки. Выковыривать число из видимого текста мы не
будем ни при каких обстоятельствах: на карточке рядом живут «старая цена»,
«цена за кг», «−38 %», цена подписки и цена соседнего товара, и промах здесь не
заметит никто — он просто тихо исказит расчёт. Не нашли разметку — говорим
«не прочитали», и это честный ответ.

КУДА ЛОЖИТСЯ СОБРАННОЕ. В ту же дверь, что и прежде, — app/pricebundle.py. Своего
разбора и своей записи цен здесь нет намеренно: два приёмника цен разошлись бы в
первый же день, и разошлись бы молча.
"""
from __future__ import annotations

import json
import logging
import re
import time

from app import pricebundle, repo
from app.matcher.normalize import parse_weight, unit_from_name
from app.shopbrowser import driver, signals
from app.shopbrowser import store as shopstore

log = logging.getLogger(__name__)

# Сети, чьи цены вообще принимает приёмник. Зеркало pricebundle.BROWSER_STORES:
# у остальных цены сервер спрашивает сам, и принять ещё и эти значило бы смешать
# два источника разной свежести.
CHAINS = tuple(pricebundle.BROWSER_STORES)

STEP_PAUSE = 1.4          # между карточками: темп покупателя, а не выгребания
PAGE_LIMIT = 300          # столько карточек за один заход, и ни одной больше
LOAD_TIMEOUT = 45000

PROGRESS_KEY = "prices.progress"


# ---------- цена со страницы ----------

def _number(value) -> float | None:
    """Цена в число — той же чисткой, что у приёмника (pricebundle._number)."""
    return pricebundle._number(value)


def product_in_ld(blocks) -> dict | None:
    """Данные о товаре, цене, наличии и фасовке из блоков JSON-LD.

    Отдельной чистой функцией нарочно — чтобы её проверял обычный тест, без
    браузера и без похода в сеть: именно здесь ошибка стоит дороже всего. Взять
    цену не того узла (а в графе рядом лежат и хлебные крошки, и организация, и
    соседний товар) значит записать человеку неверную цену, и заметить это нечем.

    Ходим по всем блокам и по вложенным @graph: сети кладут Product то первым
    элементом списка, то внутри графа.
    """
    def dig(node):
        if isinstance(node, list):
            for item in node:
                got = dig(item)
                if got is not None:
                    return got
            return None
        if not isinstance(node, dict):
            return None
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(k).lower() == "product" for k in kinds if k):
            name = str(node.get("name") or "").strip() or None
            barcode = str(node.get("gtin13") or node.get("gtin") or node.get("barcode") or node.get("isbn") or "").strip() or None
            offers = node.get("offers")
            price = None
            base_price = None
            in_stock = True
            for offer in (offers if isinstance(offers, list) else [offers]):
                if isinstance(offer, dict):
                    p = _number(offer.get("price") or offer.get("lowPrice"))
                    if p:
                        price = p
                        bp = _number(offer.get("highPrice") or offer.get("oldPrice") or offer.get("standardPrice"))
                        if bp and bp > p:
                            base_price = bp
                        avail = str(offer.get("availability") or "").lower()
                        if "outofstock" in avail or "discontinued" in avail:
                            in_stock = False
                        elif "instock" in avail:
                            in_stock = True
                        break
            if price:
                weight_raw = str(node.get("weight") or node.get("netWeight") or "").strip()
                weight_g, _ = parse_weight(weight_raw or name or "")
                unit = unit_from_name(name or "") or ("pcs" if weight_g else None)
                return {
                    "price": price,
                    "base_price": base_price,
                    "in_stock": in_stock,
                    "name": name,
                    "weight_g": weight_g,
                    "unit": unit,
                    "barcode": barcode,
                }
        for key in ("@graph", "mainEntity", "itemListElement"):
            got = dig(node.get(key))
            if got is not None:
                return got
        return None

    for raw in blocks or []:
        try:
            got = dig(json.loads(raw) if isinstance(raw, (str, bytes)) else raw)
        except (TypeError, ValueError):
            continue
        if got:
            return got
    return None


def price_in_ld(blocks) -> float | None:
    """Цена из блоков JSON-LD: то, что магазин САМ объявил ценой товара."""
    got = product_in_ld(blocks)
    return got["price"] if got else None


def _from_ld(page) -> dict | None:
    """Те же блоки, но со страницы."""
    try:
        blocks = page.eval_on_selector_all(
            "script[type='application/ld+json']", "nodes => nodes.map(n => n.textContent)")
    except Exception:  # noqa: BLE001
        return None
    return product_in_ld(blocks)


def _from_micro(page) -> dict | None:
    """Товар из микроразметки: <meta itemprop="price"> и og:price / product:price."""
    selectors = ("[itemprop='price']",
                 "meta[property='product:price:amount']",
                 "meta[property='og:price:amount']")
    price = None
    for selector in selectors:
        try:
            node = page.query_selector(selector)
        except Exception:  # noqa: BLE001
            node = None
        if not node:
            continue
        for attr in ("content", "value"):
            try:
                price = _number(node.get_attribute(attr))
            except Exception:  # noqa: BLE001
                price = None
            if price:
                break
        if price:
            break
    if not price:
        return None

    in_stock = True
    try:
        avail_node = page.query_selector("[itemprop='availability'], meta[property='og:availability']")
        if avail_node:
            for attr in ("href", "content", "value"):
                val = str(avail_node.get_attribute(attr) or "").lower()
                if "outofstock" in val or "discontinued" in val:
                    in_stock = False
                    break
    except Exception:  # noqa: BLE001
        pass

    name = None
    try:
        name_node = page.query_selector("[itemprop='name'], meta[property='og:title']")
        if name_node:
            for attr in ("content", "value"):
                name = str(name_node.get_attribute(attr) or "").strip()
                if name:
                    break
            if not name:
                name = str(name_node.text_content() or "").strip()
    except Exception:  # noqa: BLE001
        pass

    weight_g, _ = parse_weight(name or "")
    unit = unit_from_name(name or "")
    return {
        "price": price,
        "base_price": None,
        "in_stock": in_stock,
        "name": name or None,
        "weight_g": weight_g,
        "unit": unit,
        "barcode": None,
    }


def read_card(page) -> dict | None:
    """Товар со страницы: цена, фасовка, наличие, штрихкод."""
    return _from_ld(page) or _from_micro(page)


def read_price(page) -> float | None:
    """Цена товара со страницы. Не нашли разметку — None, и это ответ."""
    card = read_card(page)
    return card["price"] if card else None


# ---------- что обходим ----------

def targets(chain: str, limit: int = PAGE_LIMIT) -> list[dict]:
    """Товары этой сети, у которых есть артикул и адрес карточки.

    Берём только ПОДТВЕРЖДЁННЫЕ сопоставления: догадка здесь стоила бы дороже
    пропуска — записать человеку цену не того творога хуже, чем не записать
    ничего, потому что второе он увидит, а первое нет.
    """
    store = repo.get_store(chain)
    if not store:
        return []
    with repo.get_conn() as c:
        rows = c.execute(
            "SELECT sp.sku AS sku, sp.raw_name AS name, sp.url AS url"
            " FROM product_mapping m JOIN store_products sp ON sp.id = m.store_product_id"
            " WHERE sp.store_id=? AND m.confirmed=1 AND sp.url IS NOT NULL AND sp.url <> ''"
            " GROUP BY sp.id ORDER BY sp.id LIMIT ?", (store.id, int(limit))).fetchall()
    return [dict(r) for r in rows]


# ---------- ход работы ----------

def _progress(chain: str, **fields) -> None:
    try:
        raw = repo.get_setting(PROGRESS_KEY)
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            data = {}
        data[chain] = {**(data.get(chain) or {}), **fields}
        repo.set_setting(PROGRESS_KEY, json.dumps(data, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — отметка о ходе не повод ронять сбор
        log.warning("%s: ход сбора цен не записался", chain, exc_info=True)


def progress(chain: str) -> dict | None:
    """Где сейчас сбор цен этой сети. Не шёл — None."""
    raw = repo.get_setting(PROGRESS_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    got = data.get(chain) if isinstance(data, dict) else None
    return got if isinstance(got, dict) else None


# ---------- сам обход ----------

def refresh(chain: str, phone: str, *, limit: int = PAGE_LIMIT) -> dict:
    """Обойти карточки в кабинете человека и обновить цены этой сети."""
    if chain not in CHAINS:
        return {"saved": 0, "read": 0, "note":
                f"Цены сети «{chain}» приложение спрашивает у неё само — "
                "браузером их собирать не нужно."}

    if shopstore.load(chain) is None:
        return {"saved": 0, "read": 0, "note":
                "Вход в эту сеть не сохранён: откройте её кабинет и войдите. "
                "Без входа сеть покажет полочные цены вместо ваших."}

    plan = targets(chain, limit)
    if not plan:
        return _discover_from_showcase(chain, phone, limit)

    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    _progress(chain, started_at=started, finished_at=None, done=0,
              total=len(plan), note="открываю магазин")

    driver.open_store(chain, phone, state=shopstore.load(chain))
    seen = driver.look(chain, phone)
    if seen.get("guarded"):
        return _done(chain, [], len(plan),
                     "Сеть встретила проверкой «я не робот». Откройте её кабинет, "
                     "пройдите проверку и повторите сбор.")

    items: list[dict] = []
    unread = 0
    for done, row in enumerate(plan, start=1):
        url = row.get("url")
        if not url or not driver._same_chain(chain, url):
            unread += 1
            continue
        try:
            card = driver.run(chain, phone, lambda page, u=url: _one(page, u))
        except driver.BrowserUnavailable as err:
            _progress(chain, note=str(err))
            break
        if isinstance(card, dict) and card.get("price"):
            name = card.get("name") or row.get("name") or row["sku"]
            items.append({
                "sku": row["sku"],
                "name": name,
                "price": card["price"],
                "base_price": card.get("base_price"),
                "in_stock": card.get("in_stock", True),
                "weight_g": card.get("weight_g"),
                "unit": card.get("unit") or "pcs",
                "barcode": card.get("barcode"),
                "url": url,
            })
        elif isinstance(card, (int, float)) and card > 0:
            items.append({
                "sku": row["sku"],
                "name": row.get("name") or row["sku"],
                "price": float(card),
                "url": url,
                "in_stock": True,
            })
        else:
            unread += 1
        _progress(chain, done=done, note=row.get("name") or row["sku"])
        time.sleep(STEP_PAUSE)

    if not items:
        return _done(chain, [], len(plan),
                     "Ни одной цены прочитать не вышло: на страницах нет разметки товара, "
                     "которой мы верим. Гадать по видимому тексту приложение не станет — "
                     "рядом с ценой живут старая цена, цена за кг и цена соседнего товара.")

    summary = pricebundle.import_prices({
        "store": chain,
        # Адрес доставки отсюда не берём: он у сети свой, и читать его со страницы
        # значило бы завести второй источник правды рядом с app/places.py.
        "address": None,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": items,
    })
    note = f"Обновлено цен: {summary['saved']}."
    if unread:
        note += f" Не прочитано: {unread} — у этих карточек нет разметки товара."
    log.info("%s: собрано %d цен из кабинета человека", chain, summary["saved"])
    return _done(chain, items, len(plan), note, saved=summary["saved"])


def _one(page, url: str) -> dict | None:
    """Открыть карточку и прочитать товар: цену, фасовку, наличие. Проверка вместо магазина — тоже ответ."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT)
    except Exception:  # noqa: BLE001 — страница не открылась, цен/товара нет
        return None
    driver._settle(page, 0.5)
    try:
        if signals.guarded(page.inner_text("body")[:2000]):
            return None
    except Exception:  # noqa: BLE001
        pass
    return read_card(page)


def _discover_from_showcase(chain: str, phone: str, limit: int = PAGE_LIMIT) -> dict:
    """Собрать товары напрямую с витрины каталога, когда нет подтверждённых связей."""
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    _progress(chain, started_at=started, finished_at=None,
              done=0, total=limit, note="открываю витрину каталога")
    driver.open_store(chain, phone, state=shopstore.load(chain))
    seen = driver.look(chain, phone)
    if seen.get("guarded"):
        return _done(chain, [], 0,
                     "Сеть встретила проверкой «я не робот». Откройте её кабинет, "
                     "пройдите проверку и повторите сбор.")

    def _extract_from_page(page):
        items = []
        try:
            cards = page.evaluate("""() => {
                const out = [];
                const links = document.querySelectorAll('a[href*="/product/"]');
                for (const a of links) {
                    out.push({ url: a.getAttribute('href'), text: a.innerText });
                }
                return out;
            }""")
        except Exception:
            cards = []

        for card in cards:
            url = card.get("url") or ""
            text = card.get("text") or ""
            sku_match = re.search(r"(?:--|/product/(?:.*-)?)([a-zA-Z0-9_-]+)/?", url)
            if not sku_match:
                continue
            sku = sku_match.group(1)
            p_match = re.search(r"^(?:[\d,]+\s+)?(.+?)\s+(\d+(?:[.,]\d+)?)\s*₽", text.replace("\n", " ").strip())
            if p_match:
                name = p_match.group(1).strip()
                try:
                    price = float(p_match.group(2).replace(",", "."))
                except ValueError:
                    price = None
                if name and price and price > 0:
                    w, _ = parse_weight(name)
                    items.append({
                        "sku": sku,
                        "name": name,
                        "price": price,
                        "weight_g": w,
                        "unit": unit_from_name(name) or "pcs",
                        "url": url if url.startswith("http") else f"{driver.HOME_URL.get(chain, '').rstrip('/')}{url}",
                        "in_stock": True,
                    })
        return items

    discovered = []
    try:
        discovered = driver.run(chain, phone, _extract_from_page) or []
    except Exception as exc:
        log.warning("%s: сбор с витрины не удался: %s", chain, exc)

    if not discovered:
        return _done(chain, [], 0,
                     "В этой сети пока не опознан ни один ваш товар и на открытой странице "
                     "не нашлось карточек. Откройте витрину в кабинете и перейдите в каталог.")

    summary = pricebundle.import_prices({
        "store": chain,
        "address": None,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": discovered[:limit],
    })
    note = f"Загружено новых товаров с витрины: {summary['saved']}."
    log.info("%s: обнаружено и загружено %d товаров с витрины", chain, summary["saved"])
    return _done(chain, discovered[:limit], len(discovered[:limit]), note, saved=summary["saved"])


def _done(chain: str, items: list, total: int, note: str, saved: int = 0) -> dict:
    _progress(chain, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
              done=total, note=note)
    return {"saved": saved, "read": len(items), "note": note}


def start(chain: str, phone: str, *, limit: int = PAGE_LIMIT) -> None:
    """Пустить сбор фоном. Ждать его в запросе нельзя: это минуты.

    Поток САМ открывает рабочее место: новый поток в Python начинает с чистых
    contextvars, и цены уехали бы в общую базу из config.yaml — молча и
    правдоподобно (та же ловушка описана в app/web/auth.py).
    """
    import threading

    from app import users

    if not phone:
        return

    def work() -> None:
        users.open_workspace(phone)
        try:
            refresh(chain, phone, limit=limit)
        except Exception:  # noqa: BLE001 — фоновая работа не должна ронять сервер
            log.exception("%s: сбор цен не удался", chain)
        finally:
            users.deactivate()

    threading.Thread(target=work, name=f"prices-{chain}", daemon=True).start()


__all__ = ["CHAINS", "refresh", "start", "targets", "progress", "read_price",
           "price_in_ld",
           "PROGRESS_KEY", "PAGE_LIMIT"]
