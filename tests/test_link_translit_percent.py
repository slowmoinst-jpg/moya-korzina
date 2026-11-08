"""Мастер единой базы и транслит Перекрёстка: процент без «%» (замер 08.11.2026).

ЗАЧЕМ. Строки Перекрёстка собраны из адреса карточки, в них нет «%» и запятой:
«moloko prostokvashino pasterizovannoe 1 5 930ml» — это 1,5 %. Процент у такой строки
не читается, и мастер связывал «Молоко Простоквашино 2,5 %» с 1,5 %, а «Село Зелёное
2,5 %» — с «…6 950ml». Теперь числа такой строки сверяются с процентом товара человека
(link._numbers_disagree, разбор чисел — x5.numbers).

Тот же хвост «kefir … 3 2 1kg» сверка X5 читала как 2,1 кг и теряла верную пару
«Кефир 3,2 % 1 кг»: «1l»/«1kg» после ненулевой цифры — целый литр, а цифра — процент.
"""
from __future__ import annotations

import pytest

from app import config, repo
from app.catalog import link, refresh, store, x5
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.importers.ofd_pdf import _ensure_product
from app.models import Product


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN):
        memo.clear()
    yield
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN):
        memo.clear()


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def ids() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def bought(raw: str, qty: float = 1) -> int:
    """Товар, заведённый как при загрузке чека: эталон — display_name строки."""
    pid, _ = _ensure_product(raw, qty)
    repo.add_history_row("2026-10-10", None, pid, raw, qty, 99.0, 99.0, f"k-{pid}-{raw}")
    return pid


def sku(pid: int, chain: str) -> str | None:
    found = repo.confirmed_mapping(pid, ids()[chain])
    return found["sku"] if found else None


@pytest.mark.parametrize(
    ("dixy_name", "dixy_brand", "perek_sku", "perek_name", "receipt_name", "expected_perek"),
    [
        (
            "Молоко Простоквашино пастеризованное 2,5% 930 мл",
            "Простоквашино",
            "p15",
            "moloko prostokvashino pasterizovannoe 1 5 930ml",
            "Молоко Простоквашино 2,5% 930 мл",
            None,
        ),
        (
            "Молоко Село Зелёное пастеризованное 2,5% 950 мл",
            "Село Зелёное",
            "p6",
            "moloko selo zelenoe pasterizovannoe 6 950ml",
            "Молоко Село Зелёное 2,5% 950 мл",
            None,
        ),
        (
            "Молоко Простоквашино пастеризованное 2,5% 930 мл",
            "Простоквашино",
            "p25",
            "moloko prostokvashino pasterizovannoe 2 5 930ml",
            "Молоко Простоквашино 2,5% 930 мл",
            "p25",
        ),
        (
            "Молоко Село Зелёное пастеризованное 2,5% 950 мл",
            "Село Зелёное",
            "p25",
            "moloko selo zelenoe pasterizovannoe 2 5 950ml",
            "Молоко Село Зелёное 2,5% 950 мл",
            "p25",
        ),
    ],
)
def test_translit_row_links_only_with_the_same_percent(
    catalog, user_db, dixy_name, dixy_brand, perek_sku, perek_name, receipt_name, expected_perek
):
    """Проверка связи транслита Перекрёстка: чужой процент не связывается, верный — связывается."""
    store.upsert_products("dixy", [P("d1", dixy_name, brand=dixy_brand)])
    store.upsert_products("perekrestok", [P(perek_sku, perek_name)])
    refresh.match_all()
    pid = bought(receipt_name)
    link.link_products()
    assert sku(pid, "dixy") == "d1"
    assert sku(pid, "perekrestok") == expected_perek


def test_perekrestok_two_candidates_picks_matching_percent(catalog, user_db):
    """Из двух строк Перекрёстка с разными процентами выбирается строка с совпадающим процентом."""
    store.upsert_products(
        "dixy",
        [P("d1", "Молоко Простоквашино пастеризованное 2,5% 930 мл", brand="Простоквашино")],
    )
    store.upsert_products(
        "perekrestok",
        [
            P("p15", "moloko prostokvashino pasterizovannoe 1 5 930ml"),
            P("p25", "moloko prostokvashino pasterizovannoe 2 5 930ml"),
        ],
    )
    refresh.match_all()
    pid = bought("Молоко Простоквашино 2,5% 930 мл")
    link.link_products()
    assert sku(pid, "dixy") == "d1"
    assert sku(pid, "perekrestok") == "p25"


def test_agrees_numbers_on_prepared_rows():
    """_agrees отвергает транслит с чужими числами при названном проценте человека."""
    mine = link._prep({"id": 0, "chain": "mine", "name": "КЕФИР ДОМИК В ДЕРЕВНЕ 3,2% 1КГ"}, receipt=True)
    cand_ok = link._prep({"id": 1, "chain": "perekrestok", "name": "kefir domik v derevne 3 2 1kg"})
    cand_wrong = link._prep({"id": 2, "chain": "perekrestok", "name": "kefir domik v derevne 2 5 1kg"})
    assert link._agrees([mine], cand_ok) is True
    assert link._agrees([mine], cand_wrong) is False

    mine_no_pct = link._prep({"id": 3, "chain": "mine", "name": "КЕФИР ДОМИК В ДЕРЕВНЕ 1КГ"}, receipt=True)
    assert link._agrees([mine_no_pct], cand_ok) is True
    assert link._agrees([mine_no_pct], cand_wrong) is True


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("kefir domik v derevne 3 2 1kg", ["2", "3"]),
        ("kefir domik v derevne 1 1kg", ["1"]),
        ("moloko domik v derevne pasterizovannoe 2 5 1 4l", ["2", "5"]),
        ("moloko domik v derevne pasterizovannoe 3 7 0 93l", ["3", "7"]),
        ("moloko bolsaa kruzka ultrapasterizovannoe 2 5 1 45l", ["2", "5"]),
        ("moloko prostokvashino pasterizovannoe 1 5 930ml", ["1", "5"]),
        ("voda svyatoj istochnik 1 5l", []),
        ("konak staryj kenigsberg 40 0 1l", ["40"]),
        ("Молоко Простоквашино 2,5% 930 мл", ["2", "5"]),
    ],
)
def test_x5_numbers(raw_name, expected):
    """x5.numbers извлекает числа названия без фасовки и штук в отсортированном виде."""
    assert x5.numbers(raw_name) == expected


@pytest.mark.parametrize(
    ("raw_name", "grams"),
    [
        ("kefir domik v derevne 3 2 1kg", 1000),
        ("kefir domik v derevne 1 1kg", 1000),
        ("moloko klever ultrapasterizovannoe 6 1l", 1000),
        ("voda svyatoj istochnik 1 5l", 1500),
        ("moloko bolsaa kruzka ultrapasterizovannoe 2 5 1 45l", 1450),
        ("moloko domik v derevne pasterizovannoe 3 7 0 93l", 930),
        ("konak staryj kenigsberg 40 0 1l", 100),
    ],
)
def test_x5_weight_of_reads_a_whole_litre_after_a_percent(raw_name, grams):
    """Дробь «1 5l» — 1,5 л, а «3 2 1kg» — 3,2 % и 1 кг, не 2,1 кг."""
    assert x5.weight_of(raw_name, None) == pytest.approx(grams)


def test_x5_finds_a_kefir_with_percent_before_a_whole_kilo(catalog, user_db):
    """Сверка X5 берёт «kefir … 3 2 1kg» для «Кефир 3,2 % 1 кг», а «…2 5 1kg» — нет."""
    store.upsert_products("perekrestok", [
        P("3001", "kefir domik v derevne 3 2 1kg", weight_g=1000.0),
        P("3002", "kefir domik v derevne 2 5 1kg", weight_g=1000.0),
    ])
    p = Product(id=1, name="Кефир Домик в деревне 3,2% 1 кг", brand="Домик в деревне", weight_g=1000.0)
    with store.connect() as conn:
        assert x5.find_sku(p, x5.build_index(conn)) == ("3001", False)
