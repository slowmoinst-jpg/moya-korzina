"""Сторожа четырёх правок «Связей», телефона и строк-призраков стенда.

Каждый тест падает на коде ДО своей правки — это и есть проверка, что он проверяет
именно её, а не соседнюю зелень:

  №4  подтверждение заглушки на «Связях» снимало отказ человека от сети
      (links._do_confirm безусловно звал repo.clear_rejects): «Убрать связь», потом
      подтверждение строки чека — и убранный артикул возвращался в наряд;
  №10 кнопка «Опознать в едином каталоге» шла мимо _RUNNING: второе нажатие и
      одновременный чек из ФНС поднимали два прохода разом;
  №11 пара только с ценой-заглушкой была подписана «кандидаты есть, не выбрано»,
      цена не называла свой источник и дату, а убрать её было нечем;
  №12 пустой наряд на телефоне отвечал «Корзина уже собирается или не собралась»
      там, где честный ответ — «класть нечего»;
  preview- строки стенда предпросмотра («preview-magnit-2») считались настоящими
      артикулами сетей: завышали замеры и подтверждались как связи человека.

Сеть здесь не трогается: tests/conftest.py ловит любой живой запрос.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.models import Product  # noqa: E402

WEB_PHONE = "79990000077"
APP_PHONE = "79990000071"


# ---------- «Связи»: приложение на временной базе ----------
@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app

    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    from conftest import sign_in

    sign_in(client, WEB_PHONE)
    yield client
    users.deactivate()


def workspace() -> None:
    users.open_workspace(WEB_PHONE)


def add(name: str, unit: str = "pcs", weight_g=None) -> int:
    workspace()
    try:
        return repo.upsert_product(Product(id=None, name=name, unit=unit, weight_g=weight_g))
    finally:
        users.deactivate()


def make_link(product_id: int, store_code: str, sku: str, raw_name: str,
              price: float | None = None, confirmed: bool = True) -> int:
    """Строка сети и сопоставление — как их оставил бы матчер или единая база."""
    workspace()
    try:
        store = repo.get_store(store_code)
        sp_id = repo.upsert_store_product(store.id, sku, raw_name)
        repo.confirm_mapping(product_id, sp_id, confirmed=confirmed)
        if price is not None:
            repo.save_price(sp_id, price)
        return sp_id
    finally:
        users.deactivate()


def in_workspace(call, *args, **kwargs):
    """Позвать repo в базе этого человека: вне рабочего места repo читает общую."""
    workspace()
    try:
        return call(*args, **kwargs)
    finally:
        users.deactivate()


def text(answer) -> str:
    return answer.data.decode("utf-8")


# ---------- №4: отказ от сети снимает только настоящий артикул ----------
def test_confirming_a_stub_keeps_the_refusal(web):
    """Заглушка — цена, а не связь: отказ человека от сети она не отменяет.

    Путь целиком через экран: человек убрал связь (отказ «*» и артикул), потом
    подтвердил строку чека. До правки repo.clear_rejects снимался безусловно, и
    сверка тут же возвращала убранный артикул в наряд.
    """
    pid = add("Творог 5% 200 г")
    make_link(pid, "lenta", "L-1", "Творог Лента 5% 200 г", price=89.9)

    assert web.post("/links", data={"action": "drop", "product": str(pid),
                                    "store": "lenta"}).status_code == 302
    lenta = in_workspace(repo.get_store, "lenta")
    assert in_workspace(repo.chain_rejected, pid, lenta.id), \
        "отказ не записался — тест проверяет не то"

    stub = f"hist-{pid}"
    make_link(pid, "lenta", stub, "ТВОРОГ 5% 200Г")

    answer = web.post("/links", data={"action": "confirm", "product": str(pid),
                                      "store": "lenta", "sku": stub})
    assert answer.status_code == 302
    assert in_workspace(repo.chain_rejected, pid, lenta.id), \
        "подтверждение заглушки сняло отказ от сети"


def test_confirming_a_real_sku_clears_the_refusal(web):
    """Настоящий артикул отказ снимает по-прежнему: «до нового подтверждения» — оно."""
    pid = add("Творог 5% 200 г")
    make_link(pid, "lenta", "L-1", "Творог Лента 5% 200 г")
    web.post("/links", data={"action": "drop", "product": str(pid), "store": "lenta"})
    lenta = in_workspace(repo.get_store, "lenta")
    assert in_workspace(repo.chain_rejected, pid, lenta.id)

    make_link(pid, "lenta", "556", "Творог Лента 5% 200 г отборный")
    web.post("/links", data={"action": "confirm", "product": str(pid),
                             "store": "lenta", "sku": "556"})
    assert not in_workspace(repo.chain_rejected, pid, lenta.id), \
        "настоящий артикул не снял отказ"


def two_names(monkeypatch, name: str, replacement) -> None:
    """Подменить функцию матчера ОБОИМИ именами, которыми её зовут.

    app/matcher/__init__.py делает `from app.matcher.matcher import find_candidates`,
    поэтому у функции два независимых имени: подменить одно — подменить не то.
    """
    from app import matcher as package
    from app.matcher import matcher as module

    monkeypatch.setattr(module, name, replacement)
    monkeypatch.setattr(package, name, replacement)


def test_every_decision_forgets_the_candidates(web, monkeypatch):
    """Кандидаты прошлого подбора — результат действия: после решения они устарели.

    Сброс стоял только в ветке заглушки, и после подтверждения НАСТОЯЩЕГО артикула
    список прошлого подбора возвращался по «Убрать связь» (adversarial-проверка
    18.10.2026, T11).
    """
    from app.models import Candidate
    from app.web.screens import links as links_screen

    pid = add("Страчателла 200 г")
    make_link(pid, "lenta", "556", "Страчателла Лента 200 г")
    found = [Candidate(store_code="lenta", sku="L-77", name="Мороженое страчателла 92 г",
                       price=79.0, weight_g=92, score=0.81)]
    two_names(monkeypatch, "find_candidates", lambda *a, **k: found)

    web.post("/links", data={"action": "find", "product": str(pid), "store": "lenta"})
    with web.session_transaction() as sess:
        assert sess.get(links_screen.CANDIDATES), "кандидаты не попали в сессию"

    web.post("/links", data={"action": "confirm", "product": str(pid),
                             "store": "lenta", "sku": "556"})
    with web.session_transaction() as sess:
        assert links_screen.CANDIDATES not in sess, \
            "после подтверждения настоящего артикула кандидаты остались"


def test_a_stub_flash_tells_about_the_refusal_only_when_there_is_one(web):
    """«Отказ остаётся в силе» — только если отказ есть: иначе это выдумка."""
    pid = add("Творог 5% 200 г")
    stub = f"hist-{pid}"
    make_link(pid, "lenta", stub, "ТВОРОГ 5% 200Г")

    page = text(web.post("/links", data={"action": "confirm", "product": str(pid),
                                         "store": "lenta", "sku": stub},
                         follow_redirects=True))
    assert "записана по строке" in page
    assert "остаётся в силе" not in page, "обещали отказ, которого не было"

    # Тот же путь, но отказ человек уже поставил — теперь про него сказать честно.
    make_link(pid, "lenta", "L-1", "Творог Лента 5% 200 г")
    web.post("/links", data={"action": "drop", "product": str(pid), "store": "lenta"})
    page = text(web.post("/links", data={"action": "confirm", "product": str(pid),
                                         "store": "lenta", "sku": stub},
                         follow_redirects=True))
    assert "остаётся в силе" in page, "отказ есть, а человеку про него не сказали"


# ---------- №10: кнопка отмечается в _RUNNING ----------
def test_the_catalog_button_marks_its_pass_running(web, monkeypatch):
    """Пока кнопка считает, проход этого рабочего места отмечен — второго не будет."""
    from app.catalog import link
    from app.catalog import refresh as catalog_refresh

    add("Творог 5% 200 г")
    seen = {}

    def fake_link_products(*_a, **_k):
        seen["running"] = link.running(WEB_PHONE)
        return {"tried": 1, "linked": 0}

    monkeypatch.setattr(catalog_refresh, "link_products", fake_link_products)
    assert web.post("/links", data={"action": "link_catalog"}).status_code == 302
    assert seen.get("running") is True, "кнопка не отметила проход в _RUNNING"
    assert link.running(WEB_PHONE) is False, "метка прохода осталась после кнопки"


def test_a_second_press_while_the_pass_is_going_starts_nobody(web, monkeypatch):
    """Второе нажатие во время прохода не поднимает второй, а просит сверку после.

    Раньше кнопка шла мимо _RUNNING: и второе нажатие, и одновременный чек из ФНС
    запускали ещё один проход, а снимки цен писались дважды.
    """
    from app.catalog import link
    from app.catalog import refresh as catalog_refresh
    from app.web.screens import links

    add("Творог 5% 200 г")
    passes: list[int] = []
    handed: list[tuple] = []

    def fake_link_products(*_a, **_k):
        passes.append(1)
        if len(passes) == 1:
            # Второе нажатие приходит, пока первый проход ещё идёт.
            links._do_link_catalog(repo.list_products(active_only=False))
        return {"tried": 1, "linked": 0}

    monkeypatch.setattr(catalog_refresh, "link_products", fake_link_products)
    monkeypatch.setattr(link, "link_soon",
                        lambda phone, sync=False: handed.append((phone, sync)) or True)

    web.post("/links", data={"action": "link_catalog"})
    assert len(passes) == 1, "второе нажатие запустило второй проход"
    assert handed and handed[0][1] is True, \
        "просьба о сверке из второго нажатия не передана фону"


# ---------- №11: подписи и действия заглушек ----------
def _stub_pair(pid: int, code: str = "lenta", sku: str | None = None,
               raw_name: str = "МОЛОКО 2,5% 930МЛ", day: str = "2026-08-14") -> None:
    """Пара, где подтверждена только строка чека этого же товара, со снимком цены."""
    workspace()
    try:
        store = repo.get_store(code)
        sp_id = repo.upsert_store_product(store.id, sku or f"hist-{pid}", raw_name)
        repo.confirm_mapping(pid, sp_id, confirmed=True)
        repo.save_price(sp_id, 89.9)
        repo.add_history_row(day, store.id, pid, raw_name, 1.0, 89.9, 89.9)
    finally:
        users.deactivate()


def test_a_stub_price_names_its_source_and_date(web):
    """«Цена сейчас — из ваших чеков или прайса» называла чек двухмесячной давности
    сегодняшней ценой. Теперь у подписи есть источник, название и дата."""
    pid = add("Молоко 2,5% 930 мл")
    _stub_pair(pid)

    page = text(web.get(f"/links?product={pid}&store=lenta"))
    assert "Цена из вашего чека" in page
    assert "МОЛОКО 2,5% 930МЛ" in page
    assert "от 14.08.2026" in page, "у цены из чека нет даты"
    assert "Сравнение магазинов сейчас её учитывает" in page
    assert "Это не та цена — убрать её (от сети не отказываюсь)" in page


def test_the_queue_calls_a_stub_pair_a_stub(web):
    """Пара только с заглушкой — не «кандидаты есть, не выбрано»: кандидатов нет."""
    pid = add("Молоко 2,5% 930 мл")
    _stub_pair(pid)

    page = text(web.get("/links"))
    assert "цена из чека или прайса, артикула сети нет" in page
    assert "кандидаты есть, не выбрано" not in page, \
        "заглушку выдали за неподтверждённого кандидата"


def test_dropping_a_stub_does_not_refuse_the_chain(web):
    """Убрать цену — не отказаться от сети: кандидаты и поиск остаются."""
    pid = add("Молоко 2,5% 930 мл")
    _stub_pair(pid)

    answer = web.post("/links", data={"action": "drop_stub", "product": str(pid),
                                      "store": "lenta", "sku": f"hist-{pid}"})
    assert answer.status_code == 302
    workspace()
    try:
        lenta = repo.get_store("lenta")
        assert repo.confirmed_mapping(pid, lenta.id) is None, "цена-заглушка не убралась"
        assert not repo.chain_rejected(pid, lenta.id), \
            "убрали цену, а записали отказ от сети целиком"
    finally:
        users.deactivate()


def test_dropping_a_stub_does_not_touch_a_real_sku(web):
    """Присланный из формы артикул сверяется с базой: настоящую связь так не убрать."""
    pid = add("Молоко 2,5% 930 мл")
    make_link(pid, "lenta", "556", "Молоко Лента 2,5% 930 мл", price=89.9)
    lenta = in_workspace(repo.get_store, "lenta")

    web.post("/links", data={"action": "drop_stub", "product": str(pid),
                             "store": "lenta", "sku": "hist-1"})
    mapping = in_workspace(repo.confirmed_mapping, pid, lenta.id)
    assert mapping and mapping["sku"] == "556", "подделанная форма убрала настоящую связь"


def test_dropping_a_stub_tells_the_truth_about_the_refusal(web):
    """«Вы не отказывались» — только если отказа правда нет.

    Пара могла быть убрана целиком раньше: тогда отказ в силе, и обещать обратное
    нельзя (adversarial-проверка 18.10.2026, T11).
    """
    pid = add("Молоко 2,5% 930 мл")
    _stub_pair(pid)
    page = text(web.post("/links", data={"action": "drop_stub", "product": str(pid),
                                         "store": "lenta", "sku": f"hist-{pid}"},
                         follow_redirects=True))
    assert "вы не отказывались" in page
    assert "остаётся в силе" not in page

    # Тот же путь с уже поставленным отказом: цену убираем, отказ остаётся.
    _stub_pair(pid)
    make_link(pid, "lenta", "556", "Молоко Лента 2,5% 930 мл", price=89.9)
    web.post("/links", data={"action": "drop", "product": str(pid), "store": "lenta"})
    assert in_workspace(repo.chain_rejected, pid, in_workspace(repo.get_store, "lenta").id)
    _stub_pair(pid)

    page = text(web.post("/links", data={"action": "drop_stub", "product": str(pid),
                                         "store": "lenta", "sku": f"hist-{pid}"},
                         follow_redirects=True))
    assert "вы не отказывались" not in page, "отказ есть, а человеку сказали обратное"
    assert "остаётся в силе" in page


# ---------- №12: телефон, пустой наряд ----------
@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from app.web.screens import result

    result._CALC.clear()
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    from conftest import sign_in

    sign_in(client, APP_PHONE)
    yield client
    result._CALC.clear()
    users.deactivate()


def _phone_workspace() -> None:
    users.open_workspace(APP_PHONE)


def _priced_basket() -> None:
    """Корзина с ценами в Магните — минимальное, чтобы «Заказать» дошло до наряда."""
    values = {"magnit": (40.0, 30.0), "pyaterochka": (50.0, 25.0), "vkusvill": (45.0, 28.0)}
    ids = []
    for name in ("Молоко 930 мл", "Хлеб Бородинский"):
        ids.append(repo.upsert_product(Product(id=None, name=name, unit="pcs")))
    for code in ("magnit",):
        store = repo.get_store(code)
        for pid in ids:
            sp_id = repo.upsert_store_product(store.id, f"sku-{code}-{pid}", f"товар {pid}")
            repo.confirm_mapping(pid, sp_id, confirmed=True)
            repo.save_price(sp_id, values[code][ids.index(pid)])
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, ids[0], 2.0)
    repo.set_basket_item(basket_id, ids[1], 1.0)


def _order_ready(phone_client, monkeypatch, plan):
    """Довести «Заказать» до наряда: вход в Магнит есть, наряд подменён."""
    from app import cartplan
    from app.shopbrowser import store as shopstore

    monkeypatch.setattr(shopstore, "load", lambda code: {"code": code})
    monkeypatch.setattr(cartplan, "why_not", lambda code: "")
    monkeypatch.setattr(cartplan, "build", lambda *a, **k: plan)
    return phone_client.post("/phone/cheaper", data={"store": "magnit"})


def test_an_empty_order_says_there_is_nothing_to_put(phone, monkeypatch):
    """Пустой наряд — «класть нечего», а не «корзина уже собирается или не собралась»."""
    from app import cartplan

    _phone_workspace()
    _priced_basket()
    plan = cartplan.CartPlan(store_code="magnit", gone=["Молоко 930 мл"],
                             note="Ни одного товара сейчас нет в наличии — класть в корзину нечего.")

    answer = _order_ready(phone, monkeypatch, plan)
    assert answer.status_code == 303
    location = answer.headers["Location"]
    assert "why=gone" in location, f"пустой наряд повёл не туда: {location}"
    assert "why=busy" not in location

    page = text(phone.get(location))
    assert "Положить нечего" in page and "нет в наличии" in page


def test_an_unknown_goods_order_says_the_chain_does_not_know_them(phone, monkeypatch):
    """Товары сети незнакомы — это тоже «класть нечего», и своим словом."""
    from app import cartplan

    _phone_workspace()
    _priced_basket()
    plan = cartplan.CartPlan(store_code="magnit", unknown=["Молоко 930 мл"],
                             note="Ни одна позиция этой сети не знакома — класть в корзину нечего.")

    answer = _order_ready(phone, monkeypatch, plan)
    assert "why=unknown" in answer.headers["Location"]
    page = text(phone.get(answer.headers["Location"]))
    assert "Положить нечего" in page and "не знает артикулов" in page


def test_a_mixed_empty_order_does_not_claim_there_is_nothing_in_stock(phone, monkeypatch):
    """gone и unknown разом — «нет в наличии» было бы враньём про незнакомые позиции.

    Наличие говорят только тогда, когда ВСЕ позиции ушли в gone; иначе честнее
    «сеть их не знает» (adversarial-проверка 18.10.2026, T11).
    """
    from app import cartplan

    _phone_workspace()
    _priced_basket()
    plan = cartplan.CartPlan(store_code="magnit", gone=["Молоко 930 мл"],
                             unknown=["Хлеб Бородинский"])

    answer = _order_ready(phone, monkeypatch, plan)
    assert answer.status_code == 303
    location = answer.headers["Location"]
    assert "why=unknown" in location, f"смешанный наряд повёл не туда: {location}"
    assert "why=gone" not in location

    page = text(phone.get(location))
    assert "не знает артикулов" in page
    assert "нет в наличии" not in page


def test_a_partial_order_says_how_many_stayed_out(phone, monkeypatch):
    """Часть наряда уехала — человеку говорят, сколько осталось, а не молчат."""
    from app import cartplan
    from app.shopbrowser import cart as cart_module

    _phone_workspace()
    _priced_basket()
    plan = cartplan.CartPlan(store_code="magnit",
                             lines=[cartplan.PlanLine(sku="1", qty=2.0, name="Молоко 930 мл")],
                             unknown=["Хлеб Бородинский"])
    monkeypatch.setattr(cart_module, "start", lambda *a, **k: True)

    answer = _order_ready(phone, monkeypatch, plan)
    location = answer.headers["Location"]
    assert "sent=magnit" in location and "left=1" in location, location


def test_a_failed_start_still_says_busy(phone, monkeypatch):
    """why=busy остаётся за своим случаем: передача уже идёт или сорвалась."""
    from app import cartplan
    from app.shopbrowser import cart as cart_module

    _phone_workspace()
    _priced_basket()
    plan = cartplan.CartPlan(store_code="magnit",
                             lines=[cartplan.PlanLine(sku="1", qty=2.0, name="Молоко 930 мл")])
    monkeypatch.setattr(cart_module, "start", lambda *a, **k: False)

    answer = _order_ready(phone, monkeypatch, plan)
    assert "why=busy" in answer.headers["Location"]


# ---------- preview-: строки-призраки стенда ----------
def test_preview_rows_are_placeholders():
    """Обе формы строки стенда — заглушки, настоящие артикулы — нет."""
    from app.catalog.model import placeholder_sku

    assert placeholder_sku("preview-magnit-2", "magnit")
    assert placeholder_sku("preview-magnit-2", None), "без кода сети форма не поймана"
    assert placeholder_sku("magnit-preview-3", "magnit")
    assert placeholder_sku("lenta-preview-7", None), "форма «<сеть>-preview-N» не поймана"
    # Прежние заглушки и настоящие артикулы остаются как были.
    assert placeholder_sku("hist-1") and placeholder_sku("list-2")
    assert placeholder_sku("magnit-огурцы-450", "magnit")      # строка справочника
    assert not placeholder_sku("magnit-огурцы-450", "lenta")   # но не артикул Ленты
    assert not placeholder_sku("123456", "magnit")
    assert not placeholder_sku("4607001", "lenta")


def test_placeholder_sku_does_not_care_about_case():
    """Один регистр в Python и SQL: LIKE регистр не различает, и правила расходились."""
    from app.catalog.model import placeholder_sku

    assert placeholder_sku("PREVIEW-lenta-7", None)
    assert placeholder_sku("Lenta-Preview-7", None)
    assert placeholder_sku("HIST-1")
    assert placeholder_sku("List-2")
    assert placeholder_sku("Magnit-огурцы-450", "MAGNIT"), "код сети сравнивается регистронезависимо"
    assert not placeholder_sku("L-1", "lenta"), "настоящий артикул заглушкой не стал"


# Что проверяет паритет: форма чужой сети (SQL её раньше не знал) и верхний регистр
# (Python его раньше различал). Строка «magnit-огурцы-450» — заглушка справочника,
# «12345» и «L-1» — настоящие артикулы.
PLACEHOLDER_CASES = ["preview-magnit-2", "lenta-preview-7", "PREVIEW-lenta-8",
                     "Lenta-Preview-9", "hist-1", "HIST-2", "list-3",
                     "magnit-огурцы-450", "12345", "L-1"]


def test_the_sql_rule_and_the_python_rule_agree(web):
    """Одно правило, один регистр: SQL видит ту же заглушку, что и Python."""
    from app.catalog.model import placeholder_sku

    magnit = in_workspace(repo.get_store, "magnit")
    for sku in PLACEHOLDER_CASES:
        pid = add(f"Товар {sku}")
        make_link(pid, "magnit", sku, f"имя {sku}")
        is_stub = placeholder_sku(sku, "magnit")
        assert bool(in_workspace(repo.mapping_matrix, real_only=True).get((pid, magnit.id))) \
            is (not is_stub), f"mapping_matrix разошлась с placeholder_sku на {sku!r}"
        real = in_workspace(repo.confirmed_mapping, pid, magnit.id, True)
        assert (real is not None) is (not is_stub), \
            f"confirmed_mapping(real_only=True) разошлась с placeholder_sku на {sku!r}"


def test_preview_rows_are_not_real_skus_in_the_base(web):
    """repo.real_sku_sql и экраны покрытия видят в preview- ту же заглушку."""
    pid = add("Страчателла 200 г")
    sp_id = make_link(pid, "magnit", "preview-magnit-2", "Страчателла 200 г", price=199.0)

    workspace()
    try:
        magnit = repo.get_store("magnit")
        assert repo.confirmed_mapping(pid, magnit.id, real_only=True) is None
        assert repo.mapping_matrix(real_only=True).get((pid, magnit.id)) is False
    finally:
        users.deactivate()
    assert sp_id


def test_the_migration_takes_preview_ghosts_out(tmp_path, monkeypatch):
    """Миграция снимает строки-призраки, а строку справочника и настоящий артикул — нет.

    Удаление нарочно узкое и узнаёт призрака по тексту артикула, без сверки с сетью
    строки: «preview-magnit-2» и «magnit-preview-3» уходят вместе со своими связями и
    снимками цен, а «magnit-огурцы-450» (источник справочной цены) и живой артикул
    Ленты остаются со своими связями и ценами. Стенд предпросмотра писал имена, равные
    строкам чеков, и такие строки завышали замер сопоставления.

    Отдельно — призрак ЧУЖОЙ сети: «lenta-preview-9» лежал в магазине Магнита, и до
    правки (F3, adversarial-проверка 18.10.2026) миграция его не трогала: артикул не
    начинался с кода сети строки. Такой строке в каталоге Магнита всё равно не место.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    from app import db

    db.init_db()
    magnit = repo.get_store("magnit")
    lenta = repo.get_store("lenta")
    ghost_a = repo.upsert_store_product(magnit.id, "preview-magnit-2", "Страчателла 200 г")
    ghost_b = repo.upsert_store_product(magnit.id, "magnit-preview-3", "Слива")
    foreign = repo.upsert_store_product(magnit.id, "lenta-preview-9", "Огурцы 450 г")
    reference = repo.upsert_store_product(magnit.id, "magnit-огурцы-450", "Огурцы 450 г")
    real = repo.upsert_store_product(lenta.id, "12345", "Молоко 930 мл")
    pid = repo.upsert_product(Product(id=None, name="Страчателла 200 г", unit="pcs"))
    for sp_id, price in ((ghost_a, 199.0), (ghost_b, 89.9), (foreign, 99.9),
                         (reference, 149.0), (real, 89.9)):
        repo.confirm_mapping(pid, sp_id, confirmed=True)
        repo.save_price(sp_id, price)

    with db.get_conn() as conn:              # база прежнего выпуска
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    db.init_db()

    with db.get_conn() as conn:
        left = {r["id"] for r in conn.execute("SELECT id FROM store_products")}
        prices = {r["store_product_id"] for r in conn.execute("SELECT store_product_id FROM store_prices")}
        links = {r["store_product_id"] for r in conn.execute("SELECT store_product_id FROM product_mapping")}
    ghosts = (ghost_a, ghost_b, foreign)
    assert all(sp_id not in left for sp_id in ghosts), "строки-призраки остались в базе"
    assert all(sp_id not in prices and sp_id not in links for sp_id in ghosts), \
        "за призраком (в том числе чужой сети) остались цена или связь"
    assert reference in left and real in left, "миграция снесла не только призраков"
    assert reference in prices and reference in links, "у строки справочника пропала цена или связь"
    assert real in prices and real in links, "у настоящего артикула пропала цена или связь"


def test_the_migration_names_every_candidate_before_deleting(tmp_path, monkeypatch, caplog):
    """Перед удалением каждый кандидат назван в логе: копии тома на момент миграции нет.

    По одной итоговой строке потом не понять, что именно было у человека, поэтому
    пишется строка на кандидата — артикул, сколько связей и снимков цен уйдёт вместе
    с ним (adversarial-проверка 18.10.2026, T11).
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    from app import db

    db.init_db()
    magnit = repo.get_store("magnit")
    ghost = repo.upsert_store_product(magnit.id, "preview-magnit-2", "Страчателла 200 г")
    foreign = repo.upsert_store_product(magnit.id, "lenta-preview-9", "Огурцы 450 г")
    kept = repo.upsert_store_product(magnit.id, "magnit-огурцы-450", "Огурцы 450 г")
    pid = repo.upsert_product(Product(id=None, name="Страчателла 200 г", unit="pcs"))
    for sp_id in (ghost, foreign, kept):
        repo.confirm_mapping(pid, sp_id, confirmed=True)
        repo.save_price(sp_id, 100.0)

    with db.get_conn() as conn:              # база прежнего выпуска
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    caplog.clear()
    with caplog.at_level("INFO"):
        db.init_db()

    assert "preview-magnit-2" in caplog.text, "кандидат не назван до удаления"
    assert "lenta-preview-9" in caplog.text, "чужой призрак в лог не попал"
    assert "связей 1" in caplog.text and "снимков цен 1" in caplog.text, \
        "в логе нет счёта связей и снимков цен"
    assert "magnit-огурцы-450" not in caplog.text, \
        "в кандидаты попала строка справочника — её удалять нельзя"


# ---------- T13: подтверждение человека — написание в словаре ----------
@pytest.fixture
def dictionary(tmp_path, monkeypatch):
    """Словарь названий на временной базе: живую data/catalog.db тесты не трогают."""
    from app.catalog import store as catalog_store

    monkeypatch.setattr(catalog_store, "path", lambda: str(tmp_path / "catalog.db"))
    catalog_store.init()
    return catalog_store


def variant_rows(catalog_store) -> list[dict]:
    with catalog_store.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT item_id, variant, source, chain, hits FROM name_variants ORDER BY id")]


def test_confirming_a_real_sku_teaches_the_dictionary(web, dictionary):
    """Человек подтвердил пару — написание сети стало СИЛЬНОЙ веткой словаря.

    До этой правки store.remember_variant не звал никто: 295 020 написаний от сетей и
    124 от чеков не дали ни одного нового товара, потому что сильную связь по правилам
    словаря даёт только источник «human», а его писал только этот путь (замер на
    боевой базе 18.10.2026: привязка та же 343 из 1159).
    """
    pid = add("Творог 5% 200 г")
    in_workspace(repo.set_master, pid, 4242, "words")
    make_link(pid, "lenta", "556", "Творог Лента 5% 200 г")

    assert web.post("/links", data={"action": "confirm", "product": str(pid),
                                    "store": "lenta", "sku": "556"}).status_code == 302

    got = variant_rows(dictionary)
    assert len(got) == 1, got
    assert got[0]["item_id"] == 4242, "написание привязано не к тому единому товару"
    assert got[0]["source"] == "human" and got[0]["chain"] == "lenta"
    assert got[0]["variant"] == "Творог Лента 5% 200 г", "записано не то написание"
    assert got[0]["hits"] == 1


def test_a_repeated_confirmation_does_not_double_the_variant(web, dictionary):
    """Повтор — тот же вариант с большим счётом: ключ (товар, свод, источник, сеть)."""
    pid = add("Творог 5% 200 г")
    make_link(pid, "lenta", "556", "Творог Лента 5% 200 г")

    for _ in range(2):
        # Привязка к единому товару ставится перед КАЖДЫМ подтверждением: в тесте
        # каталог пуст, и сверка после решения человека снимает привязку, которой
        # не нашла подтверждения (link._unlink). В бою товар в каталоге есть, и
        # условие «у товара есть master_id» держится само.
        in_workspace(repo.set_master, pid, 4242, "words")
        web.post("/links", data={"action": "confirm", "product": str(pid),
                                 "store": "lenta", "sku": "556"})

    got = variant_rows(dictionary)
    assert len(got) == 1, f"вариант размножился: {got}"
    assert got[0]["hits"] == 2, "повтор не поднял счёт наблюдений"


def test_confirming_a_stub_teaches_nothing(web, dictionary):
    """Заглушка — цена, а не связь сети: запоминать её как написание нечего."""
    pid = add("Творог 5% 200 г")
    in_workspace(repo.set_master, pid, 4242, "words")
    stub = f"hist-{pid}"
    make_link(pid, "lenta", stub, "ТВОРОГ 5% 200Г")

    web.post("/links", data={"action": "confirm", "product": str(pid),
                             "store": "lenta", "sku": stub})

    assert variant_rows(dictionary) == [], "заглушка попала в словарь как написание"


def test_without_a_master_the_variant_has_nothing_to_attach_to(web, dictionary):
    """Нет номера единого товара — словарю нечего запоминать, и это не ошибка."""
    pid = add("Творог 5% 200 г")
    make_link(pid, "lenta", "556", "Творог Лента 5% 200 г")

    assert web.post("/links", data={"action": "confirm", "product": str(pid),
                                    "store": "lenta", "sku": "556"}).status_code == 302

    assert variant_rows(dictionary) == []
