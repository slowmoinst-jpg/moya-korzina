"""Артикулы Впрока поиском самой витрины: настоящие названия и фасовки вместо адресов карты сайта.

ЗАЧЕМ (замер 16.11.2026). У владельца было 0 подтверждённых связей с Впроком, и в любой корзине
сеть отвечала «класть нечего». Каталог Впрока собирается из карты сайта, а она неполна: 27 551 адрес
(sku0 + sku1) — хвост ассортимента, основного (Parmalat 306635, Papia 331445, Ясно солнышко 314341)
в ней нет, а из адреса видны только латинский слаг и вес. Страницы витрины серверу закрыты проверкой
«я не робот» (503), но её API открыт с анонимным сеансом: главная отдаёт куки, поиск
`POST /web/api/v1/catalog/search?text=…` с заголовком X-XSRF-TOKEN отдаёт карточки с настоящим
названием («Молоко Parmalat Natura Premium ультрапастеризованное 1.8% 1л»), маркой, артикулом и
наличием. Темп — RPS (0,5 в секунду), общий с остальными процессами (Pace): при 1 в секунду
(`catalog.rate_limit_rps`) витрина в первом большом проходе отвечала 429 на каждый 14-й запрос
(49 из ~700, 16.11.2026); повтор с паузой справлялся, но предел поиска у неё ниже общего.

КОГДА ИСКАТЬ. Только вне сборки корзины (поиск по названию в ней держал сеть 60–137 с): после
прохода единой базы (link.link_products), по CLI и за один раз не дольше бюджета. Товар без
подтверждённой настоящей связи с Впроком ищется раз в TTL_DAYS; нашёлся — связь пишется, нет —
повторный поиск через неделю.

ЧТО ПИШЕТСЯ. Тот же судья, что у строгой сверки по каталогам сетей (x5.fill_chains): марка, вид,
все значимые слова в обе стороны, фасовка в пределах 3 %, числа, штуки, газ, свои названия товара
не спорят, охраны единой базы (link._agrees) тоже. Связь — только если подходит ровно одна
доступная карточка. Лишнее слово карточки (название линейки «Natura Premium») и перевод марки
(«Bali Flower» против «Балийский цветок») судью не проходят: лучше пропустить, чем положить не то.
Метка связи — «vprok» (METHOD): x5.fill_chains её не снимает (он снимает только свои STRICT-связи),
единая база не перезаписывает (она уважает не-MASTER), откат — DELETE … WHERE method='vprok'.
В общий каталог (catalog.db) найденное не пишется: ночной обход заменил бы настоящие названия
слагами карты сайта и погасил строки, которых в ней нет (store.retire_unseen). Цена карточки тоже
не пишется: она по умолчанию для региона анонимного сеанса, а не для адреса человека.

shortcut: мёртвые карточки не снимаются, когда карточка уйдёт с витрины, наряд телефону скажет
«нет в наличии»; перепроверять свои связи — когда таких наберётся.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import unquote

import requests

from app import repo
from app.catalog import link as L
from app.catalog import match as M
from app.catalog import store, x5
from app.catalog.crawlers import Pace, http_get, http_post
from app.catalog.model import CrawlBlocked
from app.models import Product

log = logging.getLogger(__name__)

CHAIN = "vprok"
METHOD = "vprok"               # product_mapping.method
HOST = "https://www.vprok.ru"
SEARCH = HOST + "/web/api/v1/catalog/search"
RPS = 0.5                      # запросов в секунду: при 1.0 витрина отвечала 429 на каждый 14-й
PAGE = 30                      # карточек на страницу: столько просит сама витрина
PAGES = 2                      # во втором, широком запросе — не глубже двух страниц
TTL_DAYS = 7                   # товар без находки ищется заново не раньше
BUDGET_S = 240                 # сколько проход единой базы отдаёт поиску (CLI — без предела)
SETTING = "vprok.searched"     # {номер товара: дата последнего поиска} в базе человека
LIVE = {"available"}           # activityStatus карточки, которую можно положить в корзину
HEADERS = {"Accept-Language": "ru-RU,ru;q=0.9"}

_VOCAB: dict[int, set[str]] = {}       # слова марок единой базы по поколению каталога


@dataclass(frozen=True)
class Card:
    sku: str
    name: str
    url: str | None
    brand: str | None
    status: str | None


def cards_of(data: dict) -> list[Card]:
    """Карточки из ответа поиска: артикул (productId), настоящее название, марка, адрес, наличие."""
    out = []
    for p in data.get("products") or []:
        if not p.get("productId") or not p.get("name"):
            continue
        url = p.get("url") or ""
        out.append(Card(str(p["productId"]), str(p["name"]),
                        (HOST + url) if url.startswith("/") else (url or None),
                        (p.get("brand") or {}).get("name"), p.get("activityStatus")))
    return out


class Shop:
    """Анонимный сеанс витрины: куки главной и XSRF-заголовок. Темп и повторы — общие (Pace, http_post)."""

    def __init__(self) -> None:
        self.pace = Pace(rps=RPS, chain=CHAIN)
        self.jar = None
        self.xsrf = ""

    def open(self) -> None:
        r = http_get(HOST + "/", self.pace, timeout=30, accept="text/html,*/*;q=0.8", headers=HEADERS)
        jar = requests.cookies.RequestsCookieJar()
        for hop in (*r.history, r):
            jar.update(hop.cookies)
        self.jar, self.xsrf = jar, unquote(jar.get("XSRF-TOKEN") or "")
        if not self.xsrf:
            raise CrawlBlocked(f"{HOST}: главная не дала XSRF-TOKEN")

    def search(self, text: str, page: int = 1) -> dict:
        for again in (True, False):
            if self.jar is None:
                self.open()
            try:
                return http_post(SEARCH, self.pace, timeout=30, params={"text": text, "limit": PAGE, "page": page},
                                 json={}, cookies=self.jar, accept="application/json, text/plain, */*",
                                 headers={**HEADERS, "X-XSRF-TOKEN": self.xsrf}).json()
            except CrawlBlocked as exc:
                if not (again and "401" in str(exc)):
                    raise
                self.jar = None                   # сеанс протух: один раз заводим новый


def _tokens(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text)))


def queries(p: Product, pbrand: set[str]) -> list[str]:
    """Что спросить у поиска: слова названия; не нашлось — марка и первое слово названия.

    Сокращения чека («овсян», «солн») поиск не раскрывает, поэтому берётся человеческое название.
    """
    words = _tokens(repo.human_titles({p.id: p.name}).get(p.id) or p.name)[:6]
    # «Lay» в названии и «lays» в словаре марок — одна марка (так же её находит _brand_items).
    brand = [w for w in words if any(x5.brand_eq(b, f) or x5._near(f, b) or x5._near(b, f) for b in pbrand
                                     for f in [x5.J(M.fold(w.lower()))])]
    wide = brand + [w for w in words if w not in brand][:1]
    # Без слов марки широкий запрос — одно слово вида («Чипсы»): тысячи карточек, искать в них нечего.
    return [" ".join(words)] + ([" ".join(wide)] if brand and wide != words else [])


def fits(p: Product, evidence: tuple, person: list, card: Card) -> bool:
    """Карточка — тот же товар: условия строгой сверки по каталогу сети (x5.fill_chains), слово в слово."""
    own, pbrand, baby = evidence
    c = x5.R(card.name, None, None, card.brand, CHAIN, card.sku)
    if (baby != (c.baby or bool(pbrand & x5.BABY_BRANDS)) or any(w.startswith(x5.ASSORTED) for w in c.words)
            or not all(x5._same(o, c, pbrand) for o in own)
            or not any(x5.judge(e, c, pbrand) is None for e in own)):
        return False
    row = {"id": 0, "chain": CHAIN, "sku": card.sku, "name": card.name, "brand": card.brand,
           "weight_g": c.weight, "unit": None}
    return bool(person) and L._agrees(person, L._prep(row))


def pick(p: Product, evidence: tuple, shop: Shop) -> tuple[Card | None, str]:
    """Единственная доступная карточка поиска, которая тот же товар, и итог: found / none / ambiguous / out."""
    person = L._rows_of_person(p, repo.raw_names(p.id))
    seen: set[str] = set()
    live: dict[str, Card] = {}
    gone = False
    for n, text in enumerate(queries(p, evidence[1])):
        for page in range(1, (1 if n == 0 else PAGES) + 1):
            data = shop.search(text, page)
            for card in cards_of(data):
                if card.sku in seen or not fits(p, evidence, person, card):
                    continue
                seen.add(card.sku)
                if card.status in LIVE:
                    live[card.sku] = card
                else:
                    gone = True
            if live or page >= int(data.get("pages") or 1):
                break
        if live:
            break
    if len(live) == 1:
        return next(iter(live.values())), "found"
    return None, "ambiguous" if live else ("out" if gone else "none")


def _vocab(conn) -> set[str]:
    gen = L._generation(conn)
    if gen not in _VOCAB:
        _VOCAB.clear()
        _VOCAB[gen] = x5.build_index(conn).brand_vocab
    return _VOCAB[gen]


def _wanted(products: list[Product], shop_id: int, force: bool) -> list[Product]:
    """Кого искать: покупали или кладут в корзину, настоящей связи с Впроком нет, сеть не убрана человеком.

    Корзины — первыми: их владелец ждёт. Недавно искавшиеся (TTL_DAYS) пропускаются.
    """
    with repo.get_conn() as c:
        baskets = {r[0] for r in c.execute("SELECT DISTINCT product_id FROM basket_items")}
        food = baskets | {r[0] for r in c.execute(
            "SELECT DISTINCT product_id FROM purchase_history WHERE store_id IS NOT NULL")}
    try:
        tried = json.loads(repo.get_setting(SETTING) or "{}")
    except ValueError:
        tried = {}
    fresh = (date.today() - timedelta(days=TTL_DAYS)).isoformat()
    todo = [p for p in products if p.id in food
            and (force or tried.get(str(p.id), "") < fresh)
            and not repo.confirmed_mapping(p.id, shop_id, real_only=True)
            and not repo.chain_rejected(p.id, shop_id)]
    return sorted(todo, key=lambda p: p.id not in baskets)


def fill(products: list[Product] | None = None, *, write: bool = True, force: bool = False,
         budget_s: float | None = BUDGET_S, limit: int | None = None, shop: Shop | None = None) -> dict:
    """Найти товарам человека артикулы во Впроке поиском витрины. Отчёт: счётчики и picks."""
    out: dict = {"tried": 0, "found": 0, "ambiguous": 0, "out": 0, "none": 0, "skipped": 0, "picks": [],
                 "blocked": None}
    target = repo.get_store(CHAIN)
    if target is None:
        return out
    todo = _wanted(products if products is not None else repo.list_products(), target.id, force)
    if not todo:
        return out
    with store.connect() as conn:
        vocab = _vocab(conn)
    shop = shop or Shop()
    started, marks, errors = time.monotonic(), {}, 0
    try:
        for p in todo[:limit]:
            if budget_s is not None and time.monotonic() - started > budget_s:
                break
            evidence = x5._evidence(p, vocab)
            own, pbrand, _ = evidence
            # Без марки судья откажет любой карточке, без фасовки — тоже: поиск был бы впустую.
            if not pbrand or not any(e.weight or e.packs > 1 or e.unit == "kg" for e in own):
                out["skipped"] += 1
                marks[str(p.id)] = date.today().isoformat()     # не пересчитывать каждый проход
                continue
            try:
                card, verdict = pick(p, evidence, shop)
            except (requests.RequestException, ValueError) as exc:
                errors += 1
                log.warning("Впрок: товар %s не искался: %s", p.id, exc)
                if errors >= 3:
                    out["blocked"] = f"три сбоя подряд: {exc}"
                    break
                continue
            errors = 0
            out["tried"] += 1
            out[verdict] += 1
            marks[str(p.id)] = date.today().isoformat()
            if card is None:
                continue
            out["picks"].append((p.id, p.name, card.sku, card.name))
            if write:
                c = x5.R(card.name, None, None, card.brand, CHAIN, card.sku)
                sp = repo.upsert_store_product(target.id, card.sku, card.name, weight_g=c.weight,
                                               url=card.url, keep_known=True)
                repo.confirm_mapping(p.id, sp, confirmed=True, method=METHOD)
                repo.drop_base_candidates(p.id, target.id, keep=sp)
    except CrawlBlocked as exc:
        out["blocked"] = str(exc)
        log.warning("Впрок: поиск остановлен: %s", exc)
    if write and marks:
        try:
            tried = json.loads(repo.get_setting(SETTING) or "{}")
        except ValueError:
            tried = {}
        repo.set_setting(SETTING, json.dumps({**tried, **marks}))
    log.info("Впрок: искали %s, связано %s, неоднозначно %s, нет в наличии %s, не нашлось %s",
             out["tried"], out["found"], out["ambiguous"], out["out"], out["none"])
    return out


def main(argv: list[str] | None = None) -> int:
    from app import users

    parser = argparse.ArgumentParser(description="Найти товарам человека артикулы во Впроке поиском витрины.")
    parser.add_argument("--phone", required=True, help="рабочее место")
    parser.add_argument("--basket", type=int, help="только товары этой корзины")
    parser.add_argument("--limit", type=int, help="не больше стольких товаров")
    parser.add_argument("--force", action="store_true", help="искать и недавно искавшихся")
    parser.add_argument("--dry-run", action="store_true", help="ничего не записывать")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    users.open_workspace(args.phone)
    try:
        products = repo.list_products()
        if args.basket:
            ids = {i["product_id"] for i in repo.basket_items(args.basket)}
            products = [p for p in products if p.id in ids]
        res = fill(products, write=not args.dry_run, force=args.force, budget_s=None, limit=args.limit)
    finally:
        users.deactivate()
    print(f"{L._masked(args.phone)}: искали {res['tried']}, связано {res['found']}, неоднозначно {res['ambiguous']},"
          f" нет в наличии {res['out']}, не нашлось {res['none']}, без марки или фасовки {res['skipped']}"
          + (f"; остановлено: {res['blocked']}" if res["blocked"] else ""))
    for pid, name, sku, card in res["picks"]:
        print(f"  #{pid} {name} → {sku} {card}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
