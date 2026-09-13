"""Отчёт о качестве сопоставления: чему на самом деле равна наша экономия.

Приложение считает выгоду по ценам тех товаров, которые сопоставил сам. Если
«Страчателла» сопоставилась с мороженым, а детское пюре — с соком, то итоговая
цифра экономии — красивая неправда, и никакой оптимизатор этого не исправит.

Этот отчёт делает качество сопоставления видимым. Для каждой пары «эталон —
магазин» он показывает выбранного кандидата, оценку и, главное, признаки того,
что выбор сомнителен:

    тип     родовое слово разошлось (сыр против мороженого)
    бренд   марки разные (Axe против Rexona)
    вес     граммовка вне допуска
    слабо   оценка ниже порога уверенности

Запуск:
    python tools/match_report.py                 # по текущей базе
    python tools/match_report.py --basket 1      # только позиции корзины
    python tools/match_report.py --json          # машиночитаемо, для сравнения до/после

Отчёт ничего не меняет в базе: он читает уже подтверждённые сопоставления.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo  # noqa: E402
from app.matcher.normalize import similarity  # noqa: E402
from app.matcher.quality import doubts  # noqa: E402

CONFIDENT = 0.75


def rows(basket_id: int | None) -> list[dict]:
    products = repo.list_products(active_only=False)
    if basket_id:
        wanted = {item["product_id"] for item in repo.basket_items(basket_id)}
        products = [p for p in products if p.id in wanted]
    stores = repo.list_stores()

    out: list[dict] = []
    for product in products:
        for store in stores:
            mapping = repo.confirmed_mapping(product.id, store.id)
            if not mapping:
                continue
            raw = mapping.get("raw_name") or ""
            flags = doubts(product, raw, mapping.get("weight_g"))
            score = similarity(product.name, raw)
            if score < CONFIDENT:
                flags.append("слабо")
            out.append({
                "product": product.name,
                "store": store.name,
                "matched": raw,
                "sku": mapping.get("sku"),
                "score": round(score, 3),
                "flags": flags,
            })
    return out


def render(data: list[dict]) -> str:
    if not data:
        return "Подтверждённых сопоставлений нет — нечего проверять."
    bad = [r for r in data if r["flags"]]
    lines = [
        f"Сопоставлений: {len(data)}. Сомнительных: {len(bad)} "
        f"({round(100 * len(bad) / len(data))}%).",
        "",
    ]
    for row in sorted(data, key=lambda r: (not r["flags"], r["score"])):
        mark = "!" if row["flags"] else " "
        note = (" · " + ", ".join(row["flags"])) if row["flags"] else ""
        lines.append(f"{mark} {row['product'][:34]:<34} {row['store'][:10]:<10} "
                     f"{row['score']:<6} {row['matched'][:44]}{note}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Качество сопоставления товаров")
    parser.add_argument("--basket", type=int, default=None, help="только позиции этой корзины")
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    args = parser.parse_args()

    data = rows(args.basket)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print(render(data))
    return 1 if any(r["flags"] for r in data) else 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
