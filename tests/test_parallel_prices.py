"""Цены всех сетей разом: параллельно, в срок, в базе ЧЕЛОВЕКА, с кулдауном и паузой.

ЗАЧЕМ. «Где дешевле» рисуется сразу из того, что есть, а цену в моменте обновляет
фон. Последовательный обход восьми сетей по 25 с — это четыре минуты, которых у
экрана нет; параллельный укладывается в общий срок, а сеть, которая молчит, ждёт
только свой срок и остальных не держит.

ГЛАВНОЕ ЗДЕСЬ — БАЗА ЧЕЛОВЕКА. ThreadPoolExecutor не наследует contextvars, и путь
к базе (app/config.py:set_db_override, app/users.py:activate) в рабочих потоках
раньше был пуст: цены писались в общую data/basket.db. Тесты ниже работают на
настоящих рабочих местах (users.open_workspace) и проверяют, что в потоке видна
именно база человека, а снимки ложатся туда.

Тесты не ходят в сеть: matcher._get_connector подменён фальшивым коннектором,
_LAST_RUN и _PAUSED_UNTIL очищены на каждый тест.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import config, repo, service, users        # noqa: E402
from app.db import init_db                          # noqa: E402
from app.matcher import matcher                     # noqa: E402
from app.models import PriceSnapshot, Product       # noqa: E402

PHONE_A = "79990000016"
PHONE_B = "79990000017"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Чистые отметки кулдауна и ничей не открытый контекст на каждый тест."""
    monkeypatch.setattr(matcher, "_LAST_RUN", {})
    monkeypatch.setattr(matcher, "_PAUSED_UNTIL", {})
    config.set_db_override(None)
    yield
    users.deactivate()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Одна временная база без рабочего места: прежние тесты матчера."""
    path = str(tmp_path / "parallel.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    init_db()
    assert config.db_path() != os.path.join(ROOT, "data", "basket.db")
    return path


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    """Рабочие места людей на временном корне; общая база — тоже временная.

    Возвращает путь ОБЩЕЙ базы: она заведена (init_db) и обязана остаться пустой,
    пока человек работает в своём рабочем месте.
    """
    monkeypatch.setattr(config, "ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.deactivate()
    common = config.db_path()
    init_db()
    yield common
    users.deactivate()


class ConcurrencyProbe:
    """Сколько фальшивых коннекторов стоит в get_prices ОДНОВРЕМЕННО.

    Считаем не «стенные» секунды, а наблюдаемое перекрытие: перед ответом каждая
    сеть отмечается здесь, поэтому max_inside — факт, а не оценка по часам.

    barrier — место встречи: обе сети обязаны дойти до него ДО того, как любая
    ответит. Последовательный обход до барьера не дойдёт: его единственный вызов
    провисит до таймаута и порвёт барьер, а причина ляжет в get_prices ошибкой.
    timeout — аварийный клапан, а не мера скорости: 5 с на «второй поток не
    проснулся» с запасом хватает и под нагрузкой.

    barrier=False — только счётчик: так проверяется последовательный путь, где
    встреча как раз недопустима.
    """

    def __init__(self, expected: int = 2, timeout: float = 5.0, barrier: bool = True):
        self.expected = expected
        self.timeout = timeout
        self.inside = 0
        self.max_inside = 0
        self.arrived: list[str] = []
        self.broken: str | None = None
        self.barrier = threading.Barrier(expected, timeout=timeout) if barrier else None
        self._lock = threading.Lock()

    def enter(self, code: str) -> None:
        with self._lock:
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
            self.arrived.append(code)
        if self.barrier is None:
            return
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError as exc:
            with self._lock:
                self.broken = (
                    f"сеть {code} не встретила остальные у барьера за "
                    f"{self.timeout:g} с: обход стал последовательным"
                )
            raise RuntimeError(self.broken) from exc

    def leave(self) -> None:
        with self._lock:
            self.inside -= 1


class FakeConnector:
    """Коннектор сети: спит, отвечает снимками или бросает исключение.

    seen_paths — какой config.db_path() видел РАБОЧИЙ поток в момент запроса цен:
    так тест ловит потерю contextvars, не заглядывая внутрь ThreadPoolExecutor.

    probe — общий на несколько сетей счётчик одновременности (ConcurrencyProbe):
    вызов стоит в нём, пока коннектор «отвечает», поэтому перекрытие видно без
    единого замера времени.
    """

    def __init__(self, code: str, delay: float = 0.0, prices: dict | None = None,
                 error: BaseException | None = None, silent: bool = False,
                 probe: ConcurrencyProbe | None = None):
        self.code = code
        self.delay = delay
        self.prices = prices or {}
        self.error = error
        self.silent = silent
        self.probe = probe
        self.asked = 0
        self.seen_paths: list[str] = []
        self._lock = threading.Lock()

    def get_prices(self, skus):
        with self._lock:
            self.asked += 1
            self.seen_paths.append(config.db_path())
        try:
            if self.probe is not None:
                self.probe.enter(self.code)
            time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            if self.silent:
                return []
            stamp = datetime.now().isoformat(timespec="seconds")
            return [PriceSnapshot(self.code, sku, self.prices.get(sku, 100.0),
                                  fetched_at=stamp)
                    for sku in skus]
        finally:
            if self.probe is not None:
                self.probe.leave()


@pytest.fixture
def network(monkeypatch):
    """Сеть фальшивых коннекторов: network('magnit', delay=0.3) -> коннектор."""
    made: dict[str, FakeConnector] = {}

    def install(code: str, **fields):
        made[code] = FakeConnector(code, **fields)
        return made[code]

    monkeypatch.setattr(matcher, "_get_connector",
                        lambda code, location=None: made.get(code))
    return install


def _seed(store_code: str, sku: str = "SKU-1") -> tuple[int, int]:
    """Товар, опознанный в сети: (эталон, строка сети)."""
    store = repo.get_store(store_code)
    pid = repo.upsert_product(Product(None, f"Товар {store_code}", weight_g=1000.0, unit="pcs"))
    sp = repo.upsert_store_product(store.id, sku, f"Товар {store_code}", weight_g=1000.0,
                                   unit="pcs")
    repo.confirm_mapping(pid, sp, confirmed=True)
    return pid, sp


def _price_rows(path: str) -> int:
    """Сколько снимков цены лежит в базе по этому пути."""
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM store_prices").fetchone()[0])


# ---------- параллельность ----------
def test_two_networks_are_asked_at_the_same_time(temp_db, network):
    """Обе сети стоят в get_prices ОДНОВРЕМЕННО — видно по барьеру, а не по часам.

    Прежде здесь мерились «стенные» секунды: последовательно ~0,6 с, параллельно
    ~0,3 с. Под нагрузкой (рядом docker-сборка APK, чужие прогоны) часы плывут, и
    красный полный прогон было не отличить от настоящей регрессии. Теперь факт
    перекрытия фиксируют сами фальшивые коннекторы: прежде чем ответить, обе сети
    обязаны дойти до Barrier(2). Не дошли — обход стал последовательным: барьер
    рвётся по таймауту, в errors лежит причина, тест падает по существу.
    """
    magnit, _ = _seed("magnit")
    vkusvill, _ = _seed("vkusvill")
    probe = ConcurrencyProbe(expected=2, timeout=5.0)
    network("magnit", probe=probe)
    network("vkusvill", probe=probe)
    ids = [magnit, vkusvill]

    result = matcher.refresh_prices_parallel(ids, ["magnit", "vkusvill"],
                                             per_store_deadline=15.0, total_deadline=30.0)

    assert probe.broken is None, probe.broken
    assert probe.max_inside >= 2, (
        "обе сети обязаны быть внутри get_prices одновременно, "
        f"а максимум был {probe.max_inside}; до барьера дошли: {probe.arrived}"
    )
    assert sorted(probe.arrived) == ["magnit", "vkusvill"], "спросили обе сети"
    assert result["timed_out"] == [], result["timed_out"]
    assert result["errors"] == [], result["errors"]
    assert result["updated"] == 2


def test_sequential_refresh_asks_networks_one_at_a_time(temp_db, network):
    """Последовательный путь остался последовательным: внутри get_prices одна сеть.

    Сторож прежнего обхода (кнопка «Обновить», дозор цен): он обязан идти сеть за
    сетью. Считаем не время, а одновременность — delay держит вызов открытым, пока
    при параллели успел бы начаться следующий. Заодно сверяем порядок: он обещан
    тем, кто рисует ход по сетям.
    """
    magnit, _ = _seed("magnit")
    vkusvill, _ = _seed("vkusvill")
    probe = ConcurrencyProbe(expected=2, barrier=False)
    network("magnit", delay=0.2, probe=probe)
    network("vkusvill", delay=0.2, probe=probe)

    result = matcher.refresh_prices([magnit, vkusvill], ["magnit", "vkusvill"])

    assert result["updated"] == 2
    assert result["errors"] == [], result["errors"]
    assert probe.max_inside == 1, (
        "последовательный обход спрашивает сети по одной, "
        f"а одновременно внутри было {probe.max_inside}"
    )
    assert probe.arrived == ["magnit", "vkusvill"], "сети идут в порядке запроса"


def test_a_silent_network_times_out_and_the_others_keep_their_prices(temp_db, network):
    """Сеть не ответила за свой срок — в timed_out, остальные цены записаны."""
    silent, silent_sp = _seed("magnit")
    quick, quick_sp = _seed("vkusvill")
    network("magnit", delay=0.6, silent=True)
    network("vkusvill", prices={"SKU-1": 42.0})

    started = time.monotonic()
    result = matcher.refresh_prices_parallel([silent, quick], ["magnit", "vkusvill"],
                                             per_store_deadline=0.15, total_deadline=3.0)
    seconds = time.monotonic() - started

    assert result["timed_out"] == ["magnit"]
    assert result["errors"] == []
    assert result["updated"] == 1
    assert repo.latest_price(quick_sp)["price"] == 42.0
    assert repo.latest_price(silent_sp) is None
    assert seconds < 1.0, "ждать молчащую сеть дольше её срока нельзя"


# ---------- база человека в рабочем потоке ----------
def test_prices_are_written_to_the_persons_database(workspaces, network):
    """Снимок идёт в базу ЧЕЛОВЕКА, а не в общую data/basket.db.

    Без copy_context() путь к базе в рабочем потоке пуст, и repo.save_price пишет
    в общую базу; на общей базе без таблиц это ещё и ставит паузу всем сетям.
    """
    common = workspaces
    person = users.open_workspace(PHONE_A)
    pid, sp = _seed("magnit")
    connector = network("magnit", prices={"SKU-1": 42.0})

    result = matcher.refresh_prices_parallel([pid], ["magnit"])

    assert result["updated"] == 1
    assert connector.seen_paths == [person], \
        "рабочий поток обязан видеть базу человека, а не общую"
    assert _price_rows(person) == 1, "снимок в базе человека"
    assert _price_rows(common) == 0, "в общую базу не должно попасть ничего"
    assert repo.latest_price(sp)["price"] == 42.0


def test_the_cooldown_of_one_workspace_does_not_block_another(workspaces, network):
    """Кулдаун — по паре (база человека, сеть): обновление у одного не отменяет другого."""
    connector = network("magnit", prices={"SKU-1": 11.0})

    users.open_workspace(PHONE_A)
    person_a = config.db_path()
    pid_a, _ = _seed("magnit")
    first = matcher.refresh_prices_parallel([pid_a], ["magnit"])
    assert first["updated"] == 1 and connector.asked == 1

    users.open_workspace(PHONE_B)
    person_b = config.db_path()
    assert person_b != person_a
    pid_b, _ = _seed("magnit")
    second = matcher.refresh_prices_parallel([pid_b], ["magnit"])
    assert second["skipped"] == [], "кулдаун чужого рабочего места не мой"
    assert second["updated"] == 1 and connector.asked == 2

    users.open_workspace(PHONE_A)
    third = matcher.refresh_prices_parallel([pid_a], ["magnit"])
    assert third["skipped"] == ["magnit"], "своё рабочее место помнит свой кулдаун"
    assert connector.asked == 2
    assert (person_a, "magnit") in matcher._LAST_RUN
    assert (person_b, "magnit") in matcher._LAST_RUN


# ---------- ошибки и пауза ----------
def test_a_network_that_raises_is_in_errors_and_the_rest_survive(temp_db, network):
    broken, broken_sp = _seed("magnit")
    good, good_sp = _seed("vkusvill")
    boom = network("magnit", error=RuntimeError("кабель отвалился"))
    network("vkusvill", prices={"SKU-1": 33.0})

    result = matcher.refresh_prices_parallel([broken, good], ["magnit", "vkusvill"],
                                             per_store_deadline=2.0, total_deadline=5.0)
    assert any("magnit" in error and "кабель отвалился" in error for error in result["errors"])
    assert result["updated"] == 1
    assert repo.latest_price(good_sp)["price"] == 33.0
    assert repo.latest_price(broken_sp) is None

    # Обычный сбой — короткая передышка: второй раз её сразу не спрашивают.
    again = matcher.refresh_prices_parallel([broken], ["magnit"])
    assert again["skipped"] == ["magnit"]
    assert again["errors"] == []
    assert boom.asked == 1


def test_a_common_failure_gets_a_short_backoff_not_a_day(temp_db, network, caplog):
    """Обычное исключение — 300 с и log.warning с причиной, а не пауза до завтра.

    Сеть, у которой моргнула связь, не должна выпадать из цен на сутки; суточная
    пауза — только за явную блокировку (следующий тест).
    """
    pid, _ = _seed("magnit")
    connector = network("magnit", error=RuntimeError("кабель отвалился"))
    started = time.time()

    with caplog.at_level(logging.WARNING):
        result = matcher.refresh_prices_parallel([pid], ["magnit"])

    assert any("кабель отвалился" in error for error in result["errors"])
    until = matcher._PAUSED_UNTIL[(config.db_path(), "magnit")]
    assert 0 < until - started <= matcher.FAIL_BACKOFF_SECONDS + 5
    assert matcher.FAIL_BACKOFF_SECONDS == 300
    assert until - started < matcher.BLOCK_PAUSE_SECONDS / 10, "это не «до завтра»"
    assert any("кабель отвалился" in record.getMessage() for record in caplog.records), \
        "причина паузы должна быть в логе"

    # force обходит кулдаун, но не паузу: пока идёт backoff, сеть не спрашивают.
    forced = matcher.refresh_prices_parallel([pid], ["magnit"], force=True)
    assert forced["skipped"] == ["magnit"]
    assert connector.asked == 1


def test_crawl_blocked_is_an_error_and_a_day_pause(temp_db, network, caplog):
    """Явная блокировка — errors и сутки тишины: повтор только продлит проверку."""
    from app.catalog.model import CrawlBlocked

    pid, sp = _seed("dixy")
    blocked = network("dixy", error=CrawlBlocked("капча"))
    started = time.time()

    with caplog.at_level(logging.WARNING):
        result = matcher.refresh_prices_parallel([pid], ["dixy"])

    assert any("dixy" in error for error in result["errors"])
    assert repo.latest_price(sp) is None
    until = matcher._PAUSED_UNTIL[(config.db_path(), "dixy")]
    assert matcher.BLOCK_PAUSE_SECONDS - 5 <= until - started <= matcher.BLOCK_PAUSE_SECONDS + 5
    assert any("капча" in record.getMessage() for record in caplog.records), \
        "причина суточной паузы должна быть в логе"

    again = matcher.refresh_prices_parallel([pid], ["dixy"])
    assert again["skipped"] == ["dixy"]
    assert blocked.asked == 1


# ---------- кулдаун ----------
def test_the_second_call_within_the_cooldown_does_not_touch_the_network(temp_db, network):
    pid, sp = _seed("magnit")
    connector = network("magnit", prices={"SKU-1": 55.0})

    first = matcher.refresh_prices_parallel([pid], ["magnit"])
    assert first["updated"] == 1 and connector.asked == 1

    second = matcher.refresh_prices_parallel([pid], ["magnit"])
    assert second["skipped"] == ["magnit"]
    assert second["updated"] == 0
    assert connector.asked == 1, "в кулдауне коннектор звать нечего"

    forced = matcher.refresh_prices_parallel([pid], ["magnit"], force=True)
    assert forced["updated"] == 1
    assert connector.asked == 2, "force обходит кулдаун"


# ---------- прежний последовательный путь ----------
def test_sequential_refresh_still_works(temp_db, network):
    pid, sp = _seed("magnit")
    network("magnit", prices={"SKU-1": 77.0})

    result = matcher.refresh_prices([pid], ["magnit"])
    assert result == {"updated": 1, "errors": []}
    assert repo.latest_price(sp)["price"] == 77.0


def test_sequential_refresh_reports_a_broken_network(temp_db, network):
    pid, _ = _seed("magnit")
    network("magnit", error=RuntimeError("нет ответа"))

    result = matcher.refresh_prices([pid], ["magnit"])
    assert result["updated"] == 0
    assert any("magnit" in error and "нет ответа" in error for error in result["errors"])


# ---------- расчёт с бюджетом ----------
def test_calculate_with_a_budget_returns_variants(temp_db, network):
    """«Где дешевле» с бюджетом: обновление не падает и расчёт отдаёт варианты."""
    store = repo.get_store("vkusvill")
    pid = repo.upsert_product(Product(None, "Молоко 1 л", weight_g=1000.0, unit="pcs"))
    sp = repo.upsert_store_product(store.id, "SKU-M", "Молоко 1 л", weight_g=1000.0,
                                   unit="pcs")
    repo.confirm_mapping(pid, sp, confirmed=True)
    basket = repo.create_basket("Неделя")
    repo.set_basket_item(basket, pid, 1)
    network("vkusvill", prices={"SKU-M": 899.0})

    variants, baseline = service.calculate(basket, refresh=True, refresh_budget=1.0)

    assert variants, "свежая цена записана — вариант обязан быть"
    assert baseline > 0
    assert repo.latest_price(sp)["price"] == 899.0
