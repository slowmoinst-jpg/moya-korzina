"""Прогрев единого каталога (app/web/server.py).

Наряд телефону опознаёт товар полным проходом по таблице каталога. Пока её страницы в памяти сервера, это
0,1 с; с диска — 2–6 с, и наряды всех сетей ждут этот проход (замер 13.11.2026: 5,3–7,3 с вместо 0,1–1,7 с).
Сервер раз в минуту проходит по таблице сам.
"""
import logging
import os
import sys
import threading

import pytest

from app.catalog import store
from app.web import server


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """Настоящий пустой каталог во временной папке."""
    file = str(tmp_path / "catalog.db")
    monkeypatch.setattr(store, "path", lambda: file)
    store.init()
    return file


def test_a_warm_pass_runs_the_real_catalog_search(catalog, caplog):
    """Настоящий поиск, без подмен: сменится его вызов или запрос — прогрев не должен молча стать холостым."""
    with caplog.at_level(logging.INFO):
        server.warm_catalog()
    # Прошёл без отказа, а быстрый проход журнал не занимает.
    assert "не прогрелся" not in caplog.text and "прогрев единого каталога" not in caplog.text


def test_a_warm_pass_scans_the_catalog_table_once(catalog, monkeypatch):
    asked = []
    monkeypatch.setattr(store, "search_items", lambda query, limit=20, **_: asked.append((query, limit)) or [])
    server.warm_catalog()
    assert asked == [("молоко", 1)]


def test_no_catalog_file_means_no_warming_and_no_empty_catalog(tmp_path, monkeypatch, caplog):
    """Каталога нет (новый сервер, рабочее дерево): пустым его прогрев не заводит, это дело обхода."""
    file = str(tmp_path / "нет" / "catalog.db")
    asked = []
    monkeypatch.setattr(store, "path", lambda: file)
    monkeypatch.setattr(store, "search_items", lambda *_args, **_kwargs: asked.append(1) or [])
    with caplog.at_level(logging.INFO):
        server.warm_catalog(say=True)
    assert not asked and not os.path.exists(os.path.dirname(file))
    assert "файла каталога ещё нет" in caplog.text      # первый проход говорит и об этом: прогрев запущен


def test_a_catalog_failure_does_not_break_the_warming(catalog, monkeypatch, caplog):
    def broken(*_args, **_kwargs):
        raise RuntimeError("каталог закрыт")

    monkeypatch.setattr(store, "search_items", broken)
    with caplog.at_level(logging.WARNING):
        server.warm_catalog()                       # не бросает: поток прогрева живёт дальше
    assert "не прогрелся" in caplog.text


def test_the_first_pass_and_a_slow_pass_are_logged(catalog, monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        server.warm_catalog(say=True)               # первый проход: по журналу видно, что прогрев запущен
        assert caplog.text.count("прогрев единого каталога занял") == 1
        monkeypatch.setattr(server, "CATALOG_SLOW_SEC", 0.0)
        server.warm_catalog()                       # долгий проход: страниц в памяти, скорее всего, не было
        assert caplog.text.count("прогрев единого каталога занял") == 2


def test_the_server_starts_the_warming_as_a_daemon_before_it_serves(monkeypatch):
    daemon = []
    started = threading.Event()

    def warming():
        daemon.append(threading.current_thread().daemon)
        started.set()

    served = []
    monkeypatch.setattr(server, "_keep_catalog_warm", warming)
    monkeypatch.setattr("app.web.create_app", lambda: object())
    monkeypatch.setattr("waitress.serve", lambda *_args, **_kwargs: served.append(started.wait(5)))
    before = sys.getswitchinterval()
    try:
        assert server.main(["--port", "0"]) == 0
    finally:
        sys.setswitchinterval(before)               # main на Linux ставит свой квант потоков
    assert served == [True] and daemon == [True]    # не демон повесил бы выход процесса
