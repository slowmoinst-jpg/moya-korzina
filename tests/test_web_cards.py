"""«Карты и акции» и «О продукте» на новом интерфейсе.

Проверяется главным образом одно: ОШИБКА ВО ВВОДЕ НЕ ДОЛЖНА ПРОХОДИТЬ МОЛЧА.
Условия карты — это параметры формулы (app/optimizer/calc.py), и опечатка здесь не
роняет приложение и ничего не подсвечивает: расчёт просто выдаёт другое число, а
человек уезжает в другой магазин, не узнав почему. Поэтому здесь ловятся не
падения, а тишина — случаи, когда введённое молча превратилось бы в ноль:

  «2,5» с запятой из русской раскладки;
  «использовано» больше потолка, отчего cap_left уходит в ноль;
  срок, заданный наоборот, при котором акция не действует ни в один день;
  потолок 0 ₽ — выключенная акция, которую легко принять за настроенную.

И второе, не менее важное: ВВЕДЁННОЕ НЕ ПРОПАДАЕТ. Форма с претензией возвращает
значения в поля; у кого однажды «съело» десять заполненных полей, тот второй раз их
не заполнит.

Про «О продукте» проверяется, что экран не врёт в ту сторону, в какую соврать легче
всего, — не выдаёт непроверенное за проверенное.
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import contextmanager

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.models import Card, Offer  # noqa: E402

PHONE = "79990000001"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_web.py, и по той же причине.

    config.db_path не подменяется: подмена вернула бы одну базу всем и отключила бы
    выбор базы по человеку. Сдвигается корень и папка рабочих мест.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    client.post("/login", data={"phone": PHONE, "next": "/"})
    yield client
    users.deactivate()


@contextmanager
def base():
    """Открыть базу рабочего места, чтобы заглянуть в неё из теста напрямую.

    Приложение выбирает базу в начале запроса и СНИМАЕТ её в конце (app/web/auth.py):
    иначе поток из пула унёс бы базу одного человека следующему. Значит между
    запросами она не открыта ни у кого, и тест обязан открыть её себе сам — иначе
    repo отвечает из базы по умолчанию, где нет даже схемы.
    """
    users.open_workspace(PHONE)
    try:
        yield repo
    finally:
        users.deactivate()


def text_of(answer) -> str:
    return answer.data.decode("utf-8")


def cards_in_base() -> list[Card]:
    with base() as db:
        return db.list_cards()


def offers_in_base() -> list[Offer]:
    with base() as db:
        return db.list_offers()


def a_card(client, bank="Альфа-Банк", name="Альфа-Карта") -> int:
    client.post("/cards", data={"action": "card_save", "card_id": "", "bank": bank, "name": name})
    return next(c.id for c in cards_in_base() if c.bank == bank and c.name == name)


def a_store():
    with base() as db:
        return db.list_stores()[0]


def offer_form(card_id: int, store_id: int, **changes) -> dict:
    form = {"action": "offer_save", "offer_id": "", "card_id": str(card_id),
            "store_id": str(store_id), "percent": "5", "cap_rub": "3000",
            "cap_used": "0", "min_check_rub": "0", "valid_from": "", "valid_to": "",
            "activated": "on"}
    form.update(changes)
    return form


# ---------- экран открывается ----------
def test_cards_screen_opens(web):
    page = text_of(web.get("/cards"))
    assert "Карты и акции" in page
    assert "Справочник банков" in page


def test_about_screen_opens(web):
    assert "Дешевле по строке" in text_of(web.get("/about"))


# ---------- карты ----------
def test_a_card_is_created_and_lands_in_the_list(web):
    a_card(web)
    assert "Альфа-Карта" in text_of(web.get("/cards"))


def test_a_card_without_a_bank_is_refused_with_words_and_keeps_what_was_typed(web):
    """Отказ обязан сказать, что не так, и вернуть набранное обратно в поле."""
    answer = web.post("/cards", data={"action": "card_save", "card_id": "",
                                      "bank": "", "name": "Карта без банка"})
    page = text_of(answer)
    assert "Впишите банк" in page
    assert "Карта без банка" in page, "набранное название пропало из формы"
    assert not cards_in_base()


def test_deleting_a_card_takes_its_offers(web):
    card_id = a_card(web)
    store = a_store()
    web.post("/cards", data=offer_form(card_id, store.id))
    assert offers_in_base()

    web.post("/cards", data={"action": "card_delete", "card_id": str(card_id)})
    assert not cards_in_base()
    assert not offers_in_base(), "акции пережили свою карту"


# ---------- акции: где ошибка стоит дороже всего ----------
def test_a_comma_in_a_percent_is_a_number_and_not_a_zero(web):
    """«2,5» из русской раскладки обязано стать 2,5 — а не пропасть в ноль.

    Самая дорогая тишина на этом экране: поле <input type="number"> отдаёт серверу
    пустую строку, если в нём запятая. Человек видит своё число, сервер не видит
    ничего, и кэшбэк тихо становится нулевым.
    """
    card_id = a_card(web)
    web.post("/cards", data=offer_form(card_id, a_store().id, percent="2,5"))
    assert offers_in_base()[0].percent == 2.5


def test_spaces_and_a_rouble_sign_do_not_break_a_number(web):
    card_id = a_card(web)
    web.post("/cards", data=offer_form(card_id, a_store().id, cap_rub="3 000 ₽"))
    assert offers_in_base()[0].cap_rub == 3000.0


def test_letters_instead_of_a_percent_are_refused_by_name(web):
    card_id = a_card(web)
    page = text_of(web.post("/cards", data=offer_form(card_id, a_store().id, percent="пять")))
    assert "Процент кэшбэка" in page and "не число" in page
    assert not offers_in_base()


def test_a_percent_above_a_hundred_is_refused_with_an_explanation(web):
    card_id = a_card(web)
    page = text_of(web.post("/cards", data=offer_form(card_id, a_store().id, percent="130")))
    assert "больше ста" in page
    assert not offers_in_base()


def test_used_above_the_cap_is_refused_because_it_switches_the_offer_off(web):
    """Использовано больше потолка — не описка, а выключенная акция.

    cap_left уходит в ноль, calc перестаёт начислять, и со стороны это выглядит
    как «почему-то невыгодно». Поэтому отказ, а не тихое сохранение.
    """
    card_id = a_card(web)
    page = text_of(web.post("/cards", data=offer_form(
        card_id, a_store().id, cap_rub="1000", cap_used="1500")))
    assert "больше лимита" in page
    assert not offers_in_base()


def test_a_backwards_period_is_refused(web):
    """Срок «с 30.09 по 01.09» не сработает ни в один день — молчать нельзя."""
    card_id = a_card(web)
    page = text_of(web.post("/cards", data=offer_form(
        card_id, a_store().id, valid_from="2026-09-30", valid_to="2026-09-01")))
    assert "наоборот" in page
    assert not offers_in_base()


def test_a_refused_offer_keeps_every_typed_value(web):
    """Одна претензия не должна стоить человеку всех остальных заполненных полей."""
    card_id = a_card(web)
    page = text_of(web.post("/cards", data=offer_form(
        card_id, a_store().id, percent="абв", cap_rub="7500", min_check_rub="1200")))
    assert "абв" in page and "7500" in page and "1200" in page


def test_a_zero_cap_is_saved_but_called_out(web):
    """Потолок 0 ₽ — это выключенная акция, а не «без ограничения». И это надо сказать."""
    card_id = a_card(web)
    answer = web.post("/cards", data=offer_form(card_id, a_store().id, cap_rub="0"),
                      follow_redirects=True)
    assert offers_in_base()[0].cap_rub == 0.0
    assert "не начисляет" in text_of(answer)


def test_an_offer_needing_activation_says_it_will_be_skipped(web):
    card_id = a_card(web)
    form = offer_form(card_id, a_store().id, requires_activation="on")
    form.pop("activated")                      # галочка снята — браузер её не шлёт
    answer = web.post("/cards", data=form, follow_redirects=True)
    assert offers_in_base()[0].requires_activation is True
    assert "пропустит" in text_of(answer)


def test_a_zero_cap_and_a_spent_cap_are_told_apart(web):
    """Не дозаполненное условие и отработанный период — разные беды, следствие одно."""
    card_id = a_card(web)
    store = a_store()
    with base() as db:
        db.upsert_offer(Offer(None, card_id, store.id, 5.0, 0.0))

    assert "Потолок не задан" in text_of(web.get("/cards"))


def test_an_offer_says_why_it_does_not_work_today(web):
    """Не «не действует», а почему именно: причин три, и в расчёте они неразличимы."""
    card_id = a_card(web)
    store = a_store()                          # база открывается по одной за раз
    with base() as db:
        db.upsert_offer(Offer(None, card_id, store.id, 5.0, 3000.0, valid_to="2020-01-01"))
    assert "Срок истёк" in text_of(web.get("/cards"))


def test_editing_an_offer_has_its_own_address(web):
    """На «эту акцию» должна быть ссылка: ради этого переезд со Streamlit и затевался."""
    card_id = a_card(web)
    web.post("/cards", data=offer_form(card_id, a_store().id, min_check_rub="1499"))
    offer_id = offers_in_base()[0].id

    page = text_of(web.get(f"/cards?offer={offer_id}"))
    assert "1499" in page and "Правка акции" in page


def test_an_offer_can_be_deleted_without_its_card(web):
    card_id = a_card(web)
    web.post("/cards", data=offer_form(card_id, a_store().id))
    offer_id = offers_in_base()[0].id

    web.post("/cards", data={"action": "offer_delete", "offer_id": str(offer_id)})
    assert not offers_in_base()
    assert cards_in_base(), "вместе с акцией пропала карта"


# ---------- загрузка файлом ----------
def upload(client, body: bytes, name: str = "offers.csv"):
    return client.post("/cards", data={"action": "csv",
                                       "offers_csv": (io.BytesIO(body), name)},
                       content_type="multipart/form-data", follow_redirects=True)


def test_a_file_with_offers_is_loaded(web):
    store = a_store()
    body = ("bank,card,store_code,percent,cap_rub,min_check_rub\n"
            f"Тинькофф,Блэк,{store.code},5,3000,1000\n").encode("utf-8")
    answer = upload(web, body)
    assert "добавлено 1" in text_of(answer)
    assert offers_in_base()[0].min_check_rub == 1000.0


def test_a_russian_excel_file_is_read_and_not_declared_broken(web):
    """cp1251 и точка с запятой — обычная выгрузка русского Excel, а не сломанный файл."""
    store = a_store()
    body = ("bank;card;store_code;percent;cap_rub\n"
            f"Тинькофф;Блэк;{store.code};5;3000\n").encode("cp1251")
    answer = upload(web, body)
    assert "добавлено 1" in text_of(answer)
    assert cards_in_base()[0].bank == "Тинькофф"


def test_a_second_load_of_the_same_file_updates_and_does_not_duplicate(web):
    store = a_store()
    body = (f"bank,card,store_code,percent,cap_rub\nТинькофф,Блэк,{store.code},5,3000\n").encode()
    upload(web, body)
    upload(web, (f"bank,card,store_code,percent,cap_rub\n"
                 f"Тинькофф,Блэк,{store.code},7,3000\n").encode())
    assert len(offers_in_base()) == 1
    assert offers_in_base()[0].percent == 7.0


def test_a_skipped_line_is_named_and_the_rest_still_loads(web):
    """Тихо пропущенная строка — те же неполные условия, только незаметные."""
    store = a_store()
    body = ("bank,card,store_code,percent,cap_rub\n"
            "Тинькофф,Блэк,такой-сети-нет,5,3000\n"
            f"Тинькофф,Блэк,{store.code},5,3000\n").encode("utf-8")
    page = text_of(upload(web, body))
    assert "строка 2" in page and "такой-сети-нет" in page
    assert "добавлено 1" in page


def test_a_file_without_the_needed_columns_says_which_ones(web):
    page = text_of(upload(web, b"bank,card\n\xd0\x90,\xd0\x91\n"))
    assert "не хватает колонок" in page
    assert "store_code" in page and "percent" in page


def test_no_file_is_a_sentence_and_not_a_crash(web):
    page = text_of(web.post("/cards", data={"action": "csv"}))
    assert "Файл не выбран" in page


# ---------- справочник банков ----------
def test_a_card_can_be_taken_from_the_reference(web):
    from app import bank_reference

    banks = bank_reference.all_banks()
    if not banks:
        pytest.skip("справочника банков нет в этой установке")
    row = banks[0]
    web.post("/cards", data={"action": "bank_add", "bank": row["bank"],
                             "title": bank_reference.card_title(row)})
    assert any(c.bank == row["bank"] for c in cards_in_base())


def test_the_reference_does_not_invent_percents(web):
    """Справочник заводит карту, но не условия: выдуманный процент расчёт принял бы за факт."""
    from app import bank_reference

    banks = bank_reference.all_banks()
    if not banks:
        pytest.skip("справочника банков нет в этой установке")
    row = banks[0]
    web.post("/cards", data={"action": "bank_add", "bank": row["bank"],
                             "title": bank_reference.card_title(row)})
    assert not offers_in_base()


# ---------- «О продукте»: экран честности ----------
def test_about_does_not_pass_the_unverified_off_as_working(web):
    """Написанное, но не проверенное живьём, экран называет своим именем.

    17.09.2026 предмет этой проверки сменился: расширения-сборщика больше нет, его
    место занял браузер на нашем сервере. Обещание осталось прежним — не выдавать
    ненаписанное и непроверенное за работающее.
    """
    page = text_of(web.get("/about"))
    assert "не проверено" in page or "неизвестно" in page
    assert "Окно магазина внутри приложения" in page
    # Четыре сети встречают сервер своей проверкой — умолчать об этом нельзя,
    # иначе человек решит, что не работает приложение.
    assert "Пятёрочка, Самокат, Лента, Дикси" in page


def test_about_does_not_promise_an_extension_any_more(web):
    """Расширения нет — и звать ставить его значит посылать человека впустую."""
    page = text_of(web.get("/about"))
    assert "Расширение-сборщик" not in page
    assert "расширени" not in page.lower()


def test_about_tells_that_the_entrance_has_no_confirmation(web):
    """Вход по номеру без кода — осознанная мера, и молчать о ней нельзя."""
    page = text_of(web.get("/about"))
    assert "без подтверждения" in page
    assert "кто знает номер" in page


def test_about_no_longer_repeats_the_stale_september_claims(web):
    """Прежний текст врал в обе стороны: ВкусВилл «не отвечает», Лента и ФНС «в планах»."""
    page = text_of(web.get("/about"))
    assert "сайт не отвечает" not in page
    assert "Ozon Fresh, Лента, Самокат, Яндекс Лавка — нужен браузер" not in page
    assert "71 тест" not in page


def test_about_shows_the_real_thresholds_and_not_an_example(web):
    """Пороги берутся из акций человека: пример объяснил бы правило и промолчал о его корзине."""
    card_id = a_card(web)
    store = a_store()
    with base() as db:
        db.upsert_offer(Offer(None, card_id, store.id, 5.0, 3000.0, min_check_rub=1499.0))

    page = text_of(web.get("/about"))
    assert "1 499" in page and store.name in page


def test_about_and_stores_say_the_same_about_a_chain(web):
    """Один список на два экрана: две копии разошлись бы через месяц."""
    from app.web.screens.stores import HOW

    about = text_of(web.get("/about"))
    for state, what in HOW.values():
        assert what[:40] in about, what[:40]
        assert state


def test_a_card_page_survives_an_empty_workspace(web):
    """Пустое рабочее место — норма: настоящие начинаются без единой карты."""
    assert not cards_in_base()
    assert web.get("/cards").status_code == 200
    assert web.get("/about").status_code == 200


def test_cards_repo_is_the_only_source_of_truth(web):
    """Экран ничего не считает сам: что лежит в repo, то он и показывает."""
    with base() as db:
        db.upsert_card(Card(None, "Банк-Из-Базы", "Карта-Из-Базы"))
    assert "Карта-Из-Базы" in text_of(web.get("/cards"))
