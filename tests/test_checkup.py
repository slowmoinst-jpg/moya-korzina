"""Проверка здоровья базы: поломку видно в ту же ночь, а не через неделю.

Смысл: обход умеет «пройти успешно» и ничего не собрать (у Пятёрочки и Самоката так и
было: `blocked` при нуле строк). Ручной взгляд каждый день не случится — значит проверка
должна быть командой.
"""
from __future__ import annotations

import json

import pytest

from app.catalog import checkup, store


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _row(chain: str, sku: str, *, price=None, stock=None, item_id=None) -> None:
    with store.connect() as conn:
        conn.execute(
            "insert into chain_products (chain, sku, name, price, in_stock, item_id, active,"
            " first_seen, last_seen) values (?,?,?,?,?,?,1,?,?)",
            (chain, sku, f"Товар {sku}", price, stock, item_id, store.now(), store.now()))
        conn.commit()


def _aged(chain: str, sku: str, *, price=None, days: float = 30.0) -> None:
    """Строка, которую завели days дней назад; даты её цены нет (так лежали 10 074 строки)."""
    from datetime import datetime, timedelta

    stamp = (datetime.now() - timedelta(days=days)).isoformat(timespec="microseconds")
    with store.connect() as conn:
        conn.execute(
            "insert into chain_products (chain, sku, name, price, active, first_seen, last_seen)"
            " values (?,?,?,?,1,?,?)",
            (chain, sku, f"Товар {sku}", price, stamp, store.now()))
        conn.commit()


def _run(chain: str, status: str, *, started: str | None = None) -> None:
    with store.connect() as conn:
        conn.execute("insert into crawl_runs (chain, started_at, finished_at, status)"
                     " values (?,?,?,?)",
                     (chain, started or store.now(), store.now(), status))
        conn.commit()


def test_a_snapshot_counts_what_is_in_the_base(base):
    _row("lenta", "1", price=199.0, stock=1)
    _row("lenta", "2", price=None)
    _run("lenta", "ok")

    snap = checkup.snapshot()

    assert snap["chains"]["lenta"]["products"] == 2
    assert snap["chains"]["lenta"]["priced"] == 1
    assert snap["chains"]["lenta"]["stocked"] == 1


def test_a_chain_with_no_rows_but_an_ok_run_is_a_complaint(base):
    """Так выглядит поломка: обход «прошёл», а товаров нет."""
    _run("pyaterochka", "ok")

    snap = checkup.snapshot()
    notes = checkup.complaints(snap, None)

    assert any("pyaterochka" in note and "строк ноль" in note for note in notes), notes


def test_a_big_drop_of_rows_is_a_complaint(base):
    _row("dixy", "1", price=10.0)
    now_snap = checkup.snapshot()
    before = {"chains": {"dixy": {"products": 100, "priced": 100}}, "comparable": 0}

    notes = checkup.complaints(now_snap, before)

    assert any("меньше на" in note and "dixy" in note for note in notes), notes


def test_a_fallen_price_coverage_is_a_complaint(base):
    for i in range(4):
        _row("vprok", str(i), price=None)
    now_snap = checkup.snapshot()
    before = {"chains": {"vprok": {"products": 4, "priced": 4}}, "comparable": 0}

    notes = checkup.complaints(now_snap, before)

    assert any("доля строк с ценой упала" in note for note in notes), notes


def test_a_stale_chain_is_a_complaint(base):
    _row("monetka", "1", price=10.0)
    old = "2026-01-01T00:00:00.000000"
    _run("monetka", "ok", started=old)
    with store.connect() as conn:
        conn.execute("update crawl_runs set finished_at=? where chain='monetka'", (old,))
        conn.commit()

    notes = checkup.complaints(checkup.snapshot(), None)

    assert any("не был" in note for note in notes), notes


def test_a_healthy_base_has_no_complaints(base):
    _row("lenta", "1", price=199.0, stock=1)
    _run("lenta", "ok")

    assert checkup.complaints(checkup.snapshot(), None) == []


def test_a_price_that_was_seen_but_is_gone_is_a_complaint(base):
    """price_seen стоит, цены нет — кто-то стёр значение, не тронув отметку.

    Замер 20.10.2026: у Самоката price_seen был у 1 494 строк, а цены у 884. Товар выпал
    из сравнения, и по одной «доле цен» это видно лишь как просадка.
    """
    with store.connect() as conn:
        conn.execute(
            "insert into chain_products (chain, sku, name, price, price_seen, active,"
            " first_seen, last_seen) values ('samokat','1','Товар',NULL,?,1,?,?)",
            (store.now(), store.now(), store.now()))
        conn.commit()

    snap = checkup.snapshot()
    notes = checkup.complaints(snap, None)

    assert snap["price_gaps"]["samokat"]["gap"] == 1
    assert any("цену видели" in note and "samokat" in note for note in notes), notes


def test_a_row_that_never_had_a_price_is_not_a_complaint(base):
    """Цены не было вовсе — это не «стёрли»: так бывает у источников без цен."""
    _row("monetka", "1")

    snap = checkup.snapshot()
    notes = checkup.complaints(snap, None)

    assert "monetka" not in (snap.get("price_gaps") or {})
    assert not any("цену видели" in note for note in notes)


def test_the_snapshot_counts_prices_older_than_a_week(base):
    """Свежесть цены видна в снимке: иначе «сколько цен устарело» не спросить.

    Замер 20.10.2026: у ВкусВилла 3 132 цены из 3 305 были старше недели, а сборщик
    знакомые карточки не перечитывал вовсе.
    """
    _aged("vkusvill", "old", price=129.0, days=30)
    _aged("vkusvill", "fresh", price=167.0, days=0)
    _aged("vkusvill", "bare", price=None, days=30)

    info = checkup.snapshot()["chains"]["vkusvill"]

    assert info["priced"] == 2
    assert info["stale_week"] == 1, "без цены — не «старая цена», это другая беда"


def test_a_frozen_price_cycle_is_a_complaint(base):
    """Виток свежести встал: старых цен много и их число не уменьшилось."""
    for i in range(6):
        _aged("vkusvill", f"p{i}", price=100.0 + i, days=20)
    _aged("vkusvill", "fresh", price=90.0, days=0)
    now_snap = checkup.snapshot()
    before = {"chains": {"vkusvill": {"products": 7, "priced": 7, "stale_week": 6}},
              "comparable": 0}

    notes = checkup.complaints(now_snap, before)

    assert any("виток свежести цен не идёт" in note for note in notes), notes


def test_a_working_price_cycle_is_not_a_complaint(base):
    """Виток идёт: старых цен много, но меньше, чем в прошлый раз — это норма."""
    for i in range(6):
        _aged("vkusvill", f"p{i}", price=100.0 + i, days=20)
    _aged("vkusvill", "fresh", price=90.0, days=0)
    now_snap = checkup.snapshot()
    before = {"chains": {"vkusvill": {"products": 7, "priced": 7, "stale_week": 15}},
              "comparable": 0}

    notes = checkup.complaints(now_snap, before)

    assert not any("виток свежести" in note for note in notes), notes


def _pair(base, left, right, *, price: float = 100.0, fresh: bool = True) -> None:
    """Один и тот же товар в двух сетях — сопоставленный, чтобы был сравнимый товар."""
    from app.catalog import refresh

    name = f"Сыр Страчателла {left}{right} 200 г"
    for chain, sku in ((left, f"{left}-1"), (right, f"{right}-1")):
        if fresh:
            _row(chain, sku, price=price)
        else:
            _aged(chain, sku, price=price, days=3)
        with store.connect() as conn:
            conn.execute("UPDATE chain_products SET name=? WHERE sku=?", (name, sku))
            conn.commit()
    refresh.match_all()


def test_comparable_has_two_numbers_and_the_second_is_honest(base):
    """Сравнимых в базе и сравнимых для приложения — разные числа.

    Замер 20.10.2026 18:15 MSK: в базе 7 111 сравнимых товаров, а приложение могло взять
    190 — цене старше prices.fresh_hours (6 ч) оно не верит вовсе (app/freshness.py).
    Одно число без второго читалось бы успехом там, где выбора у человека нет.
    """
    _pair(base, "lenta", "magnit", price=100.0, fresh=False)      # старая цена
    _pair(base, "dixy", "fixprice", price=120.0, fresh=True)      # свежая цена

    snap = checkup.snapshot()

    assert snap["comparable"] == 2, "по цене в базе сравнимых два товара"
    assert snap["comparable_fresh"] == 1, "приложению доступен только свежий"
    assert snap["fresh_hours"] == 6.0


def test_a_base_with_no_fresh_comparable_prices_is_a_complaint(base, monkeypatch):
    """Сравнимых много, а свежих ноль сразу после обхода — обход не записал цен."""
    monkeypatch.setattr(checkup, "FRESH_COMPARABLE_FLOOR", 0)
    _pair(base, "lenta", "magnit", price=100.0, fresh=False)
    store.mark_sweep("done")                      # обход только что закончился

    snap = checkup.snapshot()
    notes = checkup.complaints(snap, None)

    assert snap["comparable"] == 1 and snap["comparable_fresh"] == 0
    assert any("сравнивать нечего" in note for note in notes), notes


def test_no_fresh_prices_hours_after_a_sweep_is_not_a_complaint(base, monkeypatch):
    """К вечеру утренние цены просрочены — это норма, а не поломка обхода."""
    monkeypatch.setattr(checkup, "FRESH_COMPARABLE_FLOOR", 0)
    _pair(base, "lenta", "magnit", price=100.0, fresh=False)
    with store.connect() as conn:
        conn.execute("INSERT INTO catalog_state (key, value, at) VALUES ('sweep','done',?)",
                     ("2026-10-20T03:40:00.000000",))
        conn.commit()

    snap = checkup.snapshot()
    notes = checkup.complaints(snap, None)

    assert snap["comparable_fresh"] == 0
    assert not any("сравнивать нечего" in note for note in notes), notes


def test_the_report_shows_both_comparable_numbers(base):
    _pair(base, "lenta", "magnit", price=100.0, fresh=False)
    _pair(base, "dixy", "fixprice", price=120.0, fresh=True)

    text = checkup.render(checkup.snapshot(), [])

    assert "приложение может взять" in text


def test_growth_is_counted_per_chain(base):
    """«Наполнение для всех магазинов» проверяется по каждой сети, а не суммой.

    Замер 20.10.2026: у Vprok последняя новая строка 20.09, и это не наша недосборка —
    в его карте сайта все даты lastmod из 2024 года. Без таких чисел факт «сеть не растёт»
    не отличить от «обход сломался».
    """
    _row("lenta", "fresh", price=100.0)                       # заведена сейчас
    _aged("vprok", "old", price=None, days=40)                 # заведена 40 дней назад

    snap = checkup.snapshot()

    assert snap["chains"]["lenta"]["new_week"] == 1
    assert snap["chains"]["vprok"]["new_week"] == 0
    assert snap["chains"]["vprok"]["last_new"]


def test_the_report_says_when_a_source_has_frozen(base):
    """Сеть с нулём новых месяц — не поломка обхода, а застывший источник, и так и пишем."""
    _aged("vprok", "old", price=None, days=40)
    _row("lenta", "fresh", price=100.0)

    text = checkup.render(checkup.snapshot(), [])

    assert "рост базы" in text
    assert "источник застыл" in text
    # у живой сети такой оговорки быть не должно
    line = next(l for l in text.splitlines() if "lenta" in l and "за неделю" in l)
    assert "источник застыл" not in line, line


def test_the_snapshot_says_how_long_the_night_took(base):
    """Ночь целиком: непрерывная цепочка обходов. Замер 20.10.2026 — 8 ч."""
    _row("magnit", "1", price=100.0)
    _row("lenta", "2", price=100.0)
    _run("magnit", "ok", started="2026-10-20T03:30:00")
    _run("lenta", "ok", started="2026-10-20T04:20:00")
    with store.connect() as conn:
        conn.execute("UPDATE crawl_runs SET finished_at='2026-10-20T04:07:00' WHERE chain='magnit'")
        conn.execute("UPDATE crawl_runs SET finished_at='2026-10-20T10:09:00' WHERE chain='lenta'")
        conn.commit()

    night = checkup.snapshot()["night"]

    assert night["day"] == "2026-10-20"
    assert night["from"].endswith("03:30") and night["to"].endswith("10:09")
    assert night["hours"] == 6.65
    assert night["chains"] == 2
    assert night["longest"] == {"chain": "lenta", "minutes": 349}


def test_a_manual_run_outside_the_night_does_not_stretch_it(base):
    """Ручной прогон днём — не часть ночи: иначе «ночь» покажет весь день.

    Замер 20.10.2026: ночь 03:30 → 11:32, потом ручная проверка Самоката в 17:59. Счёт
    «от первого до последнего прогона за день» дал 14,5 ч и ложную тревогу.
    """
    _row("magnit", "1", price=100.0)
    _row("lenta", "2", price=100.0)
    _row("samokat", "3", price=100.0)
    _run("magnit", "ok", started="2026-10-20T03:30:00")
    _run("lenta", "ok", started="2026-10-20T04:20:00")
    _run("samokat", "partial", started="2026-10-20T17:59:00")
    with store.connect() as conn:
        conn.execute("UPDATE crawl_runs SET finished_at='2026-10-20T04:07:00' WHERE chain='magnit'")
        conn.execute("UPDATE crawl_runs SET finished_at='2026-10-20T10:09:00' WHERE chain='lenta'")
        conn.execute("UPDATE crawl_runs SET finished_at='2026-10-20T17:59:04' WHERE chain='samokat'")
        conn.commit()

    night = checkup.snapshot()["night"]

    assert night["chains"] == 2, "ручной прогон в ночь не входит"
    assert night["to"].endswith("10:09") and night["hours"] == 6.65


def test_a_night_that_swallowed_the_day_is_a_complaint(base, monkeypatch):
    """Ночь длиной больше половины суток — уже не «постоянная основа», а догоняющая."""
    monkeypatch.setattr(checkup, "NIGHT_LONG_HOURS", 1.0)
    _row("magnit", "1", price=100.0)
    _run("magnit", "ok", started="2026-10-20T03:30:00")
    with store.connect() as conn:
        conn.execute("UPDATE crawl_runs SET finished_at='2026-10-20T11:32:00'")
        conn.commit()

    notes = checkup.complaints(checkup.snapshot(), None)

    assert any("ночь" in note and "перестаёт успевать" in note for note in notes), notes


def test_the_report_prints_the_night_line(base):
    _row("magnit", "1", price=100.0)
    _run("magnit", "ok", started="2026-10-20T03:30:00")
    with store.connect() as conn:
        conn.execute("UPDATE crawl_runs SET finished_at='2026-10-20T04:07:00'")
        conn.commit()

    text = checkup.render(checkup.snapshot(), [])

    assert "ночь 2026-10-20: 03:30 → 04:07" in text
    assert "дольше всех magnit" in text


def test_a_sweep_killed_midway_is_named_by_the_chain_it_stopped_on(base):
    """Обход убили — проверка базы говорит и о факте, и на какой сети оборвалось.

    Разбор 20.10.2026: ночь встала после Пятёрочки, Самокат не начался, и об этом не сказал
    никто: `finally` спасает только от исключений, а убитый процесс не оставляет ничего.
    """
    store.mark_sweep("running")
    store.mark_sweep_step("pyaterochka")
    with store.connect() as conn:                      # отметку «идёт» делаем старой
        conn.execute("UPDATE catalog_state SET at='2026-10-20T03:30:00.000000' WHERE key='sweep'")
        conn.commit()

    snap = checkup.snapshot()
    notes = checkup.complaints(snap, None)

    assert snap["sweep_step"]["chain"] == "pyaterochka"
    assert any("прошлая ночь оборвана на сети pyaterochka" in note for note in notes), notes


def test_a_sweep_that_stops_moving_is_caught_before_the_marker_goes_stale(base):
    """«Идёт обход», но последняя сеть трогалась 4 часа назад — обрыв, а не работа.

    Без этого признака обрыв в 04:00 и проверка в 08:00 молчали бы: 12-часовой порог
    «протухшей» отметки ещё не наступил.
    """
    store.mark_sweep("running")
    store.mark_sweep_step("lenta")
    with store.connect() as conn:                    # отметку «идёт» делаем свежей, шаг — старым
        conn.execute("UPDATE catalog_state SET at=? WHERE key='sweep'", (store.now(),))
        conn.execute("UPDATE catalog_state SET value=? WHERE key='sweep.chain'",
                     (json.dumps({"chain": "lenta", "at": "2026-10-20T03:30:00.000000"}),))
        conn.commit()

    snap = checkup.snapshot()
    notes = checkup.complaints(snap, None)

    assert snap["sweep_state"] == "running"
    assert any("оборвана" in note and "lenta" in note for note in notes), notes


def test_a_long_but_living_chain_is_not_a_stall(base):
    """METRO идёт два с лишним часа — это работа, а не обрыв."""
    store.mark_sweep("running")
    store.mark_sweep_step("metro")

    notes = checkup.complaints(checkup.snapshot(), None)

    assert not any("оборвана" in note for note in notes), notes


def test_a_finished_sweep_leaves_no_such_complaint(base):
    """Обход закончился — отметка не «идущая», замечания о обрыве нет."""
    store.mark_sweep("done")
    store.mark_sweep_step("vkusvill")

    notes = checkup.complaints(checkup.snapshot(), None)

    assert not any("оборвана" in note for note in notes), notes


def test_unmatched_rows_are_counted_and_a_stall_is_a_complaint(base, monkeypatch):
    """Строка без единого товара в сравнении не участвует — застой должен быть виден.

    Замер 20.10.2026: 26 строк из 308 670 (0,01 %) — норма. Тревога, когда не сопоставлено
    много строк И их доля выше процента: значит сопоставление перестало ходить.
    """
    monkeypatch.setattr(checkup, "UNMATCHED_FLOOR", 0)
    monkeypatch.setattr(checkup, "UNMATCHED_SHARE", 0.0)
    _row("lenta", "1", price=100.0)
    snap = checkup.snapshot()

    assert snap["unmatched"] == 1 and snap["products_total"] == 1
    notes = checkup.complaints(snap, None)

    assert any("не сопоставлено" in note and "не догоняет" in note for note in notes), notes


def test_a_small_remainder_is_not_a_complaint(base):
    """Порог по числу строк: на маленькой базе «100 % несопоставлено» — это не поломка."""
    _row("lenta", "1", price=100.0)

    notes = checkup.complaints(checkup.snapshot(), None)

    assert not any("не догоняет" in note for note in notes), notes


def test_a_matched_base_has_no_unmatched_complaint(base, monkeypatch):
    monkeypatch.setattr(checkup, "UNMATCHED_SHARE", 0.0)
    with store.connect() as conn:
        conn.execute("INSERT INTO catalog_items (name, norm, created_at, updated_at)"
                     " VALUES ('Сыр', 'сыр', ?, ?)", (store.now(), store.now()))
        item_id = conn.execute("SELECT MAX(id) FROM catalog_items").fetchone()[0]
        conn.execute("INSERT INTO chain_products (chain, sku, name, price, item_id, active,"
                     " first_seen, last_seen) VALUES ('lenta','1','Сыр',100,?,1,?,?)",
                     (item_id, store.now(), store.now()))
        conn.commit()

    notes = checkup.complaints(checkup.snapshot(), None)

    assert not any("не догоняет" in note for note in notes), notes


def test_the_report_says_how_many_rows_are_unmatched(base):
    _row("lenta", "1", price=100.0)

    text = checkup.render(checkup.snapshot(), [])

    assert "без единого товара 1" in text


def test_the_snapshot_says_what_a_price_less_chain_would_add(base):
    """Сколько НОВЫХ сравнений даст цена сети без цен (замер: Перекрёсток +4 399).

    Считаем строго: у этой сети цены нет, а у других сетей цена есть ровно у одной —
    двух уже достаточно для сравнения, и такие товары в счёт не идут.
    """
    _pair(base, "lenta", "perekrestok", price=100.0, fresh=True)
    with store.connect() as conn:                       # у Перекрёстка цены нет
        conn.execute("UPDATE chain_products SET price=NULL WHERE chain='perekrestok'")
        conn.commit()

    snap = checkup.snapshot()

    assert snap["waiting"] == {"perekrestok": 1}


def test_a_chain_whose_price_is_there_is_not_waiting(base):
    _pair(base, "lenta", "perekrestok", price=100.0, fresh=True)

    assert checkup.snapshot()["waiting"] == {}


def test_the_report_says_what_waits_for_a_chain_price(base):
    _pair(base, "lenta", "perekrestok", price=100.0, fresh=True)
    with store.connect() as conn:
        conn.execute("UPDATE chain_products SET price=NULL WHERE chain='perekrestok'")
        conn.commit()

    text = checkup.render(checkup.snapshot(), [])

    assert "ждут цены сети" in text and "Перекрёсток +1" in text


def test_the_snapshot_is_remembered_and_read_back(base):
    _row("lenta", "1", price=199.0)
    snap = checkup.snapshot()

    checkup.save(snap)

    again = checkup.previous()
    assert again is not None and again["chains"]["lenta"]["products"] == 1


def test_the_device_mark_is_shown_with_its_age(base, capsys):
    """Пятёрочка и Самокат живут телефоном: в отчёте видно, когда он приносил данные."""
    store.mark_device("samokat", "cards", 12, 7)

    snap = checkup.snapshot()
    text = checkup.render(snap, checkup.complaints(snap, None))

    assert snap["device"]["samokat"]["got"] == 12
    assert "телефон приносил" in text and "карточки, 12 шт" in text


def test_a_silent_phone_becomes_a_complaint(base):
    """Молчит дольше недели — база по телефонной сети стоит на месте."""
    from datetime import datetime, timedelta

    store.mark_device("pyaterochka", "cards", 5)
    old = (datetime.now() - timedelta(days=9)).isoformat(timespec="microseconds")
    with store.connect() as conn:
        conn.execute("update catalog_state set value=json_set(value,'$.at',?)"
                     " where key='device.pyaterochka'", (old,))
        conn.commit()

    notes = checkup.complaints(checkup.snapshot(), None)

    assert any("pyaterochka" in note and "телефон не присылал" in note for note in notes), notes


def test_the_command_speaks_with_exit_codes(base, capsys):
    """0 — всё в порядке, 1 — есть замечания: так это ставится в расписание."""
    _row("lenta", "1", price=199.0)
    _run("lenta", "ok")
    assert checkup.main([]) == 0
    assert "замечаний нет" in capsys.readouterr().out

    _run("samokat", "ok")
    assert checkup.main(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any("samokat" in note for note in payload["complaints"])
