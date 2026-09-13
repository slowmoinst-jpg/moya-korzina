"""Общие типы данных. Контракт между коннекторами, матчером, оптимизатором и UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Product:
    id: int | None
    name: str
    brand: str | None = None
    barcode: str | None = None      # EAN-13 — самый надёжный ключ сопоставления
    weight_g: float | None = None
    unit: str = "pcs"          # 'pcs' | 'kg'
    category: str | None = None
    active: bool = True


@dataclass
class Store:
    id: int
    code: str
    name: str
    delivery_fee: float = 0.0
    free_delivery_from: float = 0.0
    min_order: float = 0.0
    connector_type: str = "stub"


@dataclass
class Card:
    id: int | None
    bank: str
    name: str


@dataclass
class Offer:
    id: int | None
    card_id: int
    store_id: int
    percent: float
    cap_rub: float
    min_check_rub: float = 0.0
    valid_from: str | None = None
    valid_to: str | None = None
    requires_activation: bool = False
    activated: bool = True
    cap_used: float = 0.0

    @property
    def cap_left(self) -> float:
        return max(0.0, self.cap_rub - self.cap_used)

    def is_valid_on(self, day: str | None = None) -> bool:
        day = day or datetime.now().strftime("%Y-%m-%d")
        if self.requires_activation and not self.activated:
            return False
        if self.valid_from and day < self.valid_from:
            return False
        if self.valid_to and day > self.valid_to:
            return False
        return True


@dataclass
class Candidate:
    """Кандидат из каталога магазина — результат connector.search()."""
    store_code: str
    sku: str
    name: str
    price: float | None = None
    weight_g: float | None = None
    unit: str | None = None
    url: str | None = None
    score: float = 0.0          # похожесть на эталон, 0..1
    ean: str | None = None


@dataclass
class PriceSnapshot:
    """Снимок цены — результат connector.get_prices()."""
    store_code: str
    sku: str
    price: float
    price_per_kg: float | None = None
    in_stock: bool = True
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    name: str | None = None


@dataclass
class BasketLine:
    """Строка корзины, обогащённая ценами по магазинам: prices[store_code] -> цена за qty единиц."""
    product_id: int
    name: str
    unit: str
    qty: float
    prices: dict[str, float] = field(default_factory=dict)     # store_code -> цена позиции целиком
    in_stock: dict[str, bool] = field(default_factory=dict)


@dataclass
class VariantLine:
    store_code: str
    store_name: str
    card_id: int | None
    card_name: str | None
    product_id: int
    product_name: str
    qty: float
    price: float               # стоимость позиции целиком (цена * qty)
    discount: float = 0.0      # доля кэшбэка, отнесённая на позицию


@dataclass
class StoreBreakdown:
    store_code: str
    store_name: str
    subtotal: float
    delivery: float
    discount: float
    total: float
    card_id: int | None = None
    card_name: str | None = None
    offer_id: int | None = None
    lines: list[VariantLine] = field(default_factory=list)
    below_min_order: bool = False


@dataclass
class Variant:
    """Один вариант разбиения корзины."""
    stores: list[StoreBreakdown]
    total: float
    baseline: float
    penalty: float = 0.0
    missing_products: list[str] = field(default_factory=list)

    @property
    def savings_rub(self) -> float:
        return round(self.baseline - self.total, 2)

    @property
    def savings_pct(self) -> float:
        return round((self.baseline - self.total) / self.baseline * 100, 2) if self.baseline else 0.0

    @property
    def title(self) -> str:
        return " + ".join(s.store_name for s in self.stores)
