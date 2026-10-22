"""Сбор карточек витрин Самоката и Пятёрочки обычным Chrome через туннель ноутбука.

ЗАЧЕМ ЭТОТ ИНСТРУМЕНТ. Серверный Playwright у обеих сетей получает заглушку
ServicePipe (замеры 18–19.10.2026), поэтому их каталога в единой базе не было:
Самокат — 0 строк, Пятёрочка — 86 строк от телефона. Настоящий Chrome, запущенный
через домашний выход ноутбука, витрины читает (замер 19–20.10.2026). Playwright
здесь ТОЛЬКО подключается по CDP к уже запущенному Chrome — свой Chromium не
запускает: его витрины не пускают («Playwright chrome for dev» блокируется).

ЧТО НУЖНО ДО ЗАПУСКА.
1. Туннель ноутбука: на сервере живёт socks5 127.0.0.1:1080 (tools/tunnel),
   с рабочей машины — форвард:
       ssh -N -L 1081:127.0.0.1:1080 korzina
2. Установленный Google Chrome с этим прокси и отладкой:
       chrome --user-data-dir=<временный> --proxy-server=socks5://127.0.0.1:1081
              --remote-debugging-port=9335 --no-first-run about:blank
   Профиль лучше свежий: ServicePipe запоминает сессию. Проверку витрина проходит
   сама, если сначала открыть главную сети (инструмент так и делает).
3. Пакеты: playwright (в репозитории уже есть).

ЗАПУСК.
    python tools/browser_cards.py --chain samokat
    python tools/browser_cards.py --chain both --portion 6 --pause 45
    python tools/browser_cards.py --chain samokat --queries "молоко,хлеб,сыр"

Результат — data/browser_cards/cards_<сеть>.json, готовый для
tools/browser_cards_ingest.py. Повторный сбор по тем же страницам — это обновление
цен знакомых строк, дублей не будет (артикул сети берётся из адреса карточки).

ПОЧЕМУ ПОРЦИЯМИ И С ПАУЗАМИ. После ~16 страниц подряд Самокат отдаёт проверку
ServicePipe (/xpvnsulc/) и перестаёт пускать адрес; проверка проходящая, но лучше
не частить. Инструмент идёт порциями (--portion страниц), между порциями молчит
--pause секунд, а увидев проверку — ждёт, один раз перезагружает и, если не
отпустило, останавливается, сохранив собранное.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "browser_cards"
CDP = "http://127.0.0.1:9335"

# Запросы поиска витрины. По ним собирается разный ассортимент; один и тот же
# артикул с разных страниц не дублируется (дедупликация по пути карточки).
DEFAULT_QUERIES = [
    "молоко", "хлеб", "яйца", "сыр", "мясо", "курица", "рыба", "вода", "сок", "чай",
    "кофе", "шоколад", "печенье", "йогурт", "творог", "кефир", "колбаса", "сосиски",
    "овощи", "фрукты",
]

# Карточки Самоката: название — в title блока ProductCard_name (полное, с фасовкой),
# цена — в блоке действий: старая цена лежит отдельным узлом oldPrice и склеиваться
# с новой не должна (замер 20.10.2026: «186165 ₽» — это старая 186 и новая 165 ₽).
SAMOKAT_JS = r"""
() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/product/"]')) {
    const href = a.href || "";
    const nameEl = a.querySelector('[class*="ProductCard_name"]');
    const img = a.querySelector("img[alt]");
    let name = (nameEl && nameEl.getAttribute("title")) || (img && img.getAttribute("alt")) || "";
    name = name.replace(/\s+/g, " ").trim();
    let priceText = "";
    for (const el of a.querySelectorAll("span, div")) {
      const raw = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (!raw || raw.indexOf("₽") === -1) continue;
      const clone = el.cloneNode(true);
      clone.querySelectorAll('[class*="oldPrice"], s, del').forEach(n => n.remove());
      const clean = (clone.textContent || "").replace(/\s+/g, " ").trim();
      if (!/\d/.test(clean) || clean.length > 40) continue;
      if (!priceText || clean.length < priceText.length) priceText = clean;
    }
    const text = (a.innerText || "").replace(/\s+/g, " ").trim();
    out.push({ url: href, name, priceText, in_stock: !/нет\s+в\s+наличии/i.test(text) });
    if (out.length >= 600) break;
  }
  return JSON.stringify(out);
}
"""

# Карточки Пятёрочки: у неё подпись карточки уже содержит название, фасовку и цену
# (в скидочных — старую и новую подряд), поэтому берём текст и разбираем здесь.
PYATEROCHKA_JS = r"""
() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/product/"]')) {
    const text = (a.innerText || "").replace(/\s+/g, " ").trim();
    const href = a.href || "";
    if (!text || !href) continue;
    out.push({ url: href, text, in_stock: !/нет\s+в\s+наличии/i.test(text) });
    if (out.length >= 600) break;
  }
  return JSON.stringify(out);
}
"""

CHAINS = {
    "samokat": {
        "name": "Самокат",
        "home": "https://samokat.ru/",
        "page": "https://samokat.ru/search?value={q}",
        "mode": "dom",
        "js": SAMOKAT_JS,
        "max_cards": 1500,
        "min_links": 20,
    },
    "pyaterochka": {
        "name": "Пятёрочка",
        "home": "https://5ka.ru/",
        "main": "https://5ka.ru/",
        "page": "https://5ka.ru/search/?text={q}",
        "mode": "text",
        "js": PYATEROCHKA_JS,
        "max_cards": 600,
        "min_links": 8,
    },
}

LINK_COUNT = "() => document.querySelectorAll('a[href*=\"/product/\"]').length"

PAIR = re.compile(r"(\d{1,3}(?:\s\d{3})*|\d+)\s+(\d{2})\s*₽")
SINGLE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*₽")
TRAILING_PRICE_FOR = re.compile(r"\s*Цена\s+за\s+\d+(?:[.,]\d+)?\s*(?:г|мл|кг|л)\s*$", re.I)
TINSEL = [
    re.compile(r"^\s*Классная\s+цена\s*", re.I),
    re.compile(r"^\s*Комбо(?:\s*[\d.,]+)?\s*", re.I),
    re.compile(r"^\s*[-−–]?\d{0,3}(?:[.,]\d+)?\s*%\s*"),
    re.compile(r"^\s*Нет\s+оценок\s*", re.I),
    re.compile(r"^\s*\d+(?:[.,]\d+)?\s*оцен[а-яё]*\s*", re.I),
    re.compile(r"^(?:\d{1,2}\s+){2,}Больше\s+нет\s*", re.I),
    re.compile(r"^\s*Больше\s+нет\s*", re.I),
    re.compile(r"^\s*от\s*\d+\s*шт\s*", re.I),
    re.compile(r"^\s*(?:Хит|Новинка|Выгодно)\s*", re.I),
    re.compile(r"^\s*\d+[.,]\d+(?![\d.,])\s*"),
]


def parse_price(text: str) -> float | None:
    """Цена из подписи: «165 ₽», «299 99 ₽», «1 234 56 ₽». None — не разобрали."""
    clean = " ".join(str(text or "").split())
    match = PAIR.search(clean)
    if match:
        return round(float(match.group(1).replace(" ", "")) + int(match.group(2)) / 100, 2)
    match = SINGLE.search(clean)
    if match:
        return round(float(match.group(1).replace(",", ".")), 2)
    return None


def clean_text_name(text: str) -> str:
    clean = " ".join(str(text or "").split())
    for _ in range(8):
        before = clean
        for pattern in TINSEL:
            clean = pattern.sub("", clean, count=1)
        clean = clean.strip(" ,.;:—-·•")
        if clean == before:
            break
    cut = clean.find("·")
    if cut > 3:
        clean = clean[:cut].strip(" ,.;:—-")
    return TRAILING_PRICE_FOR.sub("", clean).strip(" ,.;:—-")


def parse_text_card(text: str) -> dict | None:
    """Карточка Пятёрочки из подписи: имя до ПЕРВОЙ цены, платим за ПОСЛЕДНЮЮ."""
    clean = " ".join(str(text or "").split())
    pairs = [(m.start(), float(m.group(1).replace(" ", "")) + int(m.group(2)) / 100)
             for m in PAIR.finditer(clean)]
    if not pairs:
        pairs = [(m.start(), float(m.group(1).replace(",", "."))) for m in SINGLE.finditer(clean)]
    if not pairs:
        return None
    name = clean_text_name(clean[:pairs[0][0]])
    price = pairs[-1][1]
    if not name or price <= 0:
        return None
    return {"name": name[:180], "price": round(price, 2)}


def parse_dom_card(card: dict) -> dict | None:
    name = clean_text_name(card.get("name") or "")
    price = parse_price(card.get("priceText") or "")
    if not name:
        return None
    return {"name": name[:180], "price": price}


def pages_for(chain: str, queries: list[str]) -> list[str]:
    cfg = CHAINS[chain]
    pages = [cfg["page"].format(q=quote(q)) for q in queries]
    if cfg.get("main"):
        pages.insert(0, cfg["main"])
    return pages


def wait_shelf(page, min_links: int, timeout_s: float) -> tuple[int, bool]:
    """Ждём витрину: (ссылок, встретилась ли проверка ServicePipe)."""
    deadline = time.time() + timeout_s
    links = 0
    while time.time() < deadline:
        page.wait_for_timeout(2500)
        try:
            links = int(page.evaluate(LINK_COUNT))
        except Exception:  # noqa: BLE001 — страница перерисовывается
            links = 0
        if links >= min_links:
            return links, False
    return links, "/xpvnsulc/" in page.url


def open_page(page, url: str, chain_cfg: dict, timeout_s: float, challenge_wait: float,
              quiet: bool = False) -> bool:
    """Открыть страницу и дождаться витрины. False — сеть не пустила."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:  # noqa: BLE001 — SPA может перенаправить
        print(f"    предупреждение перехода: {type(exc).__name__}: {str(exc)[:100]}", flush=True)
    links, challenged = wait_shelf(page, chain_cfg["min_links"], timeout_s)
    if not challenged and links >= chain_cfg["min_links"]:
        return True
    if challenged:
        print(f"    проверка ServicePipe — жду {challenge_wait:.0f} с и обновляю", flush=True)
        page.wait_for_timeout(challenge_wait * 1000)
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception:  # noqa: BLE001
            pass
        links, challenged = wait_shelf(page, chain_cfg["min_links"], timeout_s)
        if not challenged and links >= chain_cfg["min_links"]:
            return True
    if not quiet:
        print(f"    витрина не открылась (ссылок {links}) — {page.url[:90]}", flush=True)
    return False


def collect_chain(browser, chain: str, args) -> list[dict]:
    cfg = CHAINS[chain]
    context = browser.contexts[0]
    queries = args.queries or DEFAULT_QUERIES
    urls = pages_for(chain, queries)
    if args.max_pages:
        urls = urls[:args.max_pages]
    max_cards = args.max_cards or cfg["max_cards"]

    found: dict[str, dict] = {}
    page = context.new_page()
    try:
        if cfg.get("home"):
            # сначала главная: она проводит проверку и ставит куки сессии
            open_page(page, cfg["home"], cfg, args.load_timeout, args.challenge_wait, quiet=True)
        for index, url in enumerate(urls, 1):
            started = time.time()
            if not open_page(page, url, cfg, args.load_timeout, args.challenge_wait):
                print(f"  {cfg['name']}: остановка на странице {index}/{len(urls)} — сеть не пустила",
                      flush=True)
                break
            for _ in range(3):
                page.evaluate("() => window.scrollBy(0, 1800)")
                page.wait_for_timeout(1200)
            try:
                raw = json.loads(page.evaluate(cfg["js"]))
            except Exception as exc:  # noqa: BLE001
                print(f"  {cfg['name']} {index}: чтение не удалось: {type(exc).__name__}",
                      flush=True)
                raw = []
            added = 0
            for item in raw:
                parts = urlsplit(item.get("url") or "")
                path = parts.path.rstrip("/")
                if "/product/" not in path or path in found:
                    continue
                if cfg["mode"] == "dom":
                    card = parse_dom_card(item)
                else:
                    card = parse_text_card(item.get("text") or "")
                if not card:
                    continue
                card["url"] = f"{parts.scheme}://{parts.netloc}{parts.path}"
                card["in_stock"] = bool(item.get("in_stock", True))
                found[path] = card
                added += 1
            print(f"  {cfg['name']} {index}/{len(urls)}: ссылок {len(raw)}, новых {added}, "
                  f"всего {len(found)}, {time.time() - started:.0f} с", flush=True)
            if len(found) >= max_cards:
                print(f"  {cfg['name']}: потолок {max_cards} — останавливаюсь", flush=True)
                break
            if args.portion and index % args.portion == 0 and args.pause > 0:
                print(f"  пауза {args.pause} с (порция {args.portion})", flush=True)
                time.sleep(args.pause)
    finally:
        page.close()
    return list(found.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сбор карточек витрин Самоката и Пятёрочки обычным Chrome через туннель")
    parser.add_argument("--chain", default="both", choices=["samokat", "pyaterochka", "both"])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="каталог для cards_<сеть>.json")
    parser.add_argument("--cdp", default=CDP, help="адрес отладки Chrome")
    parser.add_argument("--queries", help="поисковые запросы через запятую (по умолчанию набор)")
    parser.add_argument("--max-pages", type=int, default=0, help="предел страниц на сеть (0 — все)")
    parser.add_argument("--max-cards", type=int, default=0, help="предел карточек на сеть")
    parser.add_argument("--portion", type=int, default=6, help="страниц между паузами (0 — без пауз)")
    parser.add_argument("--pause", type=float, default=45.0, help="пауза между порциями, секунды")
    parser.add_argument("--load-timeout", type=float, default=45.0, help="сколько ждать витрину")
    parser.add_argument("--challenge-wait", type=float, default=30.0,
                        help="сколько ждать после проверки ServicePipe перед повтором")
    args = parser.parse_args(argv)

    if args.queries:
        args.queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    chains = ["samokat", "pyaterochka"] if args.chain == "both" else [args.chain]
    print(f"подключаюсь к Chrome по {args.cdp}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(args.cdp)
        result = {}
        for chain in chains:
            print(f"== {CHAINS[chain]['name']} ==", flush=True)
            cards = collect_chain(browser, chain, args)
            result[chain] = cards
            path = out_dir / f"cards_{chain}.json"
            path.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"== {CHAINS[chain]['name']}: собрано {len(cards)} карточек -> {path}", flush=True)
        browser.close()
    print("ГОТОВО", json.dumps({k: len(v) for k, v in result.items()}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
