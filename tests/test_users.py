"""Рабочие места по номеру телефона: один номер — одна база, чужого не видно."""
from __future__ import annotations

import os

import pytest

from app import config, repo, users
from app.models import Product


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    yield tmp_path / "users"
    # контекст живёт в потоке pytest и пережил бы тест — возвращаем общую базу
    users.deactivate()


def test_phone_spellings_land_in_one_workspace():
    spellings = ["+7 (999) 123-45-67", "8 999 123 45 67", "79991234567",
                 "9991234567", "+7-999-123-45-67 "]
    assert {users.normalize_phone(s) for s in spellings} == {"79991234567"}


def test_not_a_phone_is_rejected():
    for junk in ["", None, "12345", "abc", "+7 999 123", "123456789012"]:
        assert users.normalize_phone(junk) is None
    phone, problem = users.resolve_login("  ")
    assert phone is None and "Введите" in problem
    phone, problem = users.resolve_login("12")
    assert phone is None and "не похоже" in problem
    phone, problem = users.resolve_login("8 999 123-45-67")
    assert phone == "79991234567" and problem is None


def test_demo_is_a_workspace_of_its_own():
    assert users.normalize_phone("Demo") == users.DEMO
    assert users.display(users.DEMO) == "Демо"
    assert users.display("79991234567") == "+7 999 123-45-67"


def test_workspaces_do_not_see_each_other(workspaces):
    users.open_workspace("79990000001")
    assert config.db_path() == users.db_path_for("79990000001")
    assert os.path.exists(config.db_path())
    repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))

    users.open_workspace("79990000002")
    assert repo.list_products() == []

    users.activate("79990000001")
    assert [p.name for p in repo.list_products()] == ["Молоко"]
    assert users.current() == "79990000001"
    assert set(users.list_workspaces()) == {"79990000001", "79990000002"}

    users.deactivate()
    assert config.db_override() is None
    assert users.current() is None


def test_stores_directory_is_seeded_in_every_workspace(workspaces):
    users.open_workspace("79990000003")
    assert {s.code for s in repo.list_stores()} >= {"magnit", "lenta", "vkusvill"}


def test_login_is_not_a_menu_screen():
    """Вход — не пункт меню, а дверь: в навигации его быть не должно.

    Проверка переехала со старого app/ui/main.SCREENS на app/web/views.SCREENS
    вместе с интерфейсом (17.09.2026). Смысл прежний: человек, уже вошедший,
    не должен видеть «Вход» среди экранов, а первым в меню стоит «Главная».
    """
    from app.web.views import SCREENS

    titles = [s.title for s in SCREENS]
    assert "Вход" not in titles
    assert titles and titles[0] == "Главная"
