"""Браузер магазина на сервере: чтение страницы, хранение входа и границы.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ БЕЗ СЕТИ И БЕЗ БРАУЗЕРА — почти всё важное. Разбор
признаков входа (app/shopbrowser/signals.py) и хранение сохранённого входа
(store.py) не требуют ни того, ни другого, а ломаются тихо: «не знаю» про вход,
сведённое к «нет», гасит человеку живое подключение вместе с персональными ценами.

ЧТО ПРОВЕРЯЕТСЯ ТОЛЬКО ЖИВЬЁМ и потому снимается по умолчанию: подъём Chromium и
открытие страницы сети. Живая проверка включается `KORZINA_LIVE=1` — иначе
обычный прогон ходил бы в сети магазинов на каждой сборке, а это и медленно, и
невежливо по отношению к чужим сайтам (tests/conftest.py запрещает сеть самому
питону, но браузер — отдельный процесс, и запрет его не касается).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config  # noqa: E402
from app.db import init_db  # noqa: E402
from app.shopbrowser import driver, signals, store  # noqa: E402

LIVE = os.getenv("KORZINA_LIVE") == "1"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "b.db"))
    init_db()


# ---------- признаки входа ----------

def test_a_page_that_says_nothing_means_not_knowing():
    """Пустая страница — это «не знаю», а не «не вошёл».

    Разница дорогая. False здесь означал бы «человек вышел», приложение погасило
    бы подключение, и вместе с ним пропали бы персональные цены — молча и на
    ровном месте.
    """
    assert signals.logged_in("magnit", "") is None
    assert signals.logged_in("magnit", "   ") is None


def test_a_word_a_guest_never_sees_is_proof_enough():
    """«Выйти» и «Здравствуйте, имя» гостю показать неоткуда — это и есть признак."""
    assert signals.logged_in("magnit", "Профиль Бонусы 340 Выйти") is True
    assert signals.logged_in("pyaterochka", "Здравствуйте, Алексей") is True


def test_an_anonymous_header_is_a_no():
    assert signals.logged_in("pyaterochka", "Войти Каталог Акции") is False
    # Настоящая ловушка, из-за которой признаки пришлось разводить на два сорта:
    # у Ленты гостю написано «Вход в личный кабинет», и по слову «личный кабинет»
    # приложение объявило бы вошедшим любого гостя.
    assert signals.logged_in("lenta", "Вход в личный кабинет") is False


def test_the_guest_menu_of_magnit_is_not_a_login():
    """Настоящая шапка magnit.ru для ГОСТЯ, снятая 17.09.2026.

    Из-за неё пришлось выбросить целую полку «подсказочных» признаков. «Карта
    Магнит Плюс» и «Бонусы» — это его собственное меню, одинаковое для всех, а
    слова «Войти» в видимом тексте нет вовсе: кнопка без подписи. Приложение
    объявило вошедшим человека, который не входил, и предложило запомнить вход,
    которого нет.
    """
    guest = ("магнит м.косметик аптека еще 8 800 200-90-02 карта магнит плюс "
             "акции магазины бонусы от партнеров поддержка вакансии партнерам")
    assert signals.logged_in("magnit", guest) is None,         "меню гостя снова принято за признак входа"
    assert signals.account("magnit", guest) is None,         "кабинету гостя придумана подпись — он решит, что вошёл"


def test_the_login_button_at_the_bottom_is_seen_too():
    """У Магнита кнопка «Войти» стоит в НИЖНЕЙ панели, а не в шапке.

    Промах, найденный на первом же живом снимке 17.09.2026: смотрели только начало
    страницы, а там её нет вовсе — приложение отвечало «не разобрал» там, где ответ
    был прямо на экране, внизу. Теперь смотрим оба конца; середину — нет, там
    витрина и подвал с чужими словами про чужие входы.
    """
    page = ("магнит м.косметик аптека карта магнит плюс акции магазины бонусы от партнеров "
            + "товар " * 900 + " главная каталог корзина моя карта войти")
    assert signals.logged_in("magnit", page) is False
    assert signals.account("magnit", page) is None


def test_a_cookie_is_stronger_than_any_text():
    """Кука, которую сеть ставит после входа, не зависит от вёрстки вообще.

    Там, где мы знаем её имя достоверно (у Магнита это mg_at — его же шлюз ждёт
    оттуда ключ), это доказательство надёжнее любых слов на странице.
    """
    jar = [{"name": "mg_udi", "value": "устройство"}, {"name": "mg_at", "value": "ключ"}]
    assert signals.logged_in_by_cookies("magnit", jar) is True
    # Пустое значение — не вход: куку заводят и до него.
    assert signals.logged_in_by_cookies("magnit", [{"name": "mg_at", "value": ""}]) is None
    # Имя куки не выдумываем: у остальных сетей оно достоверно не установлено.
    assert signals.logged_in_by_cookies("lenta", jar) is None


def test_a_guard_page_is_not_an_answer_about_login():
    """Нас встретила проверка, а не магазин: про вход она не говорит ничего.

    Это не повод её обходить — экран показывает проверку человеку, проходит он.
    """
    guard = "Не удалось загрузить сайт. Подтвердите, что вы не робот"
    assert signals.guarded(guard) is True
    assert signals.logged_in("dixy", guard) is None


def test_the_card_number_is_read_when_it_is_visible():
    assert signals.account("lenta", "Карта № ••••1234 Выйти") == "Карта Лента •••1234"
    assert signals.account("pyaterochka", "Здравствуйте, Алексей") == "Карта X5 (Алексей)"
    # Не нашли подписи — None. Общее имя карты подставляет тот, кто ЗНАЕТ, что
    # человек вошёл (driver.look), а не этот разбор: гостю оно соврало бы.
    assert signals.account("samokat", "Выйти") is None


def test_points_are_none_when_not_shown():
    """Ноль баллов и непрочитанные баллы — разные вести, и путать их нельзя."""
    assert signals.points("Профиль Выйти") is None
    assert signals.points("340 бонусов") == 340.0
    assert signals.points("1 250 баллов") == 1250.0


# ---------- хранение входа ----------

def test_a_saved_login_comes_back(db):
    state = {"cookies": [{"name": "mg_at", "value": "секрет", "domain": ".magnit.ru"}],
             "origins": []}
    store.save("magnit", state, account="Карта Магнит Плюс •••4321")

    assert store.load("magnit") == state
    assert store.saved_stores() == ["magnit"]


def test_what_the_screen_sees_has_no_cookies_in_it(db):
    """Экрану нужно «когда сохранён» и «как подписан», а куки ему не нужны ни на что.

    Таскать их в шаблон значило бы однажды их там случайно и напечатать.
    """
    store.save("magnit", {"cookies": [{"name": "mg_at", "value": "секрет"}], "origins": []},
               account="Карта Магнит Плюс")

    seen = store.about("magnit")
    assert seen["account"] == "Карта Магнит Плюс"
    assert seen["cookies"] == 1
    assert "секрет" not in str(seen)


def test_forgetting_a_login_leaves_nothing(db):
    store.save("magnit", {"cookies": [], "origins": []})
    store.forget("magnit")

    assert store.load("magnit") is None
    assert store.about("magnit") is None
    assert store.saved_stores() == []


def test_an_unbelievably_large_state_is_refused(db):
    """Миллион знаков — это не состояние входа, а чья-то выгрузка."""
    with pytest.raises(ValueError):
        store.save("magnit", {"junk": "x" * 1_200_000})


def test_nothing_is_saved_for_a_store_that_was_never_entered(db):
    assert store.load("magnit") is None
    assert store.saved_stores() == []


# ---------- границы ----------

def test_a_foreign_address_is_not_opened():
    """Адрес приходит формой, а форма приходит откуда угодно.

    Открыть по просьбе снаружи произвольный сайт значило бы сделать из приложения
    пересыльщика, которым чужими руками ходят куда угодно с нашего адреса.
    """
    assert driver._same_chain("pyaterochka", "https://5ka.ru/catalog/") is True
    assert driver._same_chain("pyaterochka", "https://www.5ka.ru/catalog/") is True
    # Подстрока — не имя узла: этот адрес принадлежит чужому сайту.
    assert driver._same_chain("pyaterochka", "https://5ka.ru.zlo.example/") is False
    assert driver._same_chain("pyaterochka", "https://magnit.ru/") is False
    assert driver._same_chain("pyaterochka", "не адрес вовсе") is False


class _FlakyPage:
    """Страница, у которой не выходит первый снимок. Ровно так ведёт себя живой
    Chromium на только что поднятом виртуальном экране: окно ещё не показано."""

    def __init__(self, fails: int):
        self.left = fails
        self.tries = 0
        self.url = "https://magnit.ru/"

    def screenshot(self, **_kw):
        self.tries += 1
        if self.left > 0:
            self.left -= 1
            raise RuntimeError(
                "Page.screenshot: Protocol error (Page.captureScreenshot): "
                "Unable to capture screenshot")
        return b"\xff\xd8\xff" + b"0" * 900

    def title(self):
        return "Магнит"


def test_the_first_snapshot_may_fail_and_the_screen_still_gets_a_picture():
    """Первый снимок на свежем экране падает, второй проходит — замерено живьём.

    Без повтора человек, открывший магазин сразу после перезапуска сервера, видел
    бы «страницу не удалось снять» вместо витрины, и экран «Кабинет» был бы для
    него сломан целиком: на снимках держится всё — и вход, и нажатия.
    """
    live = driver._Live.__new__(driver._Live)
    live.page = _FlakyPage(fails=1)
    got = driver._snapshot(live)
    assert got.jpeg.startswith(b"\xff\xd8")
    assert live.page.tries == 2, "повторили ровно один раз, а не смолчали"


def test_a_page_that_never_draws_is_named_and_not_hidden():
    """Повтор не должен превращаться в вечное ожидание и в молчание.

    Сломался снимок навсегда — человеку надо сказать об этом, а не показывать
    вчерашнюю картинку и не крутить попытки без конца.
    """
    live = driver._Live.__new__(driver._Live)
    live.page = _FlakyPage(fails=99)
    with pytest.raises(driver.BrowserUnavailable) as beda:
        driver._snapshot(live)
    assert "снять" in str(beda.value)
    assert live.page.tries == driver.SHOT_TRIES


def test_only_known_keys_may_be_pressed():
    """Имя клавиши тоже приходит формой. Список закрытый нарочно."""
    assert set(driver.KEYS) == {"enter", "backspace", "tab", "escape", "delete"}
    with pytest.raises(driver.BrowserUnavailable):
        driver.key("magnit", "79990000001", "F12")


def test_every_chain_with_a_cabinet_has_an_entrance():
    """У каждой сети, где вход что-то даёт, браузеру есть куда её открыть."""
    from app import store_accounts

    assert set(driver.LOGIN_URL) == set(store_accounts.ABILITIES)
    for chain in store_accounts.ABILITIES:
        assert signals.ENTERED.get(chain), f"{chain}: не описан признак входа"
        # Запись в словаре — половина дела. Признак входа у всех шести сетей ОДИН
        # (signals.ENTERED собран из одного значения), и такой словарь не может
        # покраснеть никогда — значит сторож обязан смотреть, что признак
        # ДЕЙСТВУЕТ на этой сети, а не что строчка в нём есть.
        assert signals.logged_in(chain, "Здравствуйте, Алексей Мои заказы Выйти") is True, \
            f"{chain}: признак входа записан, но не срабатывает"
        assert signals.logged_in(chain, "Каталог Акции Войти Регистрация") is False, \
            f"{chain}: гость не опознан как гость"
        assert signals.CARD_NAME.get(chain), f"{chain}: не описано имя карты"


# ---------- живая проверка ----------

@pytest.mark.skipif(not LIVE, reason="живая проверка включается KORZINA_LIVE=1")
def test_the_browser_opens_a_store_page():
    """Chromium поднимается и показывает страницу сети.

    Это проверка НАШЕГО кода, а не чужой защиты: встреть нас проверка «я не
    робот», снимок всё равно придёт — и именно его увидит человек, чтобы её
    пройти. Поэтому сторож смотрит на то, что снимок есть и не пуст.
    """
    shot = driver.open_store("magnit", "79990000001")
    try:
        assert shot.jpeg[:2] == b"\xff\xd8", "снимок пришёл не картинкой"
        assert len(shot.jpeg) > 5000, "снимок подозрительно пуст"
        assert shot.url.startswith("https://magnit.ru")
    finally:
        driver.close("magnit", "79990000001")


# ---------- цены из кабинета ----------

def test_the_price_is_taken_from_the_product_node_and_no_other():
    """В графе рядом с товаром лежат крошки, организация и соседи. Берём товар.

    Ошибка здесь самая дорогая во всём сборе: цена не того узла запишется человеку
    как его цена, расчёт соврёт, и заметить это будет нечем — число выглядит
    совершенно обычно.
    """
    from app.shopbrowser import collect

    graph = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Молочное"}]},
        {"@type": "Organization", "name": "Сеть"},
        {"@type": "Product", "name": "Молоко 900 мл",
         "offers": {"@type": "Offer", "price": "89,90", "priceCurrency": "RUB"}},
    ]})
    assert collect.price_in_ld([graph]) == 89.9


def test_a_page_without_a_product_gives_no_price():
    """Нет разметки товара — нет цены. Гадать по видимому тексту не станем.

    На карточке рядом с ценой живут старая цена, цена за килограмм, «−38 %», цена
    подписки и цена соседнего товара. Промах в эту сторону никто не заметит.
    """
    from app.shopbrowser import collect

    assert collect.price_in_ld([json.dumps({"@type": "Organization", "name": "Сеть"})]) is None
    assert collect.price_in_ld(['{это не json']) is None
    assert collect.price_in_ld([]) is None
    assert collect.price_in_ld(None) is None


def test_a_price_is_cleaned_the_same_way_the_receiver_cleans_it():
    """Чистка цены одна на всё приложение: две разошлись бы молча."""
    from app import pricebundle
    from app.shopbrowser import collect

    for raw in ("1 299,99 ₽", "89.90", 164.99):
        one = collect.price_in_ld([json.dumps(
            {"@type": "Product", "offers": {"price": raw}})])
        assert one == pricebundle._number(raw), f"расхождение на «{raw}»"


def test_prices_are_collected_only_where_the_receiver_takes_them():
    """Сбор браузером — ровно для тех сетей, чьи пакеты принимает приёмник.

    У остальных цены сервер спрашивает сам, и собрать ещё и браузером значило бы
    смешать два источника разной свежести.
    """
    from app import pricebundle
    from app.shopbrowser import collect

    assert collect.CHAINS == tuple(pricebundle.BROWSER_STORES)
    assert set(collect.CHAINS) == {"pyaterochka", "samokat"}


def test_collecting_without_a_login_says_what_to_do(db):
    """Без сохранённого входа сеть покажет полочные цены вместо личных.

    Записать их как личные значило бы соврать в расчёте, поэтому сбор не идёт, а
    человек читает, что сделать.
    """
    from app.shopbrowser import collect

    got = collect.refresh("pyaterochka", "79990000001")
    assert got["saved"] == 0
    assert "войдите" in got["note"].lower()


def test_collecting_a_chain_the_server_asks_itself_is_refused(db):
    from app.shopbrowser import collect

    got = collect.refresh("lenta", "79990000001")
    assert got["saved"] == 0
    assert "спрашивает у неё само" in got["note"]


# ---------- набор уходит в поле, а не внутрь чужой рамки ----------

class _PageWithFocus:
    """Страница, у которой можно сказать, что на ней сейчас выбрано."""

    def __init__(self, spot):
        self.spot = spot
        self.typed = []
        self.url = "https://magnit.ru/"

    def evaluate(self, script, *args):
        if "activeElement" in script:
            return self.spot
        return 0

    class _Keyboard:
        def __init__(self, page):
            self.page = page

        def type(self, text, **_kw):
            self.page.typed.append(text)

    @property
    def keyboard(self):
        return _PageWithFocus._Keyboard(self)

    def screenshot(self, **_kw):
        return b"\xff\xd8\xff" + b"0" * 800

    def title(self):
        return "Магнит"


def _pump_with(page, monkeypatch, chain="magnit", phone="79990000001"):
    """Подменяем очередь браузера: настоящая живёт в своём потоке с Chromium."""
    live = driver._Live.__new__(driver._Live)
    live.page = page
    live.touched_at = 0.0
    state = {"live": {driver.sid_of(chain, phone): live}}

    class _Fake:
        def call(self, job, timeout=None):
            return job(state)

    monkeypatch.setattr(driver, "_pump", _Fake())


def test_text_goes_into_a_field_and_the_field_is_named(monkeypatch):
    """Выбрано поле — набираем и говорим, какое именно."""
    page = _PageWithFocus({"kind": "field", "label": "Телефон"})
    _pump_with(page, monkeypatch)

    shot, where = driver.type_text("magnit", "79990000001", "916")

    assert page.typed == ["916"]
    assert where == {"typed": True, "kind": "field", "label": "Телефон"}
    assert shot.jpeg.startswith(b"\xff\xd8")


def test_the_check_frame_gets_the_typing_because_its_field_is_inside():
    """Выбрана рамка проверки — НАБИРАЕМ, и вот почему это изменилось.

    Раньше здесь стоял запрет: рамка — значит набирать некуда. Рассуждение
    верное ровно для одного вида проверки, галочки «я не робот», у которой поле
    снаружи. 20.09.2026 владелец встретил второй вид и не смог войти: Магнит
    показал Yandex SmartCaptcha «Введите текст с картинки», а у неё поле СВОЁ,
    внутри рамки. Он нажимал прямо в это поле и получал «Набирать некуда» —
    запрет не давал пройти проверку, ради которой окно и открывали.

    Различить два вида снаружи нельзя: рамка с чужого сайта. Значит выбор не
    между «набирать» и «не набирать», а между запретом и честным
    предупреждением. Запрет ломает рабочий случай наглухо; предупреждение
    оставляет оба пути и говорит правду о том, куда ушли буквы.
    """
    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    try:
        page = _PageWithFocus({"kind": "frame", "label": "SmartCaptcha"})
        _pump_with(page, monkeypatch)

        _shot, where = driver.type_text("magnit", "79990000001", "1234")

        assert page.typed == ["1234"], "снова не пускаем буквы в поле проверки"
        assert where["typed"] is True and where["kind"] == "frame"
    finally:
        monkeypatch.undo()


def test_nothing_is_typed_when_no_field_is_chosen(monkeypatch):
    """Ничего не выбрано — тоже не набираем: набор в никуда выглядит как удачный."""
    page = _PageWithFocus({"kind": "none"})
    _pump_with(page, monkeypatch)

    _shot, where = driver.type_text("magnit", "79990000001", "1234")

    assert page.typed == []
    assert where["typed"] is False


def test_a_page_that_cannot_be_asked_still_gets_the_text(monkeypatch):
    """Не удалось спросить страницу — набираем всё равно.

    Опрос нужен, чтобы предупредить человека, а не чтобы мешать ему работать:
    сломайся он — и вход встал бы намертво там, где раньше работал.
    """
    page = _PageWithFocus({"kind": "field", "label": ""})

    def refuse(_script, *_args):
        raise RuntimeError("страница не отвечает на опрос")

    page.evaluate = refuse
    _pump_with(page, monkeypatch)

    _shot, where = driver.type_text("magnit", "79990000001", "916")

    assert page.typed == ["916"]
    assert where["typed"] is True and where["kind"] == "unknown"


# ---------- протяжка ----------
class _PageWithMouse(_PageWithFocus):
    """Страница, которая запоминает движения мыши: нажатия, путь и отпускания."""

    def __init__(self):
        super().__init__({"kind": "none"})
        self.moves = []
        self.downs = 0
        self.ups = 0

    class _Mouse:
        def __init__(self, page):
            self.page = page

        def move(self, x, y, **_kw):
            self.page.moves.append((round(x), round(y)))

        def down(self, **_kw):
            self.page.downs += 1

        def up(self, **_kw):
            self.page.ups += 1

        def click(self, x, y, **_kw):
            self.page.moves.append(("click", round(x), round(y)))

    @property
    def mouse(self):
        return _PageWithMouse._Mouse(self)


def test_a_drag_goes_step_by_step_not_in_one_jump(monkeypatch):
    """Один прыжок из точки в точку ползунки не принимают.

    Они слушают mousemove и при единственном событии считают, что мышь
    телепортировалась, — так отличают человека от программы. Проверка Перекрёстка
    («Разверните картинку горизонтально», замер 20.09.2026) — ровно такой ползунок.
    """
    page = _PageWithMouse()
    _pump_with(page, monkeypatch)

    driver.drag("magnit", "79990000001", 60, 700, 300, 700)

    assert page.downs == 1 and page.ups == 1
    assert len(page.moves) > 10, "путь должен проходиться частями, как рукой"
    assert page.moves[0] == (60, 700), "начинать надо там, где человек взялся"
    assert page.moves[-1] == (300, 700), "и заканчивать там, где отпустил"


def test_a_drag_stays_inside_the_window(monkeypatch):
    """Точки за краем снимка прижимаются к краю, а не уходят в чужую страницу."""
    page = _PageWithMouse()
    _pump_with(page, monkeypatch)

    driver.drag("magnit", "79990000001", -50, -50, 9999, 9999)

    assert page.moves[0] == (0, 0)
    assert page.moves[-1] == (driver.WIDTH, driver.HEIGHT)


def test_the_cabinet_screen_can_ask_for_a_drag():
    """Протяжка бесполезна, пока до неё не дотянется экран: сторож на проводку."""
    from app.web.screens import cabinet

    source = __import__("inspect").getsource(cabinet)
    assert '"drag"' in source and "driver.drag(" in source


def test_the_snapshot_sends_a_drag_and_not_a_click_as_well():
    """Разметка обязана отличать протяжку от нажатия и не слать оба сразу.

    Ползунок от лишнего нажатия прыгает обратно, а дрожание руки на телефоне без
    порога превращало бы каждое касание в протяжку в никуда.
    """
    with open(os.path.join(ROOT, "app", "web", "templates", "cabinet.html"),
              encoding="utf-8") as fh:
        page = fh.read()

    assert 'do: "drag"' in page, "экран не умеет послать протяжку"
    assert "DRAG_MIN" in page, "нет порога, отделяющего протяжку от дрожания руки"
    assert "dragged" in page, "нажатие не гасится после протяжки"
