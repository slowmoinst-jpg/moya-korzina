"""История: чем семья закрывала холодильник до сих пор — и как добавить туда чек.

ФИЛЬТРЫ ПЕРЕЕХАЛИ В АДРЕС, И ЭТО НЕ КОСМЕТИКА. В Streamlit «дата с», «дата по» и
магазин жили виджетами в session_state: показать кому-то «вот наши покупки в Ленте
за август» было нельзя, «назад» сбрасывало отбор, а две вкладки с разными отборами
мешали друг другу. Здесь отбор написан в строке браузера (/history?from=2026-08-01
&store=lenta), поэтому им можно поделиться, к нему можно вернуться и его можно
открыть рядом со вторым.

ЧЕК ЗДЕСЬ — ЭТО ДОГОВОРЁННОСТЬ, А НЕ СУЩНОСТЬ. Отдельной таблицы чеков в базе нет,
строки лежат плоско (purchase_history). Поэтому чеком считается всё, что куплено в
ОДИН ДЕНЬ в ОДНОМ магазине: два похода в одну сеть за день склеятся в один, и
развести их нечем — время покупки не хранится. Появится номер чека из ФНС —
группировать надо будет по нему, и это единственное место, где придётся править.

РАСКРЫТИЕ ЧЕКОВ СДЕЛАНО ТЕГОМ <details>, А НЕ ПАМЯТЬЮ ЭКРАНА. Что раскрыто —
дело браузера, а не сервера: хранить это на сервере значило бы, что вкладка,
открытая рядом, схлопывает чек в соседней.

ЧТО ДЕЛАЕТСЯ ДВУМЯ ШАГАМИ И ПОЧЕМУ. Вставленный текст письма сначала разбирается
и показывается, и только потом пишется в историю. Разбор чужого письма — дело
негарантированное, и если он ошибся, человек обязан увидеть это ДО того, как
мусор попадёт в покупки: из истории они потом уедут в корзину, в расчёт и в
экономию. Поэтому «Проверить разбор» ничего не пишет, а «Добавить в историю» —
отдельное нажатие.

ЧЕГО ЗДЕСЬ НЕТ. Разбор чеков — app/importers, сведение строк в товары — там же,
хранение — app/repo.py. Экран только показывает и передаёт файл.
"""
from __future__ import annotations

import logging
import os
import tempfile

from flask import redirect, render_template, request

from app import repo
from app.web.screens.basket import num, plural_items, rub, unit_label
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PATH = SCREEN_BY_KEY["history"].path

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")

# То, что приложение умеет прочитать. Список нужен и для подписи на экране, и для
# самого поля выбора файла: браузер иначе предложит человеку все файлы подряд.
KINDS = ("pdf", "txt", "json", "csv", "xlsx", "html", "htm", "eml")


def human_date(iso: str | None) -> str:
    """«2026-08-16» -> «16 августа 2026»: дату чека читают глазами, а не машиной."""
    try:
        year, month, day = (int(x) for x in str(iso).split("-")[:3])
        return f"{day} {MONTHS[month - 1]} {year}"
    except (ValueError, IndexError):
        return str(iso or "—")


def _receipts(rows: list[dict]) -> list[dict]:
    """Плоские строки покупок -> чеки: дата плюс магазин, внутри состав.

    Порядок сохраняется тот, в котором пришли строки: list_history отдаёт их от
    свежих к старым, значит и чеки выйдут так же — и первым окажется последний
    поход в магазин, а его и смотрят чаще всего.
    """
    out: list[dict] = []
    index: dict[tuple, int] = {}
    for row in rows:
        key = (row.get("date"), row.get("store_code"))
        if key not in index:
            index[key] = len(out)
            out.append({"date": row.get("date"), "day": human_date(row.get("date")),
                        "store_code": row.get("store_code"),
                        "store_name": row.get("store_name") or "—",
                        "lines": [], "total": 0.0})
        receipt = out[index[key]]
        receipt["lines"].append({
            "name": row.get("product_name") or row.get("raw_name") or "—",
            # Сырое название показываем только когда оно ОТЛИЧАЕТСЯ от опознанного:
            # одинаковые строки подряд читаются как ошибка разбора, а не как уточнение.
            "raw": (row.get("raw_name") if row.get("raw_name")
                    and row.get("raw_name") != row.get("product_name") else None),
            "qty": row.get("qty"), "unit": unit_label(row.get("unit")),
            "unit_price": row.get("unit_price"), "total": row.get("total"),
        })
        receipt["total"] += float(row.get("total") or 0)
    return out


def _filters(stores) -> dict:
    """Отбор, взятый из адреса. Ничего не запоминаем: адрес и есть вся память."""
    code = (request.args.get("store") or "").strip()
    store = next((s for s in stores if s.code == code), None)
    return {"date_from": (request.args.get("from") or "").strip() or None,
            "date_to": (request.args.get("to") or "").strip() or None,
            "store": store}


def _query(filters: dict) -> str:
    """Собрать адрес с тем же отбором: он переживает любое действие на экране."""
    parts = []
    if filters.get("date_from"):
        parts.append(f"from={filters['date_from']}")
    if filters.get("date_to"):
        parts.append(f"to={filters['date_to']}")
    if filters.get("store") is not None:
        parts.append(f"store={filters['store'].code}")
    return "&".join(parts)


def page():
    if request.method == "POST":
        return _act()
    return _draw()


def _draw(preview: dict | None = None, typed: str = "", trouble: str | None = None,
          chosen: str = ""):
    """Нарисовать историю. Разбор вставленного текста приезжает сюда же, без перевода:
    переводить некуда — набранное письмо в адрес не положишь, а терять его нельзя."""
    stores = repo.list_stores()
    filters = _filters(stores)
    rows = repo.list_history(filters["date_from"], filters["date_to"],
                             filters["store"].id if filters["store"] else None)
    receipts = _receipts(rows)

    return render_template(
        "history.html",
        screen=SCREEN_BY_KEY["history"],
        stores=stores, filters=filters, query=_query(filters),
        receipts=receipts, rows_n=len(rows),
        filtered=bool(filters["date_from"] or filters["date_to"] or filters["store"]),
        preview=preview, typed=typed, trouble=trouble, chosen=chosen,
        kinds=KINDS, accept="." + ",.".join(KINDS),
        loaded=request.args.get("loaded", type=int),
        created=request.args.get("new", type=int),
        basket_path=SCREEN_BY_KEY["basket"].path,
        receipts_path=SCREEN_BY_KEY["receipts"].path,
        rub=rub, num=num, plural_items=plural_items, unit_label=unit_label,
        human_date=human_date,
    )


def _store_code() -> str | None:
    """Магазин, выбранный в форме загрузки. Пусто — пусть разбор решает сам."""
    code = (request.form.get("store") or "").strip()
    return code or None


def _act():
    do = request.form.get("do") or ""
    typed = request.form.get("text") or ""
    chosen = (request.form.get("store") or "").strip()

    if do == "upload":
        return _upload(chosen)

    if do in ("preview", "paste"):
        if not typed.strip():
            return _draw(typed=typed, chosen=chosen,
                         trouble="Вставьте текст письма или страницы заказа — разбирать пока нечего.")
        try:
            from app.importers.text_import import import_order_text, preview
        except Exception as exc:  # noqa: BLE001
            return _draw(typed=typed, chosen=chosen, trouble=f"Разбор текста недоступен: {exc}")

        if do == "preview":
            try:
                seen = preview(typed, _store_code())
            except Exception as exc:  # noqa: BLE001 — кривой текст не повод ронять экран
                log.warning("вставленный текст не разобрался", exc_info=True)
                return _draw(typed=typed, chosen=chosen, trouble=f"Не удалось разобрать текст: {exc}")
            return _draw(preview=seen, typed=typed, chosen=chosen)

        try:
            result = import_order_text(typed, _store_code())
        except Exception as exc:  # noqa: BLE001
            log.warning("вставленный заказ не сохранился", exc_info=True)
            return _draw(typed=typed, chosen=chosen, trouble=f"Не удалось сохранить заказ: {exc}")
        return redirect(_done(result))

    return redirect(PATH)


def _upload(chosen: str):
    """Файл чека. Временный файл убираем всегда: разбор падает чаще, чем хотелось бы."""
    sent = request.files.get("file")
    if sent is None or not (sent.filename or "").strip():
        return _draw(chosen=chosen, trouble="Выберите файл — загружать пока нечего.")

    try:
        from app.importers import import_receipt
    except Exception as exc:  # noqa: BLE001
        return _draw(chosen=chosen, trouble=f"Разбор чеков недоступен: {exc}")

    suffix = os.path.splitext(sent.filename)[1] or ".txt"
    handle, path = tempfile.mkstemp(prefix="korzina_upload_", suffix=suffix)
    os.close(handle)
    try:
        sent.save(path)
        result = import_receipt(path, _store_code())
    except Exception as exc:  # noqa: BLE001 — человеку объяснение, а не пятисотая
        log.warning("чек %s не прочитался", sent.filename, exc_info=True)
        return _draw(chosen=chosen, trouble=f"Не удалось прочитать чек: {exc}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return redirect(_done(result))


def _done(result) -> str:
    """Куда вернуть после загрузки.

    В адрес едут только счётчики — сколько строк легло и сколько товаров завелось.
    Ни сумм, ни названий магазина, ни дат: это покупки человека, и в строке
    браузера, в истории и в журналах прокси им делать нечего.
    """
    rows = (result or {}).get("rows") if isinstance(result, dict) else None
    rows = len(rows) if isinstance(rows, list) else (rows or 0)
    created = (result or {}).get("products_created", 0) if isinstance(result, dict) else 0
    return f"{PATH}?loaded={int(rows or 0)}&new={int(created or 0)}"


# Действия живут на адресе экрана (см. такой же приём в app/web/screens/basket.py).
page.methods = ("GET", "POST")

__all__ = ["page", "human_date"]
