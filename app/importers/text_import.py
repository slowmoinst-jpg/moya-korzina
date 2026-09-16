"""Загрузка истории из того, что человек может скопировать прямо сейчас.

Файл с чеком есть не у всех и не всегда. А письмо «Чек на ваш заказ» от Самоката
или страница заказа в личном кабинете Ленты есть у любого, кто этими доставками
пользуется. Скопировать их — два движения, и это единственный способ начать
пользоваться приложением сегодня, а не когда мы договоримся с ФНС.

Разбор нарочно снисходительный (см. app/importers/orders.py) и возвращает не
только понятые строки, но и непонятые: человек должен видеть, чего мы не взяли.
"""
from __future__ import annotations

from app import repo
from app.db import init_db
from app.importers.orders import parse_order


def preview(text: str, store_code: str | None = None) -> dict:
    """Что мы поняли из вставленного текста. В базу ничего не пишет.

    Сначала показать, потом сохранять: если разбор ошибся, человек увидит это до
    того, как мусор попадёт в историю покупок.
    """
    receipt, skipped = parse_order(text)
    return {
        "date": receipt.date,
        "store": (repo.get_store(store_code).name if store_code and repo.get_store(store_code)
                  else receipt.store_name),
        "total": receipt.total,
        "rows": [{"name": r.raw_name, "qty": r.qty, "price": r.unit_price, "total": r.total}
                 for r in receipt.rows],
        "skipped": skipped,
    }


def import_order_text(text: str, store_code: str | None = None) -> dict:
    """Пишет разобранный заказ в историю покупок и заводит недостающие эталоны."""
    init_db()
    receipt, skipped = parse_order(text)
    if not receipt.rows:
        return {"rows": 0, "products_created": 0, "skipped": skipped,
                "date": receipt.date, "store": store_code or receipt.store_name, "total": 0.0}

    # Через общий учёт: вставленный дважды чек не должен удваивать историю —
    # а вставляют повторно часто, потому что глазами чек от чека не отличить.
    from app.importers.bundle import store_receipts

    result = store_receipts([{"key": None, "receipt": receipt}], store_code, "paste")
    # «skipped» здесь исторически означает непонятые СТРОКИ, и экраны читают именно
    # их. Пропущенные чеки-дубли — другое число, и оно едет под своим именем.
    result["skipped_receipts"] = result["skipped"]
    result["skipped"] = skipped
    return result
