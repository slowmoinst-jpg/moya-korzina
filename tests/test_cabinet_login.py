"""Вход в шесть сетей: вердикт про вход, подсказка по шагам и экран кабинета.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ И ПОЧЕМУ ИМЕННО ЭТО. Живого браузера у набора тестов нет и
быть не должно: Chromium поднимается на сервере, а ходить в чужие магазины на
каждой сборке и медленно, и невежливо. Зато ломается во входе не браузер, а разбор
и слова — и ломается тихо:

    «НЕ РАЗОБРАЛ», СВЕДЁННОЕ К «НЕ ВОШЛИ», гасит человеку живое подключение, а
    вместе с ним персональные цены. Обратная ошибка хуже вдвое: объявленный
    вошедшим гость нажмёт «Запомнить вход» и получит пустые куки — приложение
    будет считать, что кабинет подключён, а корзина никуда не поедет. Обе поломки
    здесь уже случались, поэтому каждая из шести сетей проверяется поддельной
    страницей в обоих состояниях.

    ЛОВУШКА ЛЕНТЫ. Гостю она пишет «Вход в личный кабинет». По слову «личный
    кабинет» приложение однажды объявило вошедшим любого её гостя.

    ПОДСКАЗКА — ЭТО ОБЕЩАНИЕ. Сказать «нажмите „Войти“ в шапке» там, где сеть
    встречает наш сервер проверкой, значит послать человека искать то, чего на
    экране нет. Поэтому сторож смотрит, что у сетей, которые эту проверку нам
    показывают, про неё и сказано.

ПОДДЕЛЬНЫЕ СТРАНИЦЫ НИЖЕ — СОЧИНЁННЫЕ, и это честно сказано у каждой. Снята с
живой страницы только шапка гостя Магнита (17.09.2026) — она и стоит здесь
дословно. Остальные собраны из слов, которые разбор ищет: они проверяют НАШ
разбор, а не чужую вёрстку, и выдавать их за слепки было бы враньём.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import users  # noqa: E402
from app.shopbrowser import driver, signals  # noqa: E402

PHONE = "79990000042"
CHAINS = ("magnit", "pyaterochka", "samokat", "lenta", "vkusvill", "dixy", "metro",
          "perekrestok")

# Страница ГОСТЯ. У Магнита — настоящая шапка, снятая живьём; у остальных пяти
# сочинённая из тех же слов, которые сеть показывает невошедшему.
GUEST = {
    "magnit": ("магнит м.косметик аптека еще 8 800 200-90-02 карта магнит плюс "
               "акции магазины бонусы от партнеров поддержка вакансии партнерам "
               + "товар " * 400 + " главная каталог корзина моя карта войти"),
    "pyaterochka": "Пятёрочка Каталог Акции Доставка Войти Зарегистрироваться",
    "samokat": "Самокат Укажите адрес доставки Каталог Войти",
    # Та самая ловушка: гостю Ленты написано «Вход в личный кабинет».
    "lenta": "Лента Каталог Акции Магазины Вход в личный кабинет Регистрация",
    "vkusvill": "ВкусВилл Каталог Доставка Войти Регистрация",
    "dixy": "Дикси Каталог Акции Клуб Друзей Войти",
    "metro": "METRO Каталог Торговые центры Доставка Войти Регистрация",
    "perekrestok": "Перекрёсток Каталог Акции Доставка Войти Регистрация",
}

# Страница ВОШЕДШЕГО: слова, которых гостю показать неоткуда.
INSIDE = {
    "magnit": "Здравствуйте, Алексей Карта Магнит Плюс ••••4321 Мои заказы Выйти",
    "pyaterochka": "Здравствуйте, Алексей Мои купоны Выйти",
    "samokat": "Алексей Мои заказы Выйти Корзина",
    "lenta": "Карта № ••••1234 Мои заказы Выйти Вход в личный кабинет",
    "vkusvill": "Здравствуйте, Алексей Мои заказы 340 бонусов Выйти",
    "dixy": "Здравствуйте, Алексей Клуб Друзей Мои покупки Выйти",
    "metro": "Здравствуйте, Алексей Мои заказы Выйти",
    "perekrestok": "Здравствуйте, Алексей Мои заказы Выйти",
}

# Страница ПРОВЕРКИ: не магазин, а его защита. Слова взяты из того, что сети
# показали нашему серверу 17.09.2026 (замер в app/shopbrowser/__init__.py).
GUARD = {
    "magnit": "Подтвердите, что вы не робот",
    "pyaterochka": "Проверка браузера. Подождите, пожалуйста",
    "samokat": "Проверка браузера",
    "lenta": "Не удалось загрузить сайт. Проверка браузера QRATOR",
    "vkusvill": "Я не робот",
    "dixy": "Не удалось загрузить сайт",
    # METRO нашему серверу витрину отдаёт (замер 19.09.2026: 200 и почти два
    # мегабайта), но сторож обязан уметь и её проверку: сеть вправе показать её
    # завтра, и разбор не должен счесть заглушку кабинетом.
    "metro": "Подтвердите, что вы не робот",
    # НАСТОЯЩАЯ страница Перекрёстка, снятая с боевого сервера 20.09.2026. Она
    # отличается от всех прочих: сеть не просто просит подтвердить, а даёт
    # головоломку с ползунком и называет наш адрес прямо на странице.
    "perekrestok": ("Мы хотим убедиться, что имеем дело именно с вами, а не с ботом. "
                    "Пожалуйста, пройдите проверку, чтобы получить доступ к сайту. "
                    "Ваш IP: 203.0.113.7 Разверните картинку горизонтально"),
}

# ЧТО ИМЕННО СКАЗАТЬ ПРО КАЖДУЮ ИЗ ЭТИХ СТРАНИЦ. Здесь стояло одно сообщение на
# все шесть — про «я не робот», — и оно закрепляло домысел: у Дикси на странице
# написана ровно одна фраза «Не удалось загрузить сайт», никакой проверки там не
# наблюдали. Человек читал в панели «пройдите её, нажимая по снимку», смотрел на
# снимок, тыкал наугад и решал, что сломано приложение, — то есть получал ровно ту
# поломку, ради которой сообщение и написано.
#
# У Ленты на той же странице стоят оба слова, и это всё-таки проверка: на ней есть
# что нажать. Поэтому проверка сильнее глухого отказа, а не наоборот.
SAYS = {
    "magnit": "check",
    "pyaterochka": "check",
    "samokat": "check",
    "lenta": "check",
    "vkusvill": "check",
    "dixy": "blocked",
    "metro": "check",
    # Проверка, а не глухой отказ: на странице есть что тянуть, и человек её проходит.
    "perekrestok": "check",
}


# ---------- вердикт про вход: все шесть сетей ----------

@pytest.mark.parametrize("chain", CHAINS)
def test_a_guest_page_is_never_called_a_login(chain):
    """Гостя нельзя объявить вошедшим НИ В ОДНОЙ сети.

    Это самая дорогая ошибка экрана: человек нажимает «Запомнить вход», сеть
    отдаёт куки гостя, приложение пишет «кабинет подключён» — и молча возит
    корзину в никуда. Ответ «не разобрал» здесь допустим, «вошли» — нет.
    """
    seen = driver.see(chain, GUEST[chain], [])

    assert seen["logged_in"] is not True, f"{chain}: гость объявлен вошедшим"
    assert seen["account"] is None, f"{chain}: кабинету гостя придумана подпись"
    assert "вошли" in seen["says"].lower(), f"{chain}: экрану нечего сказать про вход"


@pytest.mark.parametrize("chain", CHAINS)
def test_a_page_of_someone_inside_is_read_as_a_login(chain):
    """Слова, которых гостю не показывают, читаются как вход — во всех шести."""
    seen = driver.see(chain, INSIDE[chain], [])

    assert seen["logged_in"] is True, f"{chain}: вошедший не опознан"
    assert seen["says_kind"] == "ok"
    assert "Запомнить вход" in seen["says"], f"{chain}: не сказано, что делать дальше"
    # Подпись кабинета: номер карты, имя или хотя бы имя самой карты сети.
    assert seen["account"], f"{chain}: вошедший кабинет остался без подписи"


def test_the_words_a_guest_of_lenta_sees_are_not_a_login():
    """ЛОВУШКА ЛЕНТЫ, на которой разбор уже ломался.

    У неё на странице написано «Вход в личный кабинет» — и это приглашение войти,
    а не признак входа. Разбор по словам «личный кабинет» объявлял вошедшим
    любого её гостя, и приложение предлагало запомнить несуществующий вход.
    """
    seen = driver.see("lenta", "Лента Вход в личный кабинет", [])

    assert seen["logged_in"] is False
    assert seen["account"] is None
    assert seen["points"] is None, "баллы прочитаны у того, кто не вошёл"

    # А когда вошёл — те же слова на странице входу уже не мешают.
    assert driver.see("lenta", INSIDE["lenta"], [])["logged_in"] is True


@pytest.mark.parametrize("chain", CHAINS)
def test_a_check_page_says_nothing_about_a_login(chain):
    """Встретила не витрина — про вход она не говорит ничего, и это отдельный ответ.

    Ни «вошли», ни «не вошли»: на этой странице магазина нет вовсе. Тон —
    сообщения, а не ошибки: красная плашка объявила бы сломанным то, что работает.
    И слова разные, потому что страницы разные: проверку человек ПРОХОДИТ, а
    глухое «Не удалось загрузить сайт» проходить нечем — на нём ни одной кнопки.
    """
    seen = driver.see(chain, GUARD[chain], [])

    assert seen["guarded"] is True, f"{chain}: проверка не опознана"
    assert seen["guard"] == SAYS[chain], f"{chain}: чужая страница принята за другую"
    assert seen["logged_in"] is None, f"{chain}: по странице проверки вынесен вердикт о входе"
    assert seen["says_kind"] == "info", "проверка показана как ошибка приложения"

    if SAYS[chain] == "check":
        assert seen["says"] == signals.GUARD_NOTE
        assert "сами" in seen["says"], "не сказано, что проверку проходит человек"
    else:
        assert seen["says"] == signals.BLOCKED_NOTE
        assert "нечего" in seen["says"], "человека зовут нажимать туда, где нет кнопок"


def test_a_page_that_never_said_robot_is_not_called_a_robot_check():
    """Глухая страница не объявляется проверкой «я не робот». Это был домысел.

    По замеру репозитория (docs/design/shopbrowser.md) Лента и Дикси отдают нашему
    серверу «Не удалось загрузить сайт», и никакого «я не робот» на ней не
    наблюдали. Обещать его — значит послать человека нажимать по странице, где
    стоит одна фраза: он тыкает наугад, жмёт «Обновить» и уходит чинить то, что не
    ломалось. Тем же текстом объявлялась бы «проверкой» и любая настоящая неудача
    загрузки.
    """
    seen = driver.see("dixy", "Не удалось загрузить сайт", [])

    assert seen["says"] == signals.BLOCKED_NOTE
    assert "я не робот" not in seen["says"], "обещана проверка, которой на странице нет"
    assert "нажимая по снимку" not in seen["says"], "палец зовут в пустоту"
    # А там, где проверка написана словами, про неё и сказано.
    assert driver.see("magnit", "Подтвердите, что вы не робот", [])["says"] == signals.GUARD_NOTE


def test_a_cookie_outweighs_the_text_but_only_where_its_name_is_known():
    """Кука сильнее текста: она не зависит от вёрстки вообще.

    У Магнита имя известно достоверно (его же шлюз ждёт ключ из mg_at). У
    остальных пяти оно НЕ установлено, и выдумывать его нельзя: чужая кука,
    случайно совпавшая с догадкой, объявила бы вошедшим гостя.
    """
    jar = [{"name": "mg_at", "value": "ключ"}]
    seen = driver.see("magnit", GUEST["magnit"], jar)

    assert seen["by_cookie"] is True
    assert seen["logged_in"] is True, "кука входа проиграла тексту витрины"

    # Та же кука у другой сети не значит ничего: её имя там неизвестно.
    assert driver.see("lenta", GUEST["lenta"], jar)["logged_in"] is False
    for chain in CHAINS:
        if chain == "magnit":
            continue
        assert not signals.AUTH_COOKIE[chain], \
            f"{chain}: появилось имя куки — проверьте, что оно снято с живой сети, а не придумано"


def test_a_page_we_did_not_understand_gets_no_card_number():
    """«Не разобрал» и подпись кабинета вместе не ходят.

    Приложение честно говорило «не знаю, вошли вы или нет» — и тут же подписывало
    кабинет «Карта ВкусВилл •••7788», вычитанной образцом, который живой страницей
    не подтверждён (провенанс — в signals.ACCOUNT). Дальше эта подпись ложилась и в
    рабочее место, и в store_accounts: человек видел правдоподобную карту, верил,
    что подключение живое, и получал пустой перенос корзины. Поправить это потом
    нечем — в базе лежит подпись, снятая с чужой витрины.
    """
    seen = driver.see("vkusvill", "Ваша карта № ••••7788 и ещё 250 бонусов", [])

    assert seen["logged_in"] is None, "страница про вход ничего не сказала"
    assert seen["account"] is None, "кабинету без доказанного входа придумана подпись"
    assert seen["points"] is None, "баллы прочитаны у того, про кого вход не доказан"

    # А вошедшему — и подпись, и баллы: там они уже про него.
    inside = driver.see("vkusvill", INSIDE["vkusvill"], [])
    assert inside["logged_in"] is True
    assert inside["account"] and inside["points"] == 340


def test_an_empty_page_is_not_an_answer():
    """Пустая страница — «не знаю». False здесь погасил бы живое подключение."""
    for chain in CHAINS:
        seen = driver.see(chain, "", [])
        assert seen["logged_in"] is None
        assert "Не разобрал" in seen["says"]


# ---------- подсказка: куда нажимать в каждой сети ----------

def test_every_chain_has_step_by_step_directions():
    """У всех шести сетей описан вход: где искать, что нажимать, по чему видно.

    Сеть без подсказки — это экран, на котором человек с телефоном гадает, куда
    нажать, глядя на чужую витрину в 820 точек шириной. Ради этого весь экран и
    затевался.
    """
    assert set(signals.ENTRANCE) == set(driver.LOGIN_URL)

    for chain in CHAINS:
        hint = signals.entrance(chain)
        assert hint, f"{chain}: подсказки по входу нет"
        assert hint["where"].strip(), f"{chain}: не сказано, где искать вход"
        assert len(hint["steps"]) >= 3, f"{chain}: шагов меньше трёх — это не подсказка"
        assert all(step.strip() for step in hint["steps"])
        assert hint["sign"].strip(), f"{chain}: не сказано, по чему видно, что вошёл"


def test_the_directions_admit_that_the_page_is_the_boss():
    """Последним шагом у всех сказано: на странице написано главнее подсказки.

    Подсказка написана по вчерашней странице, а человек смотрит на сегодняшнюю.
    Когда они разойдутся — прав он, и сказать это надо заранее: иначе человек
    ищет кнопку, которой уже нет, и решает, что сломано приложение.
    """
    for chain in CHAINS:
        assert signals.entrance(chain)["steps"][-1] == signals.LAST_STEP


def test_the_chains_that_meet_us_with_a_check_say_so_in_advance():
    """Четыре сети встречают НАШ сервер своей проверкой — и об этом сказано заранее.

    Замер 17.09.2026 (шапка app/shopbrowser/__init__.py): Магнит и ВкусВилл
    отдают страницу сразу, а Пятёрочка, Самокат, Лента и Дикси — свою проверку.
    Человек, которому обещали витрину, а показали «Не удалось загрузить сайт»,
    решит, что сломалось приложение, и уйдёт. Предупреждённый — просто пройдёт
    проверку.
    """
    for chain in ("pyaterochka", "samokat", "lenta", "dixy"):
        assert signals.entrance(chain)["trouble"], f"{chain}: про проверку не предупредили"
    for chain in ("magnit", "vkusvill"):
        assert not signals.entrance(chain)["trouble"], \
            f"{chain}: обещана проверка, которой эта сеть нашему серверу не показывает"


def test_the_directions_never_promise_what_we_do_not_do():
    """Подсказка не обещает того, чего приложение не делает.

    Сегодня вход в окне проходит человек: код из СМС вводит он. Подсказка — то
    место, где легче всего пообещать за него одним ласковым словом, и человек,
    поверивший обещанию, просто не введёт код. Отдельно сторожатся слова про
    расширение-сборщик: той дороги нет с 17.09.2026.
    """
    forbidden = ("автоматически войд", "введём за вас",
                 "расширени", "сборщик", "extension")
    for chain in CHAINS:
        hint = signals.entrance(chain)
        whole = " ".join([hint["where"], hint["sign"], hint["trouble"], *hint["steps"]]).lower()
        for word in forbidden:
            assert word not in whole, f"{chain}: в подсказке обещано «{word}»"


def test_an_unknown_chain_gets_no_invented_directions():
    # Азбука Вкуса взята нарочно: она закрыта даже для домашнего адреса владельца
    # (замер 19.09.2026, «Кажется, вы используете VPN»), окна у неё нет и не будет.
    # Прежде здесь стоял Перекрёсток — он перестал быть незнакомым 21.09.2026.
    assert signals.entrance("azbuka") is None
    assert signals.entrance("") is None


# ---------- адрес, по которому открывается окно ----------

def test_the_guards_here_cover_every_chain_the_browser_knows():
    """Список сетей в этом файле не отстаёт от того, что умеет окно.

    Он написан руками — у каждой сети свои образцы страниц, и вывести их неоткуда.
    Значит седьмая сеть, добавленная в driver.LOGIN_URL, молча осталась бы вне
    ВСЕХ сторожей этого файла: и разбора «вошёл или гость», и границы «окно ходит
    только по своей сети», и запрета обещать вход за человека. Так и вышло с
    METRO 19.09.2026 — поэтому расхождение теперь ошибка, а не тишина.
    """
    assert set(CHAINS) == set(driver.LOGIN_URL), \
        "сеть добавили в окно, а образцы её страниц сюда не дописали"
    for chain in CHAINS:
        assert chain in GUEST and chain in INSIDE and chain in GUARD and chain in SAYS, \
            f"{chain}: не хватает образца страницы"


def test_every_chain_opens_on_its_own_site():
    """Окно открывается на своём узле — и проверяется это не по адресу входа.

    Граница «окно ходит только по своей сети» раньше брала узел ИЗ адреса входа.
    Пока там стояла главная, разницы не было, но стоило бы однажды вписать туда
    вход на чужом узле — и граница уехала бы вместе с ним, молча: окно пустили бы
    гулять по всему чужому узлу и закрыли бы ему сам магазин. Теперь дом сети
    записан отдельно, и подмена адреса входа границу не двигает.
    """
    from urllib.parse import urlsplit

    assert set(driver.HOME_URL) == set(driver.LOGIN_URL)
    for chain in CHAINS:
        entry, home = driver.LOGIN_URL[chain], driver.HOME_URL[chain]
        assert entry.startswith("https://"), f"{chain}: вход не по защищённому адресу"
        assert urlsplit(entry).hostname == urlsplit(home).hostname, \
            (f"{chain}: адрес входа увёл на чужой узел. Так тоже бывает, но это "
             "решение про границу — поправьте HOME_URL сознательно")
        assert driver._same_chain(chain, entry), f"{chain}: собственный вход сочтён чужим"


def test_a_foreign_login_address_does_not_move_the_boundary(monkeypatch):
    """Подменённый адрес входа НЕ делает чужой узел своим."""
    monkeypatch.setitem(driver.LOGIN_URL, "lenta", "https://lenta.com.zlo.example/login")

    assert driver._same_chain("lenta", "https://lenta.com.zlo.example/login") is False
    assert driver._same_chain("lenta", "https://lenta.com/") is True


# ---------- экран кабинета ----------

@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение с рабочим местом во временной папке и «установленным» браузером.

    Наличие Chromium подменено нарочно: на машине разработчика его может не быть,
    а экран без него показывает другую страницу — и все сторожа ниже проверяли бы
    пустоту, ничего при этом не покраснев.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(driver, "available", lambda: True)

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def enter(client):
    return client.post("/login", data={"phone": PHONE, "next": "/"})


@pytest.mark.parametrize("chain", CHAINS)
def test_the_screen_shows_the_steps_for_the_chosen_chain(web, chain):
    """Шаги видны на экране — все, и подпись «по чему видно, что вошли» тоже."""
    enter(web)
    page = web.get(f"/cabinet?store={chain}").data.decode("utf-8")
    hint = signals.entrance(chain)

    assert hint["where"][:40] in page, f"{chain}: не сказано, где искать вход"
    for step in hint["steps"]:
        assert step[:40] in page, f"{chain}: шаг «{step[:30]}…» до экрана не доехал"
    assert hint["sign"][:40] in page, f"{chain}: не сказано, по чему видно, что вошёл"


def test_the_screen_warns_about_the_trap_of_lenta(web):
    """Экран Ленты прямо говорит: «Вход в личный кабинет» — это ещё не вход.

    На этой надписи ломался разбор; человек читает её так же и решает, что уже
    внутри, — нажимает «Запомнить вход» и сохраняет куки гостя.
    """
    enter(web)
    page = web.get("/cabinet?store=lenta").data.decode("utf-8")

    assert "Вход в личный кабинет" in page
    assert "ещё НЕ вошли" in page


def test_the_screen_says_who_passes_the_check(web):
    """Проверку «я не робот» и код из СМС вводит человек — сказано словами.

    Это не оговорка, а инструкция: окно ждёт от него действия, и, не сказав
    об этом, экран оставит человека смотреть на чужую страницу и ждать.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert "я не робот" in page and "код из СМС" in page
    # Отставших слов про прежнюю дорогу на экране быть не должно.
    for word in ("расширени", "сборщик", "extension"):
        assert word not in page.lower(), f"на экране осталось слово «{word}»"


def test_the_screen_is_usable_with_a_thumb(web):
    """Телефонные обещания экрана — каждое закрывает свою живую поломку.

    ПАНЕЛЬ ПОД ПАЛЬЦЕМ И НЕ ПРЫГАЕТ. Приклеенная намертво (position:fixed) панель
    уезжает под клавиатуру телефона: та меняет видимую часть окна, а панель
    считает её прежней. Липкая живёт в потоке страницы и остаётся на месте.

    ПОЛЕ ВВОДА РОВНО В ШЕСТНАДЦАТЬ ТОЧЕК. Safari на телефоне зумит всю страницу,
    когда фокус приходит в поле со шрифтом мельче; общий шрифт приложения —
    пятнадцать, и экран подпрыгивал бы на каждом нажатии на поле. Человек в этот
    момент вводит код из СМС.

    СНИМОК МОЖНО ПОКАЗАТЬ В НАТУРАЛЬНУЮ ВЕЛИЧИНУ. Вписанный в 360 точек, он
    уменьшен вдвое с лишним: буквы магазина ростом в три точки, попасть пальцем в
    поле ввода нельзя.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert "position:sticky" in page, "панель действий не держится под пальцем"
    assert "font-size:16px" in page, "поле ввода мельче шестнадцати точек — экран будет прыгать"
    assert 'id="zoom"' in page, "снимок нельзя показать крупнее"
    assert "touch-action:manipulation" in page, "двойное нажатие даст задержку в треть секунды"


def test_the_click_is_translated_into_the_points_of_the_shot(web):
    """Пересчёт нажатия — по размеру самой картинки, и ломать его нечем.

    Снимок 820×1000 показан на экране шириной 360: без пересчёта палец попадал бы
    втрое левее и вдвое выше, чем человек метил. Пересчёт верен ровно до тех пор,
    пока картинка показана ЦЕЛИКОМ: object-fit обрезал бы её или дорисовал поля —
    размер элемента перестал бы совпадать с видимым снимком, и промах рос бы к
    краям. Поэтому сторож смотрит и на формулу, и на то, что её нечем испортить.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    import re

    assert "getBoundingClientRect" in page
    assert "box.width * W" in page and "box.height * H" in page, "пересчёт нажатия пропал"
    # Ищем именно ПРАВИЛО, а не слово: про object-fit в шаблоне написано словами,
    # почему его там нет, и сторож, спотыкающийся о собственное объяснение,
    # научил бы снимать объяснения.
    assert not re.search(r"object-fit\s*:", page), \
        "картинку начали вписывать — пересчёт нажатия соврёт тем сильнее, чем дальше от центра"


def test_what_the_screen_is_told_about_the_login(web, monkeypatch):
    """Вердикт про вход доезжает до экрана готовыми словами, а не собирается там.

    Собранный в JavaScript, он разъехался бы с разбором: признак поправили бы в
    signals.py, а фраза осталась бы вчерашней — и человек читал бы «вы не вошли»
    там, где приложение уже говорит «не разобрал».
    """
    fake = driver.Shot(jpeg=b"\xff\xd8\xff\xe0 poddelka", width=driver.WIDTH,
                       height=driver.HEIGHT, url="https://lenta.com/", title="Лента", at=0.0)
    monkeypatch.setattr(driver, "shot", lambda chain, phone: fake)
    monkeypatch.setattr(driver, "look",
                        lambda chain, phone: {"url": "https://lenta.com/",
                                              **driver.see(chain, GUEST[chain], [])})

    enter(web)
    answer = web.post("/cabinet/act?store=lenta", json={"do": "shot"})
    body = answer.get_json()

    assert body["ok"] is True
    assert body["logged_in"] is False
    assert body["says"] == signals.verdict_words(False)[0]
    assert body["shot"].startswith("data:image/jpeg;base64,")
    assert body["width"] == driver.WIDTH and body["height"] == driver.HEIGHT


def test_the_screen_hears_about_the_check_in_calm_words(web, monkeypatch):
    """Встретила проверка — экран получает объяснение и спокойный тон.

    «Произошла ошибка» здесь было бы враньём дважды: приложение не сломалось, и
    делать человеку надо не «попробовать позже», а пройти проверку прямо тут.
    """
    fake = driver.Shot(jpeg=b"\xff\xd8\xff\xe0 poddelka", width=driver.WIDTH,
                       height=driver.HEIGHT, url="https://dixy.ru/", title="Дикси", at=0.0)
    monkeypatch.setattr(driver, "shot", lambda chain, phone: fake)
    monkeypatch.setattr(driver, "look",
                        lambda chain, phone: {"url": "https://dixy.ru/",
                                              **driver.see(chain, GUARD[chain], [])})

    enter(web)
    body = web.post("/cabinet/act?store=dixy", json={"do": "shot"}).get_json()

    assert body["guarded"] is True
    assert body["logged_in"] is None
    # Дикси отдаёт нашему серверу «Не удалось загрузить сайт» — значит и слова те,
    # что про неё, а не обещание проверки, которой на странице нет.
    assert body["says"] == signals.BLOCKED_NOTE
    assert body["says_kind"] == "info"


def test_there_is_exactly_one_button_that_remembers_the_login(web):
    """Кнопка «Запомнить вход» на экране одна. Вторая сводила первую на нет.

    Их было две: одна пряталась до появления признаков входа, вторая стояла в
    карточке ниже всегда. Гость нажимал вторую, сеть отдавала свои гостевые куки
    (у Магнита это mg_udi и shopCode), проверка «есть ли хоть одна» их принимала —
    и кабинет помечался подключённым. На «Кабинетах» вставало ✓, наряд корзины
    уезжал в сеанс, где никто не входил, и человек узнавал об этом у полки.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert page.count("Запомнить вход</button>") == 1, \
        "кнопок «Запомнить вход» больше одной — сторож у первой ничего не стоит"
    assert page.count("js-save") == 2, "кнопка и её обработчик разошлись"
    # И прячется она от того, про кого страница говорит «не вошли», а не от того,
    # про кого мы просто не разобрали: у пяти сетей из шести подписи входа нет.
    assert "data.logged_in !== false" in page


def test_a_guest_cannot_mark_a_cabinet_connected(web, monkeypatch):
    """Гостю сохранение входа не удаётся — и он слышит, что делать дальше.

    Это самая дорогая ошибка экрана. Куки гостю выдают все шесть сетей, и
    сохранённые, они выглядят как подключённый кабинет: ✓ на «Кабинетах», наряд
    корзины уезжает в никуда, а узнаёт об этом человек у полки.
    """
    monkeypatch.setattr(driver, "look",
                        lambda chain, phone: driver.see(chain, GUEST[chain], []))
    monkeypatch.setattr(driver, "remember",
                        lambda chain, phone: {"cookies": [{"name": "mg_udi", "value": "гость"}]})

    def never(*a, **kw):
        raise AssertionError("кабинет гостя помечен подключённым")

    monkeypatch.setattr("app.store_accounts.mark_connected", never)

    enter(web)
    body = web.post("/cabinet/act?store=magnit", json={"do": "save"}).get_json()

    assert body["ok"] is False
    assert "не вошли" in body["error"], "не названа причина отказа"
    assert "ещё раз" in body["error"], "не сказан следующий шаг"


def test_an_unrecognised_page_still_lets_a_person_save_the_login(web, monkeypatch):
    """«Не разобрал» сохранять не мешает — иначе сохранять было бы негде.

    Подписи входа достоверно известны только у Магнита; у остальных пяти разбор
    честно отвечает «не знаю». Запрет на сохранение в этом случае закрыл бы дорогу
    почти во все сети — поэтому решает человек, а приложение говорит прямо, что
    проверить вход не смогло.
    """
    saved: dict = {}
    fake = driver.Shot(jpeg=b"\xff\xd8\xff\xe0 poddelka", width=driver.WIDTH,
                       height=driver.HEIGHT, url="https://vkusvill.ru/", title="ВкусВилл", at=0.0)
    monkeypatch.setattr(driver, "shot", lambda chain, phone: fake)
    monkeypatch.setattr(driver, "look", lambda chain, phone: driver.see(chain, "", []))
    monkeypatch.setattr(driver, "remember",
                        lambda chain, phone: {"cookies": [{"name": "sid", "value": "x"}]})
    monkeypatch.setattr("app.store_accounts.mark_connected",
                        lambda *a, **kw: saved.setdefault("connected", True))

    enter(web)
    body = web.post("/cabinet/act?store=vkusvill", json={"do": "save"}).get_json()

    assert body["ok"] is True and saved.get("connected")
    assert "1 кука" in body["note"], "число и слово разошлись — «1 куки» по-русски не говорят"
    assert "не смогло" in body["note"], "умолчали, что вход не проверен"


def test_a_blinking_browser_does_not_leave_the_verdict_empty(web, monkeypatch):
    """Разбор не состоялся — экран всё равно слышит «не разобрал», а не тишину.

    Пустая строка на месте вердикта читается как «всё в порядке», а мы про вход
    не знаем ровно ничего.
    """
    fake = driver.Shot(jpeg=b"\xff\xd8\xff\xe0 poddelka", width=driver.WIDTH,
                       height=driver.HEIGHT, url="https://magnit.ru/", title="Магнит", at=0.0)
    monkeypatch.setattr(driver, "shot", lambda chain, phone: fake)

    def blink(chain, phone):
        raise driver.BrowserUnavailable("браузер как раз моргнул")

    monkeypatch.setattr(driver, "look", blink)

    enter(web)
    body = web.post("/cabinet/act?store=magnit", json={"do": "shot"}).get_json()

    assert body["logged_in"] is None
    assert "Не разобрал" in body["says"]


# ---------- набор уходит в поле, а не в никуда ----------

def _fake_shot():
    return driver.Shot(jpeg=b"\xff\xd8\xff\xe0 poddelka", width=driver.WIDTH,
                       height=driver.HEIGHT, url="https://magnit.ru/",
                       title="Магнит", at=0.0)


def test_text_that_reached_a_field_is_named_and_the_line_is_cleared(web, monkeypatch):
    """Набралось — человеку говорят, в какое поле, и строку можно очистить."""
    monkeypatch.setattr(driver, "type_text", lambda chain, phone, text: (
        _fake_shot(), {"typed": True, "kind": "field", "label": "Телефон"}))

    enter(web)
    body = web.post("/cabinet/act?store=magnit",
                    json={"do": "type", "text": "9161234567"}).get_json()

    assert body["typed"] is True
    assert "Телефон" in body["note"]
    assert body["note_kind"] == "", "удачный набор не красят тревожным"


def test_text_with_nowhere_to_go_is_refused_out_loud(web, monkeypatch):
    """Набирать некуда — так и сказано, и набранное остаётся у человека.

    Это и есть поломка, ради которой сторож поставлен: экран отвечал обычным
    снимком, строка очищалась, поле оставалось пустым — и человек набирал код из
    СМС по второму разу, пока тот не протухал.
    """
    monkeypatch.setattr(driver, "type_text", lambda chain, phone, text: (
        _fake_shot(), {"typed": False, "kind": "none", "label": ""}))

    enter(web)
    body = web.post("/cabinet/act?store=magnit",
                    json={"do": "type", "text": "1234"}).get_json()

    assert body["typed"] is False, "экран очистит строку и человек потеряет набранное"
    assert "некуда" in body["note"]
    assert "нажмите в нужное поле" in body["note"].lower(), "не сказали следующий шаг"
    assert body["note_kind"] == "bad"


def test_typing_into_the_check_frame_says_where_it_went(web, monkeypatch):
    """Набрали в рамку проверки — говорим это прямо и называем запасной путь.

    ПОЧЕМУ НЕ ОТКАЗ. Здесь стоял отказ «Набирать некуда: выбрано окошко проверки
    „я не робот"», и 20.09.2026 он не дал владельцу войти: Магнит показал
    проверку с картинкой, у которой поле СВОЁ, внутри рамки. Он нажимал в это
    поле и читал, что набирать некуда.

    ПОЧЕМУ НЕ МОЛЧАНИЕ. У другого вида проверки, галочки, поле снаружи, и буквы
    действительно уходят в пустоту. Что именно в рамке, снаружи не видно —
    поэтому называются оба исхода, а догадка за знание не выдаётся.
    """
    monkeypatch.setattr(driver, "type_text", lambda chain, phone, text: (
        _fake_shot(), {"typed": True, "kind": "frame", "label": "SmartCaptcha"}))

    enter(web)
    body = web.post("/cabinet/act?store=magnit",
                    json={"do": "type", "text": "1234"}).get_json()

    assert body["typed"] is True
    assert "проверки" in body["note"]
    assert "некуда" not in body["note"], "снова отказ там, где набор нужен"
    assert "нажмите прямо в него" in body["note"], "не назван запасной путь"


def test_typing_goes_straight_into_the_window(web):
    """Отдельного поля с кнопкой «Набрать» на экране больше нет.

    Владелец сказал прямо: «такой вариант с полем отдельным очень неудобный».
    Набрать в одном месте, нажать кнопку, чтобы уехало в другое, и посмотреть,
    доехало ли, — три действия там, где в обычном окне ноль. Теперь окно
    принимает набор само.

    Невидимое поле при этом остаётся и убрать его нельзя: клавиатуру на телефоне
    поднимает только фокус в настоящем поле, картинка его принять не может.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert 'id="keeper"' in page, "нечему поднять клавиатуру телефона"
    assert 'pointer-events:none' in page, "невидимое поле перехватит нажатия по снимку"
    assert '>Набрать<' not in page, "кнопка «Набрать» вернулась — набор снова в два приёма"
    assert 'placeholder="Телефон, код из СМС' not in page, "отдельное поле вернулось"


def test_the_screen_says_whether_the_window_takes_typing(web):
    """Человеку сказано, принимает ли окно набор прямо сейчас.

    Без этой строки он печатал бы в пустоту, не понимая, почему буквы не
    появляются, — и решил бы, что сломано приложение.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert "Нажмите по снимку" in page
    assert "печатайте прямо в страницу магазина" in page
    assert "Окно принимает набор" in page, "нет слов для того мига, когда набор уже идёт"


def test_typed_letters_wait_instead_of_getting_lost(web):
    """Набирать было некуда — знаки ждут, а не пропадают.

    Это тот же сторож, что и на отказе сервера, но со стороны экрана: код из СМС
    живёт минуту, и набрать его заново вслепую человек не должен.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert "pending = text + pending" in page, "набранное не возвращается в запас"
    assert "Набранное ждёт" in page, "человеку не сказано, что знаки сохранены"


def test_a_typed_letter_does_not_cost_a_whole_page_read(web, monkeypatch):
    """Ответ на набор — короткий, и разбор страницы на каждую букву не делается.

    Полный ответ заново читает весь текст страницы и куки (driver.look) — второй
    поход в браузер на каждый знак. Вместе с ожиданием «пока страница
    нарисуется» набор шёл больше секунды под серой пеленой, и владелец сказал
    ровно то, что чувствовал: «вводить напрямую не получается, происходит какая-то
    загрузка постоянная».
    """
    looked = []
    monkeypatch.setattr(driver, "look",
                        lambda chain, phone: looked.append(chain) or {})
    monkeypatch.setattr(driver, "type_text", lambda chain, phone, text: (
        _fake_shot(), {"typed": True, "kind": "field", "label": "Телефон"}))

    enter(web)
    body = web.post("/cabinet/act?store=magnit",
                    json={"do": "type", "text": "9"}).get_json()

    assert looked == [], "страница разобрана заново ради одной буквы"
    assert body["partial"] is True, "экран сотрёт вердикт про вход на каждой букве"
    assert "shot" in body, "кадр всё-таки нужен: человек должен видеть свою букву"


def test_typing_does_not_raise_the_working_veil(web):
    """Пелена «работаю…» на набор не поднимается.

    Она закрывает окно серым, и для буквы, которая уходит и возвращается за доли
    секунды, это и есть та самая «постоянная загрузка».
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert 'act({do: "type", text: text}, true)' in page, "набор идёт с пеленой"
    assert "if (!quiet) veil.classList.add" in page, "пелена поднимается всегда"


def test_letters_are_not_lost_when_the_window_is_busy(web):
    """Окно было занято — знаки возвращаются в запас, а не пропадают.

    Занято оно бывает ровно тогда, когда человек печатает быстро, то есть всегда,
    когда он вводит код из СМС.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert "if (data === null)" in page, "занятое окно молча съедает набранное"


# ---------- вход, переданный с телефона ----------

def _handed(store="magnit", host="magnit.ru", cookies=None) -> str:
    import json

    return json.dumps({"store": store, "host": host,
                       "cookies": cookies or [{"name": "mg_at", "value": "к" * 60}]},
                      ensure_ascii=False)


def test_a_login_handed_over_from_the_phone_is_saved(web):
    """Вход с телефона сохраняется и подключает кабинет — без нашего браузера.

    ЗАЧЕМ ЭТА ДОРОГА ВООБЩЕ ЕСТЬ. Окно магазина входит НАШИМ сервером, и
    20.09.2026 это перестало работать у Магнита: сначала проверка Yandex
    SmartCaptcha, потом «Аккаунт заблокирован. Продолжите без него или войдите
    по-другому». Тот же аккаунт с телефона владельца открывается как обычно —
    разница в адресе, с которого стучатся. Значит вход надо переносить, а не
    добывать, и переносить его нечем, кроме как руками человека.
    """
    from app.shopbrowser import store as shopstore

    enter(web)
    body = web.post("/cabinet/act?store=magnit",
                    json={"do": "paste", "text": _handed()}).get_json()

    assert body["ok"] is True
    assert body["saved"] is True
    assert "с телефона" in body["note"]
    # Рабочее место открываем сами: запрос кончился, и вместе с ним закрылась
    # база — прочитать её «просто так» значит прочитать пустоту и не заметить.
    users.activate(PHONE)
    try:
        assert shopstore.load("magnit") is not None, "вход не доехал до рабочего места"
    finally:
        users.deactivate()


def test_a_guest_jar_from_the_phone_is_refused_just_like_a_guest_window(web):
    """Гостевую банку кук не принимаем и с телефона тоже.

    У Магнита имя куки входа снято живьём (mg_at), значит «вошёл» здесь — факт,
    а не догадка. Принять банку без неё значило бы отметить подключённым
    кабинет, в который никто не входил: на «Кабинетах» встало бы ✓, а наряд
    корзины уехал бы в сеанс гостя и молча ничего не положил. Ровно эта ошибка
    уже ловилась у кнопки «Запомнить вход».
    """
    from app.shopbrowser import store as shopstore

    enter(web)
    body = web.post("/cabinet/act?store=magnit", json={
        "do": "paste", "text": _handed(cookies=[{"name": "mg_udi", "value": "гость"}]),
    }).get_json()

    assert body["ok"] is False
    assert "ещё не вошли" in body["error"]
    users.activate(PHONE)
    try:
        assert shopstore.load("magnit") is None, "гостевые куки всё-таки сохранились"
    finally:
        users.deactivate()


def test_a_login_pasted_into_the_wrong_chain_is_named_not_swallowed(web):
    """Вход Магнита, вставленный в кабинет ВкусВилла, отвергается по имени."""
    enter(web)
    body = web.post("/cabinet/act?store=vkusvill",
                    json={"do": "paste", "text": _handed()}).get_json()

    assert body["ok"] is False
    assert "другую сеть" in body["error"]


def test_the_screen_offers_the_way_that_works_when_the_server_cannot_get_in(web):
    """Поле для переданного входа есть на экране, и сказано, зачем оно.

    Сторож разметочный нарочно: условие легко потерять при первой же
    перестановке блоков, и потеря будет молчаливой — человек, которому сеть не
    даёт войти с сервера, останется без единственной оставшейся дороги.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert 'class="btn main wide js-paste"' in page
    assert "Передать вход" in page, "не названа закладка, которой это делается"


def test_the_screen_hands_over_the_bookmarklet_itself(web):
    """Закладку человеку дают прямо здесь, а не отсылают её искать.

    Без этого вся дорога «вход с телефона» остаётся описанием: поле для вставки
    есть, а взять вставляемое негде. Молчаливое отсутствие хуже любой ошибки —
    оно выглядит как работающая возможность.
    """
    enter(web)
    page = web.get("/cabinet?store=magnit").data.decode("utf-8")

    assert "javascript:" in page, "строки закладки на экране нет"
    assert "перетащите на панель закладок" in page.lower()
    assert "На телефоне перетащить некуда" in page, "телефон остался без своего пути"
