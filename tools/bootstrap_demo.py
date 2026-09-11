"""Подготовка демо: сопоставить всю тестовую корзину с магазинами и обновить цены.

Запуск из корня проекта (после tools/seed.py):
    .venv\\Scripts\\python.exe tools/bootstrap_demo.py

Сначала пробует автосопоставление по порогу похожести, затем для оставшихся позиций
подтверждает лучшего кандидата принудительно — чтобы демо-расчёт был полным.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import repo, service  # noqa: E402

STORE_CODES = ["magnit", "vkusvill"]


def main(force: bool = True, threshold: float = 0.55) -> None:
    from app.matcher import auto_match, confirm, find_candidates, refresh_prices

    products = repo.list_products()
    product_ids = [p.id for p in products]

    res = auto_match(product_ids, STORE_CODES, threshold=threshold)
    print(f"Автосопоставлено: {res.get('auto', 0)}, требуют внимания: {len(res.get('need_review', []))}")

    if force:
        forced = 0
        for p in products:
            for code in STORE_CODES:
                store = repo.get_store(code)
                if repo.confirmed_mapping(p.id, store.id):
                    continue
                cands = find_candidates(p.id, code, limit=3)
                if cands:
                    confirm(p.id, code, cands[0].sku)
                    forced += 1
        print(f"Подтверждено принудительно: {forced}")

    upd = refresh_prices(product_ids, STORE_CODES)
    print(f"Обновлено цен: {upd.get('updated', 0)}, ошибок: {len(upd.get('errors', []))}")

    matrix = repo.mapping_matrix()
    for code in STORE_CODES:
        store = repo.get_store(code)
        covered = sum(1 for p in products if matrix.get((p.id, store.id)))
        print(f"{store.name}: подтверждено {covered} из {len(products)}")

    basket = repo.list_baskets()
    if basket:
        cov = service.price_coverage(basket[0]["id"])
        print("Покрытие ценами по корзине:", cov)


if __name__ == "__main__":
    main()
