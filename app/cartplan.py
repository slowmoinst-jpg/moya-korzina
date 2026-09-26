"""Наряд на корзину: что сборщик должен положить в корзину магазина.

Последняя ступень передачи, доступная только подключённой сети (app/store_accounts.py).
Отличается от всех прежних ступеней тем, КУДА кладётся: не в ссылку и не в список,
а в ТУ САМУЮ корзину, которую человек видит, войдя в магазин. Потому и требует
входа: чужую корзину наполнить нельзя, а свою — можно, и делает это сборщик,
живущий в его же браузере.

ПОЧЕМУ НАРЯД, А НЕ ДЕЙСТВИЕ. Приложение живёт на сервере, а корзина — в браузере
человека, и дотянуться до неё оттуда нельзя ни при каком устройстве: у сетей вход
защищён капчей, и автоматический браузер до аккаунта не доходит. Поэтому сервер
готовит наряд — простой список «артикул, сколько, как называется», — а исполняет
его сборщик на стороне человека. Такое разделение заодно делает шаг проверяемым:
наряд можно показать человеку целиком до того, как что-то произойдёт.

ЧТО В НАРЯДЕ И ЧЕГО В НЁМ НЕТ

    store     код сети;
    lines     позиции: sku в этой сети, количество, название и адрес карточки;
    unknown   позиции, которых в сети не нашлось, — отдельным списком;
    total     сумма по известным позициям, чтобы человек сверил её глазами.

Ни цен, по которым «надо» купить, ни команд оформления, ни платёжных данных. Наряд
кончается наполненной корзиной; кнопку «заказать» нажимает человек. Это его деньги,
и последнее слово остаётся за ним — а заодно любая наша ошибка в расчёте остаётся
видимой ошибкой, а не списанием.

НЕИЗВЕСТНЫЕ ПОЗИЦИИ НЕ ПРЯЧУТСЯ. Товар, которому нет соответствия в этой сети,
попадает в unknown, а не выпадает молча: корзина из четырнадцати позиций вместо
шестнадцати — это нормально, но человек должен знать, каких двух не хватает.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app import repo, store_accounts

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanLine:
    """Одна строка наряда: что и сколько положить.

    `price` — стоимость позиции ЦЕЛИКОМ (цена × количество), как её считают и
    корзина, и вариант расчёта. Она здесь не указание магазину, а то, с чем
    человек сверит корзину глазами, когда та наполнится.
    """

    sku: str
    qty: float
    name: str
    unit: str | None = None
    price: float | None = None
    url: str | None = None
    # В чём считано qty: «kg» — килограммы корзины, «pcs» — штуки. unit выше —
    # единица ТОВАРА СЕТИ, и они расходятся: корзина просит 0,4 кг сыра, а сеть
    # продаёт его упаковками по 200 г.
    per: str | None = None
    # Вес одной упаковки товара сети, граммы. С ним килограммы корзины переводятся
    # в упаковки: 0,4 кг по 200 г — две, а не одна, как выходило из round(0,4).
    pack_g: float | None = None

    def as_dict(self) -> dict:
        return {"sku": self.sku, "qty": self.qty, "name": self.name,
                "unit": self.unit, "price": self.price, "url": self.url,
                "per": self.per, "pack_g": self.pack_g}


@dataclass
class CartPlan:
    """Наряд целиком. Пустой наряд — тоже ответ, и его надо показать, а не скрыть."""

    store_code: str
    lines: list[PlanLine] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.lines)

    @property
    def total(self) -> float:
        return round(sum(line.price or 0.0 for line in self.lines), 2)

    def as_dict(self) -> dict:
        return {"store": self.store_code, "lines": [line.as_dict() for line in self.lines],
                "unknown": list(self.unknown), "total": self.total, "note": self.note}


def available(store_code: str) -> bool:
    """Умеет ли эта сеть принимать наряд прямо сейчас.

    Два условия, и оба обязательны: сеть в принципе даёт наполнять корзину и
    человек в ней сейчас вошёл. Без входа наряд некуда исполнять — корзина у
    незашедшего своя на каждое устройство и живёт до закрытия вкладки.
    """
    return store_accounts.can(store_code, store_accounts.CART) and not _point_trouble(store_code)


def _point_trouble(store_code: str) -> str:
    """Мешает ли САМА ТОЧКА. Отдельно от умения сети — это разные препятствия."""
    try:
        from app.shopbrowser import point

        return point.trouble(store_code)
    except Exception:  # noqa: BLE001 — недоступный браузерный слой не повод врать
        log.info("%s: пригодность точки не проверена", store_code, exc_info=True)
        return ""


def why_not(store_code: str) -> str:
    """Почему наряд в эту сеть не исполнить. Пусто — исполнить можно.

    ДВЕ РАЗНЫЕ ПРИЧИНЫ, И ПУТАТЬ ИХ НЕЛЬЗЯ. Первая — про сеть: до витрины Ленты,
    Дикси, Пятёрочки и Самоката наш сервер не доходит вовсе, а METRO не принимает
    запись без именного токена. Вторая — про ТОЧКУ: сеть доступна, вход есть, а
    выбранный магазин работает только на полке, и заказать из него нельзя ничего
    (замеры в app/shopbrowser/point.py). Совет во втором случае другой — сменить
    точку, а не ждать, — и общей фразой он бы потерялся.
    """
    if not store_accounts.can(store_code, store_accounts.CART):
        known = store_accounts.ABILITIES.get(store_code)
        return known.note if known else "Эта сеть корзину от приложения не принимает."
    return _point_trouble(store_code)


def _price(line, store_code: str) -> float | None:
    """Стоимость позиции в ЭТОЙ сети.

    Строку сюда приносят с двух сторон, и поля у них разные: у строки корзины
    цены разложены по магазинам (prices[store_code]), у строки варианта расчёта
    цена уже одна — та, что в выбранном магазине. Берём то, что есть, и не
    придумываем: цена чужого магазина в наряде хуже, чем её отсутствие.
    """
    prices = getattr(line, "prices", None)
    if isinstance(prices, dict) and prices.get(store_code) is not None:
        return round(float(prices[store_code]), 2)
    own = getattr(line, "price", None)
    if own is not None and getattr(line, "store_code", store_code) == store_code:
        return round(float(own), 2)
    return None


def _per(line, product_id) -> str | None:
    """В чём корзина считает эту позицию: «kg» или «pcs». Не знаем — None.

    «kg» — только у товара, который и правда берут на вес (service.is_weighed):
    отметка unit="kg" в базе бывает и у буханки, чьё название просто без
    граммовки, и наряд переводил бы «1 буханку» в три упаковки по 400 г.
    """
    from app import service

    unit = getattr(line, "unit", None)
    product = repo.get_product(product_id) if product_id else None
    if unit not in ("kg", "pcs"):
        unit = product.unit if product else None
    if unit is None:
        return None
    qty = getattr(line, "qty", 1) or 1
    name = product.name if product else getattr(line, "name", None)
    return "kg" if service.is_weighed(product_id, unit, qty, name) else "pcs"


def _pack(mapping: dict | None) -> float | None:
    """Вес упаковки товара сети в граммах, если он известен и осмыслен."""
    try:
        grams = float((mapping or {}).get("weight_g") or 0)
    except (TypeError, ValueError):
        return None
    return grams if grams > 0 else None


def build(store_code: str, lines, *, force: bool = False) -> CartPlan:
    """Собрать наряд по позициям расчёта.

    Артикул берётся из ПОДТВЕРЖДЁННОГО сопоставления и ниоткуда больше. Догадка
    здесь стоила бы дороже пропуска: положить человеку в корзину не тот творог
    хуже, чем не положить ничего, — второе он увидит, первое нет.
    """
    store = repo.get_store(store_code)
    plan = CartPlan(store_code=store_code)
    if not store:
        plan.note = f"Сети «{store_code}» нет в справочнике магазинов."
        return plan
    if not force and not available(store_code):
        plan.note = ("Чтобы положить корзину прямо в магазин, войдите в него — "
                     "вход происходит на сайте самой сети.")
        return plan

    for line in lines or []:
        name = (getattr(line, "name", None) or getattr(line, "product_name", None) or "—")
        product_id = getattr(line, "product_id", None)
        mapping = repo.confirmed_mapping(product_id, store.id) if product_id else None
        sku = (mapping or {}).get("sku")
        if not sku:
            plan.unknown.append(name)
            continue
        shop_unit = (mapping or {}).get("unit")
        plan.lines.append(PlanLine(
            sku=str(sku),
            qty=float(getattr(line, "qty", 1) or 1),
            name=(mapping or {}).get("raw_name") or name,
            unit=shop_unit,
            price=_price(line, store_code),
            url=(mapping or {}).get("url"),
            per=_per(line, product_id),
            pack_g=_pack(mapping) if shop_unit != "kg" else None,
        ))

    if plan.unknown and plan.lines:
        plan.note = (f"{len(plan.unknown)} позиц. этой сети незнакомы — они останутся "
                     "вне корзины, соберите их обычным поиском.")
    elif plan.unknown:
        plan.note = "Ни одна позиция этой сети не знакома — класть в корзину нечего."
    return plan


PENDING_KEY_PREFIX = "cartplan.pending."


def save_pending(store_code: str, plan: CartPlan) -> None:
    """Отложить готовый наряд до того, как его заберут.

    Писалось для расширения, которое спрашивало наряд само; с 17.09.2026 корзину
    кладёт браузер на сервере и берёт наряд напрямую (app/shopbrowser/cart.py).
    Отложенный наряд остался ради приёмной двери — см. её докстринг про клиента.
    """
    repo.set_setting(f"{PENDING_KEY_PREFIX}{store_code}", json.dumps(plan.as_dict(), ensure_ascii=False))


def get_pending(store_code: str) -> CartPlan | None:
    """Получить ранее сохранённый наряд от оптимизатора, если он есть."""
    raw = repo.get_setting(f"{PENDING_KEY_PREFIX}{store_code}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        lines = [PlanLine(**item) for item in data.get("lines") or []]
        return CartPlan(
            store_code=data.get("store") or store_code,
            lines=lines,
            unknown=data.get("unknown") or [],
            note=data.get("note") or "",
        )
    except Exception as exc:
        log.warning("ошибка чтения сохранённого наряда %s: %s", store_code, exc)
        return None


def clear_pending(store_code: str) -> None:
    """Очистить сохранённый наряд после его выдачи или отмены."""
    repo.set_setting(f"{PENDING_KEY_PREFIX}{store_code}", None)


__all__ = ["CartPlan", "PlanLine", "build", "available", "why_not",
           "save_pending", "get_pending", "clear_pending"]
