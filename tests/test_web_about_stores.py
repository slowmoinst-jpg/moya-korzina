"""Экраны честности — «О продукте» и «Магазины» — и отставшие слова в них.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Эти два экрана ломаются не так, как остальные. Корзина,
сломавшись, перестаёт считать, и это видно сразу. Экран честности, сломавшись,
продолжает бодро отвечать 200 и рассказывать про позавчерашний замысел — и
заметить это может только человек, который знает, как всё устроено сегодня.
Отставшее слово здесь не опечатка: оно посылает человека делать то, что уже
ничего не даст, или обещает за приложение то, чего оно не умеет.

ЧТО ЗДЕСЬ ГЛАВНОЕ — ЗАПРЕТ НА ТРИ СЛОВА. Дорога через постороннее дополнение к
браузеру человека закрыта решением владельца 17.09.2026: «только работа в нашем
приложении». Разбор — docs/design/extension.md, замена — docs/design/shopbrowser.md.
Слова этой дороги успели разойтись по экранам и по страницам сайта и вычищались
оттуда трижды, каждый раз не до конца. Поэтому запрет переехал из внимательности
в проверку.

ПОЧЕМУ СМОТРИМ ОТВЕТ СТРАНИЦЫ, А НЕ ИСХОДНИК ШАБЛОНА. В шаблонах и модулях эти
слова остались — в надгробных комментариях, объясняющих, почему блока больше нет
и не надо заводить его заново. Такой комментарий полезен: он останавливает
следующего. Читает его разработчик, а не покупатель. Запрет же — про то, что
видит ЧЕЛОВЕК, и проверять его надо на том, что уходит в браузер.

ЧЕГО ЗДЕСЬ НЕТ. Проверки, что экран отвечает 200: он отвечал бы 200 и пустым.
Сторожа стоят на обещаниях, каждое из которых ломается тихо.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import users  # noqa: E402

PHONE = "79990000041"

# Слова закрытой дороги. Корни, а не целые слова: «расширение», «расширения»,
# «расширению» — одна и та же неправда в трёх падежах, и ловить надо все.
GONE_WORDS = ("расширени", "сборщик", "extension")

# Прежний интерфейс удалён целиком вместе со своей библиотекой. Его имя на экране
# означало бы, что часть приложения живёт где-то ещё, — а её нет.
GONE_UI = "streamlit"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def enter(client):
    return client.post("/login", data={"phone": PHONE, "next": "/"})


def seen_by_a_person(client, path: str) -> str:
    """То, что уходит в браузер. Ровно оно и есть «экран»."""
    answer = client.get(path)
    assert answer.status_code == 200, f"{path} ответил {answer.status_code}"
    return answer.data.decode("utf-8")


def without_comments(html: str) -> str:
    """Страница без комментариев разработчика: их человек не читает.

    Нужно для страниц сайта в docs/: там надгробия закрытой дороги оставлены
    нарочно, комментарием, — чтобы следующий не завёл ту же кнопку заново.
    """
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


# ---------- главное: отставших слов на экранах нет ----------

def test_the_screens_do_not_send_a_person_after_a_road_that_is_closed(web):
    """Ни «О продукте», ни «Магазины» не зовут ставить постороннее дополнение.

    Дороги нет с 17.09.2026, и упоминание её на экране — не устаревшая мелочь, а
    указание сделать бесполезное. Хуже всего это смотрится именно здесь: экран,
    который врёт о себе, обесценивает и всё остальное, что на нём написано.
    """
    enter(web)
    for path in ("/about", "/stores"):
        page = seen_by_a_person(web, path).lower()
        for word in GONE_WORDS:
            assert word not in page, f"{path}: на экране осталось слово «{word}»"


def test_the_screens_do_not_name_the_interface_that_was_deleted(web):
    """Прежний интерфейс удалён целиком — называть его значит обещать вторую дверь."""
    enter(web)
    for path in ("/about", "/stores"):
        assert GONE_UI not in seen_by_a_person(web, path).lower(), (
            f"{path}: назван интерфейс, которого больше нет")


def test_the_public_pages_do_not_name_that_road_either():
    """Страницы сайта — такой же экран честности, просто читают их снаружи.

    Они дольше всех оставались неисправленными: в образ не едут, глазами их никто
    не открывает. Именно поэтому сторож нужен им больше, чем экранам.
    """
    # ВСЕ страницы, а не две. Отставшее слово ловили тут уже трижды, и каждый раз
    # глазами — потому что сторож стоял только на тех двух, которые правили сейчас.
    # Перечислять их поимённо тоже нельзя: следующая страница появится без сторожа.
    import glob

    pages = sorted(glob.glob(os.path.join(ROOT, "docs", "*.html")))
    assert len(pages) >= 6, "страницы сайта потерялись — сторож смотрит не туда"
    for path in pages:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as handle:
            visible = without_comments(handle.read()).lower()
        for word in GONE_WORDS:
            # Ссылка на разбор отмены — не обещание дороги, а адрес файла, где
            # написано, почему её нет. Её вычищать нечего.
            leftovers = [line for line in visible.splitlines()
                         if word in line and "design/extension.md" not in line]
            assert not leftovers, f"docs/{name}: осталось слово «{word}» — {leftovers[:1]}"


def test_the_road_that_replaced_it_is_written_down():
    """Устройство окна магазина записано, и записано по коду, а не по памяти.

    Документ проверяется на то, ради чего он заводился: границу дозволенного и
    два решения про потоки. Без них следующий разработчик повторит обе ловушки —
    и обе тихо.
    """
    path = os.path.join(ROOT, "docs", "design", "shopbrowser.md")
    assert os.path.exists(path), "docs/design/shopbrowser.md не написан"
    with open(path, encoding="utf-8") as handle:
        text = handle.read().lower()

    assert "contextvars" in text, "не сказано, почему фоновый поток сам открывает рабочее место"
    assert "playwright" in text, "не сказано, почему поток браузера один"
    assert "капч" in text, "не проведена граница: капчу проходит человек"


# ---------- «Магазины»: что сказано про каждую из шести сетей ----------

def test_every_chain_says_where_its_prices_come_from(web):
    """Шесть сетей, у каждой названо и имя, и способ. Сеть без способа — дыра.

    Умолчание здесь читается как «работает, просто не расписали»: человек видит
    сеть в списке и считает, что цены по ней живые. Поэтому «Источник цен не
    описан» — это провал проверки, а не мягкое место.
    """
    from app import repo
    from app.web.screens.stores import HOW

    enter(web)
    page = seen_by_a_person(web, "/stores")
    users.open_workspace(PHONE)
    try:
        chains = repo.list_stores()
    finally:
        users.deactivate()

    # 19.09.2026 сетей стало семь (METRO), 21.09.2026 — восемь (Перекрёсток). Число
    # тут нарочно записано руками: новая сеть не должна проскочить на экран без
    # описания источника цен, а «поправить цифру» — это и есть перечитать проверку.
    assert len(chains) == 8, f"сетей стало {len(chains)} — проверку надо перечитать, а не править"
    for store in chains:
        assert store.name in page, f"{store.code}: сети нет на экране"
        assert store.code in HOW, f"{store.code}: не сказано, откуда у неё цены"
    assert "Источник цен не описан" not in page


def test_the_two_closed_chains_do_not_promise_live_prices(web):
    """Пятёрочка и Самокат: цену видит кабинет человека, а не наш сервер.

    Прежде экран обещал за них живые цены, потом — молчал и сваливал их в «прайс
    и чеки». Оба ответа неверны сегодня: дорога есть, но она идёт через вход
    человека, и без входа её нет. Экран обязан сказать оба конца этой правды.
    """
    from app.web.screens.stores import BADGE, HOW, STATE_CABINET

    for code in ("pyaterochka", "samokat"):
        state, what = HOW[code]
        assert state == STATE_CABINET, f"{code}: у него своя дорога, а не общая"
        assert "кабинет" in what.lower(), f"{code}: не сказано, чей кабинет читает цену"
        assert "прайс и чеки" in what.lower(), f"{code}: не сказано, что будет без входа"
    assert BADGE[STATE_CABINET][1] != BADGE["живые"][1], "метка не отличается от живых цен"

    enter(web)
    page = seen_by_a_person(web, "/stores")
    assert "Цены живые" in page, "у открытых сетей метка живых цен пропала"
    assert BADGE[STATE_CABINET][1] in page


def test_a_chain_read_through_a_cabinet_says_whether_that_cabinet_is_open(web):
    """Без сохранённого входа — «цены из прайса и чеков», с ним — «можно обновить».

    Карточка, зовущая нажать «Обновить цены» там, где вход не сохранён, кончается
    отказом. Обещание, которое отказом и кончается, на экране честности хуже
    молчания: оно подрывает всё остальное, что там написано.
    """
    enter(web)
    assert "Входа в эту сеть нет" in seen_by_a_person(web, "/stores")

    users.open_workspace(PHONE)
    try:
        from app.shopbrowser import store as shopstore
        shopstore.save("pyaterochka", {"cookies": [], "origins": []}, account="Карта •••1111")
    finally:
        users.deactivate()

    page = seen_by_a_person(web, "/stores")
    assert "Вход в эту сеть сохранён" in page


def test_the_cabinet_metka_matches_what_the_code_can_actually_do(web):
    """Метка «Цены из вашего кабинета» стоит ровно у тех сетей, которые так и умеют.

    Метка живёт в экране, а умение — в коде, и разойтись они могут молча: сеть
    добавят в сборщик цен и забудут про экран, или наоборот. Второе хуже: экран
    зовёт нажать «Обновить цены» там, где собирать нечем.
    """
    from app.shopbrowser import collect
    from app.web.screens.stores import HOW, STATE_CABINET

    marked = {code for code, (state, _what) in HOW.items() if state == STATE_CABINET}
    assert marked == set(collect.CHAINS), (
        f"экран обещает кабинетные цены у {marked}, а собирает их у {set(collect.CHAINS)}")


def test_stores_does_not_pass_the_networks_offer_off_as_our_own(web):
    """Список возможностей сети — не список того, что делает приложение.

    Подписью к кнопке «Войти» стояло «персональные цены, личные купоны, баллы и
    кэшбэк, наполнение корзины, история покупок». Всё это правда про сеть и
    наполовину неправда про нас: купоны и историю покупок приложение под этим
    входом не забирает. Экран обязан говорить от своего имени.
    """
    enter(web)
    page = seen_by_a_person(web, "/stores")
    assert "история покупок" not in page, "обещано то, чего приложение не делает"
    assert "Доступно:" not in page
    assert "не забирает" in page, "не сказано, чего приложение под входом не делает"


def test_stores_says_how_much_of_this_was_actually_walked_through(web):
    """Живым заходом пройден один Магнит — и экран не делает вид, что все шесть.

    Пять сетей встречают наш сервер своей проверкой, и пройдёт ли её человек в
    этом окне, никто пока не знает. Промолчать — значит пообещать; пообещать —
    значит объяснять потом, почему «не работает приложение», хотя приложение тут
    ни при чём.
    """
    enter(web)
    page = seen_by_a_person(web, "/stores")
    assert "Магнит" in page
    assert "не знаем" in page or "неизвестно" in page, "экран не признаёт непроверенного"


# ---------- «О продукте»: обещания, которых приложение не выполняет ----------

def test_about_does_not_repeat_what_the_app_outgrew(web):
    """Устаревшее самоуничижение так же вредно, как устаревшее хвастовство.

    В начале сентября ВкусВилл «не отвечал», Лента и Самокат были «в планах», а
    выгрузка из кабинета ФНС — «в планах». Всё это давно не так, и человек,
    прочитавший это сегодня, не станет пользоваться работающим.
    """
    enter(web)
    page = seen_by_a_person(web, "/about")
    assert "не отвечает" not in page, "ВкусВилл давно отвечает"
    assert "Лента и Самокат — в планах" not in page
    assert "выгрузка из кабинета ФНС — в планах" not in page.lower()


def test_about_names_the_window_and_its_limits(web):
    """Окно магазина названо вместе с тем, что по нему ещё не проверено.

    Название без предела — это реклама. Предел здесь измеренный: живым заходом
    пройден один Магнит, про остальные пять сетей никто пока не знает.
    """
    enter(web)
    page = seen_by_a_person(web, "/about")
    assert "Окно магазина внутри приложения" in page
    assert "не проверено" in page or "неизвестно" in page


def test_about_says_where_the_keys_to_the_cabinets_lie(web):
    """Ключи от кабинетов лежат у нас — и это сказано прямо, а не подразумевается.

    Это единственное место, где человек может узнать цену удобства: чеки приходят
    сами и корзина уезжает в магазин, потому что доступ к серверу равен доступу к
    его кабинетам. Умолчание здесь было бы обманом, а не скромностью.
    """
    enter(web)
    page = seen_by_a_person(web, "/about")
    assert "Ключи от кабинетов" in page
    assert "доступу к вашим кабинетам" in page


def test_about_does_not_promise_the_login_confirmation_by_a_date(web):
    """Вход без подтверждения назван, но срок ему не назначен.

    Прежний текст обещал код из СМС «вместе с кабинетом ФНС». Кабинет приехал,
    подтверждение входа — нет, и обещание тихо превратилось в неправду. Такие
    обещания живут на экране дольше события, к которому привязаны.
    """
    enter(web)
    page = seen_by_a_person(web, "/about")
    assert "без подтверждения" in page
    assert "кто знает номер" in page
    assert "вместе с кабинетом ФНС" not in page


def test_about_counts_shops_in_russian(web):
    """«5 магазинов», а не «5 магазина». Глубина — настройка, и она бывает любой.

    Текст со склеенным «{n} магазина» был верен ровно при двойке. Настройку
    подняли до пяти — и первое же слово на экране честности стало неграмотным;
    дальше его читают уже с недоверием, и это дороже, чем кажется.
    """
    from app.web.screens.about import _shops

    assert _shops(1) == "1 магазин"
    assert _shops(2) == "2 магазина"
    assert _shops(5) == "5 магазинов"
    assert _shops(11) == "11 магазинов", "одиннадцать — исключение из правила"

    enter(web)
    page = seen_by_a_person(web, "/about")
    assert not re.search(r"\b[05-9] магазина\b", page), "число и слово разошлись"


def test_the_login_button_is_written_in_russian(web):
    """«Войти в Пятёрочка» — то, что получается при склейке из справочника.

    Мелочь, но ровно того сорта, по которой судят обо всём остальном: если
    приложение не умеет назвать магазин, почему ему верить в подсчёте денег.
    """
    enter(web)
    page = seen_by_a_person(web, "/stores")
    assert "Войти в Пятёрочка" not in page
    assert "Войти в Лента" not in page


def test_about_and_stores_do_not_keep_two_copies_of_the_same_list(web):
    """Один список сетей на два экрана. Две копии разошлись бы через месяц."""
    from app.web.screens.stores import HOW

    enter(web)
    about = seen_by_a_person(web, "/about")
    for _state, what in HOW.values():
        assert what[:40] in about, what[:40]


def test_the_screens_do_not_promise_a_stock_that_nobody_reports(web):
    """Наличие числом обещано ровно тем сетям, которые его отдают.

    Экран честности писал, что ВкусВилл сообщает наличие числом да ещё и по вашей
    точке. Неправда трижды: остатков он не отдаёт вовсе (app/connectors/vkusvill.py
    ставит in_stock=True с оговоркой «не выдумываем»), отвечает одинаково по всей
    стране (app/places.BY_POINT) — и открытая страница проекта в это же время
    писала обратное. Человек верит пометке «в наличии», которой приложение никогда
    не проверяло, и получает отказ уже в магазине.
    """
    from app import places
    from app.connectors import get_connector

    enter(web)
    # 19.09.2026 к ним добавилась METRO: цена у неё своя по городу, остаток — свой
    # в каждом торговом центре, значит «ближайшая точка» для неё значит ровно то же,
    # что для Магнита и Ленты.
    assert set(places.BY_POINT) == {"magnit", "lenta", "metro", "pyaterochka", "samokat"}, \
        "список сетей «по вашей точке» изменился — перечитайте оба экрана"

    for path in ("/about", "/stores"):
        page = seen_by_a_person(web, path)
        assert "ВкусВилл отдают цену и наличие" not in page
        assert not re.search(r"ВкусВилл[^.]{0,80}наличие числом", page), \
            f"{path}: ВкусВиллу приписано наличие, которого он не сообщает"

    # И ни одна сеть не объявлена единственной, кто отдаёт остаток: их две.
    for path in ("/about", "/stores"):
        assert "единственная" not in seen_by_a_person(web, path).lower(), \
            f"{path}: одна из двух сетей с остатком названа единственной"

    # Магнит остаток отдаёт — значит и на открытой странице он не должен читаться
    # как сеть, чью витрину приложение разбирает из вёрстки.
    assert hasattr(get_connector("magnit"), "get_prices")
    with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as handle:
        public = without_comments(handle.read())
    assert "Единственная из шести" not in public
    assert "Страница сайта приходит уже собранной" not in public, \
        "открытая страница описывает дорогу, с которой приложение сошло 16.09.2026"


def test_both_screens_survive_an_empty_workspace(web):
    """Настоящее рабочее место начинается пустым — и экраны обязаны это пережить."""
    enter(web)
    for path in ("/about", "/stores"):
        assert web.get(path).status_code == 200
