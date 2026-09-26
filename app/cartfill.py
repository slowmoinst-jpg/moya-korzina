"""Корзины, собранные в приложении, — в кабинеты магазинов, всем разом и без нажатий.

ЗАЧЕМ ЭТО ЕСТЬ. На экране «Результат» у каждой сети своя кнопка: «Передать» кладёт
корзину в кабинет сети браузером нашего сервера, «Собрать корзину» у Ленты и
ВкусВилла получает от сети ссылку. Поручение владельца 22.09.2026 — «запусти
формирование корзин, собранных в приложении, в кабинетах магазинов» — значит
пройти эти кнопки за него: по каждому рабочему месту, по каждой сети того
варианта, который «Результат» показывает первым.

ТОТ ЖЕ ПУТЬ, ЧТО У КНОПКИ, И НИКАКОГО ДРУГОГО. Вариант выбирается тем же правилом,
что на экране (result._chosen), наряд собирает тот же cartplan.build, кладёт тот же
cart.start, отчёт ложится в ту же отметку о ходе, которую человек видит на
«Кабинетах». Своих правил здесь нет: будь они, пачка и кнопка разошлись бы при
первой же правке одной из них, и человек получил бы в корзине не то, что видел.

ЧЕГО ЗДЕСЬ НЕТ. Оформления и оплаты: наряд кончается наполненной корзиной, как и у
кнопки. Расписания: каждый прогон кладёт товары в корзину ЗАНОВО, и пачка,
пущенная по часам, наполняла бы её снова и снова. Поэтому её пускают руками, а
вторую передачу в сеть, куда уже идёт первая, не пускает сам cart.start.

На сервере — отдельным контейнером: выкладка его не снесёт, а входной скрипт образа
поднимет экран для браузера (tools/entrypoint.sh):

    docker run --rm --name korzina-carts -v korzina-data:/app/data korzina:latest \\
        python -m app.cartfill --dry-run      # что будет сделано; в сеть не ходит
    docker run --rm --name korzina-carts -v korzina-data:/app/data korzina:latest \\
        python -m app.cartfill                # сделать
"""
from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger("cartfill")


def mask(phone: str) -> str:
    """Номер в отчёте — без середины: отчёт уходит в журнал и в переписку."""
    return phone if len(phone) < 7 else f"{phone[:2]}***{phone[-4:]}"


def preferred_variant(basket_id: int):
    """Вариант, который «Результат» показывает первым. Нечего считать — None.

    Цены в сеть не спрашиваем (refresh=False), ровно как экран: пачка кладёт то,
    что человек видел посчитанным, а не то, что насчитала бы за его спиной.
    """
    from app import service
    from app.web.screens.result import _chosen

    variants, _baseline = service.calculate(basket_id, False)
    if not variants:
        return None
    return _chosen(variants, None)[0]


def fill_store(phone: str, store, *, dry_run: bool = False) -> str:
    """Одна сеть варианта: положить в кабинет, получить ссылку или сказать, почему нельзя."""
    from app import cartplan, handover, store_accounts, users
    from app.shopbrowser import cart, driver
    from app.shopbrowser import store as shopstore

    code = store.store_code
    lines = store.lines or []
    kind = handover.KIND_BY_STORE.get(code, handover.LIST)

    if kind == handover.LINK:
        # Лента и ВкусВилл принимают корзину ССЫЛКОЙ: её создаёт сеть, а корзина
        # появляется у того, кто ссылку откроет. Из сервера в кабинет её не
        # положить, поэтому ссылка и есть итог — её надо донести до человека.
        if dry_run:
            return f"сеть отдаст корзину ссылкой ({len(lines)} поз.)"
        got = handover.for_store(code, lines)
        if got.link:
            return f"корзина собрана по ссылке — откройте её, войдя в сеть: {got.link}"
        return f"ссылку сеть не выдала. {got.note}"

    plan = cartplan.build(code, lines, force=True)
    if not plan.lines:
        return plan.note or "ни одна позиция этой сети не знакома — класть нечего"
    # Сеть, чью корзину мы умеем наполнять, без входа молчит о причине: умение
    # включает только вход, сохранённый на «Кабинете» (store_accounts.mark_connected),
    # и cartplan.why_not ответил бы описанием сети вместо «войдите». Говорим прямо.
    ability = store_accounts.ABILITIES.get(code)
    if ability and store_accounts.CART in ability.gives and shopstore.load(code) is None:
        return ("вход не сохранён — откройте сеть на «Кабинетах» и войдите, "
                f"тогда {len(plan.lines)} поз. лягут в корзину")
    why = cartplan.why_not(code)
    if why:
        return f"в кабинет не положить: {why}"
    unknown = f", незнакомых сети {len(plan.unknown)}" if plan.unknown else ""
    if dry_run:
        return f"положу в корзину {len(plan.lines)} поз. на {plan.total:.2f} ₽{unknown}"

    started = cart.start(code, phone, plan, wait=True)
    # Передача с ожиданием закрывает рабочее место за собой, как её фоновый поток,
    # а пачке оно нужно дальше — под следующую сеть.
    users.open_workspace(phone)
    if not started:
        return "в эту сеть уже идёт передача — вторую не пускаю, иначе всё ляжет дважды"
    driver.close(code, phone)
    got = cart.progress(code) or {}
    landed = sum(1 for item in (got.get("items") or []) if item.get("ok"))
    return f"легло {landed} из {len(plan.lines)}{unknown}. {got.get('note') or ''}".strip()


def run(phones: list[str] | None = None, basket: int | None = None,
        dry_run: bool = False) -> list[str]:
    """Пройти рабочие места и вернуть отчёт строками.

    Без номеров — все рабочие места, кроме демо: демо — витрина с выдуманной
    корзиной, и класть её в настоящие кабинеты незачем. Корзина — последняя
    созданная, как на «Результате», если номер корзины не назван.
    """
    from app import repo, users
    from app.shopbrowser import store as shopstore

    out: list[str] = []
    for phone in phones or [p for p in users.list_workspaces() if p != users.DEMO]:
        if not users.exists(phone):
            out.append(f"{mask(phone)}: рабочего места нет")
            continue
        users.open_workspace(phone)
        try:
            # Какие кабинеты открыты — первое, что надо знать про рабочее место:
            # без входа в корзину Магнита класть некуда, есть там корзина или нет.
            logins = shopstore.saved_stores()
            where = f"входы сохранены: {', '.join(logins)}" if logins else "входов в сети нет"
            baskets = repo.list_baskets()
            chosen = next((b for b in baskets if basket is None or int(b["id"]) == basket), None)
            if chosen is None:
                out.append(f"{mask(phone)}: корзин нет — соберите её на экране «Корзина» "
                           f"(«Как в прошлый раз»); {where}" if basket is None
                           else f"{mask(phone)}: корзины #{basket} нет; {where}")
                continue
            basket_id = int(chosen["id"])
            items = repo.basket_items(basket_id)
            if not items:
                out.append(f"{mask(phone)}: корзина «{chosen.get('name')}» пуста")
                continue
            best = preferred_variant(basket_id)
            if best is None:
                out.append(f"{mask(phone)}: корзина «{chosen.get('name')}» не посчиталась — "
                           "у её позиций нет цен ни в одной сети")
                continue
            out.append(f"{mask(phone)}: корзина «{chosen.get('name')}», {len(items)} поз. — "
                       f"{best.title}, {best.total:.2f} ₽; {where}")
            for store in best.stores:
                try:
                    note = fill_store(phone, store, dry_run=dry_run)
                except Exception as exc:  # noqa: BLE001 — одна сеть не должна ронять остальные
                    log.exception("%s: корзина не передалась", store.store_code)
                    users.open_workspace(phone)
                    note = f"не вышло: {type(exc).__name__}: {exc}"
                out.append(f"    {store.store_name}: {note}")
        finally:
            users.deactivate()
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Положить корзины, посчитанные в приложении, в кабинеты сетей")
    parser.add_argument("--phone", action="append",
                        help="только это рабочее место (можно несколько раз)")
    parser.add_argument("--basket", type=int, help="номер корзины вместо последней")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать, что будет сделано, ничего не кладя и не создавая")
    args = parser.parse_args(argv)
    report = run(args.phone, args.basket, args.dry_run)
    print("\n".join(report) if report else "рабочих мест нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
