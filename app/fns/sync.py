"""Связка кабинета ФНС с рабочим местом: ключи в настройках, загрузка одной командой.

Как это выглядит для человека. Ввёл телефон при входе → на экране «Мои чеки»
нажал «Получить код» → вписал шесть цифр из СМС → всё. Дальше при каждом входе
приложение само сходит в кабинет, возьмёт новые чеки и скажет, сколько взяло.
Смотреть ему ничего не надо.

Что где лежит. В таблице settings рабочего места: fns.device_id (случайный
идентификатор «устройства», кабинет привязывает к нему ключи), fns.token и
fns.refresh_token, fns.phone, fns.connected_at, fns.last_sync_at, fns.last_result.
Ключ проверки (challengeToken) живёт только в сессии интерфейса, код из СМС —
нигде.

Границы честности. Кабинет живой не проверялся (см. app/fns/client.py), поэтому
любая его ошибка отдаётся человеку дословно, а закладка остаётся рядом как запасной
путь.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from app import config, repo
from app.fns.client import Cabinet, CabinetError, Unauthorized, new_device_id
from app.importers.bundle import import_bundle

K_DEVICE = "fns.device_id"
K_TOKEN = "fns.token"
K_REFRESH = "fns.refresh_token"
K_PHONE = "fns.phone"
K_CONNECTED = "fns.connected_at"
K_SYNCED = "fns.last_sync_at"
K_RESULT = "fns.last_result"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def make_cabinet() -> Cabinet:
    """Кабинет для этого рабочего места: с его же идентификатором устройства."""
    device = repo.get_setting(K_DEVICE)
    if not device:
        device = new_device_id()
        repo.set_setting(K_DEVICE, device)
    return Cabinet(
        base_url=config.get("fns.base_url") or "https://lkdr.nalog.ru",
        device_id=device,
        phone_style=str(config.get("fns.phone_style") or "digits"),
        timeout=float(config.get("fns.timeout_sec") or 20),
        workers=int(config.get("fns.workers") or 5),
    )


def is_connected() -> bool:
    return bool(repo.get_setting(K_TOKEN))


def status() -> dict:
    raw = repo.get_setting(K_RESULT)
    try:
        last = json.loads(raw) if raw else None
    except ValueError:
        last = None
    return {
        "connected": is_connected(),
        "phone": repo.get_setting(K_PHONE),
        "connected_at": repo.get_setting(K_CONNECTED),
        "last_sync_at": repo.get_setting(K_SYNCED),
        "last_result": last,
    }


def request_code(phone: str, cabinet: Cabinet | None = None) -> dict:
    """Попросить кабинет отправить код. Возвращает то, что нужно для второго шага."""
    cabinet = cabinet or make_cabinet()
    return cabinet.start_challenge(phone)


def confirm_code(challenge_token: str, phone: str, code: str,
                 cabinet: Cabinet | None = None) -> None:
    """Обменять код из СМС на ключи и сохранить их в рабочем месте."""
    cabinet = cabinet or make_cabinet()
    tokens = cabinet.verify(challenge_token, phone, code)
    repo.set_setting(K_TOKEN, tokens.token)
    repo.set_setting(K_REFRESH, tokens.refresh_token or "")
    repo.set_setting(K_PHONE, phone)
    repo.set_setting(K_CONNECTED, _now())


def parse_keys(text: str) -> dict:
    """Ключ из закладки: {"source": "lkdr-keys", "token": …, "refresh": …, "deviceId": …}.

    Терпим к форме: голый JWT тоже принимается (без refresh-токена ключ проживёт до
    истечения, потом понадобится новый). Возвращает {token, refresh, device_id}.
    """
    raw = (text or "").strip()
    if not raw:
        raise CabinetError("Вставьте ключ, который скопировала закладка в кабинете.")
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise CabinetError("Ключ не читается: это не тот текст, что копирует закладка.") from exc
        token = data.get("token") or data.get("accessToken") or data.get("auth.token")
        refresh = data.get("refresh") or data.get("refreshToken") or data.get("refresh.token")
        device = data.get("deviceId") or data.get("sourceDeviceId")
    else:
        # голая строка: принимаем только то, что похоже на пропуск кабинета (JWT из трёх
        # частей через точку), иначе сюда попадёт любой текст из буфера обмена
        if raw.count(".") < 2 or " " in raw:
            raise CabinetError("Это не ключ кабинета — скопируйте его закладкой ещё раз.")
        token, refresh, device = raw, None, None
    if not token or not str(token).strip():
        raise CabinetError("В ключе нет пропуска кабинета — скопируйте его закладкой ещё раз.")
    return {"token": str(token).strip(), "refresh": (str(refresh).strip() if refresh else None),
            "device_id": (str(device).strip() if device else None)}


def unpack_keys(packed: str) -> str:
    """Ключ из адреса приложения обратно в текст: base64url без хвоста «=», как пакует закладка."""
    import base64

    raw = (packed or "").strip().replace("-", "+").replace("_", "/")
    if not raw:
        raise CabinetError("В адресе нет ключа.")
    try:
        return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CabinetError("Ключ из адреса не читается — нажмите «Подключить к приложению» "
                           "в закладке ещё раз.") from exc


def connect_with_keys(text: str, phone: str | None = None, cabinet: Cabinet | None = None) -> dict:
    """Подключить кабинет ключом, который человек принёс из браузера закладкой.

    Зачем этот путь. На отправку кода кабинет требует капчу (empty.captcha, проверено
    16.09.2026), и с сервера её не пройти. Зато человек уже вошёл в кабинет сам — в
    своём браузере, со своей капчей и своим кодом. Закладка берёт оттуда ключ доступа
    и refresh-токен, а приложение дальше ходит по ним само; идентификатор устройства
    кабинета берём тот же, чтобы обновление ключа выглядело как его собственное.

    Ключ проверяется сразу первой страницей описи: мёртвый ключ не должен лечь в базу
    молча. Возвращает {"seen": сколько чеков в кабинете видно}.
    """
    keys = parse_keys(text)
    if keys["device_id"]:
        repo.set_setting(K_DEVICE, keys["device_id"])
    cabinet = cabinet or make_cabinet()
    token = keys["token"]
    try:
        page = cabinet.list_page(token, 0, 1)
    except Unauthorized:
        if not keys["refresh"]:
            raise CabinetError("Кабинет не узнал этот ключ — он истёк. Войдите в кабинет заново и "
                               "скопируйте ключ закладкой ещё раз.")
        tokens = cabinet.refresh(keys["refresh"])
        token = tokens.token
        keys["refresh"] = tokens.refresh_token or keys["refresh"]
        page = cabinet.list_page(token, 0, 1)
    repo.set_setting(K_TOKEN, token)
    repo.set_setting(K_REFRESH, keys["refresh"] or "")
    if phone:
        repo.set_setting(K_PHONE, phone)
    repo.set_setting(K_CONNECTED, _now())
    return {"seen": len(page.get("receipts") or []), "has_more": bool(page.get("hasMore"))}


def disconnect() -> None:
    """Забыть ключи. Чеки, уже загруженные в историю, остаются."""
    for key in (K_TOKEN, K_REFRESH, K_PHONE, K_CONNECTED, K_SYNCED, K_RESULT):
        repo.set_setting(key, None)


def _refreshed(cabinet: Cabinet) -> str | None:
    refresh = repo.get_setting(K_REFRESH)
    if not refresh:
        return None
    tokens = cabinet.refresh(refresh)
    repo.set_setting(K_TOKEN, tokens.token)
    if tokens.refresh_token:
        repo.set_setting(K_REFRESH, tokens.refresh_token)
    return tokens.token


def _renewed(cabinet: Cabinet) -> str:
    """Ключ истёк посреди работы: обновляем по refresh-токену или сдаёмся честно."""
    try:
        token = _refreshed(cabinet)
    except CabinetError:
        token = None
    if not token:
        disconnect()
        raise CabinetError("Кабинет перестал узнавать ключ — подключите его заново на "
                           "экране «Мои чеки».")
    return token


def _settle(entry: dict, kind: str, data: Any, taken: list[dict],
            failed: list[dict], gone: list[str]) -> None:
    """Разложить исход одного чека по местам. Только из главного потока: здесь пишут в базу.

    Три исхода нарочно разведены. Чек без состава (ФНС уже не хранит) — отмечаем и
    больше не спрашиваем никогда. Чек, не отдавшийся по другой причине, — в «не
    прочитались»: причина может быть временной, и в следующий заход он будет спрошен
    снова. Протухший ключ сюда не попадает вовсе, он не про чек.
    """
    if kind == "ok":
        taken.append({**entry, "fiscalData": data})
    elif kind == "gone":
        repo.mark_receipt_unavailable(entry["key"])
        gone.append(entry["key"])
    else:
        failed.append({"key": entry["key"], "reason": str(data)})


def _pack(catalogue: list[dict], receipts: list[dict], failed: list[dict]) -> str:
    return json.dumps({"source": "lkdr", "takenAt": _now(), "catalogue": catalogue,
                       "receipts": receipts, "failed": failed}, ensure_ascii=False)


def sync(on_step: Callable[[str, int, int], None] | None = None,
         cabinet: Cabinet | None = None, batch: int = 50) -> dict:
    """Сходить в кабинет, взять новые чеки, положить в историю. Возвращает отчёт загрузки.

    Пачка спрашивается У КАБИНЕТА СРАЗУ (cabinet.fiscal_many), а не по чеку за раз:
    bulk-запроса у кабинета нет, и единственный способ не ждать десять минут — держать
    несколько запросов в воздухе. Замерено на стенде с задержкой живого кабинета:
    1632 чека по одному — 10,5 минуты, впятером — 2,1. Разбирать ответы обязан ЭТОТ
    поток: в чужом нельзя ни писать в базу, ни двигать полосу.

    Пишем ПАЧКАМИ, а не одним куском в конце, и это уже не про скорость. Обрыв связи
    или перезапуск сервера за эти минуты случается запросто, а пока история писалась
    одним куском, каждый обрыв стирал всю работу и загрузка начиналась с нуля —
    16.09.2026 это случилось дважды подряд. Пачка в полсотни чеков обрыв переживает:
    что успели, то уже в истории, и следующий заход возьмёт только оставшееся.

    Опись пишется первой, до позиций, по той же причине: она и есть ответ «в кабинете
    столько-то, у вас столько-то», и терять её на обрыве обиднее всего.

    Ключ истёк — обновляем по refresh-токену и продолжаем с того же места; не вышло —
    отключаем кабинет и говорим, что нужен новый: молча жить с мёртвым ключом и каждый
    раз «ничего не находить» было бы обманом.
    """
    token = repo.get_setting(K_TOKEN)
    if not token:
        raise CabinetError("Кабинет не подключён — подключите его на экране «Мои чеки».")
    cabinet = cabinet or make_cabinet()
    known = repo.imported_receipt_keys()

    def catalogue(tok: str) -> list[dict]:
        return cabinet.catalogue(tok, on_step=(lambda n: on_step("list", n, n)) if on_step else None)

    try:
        entries = catalogue(token)
    except Unauthorized:
        token = _renewed(cabinet)
        entries = catalogue(token)

    result = import_bundle(_pack(entries, [], []))
    # Чеки, по которым ФНС уже сказала «состава нет», из очереди исключены: иначе
    # каждая проверка заново спрашивала бы сто с лишним чеков 2018 года ради того же
    # отказа — и минуту работы, и лишний стук в ФНС.
    skip = known | repo.unavailable_receipt_keys()
    need = [e for e in entries if e["key"] not in skip]
    # В очередь идёт не только то, что кабинет показал сейчас, но и то, что у нас уже
    # записано невзятым. Опись читается постранично, и пока её читаешь, лента в
    # кабинете живёт: часть чеков в этот заход просто не показывается. Ключи их у нас
    # есть, и спросить позиции можно напрямую. Без этого счётчик «осталось взять»
    # замирает на числе, которое само не уменьшится, — ровно так 16.09.2026 повисли
    # 33 чека, и каждый следующий заход заканчивался мгновенно и без дела.
    shown = {e["key"] for e in entries}
    need += [{"key": r["key"], "date": r["date"], "store": r["store"], "total": r["total"]}
             for r in repo.pending_receipts() if r["key"] not in skip and r["key"] not in shown]
    imported: list[dict] = []
    failed: list[dict] = []
    gone: list[str] = []
    created = 0
    done = 0

    for start in range(0, len(need), batch):
        here = need[start:start + batch]
        by_key = {e["key"]: e for e in here}
        taken: list[dict] = []
        stale: list[str] = []
        for key, kind, data in cabinet.fiscal_many(token, [e["key"] for e in here]):
            done += 1
            if on_step:
                on_step("fiscal", done, len(need))
            if kind == "auth":
                stale.append(key)
            else:
                _settle(by_key[key], kind, data, taken, failed, gone)
        # Ключ, который кабинет перестал узнавать, — это не один пропущенный чек:
        # продолжать с мёртвым ключом значило бы записать всю оставшуюся пачку в «не
        # прочитались» и соврать про причину. Обновляем ключ и добираем только тех,
        # кому не досталось; если обновить нечем, _renewed обрывает загрузку — уже
        # взятые чеки к этому моменту лежат в истории.
        if stale:
            token = _renewed(cabinet)
            for key, kind, data in cabinet.fiscal_many(token, stale):
                if kind == "auth":
                    raise Unauthorized("кабинет не принял обновлённый ключ")
                _settle(by_key[key], kind, data, taken, failed, gone)
        part = import_bundle(_pack([], taken, []))
        imported += part.get("imported") or []
        created += part.get("products_created") or 0

    dates = [r["date"] for r in imported if r.get("date")]
    stores = {r["store"] for r in imported if r.get("store")}
    result.update({
        "receipts": len(imported),
        "rows": sum(r.get("rows") or 0 for r in imported),
        "total": round(sum(r.get("total") or 0 for r in imported), 2),
        "products_created": created,
        "period": (min(dates), max(dates)) if dates else None,
        "date": max(dates) if dates else "",
        "store": stores.pop() if len(stores) == 1 else (f"{len(stores)} магазинов" if stores else "—"),
        "failed": failed,
        "imported": imported,
        "pending": len(repo.pending_receipts()),
        "no_data": len(repo.unavailable_receipt_keys()),
        "gone_now": len(gone),
        "seen": len(entries),
        "synced_at": _now(),
    })
    repo.set_setting(K_SYNCED, result["synced_at"])
    repo.set_setting(K_RESULT, json.dumps(
        {k: result.get(k) for k in ("receipts", "rows", "total", "skipped", "pending", "seen",
                                    "no_data", "synced_at", "period")},
        ensure_ascii=False, default=str))
    return result


def headline(result: dict) -> str:
    """Одна строка для человека: что случилось при последней загрузке.

    «Все чеки уже в истории» говорим только тогда, когда брать действительно нечего.
    Прежде тут стояло число увиденных в кабинете, и строка уверяла, что всё загружено,
    даже когда полторы сотни чеков остались невзятыми — на живом кабинете это
    прозвучало ровно так.
    """
    from app.receipts import plural

    if result.get("receipts"):
        line = (f"Загружено {plural(result['receipts'], 'чек', 'чека', 'чеков')} · "
                f"{plural(result.get('rows') or 0, 'позиция', 'позиции', 'позиций')}")
    elif result.get("pending"):
        line = f"Новых чеков нет · осталось взять {result['pending']}"
    elif result.get("seen"):
        line = f"Новых чеков нет — все {plural(result['seen'], 'чек', 'чека', 'чеков')} разобраны"
    else:
        line = "Новых чеков нет"
    if result.get("no_data"):
        line += (f" · у {plural(result['no_data'], 'чека', 'чеков', 'чеков')} ФНС больше "
                 "не хранит состав")
    if result.get("failed"):
        line += f" · не прочитались {len(result['failed'])}"
    return line
