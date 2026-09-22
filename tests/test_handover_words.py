"""Слова о передаче корзины: обещаем ровно то, что сеть делает."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import handover as ho  # noqa: E402


def test_every_chain_stands_on_a_rung():
    """Все семь сетей расставлены по ступеням — ни одна не осталась без способа.

    METRO пришла седьмой 19.09.2026. Её ступень здесь — ITEMS, и это ЗАПАСНОЙ путь:
    главный у неё сильнее всех прочих — собственный метод наполнения корзины
    (app/connectors/metro_cart.py). Сторож смотрит именно на запасной, потому что
    экран результата показывает его, когда наряд не собрался.
    """
    assert set(ho.KIND_BY_STORE) == {"vkusvill", "magnit", "lenta",
                                     "pyaterochka", "dixy", "samokat", "metro"}
    for code, kind in ho.KIND_BY_STORE.items():
        assert kind in (ho.LINK, ho.ITEMS, ho.SEARCH, ho.LIST), code


def test_lenta_warns_that_the_store_does_not_travel_in_the_link():
    """Магазин в ссылке не передаётся, и человек должен узнать это ДО нажатия.

    Живое открытие 18.09.2026: ссылка открылась в чужой точке («Гипер, Москва и
    МО, самовывоз −5 %»), а расчёт шёл по адресу человека. Цены зависят от точки,
    значит промолчать здесь — значит показать одну сумму, а дать другую.
    """
    note = ho.NOTE_BY_STORE["lenta"]

    assert "магазин" in note.lower()
    assert "цен" in note.lower(), "не сказано, чем это грозит"


def test_vkusvill_is_not_scared_with_a_trouble_it_does_not_have():
    """ВкусВилл сам спрашивает про адрес — пугать его предупреждением Ленты нельзя."""
    note = ho.NOTE_BY_STORE["vkusvill"]

    assert "адрес" in note.lower()
    assert "Лента" not in note


def test_a_promise_is_made_only_where_it_was_checked():
    """Про открытие в приложении сети обещаем только Магниту.

    У него это проверено по apple-app-site-association (/product/*). У Дикси
    такого подтверждения нет, и своя подпись как раз это и закрывает.
    """
    assert "приложении магазина" in ho.NOTE_BY_KIND[ho.ITEMS]
    assert "приложени" not in ho.NOTE_BY_STORE["dixy"]


# ---------- порядок способов на экране «Результат» ----------

def _result_template() -> str:
    path = os.path.join(ROOT, "app", "web", "templates", "result.html")
    with open(path, encoding="utf-8") as file:
        return file.read()


def test_the_way_that_needs_no_login_is_offered_first():
    """Способ, которому не нужен вход, стоит ВЫШЕ передачи под нашим входом.

    Это порядок по существу, а не по вёрстке: передача под сохранённым входом
    требует доверить серверу ключи от магазинного кабинета, а ссылка и карточки
    не требуют ничего. Что не нужно — предлагаем первым.
    """
    page = _result_template()
    ladder = page.find("s.handover")
    transfer = page.find("под моим входом")

    assert ladder > 0 and transfer > 0, "один из способов пропал с экрана"
    assert ladder < transfer, "передача под нашим входом снова обогнала способ без входа"


def test_the_ladder_is_not_hidden_behind_the_transfer_button():
    """Лестница видна и тогда, когда сеть подключена.

    Поломка 18.09.2026: условие «(s.asks_link or s.can_auto_cart)» уводило в свою
    ветку всё, где наряд собрался, и ветка со способом собрать заказ НЕ
    ВЫПОЛНЯЛАСЬ ВОВСЕ. Способ был построен, лежал в s.handover — и человек не
    видел его ни разу.
    """
    page = _result_template()

    assert "{% elif s.asks_link and not s.trouble %}" in page, \
        "главная кнопка снова обещает больше, чем сеть делает сама"
    assert "(s.asks_link or s.can_auto_cart)" not in page, \
        "вернулось условие, прятавшее лестницу"


def test_the_big_button_promises_only_what_the_chain_does_itself():
    """Наверху — только «Собрать корзину в …», то есть ссылка самой сети.

    Проверено гостем 18.09.2026: ВкусВилл по такой ссылке показал позиции с
    ценами, Лента спросила «Добавить в корзину?». Вход при этом не нужен.
    """
    page = _result_template()
    start = page.find("{% elif s.asks_link")
    head = page[start:page.find("{% else %}", start)]

    assert "Собрать корзину в" in head
    assert "Передать корзину" not in head, "в главной кнопке снова два разных действия"


def test_no_transfer_button_where_the_cart_cannot_be_filled():
    """Кнопки «Передать» нет там, где наполнить корзину нечем — и сказано почему.

    ПОЧЕМУ ЭТО ВАЖНО ИМЕННО КАК СТОРОЖ. Наряд собирается у ЛЮБОЙ сети, где есть
    сопоставления: он просто список «артикул, сколько». А наполняет его браузер, и
    до витрины Ленты, Дикси, Пятёрочки и Самоката он не доходит вовсе — сети не
    пускают наш сервер по адресу (замер 19.09.2026). Пока экран смотрел только на
    «собрался ли наряд», он рисовал кнопку у каждой такой сети — кнопку, которая
    способна только не сработать. Человек нажимает, ждёт и остаётся с пустой
    корзиной вместо того, чтобы собрать её самому.

    Сторож смотрит на разметку, а не на данные: условие легко потерять при первой
    же перестановке блоков, и потеря будет молчаливой.
    """
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "app/web/templates/result.html"), encoding="utf-8").read()

    button = page.index("Передать корзину в")
    guard = page.index("{% if s.plan.can_fill %}")
    closes = page.index("{% else %}", guard)

    assert guard < button < closes, \
        "кнопка «Передать» вышла из-под условия «можно ли вообще наполнить»"
    assert "s.plan.why_not" in page, "причина, по которой кнопки нет, до экрана не доехала"
