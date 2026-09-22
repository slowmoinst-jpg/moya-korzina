"""Клиент кабинета «Мои чеки онлайн» (lkdr.nalog.ru), работающий с нашего сервера.

Откуда взяты адреса. Кабинет — обычное приложение на JSON, и его собственный код
(lkdr.nalog.ru/static/js/main.*.chunk.js) отдаётся любому браузеру. Оттуда, 16.09.2026:

    POST /api/v2/auth/challenge/phone/start   — отправить код на телефон
    POST /api/v1/auth/challenge/phone/verify  — {challengeToken, phone, code, deviceInfo}
    POST /api/v1/auth/token                   — обновить ключ по refresh-токену
    POST /api/v1/receipt                      — {limit, offset, orderBy} → {receipts, brands, hasMore}
    POST /api/v1/receipt/fiscal_data          — {key} → позиции чека

    deviceInfo = {sourceDeviceId, sourceType: "WEB", appVersion: "1.0.0",
                  metaDetails: {userAgent}}
    ответ на start: {challengeToken, sentTo, sendCodeVerifyStatus, challengeTokenExpiresInSec}
    код ошибки «registration.sms.verification.not.expired» — код уже отправлен, подождите.

Список и позиции повторяют закладку docs/grab.src.js один в один: страница по сто
чеков, пауза 160 мс между запросами, три страховки от бесконечного листания.

ЧЕГО НЕ ПРОВЕРЕНО. Живого входа отсюда не было: ни учётной записи, ни номера, на
который можно отправить СМС. Поэтому тело запроса на отправку кода и имена полей с
ключами в ответе разбираются терпимо — несколько вариантов написания, — а всё, что
кабинет ответит не так, показывается человеку дословно: он единственный, кто увидит
настоящий ответ. Если кабинет потребует капчу, этот путь закроется, и останется
закладка — она делает те же запросы из браузера, где человек уже вошёл.

ГРАНИЦА, СДВИНУТАЯ решением владельца 16.09.2026. Прежде ключ доступа не покидал
браузер человека. Теперь ключ и refresh-токен лежат в базе рабочего места на нашем
сервере, чтобы чеки подтягивались без участия человека. Это удобство ценой доверия
к серверу, и код из СМС мы по-прежнему нигде не храним.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator

import requests

log = logging.getLogger(__name__)

BASE = "https://lkdr.nalog.ru"
START = "/api/v2/auth/challenge/phone/start"
VERIFY = "/api/v1/auth/challenge/phone/verify"
REFRESH = "/api/v1/auth/token"
LIST = "/api/v1/receipt"
FISCAL = "/api/v1/receipt/fiscal_data"

PAGE = 100
PAUSE = 0.16
MAX_PAGES = 300
WORKERS = 5      # сколько чеков спрашиваем одновременно, см. fiscal_many
CODE_ALREADY_SENT = "registration.sms.verification.not.expired"
NO_DATA_CODE = "receipt.fiscal.data.unavailable"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_TOKEN_NAMES = ("token", "accessToken", "access_token", "auth.token", "authToken", "jwt")
_REFRESH_NAMES = ("refreshToken", "refresh_token", "refresh.token")


class CabinetError(Exception):
    """Кабинет ответил не тем, чего ждали. Текст — для человека, дословно от кабинета."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


class Unauthorized(CabinetError):
    """Ключ не подошёл: истёк, отозван или его нет."""


class NoFiscalData(CabinetError):
    """Чек в ленте есть, а его состава у ФНС уже нет.

    Проверено живым кабинетом 16.09.2026: на чеки 2018 года ответ 422 с кодом
    `receipt.fiscal.data.unavailable`. Срок хранения содержимого вышел, и повторный
    заход ничего не изменит — такой чек нужно отметить и больше не спрашивать.
    """


class CaptchaRequired(CabinetError):
    """Кабинет требует капчу. Проверено живым номером 16.09.2026: на отправку кода
    кабинет отвечает {"code": "empty.captcha"}. С сервера её не пройти: эта ступень
    рассчитана на человека, и человек её проходит — владелец входит в свой кабинет сам,
    своим номером, а закладка передаёт приложению ключ доступа. Дальше всё идёт
    программно: маршрут рабочий, просто вход в нём человеческий.
    """


@dataclass
class Tokens:
    token: str
    refresh_token: str | None = None


def new_device_id() -> str:
    return str(uuid.uuid4())


def device_info(device_id: str, user_agent: str = USER_AGENT) -> dict:
    return {"sourceDeviceId": device_id, "sourceType": "WEB", "appVersion": "1.0.0",
            "metaDetails": {"userAgent": user_agent}}


def format_phone(phone: str, style: str = "digits") -> str:
    """Как записать номер для кабинета. Проверенного варианта нет — стиль настраивается.

    digits: 79991234567 · plus: +79991234567 · masked: +7 (999) 123-45-67
    """
    digits = re.sub(r"\D", "", phone or "")
    if style == "plus":
        return "+" + digits
    if style == "masked" and len(digits) == 11:
        return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:]}"
    return digits


def _pick(payload: Any, names: tuple[str, ...], depth: int = 2) -> str | None:
    """Первое непустое строковое поле с одним из имён — на верхнем уровне или чуть глубже."""
    if not isinstance(payload, dict):
        return None
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if depth > 0:
        for value in payload.values():
            if isinstance(value, dict):
                found = _pick(value, names, depth - 1)
                if found:
                    return found
    return None


def _error_of(payload: Any, status: int) -> tuple[str, str | None]:
    """(текст, код) ошибки из ответа кабинета — какой бы формы он ни был."""
    if isinstance(payload, dict):
        err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
        code = err.get("code") if isinstance(err, dict) else None
        message = None
        if isinstance(err, dict):
            message = err.get("message") or err.get("description") or err.get("detail")
        if not message and isinstance(payload.get("error"), str):
            message = payload["error"]
        if message:
            return str(message), (str(code) if code else None)
        return json.dumps(payload, ensure_ascii=False)[:300], (str(code) if code else None)
    if isinstance(payload, str) and payload.strip():
        return payload.strip()[:300], None
    return f"кабинет ответил {status}", None


_EXPIRED_HINTS = ("token.expired", "token_expired", "срок действия токена",
                  "срок действия ключа", "token has expired")


def _expired(message: str, code: str | None) -> bool:
    """Кабинет говорит «ключ истёк» не только кодом 401.

    Живая проверка 16.09.2026: на просроченный ключ пришёл ответ с текстом «Срок
    действия токена доступа истек» и НЕ 401, поэтому загрузка оборвалась ошибкой
    вместо того, чтобы молча обновить ключ по refresh-токену. Отличать истёкший ключ
    по смыслу, а не только по коду ответа, и есть разница между «приложение
    подтянуло чеки само» и «приложение отвалилось до утра».
    """
    hay = f"{code or ''} {message or ''}".lower()
    return any(hint in hay for hint in _EXPIRED_HINTS)


class Cabinet:
    """Один кабинет — один человек. Ключи наружу не сохраняет, это дело вызывающего."""

    def __init__(self, base_url: str = BASE, device_id: str | None = None,
                 user_agent: str = USER_AGENT, timeout: float = 20.0, pause: float = PAUSE,
                 phone_style: str = "digits", session: requests.Session | None = None,
                 workers: int = WORKERS) -> None:
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id or new_device_id()
        self.user_agent = user_agent
        self.timeout = timeout
        self.pause = pause
        self.phone_style = phone_style
        self.workers = max(1, int(workers or 1))
        self._given_session = session
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        """Своя сессия на каждый поток.

        requests.Session переиспользует соединение — на полутора тысячах запросов это
        экономит рукопожатие TLS каждый раз, и отказываться от неё ради потоков было бы
        глупо. Но одну сессию на несколько потоков делить нельзя, она к этому не
        приспособлена. Поэтому сессия заводится на поток: и соединение живёт, и никто
        никому не мешает. Сессию, переданную снаружи, не трогаем — её дал вызывающий и
        он же отвечает за то, как ею пользуется.
        """
        if self._given_session is not None:
            return self._given_session
        own = getattr(self._local, "session", None)
        if own is None:
            own = requests.Session()
            self._local.session = own
        return own

    # ---------- низ: один запрос ----------
    def _post(self, path: str, body: dict, token: str | None = None) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": self.user_agent,
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        }
        if token:
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        try:
            response = self.session.post(self.base_url + path, json=body, headers=headers,
                                         timeout=self.timeout)
        except requests.RequestException as exc:
            raise CabinetError(f"кабинет не отвечает: {exc}") from exc
        try:
            payload = response.json() if response.content else None
        except ValueError:
            payload = response.text
        if response.status_code in (401, 403):
            message, code = _error_of(payload, response.status_code)
            raise Unauthorized(message, status=response.status_code, code=code, payload=payload)
        if response.status_code >= 400:
            message, code = _error_of(payload, response.status_code)
            if _expired(message, code):
                raise Unauthorized(message, status=response.status_code, code=code,
                                   payload=payload)
            if (code or "") == NO_DATA_CODE:
                raise NoFiscalData("ФНС больше не хранит состав этого чека",
                                   status=response.status_code, code=code, payload=payload)
            if "captcha" in (code or "").lower() or "captcha" in message.lower():
                raise CaptchaRequired("кабинет требует капчу на этом шаге", status=response.status_code,
                                      code=code, payload=payload)
            raise CabinetError(message, status=response.status_code, code=code, payload=payload)
        return payload

    # ---------- вход ----------
    def start_challenge(self, phone: str) -> dict:
        """Попросить кабинет отправить код. Возвращает challenge_token и куда ушёл код."""
        body = {"phone": format_phone(phone, self.phone_style),
                "deviceInfo": device_info(self.device_id, self.user_agent)}
        try:
            payload = self._post(START, body)
        except CabinetError as exc:
            if exc.code == CODE_ALREADY_SENT:
                raise CabinetError("Код уже отправлен на этот номер — подождите немного или "
                                   "введите тот, что пришёл.", status=exc.status, code=exc.code,
                                   payload=exc.payload) from exc
            raise
        challenge = _pick(payload, ("challengeToken", "challenge_token"))
        if not challenge:
            raise CabinetError("Кабинет не вернул ключ проверки — ответ: "
                               + json.dumps(payload, ensure_ascii=False)[:300], payload=payload)
        expires = None
        if isinstance(payload, dict):
            raw = payload.get("challengeTokenExpiresInSec")
            expires = int(raw) if isinstance(raw, (int, float)) else None
        return {"challenge_token": challenge,
                "sent_to": _pick(payload, ("sentTo", "sent_to")),
                "expires_in": expires, "raw": payload}

    def verify(self, challenge_token: str, phone: str, code: str) -> Tokens:
        body = {"challengeToken": challenge_token,
                "phone": format_phone(phone, self.phone_style),
                "code": re.sub(r"\D", "", code or ""),
                "deviceInfo": device_info(self.device_id, self.user_agent)}
        payload = self._post(VERIFY, body)
        return self._tokens_of(payload, "после проверки кода")

    def refresh(self, refresh_token: str) -> Tokens:
        body = {"refreshToken": refresh_token,
                "deviceInfo": device_info(self.device_id, self.user_agent)}
        payload = self._post(REFRESH, body)
        return self._tokens_of(payload, "при обновлении ключа")

    @staticmethod
    def _tokens_of(payload: Any, when: str) -> Tokens:
        token = _pick(payload, _TOKEN_NAMES)
        if not token:
            raise CabinetError(f"Кабинет не вернул ключ доступа {when} — ответ: "
                               + json.dumps(payload, ensure_ascii=False)[:300], payload=payload)
        return Tokens(token=token, refresh_token=_pick(payload, _REFRESH_NAMES))

    # ---------- чеки ----------
    def list_page(self, token: str, offset: int, limit: int = PAGE) -> dict:
        payload = self._post(LIST, {"limit": limit, "offset": offset,
                                    "orderBy": "CREATED_DATE:DESC"}, token=token)
        return payload if isinstance(payload, dict) else {}

    def fiscal_data(self, token: str, key: str) -> Any:
        return self._post(FISCAL, {"key": key}, token=token)

    def _outcome(self, token: str, key: str) -> tuple[str, Any]:
        """Один чек, но без исключений: они плохо переживают дорогу из чужого потока."""
        try:
            data = self.fiscal_data(token, key)
        except Unauthorized as exc:
            return "auth", str(exc)
        except NoFiscalData:
            return "gone", None
        except CabinetError as exc:
            return "error", str(exc)
        if self.pause:
            time.sleep(self.pause)
        return "ok", data

    def fiscal_many(self, token: str, keys: list[str],
                    workers: int | None = None) -> Iterator[tuple[str, str, Any]]:
        """Позиции по многим чекам. Отдаёт (ключ, исход, данные) по мере готовности.

        Пачкой кабинет спрашивать не умеет: в его собственном коде fiscal_data принимает
        ровно один ключ, никакого bulk рядом нет — проверено по main.*.chunk.js. Значит
        единственный способ ускориться — спрашивать несколько чеков одновременно. На
        полутора тысячах чеков разница не косметическая: по одному это около десяти
        минут, впятером — минуты три.

        Наглеть при этом незачем. Пауза остаётся, но теперь она у каждого потока своя,
        так что нагрузка на ФНС растёт во столько раз, сколько потоков, и не больше;
        пять — это несколько запросов в секунду, меньше, чем делает обычная страница с
        картинками.

        Исключения наружу из потоков не летят: каждый чек возвращает свой исход словом.
        Разбирать их обязан вызывающий в главном потоке — там же, где он пишет в базу и
        двигает полосу, потому что ни то, ни другое из чужого потока делать нельзя.
        """
        count = workers or self.workers
        if count <= 1 or len(keys) <= 1:
            for key in keys:
                kind, data = self._outcome(token, key)
                yield key, kind, data
            return
        with ThreadPoolExecutor(max_workers=min(count, len(keys))) as pool:
            for key, (kind, data) in zip(keys, pool.map(
                    lambda k: self._outcome(token, k), keys)):
                yield key, kind, data

    def catalogue(self, token: str, on_step: Callable[[int], None] | None = None) -> list[dict]:
        """Опись кабинета: все чеки без позиций. Ровно как в закладке."""
        out: list[dict] = []
        seen: set[str] = set()
        offset = 0
        page = 0
        while True:
            data = self.list_page(token, offset)
            got = data.get("receipts") or []
            brands = {b.get("id"): b.get("name") for b in (data.get("brands") or [])
                      if isinstance(b, dict)}
            fresh = 0
            for row in got:
                if not isinstance(row, dict) or not row.get("key") or row["key"] in seen:
                    continue
                seen.add(row["key"])
                fresh += 1
                out.append({
                    "key": row["key"],
                    "date": row.get("createdDate") or row.get("receiveDate") or row.get("buyDate"),
                    "store": row.get("brand") or brands.get(row.get("brandId")) or row.get("retailPlace") or "",
                    "total": row.get("totalSum"),
                })
            offset += len(got)
            page += 1
            if on_step:
                on_step(len(out))
            if not data.get("hasMore") or not fresh or page >= MAX_PAGES:
                return out
            time.sleep(self.pause)

    def bundle(self, token: str, known_keys: set[str] | None = None,
               on_step: Callable[[str, int, int], None] | None = None) -> dict:
        """Пакет в формате закладки: опись целиком, позиции — только по новым чекам.

        on_step(этап, сделано, всего): «list» пока идёт опись, «fiscal» пока берём позиции.
        """
        known = known_keys or set()
        entries = self.catalogue(token, on_step=(lambda n: on_step("list", n, n)) if on_step else None)
        need = [e for e in entries if e["key"] not in known]
        receipts: list[dict] = []
        failed: list[dict] = []
        for i, entry in enumerate(need, start=1):
            try:
                data = self.fiscal_data(token, entry["key"])
                receipts.append({**entry, "fiscalData": data})
            except Unauthorized:
                raise
            except CabinetError as exc:
                failed.append({"key": entry["key"], "reason": str(exc)})
            if on_step:
                on_step("fiscal", i, len(need))
            if i < len(need):
                time.sleep(self.pause)
        return {"source": "lkdr", "takenAt": datetime.now().isoformat(timespec="seconds"),
                "catalogue": entries, "receipts": receipts, "failed": failed}
