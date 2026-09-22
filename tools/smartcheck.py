"""Проверка запасного разбора страницы моделью — вживую, одной командой.

Зачем отдельный инструмент. app/connectors/smart_extract.py устроен так, что
наружу он не бросает НИКОГДА: не смогла модель — вернул None, и коннектор пошёл
своей дорогой. Это правильно для расчёта и ужасно для настройки: включил,
ничего не поменялось, и почему — неизвестно. Здесь всё наоборот: причина
называется вслух, ответ модели печатается целиком.

    python tools/smartcheck.py                # на выдуманной странице
    python tools/smartcheck.py --url <адрес>  # на настоящей странице магазина
    python tools/smartcheck.py --store magnit --sku 1000070784

Ключ берётся из окружения (OPENROUTER_API_KEY и прочие по поставщику), настройка —
из config.yaml. В репозиторий ключ не попадает и здесь ему тоже не место.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.connectors import smart_extract  # noqa: E402

# Выдуманная карточка, но сломанная ровно так, как ломаются настоящие: цена не в
# отдельном теге с говорящим классом, а слитным текстом с пробелом-разделителем
# тысяч и валютой отдельным узлом. Ни одна регулярка коннектора её не возьмёт.
SAMPLE = """
<html><head><title>Сыр ЛАМБЕР 50% 230 г</title></head><body>
<div class="pdp"><h1>Сыр ЛАМБЕР 50&nbsp;% 230&nbsp;г</h1>
  <div class="pdp__pricing"><span data-qa="x7">1&nbsp;249</span><i>&#8381;</i>
  <s>1&nbsp;499</s><em>за упаковку</em></div>
  <div class="pdp__avail"><span>Сегодня в наличии</span></div>
</div></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка запасного разбора моделью")
    parser.add_argument("--url", help="адрес настоящей страницы товара")
    parser.add_argument("--store", default="magnit", help="код магазина для журнала")
    parser.add_argument("--sku", help="артикул: взять страницу коннектором магазина")
    parser.add_argument("--show", action="store_true", help="напечатать, что ушло модели")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("== настройка ==")
    print(f"  модель:   {smart_extract._cfg('model', smart_extract.DEFAULT_MODEL)}")
    print(f"  включено: {smart_extract.enabled()}")
    print(f"  стоит:    {smart_extract.installed()}")
    ok, why = smart_extract.available()
    if not ok:
        print(f"\nНЕ РАБОТАЕТ: {why}")
        return 1
    print("  ключ:     на месте")

    page, source = _page(args)
    if not page:
        return 1
    print(f"\n== страница ==\n  откуда:  {source}\n  размер:  {len(page)} знаков")
    trimmed = smart_extract.trim(page)
    print(f"  модели уходит: {len(trimmed)} знаков")
    if args.show:
        print("-" * 60)
        print(trimmed[:2000])
        print("-" * 60)

    print("\n== спрашиваем модель ==")
    started = time.monotonic()
    answer = smart_extract.price_from_html(args.store, page, "проверка")
    spent = time.monotonic() - started
    print(f"  ответ за {spent:.1f} с: {answer}")
    if answer is None:
        print("\nМодель не дала цены. Причина — в журнале выше строкой WARNING.")
        return 1
    print("\nКанал работает: цена пришла от модели, а не от разбора вёрстки.")
    return 0


def _page(args) -> tuple[str, str]:
    """Страница для разбора и откуда она взялась."""
    if args.url:
        import requests

        try:
            response = requests.get(args.url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        except Exception as exc:  # noqa: BLE001
            print(f"\nСтраницу не достали: {exc}")
            return "", ""
        if response.status_code != 200:
            print(f"\nСтраница ответила {response.status_code} — разбирать нечего. "
                  "Это отказ в доступе, а не задача для модели.")
            return "", ""
        return response.text, args.url
    if args.sku:
        from app.connectors import get_connector

        connector = get_connector(args.store)
        got = connector._get_html(connector._card_url(args.sku))  # noqa: SLF001
        page = got[0] if isinstance(got, tuple) else got
        if not page:
            print("\nКоннектор страницу не достал — до модели дело не доходит.")
            return "", ""
        return page, f"{args.store}, артикул {args.sku}"
    return SAMPLE, "выдуманная карточка со сломанной вёрсткой"


if __name__ == "__main__":
    raise SystemExit(main())
