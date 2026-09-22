"""Кабинет ФНС с сервера: код из СМС → ключи → чеки в истории, и ничего дважды.

Кабинет здесь поддельный: свой HTTP-сервер, отвечающий теми формами, что взяты из
кода настоящего (список {receipts, brands, hasMore}, позиции items[] в копейках,
вход по challengeToken и коду, обновление ключа по refresh-токену, 401 без Bearer).
Живой кабинет отсюда не проверялся — нет учётной записи; всё, что проверено здесь,
это наша сторона разговора.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import config, repo
from app.db import execute, init_db, query
from app.fns import sync
from app.fns.client import Cabinet, CabinetError, Unauthorized, format_phone

GOOD_CODE = "123456"


class FakeCabinet:
    """Состояние поддельного кабинета: какие ключи живы, сколько чеков, что спросили."""

    def __init__(self, receipts: int = 3) -> None:
        self.valid_tokens = {"acc-1"}
        self.refresh_tokens = {"ref-1": ("acc-2", "ref-2")}
        self.requests: list[tuple[str, dict, str | None]] = []
        self.no_data: set[str] = set()         # чеки, чей состав кабинет уже не хранит
        self.expired_tokens: set[str] = set()  # ключи, на которые кабинет отвечает «истёк»
        self.hidden: set[str] = set()          # чеки, не попавшие в выдачу списка
        self.receipts = [
            {"key": f"k{i}", "createdDate": f"2026-08-{1 + i % 28:02d}T19:41:00",
             "brandId": 7, "retailPlace": f"Торговая точка {i}", "totalSum": 10990 * (1 + i % 3)}
            for i in range(1, receipts + 1)
        ]

    def fiscal(self, key: str) -> dict:
        row = next(r for r in self.receipts if r["key"] == key)
        return {"key": key, "dateTime": row["createdDate"], "retailPlace": row["retailPlace"],
                "totalSum": row["totalSum"],
                "items": [{"name": f"Молоко 3.2% 900 мл ({key})", "quantity": 1,
                           "price": row["totalSum"], "sum": row["totalSum"], "nds": 1}]}


def _handler(state: FakeCabinet):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # тишина в выводе тестов
            pass

        def _send(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            auth = self.headers.get("Authorization")
            state.requests.append((self.path, body, auth))

            if self.path == "/api/v2/auth/challenge/phone/start":
                if body.get("phone") == "79990009999":
                    return self._send(400, {"code": "empty.captcha", "message": None,
                                            "additionalInfo": {}})
                if body.get("phone") == "70000000000":
                    return self._send(400, {"error": {
                        "code": "registration.sms.verification.not.expired",
                        "message": "Код уже отправлен"}})
                assert body["deviceInfo"]["sourceType"] == "WEB"
                return self._send(200, {"challengeToken": "ch-1", "sentTo": "+7 *** ***-45-67",
                                        "sendCodeVerifyStatus": "SENT",
                                        "challengeTokenExpiresInSec": 120})
            if self.path == "/api/v1/auth/challenge/phone/verify":
                if body.get("challengeToken") == "ch-1" and body.get("code") == GOOD_CODE:
                    return self._send(200, {"token": "acc-1", "refreshToken": "ref-1"})
                return self._send(400, {"error": {"code": "code.invalid",
                                                  "message": "Неверный код подтверждения"}})
            if self.path == "/api/v1/auth/token":
                pair = state.refresh_tokens.get(body.get("refreshToken"))
                if not pair:
                    return self._send(401, {"error": {"code": "token.expired",
                                                      "message": "Ключ истёк"}})
                state.valid_tokens.add(pair[0])
                return self._send(200, {"token": pair[0], "refreshToken": pair[1]})

            token = (auth or "").replace("Bearer ", "")
            # Живой кабинет на просроченный ключ отвечает НЕ 401, а обычной ошибкой
            # с текстом — проверено 16.09.2026
            if token in state.expired_tokens:
                return self._send(400, {"code": "token.expired",
                                        "message": "Срок действия токена доступа истек"})
            if token not in state.valid_tokens:
                return self._send(401, {"error": {"code": "unauthorized", "message": "Нет входа"}})
            if self.path == "/api/v1/receipt":
                offset, limit = int(body.get("offset", 0)), int(body.get("limit", 100))
                # hidden — чек в кабинете есть, а в выдачу списка не попал: так ведёт
                # себя живая лента, пока её читаешь постранично
                lenta = [r for r in state.receipts if r["key"] not in state.hidden]
                page = lenta[offset:offset + limit]
                return self._send(200, {"receipts": page, "brands": [{"id": 7, "name": "Пятёрочка"}],
                                        "hasMore": offset + limit < len(lenta)})
            if self.path == "/api/v1/receipt/fiscal_data":
                # Так живой кабинет отвечает на чеки, чей срок хранения вышел
                if body["key"] in state.no_data:
                    return self._send(422, {"code": "receipt.fiscal.data.unavailable",
                                            "message": None, "additionalInfo": {}})
                return self._send(200, state.fiscal(body["key"]))
            return self._send(404, {"error": {"code": "not.found", "message": self.path}})

    return Handler


@pytest.fixture
def cabinet_server():
    state = FakeCabinet()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state
    server.shutdown()
    server.server_close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "fns.db"))
    init_db()


def _client(url: str) -> Cabinet:
    return Cabinet(base_url=url, device_id="dev-1", pause=0.0)


def test_code_flow_stores_keys_and_loads_every_receipt(cabinet_server, db):
    url, state = cabinet_server
    cab = _client(url)

    info = sync.request_code("+7 999 123-45-67", cabinet=cab)
    assert info["challenge_token"] == "ch-1" and info["sent_to"].endswith("45-67")
    assert state.requests[-1][1]["phone"] == "79991234567"

    sync.confirm_code(info["challenge_token"], "79991234567", "123 456", cabinet=cab)
    assert sync.is_connected()
    assert repo.get_setting("fns.token") == "acc-1"
    assert repo.get_setting("fns.refresh_token") == "ref-1"
    assert state.requests[-1][1]["code"] == GOOD_CODE          # код ушёл цифрами

    steps: list[tuple] = []
    result = sync.sync(on_step=lambda *a: steps.append(a), cabinet=cab)
    assert result["receipts"] == 3 and result["rows"] == 3 and result["pending"] == 0
    assert result["seen"] == 3
    assert query("SELECT COUNT(*) AS n FROM purchase_history")[0]["n"] == 3
    assert ("fiscal", 3, 3) in steps
    assert sync.status()["last_result"]["receipts"] == 3

    # второй заход: опись читается, позиции не спрашиваются, история не удваивается
    before = len(state.requests)
    again = sync.sync(cabinet=cab)
    assert again["receipts"] == 0 and again["skipped"] == 0 and again["seen"] == 3
    paths = [p for p, _, _ in state.requests[before:]]
    assert paths == ["/api/v1/receipt"]
    assert query("SELECT COUNT(*) AS n FROM purchase_history")[0]["n"] == 3
    assert "Новых чеков нет" in sync.headline(again)
    assert "Загружено 3 чека" in sync.headline(result)


def test_wrong_code_is_reported_in_the_cabinets_words(cabinet_server, db):
    url, _ = cabinet_server
    with pytest.raises(CabinetError) as caught:
        sync.confirm_code("ch-1", "79991234567", "000000", cabinet=_client(url))
    assert "Неверный код" in str(caught.value)
    assert not sync.is_connected()


def test_code_already_sent_is_explained(cabinet_server, db):
    url, _ = cabinet_server
    with pytest.raises(CabinetError) as caught:
        sync.request_code("70000000000", cabinet=_client(url))
    assert "уже отправлен" in str(caught.value)


def test_expired_key_is_refreshed_without_asking_a_new_code(cabinet_server, db):
    url, state = cabinet_server
    repo.set_setting("fns.token", "dead")
    repo.set_setting("fns.refresh_token", "ref-1")
    result = sync.sync(cabinet=_client(url))
    assert result["receipts"] == 3
    assert repo.get_setting("fns.token") == "acc-2"
    assert repo.get_setting("fns.refresh_token") == "ref-2"
    assert "/api/v1/auth/token" in [p for p, _, _ in state.requests]


def test_dead_refresh_disconnects_and_asks_to_connect_again(cabinet_server, db):
    url, _ = cabinet_server
    repo.set_setting("fns.token", "dead")
    repo.set_setting("fns.refresh_token", "bad")
    with pytest.raises(CabinetError) as caught:
        sync.sync(cabinet=_client(url))
    assert "подключите его заново" in str(caught.value)
    assert not sync.is_connected()


def test_receipts_are_written_in_batches_so_a_break_keeps_them(cabinet_server, db, monkeypatch):
    """Обрыв на середине не должен стирать уже загруженное.

    Это главная причина, по которой загрузка пишет пачками: на 1632 чеках живого
    кабинета обрыв случился дважды, и каждый раз работа начиналась с нуля. Ломаем
    кабинет на чеке из второй пачки и проверяем, что первая уже в истории.

    Ломаем именно по ключу, а не «на третьем вызове»: чеки в пачке спрашиваются
    одновременно, и порядок вызовов не определён — счётчик сделал бы тест шатким.
    """
    url, state = cabinet_server
    cabinet = _client(url)
    repo.set_setting("fns.token", "acc-1")

    real = cabinet.fiscal_data

    def breaks_on_k3(token, key):
        if key == "k3":
            raise Unauthorized("кабинет отвалился")
        return real(token, key)

    monkeypatch.setattr(cabinet, "fiscal_data", breaks_on_k3)
    repo.set_setting("fns.refresh_token", "")   # обновить ключ нечем — загрузка оборвётся
    with pytest.raises(CabinetError):
        sync.sync(cabinet=cabinet, batch=2)

    assert len(repo.imported_receipt_keys()) == 2, "первая пачка обязана пережить обрыв"
    assert repo.pending_receipts(), "опись должна быть записана до позиций"


def test_catalogue_walks_every_page(cabinet_server):
    url, state = cabinet_server
    state.receipts = [{"key": f"p{i}", "createdDate": "2026-09-01T10:00:00", "brandId": 7,
                       "retailPlace": "ТТ", "totalSum": 100} for i in range(150)]
    entries = _client(url).catalogue("acc-1")
    assert len(entries) == 150
    assert entries[0]["store"] == "Пятёрочка"       # магазин взят из brands по brandId
    assert [b.get("offset") for p, b, _ in state.requests if p == "/api/v1/receipt"] == [0, 100]


def test_missing_bearer_is_unauthorized(cabinet_server):
    url, _ = cabinet_server
    with pytest.raises(Unauthorized):
        _client(url).list_page("nope", 0)


def test_phone_styles():
    assert format_phone("+7 (999) 123-45-67") == "79991234567"
    assert format_phone("79991234567", "plus") == "+79991234567"
    assert format_phone("79991234567", "masked") == "+7 (999) 123-45-67"


def test_captcha_on_code_request_is_named(cabinet_server, db):
    from app.fns.client import CaptchaRequired

    url, _ = cabinet_server
    with pytest.raises(CaptchaRequired) as caught:
        sync.request_code("79990009999", cabinet=_client(url))
    assert "капчу" in str(caught.value)


def test_keys_from_the_bookmarklet_connect_the_cabinet(cabinet_server, db):
    url, state = cabinet_server
    text = json.dumps({"source": "lkdr-keys", "token": "acc-1", "refresh": "ref-1",
                       "deviceId": "browser-device-7"})
    info = sync.connect_with_keys(text, phone="79991234567", cabinet=_client(url))
    assert info["seen"] == 1 and info["has_more"]          # проверено первой страницей описи
    assert sync.is_connected()
    assert repo.get_setting("fns.device_id") == "browser-device-7"
    assert sync.sync(cabinet=_client(url))["receipts"] == 3


def test_dead_key_from_the_bookmarklet_is_refreshed_or_refused(cabinet_server, db):
    url, _ = cabinet_server
    # мёртвый ключ с живым refresh — обновляется молча
    sync.connect_with_keys(json.dumps({"token": "x.y.dead", "refresh": "ref-1"}), cabinet=_client(url))
    assert repo.get_setting("fns.token") == "acc-2"
    # мёртвый ключ без refresh — честный отказ, ничего не сохранено
    sync.disconnect()
    with pytest.raises(CabinetError) as caught:
        sync.connect_with_keys("x.y.dead", cabinet=_client(url))
    assert "истёк" in str(caught.value)
    assert not sync.is_connected()


def test_garbage_key_is_refused():
    with pytest.raises(CabinetError):
        sync.parse_keys("просто текст")
    with pytest.raises(CabinetError):
        sync.parse_keys("{не json")
    assert sync.parse_keys("a.b.c")["token"] == "a.b.c"


# ---------- ключ из закладки прямо в адрес приложения ----------
def test_key_packed_by_the_bookmarklet_unpacks_back():
    """unpack_keys понимает ровно то, что пакует pack() в docs/grab.src.js.

    Там base64 с заменой «+/» на «-_» и без хвостовых «=» — иначе ключ не пережил бы
    поездку в адресной строке. Проверяем на тексте с кириллицей: pack() гоняет строку
    через UTF-8, и если бы мы читали иначе, ключ приезжал бы битым.
    """
    import base64

    original = json.dumps({"source": "lkdr-keys", "token": "a.b.c", "refresh": "ключ-обновления"},
                          ensure_ascii=False)
    packed = (base64.b64encode(original.encode("utf-8")).decode("ascii")
              .replace("+", "-").replace("/", "_").rstrip("="))
    assert sync.unpack_keys(packed) == original
    assert sync.parse_keys(sync.unpack_keys(packed))["refresh"] == "ключ-обновления"


def test_broken_key_from_the_address_is_refused_by_name():
    with pytest.raises(CabinetError) as caught:
        sync.unpack_keys("не-база64-!!!")
    assert "адрес" in str(caught.value).lower()


def test_bookmarklet_carries_a_slot_for_the_app_address():
    """В собранной закладке должна остаться заглушка — иначе подставлять некуда."""
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "docs", "grab.min.txt"), encoding="utf-8") as fh:
        built = fh.read()
    assert "__APP_URL__" in built, "закладку пересобрать: python tools/build_bookmarklet.py"


def test_app_address_is_put_into_the_bookmarklet_encoded(tmp_path, monkeypatch):
    """Адрес уезжает в закладку ЗАКОДИРОВАННЫМ: она сама — javascript:-ссылка.

    Незакодированный адрес разорвал бы её на первом же двоеточии, и закладка
    молча перестала бы работать: браузер не жалуется, он просто ничего не делает.
    Проверка переехала с app/ui/screens/fns.py на app/web/screens/receipts.py
    вместе с интерфейсом (17.09.2026).
    """
    from app.web.screens import receipts

    # BOOKMARKLET считается на импорте от config.ROOT, поэтому подменяем его сам,
    # а не корень: подмена корня до него уже не доходит.
    fake = tmp_path / "grab.min.txt"
    fake.write_text('javascript:var%20APP%20%3D%20%22__APP_URL__%22%3B', encoding="utf-8")
    monkeypatch.setattr(receipts, "BOOKMARKLET", str(fake))

    from app.web import create_app
    with create_app().test_request_context("/receipts", base_url="http://203.0.113.7/"):
        out = receipts._bookmarklet()

    assert out.startswith("javascript:")
    assert "__APP_URL__" not in out, "заглушка обязана быть заменена"
    assert "%3A%2F%2F" in out, "адрес обязан уехать закодированным, иначе ссылка порвётся"


# ---------- чеки, состав которых ФНС уже не хранит ----------
def test_receipt_without_fiscal_data_is_marked_and_never_asked_again(cabinet_server, db):
    """Живой кабинет на чеки 2018 года отвечает 422 receipt.fiscal.data.unavailable.

    Такой чек не «ещё не загружен», а «загружен не будет». Он обязан уйти из очереди:
    иначе каждая проверка заново спрашивает сотню чеков ради того же отказа, счётчик
    «не загружено» замирает навсегда, а человеку обещают, что вот-вот догрузится.
    """
    url, state = cabinet_server
    state.no_data.add("k2")
    repo.set_setting("fns.token", "acc-1")

    first = sync.sync(cabinet=_client(url))
    assert first["receipts"] == 2, "два чека с составом должны загрузиться"
    assert first["no_data"] == 1
    assert first["gone_now"] == 1
    assert not first["failed"], "это не ошибка разбора, а отсутствие данных у источника"
    assert first["pending"] == 0, "чек без состава не висит в очереди"

    # второй заход не должен спрашивать его снова
    state.requests.clear()
    second = sync.sync(cabinet=_client(url))
    asked = [body.get("key") for path, body, _ in state.requests
             if path == "/api/v1/receipt/fiscal_data"]
    assert "k2" not in asked, "чек без состава спрошен повторно"
    assert second["no_data"] == 1
    assert "больше не хранит состав" in sync.headline(second)


def test_headline_does_not_claim_everything_is_loaded_while_something_waits():
    assert "осталось взять 5" in sync.headline({"receipts": 0, "seen": 100, "pending": 5})
    assert "все 100 чеков разобраны" in sync.headline({"receipts": 0, "seen": 100, "pending": 0})


def test_expired_key_is_recognised_by_words_not_only_by_401(cabinet_server, db):
    """«Срок действия токена доступа истек» приходит не с 401, и это ловушка.

    Живой кабинет 16.09.2026 оборвал загрузку именно так: ключ протух, ответ пришёл
    обычной ошибкой, ветка обновления по refresh-токену не сработала, и человек
    увидел отказ вместо чеков. Теперь истёкший ключ узнаётся по смыслу ответа.
    """
    url, state = cabinet_server
    state.expired_tokens.add("acc-1")
    repo.set_setting("fns.token", "acc-1")
    repo.set_setting("fns.refresh_token", "ref-1")

    result = sync.sync(cabinet=_client(url))

    assert result["receipts"] == 3, "ключ должен был обновиться и загрузка продолжиться"
    assert repo.get_setting("fns.token") == "acc-2", "новый ключ обязан сохраниться"
    assert sync.is_connected()


def test_receipt_missing_from_this_walk_is_still_asked_by_its_key(cabinet_server, db):
    """Чек, записанный невзятым раньше, но не показанный в этот заход, должен браться.

    Опись читается постранично, и лента в кабинете за это время живёт: часть чеков в
    выдачу не попадает. Если брать только показанное, счётчик «осталось взять» замрёт
    навсегда — 16.09.2026 так повисли 33 чека, и каждый заход кончался мгновенно.
    """
    url, state = cabinet_server
    repo.set_setting("fns.token", "acc-1")

    # чек k3 известен нам как невзятый, а кабинет его сейчас не показывает
    repo.note_receipt("k3", "2026-08-03", "Пятёрочка", 329.7)
    state.hidden.add("k3")

    result = sync.sync(cabinet=_client(url))

    assert result["seen"] == 2, "кабинет показал только два чека"
    assert result["receipts"] == 3, "третий обязан быть взят по своему ключу"
    assert result["pending"] == 0


# ---------- одновременный обход ----------
def test_many_receipts_are_asked_at_once_and_land_on_their_own_entries(cabinet_server, db):
    """Пачка спрашивается одновременно, но исход каждого чека ложится к своему чеку.

    Кабинет отдаёт позиции только по одному ключу за запрос, bulk у него нет, поэтому
    ускориться можно единственным способом — спрашивать несколько чеков сразу. Ошибка
    тут стоила бы дорого и молча: перепутанные ответы разложили бы чужие позиции по
    чужим чекам. Поэтому проверяем не скорость, а соответствие ключу.
    """
    url, state = cabinet_server
    state.receipts = [
        {"key": f"k{i}", "createdDate": f"2026-08-{i:02d}T10:00:00", "brandId": 7,
         "retailPlace": f"Точка {i}", "totalSum": 1000 * i}
        for i in range(1, 13)
    ]
    state.no_data.add("k5")
    repo.set_setting("fns.token", "acc-1")

    result = sync.sync(cabinet=Cabinet(base_url=url, device_id="dev-1", pause=0.0, workers=4))

    assert result["receipts"] == 11 and result["no_data"] == 1
    # у каждого чека своя сумма, и она обязана совпасть со своим же ключом
    rows = query("SELECT receipt_key, total FROM purchase_history ORDER BY receipt_key")
    got = {r["receipt_key"]: r["total"] for r in rows}
    assert "k5" not in got
    for i in list(range(1, 5)) + list(range(6, 13)):
        assert got[f"k{i}"] == round(1000 * i / 100, 2), f"перепутан ответ по чеку k{i}"


def test_one_worker_gives_the_same_answer_as_five(cabinet_server, db):
    """Число потоков не должно менять результат — иначе ускорение куплено правдой."""
    url, state = cabinet_server
    state.no_data.add("k2")
    repo.set_setting("fns.token", "acc-1")

    alone = sync.sync(cabinet=Cabinet(base_url=url, device_id="dev-1", pause=0.0, workers=1))
    keys_alone = repo.imported_receipt_keys()

    repo.set_setting("fns.token", "acc-1")
    for key in list(keys_alone) + ["k2"]:
        execute("DELETE FROM receipts WHERE key = ?", (key,))
        execute("DELETE FROM purchase_history WHERE receipt_key = ?", (key,))
    together = sync.sync(cabinet=Cabinet(base_url=url, device_id="dev-1", pause=0.0, workers=5))

    assert together["receipts"] == alone["receipts"]
    assert together["no_data"] == alone["no_data"] == 1
    assert repo.imported_receipt_keys() == keys_alone
