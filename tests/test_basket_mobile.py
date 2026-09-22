"""Корзина на телефоне: сборка одной рукой.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ И ПОЧЕМУ ИМЕННО ЭТО. Живого браузера у тестов нет — значит
«красиво ли» и «попадает ли палец» они сказать не могут в принципе. Зато они могут
поймать всё, из-за чего экран ломается молча:

  ОДНА ФОРМА НА ВЕСЬ ЭКРАН. Кнопки стоят вне формы и названы её именем через
  form="cart-items". Опечатка в имени не покраснеет нигде: кнопка просто перестанет
  что-либо делать, а выглядеть будет как обычно. Поэтому имя проверяется буквально.

  НАБРАННОЕ НЕ ПРОПАДАЕТ. Раньше количества сохраняли не все нажатия: «Посчитать»
  уводил на результат по старому числу. Проверяется каждое нажатие, а не одно.

  ИТОГ НЕ ВРЁТ. Сумма в прилипшей строке считается по самой дешёвой цене каждой
  позиции, и рядом сказано, сколько позиций в неё не вошло. Сложить известное и
  выдать за итог — то враньё, которое обнаруживается только на кассе.

  МИШЕНИ. Размер в точках тест не измерит, но может проверить, что в разметке
  стоят классы, которым korzina.css даёт рост, и что в самом korzina.css эти
  правила есть. Это ловит ровно одну поломку — переименовали класс и забыли, — но
  именно она случается чаще всего.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ: как экран выглядит на ширине 360 и 390, не прыгает ли
строка итога от клавиатуры и достаёт ли большой палец. Это смотрят руками.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000031"

CSS = os.path.join(ROOT, "app", "web", "static", "korzina.css")


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_web_basket.py."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from app.web.screens import result

    result._CALC.clear()
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result._CALC.clear()
    users.deactivate()


def enter(client, phone: str = PHONE):
    return client.post("/login", data={"phone": phone, "next": "/"})


def workspace(phone: str = PHONE) -> None:
    """Открыть базу человека в самом тесте: между запросами её снимает teardown."""
    users.open_workspace(phone)


def add_product(name: str, unit: str = "pcs") -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=unit))


def price(product_id: int, store_code: str, value: float) -> None:
    store = repo.get_store(store_code)
    sp_id = repo.upsert_store_product(store.id, f"sku-{store_code}-{product_id}", f"товар {product_id}")
    repo.confirm_mapping(product_id, sp_id, confirmed=True)
    repo.save_price(sp_id, value)


def seed_basket(name: str = "Неделя 38") -> tuple[int, int, int]:
    """Корзина из двух товаров с ценами в Магните и Пятёрочке.

    Ленты и ВкусВилла здесь нет нарочно — они умеют отдавать корзину ссылкой, а
    ссылку строит СЕТЕВОЙ вызов, который тестам запрещён (tests/conftest.py).
    """
    milk = add_product("Молоко Простоквашино 930 мл")
    bread = add_product("Хлеб Бородинский")
    price(milk, "magnit", 40.0)
    price(milk, "pyaterochka", 50.0)
    price(bread, "magnit", 30.0)
    price(bread, "pyaterochka", 25.0)
    basket_id = repo.create_basket(name)
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, bread, 1.0)
    return basket_id, milk, bread


def seed_history(days: tuple[str, ...] = ("2026-08-01", "2026-08-08", "2026-08-15")) -> int:
    """Один товар, купленный в трёх разных покупках: он и есть «частое»."""
    tea = add_product("Чай Ахмад чёрный")
    store = repo.get_store("magnit")
    for day in days:
        repo.add_history_row(day, store.id, tea, "ЧАЙ АХМАД", 1.0, 120.0, 120.0)
    return tea


def text(answer) -> str:
    return answer.data.decode("utf-8")


def css() -> str:
    with open(CSS, encoding="utf-8") as fh:
        return fh.read()


def opened(page: str) -> str:
    """Название корзины, которая открыта СЕЙЧАС.

    Читаем именно шапку, а не всю страницу: под «Другая корзина» лежит список всех
    корзин человека, и проверка «имя встречается на странице» проходила бы всегда —
    даже когда открыта совсем другая. Такой зелёный тест хуже отсутствующего.
    """
    mark = 'class="cart-head-name">'
    start = page.index(mark) + len(mark)
    return page[start:page.index("<", start)].strip()


# ---------- 1. быстрое добавление ----------
def test_the_search_asks_for_hints_while_the_word_is_still_being_typed(web):
    """Подсказка отвечает данными, а не страницей.

    Гонять страницу корзины на каждое нажатие клавиши нельзя: это список позиций,
    цены по сетям и частое — человек стоит у полки и ждёт мобильную сеть.
    """
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    add_product("Огурцы гладкие")

    answer = web.get(f"/basket/suggest?q=огурц&id={basket_id}")
    assert answer.status_code == 200
    body = answer.get_json()
    assert body["q"] == "огурц"
    assert any("Огурцы" in item["name"] for item in body["items"])
    assert all(item["do"].startswith(("add:", "catalog:")) for item in body["items"])


def test_a_single_letter_gets_no_hints(web):
    """На «м» откликается половина каталога — это список, который не читают."""
    enter(web)
    workspace()
    seed_basket()
    assert web.get("/basket/suggest?q=м").get_json()["items"] == []


def test_a_hint_never_offers_what_is_already_in_the_basket(web):
    """Нажатие на такую подсказку молча переставило бы количество в единицу."""
    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()

    body = web.get(f"/basket/suggest?q=молоко&id={basket_id}").get_json()
    assert all(item["do"] != f"add:{milk}" for item in body["items"])


def test_the_search_still_works_without_the_script(web):
    """Подсказка — надстройка. Выключенный JavaScript отнимает её, а не поиск."""
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    add_product("Огурцы гладкие")

    page = text(web.get(f"/basket?id={basket_id}&q=огурц"))
    assert "Огурцы гладкие" in page
    assert 'name="do" value="add:' in page          # кнопка формы, а не вызов скрипта


def test_quantity_is_moved_by_buttons_that_the_finger_can_hit(web):
    """«−» и «＋» — кнопки ростом с палец, а не стрелочки у поля ввода."""
    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()
    page = text(web.get(f"/basket?id={basket_id}"))

    assert f'class="cart-pm" type="submit" name="do" value="minus:{milk}"' in page
    assert f'class="cart-pm" type="submit" name="do" value="plus:{milk}"' in page
    # Рост кнопке даёт korzina.css, и без этого правила она станет обычной строчкой.
    assert re.search(r"\.cart-pm\{[^}]*height:var\(--tap\)", css())
    assert "--tap:48px" in css()


# ---------- 2. частое сверху ----------
def test_what_was_bought_before_is_offered_before_any_search(web):
    """Первое, что видно на экране, — то, что человек уже брал.

    Пустое поле поиска — это вопрос там, где ответ уже есть: история покупок лежит
    в базе. Спрашивать «что вам нужно?» у того, кто третью неделю берёт один и тот
    же чай, — значит заставлять его набирать «чай» третью неделю.
    """
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    tea = seed_history()

    page = text(web.get(f"/basket?id={basket_id}"))
    assert "Часто берёте" in page
    assert "Чай Ахмад" in page
    assert f'value="add:{tea}"' in page
    # Частое стоит ВЫШЕ списка позиций: добор — это то, ради чего экран открывают.
    assert page.index("Часто берёте") < page.index("В корзине")


def test_the_most_repeated_purchase_stands_first(web):
    """Порядок не случайный: сверху то, что встречалось в наибольшем числе покупок.

    Без сортировки список шёл в порядке, в котором строки легли в базу, — то есть
    по сути случайно, и самое нужное оказывалось десятым.
    """
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    seed_history()                                   # чай — три покупки
    store = repo.get_store("magnit")
    rare = add_product("Соус ткемали")
    for day in ("2026-08-01", "2026-08-08"):         # соус — две
        repo.add_history_row(day, store.id, rare, "СОУС ТКЕМАЛИ", 1.0, 200.0, 200.0)

    page = text(web.get(f"/basket?id={basket_id}"))
    assert page.index("Чай Ахмад") < page.index("Соус ткемали")


def test_what_is_in_the_basket_is_not_offered_again(web):
    """Товар из корзины в «частом» — это предложение положить то, что уже лежит."""
    enter(web)
    workspace()
    tea = seed_history()
    basket_id = repo.create_basket("Пустая")
    repo.set_basket_item(basket_id, tea, 1.0)

    assert "Часто берёте" not in text(web.get(f"/basket?id={basket_id}"))


# ---------- 3. итог всегда виден ----------
def test_the_sticky_line_shows_how_many_and_how_much(web):
    """Сколько позиций, сколько примерно стоит и «Посчитать» — у нижнего края."""
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()          # 2 молока по 40 + хлеб за 25 = 105

    page = text(web.get(f"/basket?id={basket_id}"))
    bar = page[page.index('class="cart-bar"'):]
    assert "2 позиции" in bar
    assert "105" in bar
    assert "Посчитать" in bar
    assert 'form="cart-items"' in bar         # кнопка везёт с собой набранные числа


def test_the_total_counts_the_cheapest_price_of_every_row(web):
    """Считаем по самой дешёвой цене каждой позиции — это и есть обещание приложения.

    Сумма одного магазина врала бы в другую сторону и расходилась бы с числом на
    экране «Результат», который считает ровно так же.
    """
    from app.web.screens import basket as screen

    rows = [{"best": {"value": 40.0}}, {"best": {"value": 25.0}}, {"best": None}]
    assert screen._approx(rows) == {"total": 65.0, "priced": 2, "blind": 1, "count": 3}


def test_the_total_says_out_loud_how_many_rows_have_no_price(web):
    """Сложить известное и выдать за итог — враньё, которое видно только на кассе."""
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    workspace()
    repo.set_basket_item(basket_id, add_product("Ананас"), 1.0)

    page = text(web.get(f"/basket?id={basket_id}"))
    bar = page[page.index('class="cart-bar"'):]
    assert "1 без цены" in bar


def test_an_empty_basket_does_not_pretend_to_have_a_total(web):
    enter(web)
    workspace()
    basket_id = repo.create_basket("Пустая")

    page = text(web.get(f"/basket?id={basket_id}"))
    bar = page[page.index('class="cart-bar"'):]
    assert "Пусто" in bar
    assert "disabled" in bar                  # считать нечего, и кнопка об этом говорит


def test_counting_stays_possible_while_the_prices_are_being_refreshed(web, monkeypatch):
    """«Посчитать» гаснет только у пустой корзины, а не на время опроса цен.

    Экран обещает прямо над кнопкой: «Посчитать» внизу считает по ценам последнего
    опроса — это мгновенно». А кнопка гасла на все минуты фонового опроса, и
    страница в этом состоянии сама перезагружалась каждые четыре секунды: человек
    смотрел на серую кнопку и перечитывал обещание. Расчёт от идущего опроса не
    зависит вовсе — он уводит на «Результат» по уже снятым ценам.
    """
    from app.web.screens import basket as screen

    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    monkeypatch.setattr(screen, "job_of", lambda bid: {"status": "running", "kind": "fresh",
                                                       "done": 2, "total": 9, "what": "Молоко",
                                                       "note": ""})

    page = text(web.get(f"/basket?id={basket_id}"))
    bar = page[page.index('class="cart-bar"'):]
    assert "Обновляем цены" in page, "идущий опрос на экране не показан — тест смотрит не туда"
    assert 'value="calc"' in bar and "disabled" not in bar, \
        "«Посчитать» погашено, хотя абзац выше обещает мгновенный расчёт"


def test_the_sticky_line_is_the_last_thing_on_the_page(web):
    """У неё есть СВОЁ место в конце страницы — потому она и не закрывает список.

    Прибитая к окну строка закрывала бы низ экрана всегда, и до последней позиции
    пришлось бы доскребаться прокруткой.
    """
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    page = text(web.get(f"/basket?id={basket_id}"))

    assert page.index('class="cart-bar"') > page.rindex('class="cart-line"')
    assert re.search(r"\.cart-bar\{[^}]*position:sticky", css())
    assert "position:fixed" not in re.search(r"\.cart-bar\{[^}]*\}", css()).group(0)


def test_the_sticky_line_stands_above_the_bottom_panel(web):
    """Разъедутся эти числа — строка ляжет на панель разделов, и «Посчитать»
    придётся нажимать вслепую.

    ВЫРЕЗ ТЕЛЕФОНА СЧИТАЕТСЯ НАРАВНЕ С ВЫСОТОЙ ПАНЕЛИ. Сама панель растёт на
    env(safe-area-inset-bottom), а страница стоит с viewport-fit=cover — значит на
    iPhone X и новее вырез не ноль, а 34 точки. Подъём на плоские 64 поднимал
    строку итога ровно на высоту панели БЕЗ выреза, и панель накрывала нижнюю
    половину кнопки «Посчитать»: человек метил в неё, а попадал в «Каталог».
    В настольном браузере этого не видно — там вырез равен нулю.
    """
    body = css()
    assert re.search(r"\.cart-bar\{[^}]*bottom:var\(--cart-lift\)", body)
    lift = re.search(r"--cart-lift:([^;]+);", body.split("@media")[-1])
    assert lift, "подъём прилипшей строки на телефоне не задан"
    assert "var(--bottom-bar)" in lift.group(1), "строка поднята не на высоту панели"
    assert "env(safe-area-inset-bottom)" in lift.group(1), \
        "вырез телефона не учтён — панель накроет нижнюю половину «Посчитать»"
    # Тем же слагаемым живёт и нижний отступ страницы: иначе последняя строка
    # любого экрана уедет под панель разделов.
    main = re.search(r"\nmain\{[^}]*\}", body).group(0)
    assert "env(safe-area-inset-bottom)" in main and "var(--bottom-bar)" in main


# ---------- 4. палец достаёт ----------
def test_everything_that_is_pressed_is_at_least_forty_four_points(web):
    """Мишени экрана растут из korzina.css — проверяем, что правила на месте.

    Точек тест не измерит: живого браузера здесь нет. Но он ловит ровно ту
    поломку, которая случается чаще всего, — класс переименовали, а правило
    осталось висеть в воздухе.
    """
    body = css()
    for rule, least in ((r"\.cart-pm\{[^}]*", "var(--tap)"),
                        (r"\.cart-qty\{[^}]*", "var(--tap)"),
                        (r"\.cart-q\{[^}]*", "var(--tap)"),
                        (r"\.cart-go\{[^}]*", "var(--tap)")):
        found = re.search(rule, body)
        assert found and least in found.group(0), rule
    assert re.search(r"\.cart-off\{[^}]*min-height:44px", body)
    assert re.search(r"\.cart-often button\{[^}]*min-height:44px", body)
    # Нижняя панель разделов — тоже мишень, и она была вдвое меньше пальца.
    assert re.search(r"\.bottom a\{[^}]*min-height:48px", body)


def test_fields_do_not_make_the_phone_zoom_in(web):
    """Safari приближает страницу на поле со шрифтом меньше 16 и обратно не отдаляет.

    Выглядит это как «экран прыгнул и разъехался» — ровно то, чего нижняя строка
    итога делать не должна.
    """
    body = css()
    for rule in (r"\.cart-q\{[^}]*", r"\.cart-qty\{[^}]*"):
        assert "font-size:16px" in re.search(rule, body).group(0), rule


def test_the_screen_does_not_scroll_sideways(web):
    """Горизонтальная прокрутка на телефоне выглядит как поломка вёрстки.

    Длинное название товара — самый частый способ её получить, поэтому в строке
    позиции имя обязано сжиматься, а не распирать её.
    """
    enter(web)
    workspace()
    basket_id = repo.create_basket("Длинная")
    repo.set_basket_item(basket_id, add_product("Молоко ультрапастеризованное "
                                                "Простоквашино 3,2% в бутылке 930 мл"), 1.0)
    assert web.get(f"/basket?id={basket_id}").status_code == 200
    assert re.search(r"\.cart-line-name\{[^}]*min-width:0", css())
    assert re.search(r"\.cart-hit-name\{[^}]*text-overflow:ellipsis", css())


# ---------- 5. не терять набранное ----------
@pytest.mark.parametrize("do", ["save", "ask", "calc", "clear", "fresh"])
def test_every_button_carries_the_typed_quantity_home(web, monkeypatch, do):
    """Любое нажатие сохраняет набранные числа — не только «Сохранить».

    Раньше «Рассчитать» и «Узнать цены» их не сохраняли: человек правил количество,
    нажимал кнопку и получал ответ по СТАРОМУ числу, ничего об этом не узнав.
    """
    from app.web.screens import basket as screen

    monkeypatch.setattr(screen, "_start_job", lambda *a, **k: None)   # фон нам не нужен

    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()

    web.post("/basket", data={"basket": basket_id, "do": do, f"qty:{milk}": "4"})
    workspace()
    if do == "clear":
        assert repo.basket_items(basket_id) == []
        return
    rows = {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(basket_id)}
    assert rows[milk] == 4.0, do


def test_adding_from_the_search_does_not_eat_the_typed_quantity(web):
    """Кнопка подсказки стоит вне формы, но шлёт её же — вместе с количествами."""
    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()
    cucumber = add_product("Огурцы гладкие")

    web.post("/basket", data={"basket": basket_id, "do": f"add:{cucumber}", f"qty:{milk}": "6"})
    workspace()
    rows = {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(basket_id)}
    assert rows[milk] == 6.0
    assert cucumber in rows


def test_the_basket_is_where_it_was_left(web):
    """Ушёл на другой экран и вернулся — открылась та же корзина.

    В нижней панели телефона «Корзина» — это голый /basket, без номера, и раньше
    он открывал САМУЮ НОВУЮ. Человек уходил на «Результат» из той, которую
    набирал, возвращался — и попадал в чужую. Работа не терялась, но выглядело это
    ровно как потеря.
    """
    enter(web)
    workspace()
    seed_basket("Неделя 38")
    repo.create_basket("Праздничная")

    # Берём ту корзину, которую голый адрес НЕ открывает сам: иначе тест был бы
    # зелёным и без всякой памяти, просто потому что угадал порядок списка.
    was = opened(text(web.get("/basket")))
    workspace()
    other = next(b for b in repo.list_baskets() if b["name"] != was)

    web.post("/basket", data={"basket": other["id"], "do": "save"})
    assert opened(text(web.get("/basket"))) == other["name"]
    assert other["name"] != was


def test_a_forgotten_basket_does_not_leave_the_screen_empty(web):
    """Запомненную корзину могли удалить: тогда молча открываем свежую."""
    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket("Неделя 38")
    web.post("/basket", data={"basket": basket_id, "do": f"plus:{milk}"})

    workspace()
    repo.delete_basket(basket_id)
    repo.create_basket("Праздничная")

    assert opened(text(web.get("/basket"))) == "Праздничная"


def test_opening_the_screen_still_changes_nothing(web):
    """Показ страницы не пишет в базу — ни позиций, ни памяти о том, где человек был.

    Иначе две вкладки рядом начнут переставлять «последнюю» корзину друг у друга,
    и голый /basket станет непредсказуемым. Сторож стоит здесь, потому что память
    о корзине — первое, что хочется записать «заодно» при показе.
    """
    from app.web.screens import basket as screen

    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()

    web.get(f"/basket?id={basket_id}")
    workspace()
    assert repo.get_setting(screen.LAST_OPEN) is None

    web.get("/basket/suggest?q=молоко")
    workspace()
    assert repo.get_setting(screen.LAST_OPEN) is None


# ---------- 6. соседние экраны не поехали ----------
def test_the_shared_stylesheet_only_grew(web):
    """korzina.css общий: «Кабинеты», «Результат» и «Кабинет» рисует он же.

    Новые правила названы своей приставкой cart- и чужих классов не трогают. Два
    исключения названы вслух — .bottom a и .tabs a в шапке: это мишени самой рамы,
    и они были вдвое меньше пальца. Трогаем у них только высоту.
    """
    body = css()
    mine = re.findall(r"^\.([a-z-]+)", body[body.index("КОРЗИНА НА ТЕЛЕФОНЕ"):], re.M)
    strangers = {name for name in mine if not name.startswith("cart-")}
    assert strangers <= {"tabs", "addr", "bottom"}, strangers


@pytest.mark.parametrize("path", ["/", "/result", "/accounts", "/stores", "/history"])
def test_the_neighbours_still_open(web, path):
    """Самая дешёвая проверка «ничего не сломал» — и самая часто пропускаемая."""
    enter(web)
    workspace()
    seed_basket()
    assert web.get(path).status_code == 200


def test_the_hint_answers_only_to_its_own_person(web):
    """Подсказка отдаёт названия товаров из базы человека — и никому больше."""
    fresh = web.get("/basket/suggest?q=молоко", headers={"Accept": "application/json"})
    assert fresh.status_code == 401


def test_the_suggestion_route_is_not_a_screen_in_the_menu(web):
    """В меню ей делать нечего: она отвечает данными, а не страницей.

    Смотрим именно меню, а не страницу целиком: в скрипте внизу адрес подсказки,
    конечно, есть — им он и зовётся. Проверка «адреса нет на странице» была бы
    зелёной только до тех пор, пока скрипт не переедет выше.
    """
    from app.web import views

    assert all(screen.path != "/basket/suggest" for screen in views.SCREENS)
    enter(web)
    workspace()
    seed_basket()
    page = text(web.get("/basket"))
    menus = re.findall(r"<nav\b.*?</nav>", page, re.S)
    assert menus, "меню на странице не нашлось — проверка потеряла смысл"
    assert all("/basket/suggest" not in menu for menu in menus)
