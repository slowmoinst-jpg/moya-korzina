"""Как отдать собранную корзину в магазин, не трогая аккаунт человека.

Единого способа не существует: каждая сеть пускает к себе по-своему, а некоторые
не пускают вовсе. Поэтому здесь не одна функция, а три разных силы передачи —
от «вся корзина одной ссылкой» до «вот список, дальше руками».

    LINK   вся корзина уезжает одной ссылкой.
           ВкусВилл: vkusvill_cart_link_create, до 20 позиций.
           Лента: storefront_cart_link_create, до 100 позиций, количество целое.
           Лента переехала сюда из ITEMS 15.09.2026, когда инструмент появился
           у неё на сервере.

    ITEMS  ссылки на карточки товаров. На телефоне каждая открывается прямо в
           приложении магазина — сети объявляют это в apple-app-site-association:
           у Магнита разрешён путь /product/*. Шестнадцать позиций — шестнадцать
           нажатий, некрасиво, но работает и ничего чужого не требует.

    LIST   принять-то магазин готов, а вот адресовать ему нечего. Проверено
           15.09.2026 по apple-app-site-association: Самокат объявляет и
           /product/*, и даже /cart/sharing/* — то есть умеет и карточку, и
           расшаренную корзину. Но идентификаторов его товаров у нас нет:
           каталог Самоката и Пятёрочки закрыт антиботом, и позиции этих сетей
           живут у нас только из чеков (sku вида hist-*), а по такому sku ссылку
           не построить. Дикси отвечает 403 и капчей. Остаётся список, который
           человек открывает рядом с их приложением и отмечает по мере сборки.

           Эта ступень падёт не от нашей смекалки, а от смены места, откуда идёт
           запрос: с телефона человека, из России и из его же сессии, каталоги
           этих сетей открыты. См. docs/spec-moi-cheki.md про ту же оболочку.

Общее у всех трёх: заказ оформляет сам человек, своим аккаунтом и своей картой.
Кэшбэк, ради которого всё и затевалось, остаётся у него.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlparse, urlunparse

from app import config, repo

log = logging.getLogger(__name__)

LINK = "link"
ITEMS = "items"
LIST = "list"

# Чем сильнее способ, тем меньше человеку делать руками. Магазин попадает сюда
# только после того, как способ проверен живьём.
KIND_BY_STORE = {
    "vkusvill": LINK,
    "magnit": ITEMS,
    "lenta": LINK,
    "pyaterochka": LIST,
    "dixy": LIST,
    "samokat": LIST,
}

NOTE_BY_KIND = {
    LINK: "Откройте ссылку — товары лягут в вашу корзину, останется подтвердить заказ.",
    ITEMS: "На телефоне ссылки открываются прямо в приложении магазина, на нужном товаре.",
    LIST: "Магазин не принимает готовый список — держите его открытым рядом с приложением.",
}


@dataclass
class HandoverItem:
    name: str
    qty: float
    unit: str | None = None
    price: float | None = None
    url: str | None = None


@dataclass
class Handover:
    """Чем именно мы можем помочь с этим магазином прямо сейчас."""
    store_code: str
    kind: str = LIST
    link: str | None = None
    items: list[HandoverItem] = field(default_factory=list)
    note: str = ""

    @property
    def ready(self) -> bool:
        """Есть ли что показывать: ссылка на корзину или хотя бы одна позиция."""
        return bool(self.link or self.items)


def _with_shop(url: str, store_code: str) -> str:
    """Дописывает к ссылке Магнита выбранный магазин: без него откроется чужой."""
    if store_code != "magnit":
        return url
    shop = config.get("connectors.magnit_shop_code")
    if not shop or "shopCode=" in url:
        return url
    parts = urlparse(url)
    query = urlencode({"shopCode": str(shop), "shopType": "dostavka"})
    return urlunparse(parts._replace(query="&".join(filter(None, (parts.query, query)))))


def _items(store_code: str, lines, with_urls: bool) -> list[HandoverItem]:
    store = repo.get_store(store_code)
    out: list[HandoverItem] = []
    for line in lines or []:
        product_id = getattr(line, "product_id", None)
        mapping = repo.confirmed_mapping(product_id, store.id) if (store and product_id) else None
        url = (mapping or {}).get("url") if with_urls else None
        out.append(HandoverItem(
            name=getattr(line, "product_name", None) or (mapping or {}).get("raw_name") or "—",
            qty=float(getattr(line, "qty", 1) or 1),
            unit=(mapping or {}).get("unit"),
            price=getattr(line, "price", None),
            url=_with_shop(url, store_code) if url else None,
        ))
    return out


def for_store(store_code: str, lines) -> Handover:
    """Способ передачи корзины в конкретный магазин + всё, что для него нужно.

    Наружу не бросает: если магазин не ответил, способ просто падает на ступень
    ниже — со ссылки на корзину до списка позиций. Пустой результат тоже ответ.
    """
    kind = KIND_BY_STORE.get(store_code, LIST)
    result = Handover(store_code=store_code, kind=kind, note=NOTE_BY_KIND.get(kind, ""))

    if kind == LINK:
        try:
            from app import service

            result.link = service.cart_link(store_code, lines)
        except Exception as exc:  # noqa: BLE001
            log.warning("Ссылку на корзину %s получить не удалось: %s", store_code, exc)
        if not result.link:
            # магазин не ответил или позиции ему незнакомы — не оставляем человека ни с чем,
            # но и не врём, будто магазин так не умеет: он умеет, просто сейчас не вышло
            kind = result.kind = LIST
            result.note = ("Ссылку на корзину получить не вышло — вот список позиций, "
                           "соберите их в приложении магазина.")

    result.items = _items(store_code, lines, with_urls=(kind == ITEMS))
    return result


def as_text(handover: Handover) -> str:
    """Список для буфера обмена — то, что можно переслать себе или прочитать в магазине."""
    store = repo.get_store(handover.store_code)
    head = (store.name if store else handover.store_code) + ":"
    rows = []
    for item in handover.items:
        qty = f"{item.qty:g}"
        unit = " кг" if item.unit == "kg" else " шт"
        rows.append(f"— {item.name} · {qty}{unit}")
    return "\n".join([head, *rows])
