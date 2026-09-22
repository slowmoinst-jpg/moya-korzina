r"""Канва мобильных концептов: десять направлений, все экраны вертикально.

Запуск из корня проекта:
    .venv\Scripts\python.exe tools/build_mobile_concepts.py

Пишет в design/mobile/ по артборду на концепт (Concept01.dc.html … Concept10.dc.html,
первый из них называется Main.dc.html — это входной артборд канвы) и canvas.json с
раскладкой. Цены, товары и условия карт берутся из data/*.csv, поэтому макеты
показывают те же числа, что и приложение, и не расходятся с ним при правках данных.

Каждый артборд — один концепт: ряд из семи экранов телефона 390×844 подряд.
Экраны одни и те же во всех концептах, различается подача: шапка, строка товара,
плитки, переключатель режимов, навигация, палитра и шрифты.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "design", "mobile")

PHONE_W, PHONE_H = 390, 844
GAP = 34            # между телефонами внутри артборда
PAD = 44            # поля артборда
HEAD_H = 132        # шапка артборда с названием концепта

# Магазины: код, имя, доставка, бесплатно от, цвет метки
STORES = {
    "magnit": ("Магнит", 149.0, 2000.0, "#D8452B"),
    "vkusvill": ("ВкусВилл", 99.0, 1500.0, "#2E9E5B"),
    "pyaterochka": ("Пятёрочка", 199.0, 2500.0, "#E2A32B"),
    "lenta": ("Лента", 199.0, 3000.0, "#3B6FD4"),
    "dixy": ("Дикси", 149.0, 2000.0, "#C8562F"),
    "samokat": ("Самокат", 0.0, 0.0, "#7A5CD0"),
}
SHOP_ORDER = ["magnit", "vkusvill", "pyaterochka", "lenta", "dixy", "samokat"]
BASELINE = "pyaterochka"          # привычный магазин, от него считается выгода
BASE_GEN, BASE_PREP = "Пятёрочки", "Пятёрочке"   # падежи для подписей о выгоде
PRICE_STORES = ["magnit", "vkusvill", "pyaterochka"]   # у кого в data есть цены


def _rows(name: str) -> list[dict]:
    with open(os.path.join(DATA, name), encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def rub(value: float, kop: bool = True) -> str:
    """4 815,25 ₽ — неразрывные пробелы, запятая, как в приложении."""
    s = f"{value:,.2f}" if kop else f"{value:,.0f}"
    s = s.replace(",", " ").replace(".", ",")
    return f"{s} ₽"


@dataclass
class Line:
    """Позиция корзины со всеми ценами, какие нашлись по магазинам."""
    name: str
    short: str
    qty: float
    unit: str
    category: str
    prices: dict[str, float]

    @property
    def best_store(self) -> str:
        return min(self.prices, key=self.prices.get)

    @property
    def best(self) -> float:
        return self.prices[self.best_store]

    @property
    def qty_label(self) -> str:
        if self.unit == "kg":
            return f"{self.qty:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " кг"
        return f"{self.qty:.0f} шт"

    def total(self, store: str) -> float:
        return round(self.qty * self.prices[store], 2)


def short_name(name: str) -> str:
    """Название в строку телефона: без граммовки, если она есть отдельной подписью."""
    parts = name.split()
    if parts and parts[-1] in {"г", "мл", "кг", "л"} and parts[-2].replace(",", "").isdigit():
        return " ".join(parts[:-2])
    return name


def load_lines() -> list[Line]:
    products = {r["name"]: r for r in _rows("seed_products.csv")}
    order = [r["name"] for r in _rows("seed_products.csv")]
    qty = {r["name"]: float(r["qty"]) for r in _rows("seed_receipt.csv")}
    # цены лежат в том же порядке, что и эталоны, — по магазину на блок
    by_store: dict[str, list[float]] = {}
    for r in _rows("fallback_prices.csv"):
        by_store.setdefault(r["store_code"], []).append(float(r["price"]))
    lines = []
    for i, name in enumerate(order):
        prices = {s: by_store[s][i] for s in PRICE_STORES if i < len(by_store.get(s, []))}
        p = products[name]
        lines.append(Line(name=name, short=short_name(name), qty=qty.get(name, 1.0),
                          unit=p["unit"], category=p["category"] or "Прочее", prices=prices))
    return lines


@dataclass
class Offer:
    bank: str
    card: str
    store: str
    percent: float
    cap: float
    min_check: float
    cap_used: float


def load_offers() -> list[Offer]:
    return [Offer(r["bank"], r["card"], r["store_code"], float(r["percent"]), float(r["cap_rub"]),
                  float(r["min_check_rub"]), float(r["cap_used"] or 0)) for r in _rows("seed_offers.csv")]


OFFERS = load_offers()


def cashback(store: str, subtotal: float) -> tuple[float, Offer | None]:
    """Лучшая карта под этот подытог: процент, но не больше остатка лимита."""
    best, best_offer = 0.0, None
    for o in OFFERS:
        if o.store != store or subtotal < o.min_check:
            continue
        value = min(subtotal * o.percent / 100, o.cap - o.cap_used)
        if value > best:
            best, best_offer = round(value, 2), o
    return best, best_offer


def store_total(store: str, subtotal: float) -> dict:
    _, fee, free_from, _ = STORES[store]
    delivery = 0.0 if subtotal >= free_from else fee
    back, offer = cashback(store, subtotal)
    return {"subtotal": round(subtotal, 2), "delivery": delivery, "cashback": back,
            "offer": offer, "total": round(subtotal + delivery - back, 2)}


LINES = load_lines()

# Сплит: каждая позиция уходит туда, где дешевле среди Магнита и ВкусВилла
SPLIT_STORES = ["magnit", "vkusvill"]
SPLIT: dict[str, list[Line]] = {s: [] for s in SPLIT_STORES}
for line in LINES:
    winner = min(SPLIT_STORES, key=lambda s: line.prices[s])
    SPLIT[winner].append(line)
SPLIT_CALC = {s: store_total(s, sum(l.total(s) for l in SPLIT[s])) for s in SPLIT_STORES}
SPLIT_TOTAL = round(sum(c["total"] for c in SPLIT_CALC.values()), 2)

# Один магазин: вся корзина целиком, побеждает самый дешёвый итог
SINGLE_CALC = {s: store_total(s, sum(l.total(s) for l in LINES)) for s in PRICE_STORES}
SINGLE_STORE = min(SINGLE_CALC, key=lambda s: SINGLE_CALC[s]["total"])
SINGLE_TOTAL = SINGLE_CALC[SINGLE_STORE]["total"]

BASE_TOTAL = SINGLE_CALC[BASELINE]["total"]           # привычный магазин без выбора
GAIN = round(BASE_TOTAL - SPLIT_TOTAL, 2)
GAIN_PCT = round(GAIN / BASE_TOTAL * 100, 1)
RECEIPT_TOTAL = round(sum(l.qty * l.prices[BASELINE] for l in LINES), 2)


# ---------------------------------------------------------------- значки
# Штриховые, 24×24, один стиль. Эмодзи в макетах нет: они не перекрашиваются
# и в экспорте выглядят чужими.
_PATHS = {
    "home": '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-5h-6v5H5a1 1 0 0 1-1-1z"/>',
    "cart": '<path d="M3 5h2.2l1.9 10.2a1.5 1.5 0 0 0 1.5 1.2h8.3a1.5 1.5 0 0 0 1.5-1.2L20 8H6.2"/>'
            '<circle cx="9.5" cy="19.5" r="1.2"/><circle cx="17" cy="19.5" r="1.2"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "link": '<path d="M9 7H6.5a4.5 4.5 0 0 0 0 9H9"/><path d="M15 7h2.5a4.5 4.5 0 0 1 0 9H15"/><path d="M8.5 11.5h7"/>',
    "pin": '<path d="M12 21s6.5-6.1 6.5-10.5A6.5 6.5 0 0 0 5.5 10.5C5.5 14.9 12 21 12 21z"/><circle cx="12" cy="10.5" r="2.4"/>',
    "receipt": '<path d="M6 3h12v18l-2.5-1.6L13 21l-2.5-1.6L8 21l-2-1.4z"/><path d="M9 8h6"/><path d="M9 12h6"/>',
    "card": '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M3 10.5h18"/><path d="M7 15h3"/>',
    "bag": '<path d="M6 8h12l-1 11.5a1.5 1.5 0 0 1-1.5 1.4h-9A1.5 1.5 0 0 1 5 19.5z"/><path d="M9 8V6.2A3 3 0 0 1 15 6.2V8"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7"/>',
    "right": '<path d="m9 5 7 7-7 7"/>',
    "down": '<path d="m5 9 7 7 7-7"/>',
    "up": '<path d="m5 15 7-7 7 7"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
    "repeat": '<path d="M4 11a8 8 0 0 1 13.4-5.9L20 7"/><path d="M20 3v4.5h-4.5"/>'
              '<path d="M20 13a8 8 0 0 1-13.4 5.9L4 17"/><path d="M4 21v-4.5h4.5"/>',
    "spark": '<path d="M12 4.5 13.6 9l4.4 1.6L13.6 12l-1.6 4.5L10.4 12 6 10.6 10.4 9z"/>'
             '<path d="M18 16.5 18.7 18.4 20.5 19 18.7 19.7 18 21.5 17.3 19.7 15.5 19 17.3 18.4z"/>',
    "wallet": '<path d="M4 8.5A2.5 2.5 0 0 1 6.5 6H18v3"/><rect x="4" y="8.5" width="16" height="11" rx="2.2"/>'
              '<circle cx="16" cy="14" r="1.2"/>',
    "user": '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    "split": '<path d="M4 7h4l4 10h8"/><path d="M4 17h4l2-5"/><path d="m18 3 3 4-3 4"/><path d="m18 13 3 4-3 4"/>',
    "one": '<path d="M4 12h12"/><path d="m13 7 5 5-5 5"/>',
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 1.8"/>',
    "trash": '<path d="M5 7h14"/><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7"/>'
             '<path d="M7 7l1 12.5a1.5 1.5 0 0 0 1.5 1.4h5A1.5 1.5 0 0 0 16 19.5L17 7"/>',
}


def icon(name: str, size: int = 20, stroke: float = 1.7, color: str = "currentColor") -> str:
    body = _PATHS[name]
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" '
            f'style="flex:none;">{body}</svg>')


# ---------------------------------------------------------------- концепты
@dataclass
class Theme:
    num: int
    key: str
    name: str
    idea: str                  # одна строка: в чём направление
    tradeoff: str              # чем за это платим
    fonts: str                 # ссылка на Google Fonts
    ui: str                    # семейство интерфейса
    display: str               # семейство заголовков и сумм
    mono: str = ""             # для цифр, если направление на них опирается
    dark: bool = False
    bg: str = "#FBF7F1"
    surface: str = "#FFFFFF"
    tint: str = "#F5EFE6"
    ink: str = "#2A241E"
    ink2: str = "#6E6459"
    ink3: str = "#9C9288"
    line: str = "#EAE2D6"
    line2: str = "#DACFBF"
    accent: str = "#C2622C"
    accent_ink: str = "#FFFFFF"
    accent_soft: str = "#FBEDE2"
    good: str = "#2E7D51"
    good_soft: str = "#E8F3EC"
    ok: str = ""               # цвет «связан»; пусто — берём цвет выгоды
    head_bg: str = ""          # пусто — шапка в цвете поверхности
    head_ink: str = ""
    r_card: int = 16
    r_btn: int = 12
    r_chip: int = 999
    r_tile: int = 16
    shadow: str = "0 1px 2px rgba(42,36,30,0.04)"
    head: str = "plain"        # plain | dark | big
    nav: str = "tab"           # tab | pill | top | none
    row: str = "line"          # line | card | receipt | table | tile
    tiles: str = "soft"        # soft | square | mono | chip
    modes: str = "twobtn"      # twobtn | segment | cards | sheet
    savings: str = "strip"     # strip | block | ring | ticker
    upper: bool = False        # надзаголовки капителью
    caps_ls: str = "0.12em"

    def __post_init__(self) -> None:
        # семейство шрифта уходит в inline-стиль внутри двойных кавычек атрибута:
        # двойная кавычка в имени обрывает style="…", и весь стиль молча пропадает
        self.ui = self.ui.replace('"', "'")
        self.display = self.display.replace('"', "'")
        self.mono = self.mono.replace('"', "'")

    @property
    def headbg(self) -> str:
        return self.head_bg or self.surface

    @property
    def headink(self) -> str:
        return self.head_ink or self.ink

    @property
    def ok_color(self) -> str:
        return self.ok or self.good

    @property
    def num_family(self) -> str:
        """Чем набраны суммы: моноширинный, если он у направления есть."""
        return self.mono or self.display


G = "https://fonts.googleapis.com/css2?"

THEMES: list[Theme] = [
    Theme(1, "Paper", "Тёплая бумага",
          "Развитие нынешней темы приложения: бежевая бумага, засечные суммы, спокойные строки.",
          "Ничего не кричит — выгоду приходится читать, а не видеть с порога.",
          G + "family=Golos+Text:wght@400;500;600;700&family=Literata:opsz,wght@7..72,400;7..72,600;7..72,700&display=swap",
          ui='"Golos Text",system-ui,sans-serif', display='"Literata",Georgia,serif',
          head="plain", nav="tab", row="line", tiles="soft", modes="twobtn", savings="strip"),

    Theme(2, "Night", "Ночной счёт",
          "Тёмный финтех: итог крупными цифрами в шапке, позиции карточками, акцент лаймовый.",
          "Продуктовый список на чёрном читается как выписка по счёту, а не как еда.",
          G + "family=Manrope:wght@400;500;600;800&family=JetBrains+Mono:wght@400;600;700&display=swap",
          ui='"Manrope",system-ui,sans-serif', display='"Manrope",system-ui,sans-serif',
          mono='"JetBrains Mono",ui-monospace,monospace', dark=True,
          bg="#0E1116", surface="#171B22", tint="#1F242D", ink="#F2F4F7", ink2="#A3ACBA", ink3="#6B7481",
          line="#262C36", line2="#333B47", accent="#C6F24E", accent_ink="#10141A", accent_soft="#1E2A12",
          good="#9BE36D", good_soft="#17251A", head_bg="#0E1116", head_ink="#F2F4F7",
          r_card=18, r_btn=14, r_tile=18, shadow="0 1px 0 rgba(255,255,255,0.03)",
          head="dark", nav="tab", row="card", tiles="square", modes="segment", savings="block"),

    Theme(3, "Receipt", "Кассовый чек",
          "Всё как лента чека: моноширинные цены, пунктир между названием и суммой, печать итога.",
          "Узнаваемо, но однообразно: одни и те же строки на семи экранах подряд.",
          G + "family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap",
          ui='"IBM Plex Sans",system-ui,sans-serif', display='"IBM Plex Mono",ui-monospace,monospace',
          mono='"IBM Plex Mono",ui-monospace,monospace',
          bg="#F4F2ED", surface="#FFFFFF", tint="#EDEAE3", ink="#1B1B19", ink2="#5C5A54", ink3="#8D8A82",
          line="#DFDBD2", line2="#C9C4B8", accent="#1B1B19", accent_ink="#FFFFFF", accent_soft="#E6E3DA",
          good="#1F6B45", good_soft="#E4EFE8",
          r_card=4, r_btn=4, r_chip=4, r_tile=4, shadow="none",
          head="big", nav="top", row="receipt", tiles="mono", modes="cards", savings="ticker", upper=True),

    Theme(4, "Thumb", "Крупный палец",
          "Одна рука, крупные цели: большие кнопки, минимум подписей, важное задано размером.",
          "Экран вмещает вдвое меньше: за длинной корзиной придётся листать.",
          G + "family=Onest:wght@400;500;600;800&display=swap",
          ui='"Onest",system-ui,sans-serif', display='"Onest",system-ui,sans-serif',
          bg="#F6F5F2", surface="#FFFFFF", tint="#EFEDE8", ink="#181A17", ink2="#5E625B", ink3="#94988F",
          line="#E4E2DB", line2="#CFCCC2", accent="#2F6B3C", accent_ink="#FFFFFF", accent_soft="#E6F0E7",
          good="#2F6B3C", good_soft="#E6F0E7",
          r_card=22, r_btn=20, r_tile=22, shadow="0 2px 10px rgba(24,26,23,0.05)",
          head="big", nav="pill", row="tile", tiles="square", modes="twobtn", savings="block"),

    Theme(5, "Dense", "Плотная таблица",
          "Режим считающего: цены всех сетей прямо в строке, компактная сетка, ничего лишнего.",
          "Требует внимания — это интерфейс для того, кто любит таблицы.",
          G + "family=PT+Sans:wght@400;700&family=PT+Mono&display=swap",
          ui='"PT Sans",system-ui,sans-serif', display='"PT Sans",system-ui,sans-serif',
          mono='"PT Mono",ui-monospace,monospace',
          bg="#F2F4F6", surface="#FFFFFF", tint="#E9EDF1", ink="#16202B", ink2="#4E5B69", ink3="#8794A1",
          line="#DDE3E9", line2="#C4CDD6", accent="#1F5FA8", accent_ink="#FFFFFF", accent_soft="#E5EEF8",
          good="#15704D", good_soft="#E3F1EB",
          r_card=8, r_btn=6, r_chip=6, r_tile=8, shadow="0 1px 2px rgba(22,32,43,0.05)",
          head="plain", nav="top", row="table", tiles="chip", modes="segment", savings="strip"),

    Theme(6, "Steps", "Мастер шагов",
          "Настройка как последовательность: полоса прогресса, крупные номера, один шаг в фокусе.",
          "Хорошо в первый день и мешает на сотый: шаги никуда не деваются.",
          G + "family=Commissioner:wght@400;500;600;700&family=Alegreya:wght@500;700&display=swap",
          ui='"Commissioner",system-ui,sans-serif', display='"Alegreya",Georgia,serif',
          bg="#FAF9F6", surface="#FFFFFF", tint="#F0F2EC", ink="#1F231D", ink2="#5B6154", ink3="#8F958A",
          line="#E6E8E0", line2="#CBD0C2", accent="#4A7C3F", accent_ink="#FFFFFF", accent_soft="#EAF1E7",
          good="#4A7C3F", good_soft="#EAF1E7",
          r_card=14, r_btn=10, r_tile=14, shadow="0 1px 3px rgba(31,35,29,0.05)",
          head="plain", nav="none", row="card", tiles="soft", modes="cards", savings="ring"),

    Theme(7, "Brand", "Цвет магазина",
          "Цвет сети ведёт весь интерфейс: полоса у строки, кружок у цены, вкладка у корзины.",
          "Шесть фирменных цветов спорят между собой — палитра получается не наша, а чужая.",
          G + "family=Rubik:wght@400;500;600;700&family=Unbounded:wght@500;700&display=swap",
          ui='"Rubik",system-ui,sans-serif', display='"Unbounded",system-ui,sans-serif',
          bg="#FFFFFF", surface="#FFFFFF", tint="#F4F5F7", ink="#14161A", ink2="#565C66", ink3="#8B929C",
          line="#E7E9ED", line2="#D2D6DD", accent="#14161A", accent_ink="#FFFFFF", accent_soft="#F1F2F4",
          good="#1E7A4B", good_soft="#E6F3EC",
          r_card=14, r_btn=10, r_tile=14, shadow="0 1px 2px rgba(20,22,26,0.05)",
          head="plain", nav="tab", row="line", tiles="square", modes="twobtn", savings="block"),

    Theme(8, "Soft", "Мягкий пастельный",
          "Дружелюбный домашний вид: крупные скругления, воздух, пастель, ноль таблиц.",
          "Воздуха много, данных мало: длинные списки становятся ещё длиннее.",
          G + "family=Nunito:wght@400;600;700;800&family=Kurale&display=swap",
          ui='"Nunito",system-ui,sans-serif', display='"Kurale",Georgia,serif',
          bg="#FBF6F4", surface="#FFFFFF", tint="#F6EEEC", ink="#2B2427", ink2="#6B5F63", ink3="#9E9296",
          line="#F0E4E1", line2="#E0CFCB", accent="#C25E72", accent_ink="#FFFFFF", accent_soft="#FBEAEE",
          good="#41866B", good_soft="#E7F2EE",
          r_card=26, r_btn=22, r_tile=26, shadow="0 6px 18px rgba(43,36,39,0.06)",
          head="big", nav="pill", row="card", tiles="soft", modes="cards", savings="ring"),

    Theme(9, "Swiss", "Швейцарская сетка",
          "Чёрным по белому: сетка, линии, ноль скруглений, один красный акцент на выгоде.",
          "Строго до сухости — приложение про еду выглядит как отчёт.",
          G + "family=Montserrat:wght@400;500;600;800&display=swap",
          ui='"Montserrat",system-ui,sans-serif', display='"Montserrat",system-ui,sans-serif',
          bg="#FFFFFF", surface="#FFFFFF", tint="#F2F2F2", ink="#0B0B0B", ink2="#4D4D4D", ink3="#8A8A8A",
          line="#E0E0E0", line2="#BDBDBD", accent="#0B0B0B", accent_ink="#FFFFFF", accent_soft="#F2F2F2",
          good="#C8102E", good_soft="#FBE9EC",
          r_card=0, r_btn=0, r_chip=0, r_tile=0, shadow="none",
          ok="#0B0B0B",
          head="plain", nav="top", row="table", tiles="mono", modes="segment", savings="ticker",
          upper=True, caps_ls="0.16em"),

    Theme(10, "Sheet", "Шторки снизу",
          "Тёмная шапка с итогом, всё остальное — в шторках снизу и плавающей кнопке.",
          "Половина интерфейса спрятана: что умеет экран, видно не сразу.",
          G + "family=Manrope:wght@400;500;600;800&family=Oswald:wght@500;600&display=swap",
          ui='"Manrope",system-ui,sans-serif', display='"Oswald",system-ui,sans-serif',
          bg="#EEF0F3", surface="#FFFFFF", tint="#E3E7EC", ink="#111418", ink2="#4F5761", ink3="#858D97",
          line="#DCE0E6", line2="#C2C8D0", accent="#1B2733", accent_ink="#FFFFFF", accent_soft="#E5E9EF",
          good="#0F7A52", good_soft="#E2F1EA", head_bg="#1B2733", head_ink="#FFFFFF",
          r_card=18, r_btn=14, r_tile=18, shadow="0 8px 24px rgba(17,20,24,0.08)",
          head="dark", nav="pill", row="card", tiles="soft", modes="sheet", savings="block"),
]


# ---------------------------------------------------------------- кирпичи экрана
# Всё рисуется inline-стилями: так каждый прямоугольник в канве можно выделить
# и перекрасить, не трогая общую таблицу стилей.

TABS = [("home", "Настройка"), ("cart", "Корзина"), ("grid", "Каталог"), ("link", "Связи")]


def fnt(weight: int, size: float, height: float = 1.35, family: str = "") -> str:
    fam = family or "inherit"
    return f"font:{weight} {size}px/{height} {fam};" if family else f"font-weight:{weight};font-size:{size}px;line-height:{height};"


def eyebrow(t: Theme, text: str, color: str = "") -> str:
    color = color or t.ink3
    return (f'<div style="{fnt(600, 10.5, 1)}letter-spacing:{t.caps_ls};text-transform:uppercase;'
            f'color:{color};">{text}</div>')


def sep(t: Theme, color: str = "") -> str:
    return f'<div style="height:1px;background:{color or t.line};flex:none;"></div>'


def store_dot(code: str, size: int = 8) -> str:
    color = STORES[code][3]
    return (f'<span style="width:{size}px;height:{size}px;border-radius:999px;background:{color};'
            f'flex:none;display:block;"></span>')


def money(t: Theme, value: float, size: float = 15, weight: int = 600, color: str = "",
          kop: bool = True, family: str = "") -> str:
    fam = family or t.num_family
    return (f'<span style="font-family:{fam};{fnt(weight, size, 1.15)}color:{color or t.ink};'
            f'white-space:nowrap;">{rub(value, kop)}</span>')


def card(t: Theme, inner: str, pad: str = "14px", extra: str = "", bg: str = "") -> str:
    return (f'<div style="display:flex;flex-direction:column;background:{bg or t.surface};'
            f'border:1px solid {t.line};border-radius:{t.r_card}px;padding:{pad};'
            f'box-shadow:{t.shadow};{extra}">{inner}</div>')


def chip(t: Theme, text: str, fg: str = "", bg: str = "", border: str = "", pad: str = "5px 10px",
         size: float = 11.5, weight: int = 500) -> str:
    return (f'<span style="display:inline-flex;align-items:center;gap:5px;padding:{pad};'
            f'border-radius:{t.r_chip}px;background:{bg or t.tint};color:{fg or t.ink2};'
            f'border:1px solid {border or "transparent"};{fnt(weight, size, 1.1)}white-space:nowrap;">{text}</span>')


def btn(t: Theme, label: str, kind: str = "primary", ic: str = "", height: int = 48,
        size: float = 15, weight: int = 600, sub: str = "") -> str:
    """Кнопка во всю ширину. Ниже 44 px не опускаемся — палец в макете такой же, как в жизни."""
    if kind == "primary":
        bg, fg, bd = t.accent, t.accent_ink, t.accent
    elif kind == "soft":
        bg, fg, bd = t.accent_soft, t.accent if not t.dark else t.ink, "transparent"
    elif kind == "good":
        bg, fg, bd = t.good, "#FFFFFF" if not t.dark else t.bg, t.good
    else:  # ghost
        bg, fg, bd = "transparent", t.ink, t.line2
    inner = f'{icon(ic, 18, color=fg) if ic else ""}<span>{label}</span>'
    if sub:
        inner = (f'<div style="display:flex;flex-direction:column;align-items:center;gap:2px;">'
                 f'<span>{label}</span>'
                 f'<span style="{fnt(500, 11.5, 1.1)}opacity:0.75;">{sub}</span></div>')
        height += 12
    return (f'<div style="display:flex;align-items:center;justify-content:center;gap:8px;'
            f'min-height:{height}px;padding:0 16px;border-radius:{t.r_btn}px;background:{bg};'
            f'border:1px solid {bd};color:{fg};{fnt(weight, size, 1.2)}text-align:center;">{inner}</div>')


def head_bar(t: Theme, title: str, sub: str = "", eyeb: str = "", right: str = "",
             back: bool = False, extra_rows: str = "") -> str:
    """Шапка экрана. Три подачи: плашка, тёмная плашка, крупный заголовок без линии."""
    dark = t.head == "dark"
    bg = t.headbg if dark else t.surface
    ink = t.headink if dark else t.ink
    ink2 = t.ink2 if not dark else (t.ink3 if t.dark else "rgba(255,255,255,0.7)")
    big = t.head == "big"

    left = ""
    if back:
        left = (f'<div style="display:flex;align-items:center;justify-content:center;width:34px;height:34px;'
                f'border-radius:{t.r_chip}px;border:1px solid {t.line if not dark else "rgba(255,255,255,0.18)"};'
                f'color:{ink};transform:rotate(180deg);">{icon("right", 16, color=ink)}</div>')

    if big:
        title_html = (f'<div style="display:flex;flex-direction:column;gap:4px;">'
                      f'{eyebrow(t, eyeb) if eyeb else ""}'
                      f'<div style="font-family:{t.display};{fnt(700, 24, 1.1)}color:{ink};'
                      f'letter-spacing:-0.02em;">{title}</div>'
                      f'{f"<div style=\"{fnt(400, 12.5, 1.3)}color:{ink2};\">{sub}</div>" if sub else ""}</div>')
        return (f'<div style="flex:none;display:flex;flex-direction:column;gap:10px;padding:14px 16px 12px;'
                f'background:{bg};">'
                f'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">'
                f'{f"<div style=\"display:flex;gap:10px;align-items:center;\">{left}{title_html}</div>" if back else title_html}'
                f'{right}</div>{extra_rows}</div>')

    title_html = (f'<div style="display:flex;flex-direction:column;gap:1px;min-width:0;">'
                  f'<div style="font-family:{t.display};{fnt(600, 17, 1.2)}color:{ink};letter-spacing:-0.01em;">{title}</div>'
                  f'{f"<div style=\"{fnt(400, 11.5, 1.25)}color:{ink2};\">{sub}</div>" if sub else ""}</div>')
    return (f'<div style="flex:none;display:flex;flex-direction:column;gap:10px;padding:12px 16px 12px;'
            f'background:{bg};border-bottom:1px solid {t.line if not dark else "transparent"};">'
            f'<div style="display:flex;align-items:center;gap:10px;min-height:38px;">'
            f'{left}{title_html}<div style="flex:1;"></div>{right}</div>{extra_rows}</div>')


def topnav(t: Theme, active: str) -> str:
    """Разделы строкой под шапкой — для направлений без нижней панели."""
    items = []
    for key, label in TABS:
        on = key == active
        items.append(
            f'<div style="display:flex;align-items:center;gap:6px;padding:8px 12px;min-height:36px;'
            f'border-radius:{t.r_chip}px;background:{t.accent if on else "transparent"};'
            f'border:1px solid {t.accent if on else t.line};'
            f'color:{t.accent_ink if on else t.ink2};{fnt(600 if on else 500, 12.5, 1)}white-space:nowrap;">'
            f'{icon(key, 15, color=t.accent_ink if on else t.ink2)}{label}</div>')
    return (f'<div style="flex:none;display:flex;gap:7px;padding:10px 16px;background:{t.surface};'
            f'border-bottom:1px solid {t.line};overflow:hidden;">{"".join(items)}</div>')


def tabbar(t: Theme, active: str) -> str:
    """Нижняя навигация: панель во всю ширину или плавающая пилюля."""
    if t.nav in ("top", "none"):
        return ""
    floating = t.nav == "pill"
    items = []
    for key, label in TABS:
        on = key == active
        color = t.accent if on else t.ink3
        if floating:
            items.append(
                f'<div style="display:flex;align-items:center;gap:7px;min-height:44px;padding:0 14px;'
                f'border-radius:{t.r_chip}px;background:{t.accent if on else "transparent"};'
                f'color:{t.accent_ink if on else t.ink2};{fnt(600, 12.5, 1)}">'
                f'{icon(key, 18, color=t.accent_ink if on else t.ink2)}'
                f'{f"<span>{label}</span>" if on else ""}</div>')
        else:
            items.append(
                f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;'
                f'min-height:48px;justify-content:center;color:{color};">'
                f'{icon(key, 21, stroke=1.9 if on else 1.6, color=color)}'
                f'<span style="{fnt(600 if on else 500, 10.5, 1)}">{label}</span></div>')
    if floating:
        return (f'<div style="position:absolute;left:16px;right:16px;bottom:16px;display:flex;'
                f'align-items:center;justify-content:space-between;gap:4px;padding:5px;'
                f'border-radius:{t.r_chip}px;background:{t.surface};border:1px solid {t.line};'
                f'box-shadow:0 10px 26px rgba(0,0,0,0.12);">{"".join(items)}</div>')
    return (f'<div style="flex:none;display:flex;align-items:stretch;gap:2px;padding:8px 8px 14px;'
            f'background:{t.surface};border-top:1px solid {t.line};">{"".join(items)}</div>')


def body(t: Theme, inner: str, pad: str = "", gap: int = 12, bg: str = "") -> str:
    """Прокручиваемая середина экрана. Лишнее по высоте просто обрезается рамкой."""
    # плавающая панель висит поверх содержимого — под неё оставляем место
    pad = pad or (f"14px 16px {78 if t.nav == "pill" else 14}px")
    return (f'<div style="flex:1;display:flex;flex-direction:column;gap:{gap}px;padding:{pad};'
            f'background:{bg or t.bg};overflow:hidden;">{inner}</div>')


def phone(t: Theme, inner: str, label: str, note: str) -> str:
    """Экран 390×844 в рамке + подпись над ним."""
    return (f'<div style="display:flex;flex-direction:column;gap:11px;width:{PHONE_W}px;flex:none;">'
            f'<div style="display:flex;flex-direction:column;gap:3px;">'
            f'{eyebrow(t, label, t.ink3)}'
            f'<div style="{fnt(500, 12.5, 1.3)}color:{t.ink2};font-family:{t.ui};">{note}</div></div>'
            f'<div style="position:relative;width:{PHONE_W}px;height:{PHONE_H}px;border-radius:30px;'
            f'background:{t.bg};border:1px solid {t.line2};overflow:hidden;display:flex;'
            f'flex-direction:column;box-shadow:0 18px 40px -28px rgba(0,0,0,0.45);">{inner}</div></div>')


def fab(t: Theme, label: str, ic: str = "cart") -> str:
    """Плавающая кнопка — для направления со шторками."""
    return (f'<div style="position:absolute;right:16px;bottom:88px;display:flex;align-items:center;gap:8px;'
            f'min-height:52px;padding:0 20px;border-radius:{t.r_chip}px;background:{t.accent};'
            f'color:{t.accent_ink};box-shadow:0 12px 26px rgba(0,0,0,0.22);{fnt(600, 14.5, 1)}">'
            f'{icon(ic, 19, color=t.accent_ink)}{label}</div>')


def progress(t: Theme, done: int, total: int) -> str:
    pct = int(done / total * 100)
    return (f'<div style="display:flex;flex-direction:column;gap:6px;">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;">'
            f'<span style="{fnt(600, 12, 1.1)}color:{t.ink2};">Шаг {done} из {total}</span>'
            f'<span style="{fnt(500, 12, 1.1)}color:{t.ink3};">осталось {total - done}</span></div>'
            f'<div style="height:6px;border-radius:999px;background:{t.tint};overflow:hidden;">'
            f'<div style="width:{pct}%;height:100%;background:{t.accent};border-radius:999px;"></div></div></div>')


def dotted(t: Theme) -> str:
    """Отточие чека: заполняет место между названием и ценой."""
    return (f'<div style="flex:1;min-width:10px;height:1px;align-self:flex-end;margin-bottom:5px;'
            f'border-bottom:1px dotted {t.line2};"></div>')


def section(t: Theme, title: str, inner: str, note: str = "", step: int = 0, action: str = "") -> str:
    """Блок настройки: номер шага там, где направление на шаги опирается."""
    num = ""
    if step:
        square = t.nav == "none" or t.head == "big"
        num = (f'<div style="display:flex;align-items:center;justify-content:center;width:26px;height:26px;'
               f'flex:none;border-radius:{8 if square else 999}px;background:{t.accent};color:{t.accent_ink};'
               f'font-family:{t.display};{fnt(700, 13, 1)}">{step}</div>')
    head = (f'<div style="display:flex;align-items:center;gap:9px;">{num}'
            f'<div style="display:flex;flex-direction:column;gap:1px;min-width:0;">'
            f'<div style="font-family:{t.display};{fnt(600, 15, 1.2)}color:{t.ink};">{title}</div>'
            f'{f"<div style=\"{fnt(400, 11.5, 1.3)}color:{t.ink3};\">{note}</div>" if note else ""}</div>'
            f'<div style="flex:1;"></div>{action}</div>')
    return f'<div style="display:flex;flex-direction:column;gap:8px;">{head}{inner}</div>'


# ---------------------------------------------------------------- витрина макетов
# Адрес и телефон — демонстрационные. История: первый чек настоящий (data/seed_receipt.csv),
# второй и третий собраны из тех же позиций по ценам соответствующей сети, чтобы суммы
# в макете нельзя было опровергнуть калькулятором.
ADDRESS = "Москва, Ленинский просп., 42"
PHONE_HINT = "+7 (9··) ···-··-21"

CONNECTED = {"magnit", "vkusvill", "pyaterochka"}

CARDS = [
    ("Ромашка-Банк", "Карта Плюс", "5 % Магнит", True),
    ("Вектор-Банк", "Кэшбэк Про", "10 % ВкусВилл", True),
    ("Север-Банк", "Повседневная", "3 % Пятёрочка", True),
]


def _receipt(date: str, store: str, count: int, offset: int = 0) -> dict:
    picked = LINES[offset:offset + count]
    total = round(sum(l.qty * l.prices[store] for l in picked), 2)
    return {"date": date, "store": store, "lines": picked, "total": total}


RECEIPTS = [
    {"date": "16 августа", "store": BASELINE, "lines": LINES, "total": RECEIPT_TOTAL},
    _receipt("9 августа", "magnit", 7, 2),
    _receipt("2 августа", "vkusvill", 5, 6),
]

CATEGORIES = ["Молочное", "Сыры", "Овощи", "Фрукты", "Рыба", "Колбасы", "Консервы", "Бакалея",
              "Детское питание", "Бытовая химия"]


# ---------------------------------------------------------------- плитки
def store_tiles(t: Theme, codes: list[str] | None = None) -> str:
    """Сети плитками по три в ряд: подключённые с галочкой, остальные зовут войти."""
    codes = codes or SHOP_ORDER
    cells = []
    for code in codes:
        name, _, _, color = STORES[code]
        on = code in CONNECTED
        mark = (f'<span style="display:inline-flex;align-items:center;gap:4px;{fnt(600, 10.5, 1)}'
                f'color:{t.ok_color};">{icon("check", 12, 2.2, t.ok_color)}связан</span>' if on else
                f'<span style="{fnt(500, 10.5, 1)}color:{t.ink3};">войти</span>')

        if t.tiles == "square":
            badge = (f'<div style="width:30px;height:30px;border-radius:{max(t.r_tile - 8, 0)}px;background:{color};'
                     f'display:flex;align-items:center;justify-content:center;color:#fff;'
                     f'font-family:{t.display};{fnt(700, 15, 1)}">{name[0]}</div>')
        elif t.tiles == "mono":
            badge = (f'<div style="width:30px;height:30px;border:1.5px solid {color};display:flex;'
                     f'align-items:center;justify-content:center;color:{color};{fnt(600, 14, 1)}">{name[0]}</div>')
        elif t.tiles == "chip":
            badge = store_dot(code, 10)
        else:
            badge = (f'<div style="width:30px;height:30px;border-radius:999px;background:{color}1F;'
                     f'display:flex;align-items:center;justify-content:center;color:{color};'
                     f'font-family:{t.display};{fnt(700, 15, 1)}">{name[0]}</div>')

        pad = "8px 7px" if t.tiles == "chip" else "10px 9px"
        cells.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:6px;padding:{pad};'
            f'min-height:{60 if t.tiles == "chip" else 76}px;justify-content:center;'
            f'border-radius:{t.r_tile}px;border:1px solid {t.accent if on and t.tiles == "mono" else t.line};'
            f'background:{t.surface if on else t.bg};box-shadow:{t.shadow if on else "none"};">'
            f'{badge}<div style="{fnt(600, 11.5, 1.15)}color:{t.ink};text-align:center;">{name}</div>{mark}</div>')
    return (f'<div style="display:grid;grid-template-columns:repeat(3, minmax(0, 1fr));gap:8px;">'
            f'{"".join(cells)}</div>')


def card_tiles(t: Theme) -> str:
    """Карты — такими же квадратиками: отмеченные участвуют в расчёте."""
    cells = []
    for bank, name, gain, on in CARDS:
        cells.append(
            f'<div style="display:flex;flex-direction:column;gap:6px;padding:9px;min-height:76px;'
            f'border-radius:{t.r_tile}px;border:1px solid {t.accent if on else t.line};'
            f'background:{t.surface};box-shadow:{t.shadow};">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;">'
            f'{icon("card", 16, color=t.ink2)}'
            f'{icon("check", 14, 2.2, t.ok_color) if on else icon("plus", 14, 2, t.ink3)}</div>'
            f'<div style="display:flex;flex-direction:column;gap:1px;">'
            f'<div style="{fnt(600, 11.5, 1.15)}color:{t.ink};">{name}</div>'
            f'<div style="{fnt(400, 10.5, 1.2)}color:{t.ink3};">{bank}</div></div>'
            f'<div style="{fnt(600, 10.5, 1.1)}color:{t.good};">{gain}</div></div>')
    # четвёртая плитка увела бы сетку на второй ряд, а он на этом экране не помещается:
    # добавление карты живёт действием в заголовке блока
    return (f'<div style="display:grid;grid-template-columns:repeat(3, minmax(0, 1fr));gap:8px;">'
            f'{"".join(cells)}</div>')


# ---------------------------------------------------------------- 1. настройка
def screen_setup(t: Theme) -> str:
    steps = t.nav == "none"          # мастер ведёт по шагам, остальные показывают всё сразу

    about = card(
        t,
        f'<div style="display:flex;flex-direction:column;gap:5px;">'
        f'<div style="font-family:{t.display};{fnt(700, 16.5, 1.2)}color:{t.ink};letter-spacing:-0.015em;">'
        f'Те же продукты, меньше денег</div>'
        f'<div style="{fnt(400, 12, 1.35)}color:{t.ink2};">Читаем чеки и собираем корзину там, '
        f'где дешевле.</div></div>',
        pad="11px 12px", bg=t.accent_soft if not t.dark else t.surface)

    address = card(
        t,
        f'<div style="display:flex;align-items:center;gap:10px;">'
        f'{icon("pin", 19, color=t.accent)}'
        f'<div style="display:flex;flex-direction:column;gap:1px;min-width:0;flex:1;">'
        f'<div style="{fnt(600, 13, 1.25)}color:{t.ink};">{ADDRESS}</div>'
        f'<div style="{fnt(400, 11, 1.2)}color:{t.ink3};">сюда возят 6 сетей из 6</div></div>'
        f'{chip(t, "изменить", t.ink2, t.tint, pad="5px 10px", size=11)}</div>', pad="10px 12px")

    # поле и кнопка в одной строке: вертикально на этом экране места нет
    fns = card(
        t,
        f'<div style="display:flex;flex-direction:column;gap:8px;">'
        f'<div style="display:flex;align-items:center;gap:9px;">{icon("receipt", 18, color=t.accent)}'
        f'<div style="{fnt(600, 13, 1.2)}color:{t.ink};flex:1;">Кабинет ФНС «Мои чеки»</div>'
        f'{chip(t, "связан", t.ok_color, t.good_soft if not t.ok else t.tint, size=11, pad="4px 9px")}</div>'
        f'<div style="display:flex;align-items:stretch;gap:7px;">'
        f'<div style="flex:1;display:flex;align-items:center;gap:8px;min-height:44px;padding:0 11px;'
        f'border-radius:{t.r_btn}px;border:1px solid {t.line2};background:{t.bg};">'
        f'{icon("user", 15, color=t.ink3)}'
        f'<span style="{fnt(500, 12.5, 1.1)}color:{t.ink2};">{PHONE_HINT}</span></div>'
        f'<div style="display:flex;align-items:center;justify-content:center;min-height:44px;padding:0 15px;'
        f'border-radius:{t.r_btn}px;background:{t.accent};color:{t.accent_ink};{fnt(600, 12.5, 1)}">Войти</div>'
        f'</div>'
        f'<div style="{fnt(400, 11, 1.3)}color:{t.ink3};">Загружено 16 августа · '
        f'{len(LINES)} позиций.</div></div>', pad="10px 12px")

    numbered = steps or t.head == "big"
    blocks = [
        about,
        section(t, "Куда везём", address, "", 1 if numbered else 0),
        section(t, "Магазины", store_tiles(t), "", 2 if numbered else 0,
                action=chip(t, "все сети", t.ink3, "transparent", t.line, pad="4px 9px", size=11)),
        section(t, "Мои чеки", fns, "", 3 if numbered else 0),
        section(t, "Карты", card_tiles(t), "", 4 if numbered else 0,
                action=chip(t, "+ карта", t.ink3, "transparent", t.line, pad="4px 9px", size=11)),
    ]
    if steps:
        blocks.insert(0, progress(t, 3, 4))

    head = head_bar(t, "Моя корзина", "настройка занимает минуту", eyeb="с чего начать",
                    right=chip(t, "готово 3 из 4", t.ink2, t.tint))
    return phone(t,
                 head + (topnav(t, "home") if t.nav == "top" else "")
                 + body(t, "".join(blocks), gap=10)
                 + tabbar(t, "home"),
                 "01 · Главная", "Продукт в двух строках и четыре быстрых настройки")


# ---------------------------------------------------------------- 2. пустая корзина
def screen_empty(t: Theme) -> str:
    big_repeat = (
        f'<div style="display:flex;flex-direction:column;gap:10px;padding:16px;border-radius:{t.r_card}px;'
        f'background:{t.accent};color:{t.accent_ink};">'
        f'<div style="display:flex;align-items:center;gap:10px;">{icon("repeat", 22, color=t.accent_ink)}'
        f'<div style="font-family:{t.display};{fnt(700, 18, 1.15)}">Повторить заказ</div></div>'
        f'<div style="{fnt(400, 12.5, 1.4)}opacity:0.85;">Откроем ваши чеки: разверните любой, '
        f'отметьте нужное — корзина соберётся сама.</div>'
        f'<div style="display:flex;align-items:center;gap:8px;padding-top:2px;">'
        f'<span style="{fnt(600, 12, 1.1)}">{len(RECEIPTS)} чека загружено</span>'
        f'<span style="opacity:0.6;">·</span>'
        f'<span style="{fnt(400, 12, 1.1)}opacity:0.85;">последний 16 августа</span></div></div>')

    usual = card(
        t,
        f'<div style="display:flex;align-items:center;gap:11px;">'
        f'{icon("clock", 19, color=t.ink2)}'
        f'<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;">'
        f'<div style="{fnt(600, 13.5, 1.2)}color:{t.ink};">Как обычно</div>'
        f'<div style="{fnt(400, 11.5, 1.25)}color:{t.ink3};">средний заказ: {len(LINES)} позиций, '
        f'≈ {rub(RECEIPT_TOTAL, False)}</div></div>'
        f'<div style="display:flex;align-items:center;justify-content:center;min-height:44px;padding:0 15px;'
        f'border-radius:{t.r_btn}px;border:1px solid {t.accent};color:{t.accent};{fnt(600, 12.5, 1)}">'
        f'Собрать</div></div>', pad="11px 12px")

    empty = (
        f'<div style="display:flex;flex-direction:column;align-items:center;gap:10px;padding:26px 16px 22px;">'
        f'<div style="width:64px;height:64px;border-radius:{"999px" if t.r_card > 6 else "0"};'
        f'background:{t.tint};display:flex;align-items:center;justify-content:center;">'
        f'{icon("cart", 28, 1.6, t.ink3)}</div>'
        f'<div style="font-family:{t.display};{fnt(600, 17, 1.2)}color:{t.ink};">Корзина пуста</div>'
        f'<div style="{fnt(400, 12.5, 1.4)}color:{t.ink3};text-align:center;max-width:250px;">'
        f'Начинать с нуля не нужно: всё уже есть в ваших чеках.</div></div>')

    catalog = (
        f'<div style="display:flex;align-items:center;gap:10px;padding:13px;border-radius:{t.r_card}px;'
        f'border:1px dashed {t.line2};background:transparent;">'
        f'{icon("grid", 18, color=t.ink2)}'
        f'<div style="flex:1;{fnt(500, 13, 1.2)}color:{t.ink2};">Собрать руками из каталога</div>'
        f'{icon("right", 16, color=t.ink3)}</div>')

    hint = (f'<div style="display:flex;align-items:center;gap:7px;justify-content:center;'
            f'{fnt(400, 11.5, 1.3)}color:{t.ink3};">{icon("spark", 14, 1.6, t.ink3)}'
            f'Цены берём из трёх связанных сетей и обновляем при сборке</div>')

    head = head_bar(t, "Корзина", "пока пусто", eyeb="моя корзина")
    return phone(t,
                 head + (topnav(t, "cart") if t.nav == "top" else "")
                 + body(t, empty + big_repeat + usual + catalog + hint, gap=12)
                 + tabbar(t, "cart"),
                 "02 · Пустая корзина", "Два быстрых пути: повторить чек или «как обычно»")


# ---------------------------------------------------------------- 3. повторить заказ
def screen_repeat(t: Theme) -> str:
    cards_html = []
    for i, r in enumerate(RECEIPTS):
        name, _, _, color = STORES[r["store"]]
        opened = i == 0
        head_row = (
            f'<div style="display:flex;align-items:center;gap:10px;">'
            f'<div style="display:flex;align-items:center;justify-content:center;width:34px;height:34px;flex:none;'
            f'border-radius:{max(t.r_tile - 8, 0) if t.r_card else 0}px;background:{color}1F;color:{color};'
            f'font-family:{t.display};{fnt(700, 14, 1)}">{name[0]}</div>'
            f'<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;">'
            f'<div style="{fnt(600, 13.5, 1.2)}color:{t.ink};">{name}</div>'
            f'<div style="{fnt(400, 11.5, 1.2)}color:{t.ink3};">{r["date"]} · '
            f'{len(r["lines"])} позиций</div></div>'
            f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;">'
            f'{money(t, r["total"], 14.5, 600)}'
            f'{icon("up" if opened else "down", 16, color=t.ink3)}</div></div>')

        inner = head_row
        if opened:
            rows = []
            for line in r["lines"][:6]:
                rows.append(
                    f'<div style="display:flex;align-items:center;gap:10px;min-height:34px;">'
                    f'<div style="width:18px;height:18px;border-radius:{4 if t.r_card else 0}px;'
                    f'border:1.5px solid {t.accent};background:{t.accent};display:flex;align-items:center;'
                    f'justify-content:center;flex:none;">{icon("check", 12, 2.4, t.accent_ink)}</div>'
                    f'<div style="flex:1;min-width:0;{fnt(400, 12.5, 1.25)}color:{t.ink};'
                    f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{line.short}</div>'
                    f'<span style="{fnt(400, 11.5, 1.1)}color:{t.ink3};">{line.qty_label}</span>'
                    f'{money(t, line.qty * line.prices[r["store"]], 12.5, 500, t.ink2)}</div>')
            rest = len(r["lines"]) - 6
            inner += (
                f'{sep(t)}'
                f'<div style="display:flex;flex-direction:column;gap:2px;">{"".join(rows)}'
                f'<div style="{fnt(500, 12, 1.3)}color:{t.accent};padding-top:4px;">'
                f'показать ещё {rest} позиции</div></div>'
                f'<div style="display:flex;gap:8px;padding-top:2px;">'
                f'<div style="flex:1;">{btn(t, "Повторить весь чек", "primary", height=44, size=13.5)}</div>'
                f'<div style="flex:none;width:120px;">{btn(t, "Снять всё", "ghost", height=44, size=13.5)}</div>'
                f'</div>')

        cards_html.append(card(t, f'<div style="display:flex;flex-direction:column;gap:10px;">{inner}</div>',
                               pad="12px 13px",
                               extra=f"border-color:{t.accent};" if opened else ""))

    picked = sum(1 for _ in RECEIPTS[0]["lines"][:6])
    footer = (
        f'<div style="flex:none;display:flex;align-items:center;gap:12px;padding:12px 16px 16px;'
        f'background:{t.surface};border-top:1px solid {t.line};">'
        f'<div style="display:flex;flex-direction:column;gap:2px;flex:1;">'
        f'<span style="{fnt(400, 11.5, 1.1)}color:{t.ink3};">выбрано {picked} позиции</span>'
        f'{money(t, sum(l.qty * l.prices[RECEIPTS[0]["store"]] for l in RECEIPTS[0]["lines"][:4]), 17, 700)}</div>'
        f'<div style="flex:1;">{btn(t, "В корзину", "primary", "cart", 46, 14)}</div></div>')

    head = head_bar(t, "Повторить заказ", "чеки за последний месяц", eyeb="история", back=True)
    return phone(t,
                 head + body(t, "".join(cards_html), gap=10) + footer,
                 "03 · Повторить заказ", "Чеки разворачиваются, позиции отмечаются галочками")


# ---------------------------------------------------------------- выгода
def savings(t: Theme, compact: bool = False) -> str:
    """Сколько экономим против привычного магазина. Четыре подачи на выбор."""
    base_name = STORES[BASELINE][0]
    if t.savings == "ring":
        return (f'<div style="display:flex;align-items:center;gap:13px;padding:13px;border-radius:{t.r_card}px;'
                f'background:{t.good_soft};border:1px solid {t.good}33;">'
                f'<div style="width:56px;height:56px;border-radius:999px;flex:none;'
                f'background:conic-gradient({t.good} {GAIN_PCT * 3.6}deg, {t.good}22 0);'
                f'display:flex;align-items:center;justify-content:center;">'
                f'<div style="width:42px;height:42px;border-radius:999px;background:{t.surface};'
                f'display:flex;align-items:center;justify-content:center;font-family:{t.num_family};'
                f'{fnt(700, 13, 1)}color:{t.good};">{str(GAIN_PCT).replace(".", ",")}%</div></div>'
                f'<div style="display:flex;flex-direction:column;gap:3px;">'
                f'<span style="{fnt(400, 11.5, 1.2)}color:{t.ink2};">дешевле, чем в «{BASE_PREP}»</span>'
                f'{money(t, GAIN, 20, 700, t.good)}</div></div>')
    if t.savings == "ticker":
        return (f'<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;'
                f'padding:10px 12px;border:1.5px solid {t.good};border-radius:{t.r_card}px;">'
                f'<span style="font-family:{t.num_family};{fnt(600, 10.5, 1.1)}letter-spacing:{t.caps_ls};'
                f'text-transform:uppercase;color:{t.good};">выгода против {BASE_GEN}</span>'
                f'<span style="display:flex;align-items:baseline;gap:8px;">'
                f'{money(t, GAIN, 17, 700, t.good)}'
                f'<span style="font-family:{t.num_family};{fnt(500, 12, 1.1)}color:{t.good};">'
                f'−{str(GAIN_PCT).replace(".", ",")}%</span></span></div>')
    if t.savings == "block":
        return (f'<div style="display:flex;flex-direction:column;gap:6px;padding:14px;border-radius:{t.r_card}px;'
                f'background:{t.good_soft};border:1px solid {t.good}2E;">'
                f'<span style="{fnt(500, 11.5, 1.2)}color:{t.ink2};">дешевле, чем всё в «{BASE_PREP}»</span>'
                f'<div style="display:flex;align-items:baseline;gap:9px;">'
                f'{money(t, GAIN, 26, 700, t.good)}'
                f'<span style="{fnt(600, 13, 1.1)}color:{t.good};">−{str(GAIN_PCT).replace(".", ",")} %</span></div>'
                f'<span style="{fnt(400, 11.5, 1.3)}color:{t.ink3};">учтены доставка и кэшбэк карт</span></div>')
    return (f'<div style="display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:{t.r_chip}px;'
            f'background:{t.good_soft};">{icon("spark", 15, 1.8, t.good)}'
            f'<span style="{fnt(500, 12, 1.2)}color:{t.ink2};flex:1;">дешевле, чем в «{BASE_PREP}»</span>'
            f'{money(t, GAIN, 14.5, 700, t.good)}'
            f'<span style="{fnt(600, 12, 1.1)}color:{t.good};">−{str(GAIN_PCT).replace(".", ",")}%</span></div>')


# ---------------------------------------------------------------- строка корзины
def basket_row(t: Theme, line: Line, show_alt: bool = True) -> str:
    """Товар в корзине: цена лучшая из связанных сетей, и видно, чья она."""
    store = line.best_store
    name, _, _, color = STORES[store]
    total = line.total(store)
    others = sorted((s for s in line.prices if s != store), key=lambda s: line.prices[s])
    save = round((line.prices[others[0]] - line.prices[store]) * line.qty, 2)

    if t.row == "table":
        cells = []
        for s in PRICE_STORES:
            on = s == store
            cells.append(
                f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:2px;'
                f'padding:3px 6px;border-radius:{t.r_chip if t.r_chip < 20 else 4}px;'
                f'background:{t.good_soft if on else "transparent"};">'
                f'<span style="font-family:{t.num_family};{fnt(700 if on else 400, 12, 1.1)}'
                f'color:{t.good if on else t.ink3};">{line.prices[s]:.2f}'.replace(".", ",") + '</span></div>')
        return (f'<div style="display:grid;grid-template-columns:minmax(0,1fr) 62px 62px 62px;gap:6px;'
                f'align-items:center;padding:7px 0;border-bottom:1px solid {t.line};">'
                f'<div style="display:flex;flex-direction:column;gap:2px;min-width:0;">'
                f'<span style="{fnt(500, 12.5, 1.2)}color:{t.ink};overflow:hidden;text-overflow:ellipsis;'
                f'white-space:nowrap;">{line.short}</span>'
                f'<span style="{fnt(400, 10.5, 1.1)}color:{t.ink3};">{line.qty_label}</span></div>'
                f'{"".join(cells)}</div>')

    if t.row == "receipt":
        return (f'<div style="display:flex;flex-direction:column;gap:2px;padding:6px 0;">'
                f'<div style="display:flex;align-items:flex-end;gap:6px;">'
                f'<span style="{fnt(500, 13, 1.25)}color:{t.ink};max-width:220px;overflow:hidden;'
                f'text-overflow:ellipsis;white-space:nowrap;">{line.short}</span>'
                f'{dotted(t)}{money(t, total, 13.5, 600)}</div>'
                f'<div style="display:flex;align-items:center;gap:6px;font-family:{t.num_family};'
                f'{fnt(400, 11, 1.2)}color:{t.ink3};">{store_dot(store, 7)}'
                f'<span>{name.upper()}</span><span>{line.qty_label} × {rub(line.prices[store])}</span>'
                f'{f"<span style=\"color:{t.good};\">−{rub(save)}</span>" if save > 0 and show_alt else ""}</div></div>')

    if t.row == "tile":
        return (f'<div style="display:flex;align-items:center;gap:12px;padding:13px;border-radius:{t.r_card}px;'
                f'background:{t.surface};border:1px solid {t.line};box-shadow:{t.shadow};">'
                f'<div style="display:flex;flex-direction:column;gap:5px;flex:1;min-width:0;">'
                f'<span style="{fnt(600, 15, 1.25)}color:{t.ink};">{line.short}</span>'
                f'<div style="display:flex;align-items:center;gap:6px;">{store_dot(store, 9)}'
                f'<span style="{fnt(500, 12.5, 1.1)}color:{t.ink2};">{name}</span>'
                f'{f"<span style=\"{fnt(600, 12.5, 1.1)}color:{t.good};\">−{rub(save, False)}</span>" if save > 0 else ""}'
                f'</div></div>'
                f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:7px;">'
                f'{money(t, total, 18, 700)}'
                f'<div style="display:flex;align-items:center;gap:0;border:1px solid {t.line2};'
                f'border-radius:{t.r_btn}px;overflow:hidden;">'
                f'<div style="width:40px;height:40px;display:flex;align-items:center;justify-content:center;">'
                f'{icon("minus", 18, color=t.ink2)}</div>'
                f'<span style="min-width:34px;text-align:center;{fnt(600, 14, 1)}color:{t.ink};">'
                f'{line.qty_label.split()[0]}</span>'
                f'<div style="width:40px;height:40px;display:flex;align-items:center;justify-content:center;'
                f'background:{t.accent};color:{t.accent_ink};">{icon("plus", 18, color=t.accent_ink)}</div>'
                f'</div></div></div>')

    if t.row == "card":
        alt = ""
        if show_alt:
            alt = (f'<div style="display:flex;align-items:center;gap:6px;{fnt(400, 11, 1.2)}color:{t.ink3};">'
                   f'{store_dot(others[0], 7)}{STORES[others[0]][0]} {rub(line.prices[others[0]])}</div>')
        return (f'<div style="display:flex;align-items:center;gap:11px;padding:12px;border-radius:{t.r_card}px;'
                f'background:{t.surface};border:1px solid {t.line};box-shadow:{t.shadow};">'
                f'<div style="display:flex;flex-direction:column;gap:5px;flex:1;min-width:0;">'
                f'<span style="{fnt(600, 13.5, 1.25)}color:{t.ink};">{line.short}</span>'
                f'<div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;">'
                f'{chip(t, f"{name}", t.ink if not t.dark else t.ink, f"{color}1F", pad="3px 8px", size=11)}'
                f'<span style="{fnt(400, 11, 1.1)}color:{t.ink3};">{line.qty_label}</span>'
                f'{f"<span style=\"{fnt(600, 11, 1.1)}color:{t.good};\">выгода {rub(save, False)}</span>" if save > 0 else ""}'
                f'</div>{alt}</div>'
                f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;">'
                f'{money(t, total, 16, 700)}'
                f'<span style="{fnt(400, 10.5, 1.1)}color:{t.ink3};">{rub(line.prices[store])} / шт</span>'
                f'</div></div>')

    # line — самая спокойная подача: строка, под ней чья цена
    return (f'<div style="display:flex;align-items:center;gap:11px;padding:10px 0;'
            f'border-bottom:1px solid {t.line};">'
            f'<div style="display:flex;flex-direction:column;gap:3px;flex:1;min-width:0;">'
            f'<span style="{fnt(500, 13.5, 1.25)}color:{t.ink};overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{line.short}</span>'
            f'<div style="display:flex;align-items:center;gap:6px;">{store_dot(store, 7)}'
            f'<span style="{fnt(400, 11.5, 1.15)}color:{t.ink2};">{name}</span>'
            f'{f"<span style=\"{fnt(500, 11.5, 1.15)}color:{t.good};\">дешевле на {rub(save, False)}</span>" if save > 0 and show_alt else ""}'
            f'</div></div>'
            f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:2px;">'
            f'{money(t, total, 15, 600)}'
            f'<span style="{fnt(400, 10.5, 1.1)}color:{t.ink3};">{line.qty_label}</span></div></div>')


# ---------------------------------------------------------------- выбор режима
def mode_switch(t: Theme) -> str:
    """Как собирать: разделить между сетями или взять всё в одной."""
    split_sum, single_sum = SPLIT_TOTAL, SINGLE_TOTAL
    single_name = STORES[SINGLE_STORE][0]
    diff = round(single_sum - split_sum, 2)

    if t.modes == "segment":
        return (f'<div style="display:flex;flex-direction:column;gap:9px;">'
                f'<div style="display:flex;gap:4px;padding:4px;border-radius:{t.r_btn + 4}px;'
                f'background:{t.tint};">'
                f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;'
                f'padding:9px 6px;border-radius:{t.r_btn}px;background:{t.surface};'
                f'border:1px solid {t.accent};">'
                f'<span style="{fnt(600, 12.5, 1.1)}color:{t.ink};">Разделить</span>'
                f'{money(t, split_sum, 13.5, 700)}</div>'
                f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;'
                f'padding:9px 6px;border-radius:{t.r_btn}px;">'
                f'<span style="{fnt(500, 12.5, 1.1)}color:{t.ink2};">Один магазин</span>'
                f'{money(t, single_sum, 13.5, 600, t.ink2)}</div></div>'
                f'{btn(t, "Собрать заказ", "primary", "cart", 50, 15)}</div>')

    if t.modes == "cards":
        def option(title: str, total: float, note: str, on: bool, ic: str) -> str:
            return (f'<div style="display:flex;align-items:center;gap:11px;padding:10px 12px;'
                    f'border-radius:{t.r_card}px;border:1.5px solid {t.accent if on else t.line};'
                    f'background:{t.surface};">'
                    f'<div style="width:20px;height:20px;border-radius:999px;flex:none;'
                    f'border:2px solid {t.accent if on else t.line2};display:flex;align-items:center;'
                    f'justify-content:center;">'
                    f'{f"<span style=\"width:10px;height:10px;border-radius:999px;background:{t.accent};display:block;\"></span>" if on else ""}'
                    f'</div>'
                    f'<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;">'
                    f'<span style="{fnt(600, 13.5, 1.2)}color:{t.ink};">{title}</span>'
                    f'<span style="{fnt(400, 11.5, 1.25)}color:{t.ink3};">{note}</span></div>'
                    f'{money(t, total, 15.5, 700 if on else 600, t.ink if on else t.ink2)}</div>')
        return (f'<div style="display:flex;flex-direction:column;gap:8px;">'
                f'{option("Разделить между сетями", split_sum, "два заказа: Магнит и ВкусВилл", True, "split")}'
                f'{option("Один магазин", single_sum, f"всё в «{single_name}», на {rub(diff, False)} дороже", False, "one")}'
                f'{btn(t, "Собрать заказ", "primary", "cart", 50, 15)}</div>')

    if t.modes == "sheet":
        return (f'<div style="display:flex;flex-direction:column;gap:10px;padding:14px 16px 16px;'
                f'border-radius:{t.r_card + 6}px {t.r_card + 6}px 0 0;background:{t.surface};'
                f'border-top:1px solid {t.line};box-shadow:0 -12px 30px rgba(0,0,0,0.10);">'
                f'<div style="width:38px;height:4px;border-radius:999px;background:{t.line2};'
                f'align-self:center;"></div>'
                f'<div style="display:flex;align-items:center;justify-content:space-between;">'
                f'<span style="{fnt(600, 13.5, 1.2)}color:{t.ink};">Как собрать заказ</span>'
                f'<span style="{fnt(400, 11.5, 1.2)}color:{t.ink3};">разница {rub(diff, False)}</span></div>'
                f'<div style="display:flex;gap:8px;">'
                f'<div style="flex:1;display:flex;flex-direction:column;gap:3px;padding:11px;'
                f'border-radius:{t.r_btn}px;background:{t.accent};color:{t.accent_ink};">'
                f'<span style="{fnt(600, 12.5, 1.1)}">Разделить</span>'
                f'<span style="font-family:{t.num_family};{fnt(700, 15, 1.1)}">{rub(split_sum)}</span>'
                f'<span style="{fnt(400, 11, 1.2)}opacity:0.8;">Магнит + ВкусВилл</span></div>'
                f'<div style="flex:1;display:flex;flex-direction:column;gap:3px;padding:11px;'
                f'border-radius:{t.r_btn}px;border:1px solid {t.line2};">'
                f'<span style="{fnt(600, 12.5, 1.1)}color:{t.ink};">Один магазин</span>'
                f'<span style="font-family:{t.num_family};{fnt(700, 15, 1.1)}color:{t.ink};">{rub(single_sum)}</span>'
                f'<span style="{fnt(400, 11, 1.2)}color:{t.ink3};">{single_name}</span></div></div></div>')

    # twobtn — две крупные кнопки: выбор и есть действие
    return (f'<div style="display:flex;flex-direction:column;gap:9px;">'
            f'<div style="display:flex;align-items:center;justify-content:center;gap:10px;min-height:56px;'
            f'padding:0 16px;border-radius:{t.r_btn}px;background:{t.accent};color:{t.accent_ink};">'
            f'{icon("split", 20, color=t.accent_ink)}'
            f'<div style="display:flex;flex-direction:column;gap:1px;">'
            f'<span style="{fnt(700, 15, 1.1)}">Разделить заказ</span>'
            f'<span style="{fnt(400, 11.5, 1.1)}opacity:0.85;">Магнит + ВкусВилл</span></div>'
            f'<div style="flex:1;"></div>'
            f'<span style="font-family:{t.num_family};{fnt(700, 16, 1.1)}">{rub(split_sum)}</span></div>'
            f'<div style="display:flex;align-items:center;justify-content:center;gap:10px;min-height:56px;'
            f'padding:0 16px;border-radius:{t.r_btn}px;border:1px solid {t.line2};background:{t.surface};">'
            f'{icon("one", 20, color=t.ink2)}'
            f'<div style="display:flex;flex-direction:column;gap:1px;">'
            f'<span style="{fnt(600, 15, 1.1)}color:{t.ink};">Один магазин</span>'
            f'<span style="{fnt(400, 11.5, 1.1)}color:{t.ink3};">{single_name}, на {rub(diff, False)} дороже</span></div>'
            f'<div style="flex:1;"></div>'
            f'<span style="font-family:{t.num_family};{fnt(700, 16, 1.1)}color:{t.ink2};">{rub(single_sum)}</span></div>'
            f'</div>')


# ---------------------------------------------------------------- 4. корзина
def screen_basket(t: Theme) -> str:
    count = {"line": 8, "table": 9, "receipt": 7, "card": 5, "tile": 3}[t.row]
    # выбор режима занимает разную высоту: карточки съедают две строки списка, шторка — одну
    count -= {"cards": 2, "sheet": 1}.get(t.modes, 0) if t.row in ("card", "tile") else              {"cards": 1}.get(t.modes, 0)
    rows = "".join(basket_row(t, line) for line in LINES[:count])
    if t.row == "table":
        legend = "".join(
            f'<span style="display:inline-flex;align-items:center;gap:5px;{fnt(500, 10.5, 1)}'
            f'color:{t.ink3};">{store_dot(code, 7)}{STORES[code][0]}</span>' for code in PRICE_STORES)
        header = (f'<div style="display:flex;gap:12px;padding-bottom:8px;">{legend}</div>'
                  f'<div style="display:grid;grid-template-columns:minmax(0,1fr) 62px 62px 62px;gap:6px;'
                  f'padding-bottom:6px;border-bottom:1.5px solid {t.line2};">'
                  f'<span style="{fnt(600, 10.5, 1)}letter-spacing:{t.caps_ls};text-transform:uppercase;'
                  f'color:{t.ink3};">товар</span>'
                  + "".join(f'<span style="{fnt(700, 10.5, 1)}color:{STORES[code][3]};text-align:right;">'
                            f'{STORES[code][0][0]}</span>' for code in PRICE_STORES) + '</div>')
        rows = header + rows
    listing = card(t, f'<div style="display:flex;flex-direction:column;">{rows}'
                      f'<div style="padding-top:10px;{fnt(500, 12.5, 1.2)}color:{t.accent};">'
                      f'ещё {len(LINES) - count} позиций</div></div>',
                   pad="12px 13px") if t.row in ("line", "table", "receipt") else \
        f'<div style="display:flex;flex-direction:column;gap:9px;">{rows}' \
        f'<div style="{fnt(500, 12.5, 1.2)}color:{t.accent};padding:2px 2px 0;">' \
        f'ещё {len(LINES) - count} позиций</div></div>'

    head_right = (f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:1px;">'
                  f'{money(t, SPLIT_TOTAL, 16, 700, t.headink)}'
                  f'<span style="{fnt(400, 10.5, 1.1)}color:{t.ink3 if not t.head == "dark" else t.headink};'
                  f'opacity:{0.7 if t.head == "dark" else 1};">{len(LINES)} позиций</span></div>')
    head = head_bar(t, "Корзина", f"цены для адреса: {ADDRESS.split(',')[1].strip()}",
                    eyeb="моя корзина", right=head_right)

    foot_pad = "12px 16px 16px" if t.nav != "pill" else "12px 16px 78px"
    if t.modes == "sheet":
        foot = f'<div style="flex:none;">{mode_switch(t)}</div>'
    else:
        foot = (f'<div style="flex:none;display:flex;flex-direction:column;gap:10px;padding:{foot_pad};'
                f'background:{t.surface};border-top:1px solid {t.line};">{mode_switch(t)}</div>')

    return phone(t,
                 head + (topnav(t, "cart") if t.nav == "top" else "")
                 + body(t, savings(t) + listing, gap=12)
                 + foot,
                 "04 · Корзина", "Лучшая цена в строке, снизу — выбор способа сборки")


# ---------------------------------------------------------------- 5. сплит
def screen_split(t: Theme) -> str:
    blocks = []
    for i, code in enumerate(SPLIT_STORES):
        name, fee, free_from, color = STORES[code]
        calc = SPLIT_CALC[code]
        opened = i == 0
        split_rows = 4 if t.row in ("tile", "card") else 5
        delivery_note = ("доставка бесплатно" if calc["delivery"] == 0
                         else f"доставка {rub(calc['delivery'], False)}")
        head_row = (
            f'<div style="display:flex;align-items:center;gap:10px;">'
            f'<div style="display:flex;align-items:center;justify-content:center;width:36px;height:36px;flex:none;'
            f'border-radius:{max(t.r_tile - 6, 0)}px;background:{color};color:#fff;'
            f'font-family:{t.display};{fnt(700, 15, 1)}">{name[0]}</div>'
            f'<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;">'
            f'<span style="{fnt(600, 14, 1.2)}color:{t.ink};">{name}</span>'
            f'<span style="{fnt(400, 11.5, 1.2)}color:{t.ink3};">{len(SPLIT[code])} позиций · '
            f'{delivery_note}</span></div>'
            f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;">'
            f'{money(t, calc["total"], 15, 700)}{icon("up" if opened else "down", 16, color=t.ink3)}</div></div>')

        inner = head_row
        if opened:
            items = "".join(
                f'<div style="display:flex;align-items:center;gap:8px;min-height:30px;">'
                f'<span style="flex:1;min-width:0;{fnt(400, 12.5, 1.2)}color:{t.ink};overflow:hidden;'
                f'text-overflow:ellipsis;white-space:nowrap;">{l.short}</span>'
                f'<span style="{fnt(400, 11, 1.1)}color:{t.ink3};">{l.qty_label}</span>'
                f'{money(t, l.total(code), 12.5, 500, t.ink2)}</div>'
                for l in SPLIT[code][:split_rows])
            money_rows = (
                f'<div style="display:flex;flex-direction:column;gap:5px;">'
                f'<div style="display:flex;justify-content:space-between;{fnt(400, 12, 1.2)}color:{t.ink2};">'
                f'<span>товары</span>{money(t, calc["subtotal"], 12.5, 500, t.ink2)}</div>'
                f'<div style="display:flex;justify-content:space-between;{fnt(400, 12, 1.2)}color:{t.ink2};">'
                f'<span>доставка{"" if calc["delivery"] else f" (бесплатно от {rub(free_from, False)})"}</span>'
                f'{money(t, calc["delivery"], 12.5, 500, t.ink2)}</div>'
                f'<div style="display:flex;justify-content:space-between;{fnt(400, 12, 1.2)}color:{t.good};">'
                f'<span>кэшбэк · {calc["offer"].card if calc["offer"] else "нет карты"}</span>'
                f'<span style="font-family:{t.num_family};{fnt(600, 12.5, 1.1)}">−{rub(calc["cashback"])}</span></div></div>')
            inner += (f'{sep(t)}<div style="display:flex;flex-direction:column;gap:2px;">{items}'
                      f'<div style="{fnt(500, 12, 1.3)}color:{t.accent};padding-top:3px;">'
                      f'и ещё {len(SPLIT[code]) - split_rows} позиций</div></div>{sep(t)}{money_rows}'
                      f'{btn(t, f"Открыть {name}", "ghost", "right", 44, 13.5)}')

        blocks.append(card(t, f'<div style="display:flex;flex-direction:column;gap:11px;">{inner}</div>',
                           pad="12px 13px", extra=f"border-color:{color}55;" if opened else ""))

    switch = (f'<div style="display:flex;gap:4px;padding:4px;border-radius:{t.r_btn + 4}px;background:{t.tint};">'
              f'<div style="flex:1;text-align:center;padding:8px;border-radius:{t.r_btn}px;background:{t.surface};'
              f'border:1px solid {t.accent};{fnt(600, 12.5, 1.1)}color:{t.ink};">Сплит · {rub(SPLIT_TOTAL, False)}</div>'
              f'<div style="flex:1;text-align:center;padding:8px;border-radius:{t.r_btn}px;'
              f'{fnt(500, 12.5, 1.1)}color:{t.ink2};">Один · {rub(SINGLE_TOTAL, False)}</div></div>')

    foot_pad = "12px 16px 16px" if t.nav != "pill" else "12px 16px 78px"
    foot = (f'<div style="flex:none;display:flex;flex-direction:column;gap:9px;padding:{foot_pad};'
            f'background:{t.surface};border-top:1px solid {t.line};">'
            f'<div style="display:flex;align-items:baseline;justify-content:space-between;">'
            f'<span style="{fnt(500, 12.5, 1.2)}color:{t.ink2};">два заказа, итого</span>'
            f'{money(t, SPLIT_TOTAL, 20, 700)}</div>'
            f'{btn(t, "Оформить оба заказа", "primary", "bag", 50, 15)}</div>')

    head = head_bar(t, "Сплит корзины", f"выгода {rub(GAIN)} · −{str(GAIN_PCT).replace('.', ',')} %",
                    eyeb="как собрать", back=True)
    return phone(t,
                 head + body(t, switch + "".join(blocks), gap=10) + foot,
                 "05 · Сплит", "Корзина разошлась по сетям — каждая сворачивается")


# ---------------------------------------------------------------- 6. каталог
def screen_catalog(t: Theme) -> str:
    search = (f'<div style="display:flex;align-items:center;gap:9px;min-height:44px;padding:0 13px;'
              f'border-radius:{t.r_btn}px;background:{t.surface};border:1px solid {t.line2};">'
              f'{icon("search", 17, color=t.ink3)}'
              f'<span style="{fnt(400, 13, 1.1)}color:{t.ink3};">Поиск по 4 218 товарам</span></div>')

    chips = "".join(
        chip(t, c, t.accent_ink if i == 1 else t.ink2, t.accent if i == 1 else t.tint,
             pad="8px 12px", size=12.5, weight=600 if i == 1 else 500)
        for i, c in enumerate(CATEGORIES[:5]))
    chips_row = (f'<div style="display:flex;gap:7px;overflow:hidden;">{chips}</div>')

    def product(line: Line, opened: bool = False) -> str:
        prices = sorted(line.prices.items(), key=lambda kv: kv[1])
        best_store, best_price = prices[0]
        head_row = (
            f'<div style="display:flex;align-items:center;gap:11px;">'
            f'<div style="width:40px;height:40px;flex:none;border-radius:{max(t.r_tile - 8, 0)}px;'
            f'background:{t.tint};display:flex;align-items:center;justify-content:center;">'
            f'{icon("bag", 18, 1.6, t.ink3)}</div>'
            f'<div style="display:flex;flex-direction:column;gap:3px;flex:1;min-width:0;">'
            f'<span style="{fnt(600, 13.5, 1.2)}color:{t.ink};overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{line.short}</span>'
            f'<span style="{fnt(400, 11.5, 1.2)}color:{t.ink3};">{line.category} · в {len(line.prices)} сетях</span></div>'
            f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;">'
            f'<span style="{fnt(400, 10.5, 1.1)}color:{t.ink3};">от</span>'
            f'{money(t, best_price, 14.5, 700)}</div>'
            f'{icon("down" if not opened else "up", 16, color=t.ink3)}</div>')
        if not opened:
            return card(t, head_row, pad="11px 12px")
        rows = []
        for code, price in prices:
            name, _, _, color = STORES[code]
            first = code == best_store
            rows.append(
                f'<div style="display:flex;align-items:center;gap:9px;min-height:44px;">'
                f'{store_dot(code, 9)}'
                f'<div style="display:flex;flex-direction:column;gap:1px;flex:1;min-width:0;">'
                f'<span style="{fnt(500, 12.5, 1.15)}color:{t.ink};">{name}</span>'
                f'<span style="{fnt(400, 10.5, 1.1)}color:{t.ink3};">'
                f'{"дешевле всех" if first else "есть в наличии"}</span></div>'
                f'{money(t, price, 13.5, 700 if first else 500, t.good if first else t.ink2)}'
                f'<div style="width:34px;height:34px;border-radius:{max(t.r_btn - 2, 0)}px;flex:none;'
                f'background:{t.accent if first else "transparent"};border:1px solid '
                f'{t.accent if first else t.line2};display:flex;align-items:center;justify-content:center;">'
                f'{icon("plus", 16, 2, t.accent_ink if first else t.ink2)}</div></div>')
        rows.append(
            f'<div style="display:flex;align-items:center;gap:9px;min-height:36px;opacity:0.55;">'
            f'{store_dot("samokat", 9)}'
            f'<span style="flex:1;{fnt(500, 12.5, 1.15)}color:{t.ink2};">Самокат</span>'
            f'<span style="{fnt(400, 11.5, 1.1)}color:{t.ink3};">нет сопоставления</span></div>')
        return card(t, f'<div style="display:flex;flex-direction:column;gap:9px;">{head_row}{sep(t)}'
                       f'<div style="display:flex;flex-direction:column;gap:2px;">{"".join(rows)}</div></div>',
                    pad="11px 12px", extra=f"border-color:{t.accent};")

    cat_title = (f'<div style="display:flex;align-items:baseline;justify-content:space-between;">'
                 f'{eyebrow(t, "сыры")}'
                 f'<span style="{fnt(400, 11.5, 1.2)}color:{t.ink3};">2 товара</span></div>')

    items = [product(LINES[1], opened=True), product(LINES[14])]
    cat2_title = (f'<div style="display:flex;align-items:baseline;justify-content:space-between;">'
                  f'{eyebrow(t, "рыба")}'
                  f'<span style="{fnt(400, 11.5, 1.2)}color:{t.ink3};">2 товара</span></div>')
    items2 = [product(LINES[3]), product(LINES[6])]

    head = head_bar(t, "Каталог", "товары всех связанных сетей", eyeb="что купить")
    return phone(t,
                 head + (topnav(t, "grid") if t.nav == "top" else "")
                 + body(t, search + chips_row + cat_title + "".join(items) + cat2_title + "".join(items2), gap=10)
                 + tabbar(t, "grid"),
                 "06 · Каталог", "Категории, товар раскрывается ценами по сетям")


# ---------------------------------------------------------------- 7. сопоставление
def screen_match(t: Theme) -> str:
    pair = MATCH_PAIRS[0]
    line, code, cand, score = pair
    name, _, _, color = STORES[code]

    big = card(
        t,
        f'<div style="display:flex;flex-direction:column;gap:12px;">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;">'
        f'{eyebrow(t, "наш эталон")}'
        f'{chip(t, f"похоже на {score} %", t.good, t.good_soft, size=11)}</div>'
        f'<div style="{fnt(600, 15, 1.3)}color:{t.ink};">{line.name}</div>'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<div style="flex:1;height:1px;background:{t.line};"></div>'
        f'{icon("link", 17, color=t.ink3)}'
        f'<div style="flex:1;height:1px;background:{t.line};"></div></div>'
        f'<div style="display:flex;align-items:center;gap:8px;">{store_dot(code, 9)}'
        f'<span style="{fnt(600, 12, 1.1)}color:{t.ink2};">{name}</span>'
        f'{money(t, line.prices[code], 12.5, 600, t.ink2)}</div>'
        f'<div style="{fnt(500, 14.5, 1.3)}color:{t.ink};">{cand}</div>'
        f'<div style="display:flex;gap:8px;padding-top:2px;">'
        f'<div style="flex:1;">{btn(t, "Это одно и то же", "good", "check", 48, 14)}</div>'
        f'<div style="flex:none;width:96px;">{btn(t, "Нет", "ghost", height=48, size=14)}</div></div></div>',
        pad="14px")

    queue = []
    for line_, code_, cand_, score_ in MATCH_PAIRS[1:4]:
        nm = STORES[code_][0]
        queue.append(
            f'<div style="display:flex;align-items:center;gap:10px;padding:10px 0;'
            f'border-bottom:1px solid {t.line};">'
            f'<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;">'
            f'<span style="{fnt(500, 12.5, 1.2)}color:{t.ink};overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{line_.short}</span>'
            f'<span style="{fnt(400, 11, 1.2)}color:{t.ink3};overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{nm}: {cand_}</span></div>'
            f'{chip(t, f"{score_} %", t.ink2, t.tint, size=11, pad="4px 8px")}'
            f'{icon("right", 15, color=t.ink3)}</div>')

    stats = (f'<div style="display:flex;gap:8px;">'
             f'<div style="flex:1;display:flex;flex-direction:column;gap:3px;padding:11px;'
             f'border-radius:{t.r_card}px;background:{t.good_soft};">'
             f'<span style="font-family:{t.num_family};{fnt(700, 19, 1)}color:{t.good};">{MATCH_DONE}</span>'
             f'<span style="{fnt(400, 11, 1.2)}color:{t.ink2};">подтверждено</span></div>'
             f'<div style="flex:1;display:flex;flex-direction:column;gap:3px;padding:11px;'
             f'border-radius:{t.r_card}px;background:{t.tint};">'
             f'<span style="font-family:{t.num_family};{fnt(700, 19, 1)}color:{t.ink};">{MATCH_LEFT}</span>'
             f'<span style="{fnt(400, 11, 1.2)}color:{t.ink2};">ждут решения</span></div>'
             f'<div style="flex:1;display:flex;flex-direction:column;gap:3px;padding:11px;'
             f'border-radius:{t.r_card}px;background:{t.tint};">'
             f'<span style="font-family:{t.num_family};{fnt(700, 19, 1)}color:{t.ink};">{len(PRICE_STORES)}</span>'
             f'<span style="{fnt(400, 11, 1.2)}color:{t.ink2};">сети с ценами</span></div></div>')

    head = head_bar(t, "Связи товаров", "один товар — разные названия в сетях", eyeb="сопоставление")
    return phone(t,
                 head + (topnav(t, "link") if t.nav == "top" else "")
                 + body(t, stats + big
                        + f'<div style="display:flex;flex-direction:column;gap:0;">'
                          f'{eyebrow(t, "очередь")}{"".join(queue)}</div>', gap=12)
                 + tabbar(t, "link"),
                 "07 · Сопоставление", "Пара «наш товар — товар сети» с решением в один тап")


# ---------------------------------------------------------------- сопоставление
def _store_names() -> list[dict[str, str]]:
    """Название каждого эталона в каждой сети — из тех же строк, что и цены."""
    by_store: dict[str, list[str]] = {}
    for r in _rows("fallback_prices.csv"):
        by_store.setdefault(r["store_code"], []).append(r["name"])
    return [{s: by_store[s][i] for s in PRICE_STORES if i < len(by_store.get(s, []))}
            for i in range(len(LINES))]


def _similarity(a: str, b: str) -> int:
    from difflib import SequenceMatcher
    return int(round(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100))


STORE_NAMES = _store_names()

# Пары, где название в сети дословно не совпадает с эталоном, — их и подтверждает человек.
MATCH_PAIRS: list[tuple[Line, str, str, int]] = []
MATCH_DONE = 0
for _i, _line in enumerate(LINES):
    for _code, _name in STORE_NAMES[_i].items():
        if _name == _line.name:
            MATCH_DONE += 1
        else:
            MATCH_PAIRS.append((_line, _code, _name, _similarity(_line.name, _name)))
MATCH_PAIRS.sort(key=lambda p: -p[3])
MATCH_LEFT = len(MATCH_PAIRS)


# ---------------------------------------------------------------- артборд концепта
SCREENS = [screen_setup, screen_empty, screen_repeat, screen_basket,
           screen_split, screen_catalog, screen_match]

BOARD_W = PAD * 2 + PHONE_W * len(SCREENS) + GAP * (len(SCREENS) - 1)
BOARD_H = 1150


def swatches(t: Theme) -> str:
    chips = "".join(
        f'<span style="width:22px;height:22px;border-radius:6px;background:{c};'
        f'border:1px solid rgba(0,0,0,0.08);display:block;"></span>'
        for c in [t.bg, t.surface, t.ink, t.accent, t.good])
    fonts = t.ui.split(",")[0].strip('"')
    if t.display != t.ui:
        fonts += " · " + t.display.split(",")[0].strip('"')
    if t.mono and t.mono != t.display:
        fonts += " · " + t.mono.split(",")[0].strip('"')
    return (f'<div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end;">'
            f'<div style="display:flex;gap:6px;">{chips}</div>'
            f'<span style="{fnt(500, 12, 1.2)}color:{t.ink3};">{fonts}</span></div>')


def board_head(t: Theme) -> str:
    return (f'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:40px;">'
            f'<div style="display:flex;flex-direction:column;gap:7px;max-width:1500px;">'
            f'<div style="{fnt(600, 12, 1)}letter-spacing:0.14em;text-transform:uppercase;color:{t.ink3};">'
            f'концепт {t.num:02d}</div>'
            f'<div style="font-family:{t.display};{fnt(700, 34, 1.05)}color:{t.ink};letter-spacing:-0.02em;">'
            f'{t.name}</div>'
            f'<div style="{fnt(400, 15, 1.45)}color:{t.ink2};max-width:980px;">{t.idea}</div>'
            f'<div style="display:flex;align-items:center;gap:8px;{fnt(400, 13.5, 1.4)}color:{t.ink3};">'
            f'<span style="width:16px;height:1px;background:{t.line2};display:block;"></span>'
            f'{t.tradeoff}</div></div>{swatches(t)}</div>')


def artboard(t: Theme) -> str:
    phones = "".join(fn(t) for fn in SCREENS)
    canvas_bg = "#15181D" if t.dark else "#F7F6F3"
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="{t.fonts}">
  <style>
    *{{box-sizing:border-box;}}
    body{{margin:0;background:{canvas_bg};color:{t.ink};font-family:{t.ui};
      -webkit-font-smoothing:antialiased;font-feature-settings:"tnum" 1,"lnum" 1;}}
    a{{color:{t.accent};text-decoration:none;}}
    a:hover{{color:{t.ink};}}
  </style>
</helmet>

<div style="display:flex;flex-direction:column;gap:26px;width:{BOARD_W}px;height:{BOARD_H}px;
     padding:{PAD}px;background:{canvas_bg};overflow:hidden;">
  {board_head(t)}
  <div style="display:flex;align-items:flex-start;gap:{GAP}px;">{phones}</div>
</div>
</x-dc>
</body>
</html>
"""


# Направление, выбранное владельцем 16.09.2026: его артборд — входной (Main.dc.html)
# и единственный на первой странице канвы, остальные девять уехали на вторую.
LEAD_KEY = "Brand"


def lead() -> Theme:
    return next(t for t in THEMES if t.key == LEAD_KEY)


def file_name(t: Theme) -> str:
    return "Main.dc.html" if t.key == LEAD_KEY else f"{t.key}.dc.html"


def canvas_json() -> dict:
    step = BOARD_H + 170
    boards = [{"file": "Main.dc.html", "x": 0, "y": 0, "w": BOARD_W, "h": BOARD_H,
               "title": f"{lead().name} — выбранное направление", "page": "page-1"}]
    for i, t in enumerate([t for t in THEMES if t.key != LEAD_KEY]):
        boards.append({"file": file_name(t), "x": 0, "y": i * step, "w": BOARD_W, "h": BOARD_H,
                       "title": f"{t.num:02d} · {t.name}", "page": "page-2"})
    notes = [
        {"id": "vybor", "x": 0, "y": -330, "w": 900, "page": "page-1",
         "text": (f"Выбрано направление «{lead().name}»: {lead().idea}\n"
                  "Экраны идут слева направо: главная с настройкой, пустая корзина, повтор заказа, "
                  "корзина, сплит, каталог, связи товаров.\n"
                  f"Чем платим: {lead().tradeoff}")},
        {"id": "dannye", "x": 960, "y": -330, "w": 860, "page": "page-1",
         "text": (f"Числа не придуманы: товары, цены и карты взяты из data/*.csv репозитория.\n"
                  f"Вся корзина в «Пятёрочке» — {rub(BASE_TOTAL)}; сплит Магнит + ВкусВилл — {rub(SPLIT_TOTAL)};\n"
                  f"один магазин — {rub(SINGLE_TOTAL)} ({STORES[SINGLE_STORE][0]}). Выгода {rub(GAIN)}, "
                  f"это {str(GAIN_PCT).replace('.', ',')} %.\n"
                  "Адрес, телефон и два чека из трёх — демонстрационные.")},
        {"id": "arhiv", "x": 0, "y": -330, "w": 900, "page": "page-2",
         "text": ("Девять невыбранных направлений — здесь, чтобы было с чем сравнивать.\n"
                  "У каждого свои шапка, строка товара, плитки сетей, переключатель режима сборки,\n"
                  "навигация, палитра и шрифты. Под названием — чем за направление платим.")},
    ]
    return {"artboards": boards, "annotations": notes,
            "pages": [{"id": "page-1", "name": lead().name},
                      {"id": "page-2", "name": "Другие направления"}],
            "launch": {"view": "canvas", "page": "page-1"}}


def main() -> None:
    # в консоли Windows по умолчанию cp1251, а в отчёте есть ₽ и названия сетей
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 — не повод не собрать макеты
        pass
    os.makedirs(OUT, exist_ok=True)
    for t in THEMES:
        path = os.path.join(OUT, file_name(t))
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(artboard(t))
        print(f"{file_name(t):<18} {os.path.getsize(path) // 1024:>4} КБ  {t.name}")
    with open(os.path.join(OUT, "canvas.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(canvas_json(), fh, ensure_ascii=False, indent=2)
    print(f"\nАртборд {BOARD_W}x{BOARD_H}, экранов в ряду {len(SCREENS)}, концептов {len(THEMES)}.")
    print(f"Корзина: база {rub(BASE_TOTAL)}, сплит {rub(SPLIT_TOTAL)}, один магазин {rub(SINGLE_TOTAL)}, "
          f"выгода {rub(GAIN)} ({GAIN_PCT} %).")
    print(f"Сопоставление: подтверждено {MATCH_DONE}, в очереди {MATCH_LEFT}.")


if __name__ == "__main__":
    main()
