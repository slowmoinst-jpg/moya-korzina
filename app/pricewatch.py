"""Дозор цен: цены корзин всех рабочих мест свежие круглые сутки, а не после ночного обхода.

ЗАЧЕМ. Расчёт верит только свежей цене: снимок старше prices.fresh_hours (6 ч) не
показывается и в расчёт не идёт (app/freshness.py). Свежесть до сих пор приносили кнопка
«Узнать цены по магазинам» и ночной обход каталога, больше ничего. Поэтому утром
«Результат» был пуст, пока человек сам не нажмёт кнопку, а цена Дикси жила шесть часов
после ночного обхода и пропадала до следующей ночи. Человек ждал цены, которые
приложение могло снять за него заранее.

ЧТО ДЕЛАЕТ. Раз в prices.watch.check_every_min минут дозор просыпается и у каждого
рабочего места смотрит товары последних корзин (prices.watch.baskets). Цены, снятые
раньше чем refresh_after_min минут назад, он спрашивает у сети тем же путём, что и
кнопка: matcher.refresh_prices, коннектор сети, место человека. Цену, которую человек
обновил сам полчаса назад, повторно не спрашивает. Порог в два часа втрое короче срока
доверия, поэтому пара пропущенных проходов (туннель спал, сеть не ответила) цену ещё не
гасит.

КАКИЕ СЕТИ. Те, где у приложения есть живая цена (prices.watch.chains): Магнит — шлюз
сайта, ВкусВилл и Лента — их MCP, METRO — API, Дикси — карточка через домашний выход.
Пятёрочка и Самокат живут на чеках и прайсе человека: у сети там спрашивать нечего.
Перекрёсток цен не отдаёт вовсе. Появится живая дорога — сеть добавляется строкой в
config.yaml.

НАГРУЗКА. Темп тот же, что у кнопки и ночного обхода, общий на сеть для всех процессов
(connectors.rate_limit_rps; сети домашнего выхода реже — connectors.home_exit.rate_limit_rps).
Сети идут по очереди, одна за другой. Ответ сети кэш делит между рабочими местами: одна
и та же карточка за проход спрашивается один раз. Цену старее половины порога дозор из
кэша не берёт (refresh_prices, fresh_within → cache.max_age), иначе он обновил бы снимок
одной видимостью; подбор магазина к адресу при этом живёт со своим обычным сроком.
Домашний выход не отвечает — его сети в этом проходе пропускаются: сервер они не
пускают, стучаться незачем. Следующий проход проверит выход заново. Предохранитель сетей
(base.api_disabled) дозор сбрасывает на каждом проходе: вчерашняя неудача не повод
молчать сегодня. Товар, которому сеть живой цены не дала, спрашивается снова не раньше
чем через refresh_after_min, а не на каждом проходе.

ЧТО ПИШЕТ. Только живые снимки этого прохода (refresh_prices, fresh_within). Цену из
чека, прайса или справочника, которую коннектор отдаёт, не дозвонившись до сети, дозор
не пишет. Иначе каждые два часа ложилась бы новая копия вчерашней цены.

ГДЕ ЖИВЁТ. Поток в службе korzina-jobs (app/catalog/worker.py) рядом с ночным обходом,
но не в его очереди: обход длится часами, и цены за это время протухли бы. Руками:

    python -m app.pricewatch --once                  — один проход и выход
    python -m app.pricewatch --once --chain dixy --phone demo
    python -m app.pricewatch --dry-run               — что устарело; в сети не ходит
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from app import config

log = logging.getLogger("pricewatch")

# Сети с живой ценой — если config.yaml молчит.
CHAINS = ("magnit", "vkusvill", "lenta", "metro", "dixy")
REFRESH_AFTER_MIN = 120.0
CHECK_EVERY_MIN = 15.0
BASKETS = 3
START_DELAY_SEC = 60.0       # служба только поднялась — первый проход через минуту
MIN_SLEEP_SEC = 60.0


def _cfg(key: str, default):
    value = config.get(f"prices.watch.{key}")
    return default if value is None else value


def _number(key: str, default: float) -> float:
    try:
        value = float(_cfg(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def enabled() -> bool:
    return bool(_cfg("enabled", True))


def refresh_after_min() -> float:
    return _number("refresh_after_min", REFRESH_AFTER_MIN)


def check_every_min() -> float:
    return _number("check_every_min", CHECK_EVERY_MIN)


def chains() -> list[str]:
    configured = _cfg("chains", None)
    if isinstance(configured, list) and configured:
        return [str(code) for code in configured]
    return list(CHAINS)


def baskets_per_place() -> int:
    return int(_number("baskets", BASKETS))


def with_demo() -> bool:
    """Демо в дозоре: его «Результат» видит каждый гость, и по нему владелец проверяет сервер."""
    return bool(_cfg("demo", True))


def mask(phone: str) -> str:
    """Номер в журнале — без середины, как в отчёте app/cartfill.py."""
    from app.cartfill import mask as masked

    return masked(phone)


# ---------- что устарело ----------
def basket_products(limit: int) -> list[int]:
    """Товары последних корзин рабочего места — то, что считает «Результат». Без повторов."""
    from app import repo

    seen: dict[int, None] = {}
    for basket in repo.list_baskets()[:max(1, limit)]:
        for item in repo.basket_items(int(basket["id"])):
            seen.setdefault(int(item["product_id"]), None)
    return list(seen)


def stale(product_ids: list[int], store, hours: float,
          now: datetime | None = None) -> list[int]:
    """Товары, опознанные в сети `store`, у которых живой цены нет или она старше `hours`.

    Справочная цена из CSV живой не считается: расчёт её не берёт (app/freshness.py).
    Артикул, которого у сети нет, у неё не спросить: «hist-…» — строка чека,
    «<сеть>-…» — строка справочника data/fallback_prices.csv (так сопоставлено демо).
    Дозору там делать нечего.
    """
    from app import freshness, repo
    from app.connectors.history import PREFIX

    made_up = (PREFIX, f"{store.code}-")
    out: list[int] = []
    for product_id in product_ids:
        mapping = repo.confirmed_mapping(product_id, store.id)
        if not mapping or str(mapping.get("sku") or "").startswith(made_up):
            continue                    # в этой сети товар не опознан — спрашивать нечего
        snap = repo.latest_price(int(mapping["id"]))
        if (snap and snap.get("source") != freshness.REFERENCE
                and freshness.is_fresh(snap.get("fetched_at"), now, hours=hours)):
            continue
        out.append(product_id)
    return out


# ---------- проход ----------
@dataclass
class ChainTally:
    """Итог прохода по одной сети: сколько цен устарело и сколько свежих записано."""

    code: str
    due: int = 0
    updated: int = 0
    places: int = 0
    skipped: str = ""
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        if self.skipped:
            return f"{self.code}: пропущена — {self.skipped}"
        if not self.due:
            return f"{self.code}: всё свежее"
        text = (f"{self.code}: устарело {self.due}, свежих цен записано {self.updated} "
                f"(рабочих мест {self.places})")
        if self.errors:
            text += "; " + "; ".join(self.errors[:3])
        return text


# Отвечал ли домашний выход на прошлом проходе: о смене говорим в журнал один раз, а не
# каждые четверть часа всю ночь, пока ноутбук спит.
_EXIT_UP: dict[str, bool] = {}

# Когда товар последний раз спрашивали: (рабочее место, сеть, товар) -> time.time().
# Товар, которому сеть живой цены не дала (карточки нет, цену витрина прячет), иначе
# спрашивался бы каждые четверть часа. Так — не чаще раза в refresh_after_min, как и
# удачный. Сеть, пропущенная целиком (туннель спит), попыткой не считается: выход
# поднимется — её товары спросятся на ближайшем проходе.
_ASKED: dict[tuple[str, str, int], float] = {}


def _not_asked_lately(phone: str, code: str, product_ids: list[int], seconds: float,
                      now: float) -> list[int]:
    return [pid for pid in product_ids if now - _ASKED.get((phone, code, pid), 0.0) >= seconds]


def _forget_old(now: float, seconds: float) -> None:
    for key in [k for k, at in _ASKED.items() if now - at > seconds]:
        _ASKED.pop(key, None)


def _reachable(codes: list[str]) -> tuple[list[str], dict[str, str]]:
    """Сети, до которых сейчас есть дорога, и почему остальные пропущены.

    Сеть домашнего выхода при спящем туннеле пропускается целиком: сервер она не
    пускает, и стучаться к ней с его адреса незачем.
    """
    from app import homeexit

    live: list[str] = []
    skipped: dict[str, str] = {}
    for code in codes:
        url = homeexit.configured(code)
        if url and not homeexit.alive(url):
            if _EXIT_UP.get(code, True):
                log.warning("дозор цен: %s ждёт домашний выход — он не отвечает (ноутбук "
                            "выключен, спит или владелец вышел из системы)", code)
            _EXIT_UP[code] = False
            skipped[code] = "домашний выход не отвечает, проверю на следующем проходе"
            continue
        if url:
            if _EXIT_UP.get(code) is False:
                log.info("дозор цен: домашний выход снова отвечает — %s снова в дозоре", code)
            _EXIT_UP[code] = True
            until = homeexit.paused_until(code)
            if until:
                skipped[code] = ("через дом сеть недавно показала проверку «я не робот» — "
                                 f"пауза до {time.strftime('%H:%M', time.localtime(until))}")
                continue
        live.append(code)
    return live, skipped


def run_once(only: list[str] | None = None, phones: list[str] | None = None,
             dry_run: bool = False) -> list[ChainTally]:
    """Один проход дозора по рабочим местам. Наружу не бросает: одно место не роняет другие."""
    from app import repo, users
    from app.connectors.base import reset_failures
    from app.matcher import refresh_prices

    started = datetime.now()
    clock = time.time()
    hours = refresh_after_min() / 60.0
    _forget_old(clock, 24 * 3600.0)
    codes = [code for code in chains() if not only or code in only]
    tallies = {code: ChainTally(code) for code in codes}
    live, skipped = _reachable(codes)
    for code, why in skipped.items():
        tallies[code].skipped = why
    if not live:
        return list(tallies.values())
    if not dry_run:
        reset_failures()
    # Кэш моложе половины порога годится: так одна карточка на проход спрашивается
    # один раз, сколько бы рабочих мест её ни ждали.
    fresh_within = hours * 3600.0 / 2
    places = phones or [p for p in users.list_workspaces() if with_demo() or p != users.DEMO]
    for phone in places:
        if not users.exists(phone):
            continue
        try:
            users.open_workspace(phone)
            products = basket_products(baskets_per_place())
            if not products:
                continue
            for code in live:
                store = repo.get_store(code)
                if store is None:
                    continue
                due = stale(products, store, hours, started)
                if not dry_run:
                    due = _not_asked_lately(phone, code, due, hours * 3600.0, clock)
                if not due:
                    continue
                tally = tallies[code]
                tally.due += len(due)
                tally.places += 1
                if dry_run:
                    log.info("%s: %s — устарело %d из %d", mask(phone), code, len(due), len(products))
                    continue
                for pid in due:
                    _ASKED[(phone, code, pid)] = clock
                result = refresh_prices(due, [code], fresh_within=fresh_within)
                tally.updated += int(result.get("updated") or 0)
                tally.errors.extend(result.get("errors") or [])
        except Exception as exc:  # noqa: BLE001 — одно рабочее место не роняет дозор
            log.exception("дозор цен: рабочее место %s не обошлось", mask(phone))
            for code in live:
                tallies[code].errors.append(f"{mask(phone)}: {type(exc).__name__}")
        finally:
            users.deactivate()
    return list(tallies.values())


def report(tallies: list[ChainTally], seconds: float) -> str:
    due = sum(t.due for t in tallies)
    updated = sum(t.updated for t in tallies)
    head = f"дозор цен: проход за {seconds:.0f} с, устарело {due}, свежих цен записано {updated}"
    return "\n".join([head] + ["  " + t.line() for t in tallies])


# ---------- служба ----------
def serve() -> None:
    """Дозор без конца: проход, сон до следующей проверки. Проход упал — следующий по часам.

    В журнал идут проходы, которые что-то спрашивали; пустой проход раз в четверть часа
    журналу не нужен.
    """
    time.sleep(START_DELAY_SEC)
    while True:
        begun = time.time()
        try:
            tallies = run_once()
            if any(t.due or t.errors for t in tallies):
                log.info("%s", report(tallies, time.time() - begun))
        except Exception:  # noqa: BLE001 — поток дозора не должен умирать
            log.exception("дозор цен: проход не удался, следующий по расписанию")
        left = check_every_min() * 60.0 - (time.time() - begun)
        time.sleep(max(MIN_SLEEP_SEC, left))


def serve_in_background() -> threading.Thread | None:
    """Поток дозора в службе. Выключен настройкой — None, и служба живёт без него."""
    if not enabled():
        log.info("дозор цен выключен (prices.watch.enabled)")
        return None
    thread = threading.Thread(target=serve, name="pricewatch", daemon=True)
    thread.start()
    log.info("дозор цен: проверка раз в %.0f мин, цену старше %.0f мин спрашиваю заново; сети: %s",
             check_every_min(), refresh_after_min(), ", ".join(chains()))
    return thread


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Дозор цен: обновить устаревшие цены корзин")
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать, что устарело, в сети не ходить")
    parser.add_argument("--chain", action="append", help="только эта сеть (можно несколько раз)")
    parser.add_argument("--phone", action="append", help="только это рабочее место")
    args = parser.parse_args(argv)
    if not (args.once or args.dry_run):
        serve()
        return 0
    from app import users

    phones = [users.normalize_phone(p) or p for p in args.phone] if args.phone else None
    begun = time.time()
    tallies = run_once(args.chain, phones, dry_run=args.dry_run)
    print(report(tallies, time.time() - begun))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
