"""Серверный поиск товара в сети (app/storefind.py).

Зачем: телефон ищет в витрине и упирается в стены (403 у Ленты и Дикси, пустая SPA у
Магнита, «Выключите VPN» после серии запросов), а сервер ходит рабочими дорогами:
Магнит — JSON-шлюз, Лента — их MCP, Дикси — Diginetica.

Правило владельца: положить не тот товар хуже, чем не положить. Поэтому «похожее» — это
НЕ успех: ответ несёт `exact: False` и различие словами (нет слов / лишние / фасовка).
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import storefind  # noqa: E402
from app.catalog import store  # noqa: E402
from app.models import Candidate  # noqa: E402


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    storefind._LAST.clear()
    # Пауз в тестах не ждём, но набор сетей оставляем: по нему find понимает, у кого
    # серверная дорога есть, а у кого нет.
    monkeypatch.setattr(storefind, "PAUSE",
                        {"magnit": 0.0, "lenta": 0.0, "dixy": 0.0})
    return tmp_path


def _cand(sku: str, name: str, score: float, weight: float | None = None,
          price: float | None = None) -> Candidate:
    return Candidate(store_code="magnit", sku=sku, name=name, price=price,
                     weight_g=weight, url=f"https://magnit.ru/product/{sku}",
                     score=score)


def test_exact_match_is_ok(catalog, monkeypatch):
    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [
        _cand("1", "Молоко Parmalat 1,8% 1л безлактозное ультрапастеризованное",
              score=0.9, weight=1000, price=136.0)])

    answer = storefind.find("magnit", "Молоко Parmalat 1,8% 1л безлактозное "
                                      "ультрапастеризованное", pack_g=1000)

    assert answer["ok"] is True and answer["exact"] is True, answer
    assert answer["found"][0]["sku"] == "1" and answer["found"][0]["price"] == 136.0


def test_another_line_is_not_ok_and_says_why(catalog, monkeypatch):
    """«Parmalat Edge» — не наш товар: отказ с различием, а не тихое «нашлось».

    Марка общая («Parmalat»), поэтому ответ говорит «похожее под другим названием» и
    просит подтвердить связь человеком — но `ok` остаётся False: автоматически не кладём.
    """
    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [
        _cand("2", "Молоко ультрапастеризованное PARMALAT Edge 1,8%, 1000мл",
              score=0.82, weight=1000)])

    answer = storefind.find("lenta", "Молоко Parmalat 1,8% 1л безлактозное "
                                     "ультрапастеризованное", pack_g=1000)

    assert answer["ok"] is False and answer["exact"] is False, answer
    assert "подтвердите связь" in answer["why"], answer["why"]
    assert "edge" in answer["why"].lower(), "различие должно быть названо словами"
    assert answer["found"][0]["difference"], "различие должно быть в самом кандидате"


def test_wrong_pack_lowers_confidence(catalog, monkeypatch):
    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [
        _cand("3", "Молоко Parmalat 1,8%", score=0.95, weight=200)])

    answer = storefind.find("magnit", "Молоко Parmalat 1,8% 1л", pack_g=1000)

    assert answer["ok"] is False, answer
    assert "фасовка" in answer["why"], answer["why"]


def test_nothing_found_is_its_own_answer(catalog, monkeypatch):
    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [])

    answer = storefind.find("magnit", "Молоко Parmalat 1,8% 1л")

    assert answer["ok"] is False and answer["found"] == []
    assert answer["why"] == "нет товара в сети", answer["why"]


def test_response_is_cached_for_a_day(catalog, monkeypatch):
    calls: list[str] = []

    def search(code, name, lat, lon, address, limit=3):
        calls.append(name)
        return [_cand("1", "Молоко Parmalat 1,8% 1л", score=0.95, weight=1000)]

    monkeypatch.setattr(storefind, "_search", search)

    first = storefind.find("magnit", "Молоко Parmalat 1,8% 1л", pack_g=1000)
    second = storefind.find("magnit", "Молоко Parmalat 1,8% 1л", pack_g=1000)

    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1, "второй раз сеть не спрашиваем: кэш на сутки"


def test_chain_without_server_road_says_so(catalog):
    answer = storefind.find("samokat", "Молоко 1 л")

    assert answer["ok"] is False and answer["found"] == []
    assert "телефоном" in answer["why"], answer["why"]


def test_network_error_is_a_reason_not_a_crash(catalog, monkeypatch):
    def boom(*args, **kwargs):
        raise TimeoutError("сеть молчит")

    monkeypatch.setattr(storefind, "_search", boom)

    answer = storefind.find("dixy", "Молоко 1 л")

    assert answer["ok"] is False and "не ответила" in answer["why"], answer


def test_cart_link_matches_by_name_and_skips_the_unknown(catalog, monkeypatch):
    """Ссылка на корзину Ленты: артикул ищется по русскому имени, не найденное — в missing."""
    monkeypatch.setattr(storefind, "find", lambda code, name, pack_g=None, **kw: (
        {"ok": True, "found": [{"sku": "70492", "url": "", "name": name,
                                "confidence": 0.95, "difference": ""}]}
        if "parmalat" in name.lower() else
        {"ok": False, "found": [], "why": "точного товара нет"}))

    from app.connectors import lenta as lenta_connector
    seen: dict[str, object] = {}

    def cart_link(items, location):
        seen["items"] = items
        return "https://lenta.com/basket/?share_id=test"

    monkeypatch.setattr(lenta_connector, "cart_link", cart_link)

    answer = storefind.cart_link("lenta", [
        {"product_id": 10, "name": "Молоко Parmalat 1,8% 1 л", "qty": 1, "pack_g": 1000},
        {"product_id": 11, "name": "Молоко Neo 0,5% 950 г", "qty": 2, "pack_g": 950}])

    assert answer["ok"] is True and answer["url"].startswith("https://lenta.com/basket/")
    assert answer["matched"] == [10] and answer["missing"] == [11], answer
    assert seen["items"] == [(70492, 1.0, None)], seen["items"]
    assert "не вошло 1 поз." in answer["note"], answer["note"]


def test_cart_link_refuses_chains_without_the_road(catalog):
    answer = storefind.cart_link("magnit", [{"product_id": 1, "name": "Молоко 1 л"}])

    assert answer["ok"] is False and answer["url"] == ""
    assert "не даёт ссылку" in answer["why"], answer["why"]


def test_find_chains_gives_one_answer_per_store(catalog, monkeypatch):
    """Контракт для телефона: по каждой сети found/why, четыре разные правды в why."""
    def fake(store_code, name, pack_g=None, **kw):
        if store_code == "magnit":
            return {"ok": True, "exact": True, "why": "",
                    "found": [{"sku": "1000417765", "url": "https://magnit.ru/product/1000417765",
                               "name": "Молоко Parmalat 1,8% 1л", "pack_g": 1000,
                               "price": 136.0, "confidence": 0.95, "difference": ""}]}
        if store_code == "lenta":
            return {"ok": False, "exact": False,
                    "why": "у сети похожее под другим названием: «PARMALAT Edge 1,8%» "
                           "(отличие: лишние: edge) — подтвердите связь",
                    "found": [{"sku": "70492", "url": "", "name": "PARMALAT Edge 1,8%",
                               "pack_g": 1000, "price": 138.99, "confidence": 0.47,
                               "difference": "лишние: edge"}]}
        return {"ok": False, "exact": False, "why": "сеть не ответила (TimeoutError)",
                "found": []}

    monkeypatch.setattr(storefind, "find", fake)

    answer = storefind.find_chains("Молоко Parmalat 1,8% 1л", ["magnit", "lenta", "dixy"],
                                   weight_g=1000)

    states = answer["chains"]
    assert states["magnit"]["found"] is True and states["magnit"]["sku"] == "1000417765"
    assert states["magnit"]["why"] == ""
    assert states["lenta"]["found"] is False
    assert "под другим названием" in states["lenta"]["why"], states["lenta"]["why"]
    assert "сервер не смог" in states["dixy"]["why"], states["dixy"]["why"]
    assert "не нашли: lenta, dixy" in answer["note"], answer["note"]


def test_find_chains_says_when_there_is_no_product(catalog, monkeypatch):
    monkeypatch.setattr(storefind, "find", lambda code, name, **kw: {
        "ok": False, "exact": False, "why": "сеть ничего не нашла по этому названию",
        "found": []})

    answer = storefind.find_chains("Молоко 1 л", ["magnit"])

    assert answer["chains"]["magnit"]["found"] is False
    assert answer["chains"]["magnit"]["why"] == "нет товара в сети"


def test_candidates_carry_brand_and_pack_facts_not_a_verdict(catalog, monkeypatch):
    """`brand_match`/`pack_match` — ФАКТЫ, а не «тот же товар».

    Замер 21.10.2026: по марке и фасовке совпадают и чужие линейки («Parmalat Edge»,
    «Parmalat Natura Premium» — та же марка и тот же литр). Флаг `same_line` подсказал бы
    человеку «это ваш товар», а он не наш. Поэтому отдаём два факта, а вывод делает экран.
    """
    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [
        _cand("2", "Молоко ультрапастеризованное PARMALAT Edge 1,8%, 1000мл",
              score=0.82, weight=1000)])

    answer = storefind.find("lenta", "Молоко Parmalat 1,8% 1л безлактозное", pack_g=1000)

    best = answer["found"][0]
    assert best["brand_match"] is True, "марка общая — это факт"
    assert best["pack_match"] is True, "фасовка сходится — это факт"
    assert answer["ok"] is False, "но товар другой: класть нельзя"


def test_find_chains_passes_the_facts_through(catalog, monkeypatch):
    monkeypatch.setattr(storefind, "find", lambda code, name, **kw: {
        "ok": False, "exact": False, "why": "у сети похожее под другим названием",
        "found": [{"sku": "1000151768", "name": "Туалетная бумага Papia 3 слоя 8 рулонов",
                   "url": "https://magnit.ru/product/1000151768", "price": 199.0,
                   "pack_g": None, "confidence": 0.9, "difference": "нет слов: bali, flower",
                   "brand_match": True, "pack_match": None}]})

    answer = storefind.find_chains("Туалетная бумага Papia Bali Flower 3 слоя, 8 рулонов",
                                   ["magnit"])

    state = answer["chains"]["magnit"]
    assert state["brand_match"] is True and state["pack_match"] is None
    assert state["difference"] == "нет слов: bali, flower"


def test_hint_is_a_sentence_from_facts_not_a_verdict(catalog, monkeypatch):
    """Готовая строка для человека: «марка совпала, фасовка неизвестна … — проверьте»."""
    monkeypatch.setattr(storefind, "find", lambda code, name, **kw: {
        "ok": False, "exact": False, "why": "у сети похожее под другим названием",
        "found": [{"sku": "1000151768", "name": "Туалетная бумага Papia 3 слоя 8 рулонов",
                   "url": "https://magnit.ru/product/1000151768", "price": 199.0,
                   "pack_g": None, "confidence": 0.9,
                   "difference": "нет слов: bali, flower",
                   "brand_match": True, "pack_match": None}]})

    answer = storefind.find_chains("Туалетная бумага Papia Bali Flower 3 слоя, 8 рулонов",
                                   ["magnit"])

    hint = answer["chains"]["magnit"]["hint"]
    assert "марка совпала" in hint and "проверьте" in hint, hint
    assert "это ваш" not in hint.lower(), "подсказка не должна уверять, что товар тот же"


def test_hint_is_empty_when_found(catalog, monkeypatch):
    monkeypatch.setattr(storefind, "find", lambda code, name, **kw: {
        "ok": True, "exact": True, "why": "",
        "found": [{"sku": "1", "name": "Молоко 1 л", "url": "https://x/1", "price": 99.0,
                   "pack_g": 1000, "confidence": 0.95, "difference": "",
                   "brand_match": True, "pack_match": True}]})

    answer = storefind.find_chains("Молоко 1 л", ["magnit"])

    assert answer["chains"]["magnit"]["found"] is True
    assert answer["chains"]["magnit"]["hint"] == ""



def test_single_chain_find_also_gives_the_hint(catalog, monkeypatch):
    """`hint` нужен и в односетевой `find`: телефон зовёт именно её.

    Замер 21.10.2026: сборка ходит по одной сети за строку, и без `hint` в отчёт попадал
    только технический `difference` («нет слов: bali, flower») вместо человеческой фразы.
    """
    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [
        _cand("1000151768", "Туалетная бумага Papia 3 слоя 8 рулонов", score=0.9)])

    answer = storefind.find("magnit", "Туалетная бумага Papia Bali Flower 3 слоя, 8 рулонов")

    assert answer["ok"] is False
    assert answer["hint"], "подсказка должна быть в односетевой find"
    assert "проверьте" in answer["hint"], answer["hint"]
    assert "марка совпала" in answer["hint"], "фраза должна начинаться с факта, а не с difference"


def test_single_chain_find_hint_is_empty_when_found(catalog, monkeypatch):
    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [
        _cand("1", "Молоко Parmalat 1,8% 1л", score=0.95, weight=1000)])

    answer = storefind.find("magnit", "Молоко Parmalat 1,8% 1л", pack_g=1000)

    assert answer["ok"] is True and answer["hint"] == ""



def test_cache_version_bump_invalidates_old_shape(catalog, monkeypatch):
    """Старая запись кэша (другая версия формы) не отдаётся — иначе ответ без `hint`.

    Замер 21.10.2026: `hint` добавили в `find`, но кэш версии 2 продолжал отдавать ответ
    без него сутки, и в отчёте телефона причина оставалась старой.
    """
    import json
    import time

    monkeypatch.setattr(storefind, "_search", lambda code, name, lat, lon, address, limit=3: [
        _cand("1", "Молоко Parmalat 1,8% 1л", score=0.95, weight=1000)])

    with store.connect() as conn:      # кладём запись «старой формы»
        conn.execute("INSERT INTO storefind_cache (store, name_key, payload, at)"
                     " VALUES ('magnit', ?, ?, ?)",
                     ("молоко parmalat 1,8% 1л",
                      json.dumps({"ok": True, "found": [], "v": 2}), time.time()))
        conn.commit()

    answer = storefind.find("magnit", "Молоко Parmalat 1,8% 1л", pack_g=1000)

    assert answer["cached"] is False, "запись другой версии — промах, а не попадание"
    assert "hint" in answer, "свежий ответ обязан нести hint"
