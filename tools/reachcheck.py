"""Доходит ли до сети НАШ браузер там, где не доходит простой запрос.

ЗАЧЕМ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ. Сборщики каталога ходят простым HTTP (requests), а
окно магазина (app/shopbrowser) — настоящим Chromium. Это две разные двери, и до
20.09.2026 мы путали их выводы: если карточка Перекрёстка отдала заглушку на
1 837 байт сборщику, это НЕ значит, что её же не отдаст браузер. Стены бывают
двух видов, и лечатся они разным:

    по адресу     — сеть называет наш IP и отказывает до всякой проверки;
                    браузер тут не поможет, нужен другой выходной адрес;
    по «не браузер» — сеть ждёт исполнения JS и настоящего окна;
                    наш Chromium её проходит сам, ничего не подменяя.

Инструмент открывает адрес настоящим Chromium и печатает, что пришло: размер,
заголовок, сколько знаков рубля на странице и не видно ли слов отказа. Этого
достаточно, чтобы отличить одну стену от другой.

    python tools/reachcheck.py                    весь список ниже
    python tools/reachcheck.py fixprice           одну цель
    python tools/reachcheck.py --url <адрес>      произвольный адрес
    python tools/reachcheck.py dixy --proxy socks5://127.0.0.1:1080
                                                  через туннель с ноутбука владельца
                                                  (tools/tunnel; контейнеру нужен --network host)

Запросов на запись нет: только открываем и читаем.
"""
from __future__ import annotations

import argparse
import re
import sys

# Цели — именно КАРТОЧКИ и разделы с ценами, а не главные страницы: у половины
# сетей главная открывается всем, а цена живёт там, куда не пускают.
TARGETS = {
    "perekrestok": "https://www.perekrestok.ru/cat/121/p/"
                   "maslo-slivocnoe-icalki-krestanskoe-72-5-500g-3636734",
    "fixprice": "https://fix-price.com/catalog/produkty-i-napitki",
    "vprok": "https://www.vprok.ru/catalog/2050/molochnoe-yaytsa",
    "pyaterochka": "https://5ka.ru/catalog/",
    "samokat": "https://samokat.ru/",
    "monetka": "https://monetka.ru/",
    "dixy": "https://dixy.ru/catalog/",
}

# Слова, которыми сети отказывают. Различать их важно: первая группа говорит про
# адрес, вторая — про проверку браузера.
BY_ADDRESS = ("доступ к сайту", "forbidden", "включён впн", "включен впн",
              "используете vpn", "проверьте настройки интернета")
BY_CHECK = ("я не робот", "проверка браузера", "checking your browser",
            "enable javascript", "ddos-guard", "qrator",
            "пройдите проверку", "не с ботом")


def wall_of(text: str) -> str:
    """Какая стена на странице: «по проверке», «по адресу» или никакой.

    ПРОВЕРКА СМОТРИТСЯ ПЕРВОЙ. Страница ServicePipe у Самоката и Пятёрочки пишет
    «пройдите проверку, чтобы получить доступ к сайту», и «доступ к сайту» из списка
    адресных слов называл её стеной по адресу (замер 23.09.2026). Разница не
    словесная: проверку проходит человек, а адрес лечится только другим выходом.
    """
    low = (text or "").lower()
    if any(word in low for word in BY_CHECK):
        return "по проверке"
    if any(word in low for word in BY_ADDRESS):
        return "по адресу"
    return ""

RUB = re.compile(r"[₽]|руб\.")
PRICE = re.compile(r"\d{1,5}[.,]\d{2}\s*(?:₽|руб)|(?:₽|руб)\s*\d{1,5}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Доходит ли наш браузер до сети")
    parser.add_argument("target", nargs="?", help="имя цели из списка")
    parser.add_argument("--url", help="произвольный адрес вместо списка")
    parser.add_argument("--wait", type=float, default=6.0, help="сколько ждать отрисовки, с")
    parser.add_argument("--proxy", help="выход через прокси, например socks5://127.0.0.1:1080")
    parser.add_argument("--headed", action="store_true",
                        help="обычный режим с окном (нужен DISPLAY — в контейнере его даёт Xvfb) "
                             "и родная подпись браузера, как у окна магазина app/shopbrowser")
    args = parser.parse_args()

    if args.url:
        jobs = [("вручную", args.url)]
    elif args.target:
        if args.target not in TARGETS:
            print(f"нет такой цели. Есть: {', '.join(TARGETS)}")
            return 2
        jobs = [(args.target, TARGETS[args.target])]
    else:
        jobs = list(TARGETS.items())

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright не установлен — проверять нечем")
        return 1

    # С ОКНОМ — ПОТОМУ ЧТО ТАК ХОДИТ ОКНО МАГАЗИНА. Замер 25.09.2026: 5ka.ru через
    # домашний интернет владельца отказал безоконному браузеру («Проблемы со связью»),
    # а обычный браузер на том же ноутбуке открыл витрину. Безоконный режим сам
    # сообщает странице, что за ним никто не смотрит (app/shopbrowser/driver._headless),
    # и проверка без окна меряет не то, с чем придёт сборщик.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=["--no-sandbox"],
                                     proxy={"server": args.proxy} if args.proxy else None)
        try:
            for name, url in jobs:
                _one(browser, name, url, args.wait, own_agent=args.headed)
        finally:
            browser.close()
    return 0


def _one(browser, name: str, url: str, wait: float, own_agent: bool = False) -> None:
    print(f"\n=== {name} ===\n  {url}")
    # Подпись «Windows» ставится только безоконному: его родная подпись содержит
    # «HeadlessChrome». Браузеру с окном подменять нечего — он представляется собой.
    agent = {} if own_agent else {"user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")}
    context = browser.new_context(
        locale="ru-RU", timezone_id="Europe/Moscow",
        viewport={"width": 1366, "height": 900}, **agent)
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = response.status if response else "?"
        page.wait_for_timeout(int(wait * 1000))
        html = page.content()
        text = page.inner_text("body")[:200000]
    except Exception as exc:  # noqa: BLE001 — отказ тоже ответ
        print(f"  НЕ ОТКРЫЛОСЬ: {type(exc).__name__}: {str(exc)[:160]}")
        context.close()
        return

    wall = wall_of(text)
    prices = PRICE.findall(text)
    print(f"  HTTP {status}, разметки {len(html)} знаков, текста {len(text)}")
    print(f"  заголовок: {page.title()[:90]}")
    print(f"  знаков рубля: {len(RUB.findall(text))}, похожих на цену: {len(prices)}")
    if prices[:5]:
        print(f"  примеры цен: {prices[:5]}")
    if wall:
        print(f"  СТЕНА {wall}: {text.strip()[:140]}")
    elif prices:
        print("  ЦЕНЫ ВИДНЫ — эту сеть браузер берёт")
    else:
        print(f"  страница пришла, цен не видно: {text.strip()[:140]}")
    context.close()


if __name__ == "__main__":
    raise SystemExit(main())
