"""Три человеческих шага: что уже сделано, а что ещё ждёт владельца.

ЗАЧЕМ (21.10.2026). Кодом сделано всё, что можно: серверный поиск, ссылка на корзину Ленты,
серверная кладка через домашний канал. Дальше три шага, которые может сделать только
человек, потому что это его входы и его деньги:

  1. Лента   — открыть ссылку корзины на своём телефоне и подтвердить «Добавить в корзину»
               (сайт не пускает наш сервер по отпечатку автоматизации);
  2. Дикси   — войти в кабинет заново (сессия протухла: `stop: login`) и подтвердить связь
               молока на экране «Связи» (сейчас там кандидат, а не подтверждение);
  3. Магнит  — один раз выбрать магазин/адрес в своём аккаунте: без выбранной точки сеть
               принимает нажатие и не кладёт товар.

Этот модуль НИЧЕГО не меняет: он только читает состояние и говорит, какой шаг ещё не сделан.
Вызов: `python -m app.humansteps` (на сервере: `docker exec -i -w /app korzina-jobs ...`).
"""
from __future__ import annotations

import argparse
import json

from app import repo, store_accounts, users
from app import storefind
from app.shopbrowser import store as browser_store


def _basket_lines() -> list[dict]:
    """Позиции корзины владельца — по ним видно, есть ли у сетей точные совпадения."""
    out: list[dict] = []
    try:
        baskets = repo.list_baskets()
        if not baskets:
            return out
        for item in repo.basket_items(int(baskets[0]["id"])):
            if not item.get("product_id"):
                continue
            out.append({"product_id": int(item["product_id"]), "name": item.get("name"),
                        "qty": item.get("qty") or 1, "pack_g": item.get("pack_g")})
    except Exception:  # noqa: BLE001 — нет базы человека: шаги просто не оценить
        return []
    return out


def check(lines: list[dict] | None = None, use_network: bool = False) -> dict:
    """Состояние трёх шагов. Ничего не меняет; сеть — только если use_network."""
    lines = _basket_lines() if lines is None else lines
    out: dict[str, dict] = {}

    # ЛЕНТА: сервер умеет собрать ссылку; шаг человека — открыть её. Проверяем, есть ли
    # вообще что открывать: точные совпадения по позициям корзины.
    exact = 0
    for line in lines:
        answer = storefind.find("lenta", line.get("name") or "",
                                pack_g=line.get("pack_g"), use_cache=not use_network)
        if answer.get("ok"):
            exact += 1
    out["lenta"] = {"step": "открыть ссылку корзины на телефоне и подтвердить",
                    "exact_matches": exact, "lines": len(lines),
                    "ready_to_try": exact > 0,
                    "note": "" if exact else "Лента не продаёт эти позиции точно — ссылки не будет"}

    # ДИКСИ: нужен вход заново (иначе deliver вернёт stop: login) и подтверждённые артикулы.
    state = browser_store.load("dixy")
    confirmed = 0
    try:
        dix_store = repo.get_store("dixy")
        for line in lines:
            if line.get("product_id") and repo.confirmed_mapping(
                    int(line["product_id"]), dix_store.id if dix_store else 0, real_only=True):
                confirmed += 1
    except Exception:  # noqa: BLE001
        confirmed = 0
    out["dixy"] = {"step": "войти в кабинет заново и подтвердить связь на «Связях»",
                   "login_saved": bool(state), "confirmed_articles": confirmed,
                   "can_place": bool(state) and confirmed > 0
                                 and store_accounts.can("dixy", store_accounts.CART),
                   "note": ("вход сохранён, но флаг cart в ABILITIES ещё не включён"
                            if state and confirmed and not store_accounts.can(
                                "dixy", store_accounts.CART) else "")}

    # МАГНИТ: нужен выбранный магазин в сессии. По умолчанию только факт сохранённого входа:
    # открывать сайт ради проверки — это запрос через домашний канал владельца, поэтому
    # только по просьбе (use_network).
    magnit: dict = {"step": "один раз выбрать магазин/адрес в аккаунте",
                    "login_saved": bool(browser_store.load("magnit")),
                    # ТРИ СОСТОЯНИЯ, А НЕ ДВА. По сохранённому профилю выбор магазина не
                    # виден (в нём только куки входа — проверено 21.10.2026), поэтому без
                    # похода в витрину честный ответ «неизвестно», а не «нет».
                    "store_choice": "unknown"}
    if use_network:
        try:
            from app import users
            from app.shopbrowser import driver

            # Профиль браузера — того, чьё рабочее место открыто (main открывает владельца).
            page = driver.look("magnit", users.current() or "")
            needs = "выберите магазин" in str(page.get("text") or "").lower()
            magnit["needs_store"] = needs
            magnit["store_choice"] = "no" if needs else "yes"
        except Exception as exc:  # noqa: BLE001
            magnit["needs_store"] = None
            magnit["store_choice"] = "unknown"
            magnit["error"] = type(exc).__name__
    out["magnit"] = magnit
    return out


def render(state: dict) -> str:
    lines = ["три человеческих шага (код со своей стороны готов):"]
    for chain, info in state.items():
        lines.append(f"  {chain:<10} {info.get('step')}")
        if chain == "lenta":
            lines.append(f"    точных совпадений в корзине: {info.get('exact_matches')}"
                         f" из {info.get('lines')}")
        elif chain == "dixy":
            lines.append(f"    вход сохранён: {'да' if info.get('login_saved') else 'нет'}"
                         f" | подтверждённых артикулов: {info.get('confirmed_articles')}"
                         f" | флаг cart: {'есть' if info.get('can_place') else 'нет'}")
        else:
            choice = info.get("store_choice")
            shown = {"yes": "да", "no": "нет", "unknown": "неизвестно"}.get(str(choice), "неизвестно")
            lines.append(f"    вход сохранён: {'да' if info.get('login_saved') else 'нет'}"
                         f" | магазин выбран: {shown}")
        if info.get("note"):
            lines.append(f"    {info['note']}")
    return "\n".join(lines)


def board(signed_in: set[str] | None = None) -> list[dict]:
    """Состояние автосборки по ВСЕМ одиннадцати сетям (Этап 0): вход, магазин, что легло, итог сети.

    Ничего не меняет и не ходит в сети: читает подключения и последние строки «Сборки
    корзин». По строке видно, на каком шаге каждая сеть стоит и что делать дальше.

    signed_in — сети, в которые вход СОХРАНЁН: так «подключено» понимает экран «Настройки»
    (web/phone/common.py, _login), потому что флаг подключения ставится отчётом о сборе и
    чаще погашен, чем нет. Без него экран показывал бы «подключить сеть» у сети, которая
    в соседнем списке стоит среди подключённых. Не передан — берётся флаг, как раньше.
    """
    from app import cartrun, carttotal

    names = {s.code: s.name for s in repo.list_stores()}
    out: list[dict] = []
    for conn in store_accounts.connections():
        code = conn.store_code
        connected = conn.connected if signed_in is None else code in signed_in
        name = names.get(code, code)
        row = cartrun.get(code)
        planned = row.planned if row else 0
        landed = len(row.landed) if row else 0
        network = bool(row and row.total and row.total.source == carttotal.NETWORK)
        state = row.state if row else cartrun.IDLE
        note = row.note if row and row.note else ""

        # Выбор магазина/адреса в аккаунте сети (Магнит и др.)
        if code == "magnit":
            if row and row.login == "store":
                store_choice = "no"
            elif connected:
                store_choice = "yes"
            else:
                store_choice = "unknown"
        else:
            store_choice = "yes" if connected else "unknown"

        act = ""    # шаг, который человек делает в окне сети: store — выбрать магазин, login — войти
        if not connected:
            step = "подключить сеть (войти в кабинет)"
        elif state == cartrun.LOGIN:
            act = "store" if (row and row.login == "store") else "login"
            step = "выбрать магазин в аккаунте" if act == "store" else "войти заново: сессия истекла"
        elif state in (cartrun.FAILED, cartrun.REFUSED, cartrun.CANT):
            step = note or "сеть не приняла корзину"
        elif state == cartrun.IDLE:
            step = "собрать корзину: в эту сеть ещё не собирали"
        elif planned and landed < planned:
            step = f"легло {landed} из {planned}: добрать остальное"
        elif not network:
            step = "корзина легла, но итог сети не прочитан"
        else:
            step = ""

        out.append({
            "code": code,
            "name": name,
            "connected": connected,
            "store_choice": store_choice,
            "road": cartrun.road(code),
            "state": state,
            "planned": planned,
            "landed": landed,
            "network_total": network,
            "ready": connected and not step,
            "note": note,
            "next": step,
            "act": act,
        })
    return out


def render_board(rows: list[dict]) -> str:
    lines = ["автосборка по ВСЕМ сетям (Этап 0):"]
    for r in rows:
        mark = "готово" if r["ready"] else "нужно"
        choice_str = {"yes": "да", "no": "НЕТ", "unknown": "неизвестно"}.get(r.get("store_choice"), "—")
        lines.append(f"  {r['code']:<12} ({r.get('name', r['code'])}) {mark:<7} дорога: {r['road']:<8} "
                     f"вход: {'да' if r['connected'] else 'нет'} | точка: {choice_str} | "
                     f"легло {r['landed']}/{r['planned']} | итог сети: "
                     f"{'да' if r['network_total'] else 'нет'}")
        if r["next"]:
            lines.append(f"    -> {r['next']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Состояние сборки корзины во всех сетях (humansteps)")
    parser.add_argument("--phone", default=None, help="телефон владельца (иначе первый в базе)")
    parser.add_argument("--network", action="store_true",
                        help="спросить сети (медленно, через кэш) и открыть Магнит браузером")
    parser.add_argument("--json", action="store_true", help="ответ в JSON")
    parser.add_argument("--board", action="store_true", help="состояние автосборки по всем сетям (по умолчанию)")
    parser.add_argument("--three", action="store_true", help="старый вывод трёх человеческих шагов")
    args = parser.parse_args(argv)

    phone = args.phone
    if phone is None:
        try:
            workspaces = users.list_workspaces()
            phone = workspaces[0] if workspaces else None
        except Exception:  # noqa: BLE001
            phone = None
    if phone:
        users.open_workspace(phone)
    try:
        use_board = not args.three
        state = board() if use_board else check(use_network=args.network)
    finally:
        if phone:
            users.deactivate()
    shown = render_board(state) if use_board else (None if args.json else render(state))
    print(json.dumps(state, ensure_ascii=False, indent=1) if args.json else shown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
