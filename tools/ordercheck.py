"""Живая проверка «закажем ли»: по адресу человека в каждой сети — точка, цены,
наличие товаров его корзины и дорога корзины до заказа.

ЗАЧЕМ. Тесты приложения в сеть не ходят (tests/conftest.py это запрещает), и
вопрос владельца «сможем ли заказать в каждой сети и подгрузятся ли остатки по
адресу» ими не проверить. Ответ зависит от того, ОТКУДА спрошено: российские сети
по-разному пускают дата-центр, домашний туннель и телефон человека. Поэтому
проверка живёт здесь и запускается на сервере, в контейнере приложения:

    ssh korzina docker exec korzina python -m tools.ordercheck --phone 79991234567
        --basket 12       корзина по номеру вместо последней
        --chain magnit    одна сеть (можно несколько раз)
        --no-links        не создавать ссылки на корзину Ленты и ВкусВилла
        --no-browser      не открывать окно магазина (Магнит)
        --json            ответ одним JSON — удобно переслать

ЧТО ОНА ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ.
  * Цены и остатки спрашивает теми же коннекторами и по тому же месту, что и
    кнопка «Обновить цены», — но снимки НЕ сохраняет: база цен человека не меняется.
  * Ссылку на корзину Ленты и ВкусВилла создаёт — как кнопка на «Результате». Ссылка
    ничего не кладёт, пока её не откроют; не нужна — --no-links.
  * Окно Магнита открывает под сохранённым входом человека и доходит до кнопки
    «в корзину» на первой карточке — НЕ НАЖИМАЯ её. В корзину не кладётся ничего,
    заказ не оформляется никогда.
  * Кэш ответов сетей (data/cache) пополняется — это не база человека.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

from app import location, repo, users

log = logging.getLogger("ordercheck")

# Сеанс окна магазина у проверки свой: окно человека в приложении она не трогает.
PROBE = "ordercheck"

# Сети, чьи коннекторы отдают настоящий остаток по точке. У остальных «в наличии»
# в снимке — умолчание, а не знание (ВкусВилл остатков не отдаёт, у Дикси цены за
# защитой, Пятёрочка и Самокат живут на прайсе и чеках).
STOCK_CHAINS = ("magnit", "lenta", "metro")
# Сети, где живой цены у приложения нет вовсе: прайс человека и его чеки.
RECEIPT_CHAINS = ("pyaterochka", "samokat")


@dataclass
class ChainReport:
    """Ответ по одной сети. Поля — то, что человек спросил бы, стоя у полки."""

    code: str
    name: str
    point: str = ""
    items: int = 0                      # позиций корзины
    mapped: int = 0                     # из них опознаны в этой сети
    live: int = 0                       # цена пришла живая
    receipts: int = 0                   # цена из чеков и прайса
    reference: int = 0                  # справочная цена из CSV
    no_price: int = 0                   # цены нет вовсе
    in_stock: int | None = None         # есть в точке (None — сеть остатков не отдаёт)
    out_of_stock: int | None = None
    route: str = ""                     # как корзина уходит в сеть
    order: str = ""                     # можно ли довести до заказа
    ok: bool | None = None              # True — дорога проверена, False — оборвана, None — руками
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


# ---------- точка ----------
def _point(code: str, address: str) -> str:
    """Какая точка сети считает цены по этому адресу. Словами."""
    if code in location.ADDRESS_STORES:
        place = location.for_store(code)
        if code == "magnit":
            if place is None or not place.store_id:
                return "рядом с адресом магазина Магнита нет — цен не будет"
            from app.shopbrowser import point

            where = f"магазин {place.store_id} ({place.shop_type or '—'}), " \
                    f"{'доставка' if place.delivery else 'самовывоз'}"
            trouble = point.trouble(code, place)
            return where + ("; " + trouble if trouble else "")
        if code == "lenta":
            from app.connectors.lenta import delivery_hub

            hub = delivery_hub(address)
            return f"хаб доставки {hub}" if hub else "хаб по адресу не подобран — Лента ответит по адресу"
        if code == "metro":
            from app.connectors.metro import nearest_store

            found = nearest_store(address)
            if not found:
                return "торгового центра METRO рядом нет — цен не будет"
            return f"центр {found['code']}, {found.get('address') or ''} ({found.get('distance_km')} км)"
    from app import places

    if code in places.BY_POINT:
        spots = places.points_for(address, code)
        label = (spots[0].label or spots[0].code) if spots else "не подобрана"
        return f"ближайшая точка: {label} (цены в расчёте — из чеков и прайса, не по ней)"
    return "цены одни на всю страну — точка не нужна"


# ---------- цены и наличие ----------
def _mappings(store, items: list[dict]) -> list[tuple[dict, dict]]:
    """(позиция корзины, подтверждённое сопоставление) — только опознанные в сети."""
    out = []
    for item in items:
        mapping = repo.confirmed_mapping(int(item["product_id"]), store.id)
        if mapping and mapping.get("sku"):
            out.append((item, mapping))
    return out


def _prices(report: ChainReport, code: str, mapped: list[tuple[dict, dict]]) -> None:
    """Спросить сеть о ценах и остатках опознанных позиций. Снимки НЕ сохраняются."""
    from app.connectors import get_connector

    if not mapped:
        return
    skus = [str(m["sku"]) for _i, m in mapped]
    connector = get_connector(code, location.for_store(code))
    snaps = {str(s.sku): s for s in (connector.get_prices(skus) or [])}
    stock_known = code in STOCK_CHAINS
    report.in_stock = 0 if stock_known else None
    report.out_of_stock = 0 if stock_known else None
    for sku in skus:
        snap = snaps.get(sku)
        if snap is None:
            report.no_price += 1
            continue
        if stock_known:
            # «Нет в этой точке» сеть говорит и нулевой ценой (Магнит, Лента), и это
            # ответ про наличие, а не отсутствие цены.
            if snap.in_stock:
                report.in_stock += 1
            else:
                report.out_of_stock += 1
        if not snap.price:
            report.no_price += 1
        elif getattr(snap, "source", None) == "fallback":
            report.reference += 1
        elif code in RECEIPT_CHAINS or str(sku).startswith("hist-"):
            report.receipts += 1
        else:
            report.live += 1


# ---------- дорога корзины ----------
def _lines(mapped: list[tuple[dict, dict]]) -> list:
    return [SimpleNamespace(product_id=int(i["product_id"]), qty=float(i.get("qty") or 1),
                            name=i.get("name") or "", unit=i.get("unit"))
            for i, _m in mapped]


def _route(report: ChainReport, code: str, mapped, *, links: bool, browser: bool) -> None:
    from app import handover

    kind = handover.KIND_BY_STORE.get(code, handover.LIST)
    lines = _lines(mapped)
    if not lines:
        report.route = "в этой сети не опознано ни одной позиции корзины"
        report.order, report.ok = "нечего заказывать", False
        return

    if kind == handover.LINK:
        report.route = "одна ссылка на корзину"
        if not links:
            report.order, report.ok = "ссылку не создавали (--no-links)", None
            return
        from app import service

        link = service.cart_link(code, lines)
        if link:
            report.order, report.ok = f"ссылка создана — открыть и подтвердить заказ: {link}", True
            if code == "lenta":
                report.problems.append("магазин в ссылке не передаётся — проверьте точку в шапке Ленты")
        else:
            report.order, report.ok = "ссылка не создалась — собирать по списку", False
        return

    if code == "magnit":
        report.route = "браузер сервера кладёт товары под вашим входом"
        ok, why = _browser_probe(code, mapped, browser=browser)
        report.order, report.ok = why, ok
        from app import cartplan

        blocked = cartplan.why_not(code)
        if blocked:
            report.problems.append("кнопка «Передать» на «Результате» скрыта: " + blocked)
        return

    if code == "metro":
        from app.connectors import metro_cart

        with_url = sum(1 for _i, m in mapped if m.get("url"))
        report.route = f"ссылки на карточки товаров ({with_url} из {len(mapped)})"
        if metro_cart.writable():
            report.order, report.ok = "корзину можно наполнить API METRO (токен задан)", None
        else:
            report.order, report.ok = ("кладёте руками по ссылкам: запись корзины METRO "
                                       "требует именного токена, его нет"), None
        return

    if kind == handover.ITEMS:
        with_url = sum(1 for _i, m in mapped if m.get("url"))
        report.route = f"ссылки на карточки товаров ({with_url} из {len(mapped)})"
        report.order, report.ok = "кладёте руками по ссылкам, заказ — в приложении сети", None
        return

    if kind == handover.SEARCH:
        report.route = "кнопки поиска по названию на сайте сети"
        report.order, report.ok = "собираете руками с телефона: сервер к витрине не пускают", None
        return

    report.route = "список позиций"
    report.order, report.ok = "собираете руками по списку", None


def _browser_probe(chain: str, mapped, *, browser: bool) -> tuple[bool | None, str]:
    """Дойдёт ли наряд до кнопки «в корзину». Кнопку НЕ нажимаем."""
    from app.shopbrowser import cart, driver, point, signals
    from app.shopbrowser import store as shopstore

    state = shopstore.load(chain)
    if not state:
        return False, "вход не сохранён — войдите в сеть на «Кабинетах»"
    hitch = point.trouble(chain)
    if hitch:
        return False, hitch
    if not browser:
        return None, "окно магазина не открывали (--no-browser); вход сохранён"
    if not driver.available():
        return False, "на этой машине нет браузера (пакет playwright)"
    first = next((m.get("url") for _i, m in mapped if m.get("url")), None)
    if not first:
        return False, "у опознанных товаров нет адреса карточки"

    carried = bool(point.cookies_for(chain))
    state = point.with_point(state, chain)
    try:
        driver.open_store(chain, PROBE, state=state)
        seen = driver.look(chain, PROBE)
        if seen.get("guarded"):
            return False, f"сеть не отдала страницу серверу ({seen.get('guard') or 'проверка'})"
        if seen.get("logged_in") is False:
            return False, "сохранённый вход устарел — войдите заново на «Кабинетах»"
        waiting = driver.run(chain, PROBE,
                             lambda page: signals.needs_store(page.inner_text("body")[:20000]),
                             timeout=40)
        if waiting and carried:
            waiting = not driver.run(chain, PROBE, cart._keep_point, timeout=40)
        if waiting:
            return False, "витрина ждёт выбора магазина — передача остановится до первой карточки"
        return driver.run(chain, PROBE, lambda page: _card(page, chain, first), timeout=150)
    except Exception as exc:  # noqa: BLE001 — ответ проверки, а не её падение
        return False, f"окно магазина не открылось: {type(exc).__name__}: {str(exc)[:160]}"
    finally:
        try:
            driver.close(chain, PROBE)
        except Exception:  # noqa: BLE001
            pass


def _card(page, chain: str, url: str) -> tuple[bool, str]:
    """Первая карточка: открылась ли и есть ли на ней кнопка товара. Без нажатия."""
    from app.shopbrowser import cart, driver, signals

    page.goto(url, wait_until="domcontentloaded", timeout=cart.LOAD_TIMEOUT)
    driver._settle(page, 0.6)
    text = page.inner_text("body")[:6000]
    if signals.guard_kind(text):
        return False, "карточка встретила защитой сети"
    if signals.no_showcase(text):
        return False, "витрина не открывает карточки в выбранном магазине"
    if cart._gone(page, text):
        return True, "карточка открылась, но товара сейчас нет — наряд пропустит его с причиной"
    node = cart._add_button(page, chain, again=False)
    if node is None:
        return False, "на карточке не нашлось кнопки «в корзину» — вёрстка сменилась?"
    return True, "наряд доходит до кнопки «в корзину» (не нажимали)"


# ---------- сборка ответа ----------
def check_chain(store, items: list[dict], address: str, *,
                links: bool = True, browser: bool = True) -> ChainReport:
    """Одна сеть целиком. Падение одной части не роняет остальные: это отчёт."""
    report = ChainReport(code=store.code, name=store.name, items=len(items))
    try:
        report.point = _point(store.code, address)
    except Exception as exc:  # noqa: BLE001
        report.point = f"не выяснилась: {type(exc).__name__}: {str(exc)[:120]}"
    mapped = _mappings(store, items)
    report.mapped = len(mapped)
    try:
        _prices(report, store.code, mapped)
    except Exception as exc:  # noqa: BLE001
        report.problems.append(f"цены не спросились: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        _route(report, store.code, mapped, links=links, browser=browser)
    except Exception as exc:  # noqa: BLE001
        report.order, report.ok = f"дорога не проверилась: {type(exc).__name__}: {str(exc)[:120]}", False
    return report


def run(phone: str, *, basket: int | None = None, chains: list[str] | None = None,
        links: bool = True, browser: bool = True) -> dict:
    """Проверка целиком: {address, basket, chains: [ChainReport.as_dict()]}."""
    users.open_workspace(phone)
    try:
        address = location.address()
        if not address:
            return {"error": "адрес не указан — сохраните его в шапке приложения"}
        baskets = repo.list_baskets()
        chosen = next((b for b in baskets if basket is None or int(b["id"]) == basket), None)
        if chosen is None:
            return {"error": "корзины нет" if basket is None else f"корзины #{basket} нет"}
        items = repo.basket_items(int(chosen["id"]))
        if not items:
            return {"error": f"корзина «{chosen.get('name')}» пуста"}
        reports = []
        for store in repo.list_stores():
            if chains and store.code not in chains:
                continue
            reports.append(check_chain(store, items, address,
                                       links=links, browser=browser).as_dict())
        return {"address": address, "basket": chosen.get("name"), "items": len(items),
                "chains": reports}
    finally:
        users.deactivate()


def as_text(answer: dict) -> str:
    """Отчёт для терминала: по блоку на сеть и сводка в конце."""
    if answer.get("error"):
        return "Проверка не началась: " + answer["error"]
    out = [f"Адрес: {answer['address']}",
           f"Корзина «{answer['basket']}», позиций: {answer['items']}", ""]
    summary = []
    for r in answer["chains"]:
        mark = {True: "ДА", False: "НЕТ", None: "РУКАМИ"}[r["ok"]]
        out.append(f"{r['name'].upper()} — заказ: {mark}")
        out.append(f"  точка:    {r['point']}")
        priced = (f"живых {r['live']}, из чеков {r['receipts']}, справочных {r['reference']}, "
                  f"без цены {r['no_price']}")
        out.append(f"  цены:     опознано {r['mapped']} из {r['items']}; {priced}")
        if r["in_stock"] is None:
            out.append("  наличие:  сеть остатков не отдаёт")
        else:
            out.append(f"  наличие:  есть {r['in_stock']}, нет {r['out_of_stock']}")
        out.append(f"  корзина:  {r['route']}")
        out.append(f"  заказ:    {r['order']}")
        for problem in r["problems"]:
            out.append(f"  внимание: {problem}")
        out.append("")
        stock = "—" if r["in_stock"] is None else f"{r['in_stock']}/{r['in_stock'] + r['out_of_stock']}"
        summary.append(f"  {r['name'][:12]:<12} цены {r['live'] + r['receipts']:>2}/{r['mapped']:<2} "
                       f"наличие {stock:<6} заказ: {mark}")
    out.append("Сводка:")
    out.extend(summary)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Живая проверка: закажем ли в каждой сети")
    parser.add_argument("--phone", required=True, help="рабочее место (номер телефона)")
    parser.add_argument("--basket", type=int, help="номер корзины вместо последней")
    parser.add_argument("--chain", action="append", help="только эта сеть (можно несколько раз)")
    parser.add_argument("--no-links", action="store_true", help="не создавать ссылки на корзину")
    parser.add_argument("--no-browser", action="store_true", help="не открывать окно магазина")
    parser.add_argument("--json", action="store_true", help="ответ одним JSON")
    args = parser.parse_args(argv)

    phone = users.normalize_phone(args.phone)
    if not phone or not users.exists(phone):
        print(f"рабочего места {args.phone} нет")
        return 2
    answer = run(phone, basket=args.basket, chains=args.chain,
                 links=not args.no_links, browser=not args.no_browser)
    print(json.dumps(answer, ensure_ascii=False, indent=2) if args.json else as_text(answer))
    return 2 if answer.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
