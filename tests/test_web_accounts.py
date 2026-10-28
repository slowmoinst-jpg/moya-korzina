"""Экран «Кабинеты»: вход в аккаунты сетей и передача корзины туда.

ЧТО ЗДЕСЬ ВАЖНО ПРОВЕРИТЬ, А ЧТО НЕТ. Проверять, что страница отвечает 200, почти
бесполезно: она отвечала бы 200 и пустой. Поэтому сторожа стоят на обещаниях
экрана, каждое из которых ломается тихо.

    ЧЕСТНОСТЬ ПРО ТО, КТО ЧТО ДЕЛАЕТ. С 17.09.2026 вход происходит в самом
    приложении, в окне с настоящей страницей сети. Экран обязан сказать, что
    проверку «я не робот» и код из СМС проходит человек: умолчание дало бы повод
    думать, что приложение умеет их обходить.

    ПЕРЕДАЧА ДЕЙСТВИТЕЛЬНО НАЧИНАЕТСЯ. Кнопка «Передать корзину» пускает браузер
    на сервере под сохранённым входом. Если передача не началась, экран покажет
    «пошла», а в магазине не появится ничего — ложь, заметная только у кассы.

    «ЗАБЫТЬ» ЗАБИРАЕТ И КЛЮЧ. Оставить куки сети, погасив подключение, значило бы
    держать доступ к кабинету, о котором приложение говорит «не подключён».

    НЕТ ОТСТАВШИХ ИНСТРУКЦИЙ. Расширения в этой дороге больше нет, и звать
    ставить его — значит посылать человека делать то, что уже ничего не даст.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartplan, repo, store_accounts, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000031"


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
    from conftest import sign_in

    return sign_in(client, PHONE)


def seed_basket() -> int:
    """Корзина из одной позиции с подтверждённым артикулом Магнита.

    Магнит взят нарочно: корзину он принимает нарядом через расширение, а не
    ссылкой, — то есть проходит весь путь, который здесь и проверяется.
    """
    users.open_workspace(PHONE)
    product_id = repo.upsert_product(Product(id=None, name="Молоко 3.2% 900 мл", unit="pcs"))
    store = repo.get_store("magnit")
    sp_id = repo.upsert_store_product(store.id, "100001", "Молоко Простоквашино 900 мл")
    repo.confirm_mapping(product_id, sp_id, confirmed=True)
    repo.save_price(sp_id, 89.9)
    basket_id = repo.create_basket("Неделя 38")
    repo.set_basket_item(basket_id, product_id, 2)
    return basket_id


# ---------- честность про вход ----------

def test_the_screen_says_who_does_what(web):
    """Экран проговаривает три вещи, и каждая из них — обещание, а не украшение.

    ГДЕ ПРОИСХОДИТ ВХОД. С 17.09.2026 — в самом приложении (решение владельца:
    «только работа в нашем приложении»). Человек вправе знать, что чужая страница
    открыта нашим сервером, а не его браузером.

    ЧТО ДЕЛАЕТ ОН. Проверку «я не робот» и код из СМС вводит он. Умолчать об
    этом значило бы оставить его ждать у чужой страницы.

    ЧТО ОСТАЁТСЯ У НАС. Только сам вход, и только по его просьбе. Ни пароля, ни
    кода — и ни одного поля для них на нашей странице.
    """
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")
    assert "прямо здесь" in page, "не сказано, что вход происходит в приложении"
    assert "я не робот" in page and "код из СМС" in page,         "не сказано, что проверку и код проходит человек"
    # Ни одного поля для чужого секрета: их здесь быть не должно вовсе.
    assert 'type="password"' not in page


def test_the_screen_does_not_send_anyone_to_install_an_extension(web):
    """Расширения в этой дороге больше нет, и обещать его нельзя.

    Сторож грубый нарочно: отставшая инструкция «поставьте расширение» посылает
    человека делать то, что уже ничего не даст, — и он решит, что приложение
    сломано, когда ничего не произойдёт.

    С 18.10.2026 рядом с этим стоит раздел с закладкой «Забрать цены», и в нём
    секрет рабочего места показан НАРОЧНО (владелец искал его в переписке). Поэтому
    здесь проверяются именно следы расширения, а не слово «секрет».
    """
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")
    assert "Загрузить распакованное" not in page
    assert "chrome://extensions" not in page
    assert "manifest.json" not in page
    assert "расширения ставить не нужно" in page


def test_every_chain_with_a_cabinet_is_on_the_screen(web):
    """Все шесть сетей на экране: спрятать сеть значит соврать про её кабинет."""
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")
    users.open_workspace(PHONE)   # базу снимает teardown после запроса, справочник читаем сами
    for chain in store_accounts.ABILITIES:
        store = repo.get_store(chain)
        assert store is not None and store.name in page, f"{chain} на экране нет"


def test_a_connected_chain_shows_its_account(web):
    """Подключённая сеть показывает подпись аккаунта и баллы — иначе непонятно, чей вход."""
    enter(web)
    users.open_workspace(PHONE)
    store_accounts.mark_connected("magnit", account="Карта •••4321", points=340.0,
                                  gives=[store_accounts.PRICES, store_accounts.CART])
    page = web.get("/accounts").data.decode("utf-8")
    assert "Карта •••4321" in page
    assert "340" in page
    assert "вы вошли" in page


# ---------- передача корзины ----------

def test_sending_without_a_saved_login_leads_to_the_cabinet(web):
    """Класть некуда, пока человек не вошёл, — и экран ведёт его туда, где входят.

    Отказ со словами «не получилось» был бы диагнозом без лечения: корзина у
    невошедшего своя на каждое устройство и живёт до закрытия вкладки, так что
    нужен не текст, а следующий шаг.
    """
    enter(web)
    seed_basket()

    answer = web.post("/accounts", data={"do": "send:magnit"})
    assert answer.status_code == 302
    assert "/cabinet?store=magnit" in answer.headers["Location"]


def test_sending_starts_a_real_delivery(web, monkeypatch):
    """С сохранённым входом нажатие пускает передачу — фоном и с теми позициями.

    Сторож смотрит НЕ на надпись об успехе, а на то, что передача действительно
    началась и получила наряд из корзины человека: надпись нарисовалась бы и без
    этого, и человек ушёл бы ждать корзину, которой никто не наполняет.

    Сам браузер здесь подменён: поднимать Chromium в наборе тестов значило бы
    ходить в сети магазинов на каждой сборке.
    """
    import threading

    from app.shopbrowser import cart, store as shopstore

    started = threading.Event()
    seen = {}

    def fake_deliver(chain, phone, plan):
        seen["chain"] = chain
        seen["skus"] = [line.sku for line in plan.lines]
        started.set()
        return {"ok": [line.sku for line in plan.lines], "failed": [], "note": ""}

    monkeypatch.setattr(cart, "deliver", fake_deliver)

    enter(web)
    seed_basket()
    users.open_workspace(PHONE)
    shopstore.save("magnit", {"cookies": [{"name": "mg_at", "value": "ключ"}], "origins": []})

    answer = web.post("/accounts", data={"do": "send:magnit"})
    assert answer.status_code == 302
    assert "sent=magnit" in answer.headers["Location"]

    assert started.wait(timeout=5), "передача не началась — человек ждал бы напрасно"
    assert seen["chain"] == "magnit"
    assert seen["skus"] == ["100001"]


def test_sending_an_empty_basket_says_so(web):
    """Корзина без связанных артикулов — не молчаливая пустота, а объяснение."""
    enter(web)
    answer = web.post("/accounts", data={"do": "send:magnit"})
    assert answer.status_code == 302
    assert "trouble=magnit" in answer.headers["Location"]

    users.open_workspace(PHONE)
    assert cartplan.get_pending("magnit") is None
    page = web.get("/accounts?trouble=magnit").data.decode("utf-8")
    assert "не вышло" in page


def test_an_unknown_chain_is_not_obeyed(web):
    """Код сети приходит формой, а форма приходит откуда угодно."""
    enter(web)
    answer = web.post("/accounts", data={"do": "send:azbuka"})
    assert answer.status_code == 302
    assert "trouble" not in answer.headers["Location"]


# ---------- «забыть» ----------

def test_forgetting_a_chain_does_not_pretend_to_revoke_access(web):
    """«Забыть» гасит подключение у нас — и экран говорит, где отзывают доступ."""
    enter(web)
    users.open_workspace(PHONE)
    store_accounts.mark_connected("magnit", account="Иван")
    page = web.get("/accounts").data.decode("utf-8")
    assert "выйдите из аккаунта" in page.lower(), \
        "экран не сказал, что доступ отзывают в магазине, — «забыть» прочтётся как «отключить»"

    answer = web.post("/accounts", data={"do": "forget:magnit"})
    assert answer.status_code == 302
    users.open_workspace(PHONE)
    assert store_accounts.connection("magnit").connected is False


def test_forgetting_a_chain_takes_the_saved_login_with_it(web):
    """Забыли сеть — забрали и сохранённый вход, и отложенный наряд.

    Оставить куки сети, погасив подключение, значило бы держать ключ от кабинета,
    о котором приложение уже говорит «не подключён»: человек считает, что доступа
    у нас нет, а он есть.
    """
    from app.shopbrowser import store as shopstore

    enter(web)
    seed_basket()
    users.open_workspace(PHONE)
    store_accounts.mark_connected("magnit")
    shopstore.save("magnit", {"cookies": [{"name": "mg_at", "value": "ключ"}], "origins": []})
    cartplan.save_pending("magnit", cartplan.build("magnit", [], force=True))

    web.post("/accounts", data={"do": "forget:magnit"})

    users.open_workspace(PHONE)
    assert shopstore.load("magnit") is None, "куки сети остались у нас после «забыть»"
    assert cartplan.get_pending("magnit") is None


def test_the_screen_shows_how_the_last_handover_ended(web):
    """Экран показывает, чем кончилась прошлая передача, — вместе с причинами.

    Раньше отчёт расширения уходил в журнал сервера, которого человек не читает:
    он нажимал «Передать корзину» и не узнавал в приложении ровно ничего — ни
    сколько легло, ни почему не легло остальное.
    """
    from app import collector

    enter(web)
    users.open_workspace(PHONE)
    store_accounts.mark_connected("magnit")
    collector.accept({
        "store": "magnit",
        "collected_at": "2026-09-17T18:42:00",
        "cart_result": {"ok": ["100001", "100002"],
                        "failed": [{"sku": "100009", "why": "нет в наличии"}]},
    })

    page = web.get("/accounts").data.decode("utf-8")
    assert "легло 2" in page
    assert "не легло 1" in page
    assert "нет в наличии" in page, "причина неудачи до человека не доехала"


def test_collecting_prices_needs_a_login_and_leads_to_the_cabinet(web):
    """Без входа сеть покажет полочные цены вместо личных — и это соврало бы в расчёте.

    Поэтому кнопка не делает вид, что сработала, а ведёт туда, где входят.
    """
    enter(web)
    answer = web.post("/accounts", data={"do": "prices:pyaterochka"})
    assert answer.status_code == 302
    assert "/cabinet?store=pyaterochka" in answer.headers["Location"]


def test_collecting_prices_starts_with_a_saved_login(web, monkeypatch):
    """С сохранённым входом нажатие пускает сбор — фоном и в той сети, что просили."""
    import threading

    from app.shopbrowser import collect, store as shopstore

    started = threading.Event()
    seen = {}

    def fake_refresh(chain, phone, **kw):
        seen["chain"] = chain
        started.set()
        return {"saved": 0, "read": 0, "note": ""}

    monkeypatch.setattr(collect, "refresh", fake_refresh)

    enter(web)
    users.open_workspace(PHONE)
    shopstore.save("pyaterochka", {"cookies": [{"name": "x", "value": "1"}], "origins": []})

    answer = web.post("/accounts", data={"do": "prices:pyaterochka"})
    assert answer.status_code == 302
    assert "prices=pyaterochka" in answer.headers["Location"]
    assert started.wait(timeout=5), "сбор не начался — человек ждал бы напрасно"
    assert seen["chain"] == "pyaterochka"


def test_only_two_chains_offer_a_price_refresh(web):
    """Кнопка сбора — только у сетей, чьи цены сервер сам не видит.

    У остальных приложение спрашивает цены у сети напрямую, и вторая дорога рядом
    означала бы два источника разной свежести в одной базе.
    """
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")
    assert 'value="prices:pyaterochka"' in page
    assert 'value="prices:samokat"' in page
    for chain in ("magnit", "lenta", "vkusvill", "dixy"):
        assert f'value="prices:{chain}"' not in page, f"{chain}: лишняя кнопка сбора цен"


# ---------- «Забрать цены»: закладка, адрес, секрет ----------
BOOKMARKLET_FILE = os.path.join(ROOT, "docs", "grab-prices.min.txt")


def _secret_of(page: str) -> str:
    """Секрет из разметки страницы: <span class="sekret">…</span>.

    Ищем именно разметку, а не «какую-то длинную строку»: так сторож заодно
    проверяет, что секрет стоит в своём месте и что его вообще показали.
    """
    found = re.search(r'<span class="sekret">([^<]+)</span>', page)
    return found.group(1).strip() if found else ""


def test_the_screen_gives_the_ready_bookmarklet_with_the_address_and_secret(web):
    """Раздел отдаёт ТУ САМУЮ закладку из репозитория, адрес и секрет рабочего места.

    Закладку берём из docs, а не собираем на месте: собранная здесь разошлась бы с
    docs/grab-prices.src.js при первой правке, и человек получил бы не то, что
    проверено. Адрес и номер он вводит в закладку руками, секрет — тем более:
    без них дверь /api/prices не пускает, и владелец искал их в переписке.
    """
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")

    with open(BOOKMARKLET_FILE, encoding="utf-8") as fh:
        ready = fh.read().strip()
    assert ready and ready.startswith("javascript:"), "закладка в репозитории не собрана"
    assert ready[:120] in page, "на экране не та закладка, что лежит в docs"

    assert "Собрать витрину Пятёрочки и Самоката" in page
    assert "http://localhost" in page, "адрес приложения не показан"
    assert PHONE in page, "номер рабочего места не показан"
    secret = _secret_of(page)
    assert secret, "секрет рабочего места не показан"

    users.open_workspace(PHONE)
    from app import api

    assert secret == api.workplace_secret(create=False), "показан не тот секрет"
    users.deactivate()


def test_the_bookmarklet_section_says_why_there_is_no_other_way(web):
    """Честная строка про ServicePipe и порядок шагов: иначе раздел читается как совет."""
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")
    assert "ServicePipe" in page
    assert "Пропускает только ваш браузер" in page
    assert "Отправить в приложение" in page
    assert "Секрет заменён" not in page, "оговорка о смене показана до самой смены"


def test_the_secret_lives_only_on_this_screen(web):
    """Секрет показывается на «Кабинетах» и не уезжает на другие экраны.

    Он и есть ключ рабочего места: попав в шапку, в чужой шаблон или в общий
    ответ, он перестал бы что-либо защищать.
    """
    enter(web)
    secret = _secret_of(web.get("/accounts").data.decode("utf-8"))
    assert secret

    for path in ("/", "/prices", "/stores"):
        answer = web.get(path)
        body = answer.data.decode("utf-8", "replace")
        assert secret not in body, f"{path}: секрет рабочего места уехал на другой экран"


def test_renewing_the_secret_changes_it_and_shuts_the_old_one_out(web):
    """«Сменить секрет» — одно нажатие, и старый перестаёт отпирать дверь сразу.

    Проверяем не надпись, а саму дверь: старый секрет получает отказ, новый
    принимает пакет и товары ложатся. Иначе смена была бы сменой надписи на экране.
    """
    enter(web)
    old = _secret_of(web.get("/accounts").data.decode("utf-8"))
    assert old

    answer = web.post("/accounts", data={"do": "new-secret"})
    assert answer.status_code == 302 and "secret=new" in answer.headers["Location"]

    page = web.get("/accounts?secret=new").data.decode("utf-8")
    fresh = _secret_of(page)
    assert fresh and fresh != old, "секрет не сменился"
    assert "Секрет заменён" in page, "человеку не сказали, что закладку надо ввести заново"

    packet = {"store": "pyaterochka", "collected_at": "2026-10-18T22:00:00",
              "items": [{"sku": "4306830", "name": "Азу из курицы 300г", "price": 176.99,
                         "in_stock": True, "unit": "pcs", "weight_g": 300,
                         "url": "https://5ka.ru/product/azu-iz-kuritsy--4306830/"}]}

    stale = web.post("/api/prices", json={**packet, "workplace": PHONE, "secret": old})
    assert stale.status_code == 401, "старый секрет всё ещё отпирает дверь"

    good = web.post("/api/prices", json={**packet, "workplace": PHONE, "secret": fresh})
    assert good.status_code == 200, good.get_data(as_text=True)[:300]
    assert good.get_json()["prices_saved"] == 1


def flat(page: str) -> str:
    """Страница без лишних пробелов и переводов строк — для проверки подписей."""
    return " ".join(page.split())


def _personal_rows() -> None:
    """Строки в ЛИЧНОМ рабочем месте — так их кладёт закладка: у Пятёрочки с ценой."""
    users.open_workspace(PHONE)
    five = repo.get_store("pyaterochka")
    samokat = repo.get_store("samokat")
    with_price = repo.upsert_store_product(five.id, "4306830", "Азу из курицы 300г",
                                           url="https://5ka.ru/product/azu--4306830/")
    repo.save_price(with_price, 176.99)
    repo.upsert_store_product(samokat.id, "78001656", "Молоко 3,2% 900 мл")   # без цены
    users.deactivate()


def _phone_cards() -> None:
    """Строки в ЕДИНОЙ базе — так их кладёт телефон кнопкой «Раздел»."""
    from app import phonecart

    assert phonecart.accept_cards({"store": "pyaterochka", "cards": [
        {"url": "https://5ka.ru/product/moloko-prostokvashino--1111111/",
         "name": "Молоко Простоквашино 2,5% 930 мл", "price": 99.9},
        {"url": "https://5ka.ru/product/hleb-borodinski--2222222/",
         "name": "Хлеб Бородинский 400 г", "price": 45.0},
    ]})["ok"]
    assert phonecart.accept_cards({"store": "samokat", "cards": [
        {"url": "https://samokat.ru/product/333333", "name": "Сыр Тильзитер 200 г",
         "price": 219.0},
    ]})["ok"]


def test_the_shelf_counts_the_unified_base_from_the_phone(web):
    """Карточки, приехавшие с телефона, видны на «Кабинетах» — иначе экран врёт.

    19.10.2026: Пятёрочка и Самокат приезжают в ЕДИНУЮ базу (catalog.db), а _shelf
    считал только личное рабочее место и писал «товаров в базе нет» сразу после
    успешного «Раздела» на телефоне, хотя «Магазины» эти товары показывали.
    """
    enter(web)
    _phone_cards()

    page = flat(web.get("/accounts").data.decode("utf-8"))

    assert "в единой базе товаров этой сети — 2" in page, "Пятёрочка с телефона не сосчитана"
    assert "в единой базе товаров этой сети — 1" in page, "Самокат с телефона не сосчитан"
    assert "в единой базе товаров этой сети нет" not in page, "экран говорит «нет» там, где есть"


def test_the_shelf_counts_both_bases_side_by_side(web):
    """Личное рабочее место и единая база — две разные цифры, и обе на экране."""
    enter(web)
    _personal_rows()
    _phone_cards()

    page = flat(web.get("/accounts").data.decode("utf-8"))

    assert page.count("в единой базе товаров этой сети — 2") == 1
    assert page.count("в единой базе товаров этой сети — 1") == 1
    assert page.count("в вашем рабочем месте строк — 1") == 2
    # Цена есть только у Пятёрочки в личном рабочем месте — про неё и говорим.
    assert page.count("в вашем рабочем месте строк — 1, последние цены приняты") == 1


def test_an_empty_shelf_says_it_is_empty(web):
    """Ни одного товара ни там, ни там — так и написано, у обеих сетей."""
    enter(web)
    page = flat(web.get("/accounts").data.decode("utf-8"))
    assert page.count("в единой базе товаров этой сети нет · в вашем рабочем месте строк нет") == 2


def test_a_broken_catalog_base_does_not_break_the_section(web):
    """Битая единая база: раздел остаётся, её число не показывается, личный счёт — есть.

    Ошибка счёта ОДНОЙ базы не повод ронять раздел: человек всё равно должен увидеть,
    что приехало в рабочее место, и что единую базу сосчитать не вышло.
    """
    from app.catalog import store as catalog_store

    os.makedirs(os.path.dirname(catalog_store.path()), exist_ok=True)
    with open(catalog_store.path(), "wb") as fh:
        fh.write(b"this is not a database")     # чужой файл на месте базы

    enter(web)
    answer = web.get("/accounts")
    assert answer.status_code == 200, "битая единая база уронила раздел"
    page = flat(answer.data.decode("utf-8"))
    assert page.count("единая база не сосчиталась") == 2
    assert page.count("в вашем рабочем месте строк нет") == 2


APP_UA = "Mozilla/5.0 (Linux; Android 14; wv) KorzinaApp/1.6"


def test_the_phone_gets_no_bookmarklet(web):
    """На телефоне закладку не предлагают: там витрину забирает «Раздел» в приложении.

    Перетаскивать на телефоне некуда, а сбор идёт кнопкой приложения — инструкция про
    закладку и показ секрета были бы там советом сделать невозможное.
    """
    enter(web)
    page = web.get("/accounts", headers={"User-Agent": APP_UA}).data.decode("utf-8")

    with open(BOOKMARKLET_FILE, encoding="utf-8") as fh:
        ready = fh.read().strip()
    assert ready[:120] not in page, "телефону всё ещё предлагают закладку"
    assert "Закладка нужна только на компьютере" in flat(page), "не сказано, что происходит"
    assert "Раздел" in page, "не названа кнопка приложения, которой идёт сбор"
    assert _secret_of(page) == "", "телефону показан секрет рабочего места для закладки"


def test_the_desktop_still_gets_the_bookmarklet(web):
    """На компьютере закладка остаётся: это его дорога, телефонная — «Раздел»."""
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")

    with open(BOOKMARKLET_FILE, encoding="utf-8") as fh:
        ready = fh.read().strip()
    assert ready[:120] in page, "на компьютере пропала закладка"
    assert "Закладка нужна только на компьютере" not in flat(page), "компьютеру говорят про телефон"


def test_without_the_bookmarklet_file_the_screen_says_so(web, monkeypatch):
    """Закладки нет в сборке — экран не молчит и не рисует пустую ссылку."""
    from app.web.screens import accounts

    monkeypatch.setattr(accounts, "PRICES_BOOKMARKLET",
                        os.path.join(ROOT, "docs", "нет-такого-файла.min.txt"))
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")
    assert "Закладка не собрана в этой сборке" in page


def test_the_demo_shelf_is_warned_about(web):
    """Демо — общая витрина: дверь её не пускает, и об этом сказано до нажатия."""
    web.post("/demo")
    page = web.get("/accounts").data.decode("utf-8")
    assert "Это демо-витрина, и дверь" in page


def test_the_secret_never_reaches_the_journal(web, caplog):
    """Секрет не уезжает в журнал — даже когда дверь им отказывает.

    Экран показывает секрет нарочно, и обещание у этого показа одно: он остаётся
    на странице. Журнал обещание нарушает молча — он уезжает в файлы и виден
    всякому, у кого есть доступ к серверу. Самое вероятное место утечки — отказ
    замка: там пишется настоящая причина, и подставить в неё присланное значение
    проще всего.
    """
    import logging

    with caplog.at_level(logging.DEBUG):
        enter(web)
        secret = _secret_of(web.get("/accounts").data.decode("utf-8"))
        assert secret, "секрет не показан — сторожу нечего проверять"

        web.post("/api/prices", json={"store": "pyaterochka",
                                      "collected_at": "2026-10-18T22:00:00",
                                      "workplace": PHONE,
                                      "secret": secret + "хвост",
                                      "items": []})

    journal = "\n".join(str(r.getMessage()) for r in caplog.records)
    assert journal, "журнал пуст — сторож ничего не поймал"
    assert secret not in journal, "секрет рабочего места уехал в журнал"
    # Контроль сторожа: отказ двери в журнале ЕСТЬ. Иначе «секрета в журнале нет»
    # значило бы всего лишь «в журнале вообще ничего нет», и сторож был бы слеп.
    assert "отказ API /api/prices" in journal, "отказ замка в журнал не записан"

