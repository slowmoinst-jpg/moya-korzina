"""Отвечает ли закрытый API, если звать его ИЗНУТРИ открытой страницы сети.

ЗАЧЕМ. У части сетей адрес API отвечает 307 или 403 на прямой запрос, но та же
витрина этим же адресом пользуется у себя в браузере и получает JSON. Разница не
в адресе, а в том, откуда пришли: у страницы есть куки сессии, Origin, Referer и
заголовки, которые её собственный JS добавляет сам.

Проверка ровно эта: открыть витрину настоящим Chromium, дождаться, пока сеть
заведёт сессию, и дальше звать API не снаружи, а `fetch` ИЗ САМОЙ СТРАНИЦЫ.
Ничего не подменяем: браузер наш, отпечаток его собственный, никаких проверок за
человека не проходим — если сеть покажет «я не робот», замер это честно скажет.

    python tools/apiprobe.py fixprice
    python tools/apiprobe.py perekrestok
    python tools/apiprobe.py --home https://site.ru --api https://api.site.ru/x

Только чтение: ни одного запроса на запись здесь нет.
"""
from __future__ import annotations

import argparse
import json

# Витрина, которую открываем, и адреса API, которые пробуем из неё. Пути взяты из
# того, чем живут сами витрины (у Fix Price — api.fix-price.com/buyer, у
# Перекрёстка — www.perekrestok.ru/api/customer/1.4.1.0).
SITES = {
    "fixprice": {
        "home": "https://fix-price.com/catalog",
        "api": [
            "https://api.fix-price.com/buyer/v1/location/city",
            "https://api.fix-price.com/buyer/v1/category",
            "https://api.fix-price.com/buyer/v1/product/in/produkty-i-napitki?page=1&limit=24",
        ],
    },
    "perekrestok": {
        "home": "https://www.perekrestok.ru",
        "api": [
            "https://www.perekrestok.ru/api/customer/1.4.1.0/catalog/tree",
            "https://www.perekrestok.ru/api/customer/1.4.1.0/catalog/product/feed",
        ],
    },
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Кусок JS, который зовёт адрес ИЗ страницы и возвращает, что вышло. Тело режем:
# нам нужен признак «пустили или нет», а не весь каталог.
FETCH_JS = """
async (url) => {
  try {
    const r = await fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}});
    const t = await r.text();
    return {ok: r.ok, status: r.status, len: t.length, head: t.slice(0, 400)};
  } catch (e) { return {ok: false, status: 0, len: 0, head: String(e)}; }
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Отвечает ли API изнутри страницы")
    parser.add_argument("site", nargs="?", help=f"одна из: {', '.join(SITES)}")
    parser.add_argument("--home", help="произвольная витрина")
    parser.add_argument("--api", action="append", help="адрес API (можно несколько)")
    parser.add_argument("--wait", type=float, default=8.0)
    args = parser.parse_args()

    if args.home and args.api:
        plan = {"вручную": {"home": args.home, "api": args.api}}
    elif args.site in SITES:
        plan = {args.site: SITES[args.site]}
    elif args.site:
        print(f"нет такой витрины. Есть: {', '.join(SITES)}")
        return 2
    else:
        plan = SITES

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            for name, spec in plan.items():
                _probe(browser, name, spec, args.wait)
        finally:
            browser.close()
    return 0


def _probe(browser, name: str, spec: dict, wait: float) -> None:
    print(f"\n=== {name} ===")
    context = browser.new_context(
        locale="ru-RU", timezone_id="Europe/Moscow", user_agent=UA,
        viewport={"width": 1366, "height": 900})
    page = context.new_page()

    # Что витрина зовёт сама — это и есть перечень её настоящих адресов. Снимаем
    # его заодно: гадать по чужому парсеру дешевле один раз, а знать — всегда.
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if "api" in r.url else None)

    # А это главное. Звать чужой API из страницы мешает CORS — браузер режет ответ
    # ДО нас, и «Failed to fetch» выглядит как отказ сети, хотя сеть ответила. Но
    # витрина зовёт тот же адрес сама, и её ответ через нас проходит целиком.
    # Поэтому не спрашиваем — СЛУШАЕМ: что витрина получила, то получили и мы.
    caught: list[dict] = []

    def _listen(response) -> None:
        if "/v1/product/in/" not in response.url and "/catalog/product/feed" not in response.url:
            return
        try:
            caught.append({"url": response.url, "status": response.status,
                           "body": response.json()})
        except Exception:  # noqa: BLE001 — не JSON тоже ответ
            pass

    page.on("response", _listen)

    try:
        page.goto(spec["home"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(int(wait * 1000))
    except Exception as exc:  # noqa: BLE001
        print(f"  витрина не открылась: {type(exc).__name__}: {str(exc)[:140]}")
        context.close()
        return

    text = page.inner_text("body")[:400]
    if "не робот" in text.lower() or "пройдите проверку" in text.lower():
        print(f"  ВИТРИНА ПОКАЗАЛА ПРОВЕРКУ ЧЕЛОВЕКУ: {text.strip()[:120]}")
        print("  Дальше — только с человеком: проверку проходит он, не мы.")
        context.close()
        return
    print(f"  витрина открылась, заголовок: {page.title()[:80]}")
    print(f"  куки сессии: {len(context.cookies())}")

    for url in spec["api"]:
        got = page.evaluate(FETCH_JS, url)
        mark = "ПУСТИЛ" if got["ok"] else "отказ"
        print(f"  {mark:7} HTTP {got['status']:<4} {got['len']:>7}b  {url}")
        if got["ok"]:
            print(f"          {got['head'][:200]}")
        elif got["head"]:
            print(f"          {str(got['head'])[:160]}")

    own = sorted({u.split("?")[0] for u in seen})[:12]
    if own:
        print("  чем витрина пользуется сама:")
        for u in own:
            print(f"    {u}")

    for got in caught[:2]:
        print(f"\n  ПОЙМАН ОТВЕТ ВИТРИНЫ: HTTP {got['status']} {got['url'][:100]}")
        body = got["body"]
        rows = body if isinstance(body, list) else (
            body.get("items") or body.get("products") or body.get("data") or [])
        print(f"  товаров в ответе: {len(rows) if isinstance(rows, list) else '?'}")
        if isinstance(rows, list) and rows:
            first = rows[0]
            print(f"  поля товара: {sorted(first)[:24]}")
            print("  первый товар:")
            print("   ", json.dumps(first, ensure_ascii=False)[:600])
    context.close()


if __name__ == "__main__":
    raise SystemExit(main())
