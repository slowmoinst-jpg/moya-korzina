"""Пакет чеков: много чеков за одну загрузку плюс опись того, что осталось.

Откуда берётся пакет. Букмарклет «Забрать все чеки» (docs/grab.src.js) работает
на странице кабинета ФНС и складывает в один файл две разные вещи:

- **receipts** — чеки целиком, с позициями; их мы кладём в историю;
- **catalogue** — ОПИСЬ всех чеков кабинета: ключ, дата, магазин, сумма, без
  позиций. Она нужна ровно для одного ответа, который иначе взять неоткуда:
  «в кабинете 213 чеков, у вас загружено 47, не хватает 166».

Почему чек считается по ключу. Автоматическая загрузка по определению повторяется:
человек нажмёт кнопку и завтра, и через неделю. Без отпечатка второй заход удвоил
бы историю. У ФНС ключ свой, готовый; для чеков из файла и из вставки отпечаток
считается по дате, магазину, сумме и составу — см. fingerprint.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app import repo
from app.db import init_db
from app.importers.ofd_pdf import Receipt, _ensure_product, _remember_barcode, _resolve_store_id
from app.importers.sources import parse_receipt_json


def fingerprint(receipt: Receipt) -> str:
    """Отпечаток чека для источников без собственного ключа.

    Берём то, что чек не меняет при повторной выгрузке: дату, магазин, итог и
    состав. Названия позиций входят в отпечаток, потому что два похода в один
    магазин в один день на одну сумму — редкость, а вот один и тот же чек,
    загруженный дважды, — обычное дело.
    """
    parts = [receipt.date, receipt.store_name, f"{receipt.total:.2f}"]
    parts += [f"{r.raw_name}|{r.qty}|{r.total}" for r in receipt.rows]
    digest = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()
    return f"fp:{digest[:16]}"


def _as_data(payload: str | bytes | dict | list) -> Any:
    if isinstance(payload, (str, bytes)):
        return json.loads(payload)
    return payload


def _catalogue_entry(item: Any) -> dict | None:
    """Строка описи. Принимает и наш формат, и сырую строку списка ФНС."""
    if not isinstance(item, dict):
        return None
    key = item.get("key") or item.get("receiptKey") or item.get("id")
    if not key:
        return None
    from app.importers.sources import _date, _money

    total = _money(item.get("totalSum", item.get("total")))
    raw_date = (item.get("date") or item.get("createdDate") or item.get("receiveDate")
                or item.get("buyDate") or item.get("dateTime"))
    return {
        "key": str(key),
        "date": _date(raw_date) if raw_date else None,
        "store": str(item.get("store") or item.get("brand") or item.get("retailPlace") or "").strip() or None,
        "total": total,
    }


_DATE_FIELDS = ("dateTime", "date", "createdDate", "receiveDate", "buyDate")


def _has_own_date(body: Any) -> bool:
    """Есть ли дата внутри самого чека — на любой глубине, где лежат позиции."""
    if isinstance(body, dict):
        if any(body.get(f) for f in _DATE_FIELDS):
            return True
        return any(_has_own_date(v) for v in body.values())
    if isinstance(body, list):
        return any(_has_own_date(v) for v in body)
    return False


def _receipt_entries(data: Any) -> list[tuple[str | None, Any, dict]]:
    """Находит чеки в пакете: «ключ — сырой чек — подсказки из обёртки».

    Терпим к форме: пакет букмарклета, голый список чеков, один чек — всё
    это приходит от разных людей и разных источников, и отказывать из-за обёртки
    было бы придирчивостью.

    Подсказки нужны из-за того, как устроен сам кабинет: магазин и дата приходят
    в СПИСКЕ чеков, а позиции — отдельным запросом за фискальными данными. Внутри
    ответа с позициями названия сети может не быть вовсе, поэтому то, что знала
    обёртка, нельзя терять по дороге.
    """
    if isinstance(data, list):
        return [(None, item, {}) for item in data]
    if not isinstance(data, dict):
        return []

    raw = data.get("receipts")
    if isinstance(raw, list) and raw:
        out: list[tuple[str | None, Any, dict]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            # у нас чек лежит как {key, …, fiscalData}, у ФНС — плоско
            body = item.get("fiscalData") or item.get("ticket") or item
            key = item.get("key") or item.get("receiptKey")
            hint = _catalogue_entry(item) or {}
            out.append((str(key) if key else None, body, hint))
        return out
    return [(None, data, {})]


def parse_bundle(payload: str | bytes | dict | list) -> dict:
    """Разбирает пакет, ничего не записывая.

    Возвращает разобранные чеки, опись и список чеков, которые прочитать не вышло:
    молча терять чек нельзя, человек должен видеть, что именно не поддалось.
    """
    data = _as_data(payload)
    catalogue: list[dict] = []
    if isinstance(data, dict):
        for item in data.get("catalogue") or []:
            entry = _catalogue_entry(item)
            if entry:
                catalogue.append(entry)

    parsed: list[dict] = []
    failed: list[dict] = []
    for key, body, hint in _receipt_entries(data):
        try:
            receipt = parse_receipt_json(body)
        except Exception as exc:  # noqa: BLE001 — причина уходит человеку на экран
            failed.append({"key": key, "reason": str(exc)})
            continue
        # Магазин из описи ПЕРЕБИВАЕТ то, что нашлось внутри, и это не произвол.
        # В списке кабинета лежит сеть («Пятёрочка»), а в фискальных данных —
        # торговая точка («ООО "Агроторг", Торговая точка 412»). Сопоставить с
        # нашим справочником магазинов можно только первое.
        if hint.get("store"):
            receipt.store_name = hint["store"]
        # Дату из обёртки берём только если внутри её не было: разбор при пустой дате
        # подставляет сегодняшний день, и отличить «сегодня» от «даты нет» иначе нельзя,
        # а чек трёхлетней давности, помеченный сегодняшним числом, испортит историю.
        if hint.get("date") and not _has_own_date(body):
            receipt.date = hint["date"]
        parsed.append({"key": key or fingerprint(receipt), "receipt": receipt})

    return {"receipts": parsed, "catalogue": catalogue, "failed": failed,
            "source": (data.get("source") if isinstance(data, dict) else None) or "file"}


def store_receipts(items: list[dict], store_code: str | None = None,
                   source: str = "file", catalogue: list[dict] | None = None,
                   failed: list[dict] | None = None) -> dict:
    """Кладёт разобранные чеки в историю и отвечает, сколько взято и сколько осталось.

    Через это место проходит ЛЮБАЯ загрузка — и пакет из кабинета, и одиночный
    файл. Иначе учёт чеков знал бы только про кабинет, и повторная загрузка того
    же PDF по-прежнему удваивала бы историю.

    Уже загруженный чек пропускается — это не ошибка, а норма: автоматическая
    загрузка повторяется, и второй заход обязан быть безвредным.
    """
    init_db()
    known = repo.imported_receipt_keys()

    imported: list[dict] = []
    skipped: list[dict] = []
    created = 0
    rows_total = 0

    for item in items:
        receipt = item["receipt"]
        key = item.get("key") or fingerprint(receipt)
        if key in known:
            skipped.append({"key": key, "date": receipt.date, "total": receipt.total})
            continue

        store_id, store_label = _resolve_store_id(receipt, store_code)
        for row in receipt.rows:
            product_id, is_new = _ensure_product(row.raw_name)
            created += int(is_new)
            if row.barcode:
                _remember_barcode(product_id, row.barcode)
            repo.add_history_row(
                date=receipt.date, store_id=store_id, product_id=product_id,
                raw_name=row.raw_name, qty=row.qty, unit_price=row.unit_price,
                total=row.total, receipt_key=key,
            )
        repo.mark_receipt_imported(key, receipt.date, store_label, receipt.total,
                                   len(receipt.rows), source)
        known.add(key)
        rows_total += len(receipt.rows)
        imported.append({"key": key, "date": receipt.date, "store": store_label,
                         "total": receipt.total, "rows": len(receipt.rows)})

    # Опись пишем после чеков: чек, который в этом же пакете приехал целиком,
    # уже отмечен загруженным, и в «осталось» он не попадёт.
    for entry in catalogue or []:
        repo.note_receipt(entry["key"], entry["date"], entry["store"], entry["total"], source)

    dates = [r["date"] for r in imported if r["date"]]
    stores = {r["store"] for r in imported if r["store"]}
    return {
        "receipts": len(imported),
        "skipped": len(skipped),
        "rows": rows_total,
        "products_created": created,
        "total": round(sum(r["total"] or 0 for r in imported), 2),
        "period": (min(dates), max(dates)) if dates else None,
        "failed": list(failed or []),
        "catalogue": len(catalogue or []),
        "pending": len(repo.pending_receipts()),
        "imported": imported,
        # то же самое словами прежнего одиночного импорта — экраны читают эти ключи
        "date": max(dates) if dates else "",
        "store": stores.pop() if len(stores) == 1 else (f"{len(stores)} магазинов" if stores else "—"),
    }


def import_bundle(payload: str | bytes | dict | list, store_code: str | None = None) -> dict:
    """Пакет чеков из файла или из букмарклета — в историю."""
    bundle = parse_bundle(payload)
    return store_receipts(bundle["receipts"], store_code, bundle["source"],
                          bundle["catalogue"], bundle["failed"])


__all__ = ["parse_bundle", "import_bundle", "store_receipts", "fingerprint"]
