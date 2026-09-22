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
    return client.post("/login", data={"phone": PHONE, "next": "/"})


def seed_basket() -> int:
    """Корзина из одной позиции с подтверждённым артикулом Магнита.

    Магнит взят нарочно: корзину он принимает нарядом через расширение, а не
    ссылкой, — то есть проходит весь путь, который здесь и проверяется.
    """
    users.open_workspace(PHONE)
    product_id = repo.upsert_product(Product(id=None, name="Молоко 3.2% 900 мл", unit="pcs"))
    store = repo.get_store("magnit")
    sp_id = repo.upsert_store_product(store.id, "magnit-1", "Молоко Простоквашино 900 мл")
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
    """
    enter(web)
    page = web.get("/accounts").data.decode("utf-8")
    assert "Загрузить распакованное" not in page
    assert "Секрет рабочего места" not in page
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
    assert seen["skus"] == ["magnit-1"]


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
        "cart_result": {"ok": ["magnit-1", "magnit-2"],
                        "failed": [{"sku": "magnit-9", "why": "нет в наличии"}]},
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
