"""Вердикт живого прогона (tools/emucart.py) — на выдуманных ответах, без сети.

Инструмент ходит и на локальный сервер, и на эмулятор, и в CDP. Здесь проверяется то,
от чего эти походы не зависят, но что решает исход прогона: чистка ответа Магнита от
личного, вердикт «легло / не легло / частично / не доказано» и разговор с окном
отладки. Ошибки здесь дорогие, и все три вида уже случались живьём:

* непрочищенный снимок уносит в публичный репозиторий адрес и телефон владельца;
* мягкий вердикт печатает «наполнилась» на прогоне «1 из 4» (ревью 18.10.2026);
* снимок без привязки к прогону сходит за свежий, и вердикт выносится по чужой корзине.

Живой CDP здесь не поднимается и эмулятор не нужен: разговор с окном проверяется на
подставной странице-сокете на 127.0.0.1 (её tests/conftest.py пускает, наружу хода нет),
а команды — на подставных функциях инструмента.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import emucart  # noqa: E402

ADDRESS = "г. Москва, ул. Примерная, д. 7, кв. 12"
PHONE = "+79995550001"
FIO = "Иванов Иван Иванович"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.verysecret"
LAT, LON = 55.75, 37.62
ZIP, FLAT, BONUS = 123456, 42, 1234.5

SKU_A = "1000218414"
SKU_B = "1000227406"
STAMP = "2d6dc4ec9b85"
BEFORE_AT = "2026-10-18T16:04:47"
REPORT_AT = "2026-10-18T16:40:06"
AFTER_AT = "2026-10-18T16:41:10"


def raw_preview(rows, total_kop=51097, items_count=None, added=None):
    """Ответ checkout/preview, как он приходит: товары в suborders[].items[] и личное рядом.

    Личное положено в те же места, где оно бывает живьём, и ЧИСЛАМИ тоже: ревью
    18.10.2026 показало, что сторож искал только строки, а числовые координаты и индекс
    проходили мимо него — `_number` округляет, и `str(LAT)` их не находил.
    """
    items = [{"goodId": sku, "name": name, "qnty": qty, "price": price,
              "available": True, "image": "https://cdn.magnit.ru/x.jpg",
              "promoId": 123456, "payCardLast4": 7788,
              "url": f"https://magnit.ru/product/{sku}"}
             for sku, name, qty, price in rows]
    summary = {
        "itemsCount": len(items) if items_count is None else items_count,
        "totalFinalPrice": total_kop,
        "totalBasePrice": 49297,
        "itemsTotalSalePrice": 41297,
        "deliveryInfo": {"fee": 5900, "deliveryAddress": ADDRESS},
        "promoCode": "SALE10",
        "latitude": LAT, "longitude": LON, "zip": ZIP, "flat": FLAT,
        "bonusBalance": BONUS,
    }
    if added is not None:
        summary["addedItemsCount"] = added
    return {
        "summary": summary,
        "suborders": [{
            "items": items,
            "deliveryAddress": ADDRESS,
            "recipient": {"fio": FIO, "phone": PHONE, "name": FIO},
            "coords": {"lat": LAT, "lon": LON},
            "storeAddress": ADDRESS,
        }],
        "authToken": TOKEN,
        "userPhone": PHONE,
    }


def snap(rows, items_count=None, total_kop=51097, at=BEFORE_AT, status=200, added=None,
         bound=True, url="https://magnit.ru/webgate/v1/checkout/preview",
         request_id="16502.51"):
    """Снимок корзины: очищенный и привязанный к прогону (или без привязки, как старый)."""
    meta = ({"captured_at": at, "url": url, "request_id": request_id, "status": status}
            if bound else None)
    return emucart.sanitize_preview(raw_preview(rows, total_kop=total_kop,
                                                items_count=items_count, added=added), meta)


def report_of(items, total_kop=51097, items_count=None, background=False, stamp=STAMP,
              at=REPORT_AT):
    """phonecart.last.magnit, как его сохраняет сервер (app/phonecart.accept_report)."""
    return {
        "store": "magnit",
        "at": at,
        "app_version": "1.13",
        "planned": len(items),
        "landed": sum(1 for item in items if item["ok"]),
        "cart_count": items_count,
        "items": items,
        "api_totals": {emucart.PREVIEW_PATH: {
            "summary.itemsCount": len(items) if items_count is None else items_count,
            "summary.totalFinalPrice": total_kop,
            "summary.totalBasePrice": 49297,
            "summary.itemsTotalSalePrice": 41297,
            "deliveryInfo.fee.fee": 5900}},
        "page_totals": [],
        "background": background,
        "stamp": stamp,
    }


def report_item(sku, ok=True, times=1, how="tap", why="", name="товар"):
    return {"name": name, "sku": sku, "ok": ok, "why": why,
            "put": times if ok else 0, "times": times, "how": how, "calls": []}


def one_of(sku, qty=1, name="товар", price=7999, at=BEFORE_AT, items_count=None,
           total_kop=51097, bound=True, added=None, status=200,
           url="https://magnit.ru/webgate/v1/checkout/preview"):
    """Снимок из одной позиции: так короче писать сценарии."""
    return snap([(sku, name, qty, price)],
                items_count=qty if items_count is None else items_count,
                total_kop=total_kop, at=at, bound=bound, added=added, status=status,
                url=url)


def proof_of(result):
    return {row["id"]: row for row in result["proof"]}


def keys_of(value) -> set[str]:
    """Все имена полей во вложенной структуре — чтобы убедиться, что полей с личным нет."""
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in keys_of(child)}
    if isinstance(value, list):
        return {key for child in value for key in keys_of(child)}
    return set()


# ---------- чистка ответа Магнита ----------
def test_personal_never_reaches_the_snapshot():
    """Адрес, телефон, ФИО, токен и координаты выбрасываются — и строками, и числами."""
    clean = emucart.sanitize_preview(raw_preview([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)]))
    dump = json.dumps(clean, ensure_ascii=False)

    for secret in (ADDRESS, PHONE, FIO, TOKEN, str(LAT), str(LON), str(ZIP), str(FLAT),
                   str(BONUS)):
        assert secret not in dump, f"в снимок попало личное: {secret}"
    assert not (keys_of(clean) & {"address", "deliveryAddress", "recipient", "coords",
                                 "authToken", "userPhone", "fio", "phone", "storeAddress",
                                 "latitude", "longitude", "zip", "flat", "bonusBalance"})
    assert clean["dropped"]["personal_fields"] >= 5


def test_summary_keeps_only_the_whitelist():
    """В summary остаются только замеренные итоги: числа координат, индекса и бонусов — нет."""
    clean = emucart.sanitize_preview(raw_preview([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)]))

    assert clean["summary"] == {"itemsCount": 1, "totalFinalPrice": 51097,
                                "totalBasePrice": 49297, "itemsTotalSalePrice": 41297}


def test_position_keeps_article_name_quantity_prices_and_availability():
    """У позиции остаётся ровно то, чем её сверяют: артикул, название, число, цены, наличие."""
    clean = emucart.sanitize_preview(raw_preview([
        (SKU_A, "Алёнка  шоколад   200 г", 3, 7999),
        (SKU_B, "Молоко 1 л", 1, 8999),
    ]))

    first, second = clean["items"]
    assert set(first) == {"article", "article_from", "name", "qnty", "prices", "available"}
    assert first["article"] == SKU_A and first["article_from"] == "goodId"
    # Двойные пробелы свёрнуты: снимок читает человек, а не браузер.
    assert first["name"] == "Алёнка шоколад 200 г"
    assert first["qnty"] == 3.0
    # promoId — номер акции, payCardLast4 — карта: в ценах позиции им не место.
    assert first["prices"] == {"price": 7999.0}
    assert first["available"] is True
    assert clean["items_found"] == 2 and clean["where"] == ["suborders[0].items"]
    assert second["article"] == SKU_B


def test_snapshot_carries_the_binding_to_the_run():
    """Снимок помнит, когда, откуда и с каким кодом он снят — иначе он не доказательство."""
    clean = snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)], at=AFTER_AT, status=200,
                 request_id="16502.99", url="https://magnit.ru/x?secret=1")

    assert clean["captured_at"] == AFTER_AT
    assert clean["status"] == 200 and clean["request_id"] == "16502.99"
    # Адрес без параметров: в query бывает и номер заказа, и метка человека.
    assert clean["url"] == "https://magnit.ru/x"
    assert emucart.sanitize_preview(raw_preview([]))["captured_at"] == ""


def test_snapshot_reads_json_text_and_refuses_garbage():
    """Ответ приходит строкой; не-ответ корзины (страница проверки) — понятная ошибка."""
    text = json.dumps(raw_preview([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)]))

    assert emucart.sanitize_preview(text)["items_found"] == 1
    with pytest.raises(ValueError):
        emucart.sanitize_preview("<html>Я не робот</html>")
    with pytest.raises(ValueError):
        emucart.sanitize_preview({"captcha": True})


def test_fallback_branch_does_not_take_an_address_for_a_cart():
    """Обход по ключам не берёт чужой список: адрес в `name` позиции не попадает.

    Ревью 18.10.2026: `addressLines: [{id, name: "г. Москва…"}]` проходил как позиции
    корзины. Обход нужен — замеренный путь на живом «до» был пуст, — но строгий.
    """
    raw = {
        "summary": {"itemsCount": 1, "totalFinalPrice": 100},
        "addressLines": [{"id": 7, "name": ADDRESS, "qnty": 1}],
        "basket": {"goods": [{"goodId": SKU_A, "qnty": 2, "name": "Молоко 1 л"}]},
    }

    clean = emucart.sanitize_preview(raw)

    assert [item["article"] for item in clean["items"]] == [SKU_A]
    assert ADDRESS not in json.dumps(clean, ensure_ascii=False)
    assert clean["where"] == ["basket.goods"]


def test_fallback_branch_needs_article_and_quantity():
    """Список без количества позицией не считается: артикул есть у любой чужой строки."""
    raw = {"summary": {"itemsCount": 0, "totalFinalPrice": 0},
           "recommendations": [{"id": 5, "name": "Похожий товар"}]}

    clean = emucart.sanitize_preview(raw)

    assert clean["items"] == [] and clean["items_found"] == 0


def test_safe_url_and_own_link():
    """Адрес печатается без параметров, а «свой» определяется по хосту, не подстрокой."""
    assert emucart._safe_url("https://magnit.ru/ecom/cart?uid=1#x") == \
        "https://magnit.ru/ecom/cart"
    assert emucart._safe_url("") == ""
    assert emucart._own_link("https://magnit.ru/product/1") is True
    assert emucart._own_link("https://cdn.magnit.ru/x.jpg") is True
    assert emucart._own_link("https://example.com/?x=magnit.ru/product/1") is False
    assert emucart._own_link("") is False


# ---------- вердикт: исход ----------
def test_verdict_of_a_filled_cart_is_proven():
    """Всё легло, всё сверено, снимки привязаны — «наполнилась»."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999), (SKU_B, "Молоко 1 л", 1, 8999)],
             at=AFTER_AT),
        report_of([report_item(SKU_A, times=3), report_item(SKU_B, times=1)]),
        plan={"stamp": STAMP}, allow_unbound=False)

    assert result["verdict"] == "наполнилась" and result["ok"] is True, result["unproven"]
    assert result["share"] == "2 из 2" and result["defects"] == []
    assert result["bound"] is True
    assert all(row["done"] for row in result["proof"] if row["required"])
    assert result["totals"]["match"] is True and result["counters"]["ok"] is True


def test_one_of_four_is_partly_not_filled():
    """Прогон «1 из 4» — «частично», а не «наполнилась» (ревью 18.10.2026, п. 1)."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([(SKU_A, "Колбаса Вязанка", 1, 19900)], items_count=1, total_kop=29700,
             at=AFTER_AT),
        report_of([report_item(SKU_A, name="Колбаса Вязанка"),
                   report_item(SKU_B, ok=False, why="корзина сети показывает: нет в наличии",
                               name="Нектарины")], items_count=1, total_kop=29700),
        plan={"stamp": STAMP})

    assert result["verdict"] == "частично", result["unproven"]
    assert result["ok"] is False and result["share"] == "1 из 2"
    assert [row["name"] for row in result["not_landed"]] == ["Нектарины"]
    assert "нет в наличии" in result["not_landed"][0]["why"]
    assert any("доля корзины" in note for note in result["notes"])


def test_nothing_landed_is_not_filled():
    """Ни одна позиция не легла — это «не наполнилась», а не «частично»."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([], items_count=0, total_kop=0, at=AFTER_AT),
        report_of([report_item(SKU_A, ok=False, why="нет в наличии")]),
        plan={"stamp": STAMP})

    assert result["verdict"] == "не наполнилась" and result["ok"] is False


def test_ok_true_without_a_position_in_the_preview_is_a_defect():
    """«Легло», а позиции в корзине нет — та самая ошибка, ради которой прогон и затевался."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)], at=AFTER_AT),
        report_of([report_item(SKU_A, times=3), report_item(SKU_B, times=1)]),
        plan={"stamp": STAMP})

    assert result["verdict"] == "не наполнилась"
    assert [defect["kind"] for defect in result["defects"]] == [emucart.DEFECT_MISSING]
    assert result["defects"][0]["sku"] == SKU_B
    assert proof_of(result)["positions"]["done"] is False


def test_ok_false_while_the_position_is_in_the_cart_is_a_defect():
    """Обратный разбор: «не легло», а позиция в корзине прибавилась (раздел 6 плана)."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999), (SKU_B, "Молоко 1 л", 2, 8999)],
             at=AFTER_AT),
        report_of([report_item(SKU_A, times=3),
                   report_item(SKU_B, ok=False, times=1, why="кнопка не нашлась")]),
        plan={"stamp": STAMP})

    assert result["verdict"] == "не наполнилась"
    assert [defect["kind"] for defect in result["defects"]] == [emucart.DEFECT_EXTRA]
    assert "MainActivity" in result["defects"][0]["where"]


def test_ok_false_with_a_position_but_no_quantity_is_not_proven():
    """Позиция в снимке есть, а количества нет: «прошло» сказать нельзя (ревью, п. 4)."""
    after = snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)], at=AFTER_AT)
    del after["items"][0]["qnty"]                 # снимок без количества

    result = emucart.verdict(
        snap([(SKU_A, "Алёнка шоколад 200 г", 1, 7999)], items_count=1, total_kop=7999,
             at=BEFORE_AT),
        after,
        report_of([report_item(SKU_A, ok=False, why="не легло")], total_kop=7999,
                  items_count=1),
        plan={"stamp": STAMP})

    assert result["positions"][0]["match"] is None
    assert result["verdict"] == "не доказано"


def test_quantity_is_counted_from_the_before_snapshot():
    """При непустом «до» ждём прибавку к лежавшему, а не clicks наряда заново."""
    before = snap([(SKU_A, "Алёнка шоколад 200 г", 2, 7999)], items_count=2, total_kop=15998,
                  at=BEFORE_AT)
    report = report_of([report_item(SKU_A, times=3)], items_count=5)

    short = emucart.verdict(before, one_of(SKU_A, 4, at=AFTER_AT, items_count=4,
                                           total_kop=15998),
                            report, plan={"stamp": STAMP})
    assert short["verdict"] == "не наполнилась"
    assert [defect["kind"] for defect in short["defects"]] == [emucart.DEFECT_QTY]
    assert short["positions"][0]["expected"] == 5.0

    exact = emucart.verdict(before, one_of(SKU_A, 5, at=AFTER_AT, items_count=5), report,
                            plan={"stamp": STAMP})
    assert exact["verdict"] == "наполнилась", exact["unproven"]


def test_duplicate_article_rows_are_summed():
    """Две строки отчёта на одном артикуле — одно ожидание, а не «легло 2» (ревью, п. 3)."""
    rows = [(SKU_A, "Алёнка шоколад 200 г", 2, 7999)]
    report = report_of([report_item(SKU_A, times=1, name="Алёнка"),
                        report_item(SKU_A, times=1, name="Алёнка (дубль)")], items_count=2,
                       total_kop=15998)

    good = emucart.verdict(snap([], items_count=0, total_kop=0, at=BEFORE_AT),
                           snap(rows, items_count=2, total_kop=15998, at=AFTER_AT), report,
                           plan={"stamp": STAMP})
    assert good["verdict"] == "наполнилась", good["unproven"]
    assert good["articles"][SKU_A]["add"] == 2 and good["articles"][SKU_A]["rows"] == 2

    bad = emucart.verdict(snap([], items_count=0, total_kop=0, at=BEFORE_AT),
                          one_of(SKU_A, 1, at=AFTER_AT, items_count=1, total_kop=7999),
                          report, plan={"stamp": STAMP})
    assert [defect["kind"] for defect in bad["defects"]] == [emucart.DEFECT_QTY]


def test_kept_position_must_have_been_in_the_cart_before():
    """«Уже лежит» проверяется по «до»: приложение не нажимало, значит позиция лежала."""
    kept = report_item(SKU_A, times=2, how="kept")

    good = emucart.verdict(one_of(SKU_A, 2, at=BEFORE_AT, items_count=2, total_kop=15998),
                           one_of(SKU_A, 2, at=AFTER_AT, items_count=2, total_kop=15998),
                           report_of([kept], items_count=2, total_kop=15998),
                           plan={"stamp": STAMP})
    assert good["verdict"] == "наполнилась", good["unproven"]

    empty_before = emucart.verdict(snap([], items_count=0, total_kop=0, at=BEFORE_AT),
                                   one_of(SKU_A, 2, at=AFTER_AT, items_count=2,
                                          total_kop=15998),
                                   report_of([kept], items_count=2, total_kop=15998),
                                   plan={"stamp": STAMP})
    assert empty_before["verdict"] == "не наполнилась"
    assert [defect["kind"] for defect in empty_before["defects"]] == [emucart.DEFECT_MISSING]
    assert "уже лежит" in empty_before["defects"][0]["why"]


def test_unverifiable_row_is_not_a_failure():
    """Нет числа нажатий или количества — «не доказано», а не ложный провал (ревью, п. 6)."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        one_of(SKU_A, 1, at=AFTER_AT, items_count=1, total_kop=7999),
        report_of([report_item(SKU_A, times=None)], items_count=1, total_kop=7999),
        plan={"stamp": STAMP})

    assert result["verdict"] == "не доказано"
    assert result["positions"][0]["match"] is None
    assert result["defects"] == []


def test_without_the_after_snapshot_the_verdict_is_not_proven():
    """Снимка «после» нет — это не «не наполнилась»: доказательства просто не собраны."""
    result = emucart.verdict(snap([], items_count=0, total_kop=0, at=BEFORE_AT), None,
                             report_of([report_item(SKU_A, times=3)]))

    assert result["verdict"] == "не доказано"
    assert proof_of(result)["after"]["done"] is False
    assert any("api_totals" in note for note in result["notes"])
    assert any("uiautomator" in note for note in result["notes"])


def test_total_must_match_to_the_kopeck():
    """Итог в отчёте — это ответ Магнита; разошёлся хоть на рубль, приёмка не закрыта."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        one_of(SKU_A, 3, at=AFTER_AT, total_kop=51197),
        report_of([report_item(SKU_A, times=3)]), plan={"stamp": STAMP})

    assert result["verdict"] == "не доказано"
    assert proof_of(result)["total"]["done"] is False
    assert result["totals"]["preview"] == 511.97 and result["totals"]["report"] == 510.97


def test_count_mismatch_between_report_and_snapshot_is_not_proven():
    """Итог сошёлся, а число позиций нет — «сошлось» говорить нельзя."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999), (SKU_B, "Молоко 1 л", 1, 8999)],
             items_count=2, at=AFTER_AT),
        report_of([report_item(SKU_A, times=3), report_item(SKU_B, times=1)], items_count=9),
        plan={"stamp": STAMP})

    assert result["totals"]["counts_match"] is False
    assert result["verdict"] == "не доказано"


def test_counter_mismatch_is_not_proven_not_a_pass():
    """`addedItemsCount` сайта против нажатий отчёта — расхождение идёт в «не доказано»."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([(SKU_A, "Алёнка шоколад 200 г", 1, 19900)], items_count=1, total_kop=29700,
             at=AFTER_AT, added=4),
        report_of([report_item(SKU_A, times=1)], items_count=1, total_kop=29700),
        plan={"stamp": STAMP})

    assert result["counters"]["ok"] is False
    assert result["verdict"] == "не доказано"
    assert any("addedItemsCount" in line for line in result["unproven"])


def test_items_count_must_equal_the_positions_or_the_pieces():
    """Снимок сам себе не должен противоречить: itemsCount против позиций и количеств.

    Обе трактовки законны (сайт считает либо позиции, либо штуки), и на живых замерах
    различить их было нечем. Не подходит ни одна — расхождение.
    """
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at=BEFORE_AT),
        snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)], items_count=9, at=AFTER_AT),
        report_of([report_item(SKU_A, times=3)], items_count=9),
        plan={"stamp": STAMP})

    assert result["counters"]["ok"] is False
    assert result["verdict"] == "не доказано"

    # Позиция на 3 штуки: itemsCount=3 — «штуки», itemsCount=1 — «позиции». Обе годны.
    for count in (1, 3):
        fine = emucart.verdict(
            snap([], items_count=0, total_kop=0, at=BEFORE_AT),
            snap([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)], items_count=count,
                 at=AFTER_AT),
            report_of([report_item(SKU_A, times=3)], items_count=count),
            plan={"stamp": STAMP})
        assert fine["counters"]["ok"] is True, (count, fine["unproven"])


# ---------- вердикт: привязка к прогону ----------
def test_unbound_snapshot_is_not_proven_without_the_key():
    """Снимки без времени и источника — «не доказано»; послабление только по ключу."""
    result = emucart.verdict(one_of(SKU_A, 1, at=BEFORE_AT, bound=False),
                             one_of(SKU_A, 1, at=AFTER_AT, bound=False),
                             report_of([report_item(SKU_A, times=1)]),
                             plan={"stamp": STAMP})

    assert result["verdict"] == "не доказано"
    assert result["bound"] is False
    assert "before.captured_at" in result["binding"]["missing"]
    assert any("--allow-unbound" in line for line in result["unproven"])


def test_allow_unbound_counts_but_says_the_proof_is_weaker():
    """С `--allow-unbound` вердикт считается, но оговорка стоит и в тексте, и в JSON."""
    result = emucart.verdict(snap([], items_count=0, total_kop=0, at=BEFORE_AT, bound=False),
                             one_of(SKU_A, 1, at=AFTER_AT, items_count=1, total_kop=7999,
                                    bound=False),
                             report_of([report_item(SKU_A, times=1)], items_count=1,
                                       total_kop=7999),
                             plan={"stamp": STAMP}, allow_unbound=True)

    assert result["verdict"] == "наполнилась", result["unproven"]
    assert result["bound"] is False
    assert result["binding_note"] == "доказательство слабее: снимки без времени и источника"
    assert any("доказательство слабее" in line for line in result["unproven"])


def test_foreign_snapshot_is_not_excused_by_the_key():
    """Метаданные есть и говорят «снято не вокруг этого отчёта» — ключ не поможет."""
    late = snap([(SKU_A, "Алёнка шоколад 200 г", 1, 7999)], items_count=1, total_kop=7999,
                at="2026-10-01T10:00:00")
    report = report_of([report_item(SKU_A, times=1)], total_kop=7999, items_count=1)

    result = emucart.verdict(one_of(SKU_A, 1, at="2026-09-30T10:00:00", bound=True), late,
                             report, plan={"stamp": STAMP}, allow_unbound=True)

    assert result["verdict"] == "не доказано"
    assert any("порядок времён нарушен" in line for line in result["binding"]["problems"])


def test_old_snapshot_of_the_same_goods_is_caught_by_time():
    """Тот же товар, но снимок снят до отчёта — старый снимок за свежий не сходит."""
    result = emucart.verdict(
        snap([], items_count=0, total_kop=0, at="2026-10-01T10:00:00"),
        snap([(SKU_A, "Алёнка шоколад 200 г", 1, 7999)], items_count=1, total_kop=7999,
             at="2026-10-01T10:05:00"),
        report_of([report_item(SKU_A, times=1)], items_count=1, total_kop=7999),
        plan={"stamp": STAMP})

    assert result["verdict"] == "не доказано"
    assert result["binding"]["problems"]


def test_plan_stamp_and_background_are_checked():
    """Отпечаток наряда и признак «фоновая сборка» — то, чем один прогон отличается от другого."""
    after = one_of(SKU_A, 1, at=AFTER_AT, items_count=1, total_kop=7999)
    before = snap([], items_count=0, total_kop=0, at=BEFORE_AT)

    other = emucart.verdict(before, after,
                            report_of([report_item(SKU_A, times=1)], items_count=1,
                                      total_kop=7999, stamp="deadbeef"),
                            plan={"stamp": STAMP})
    assert any("отпечаток наряда" in line for line in other["binding"]["problems"])
    assert other["verdict"] == "не доказано"

    background = emucart.verdict(before, after,
                                 report_of([report_item(SKU_A, times=1)], items_count=1,
                                           total_kop=7999, background=True),
                                 plan={"stamp": STAMP})
    assert any("фоновой сборки" in line for line in background["binding"]["problems"])
    # Для фонового прогона тот же отчёт — свой.
    as_background = emucart.verdict(before, after,
                                    report_of([report_item(SKU_A, times=1)], items_count=1,
                                              total_kop=7999, background=True),
                                    plan={"stamp": STAMP}, manual=False)
    assert as_background["binding"]["problems"] == []


def test_bad_status_and_foreign_host_are_problems():
    """Снимок ошибки и снимок чужого сайта — не доказательство."""
    result = emucart.verdict(
        one_of(SKU_A, 1, at=BEFORE_AT, status=403),
        one_of(SKU_A, 1, at=AFTER_AT, url="https://example.org/cart"),
        report_of([report_item(SKU_A, times=1)]), plan={"stamp": STAMP})

    assert result["verdict"] == "не доказано"
    problems = " ".join(result["binding"]["problems"])
    assert "кодом 403" in problems and "не с magnit.ru" in problems


# ---------- разговор с окном отладки (CDP) ----------
def test_frame_carries_id_method_and_params():
    """Кадр запроса — id, method, params: по id страница узнаёт, чей это ответ."""
    assert json.loads(emucart._frame(7, "Network.enable")) == {
        "id": 7, "method": "Network.enable", "params": {}}
    assert json.loads(emucart._frame(8, "Runtime.evaluate", {"expression": "1 + 1"})) == {
        "id": 8, "method": "Runtime.evaluate", "params": {"expression": "1 + 1"}}


def test_answer_is_found_by_id_and_a_refusal_is_named():
    """Ответ ищем по своему id; отказ страницы и чужой id — понятная ошибка, не молчание."""
    assert emucart._answer_of('{"id": 3, "result": {"body": "x"}}', 3) == {"body": "x"}
    assert emucart._answer_of('{"id": 4, "result": null}', 4) == {}

    with pytest.raises(emucart.CdpError) as wrong:
        emucart._answer_of('{"id": 9, "result": {}}', 4)
    assert "не на наш вызов" in str(wrong.value)

    with pytest.raises(emucart.CdpError) as refused:
        emucart._answer_of('{"id": 4, "error": {"message": "Invalid parameters"}}', 4,
                           "Network.getResponseBody")
    assert "Network.getResponseBody" in str(refused.value)
    assert "Invalid parameters" in str(refused.value)

    with pytest.raises(emucart.CdpError):
        emucart._answer_of("<html>не CDP</html>", 4)


def test_event_is_told_from_an_answer():
    """Событие идёт без id и копится отдельно: принять его за ответ — потерять ответ."""
    assert emucart._event_of('{"method": "Network.responseReceived", "params": {"a": 1}}') == \
        ("Network.responseReceived", {"a": 1})
    assert emucart._event_of('{"method": "Network.enable", "params": null}') == \
        ("Network.enable", {})
    assert emucart._event_of('{"id": 1, "result": {}}') is None
    assert emucart._event_of("не json") is None


def test_preview_hit_picks_the_cart_answer_by_url():
    """Ответ корзины выбирается по адресу и берётся последний: прошлый снимок устарел."""
    other = ("Network.responseReceived", {"requestId": "1",
             "response": {"url": "https://magnit.ru/webgate/v1/carts/lite"}})
    preview = ("Network.responseReceived", {"requestId": "2", "response": {
        "url": f"https://magnit.ru{emucart.PREVIEW_PATH}?x=1", "status": 200}})
    fresher = ("Network.responseReceived", {"requestId": "3", "response": {
        "url": f"https://magnit.ru{emucart.PREVIEW_PATH}", "status": 201}})

    assert emucart._preview_hit([other]) is None
    assert emucart._preview_hit([]) is None
    assert emucart._preview_hit([other, preview, fresher]) == {
        "requestId": "3", "url": f"https://magnit.ru{emucart.PREVIEW_PATH}", "status": 201}
    assert emucart._preview_hit([("Network.responseReceived", {}), other]) is None


def test_body_is_read_only_when_loading_finished_and_it_looks_like_a_cart():
    """Тело берём после loadingFinished, и только если в нём есть итог корзины (ревью, п. 15)."""
    started = [("Network.responseReceived", {"requestId": "5"})]
    finished = [("Network.responseReceived", {"requestId": "5"}),
                ("Network.loadingFinished", {"requestId": "5"})]

    assert emucart._body_loaded(started, "5") is False
    assert emucart._body_loaded(finished, "5") is True
    assert emucart._body_loaded(finished, "9") is False
    assert emucart._cart_body('{"summary": {"totalFinalPrice": 29700}}') is True
    assert emucart._cart_body('{"totalFinalPrice": 0}') is False
    assert emucart._cart_body("<html>Я не робот</html>") is False


def test_page_is_chosen_by_site_and_a_missing_page_is_named():
    """Страницу берём Магнита, если она есть; служебные цели и пустой список — не страница."""
    targets = [
        {"type": "worker", "url": "https://magnit.ru/sw.js",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/W"},
        {"type": "page", "url": "http://10.0.2.2:8611/phone/basket",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/A"},
        {"type": "page", "url": "https://magnit.ru/ecom/cart",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/B"},
        {"type": "page", "url": "https://magnit.ru/ecom/cart"},      # без своего websocket
    ]

    pages = emucart._pages(targets)
    assert [row["url"] for row in pages] == ["http://10.0.2.2:8611/phone/basket",
                                             "https://magnit.ru/ecom/cart"]
    assert emucart._pick_page(pages, "magnit.ru")["url"] == "https://magnit.ru/ecom/cart"
    assert emucart._pick_page(pages, "")["url"] == "http://10.0.2.2:8611/phone/basket"
    assert emucart._pick_page([], "magnit.ru") is None


def test_cookie_names_are_read_never_values():
    """Проверка входа читает ИМЕНА кук и ключей: значения — это и есть вход владельца."""
    assert "split('=')[0]" in emucart.COOKIE_NAMES_JS
    assert emucart._login_visible(["session", "mg_at"]) is True
    assert emucart._login_visible(["mg_at_extra", "session"]) is False
    assert emucart._login_visible([]) is False


def test_login_js_reads_names_and_says_yes_or_no_only():
    """Слова страницы проверяются В СТРАНИЦЕ; регулярки берутся у приложения, не свои."""
    from app.shopbrowser import signals

    js = emucart._login_js()

    assert "getItem" not in js
    assert "localStorage.key" in js
    assert "document.title" not in js
    assert emucart.SESSION_PREFIX in js
    assert json.dumps(signals.STRONG_IN) in js
    assert json.dumps(signals.ANONYMOUS) in js
    assert f"const head = {signals.HEAD_CHARS}, tail = {signals.TAIL_CHARS};" in js
    assert js.startswith("(() =>") and js.endswith(")()")


def test_session_keys_are_the_au_family():
    """Ключи сессии — семейство au_: три имени замерены 18.10.2026, лишнее не мешает."""
    assert emucart._session_keys(["au_atid", "theme", "au_expd", "au_phnu"]) == \
        ["au_atid", "au_expd", "au_phnu"]
    assert emucart._session_keys(["mg_sh", "tf_id"]) == []
    assert emucart._session_keys([]) == []


def test_live_session_beats_the_missing_cookie():
    """Главный случай живого прогона: сессия есть, куки mg_at нет — «вошёл», не «не вошёл»."""
    facts = {"url": "https://magnit.ru/profile", "cookies": ["mg_sh"],
             "session_keys": ["au_atid", "au_expd", "au_phnu"], "storage_count": 47,
             "strong": False, "anon": False, "guard": False}

    state, why = emucart._login_state(facts, "magnit.ru")

    assert state == "yes", why
    assert "au_atid" in why
    line = emucart._human(facts, "magnit.ru")
    assert "вошёл" in line and "не вошёл" not in line


def test_nothing_visible_is_unknown_not_a_refusal():
    """Не видно ни одного признака — «не знаю». «Не вошёл» здесь было бы ложью."""
    facts = {"url": "https://magnit.ru/ecom/cart", "cookies": ["mg_sh"],
             "session_keys": [], "storage_count": 3, "strong": False, "anon": False,
             "guard": False}

    state, why = emucart._login_state(facts, "magnit.ru")

    assert state == "unknown", why
    line = emucart._human(facts, "magnit.ru")
    assert "не знаю" in line and "не вошёл" not in line


def test_login_state_follows_the_app_words():
    """Слова решают, как у приложения: вошедший → да, гость → нет, проверка → не знаю."""
    base = {"url": "https://magnit.ru/ecom/cart", "cookies": [], "session_keys": [],
            "storage_count": 0, "strong": False, "anon": False, "guard": False}

    assert emucart._login_state({**base, "strong": True}, "magnit.ru")[0] == "yes"
    assert emucart._login_state({**base, "anon": True}, "magnit.ru")[0] == "no"
    assert emucart._login_state({**base, "guard": True}, "magnit.ru")[0] == "unknown"
    # Страница зовёт войти, а ключи сессии на месте — верить нечему, говорим «не знаю».
    assert emucart._login_state({**base, "anon": True, "session_keys": ["au_atid"]},
                                "magnit.ru")[0] == "unknown"
    # Старый признак остаётся подсказкой, когда ничего другого нет.
    assert emucart._login_state({**base, "cookies": ["mg_at"]}, "magnit.ru")[0] == "yes"


def test_login_state_of_another_site_is_unknown():
    """Наша собственная страница про вход в магазин не говорит ничего."""
    facts = {"url": "http://10.0.2.2:8611/phone/basket", "cookies": [],
             "session_keys": [], "strong": True, "anon": False, "guard": False}

    state, why = emucart._login_state(facts, "magnit.ru")

    assert state == "unknown" and "не страница magnit.ru" in why


def test_page_expressions_call_themselves():
    """Выражение страницы обязано ЗОВУЩИМ себя быть: `() => …` вернул бы саму функцию.

    Живой прогон 18.10.2026: без `()` проверка входа молча показала «кук нет», потому
    что Runtime.evaluate выполняет код, а функцию-значение не зовёт.
    """
    for expression in (emucart.COOKIE_NAMES_JS, emucart._login_js()):
        assert expression.startswith("(() =>") and expression.endswith(")()"), expression


class FakePage:
    """Подставное окно WebView: те же кадры, что у страницы, включая её особенности.

    Событие с ответом сайта приходит ВПЕРЕМЕЖКУ с ответом на наш вызов — у живого
    WebView было так же, и клиент обязан различать их по id. Функцию, которую забыли
    позвать, страница отдаёт функцией, а не значением: так она и врала на прогоне.
    """

    def __init__(self, preview_url: str, body: str, login_facts: dict, finish: bool = True):
        self.preview_url = preview_url
        self.body = body
        self.login_facts = login_facts
        self.finish = finish
        self.marks: list[dict] = []

    async def handler(self, ws):
        async for raw in ws:
            frame = json.loads(raw)
            method = frame["method"]
            self.marks.append({"method": method, "params": frame.get("params") or {}})
            call_id = frame["id"]
            if method == "Runtime.evaluate":
                expression = str(frame["params"].get("expression") or "")
                if not expression.endswith(")()"):
                    await ws.send(json.dumps({
                        "id": call_id, "result": {"result": {"type": "function"}}}))
                else:
                    await ws.send(json.dumps({"id": call_id, "result": {
                        "result": {"type": "object", "value": self.login_facts}}}))
                continue
            if method == "Network.getResponseBody":
                await ws.send(json.dumps({"id": call_id, "result": {"body": self.body}}))
                continue
            await ws.send(json.dumps({"id": call_id, "result": {}}))
            if method == "Network.enable":
                await ws.send(json.dumps({
                    "method": "Network.responseReceived",
                    "params": {"requestId": "16502.51", "type": "Fetch",
                               "response": {"url": self.preview_url, "status": 200}}}))
                if self.finish:
                    await ws.send(json.dumps({
                        "method": "Network.loadingFinished",
                        "params": {"requestId": "16502.51"}}))


PAGE_FACTS = {"url": "https://magnit.ru/ecom/cart", "cookies": ["mg_sh", "session"],
              "storage_count": 47, "session_keys": ["au_atid", "au_expd", "au_phnu"],
              "strong": False, "anon": False, "guard": False}


async def _talk_to_fake_page(fake: FakePage):
    """Слушаем подставную страницу изнутри её же цикла (serve поднимается тут же)."""
    from websockets.asyncio.server import serve

    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        socket = f"ws://127.0.0.1:{port}"
        caught = await emucart._preview_from_page(socket, "https://magnit.ru/ecom/cart", 2.0, 5.0)
        facts = await emucart._page_facts(socket, 5.0)
    return caught, facts


def test_cdp_client_reads_the_cart_answer_from_a_page_socket():
    """Весь путь: Network.enable → событие → loadingFinished → тело, на живом websocket."""
    preview = json.dumps(raw_preview([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)]),
                         ensure_ascii=False)
    fake = FakePage(f"https://magnit.ru{emucart.PREVIEW_PATH}", preview, PAGE_FACTS)

    caught, facts = asyncio.run(_talk_to_fake_page(fake))

    assert caught["body"] == preview
    assert caught["url"] == f"https://magnit.ru{emucart.PREVIEW_PATH}"
    assert caught["request_id"] == "16502.51" and caught["status"] == 200
    assert emucart.sanitize_preview(caught["body"])["items_found"] == 1
    assert facts["cookies"] == ["mg_sh", "session"]
    assert facts["session_keys"] == ["au_atid", "au_expd", "au_phnu"]
    assert facts["url"] == "https://magnit.ru/ecom/cart"
    assert emucart._login_state(facts, "magnit.ru")[0] == "yes"
    # Переход на корзину — через саму страницу, а не запросом инструмента.
    assert [mark["method"] for mark in fake.marks][:3] == [
        "Network.enable", "Page.enable", "Page.navigate"]
    assert fake.marks[2]["params"]["url"] == "https://magnit.ru/ecom/cart"


def test_cdp_client_waits_for_loading_and_refuses_a_foreign_body():
    """Незавершённая загрузка и тело без итога корзины — не снимок, а честный отказ."""
    preview = f"https://magnit.ru{emucart.PREVIEW_PATH}"

    unfinished = FakePage(preview, '{"summary": {"totalFinalPrice": 1}}', {}, finish=False)
    caught, _ = asyncio.run(_talk_to_fake_page(unfinished))
    assert caught["body"] is None and "не завершилась" in caught["why"]

    foreign = FakePage(preview, "<html>Я не робот</html>", {})
    caught, _ = asyncio.run(_talk_to_fake_page(foreign))
    assert caught["body"] is None and "не похоже на ответ корзины" in caught["why"]


def test_cdp_client_says_so_when_the_cart_answer_never_comes():
    """Ответа корзины нет — это не пустой снимок, а честное «не поймали»."""
    fake = FakePage("https://magnit.ru/webgate/v1/carts/lite", "{}", {})

    caught, _ = asyncio.run(_talk_to_fake_page(fake))

    assert caught["body"] is None and "не запросил" in caught["why"]


def test_evaluate_refuses_undefined_and_functions():
    """Пустота от `evaluate` — ошибка: `undefined` уже читался как «кук нет» (ревью, п. 16)."""
    class Stub:
        async def call(self, method, params=None):
            return {"result": {"type": "undefined"}}

    with pytest.raises(emucart.CdpError) as undefined:
        asyncio.run(emucart.CdpSocket.evaluate(Stub(), "(() => undefined)()"))
    assert "undefined" in str(undefined.value)

    class Fn:
        async def call(self, method, params=None):
            return {"result": {"type": "function"}}

    with pytest.raises(emucart.CdpError):
        asyncio.run(emucart.CdpSocket.evaluate(Fn(), "() => 1"))


# ---------- команды ----------
class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Подставной сервер «Моей корзины»: отвечает тем, чем скажет тест."""

    def __init__(self, plan_payload=None, gate=200):
        self.plan_payload = plan_payload or {"ok": True, "lines": [], "unknown": [],
                                             "basket": 1, "stamp": STAMP, "note": ""}
        self.gate = gate
        self.asked: list[tuple[str, Any]] = []
        self.trust_env = True

    def get(self, url, timeout=None):
        self.asked.append(("GET", url))
        if isinstance(self.gate, Exception):
            raise self.gate
        return FakeResponse(self.gate, {})

    def post(self, url, json=None, timeout=None):
        self.asked.append(("POST", url))
        if url.endswith("/demo"):
            return FakeResponse(200, {})
        return FakeResponse(200, self.plan_payload)


def test_plan_counts_real_links_by_host(tmp_path, capsys, monkeypatch):
    """Наряд: порог считается по хосту, а не подстрокой, и бедный наряд даёт код 30."""
    rows = [{"sku": "1", "clicks": 1, "name": "А", "url": "https://magnit.ru/product/1"},
            {"sku": "2", "clicks": 1, "name": "Б", "url": "https://example.com/?x=magnit.ru"},
            {"sku": "3", "clicks": 1, "name": "В", "url": ""},
            {"sku": "4", "clicks": 1, "name": "Г", "url": "https://magnit.ru/product/4"}]
    monkeypatch.setattr(emucart, "_http", lambda: FakeSession(
        {"ok": True, "lines": rows, "unknown": ["X"], "basket": 1, "stamp": STAMP, "note": "н"}))

    code = emucart.main(["plan", "--out", str(tmp_path / "plan.json")])
    printed = capsys.readouterr().out

    assert code == emucart.EXIT_NOT_FILLED          # настоящих ссылок 2 < порога 4
    assert "из них с настоящими ссылками 2" in printed
    assert "НАРЯД БЕДНЕЕ ПОРОГА" in printed
    saved = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert saved["real_rows"] == 2 and saved["stamp"] == STAMP


def test_plan_says_the_stand_is_down(monkeypatch, capsys):
    """Стенд не отвечает — код 10 и словами, что делать, а не стек."""
    monkeypatch.setattr(emucart, "_http", lambda: FakeSession(gate=ConnectionError("нет")))

    assert emucart.main(["plan"]) == emucart.EXIT_STAND
    assert "СТЕНД НЕ ПОДНЯТ" in capsys.readouterr().out


def test_wait_report_takes_only_its_own_run(monkeypatch, capsys):
    """Отчёт фоновой сборки или другой корзины за отчёт этого прогона не сходит (ревью, п. 8)."""
    reports = [{"store": "magnit", "at": "2026-10-18T16:40:06", "stamp": "deadbeef",
                "background": False, "planned": 1, "landed": 1, "items": []}]
    monkeypatch.setattr(emucart, "_last_report", lambda store, workplace: reports[-1])

    code = emucart.main(["wait-report", "--stamp", STAMP, "--since", "2026-10-18T16:00:00",
                         "--timeout", "0.3", "--interval", "0.1"])
    printed = capsys.readouterr().out

    assert code == emucart.EXIT_NOT_FILLED
    assert "не наш: отпечаток deadbeef" in printed

    reports[-1] = {"store": "magnit", "at": "2026-10-18T16:41:06", "stamp": STAMP,
                   "background": True, "planned": 1, "landed": 1, "items": []}
    code = emucart.main(["wait-report", "--stamp", STAMP, "--since", "2026-10-18T16:00:00",
                         "--timeout", "0.3", "--interval", "0.1"])
    printed = capsys.readouterr().out
    assert code == emucart.EXIT_NOT_FILLED
    assert "фоновой сборки" in printed


def test_wait_report_shows_its_own_and_calls_a_human_for_login(monkeypatch):
    """Свой отчёт показывается; `why` про вход — код 20 (п. 2а плана)."""
    report = {"store": "magnit", "at": "2026-10-18T16:40:06", "stamp": STAMP,
              "background": False, "planned": 1, "landed": 0, "cart_count": 0,
              "items": [{"name": "Колбаса", "sku": "1", "ok": False, "put": 0, "times": 1,
                         "how": "tap", "why": "сайт увёл на страницу входа"}]}
    monkeypatch.setattr(emucart, "_last_report", lambda store, workplace: report)

    assert emucart.main(["wait-report", "--stamp", STAMP, "--since", "2026-10-18T16:00:00",
                         "--timeout", "5", "--interval", "0.1"]) == emucart.EXIT_HUMAN


def test_snap_preview_refuses_to_steer_the_apps_own_window(monkeypatch, capsys):
    """Без страницы Магнита шаг не навигирует окно приложения, а зовёт человека (ревью, п. 18)."""
    monkeypatch.setattr(emucart, "_cdp_targets", lambda cdp: [
        {"type": "page", "url": "http://10.0.2.2:8611/phone/basket",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/A"}])
    called = []
    monkeypatch.setattr(emucart, "_preview_from_page",
                        lambda *a, **kw: called.append(a) or {})

    code = emucart.main(["snap-preview", "--out", "preview-after.json"])

    assert code == emucart.EXIT_HUMAN
    assert called == []
    assert "в WebView нет страницы magnit.ru" in capsys.readouterr().out


async def _facts_stub(socket, timeout=0):
    return {"url": "https://magnit.ru/ecom/cart", "cookies": [], "session_keys": [],
            "storage_count": 0, "strong": False, "anon": False, "guard": False}


async def _resolved_answer(*a, **kw):
    return {"body": json.dumps(raw_preview([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)])),
            "url": f"https://magnit.ru{emucart.PREVIEW_PATH}?x=1",
            "request_id": "42", "status": 200, "why": ""}


def test_snap_preview_writes_a_bound_snapshot(tmp_path, monkeypatch, capsys):
    """Удачный снимок несёт привязку к прогону и очищен от личного."""
    monkeypatch.setattr(emucart, "_cdp_targets", lambda cdp: [
        {"type": "page", "url": "https://magnit.ru/ecom/cart",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/B"}])
    monkeypatch.setattr(emucart, "_preview_from_page", _resolved_answer)
    monkeypatch.setattr(emucart, "_page_facts", _facts_stub)

    out = tmp_path / "preview-after.json"
    code = emucart.main(["snap-preview", "--out", str(out)])

    assert code == emucart.EXIT_DONE
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["captured_at"] and saved["request_id"] == "42" and saved["status"] == 200
    assert saved["url"] == f"https://magnit.ru{emucart.PREVIEW_PATH}"
    assert ADDRESS not in json.dumps(saved, ensure_ascii=False)


def test_snap_preview_keeps_raw_output_out_of_the_repository(capsys):
    """Сырой ответ и снимок экрана в репозиторий не пишутся — отказ до всякого CDP."""
    inside = os.path.join(ROOT, "docs", "raw.json")

    assert emucart.main(["snap-preview", "--out", "p.json", "--raw-out", inside]) == \
        emucart.EXIT_STAND
    assert "нельзя писать в репозиторий" in capsys.readouterr().out

    assert emucart.main(["snap-preview", "--out", "p.json", "--shot",
                         os.path.join(ROOT, "shot.png")]) == emucart.EXIT_STAND
    assert "нельзя писать в репозиторий" in capsys.readouterr().out


def test_check_login_prints_the_verdict_and_needs_no_channel(monkeypatch, capsys):
    """`check-login` — вердикт по сессии: код 0, когда проверка сделана.

    Живой CDP в тестах не поднимается: окно отладки подставлено, а когда его нет —
    инструмент обязан сказать это словами и не притвориться, что вход проверен.
    """
    monkeypatch.setattr(emucart, "_cdp_targets", lambda cdp: [])
    assert emucart.main(["check-login", "--cdp", "http://127.0.0.1:9222"]) == emucart.EXIT_STAND
    assert "ВЕРДИКТ" not in capsys.readouterr().out

    monkeypatch.setattr(emucart, "_cdp_targets",
                        lambda cdp: [{"type": "page", "url": "https://magnit.ru"}])
    assert emucart.main(["check-login"]) == emucart.EXIT_STAND
    assert "ВЕРДИКТ" not in capsys.readouterr().out

    monkeypatch.setattr(emucart, "_cdp_targets", lambda cdp: [
        {"type": "page", "url": "https://magnit.ru/ecom/cart?uid=1",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/B"}])

    async def live_session(socket, timeout=0):
        return {"url": "https://magnit.ru/ecom/cart", "cookies": ["mg_sh"],
                "session_keys": ["au_atid", "au_expd", "au_phnu"], "storage_count": 47,
                "strong": False, "anon": False, "guard": False}

    monkeypatch.setattr(emucart, "_page_facts", live_session)
    assert emucart.main(["check-login"]) == emucart.EXIT_DONE
    printed = capsys.readouterr().out
    assert "login=yes" in printed and "mg_at=not-visible" in printed
    assert "вошёл" in printed and "не вошёл" not in printed
    # Адрес печатается без параметров: в query бывает метка человека.
    assert "uid=1" not in printed

    async def nothing_visible(socket, timeout=0):
        return {"url": "https://magnit.ru/ecom/cart", "cookies": [], "session_keys": [],
                "storage_count": 3, "strong": False, "anon": False, "guard": False}

    monkeypatch.setattr(emucart, "_page_facts", nothing_visible)
    assert emucart.main(["check-login"]) == emucart.EXIT_DONE
    printed = capsys.readouterr().out
    assert "login=unknown" in printed and "не знаю" in printed
    assert emucart.main(["check-login", "--require"]) == emucart.EXIT_HUMAN
    assert "НУЖЕН ЧЕЛОВЕК" in capsys.readouterr().out


def test_export_copies_only_sanitized(tmp_path, capsys):
    """Сырой ответ preview в репозиторий не идёт, а очищенный — идёт.

    Заодно сторожится разбор команды: у export есть свой ключ --run, и он однажды уже
    затирал имя обработчика — команда падала с TypeError вместо отказа.
    """
    run = tmp_path / "run"
    run.mkdir()
    raw = raw_preview([(SKU_A, "Алёнка шоколад 200 г", 3, 7999)])
    (run / "preview-after.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    (run / "report.json").write_text("{}", encoding="utf-8")

    assert emucart.main(["export", "--run", str(run),
                         "--to", str(tmp_path / "to")]) == emucart.EXIT_STAND
    assert "ОТКАЗ" in capsys.readouterr().out
    assert not (tmp_path / "to" / "preview-after.json").exists()

    (run / "preview-after.json").write_text(
        json.dumps(emucart.sanitize_preview(raw), ensure_ascii=False), encoding="utf-8")
    assert emucart.main(["export", "--run", str(run),
                         "--to", str(tmp_path / "to")]) == emucart.EXIT_DONE
    assert (tmp_path / "to" / "preview-after.json").exists()
    assert (tmp_path / "to" / "report.json").exists()


def test_export_cleans_plan_and_report_by_whitelist(tmp_path):
    """plan.json и report.json переписываются по белому списку: чужое поле не уедет."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "plan.json").write_text(json.dumps({
        "store": "magnit", "stamp": STAMP,
        "rows": [{"sku": "1", "name": "А", "url": "https://magnit.ru/p/1?x=1"}],
        "кука": "mg_at=SECRET", "адрес": ADDRESS}), encoding="utf-8")
    (run / "report.json").write_text(json.dumps({
        "store": "magnit", "planned": 1, "landed": 1, "background": False,
        "items": [{"sku": "1", "name": "А", "ok": True, "times": 1, "how": "tap"}],
        "чужое": ADDRESS}), encoding="utf-8")

    assert emucart.main(["export", "--run", str(run),
                         "--to", str(tmp_path / "to")]) == emucart.EXIT_DONE

    plan = json.loads((tmp_path / "to" / "plan.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "to" / "report.json").read_text(encoding="utf-8"))
    assert "кука" not in plan and "адрес" not in plan
    assert plan["rows"][0]["url"] == "https://magnit.ru/p/1"
    assert "чужое" not in report and report["landed"] == 1


def test_export_cleans_logs(tmp_path, capsys):
    """Журнал перед выкладкой чистится: строки про корзину остаются, вход — нет."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "report.json").write_text("{}", encoding="utf-8")
    (run / "logcat-korzina.txt").write_text(
        "I Korzina: кладу 1 из 4\n"
        "I Korzina: cookie mg_at=SECRETVALUE\n"
        "I Other: чужая строка\n"
        "I Korzina: отчёт ушёл, тел 79995550001\n", encoding="utf-8")

    assert emucart.main(["export", "--run", str(run), "--to", str(tmp_path / "to"),
                         "--logs"]) == emucart.EXIT_DONE

    cleaned = (tmp_path / "to" / "logcat-korzina.txt").read_text(encoding="utf-8")
    assert "кладу 1 из 4" in cleaned
    assert "SECRETVALUE" not in cleaned and "79995550001" not in cleaned
    assert "чужая строка" not in cleaned
    assert "выброшено строк: 3" in capsys.readouterr().out


def test_clean_log_stands_alone():
    """Чистка журнала: без тега не берём, с признаком входа — тоже."""
    text = "Korzina: ок\nphonecart.last: ок\ncookie: нет\nBearer abc\nKorzina: почта a@b.ru\n"

    cleaned, dropped = emucart.clean_log(text)

    assert cleaned.splitlines() == ["Korzina: ок", "phonecart.last: ок"]
    assert dropped == 3


def test_a_missing_output_directory_is_a_clear_refusal(tmp_path, capsys):
    """Опечатка в пути к файлу — понятный отказ, а не трассировка и не пустая папка.

    Так и вышло на приёмочном прогоне 18.10.2026: `--out` в ещё не созданный каталог
    дал стек Python, и это читалось как поломка инструмента.
    """
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    missing = tmp_path / "нет-такого-каталога" / "verdict.json"

    assert emucart.main(["verdict", "--report", str(tmp_path / "report.json"),
                         "--out", str(missing)]) == emucart.EXIT_STAND
    assert "каталога для записи нет" in capsys.readouterr().out
    assert not (tmp_path / "нет-такого-каталога").exists()
