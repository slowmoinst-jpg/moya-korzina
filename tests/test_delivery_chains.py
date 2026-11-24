"""Чеки доставки: сеть узнаётся при импорте и проставляется уже загруженным.

ЗАЧЕМ (замер 16.11.2026, история владельца). Чеки доставки лежали в истории без сети, и их товары
не шли в сверку с каталогами сетей. Кабинет ФНС пишет такого продавца местом расчётов:

- доставка Магнита — «https://magnit.ru/dostavka/», юрлицо АО «Тандер»;
- доставка X5 — «http://delivery.x5-digital.ru», юрлицо ООО «ИКС 5 ДИДЖИТАЛ» у обеих сетей; чья
  покупка, видно только по адресу отправителя чека (support@5-delivery.ru — Пятёрочка,
  support@perekrestok-dostavka.ru — Перекрёсток);
- «Перекресток» узнавался и раньше, но строки его чеков легли без сети: в справочнике магазинов
  рабочего места Перекрёстка тогда не было. С 16.11.2026 он входит в orders.chains (решение
  владельца), и сеть ему ставится, как остальным. Сеть вне списка проход не ставит: с ней
  покупки ушли бы из «Истории заказов» (в тестах это METRO).

Формы чеков ниже — те, что отдал кабинет; товары и суммы выдуманы.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import baskets, chains, config, repo, users
from app.db import init_db
from app.fns import sync
from app.fns.client import CabinetError
from app.importers import bundle
from app.models import Product

MAGNIT = "https://magnit.ru/dostavka/"
X5 = "http://delivery.x5-digital.ru"
FIVE = "support@5-delivery.ru"
CROSS = "support@perekrestok-dostavka.ru"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "delivery.db"))
    # заказы — в сетях по умолчанию, что бы ни стояло в config.yaml
    monkeypatch.setattr(chains, "orderable", lambda: set(chains.DEFAULT_ORDERABLE))
    init_db()


# ---------- продавец -> сеть ----------
@pytest.mark.parametrize("seller, sender, code", [
    (MAGNIT, None, "magnit"),
    (MAGNIT, "dostavka@magnit.ru", "magnit"),
    ("Перекресток", None, "perekrestok"),
    (X5, FIVE, "pyaterochka"),
    (X5, CROSS, "perekrestok"),
    ('ООО "ИКС 5 ДИДЖИТАЛ" ', FIVE, "pyaterochka"),
    (X5, None, None),                              # чья доставка, по продавцу не видно
    (X5, "ofdreceipt@beeline.ru", None),
    ("Яндекс.Такси", FIVE, None),                  # отправителю верим только у общего продавца
    ("Яндекс.Еда", "dostavka@magnit.ru", None),
])
def test_delivery_seller_resolves_to_its_chain(seller, sender, code):
    assert chains.resolve(seller, sender) == code


def test_only_x5_delivery_is_a_shared_seller():
    assert chains.shared(X5) and chains.shared('ООО "ИКС 5 ДИДЖИТАЛ"')
    assert not chains.shared(MAGNIT) and not chains.shared("Пятёрочка") and not chains.shared(None)


# ---------- импорт ----------
def cheque(key: str, day: str, seller: str, user: str, sender: str, name: str) -> dict:
    """Чек, каким его отдаёт кабинет: в описи место расчётов, внутри — юрлицо и отправитель."""
    return {"key": key, "date": f"{day}T21:02:00", "store": seller, "total": 12900,
            "fiscalData": {"dateTime": f"{day}T21:02:00", "user": user, "retailPlace": seller,
                           "retailPlaceAddress": "127083, г. Москва, ул 8 Марта, д 10 стр 14",
                           "sellerAddress": sender, "totalSum": 12900,
                           "items": [{"name": name, "quantity": 1, "price": 12900, "sum": 12900}]}}


CHEQUES = [
    cheque("m1", "2026-07-06", MAGNIT, 'АО "ТАНДЕР"', "dostavka@magnit.ru", "Сметана Резной Палисад 15% 300г"),
    cheque("p1", "2026-08-08", X5, 'ООО "ИКС 5 ДИДЖИТАЛ" ', FIVE, "Хлебцы Каравай Финские 240г"),
    cheque("x1", "2026-07-06", X5, 'ООО "ИКС 5 ДИДЖИТАЛ" ', CROSS, "Гречка ядрица 900г"),
    cheque("u1", "2026-07-07", X5, 'ООО "ИКС 5 ДИДЖИТАЛ" ', "ofdreceipt@beeline.ru", "Сок томатный 1л"),
]


def stores_in_history() -> dict[str, str | None]:
    return {r["receipt_key"]: r["store_code"] for r in repo.list_history()}


def test_delivery_receipts_get_their_chain_on_import(db):
    got = bundle.import_bundle(json.dumps({"source": "lkdr", "receipts": CHEQUES}, ensure_ascii=False))

    assert stores_in_history() == {"m1": "magnit", "p1": "pyaterochka", "x1": "perekrestok"}
    # чья доставка u1, по чеку не видно: он отложен, а не потерян
    assert {r["key"] for r in got["other"]} == {"u1"}
    assert repo.pending_receipts() == []


class Cabinet:
    """Кабинет ФНС в одну страницу: опись и состав по ключу. token — единственный ключ, который
    он узнаёт (None — любой); обновление выдаёт fresh (не задан — его же). gone — чеки, которых
    в кабинете больше нет."""

    def __init__(self, receipts: list[dict], token: str | None = None, fresh: str | None = None,
                 gone: tuple[str, ...] = ()) -> None:
        self.receipts, self.asked, self.token = receipts, [], token
        self.fresh, self.gone, self.refreshed = fresh or token, gone, 0

    def catalogue(self, token, on_step=None):
        return [{k: r[k] for k in ("key", "date", "store", "total")} for r in self.receipts]

    def fiscal_many(self, token, keys):
        for key in keys:
            if self.token and token != self.token:
                yield key, "auth", None
            elif key in self.gone:
                yield key, "gone", None
            else:
                self.asked.append(key)
                yield key, "ok", next(r["fiscalData"] for r in self.receipts if r["key"] == key)

    def refresh(self, refresh_token):
        self.refreshed += 1
        return SimpleNamespace(token=self.fresh, refresh_token="ref-2")


def test_cabinet_asks_the_shared_delivery_instead_of_setting_it_aside(db):
    """По описи доставку X5 не судим: её состав спрашиваем и решаем по самому чеку."""
    taxi = cheque("t1", "2026-07-08", "Яндекс.Такси", 'ООО "ЯНДЕКС.ТАКСИ"', "", "Перевозка пассажиров")
    cabinet = Cabinet(CHEQUES + [taxi])
    repo.set_setting(sync.K_TOKEN, "acc-1")

    sync.sync(cabinet=cabinet)

    assert sorted(cabinet.asked) == ["m1", "p1", "u1", "x1"], "такси отложено по описи, без запроса состава"
    assert stores_in_history() == {"m1": "magnit", "p1": "pyaterochka", "x1": "perekrestok"}
    assert repo.other_store_receipt_keys() == {"u1", "t1"}

    cabinet.asked.clear()
    sync.sync(cabinet=cabinet)
    assert cabinet.asked == ["t1"]
    assert repo.other_receipt_lines("t1")[0]["name"] == "Перевозка пассажиров"
    assert "t1" not in stores_in_history()

    cabinet.asked.clear()
    sync.sync(cabinet=cabinet)
    assert cabinet.asked == [], "решённый чек второй раз не спрашивается"


# ---------- уже загруженные чеки ----------
def loaded(key: str, seller: str, day: str = "2026-07-06", total: float = 129.0) -> int:
    """Чек, который лёг в историю без сети, как до отсева."""
    pid = repo.upsert_product(Product(id=None, name=f"Товар {key}", unit="pcs"))
    repo.add_history_row(day, None, pid, f"Товар {key}", 1, total, total, key)
    repo.mark_receipt_imported(key, day, seller, total, 1, "lkdr")
    return pid


def test_settle_gives_loaded_receipts_their_chain_and_is_repeatable(db, monkeypatch):
    for key, seller in (("m1", MAGNIT), ("c1", "Перекресток"), ("p1", X5), ("x1", X5), ("t1", "Яндекс.Такси"),
                        ("k1", "Метро Кэш энд Керри")):
        loaded(key, seller)
    kept = repo.upsert_product(Product(id=None, name="Товар с сетью", unit="pcs"))
    repo.add_history_row("2026-07-06", repo.get_store("dixy").id, kept, "Товар с сетью", 1, 50.0, 50.0, "d1")
    repo.mark_receipt_imported("d1", "2026-07-06", MAGNIT, 50.0, 1, "lkdr")
    assert {r["receipt_key"] for r in baskets.orderable_history()} == {"d1"}

    assert chains.settle() == {"magnit": 1, "perekrestok": 1}
    after = stores_in_history()
    assert after == {"m1": "magnit", "c1": "perekrestok", "p1": None, "x1": None, "t1": None, "k1": None,
                     "d1": "dixy"}, "строка со своей сетью не переписывается"

    assert chains.settle() == {}, "второй проход ничего не меняет"
    assert stores_in_history() == after

    # доставку X5 решает адрес отправителя, спрошенный у кабинета заново
    senders = {"p1": FIVE, "x1": CROSS, "t1": FIVE}
    assert chains.settle(senders) == {"pyaterochka": 1, "perekrestok": 1}
    assert chains.settle(senders) == {}
    assert stores_in_history() == {**after, "p1": "pyaterochka", "x1": "perekrestok"}
    assert {r["receipt_key"] for r in baskets.orderable_history()} == {"m1", "c1", "p1", "x1", "d1"}

    # METRO вне orders.chains: с сетью его покупки пропали бы из «Истории заказов». Войдёт сеть
    # в список — тот же проход проставит её и этому чеку.
    monkeypatch.setattr(chains, "orderable", lambda: {*chains.DEFAULT_ORDERABLE, "metro"})
    assert chains.settle(senders) == {"metro": 1}
    assert stores_in_history()["k1"] == "metro"
    assert {r["receipt_key"] for r in baskets.orderable_history()} == {"m1", "c1", "p1", "x1", "d1", "k1"}


def test_cabinet_tells_who_sent_a_loaded_receipt(db):
    """Адреса отправителя в базе нет: проход спрашивает его у кабинета по ключу чека."""
    cabinet = Cabinet(CHEQUES, token="acc-2")
    assert sync.senders(["p1", "x1"], cabinet=cabinet) == {}, "кабинет не подключён"

    repo.set_setting(sync.K_TOKEN, "acc-1")
    assert sync.senders(["p1", "x1"], cabinet=cabinet) == {}
    assert repo.get_setting(sync.K_TOKEN) == "acc-1", "ключ обновить нечем: настройки не тронуты"

    repo.set_setting(sync.K_REFRESH, "ref-1")
    assert sync.senders(["p1", "x1"], cabinet=cabinet) == {"p1": FIVE, "x1": CROSS}
    assert repo.get_setting(sync.K_TOKEN) == "acc-2", "протухший ключ обновлён, как при загрузке"


def test_cabinet_trouble_leaves_the_rest_as_it_was(db):
    """Кабинет подвёл: отдаём, что успели, второй раз ключ не обновляем и кабинет не отключаем."""
    repo.set_setting(sync.K_TOKEN, "acc-1")
    repo.set_setting(sync.K_REFRESH, "ref-1")

    assert sync.senders(["p1", "x1"], cabinet=Cabinet(CHEQUES, gone=("x1",))) == {"p1": FIVE}

    def down(refresh_token):
        raise CabinetError("нет связи")

    cabinet = Cabinet(CHEQUES, token="acc-2")
    cabinet.refresh = down
    assert sync.senders(["p1", "x1"], cabinet=cabinet) == {}
    assert (repo.get_setting(sync.K_TOKEN), repo.get_setting(sync.K_REFRESH)) == ("acc-1", "ref-1")

    cabinet = Cabinet(CHEQUES, token="acc-2", fresh="acc-3")     # не узнаёт и обновлённый ключ
    assert sync.senders(["p1", "x1"], cabinet=cabinet) == {}
    assert cabinet.refreshed == 1


def test_settled_receipt_is_seen_as_the_same_purchase(db):
    """Чек с сетью узнаёт свой заказ из кабинета сети: вторая запись покупки не появится."""
    loaded("m1", MAGNIT, day="2026-07-06", total=1820.51)
    magnit = repo.get_store("magnit").id

    assert not bundle.same_purchase(magnit, "2026-07-06", 1820.51, orders=False)
    chains.settle()
    assert bundle.same_purchase(magnit, "2026-07-06", 1820.51, orders=False)


@pytest.fixture
def places(tmp_path, monkeypatch):
    """Рабочие места в своей папке; заказы — в сетях по умолчанию, как у фикстуры db."""
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(chains, "orderable", lambda: set(chains.DEFAULT_ORDERABLE))
    yield
    users.deactivate()


def test_cli_walks_every_workspace(places, capsys):
    for phone in ("79990000001", "79990000002"):
        users.open_workspace(phone)
        loaded("m1", MAGNIT)
        users.deactivate()

    assert chains.main(["--all"]) == 0
    assert capsys.readouterr().out.splitlines() == ["…0001: {'magnit': 1}", "…0002: {'magnit': 1}"]
    assert chains.main(["--all"]) == 0
    assert capsys.readouterr().out.splitlines() == ["…0001: проставлять нечего", "…0002: проставлять нечего"]


@pytest.mark.parametrize("key, code", [("p1", "pyaterochka"), ("x1", "perekrestok")])
def test_cli_asks_the_cabinet_about_shared_delivery(places, monkeypatch, capsys, key, code):
    monkeypatch.setattr(sync, "make_cabinet", lambda: Cabinet(CHEQUES))
    users.open_workspace("79990000001")
    loaded(key, X5)
    repo.set_setting(sync.K_TOKEN, "acc-1")
    users.deactivate()

    assert chains.main(["--phone", "79990000001"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"…0001: {{'{code}': 1}}"]


def test_cli_says_what_it_left_without_a_chain(places, capsys):
    """Кабинет не подключён: одно «нечего» скрыло бы доставку, о которой не спросили."""
    users.open_workspace("79990000001")
    loaded("p1", X5)
    users.deactivate()

    assert chains.main(["--all"]) == 0
    assert capsys.readouterr().out.splitlines() == ["…0001: проставлять нечего; общая доставка без сети: 1"]


def test_cli_does_not_make_a_workspace_out_of_a_typo(places):
    with pytest.raises(SystemExit):
        chains.main(["--phone", "79990000009"])
    assert users.list_workspaces() == []
