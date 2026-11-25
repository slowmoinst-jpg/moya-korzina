"""Сеть по чеку: в каком магазине куплено и собираем ли мы там заказы.

ЗАЧЕМ. «Заказы» пополняются чеками — из ФНС и добавленными вручную. Но чек из аптеки,
кафе или сети, где приложение не умеет собрать заказ, в заказах только мешает: корзина
«Как обычно» начала бы предлагать бинт и капучино, а экономия считалась бы по покупкам,
повторить которые мы не можем. Поэтому такие чеки не добавляются, а откладываются
отдельно — «Не добавлены: N чеков» (docs/design/nazvaniya-2026-09-26.md, «Заказы и чеки»).

КАК УЗНАЁТСЯ СЕТЬ. В чеке ФНС магазин записан как придётся: брендом («Пятёрочка»),
юрлицом («ООО "Агроторг"», «АО "Тандер"») или адресом точки. Поэтому у каждой сети
список примет — бренд и юрлица. Перекрёсток проверяется раньше Пятёрочки: у X5 оба
бренда, и слово «перекресток» точнее общего юрлица.

КАКИЕ СЕТИ ПРИНИМАЮТСЯ — настройка сервера `orders.chains`: те, где приложение
собирает заказ в аккаунте человека. Непонятный магазин не принимается: лучше
отложить чек, который можно вернуть, чем смешать заказы с чужими покупками.
"""
from __future__ import annotations

from app import config

# код сети -> приметы в названии продавца (нижний регистр, «ё» как «е»)
SIGNS: list[tuple[str, tuple[str, ...]]] = [
    ("perekrestok", ("перекресток", "перекрёсток", "perekrestok")),
    ("pyaterochka", ("пятерочка", "агроторг", "5ka", "5-delivery")),
    # доставка Магнита записана местом расчётов: «https://magnit.ru/dostavka/»
    ("magnit", ("магнит", "тандер", "magnit.ru")),
    ("vkusvill", ("вкусвилл", "вкус вилл", "vkusvill")),
    ("lenta", ("лента", "lenta")),
    ("dixy", ("дикси", "dixy")),
    ("samokat", ("самокат", "умный ритейл", "samokat")),
    ("metro", ("метро кэш", "metro cash", "метро кеш")),
]

# где приложение сегодня собирает заказ в аккаунте человека
DEFAULT_ORDERABLE = ("magnit", "pyaterochka", "vkusvill", "lenta", "samokat", "dixy", "perekrestok")

# чем сеть не является, хотя слово похоже: «Лента Строй» — стройматериалы
NOT_GROCERY = ("строй", "аптек")

# Продавец на две сети: доставку Пятёрочки и Перекрёстка пробивает одно юрлицо, ООО «ИКС 5
# ДИДЖИТАЛ», с местом расчётов delivery.x5-digital.ru. Чья покупка, в чеке видно только по адресу
# отправителя (sellerAddress): support@5-delivery.ru или support@perekrestok-dostavka.ru
# (семь чеков кабинета ФНС, 16.11.2026: три Пятёрочки, четыре Перекрёстка).
SHARED = ("x5-digital", "икс 5 диджитал")


def _plain(text: str | None) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def shared(store_name: str | None) -> bool:
    """Продавец возит заказы нескольких сетей: сеть называет сам чек, а не его продавец."""
    text = _plain(store_name)
    return any(sign in text for sign in SHARED)


def resolve(store_name: str | None, sender: str | None = None) -> str | None:
    """Код сети по тому, как магазин записан в чеке. None — сеть не узнана.

    sender — адрес отправителя чека. По нему судим только общего продавца (SHARED): у остальных
    это почта оператора данных, и чужое слово в ней сделало бы заказом посторонний чек.
    """
    text = _plain(sender if shared(store_name) else store_name)
    if not text:
        return None
    for code, signs in SIGNS:
        if any(_plain(sign) in text for sign in signs):
            if code == "lenta" and any(word in text for word in NOT_GROCERY):
                return None
            return code
    return None


def orderable() -> set[str]:
    """Сети, чеки которых становятся заказами."""
    value = config.get("orders.chains")
    if not value:
        return set(DEFAULT_ORDERABLE)
    return {str(code) for code in value}


def accepts(store_name: str | None = None, store_code: str | None = None) -> bool:
    """Становится ли чек этого магазина заказом."""
    code = store_code or resolve(store_name)
    return bool(code) and code in orderable()


# загруженные чеки, строки которых лежат в истории без сети
_UNSETTLED = ("SELECT r.key, r.store FROM receipts r WHERE r.imported_at IS NOT NULL AND EXISTS"
              " (SELECT 1 FROM purchase_history h WHERE h.receipt_key = r.key AND h.store_id IS NULL)")


def settle(senders: dict[str, str] | None = None) -> dict[str, int]:
    """Проставить сеть строкам истории, чей продавец теперь узнаётся: {сеть: чеков}.

    ЗАЧЕМ. Пока не было отсева, чек неузнанного продавца ложился в историю без сети: доставка
    Магнита записана адресом сайта, а Перекрёстка тогда не было в справочнике магазинов. Товары
    таких чеков не шли в сверку с каталогами сетей (app/catalog/x5.py). Повторный запуск ничего
    не меняет: берутся только строки без сети.

    Сеть вне orders.chains не ставится: с ней покупки ушли бы из «Истории заказов» и привычек
    (baskets.orderable_history). Войдёт сеть в список — следующий запуск проставит и такие
    чеки (так Перекрёсток вошёл 16.11.2026). Чеки, отложенные при импорте («Не добавлены»),
    проход не трогает: их строк в истории нет.

    senders — адрес отправителя по ключу чека. В базе его нет, поэтому общую доставку (shared)
    проход без него не судит: адрес спрашивают у кабинета ФНС заново (fns.sync.senders).
    """
    from app import repo

    stores = {s.code: s.id for s in repo.list_stores()}
    done: dict[str, int] = {}
    with repo.get_conn() as c:
        for row in c.execute(_UNSETTLED).fetchall():
            code = resolve(row["store"], (senders or {}).get(row["key"]))
            if code in stores and accepts(store_code=code):
                c.execute("UPDATE purchase_history SET store_id = ? WHERE receipt_key = ?"
                          " AND store_id IS NULL", (stores[code], row["key"]))
                done[code] = done.get(code, 0) + 1
        c.commit()
    return done


def main(argv: list[str] | None = None) -> int:
    """python -m app.chains --all | --phone N: сеть уже загруженным чекам.

    Чеки общей доставки спрашивает у кабинета ФНС заново, если он подключён: по запросу на чек.
    """
    import argparse

    from app import repo, users
    from app.fns import sync

    parser = argparse.ArgumentParser(description="Проставить сеть чекам истории, чей продавец теперь узнаётся.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--all", action="store_true", help="все рабочие места")
    who.add_argument("--phone", help="одно рабочее место")
    args = parser.parse_args(argv)
    if args.phone and args.phone not in users.list_workspaces():
        parser.error("нет такого рабочего места")       # open_workspace завёл бы пустое

    def waiting() -> list[str]:
        with repo.get_conn() as c:
            return [r["key"] for r in c.execute(_UNSETTLED) if shared(r["store"])]

    for phone in users.list_workspaces() if args.all else [args.phone]:
        try:
            users.open_workspace(phone)
            done = settle(sync.senders(waiting()))
            # кабинет не ответил или сеть вне orders.chains: одно «нечего» это скрыло бы
            left = len(waiting())
            print(f"…{phone[-4:]}: {done or 'проставлять нечего'}"
                  + (f"; общая доставка без сети: {left}" if left else ""))
        finally:
            users.deactivate()
    return 0


__all__ = ["resolve", "shared", "orderable", "accepts", "settle", "SIGNS", "SHARED",
           "DEFAULT_ORDERABLE"]

if __name__ == "__main__":
    raise SystemExit(main())
