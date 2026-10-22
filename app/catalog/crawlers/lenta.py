"""Лента: перечень — из sitemap, подробности — карточкой через MCP.

Почему так, а не поиском. Сайт lenta.com закрыт Qrator (401 с вызовом на любую
страницу и любой API-путь), а sitemap отдаётся свободно и содержит ВСЕ товары —
80 743 адреса вида https://lenta.com/product/<slug>-<id>/ (разведка 16.09.2026).
Единственный читаемый источник названий и фасовок — MCP: storefront_product_details по
id и коду хаба отдаёт name, package, price, stock, url. Поиск отдаёт 10 позиций за
вызов, но требует слов и не гарантирует покрытия; карточка детерминирована.

Цена вопроса: 80 тысяч карточек по одной в секунду — 22 часа. Поэтому сборщик работает
ПРИРАЩЕНИЕМ: за один обход спрашивает карточки только у незнакомых id, не больше
catalog.lenta.details_per_run (по умолчанию 2000 ≈ 35 минут), а всем известным
продлевает жизнь отметкой seen_only. Полный каталог набирается за несколько недель,
дальше в сутки уходят только новинки.

Хаб: код точки ДОСТАВКИ из storefront_resolve_store — поле suggested.delivery.aliasId
(291 для Москвы, Ходынский бульвар 4). Не путать с физическими магазинами из hubs:
по их кодам витрина молчит (проверено 15.09.2026). Категории у Ленты в карточке нет —
поле остаётся пустым, sitemap категорий с товарами не связан.

СПРОСИЛИ — ТОВАРА НЕТ (18.10.2026). В карте 82 908 адресов, а витрина хаба знает не все:
замер 18.10.2026 по выборке из 40 незнакомых артикулов — 31 есть, 9 «товар N не найден»
(77,5 % есть). Мёртвые артикулы стоят в НАЧАЛЕ карты и раньше висели в списке незнакомых
вечно: каждый обход потолок details_per_run (2000) уходил на те же «не найденные» первыми,
а каталог прирастал на ~500 за ночь вместо ~1 500. Теперь ответ «не найден» запоминается в
chain_misses (app/catalog/store.py) и артикул не спрашивается retry_days дней, а находка
память о нём снимает. Обход спрашивает ровно то, чего ещё не видел, и потолок работает.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from app import config
from app.catalog.crawlers import Pace, sitemap_locs
from app.catalog.model import ChainProduct, Crawler, Progress
from app.connectors import mcp_client
from app.connectors.lenta import MCP_URL, _number, _per_kg, _unit
from app.matcher.normalize import parse_weight

log = logging.getLogger(__name__)

SITEMAP = "https://lenta.com/sitemap/sitemap_index.xml"
ITEM_MAPS = re.compile(r"sitemap_item_\d+\.xml")
PRODUCT_URL = re.compile(r"/product/(?P<slug>[^/]+?)-(?P<id>\d+)/?$")
DEFAULT_STORE_ID = 291

# Чем кончился вопрос о карточке. ok — товар есть, missing — сеть сама сказала «не
# найден» (это можно запомнить), error — не достучались или ответ непонятный
# (запоминать нельзя: завтра тот же артикул может отдаться).
OK, MISSING, ERROR = "ok", "missing", "error"


def to_product(item: dict) -> ChainProduct | None:
    sku, name = item.get("id"), (item.get("name") or "").strip()
    if not sku or not name:
        return None
    package = (item.get("package") or "").strip()
    weight, _ = parse_weight(package or name)
    price = _number(item.get("price"))
    stock = _number(item.get("stock"))
    unit = _unit(name)
    if unit == "kg" and price:
        # У развесного Лента отдаёт цену ПОРЦИИ (price = pricePerKg × weightGrams / 1000),
        # а строка каталога с единицей «kg» хранит цену килограмма.
        price = _per_kg(item, price, name) or price
    return ChainProduct(
        sku=str(sku), name=name, weight_g=weight or None, unit=unit,
        # МАРКА И ШТРИХКОД ЛЕЖАТ В КАРТОЧКЕ, А МЫ ИХ НЕ БРАЛИ (замер 18.10.2026):
        # из 60 карточек Ленты штрихкод есть у 59, и 27 из них — единичные (EAN-13),
        # а 6 (10 %) уже сейчас сходятся со штрихкодом METRO в единой базе. Марка есть
        # у каждой карточки. Без них 28 574 строки склеивались только словами.
        brand=(item.get("brand") or "").strip() or None,
        barcode=(item.get("ean") or "").strip() or None,
        url=item.get("url") or f"https://lenta.com/product/{item.get('slug', '')}-{sku}/",
        price=price if price and price > 0 else None,
        in_stock=(stock > 0) if stock is not None else None,
    )


class LentaCrawler(Crawler):
    code = "lenta"
    name = "Лента"

    def __init__(self, hub: int | str | None = None) -> None:
        # Хаб приходит от адреса человека (app/places.py). Перечень товаров у Ленты
        # общий на страну — он берётся из sitemap, — и от точки зависят только
        # карточки: цена, остаток, фасовка. Поэтому хаб здесь ОДИН, а не список:
        # второй хаб не добавил бы каталогу ни строки, зато удвоил бы запросы.
        self.store_id = int(hub or config.get("catalog.lenta.store_id") or DEFAULT_STORE_ID)
        self.cap = int(config.get("catalog.lenta.details_per_run") or 2000)
        # Через сколько дней снова спрашивать артикул, про который сеть сказала «нет».
        # Ноль и None — не спрашивать никогда (так смотрят, сколько их накопилось).
        raw = config.get("catalog.lenta.miss_retry_days")
        self.retry_days = None if raw is None else float(raw or 0)
        # Сколько карточек за обход спрашиваем ради марки и штрихкода у строк, записанных
        # до этой правки (28 574 строки без них). Ноль — не добирать вовсе.
        self.backfill = int(config.get("catalog.lenta.backfill_per_run") or 0)
        # Сколько знакомых карточек за обход перечитываем ради свежей цены. Ноль — не
        # перечитывать вовсе.
        self.price_refresh = int(config.get("catalog.lenta.price_refresh_per_run") or 0)
        self.pace = Pace(chain=self.code)

    def universe(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for loc in sitemap_locs(SITEMAP, self.pace, pick=ITEM_MAPS):
            m = PRODUCT_URL.search(loc)
            if m and m.group("id") not in seen:
                seen.add(m.group("id"))
                ids.append(m.group("id"))
        return ids

    def details(self, sku: str) -> tuple[dict | None, str]:
        """Карточка товара: (товар или None, чем кончилось — OK / MISSING / ERROR).

        Различать «сеть сказала, что товара нет» и «до сети не достучались» обязательно:
        первое можно запомнить и больше не спрашивать, второе — нельзя, иначе одна
        сетевая заминка вычеркнет из каталога сотни живых товаров.
        """
        self.pace.wait()
        answer = mcp_client.call_tool(MCP_URL, self.code, "storefront_product_details",
                                      {"id": int(sku), "storeId": self.store_id, "channel": "lo"})
        if isinstance(answer, dict) and answer.get("ok") is False:
            why = str(answer.get("error") or "")
            return None, MISSING if "не найден" in why.lower() else ERROR
        data = mcp_client.ok_payload(answer) or {}
        item = data.get("item") if isinstance(data.get("item"), dict) else data
        if isinstance(item, dict) and item.get("name"):
            return item, OK
        return None, ERROR

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        from app.catalog import store

        say = progress or (lambda msg: None)
        ids = self.universe()
        known = store.known_skus(self.code)
        missed = store.missed_skus(self.code, self.retry_days)
        here = set(ids)
        unknown = [sku for sku in ids if sku not in known]
        fresh = [sku for sku in unknown if sku not in missed]
        # Сколько разобрано из перечня — для проверки базы (store.mark_map). «Мёртвые»
        # артикулы в остаток не входят: их уже спрашивали, и сеть сказала «нет».
        self.map_total, self.map_known, self.map_left = len(here), len(known & here), len(fresh)
        say(f"в sitemap {len(ids)} товаров: известно {len(known & here)}, "
            f"«не найдено» ранее {len(unknown) - len(fresh)}, спросить {len(fresh)}")
        for sku in ids:
            if sku in known:
                yield ChainProduct(sku=sku, name="", seen_only=True)
        taken = misses = errors = 0
        for n, sku in enumerate(fresh[:self.cap], start=1):
            item, why = self.details(sku)
            product = to_product(item) if item else None
            if product:
                taken += 1
                store.clear_miss(self.code, sku)      # товар вернулся — память не нужна
                yield product
            elif why == MISSING:
                misses += 1
                store.note_miss(self.code, sku, "карточка ответила «товар не найден»")
            else:
                errors += 1                           # не достучались: запоминать нельзя
            if n % 100 == 0:
                say(f"карточек спрошено {n} из {min(len(fresh), self.cap)}, "
                    f"взято {taken}, «не найдено» {misses}")
        say(f"спрошено {min(len(fresh), self.cap)}: взято {taken}, «не найдено» {misses}, "
            f"без ответа {errors}")
        if len(fresh) > self.cap:
            say(f"незнакомых ещё {len(fresh) - self.cap} — доберём в следующие обходы")
        yield from self._backfill(known, say)
        yield from self._refresh_prices(say)

    def _backfill(self, known: set[str], say: Progress) -> Iterator[ChainProduct]:
        """Добрать марку и штрихкод у строк, записанных до этой правки.

        ЗАЧЕМ. Карточка Ленты отдаёт марку и штрихкод всегда (замер 18.10.2026: 59
        карточек из 60 со штрихкодом, 27 из них единичные), а прежний сборщик их не
        брал: 28 574 строки в базе — без марки и без штрихкода, и склеиваются они
        только словами. Штрихкод — самая твёрдая улика сопоставления: 6 % карточек уже
        сходятся с METRO по коду (замер по выборке 60).

        ПОЧЕМУ ЭТО НЕ ОБХОД ЦЕЛИКОМ. Одна карточка — один запрос, и 28 574 карточки
        за один обход — это восемь часов. Поэтому за обход спрашиваем не больше
        backfill_per_run (по умолчанию 2000 ≈ 34 минуты): за две недели база добирается
        целиком, а сеть не получает вдвое больше запросов зараз.
        """
        if not self.backfill:
            return
        from app.catalog import store

        thin = store.thin_skus(self.code, self.backfill)
        if not thin:
            return
        # Тот же счёт по потолку, что у свежести цен: не съедаем остаток целиком, иначе
        # обход закончится НЕПОЛНЫМ из-за добора, а не из-за нового товара.
        fit = self.cards_left(self.pace.gap)
        if fit is not None:
            if fit <= 0:
                say("добор подробностей: до потолка обхода времени не осталось — пропускаю")
                return
            if fit < len(thin):
                say(f"добор подробностей: ждут {len(thin)}, до потолка влезает {fit}")
                thin = thin[:fit]
        say(f"добор подробностей: строк без марки или штрихкода {len(thin)}")
        filled = errors = 0
        for n, sku in enumerate(thin, start=1):
            item, why = self.details(sku)
            product = to_product(item) if item else None
            if product:
                filled += 1
                yield product                       # строка обновится маркой и штрихкодом
            else:
                errors += 1
            if n % 200 == 0:
                say(f"добор подробностей: спрошено {n} из {len(thin)}, добрано {filled}")
        say(f"добор подробностей: добрано {filled} из {len(thin)}, без ответа {errors}")

    def _refresh_prices(self, say: Progress) -> Iterator[ChainProduct]:
        """Перечитать карточки у знакомых строк, чья цена в базе старше всех.

        ЗАЧЕМ. Карточка Ленты спрашивается только при первом знакомстве с артикулом,
        знакомым строкам обход лишь продлевает «видели» — цену никто не перечитывает.
        Замер 20.10.2026 по боевой базе: 9 627 цен Ленты старше недели, а карточка
        отдаёт и цену, и остаток — то есть стареют заодно и наличие, и цена.

        ПОЧЕМУ ИДЁТ ПОСЛЕДНЕЙ. Время обхода ограничено потолком на сеть (180 мин), а у
        Ленты впереди стоят новые артикулы (6 000) и добор марки со штрихкодом (2 000),
        и они важнее: без нового товара каталог не растёт, а без марки не сходятся
        строки. Свежесть цен — третья по важности, поэтому если ночь упёрлась в потолок,
        обход останавливается на ней, а не на них.
        """
        if not self.price_refresh:
            return
        from app.catalog import store

        stale = store.stale_price_skus(self.code, self.price_refresh)
        if not stale:
            return
        # ПОТОЛОК ОБХОДА ВАЖНЕЕ ДОБОРА. Свежесть идёт последней, и если до потолка осталось
        # мало времени, лучше перечитать меньше карточек и закончить обход как обычно, чем
        # упереться в потолок и записать всю ночь неполной (замер 20.10.2026: у Ленты
        # 6 000 новых + 2 000 марки + 2 000 свежести = 167 мин при потолке 180).
        fit = self.cards_left(self.pace.gap)
        if fit is not None:
            if fit <= 0:
                say("свежесть цен: до потолка обхода времени не осталось — пропускаю")
                return
            if fit < len(stale):
                say(f"свежесть цен: ждут {len(stale)}, до потолка влезает {fit} — беру сколько успею")
                stale = stale[:fit]
        say(f"свежесть цен: перечитываю {len(stale)} карточек, начиная с самой старой")
        refreshed = errors = 0
        for n, sku in enumerate(stale, start=1):
            item, _ = self.details(sku)
            product = to_product(item) if item else None
            if product:
                refreshed += 1
                yield product
            else:
                errors += 1
            if n % 200 == 0:
                say(f"свежесть цен: спрошено {n} из {len(stale)}, обновлено {refreshed}")
        say(f"свежесть цен: обновлено {refreshed} из {len(stale)}, без ответа {errors}")
