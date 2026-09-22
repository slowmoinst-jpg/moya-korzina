"""Сохранённый вход в сеть: куки и localStorage в рабочем месте человека.

ЧТО ЗДЕСЬ ЛЕЖИТ И ПОЧЕМУ ЭТО СЕРЬЁЗНО. storage_state сети — это её куки и её
localStorage, то есть РОВНО ТО, чем браузер человека доказывает магазину, что он
это он. Владея им, наш сервер может видеть его заказы и наполнять его корзину —
за него. Значит доступ к серверу равен доступу к шести кабинетам.

РЕШЕНИЕ ВЛАДЕЛЬЦА 17.09.2026: хранить, как хранится ключ кабинета ФНС. Оно принято
знающим — вопрос был задан прямо, со сказанной ценой, — и оно же единственное,
при котором корзина уезжает в магазин БЕЗ участия человека: ключ, живущий только
на время сеанса, требовал бы входить заново перед каждой передачей.

ЧЕГО ЗДЕСЬ НЕТ. Ни пароля, ни кода из СМС. Человек набирает их в странице самой
сети, показанной в нашем экране; до приложения они доходят нажатиями клавиш и
нигде не оседают. Хранится только то, что сеть выдала ПОСЛЕ входа.

ПОЧЕМУ В НАСТРОЙКАХ РАБОЧЕГО МЕСТА, А НЕ В ТАБЛИЦЕ. Это не история и не
справочник, а один снимок «чем мы сейчас представляемся магазину», целиком
заменяемый следующим входом, — ровно как купоны (app/collector.COUPONS_KEY) и
подключения (app/store_accounts.KEY). И живёт он в базе ЧЕЛОВЕКА: общая база из
config.yaml здесь была бы чужим кабинетом в общем доступе.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from app import repo

log = logging.getLogger(__name__)

KEY = "shop_sessions"

# Больше этого storage_state не бывает у честной сети: миллион знаков — признак
# того, что нам подсунули не состояние входа, а чью-то выгрузку.
MAX_STATE_CHARS = 1_000_000


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _all() -> dict[str, dict]:
    raw = repo.get_setting(KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("сохранённые входы в магазины не прочитались — начинаю с пустого")
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, dict]) -> None:
    repo.set_setting(KEY, json.dumps(data, ensure_ascii=False))


def save(chain: str, state: dict, *, account: str | None = None,
         points: float | None = None) -> None:
    """Запомнить вход в сеть. Зовётся из потока запроса, а не из потока браузера."""
    if not isinstance(state, dict):
        raise ValueError("состояние входа должно быть объектом")
    body = json.dumps(state, ensure_ascii=False)
    if len(body) > MAX_STATE_CHARS:
        raise ValueError("состояние входа неправдоподобно велико — не сохраняю")
    data = _all()
    was = data.get(chain) or {}
    data[chain] = {
        "state": state,
        "saved_at": _now(),
        "first_at": was.get("first_at") or _now(),
        "account": account or was.get("account"),
        "points": points if points is not None else was.get("points"),
    }
    _write(data)
    log.info("%s: вход сохранён в рабочем месте", chain)


def load(chain: str) -> dict | None:
    """Состояние входа для подстановки в браузер. Нет — None, а не пустой словарь."""
    got = _all().get(chain) or {}
    state = got.get("state")
    return state if isinstance(state, dict) and state else None


def about(chain: str) -> dict | None:
    """Что известно про сохранённый вход, БЕЗ самого состояния.

    Экрану нужно «когда сохранён» и «как подписан», а куки ему не нужны ни на
    что: показывать их негде, а таскать их в шаблон значило бы однажды их там
    случайно и напечатать.
    """
    got = _all().get(chain)
    if not isinstance(got, dict) or not got.get("state"):
        return None
    return {"saved_at": got.get("saved_at"), "first_at": got.get("first_at"),
            "account": got.get("account"), "points": got.get("points"),
            "cookies": len((got.get("state") or {}).get("cookies") or [])}


def forget(chain: str) -> None:
    """Забыть вход у нас.

    Это НЕ выход из аккаунта в магазине: сеть по-прежнему считает то устройство
    вошедшим, пока человек сам не выйдет. Разница проговаривается на экране.
    """
    data = _all()
    if data.pop(chain, None) is not None:
        _write(data)
        log.info("%s: сохранённый вход забыт", chain)


def saved_stores() -> list[str]:
    """Сети, вход в которые сохранён."""
    return sorted(code for code, got in _all().items()
                  if isinstance(got, dict) and got.get("state"))


def any_saved(chain: str) -> dict | None:
    """Сохранённый вход в сеть ИЗ ЛЮБОГО рабочего места, а не только текущего.

    ЗАЧЕМ ЭТО ОТДЕЛЬНО ОТ load(). Всё, что выше, читает базу ЧЕЛОВЕКА, который
    пришёл с запросом, — так и должно быть, кабинет чужим не бывает. Но обход
    каталога (app/catalog) работает БЕЗ человека: он живёт в korzina-jobs и
    запускается по часам. А пройденная проверка сети — это не личный кабинет, а
    пропуск на витрину: он открывает ОБЩИЙ каталог с ценами, одинаковый для всех.

    Поэтому обход берёт первый попавшийся сохранённый пропуск и ходит с ним за
    ценами, которые всё равно лягут в общий каталог. Ходить за ними в чужой
    личный кабинет — заказы, карта, история — этой дорогой нельзя: сюда приходят
    только сборщики каталога, и берут они из состояния ровно куки.

    Порядок тот же, что у app/places.addresses: живые рабочие места раньше демо,
    сломанная база одного не мешает остальным.
    """
    from app import config, users

    keep = config.db_override()
    try:
        names = [p for p in users.list_workspaces() if p != users.DEMO]
        names += [p for p in users.list_workspaces() if p == users.DEMO]
        for phone in names:
            try:
                config.set_db_override(users.db_path_for(phone))
                state = load(chain)
            except Exception as exc:  # noqa: BLE001 — чужая база не наша забота
                log.warning("вход %s в рабочем месте %s не прочитан (%s)", chain, phone, exc)
                continue
            if state:
                return state
    finally:
        config.set_db_override(keep)
    return None


__all__ = ["KEY", "save", "load", "about", "forget", "saved_stores"]
