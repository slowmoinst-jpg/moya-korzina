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
    ("perekrestok", ("перекресток", "перекрёсток")),
    ("pyaterochka", ("пятерочка", "агроторг", "5ka")),
    ("magnit", ("магнит", "тандер")),
    ("vkusvill", ("вкусвилл", "вкус вилл", "vkusvill")),
    ("lenta", ("лента", "lenta")),
    ("dixy", ("дикси", "dixy")),
    ("samokat", ("самокат", "умный ритейл", "samokat")),
    ("metro", ("метро кэш", "metro cash", "метро кеш")),
]

# где приложение сегодня собирает заказ в аккаунте человека
DEFAULT_ORDERABLE = ("magnit", "pyaterochka", "vkusvill", "lenta", "samokat", "dixy")

# чем сеть не является, хотя слово похоже: «Лента Строй» — стройматериалы
NOT_GROCERY = ("строй", "аптек")


def _plain(text: str | None) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def resolve(store_name: str | None) -> str | None:
    """Код сети по тому, как магазин записан в чеке. None — сеть не узнана."""
    text = _plain(store_name)
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


__all__ = ["resolve", "orderable", "accepts", "SIGNS", "DEFAULT_ORDERABLE"]
