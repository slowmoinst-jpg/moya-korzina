"""Проверка доступа (tools/reachcheck.py): какую стену она называет.

Тексты — с настоящих страниц отказа, снятых 20–23.09.2026. Стена по адресу и
стена-проверка лечатся разным: первую — другим выходом в интернет, вторую проходит
человек. Перепутать их — значит отправить работу не туда.
"""
from __future__ import annotations

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("reachcheck", os.path.join(ROOT, "tools", "reachcheck.py"))
reachcheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reachcheck)

SERVICEPIPE = ("Мы хотим убедиться, что имеем дело именно с вами, а не с ботом.\n"
               "Пожалуйста, пройдите проверку, чтобы получить доступ к сайту.")
PYATEROCHKA_BY_IP = ("Проблемы со связью.\nПроверьте настройки интернета и VPN\n"
                     "Дата и время: 2026-09-23 07:06:49 +0000\nIP: 203.0.113.7")
DIXY_VPN = ("Не удалось загрузить сайт\nВозможно, у вас включён ВПН или отсутствует "
            "подключение к интернету\nОбновить страницу")
DIXY_CHECKBOX = "Поставь галочку в поле «Я не робот»\n\nИ продолжай пользоваться сайтом."
MONETKA = "403 Forbidden. Доступ к сайту monetka.ru запрещен"


def test_the_servicepipe_check_is_a_check_not_an_address_wall():
    """Самокат и Пятёрочка с домашнего адреса: проверку проходит человек."""
    assert reachcheck.wall_of(SERVICEPIPE) == "по проверке"


def test_a_page_that_names_our_address_is_an_address_wall():
    assert reachcheck.wall_of(PYATEROCHKA_BY_IP) == "по адресу"
    assert reachcheck.wall_of(DIXY_VPN) == "по адресу"
    assert reachcheck.wall_of(MONETKA) == "по адресу"


def test_the_dixy_checkbox_is_a_check():
    assert reachcheck.wall_of(DIXY_CHECKBOX) == "по проверке"


def test_a_showcase_is_no_wall():
    assert reachcheck.wall_of("Сахар белый, 1 кг\n79 ₽\nВ корзину") == ""
