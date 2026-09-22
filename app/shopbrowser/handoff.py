"""Вход, переданный с телефона человека, а не добытый нашим сервером.

ЗАЧЕМ ЭТА ДОРОГА ПОЯВИЛАСЬ. 20.09.2026 окно магазина на сервере уперлось в
стену, которой нечем помочь изнутри: Магнит встретил вход проверкой Yandex
SmartCaptcha, а после неё ответил «Аккаунт заблокирован. Продолжите без него или
войдите по-другому». С телефона владельца тот же аккаунт открывается как обычно.
Разница между двумя случаями одна — адрес, с которого стучатся: у сервера он
один на всё, и по нему же нас не пускают Лента, Дикси,
Пятёрочка и Самокат.

ПОЧЕМУ ЭТО ВООБЩЕ ВОЗМОЖНО. Замер 17.09.2026 (проверка `android/probe`): владелец
вошёл в Магнит с телефона, и страница видит ВСЕ двадцать одну куку `magnit.ru`,
включая `mg_at` со значением в 648 знаков. То есть кука входа НЕ `HttpOnly`, и
скрипт на самой странице читает её так же, как читает её код сети. Рассуждение
«куки входа всё равно не достать» — неверно, и оно уже дважды уводило выбор
дороги в сторону.

ПОЧЕМУ ЧЕРЕЗ БУФЕР, А НЕ ЗАПРОСОМ ИЗ ЗАКЛАДКИ. Закладка живёт на странице сети,
то есть на чужом источнике по `https`. Постучаться из неё в нашу дверь нельзя
дважды: браузер запретит смешанное содержимое (`https` → `http`) и потребует
разрешения CORS. Обе преграды настоящие, и обходить их пришлось бы на нашей
стороне, ослабляя дверь. Буфер обмена их просто не касается — той же дорогой
работает закладка кабинета ФНС, и человеку она знакома.

ЧТО ИМЕННО ЕДЕТ И ЧЕГО ЗДЕСЬ НЕТ. Едет банка кук одного домена: имена и
значения. Ни пароля, ни кода из СМС в ней нет — их человек набрал в самой сети,
и наружу они не выходят. Имя куки входа знать не требуется: у пяти сетей из
шести оно нам неизвестно (`signals.AUTH_COOKIE`), и выдумывать его нельзя —
поэтому берётся вся банка целиком, а не угаданная строка из неё.

ПОЧЕМУ РАЗБОР ТАКОЙ ПРИДИРЧИВЫЙ. Сюда приезжает то, чем человек доказывает
магазину, что он это он, — и приезжает вставкой из буфера, то есть куда угодно
могло попасть что угодно. Ошибка здесь не «не сработало», а «сервер ходит в
магазин с чужим ключом». Поэтому проверяется всё: домен принадлежит именно этой
сети, имена похожи на имена кук, размер в пределах разумного, а непонятное
отбрасывается с объяснением, а не «на всякий случай сохраняется».
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Имя куки по RFC: печатные знаки без разделителей. Кириллицы тут не бывает — и
# если она встретилась, это не кука, а вставленный кусок чего-то другого.
NAME_OK = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}$")

MAX_COOKIES = 80            # у Магнита их двадцать одна; восемьдесят — с большим запасом
MAX_VALUE = 8192            # mg_at длиной 648 знаков; восемь тысяч закрывает любой разумный ключ
MAX_TEXT = 512 * 1024       # больше этого во вставке не бывает ничего осмысленного


class Rejected(ValueError):
    """Переданное не приняли, и человеку сказано почему.

    Отдельный класс, потому что ответ на это — не «ошибка приложения», а
    понятная фраза на экране: вставили не то, вставили от другой сети, вставили
    просроченное. Все три лечатся человеком за десять секунд, если их назвать.
    """


def _host_of(chain: str) -> str:
    """Дом сети. Берётся из driver.HOME_URL, а не пишется здесь второй раз.

    Своя копия списка узлов однажды разошлась бы с настоящей — и разошлась бы
    молча, в сторону «принимаем куки чужого сайта».
    """
    from app.shopbrowser import driver

    home = driver.HOME_URL.get(chain) or ""
    return (urlsplit(home).hostname or "").lower()


def _same_site(host: str, home: str) -> bool:
    """Тот ли это дом. Поддомен свой, чужой узел с похожим именем — нет."""
    host = (host or "").lower().lstrip(".")
    if not host or not home:
        return False
    return host == home or host.endswith("." + home)


def parse(chain: str, text: str) -> dict:
    """Разобрать вставленное в состояние входа для браузера.

    Принимает то, что кладёт в буфер закладка (`docs/hand.src.js`): объект с
    полями `store`, `host` и `cookies`. Отвечает storage_state — ровно тем
    словарём, который понимает и Playwright, и app/shopbrowser/store.save.
    """
    raw = (text or "").strip()
    if not raw:
        raise Rejected("Вставлять нечего: поле пустое.")
    if len(raw) > MAX_TEXT:
        raise Rejected("Вставленное неправдоподобно велико — это не передача входа.")

    try:
        data = json.loads(raw)
    except ValueError:
        raise Rejected("Это не то, что кладёт закладка: ожидался её текст целиком. "
                       "Скопируйте ещё раз — целиком, от первой скобки до последней.") from None
    if not isinstance(data, dict):
        raise Rejected("Это не то, что кладёт закладка.")

    said = str(data.get("store") or "").strip().lower()
    if said and said != chain:
        # Самая дорогая из ошибок человека и самая простая: открыл закладку в
        # одной сети, вставляет в кабинет другой. Промолчать значило бы
        # записать Магниту вход во ВкусВилл и объявить его подключённым.
        raise Rejected(f"Это вход в другую сеть — «{said}». Вставьте его в её кабинет.")

    home = _host_of(chain)
    if not home:
        raise Rejected("Эта сеть браузеру незнакома.")
    host = str(data.get("host") or "").strip().lower()
    if host and not _same_site(host, home):
        raise Rejected(f"Куки сняты с чужого сайта — «{host}», а ожидался {home}.")

    cookies = _cookies(data.get("cookies"), home)
    if not cookies:
        raise Rejected("В переданном нет ни одной куки. Похоже, закладка сработала "
                       "не на странице магазина — откройте её на сайте сети, уже войдя.")
    return {"cookies": cookies, "origins": []}


def _cookies(rows, home: str) -> list[dict]:
    """Отобрать годные куки. Негодные отбрасываются молча для чужих и с журналом.

    Молча — потому что банка страницы содержит и счётчики посещений, и мусор
    чужих встроек; перечислять их человеку значит топить нужное в шуме. А вот в
    журнал отбор попадает: по нему видно, если сеть однажды сменит домен.
    """
    if not isinstance(rows, list):
        raise Rejected("В переданном нет списка кук.")
    out: list[dict] = []
    skipped = 0
    for row in rows[:MAX_COOKIES * 4]:
        if not isinstance(row, dict):
            skipped += 1
            continue
        name = str(row.get("name") or "").strip()
        value = row.get("value")
        if not NAME_OK.match(name) or not isinstance(value, str) or len(value) > MAX_VALUE:
            skipped += 1
            continue
        domain = str(row.get("domain") or "").strip().lower() or "." + home
        if not _same_site(domain, home):
            skipped += 1
            continue
        out.append({"name": name, "value": value,
                    "domain": domain if domain.startswith(".") else "." + domain,
                    "path": str(row.get("path") or "/") or "/"})
        if len(out) >= MAX_COOKIES:
            break
    if skipped:
        log.info("передача входа: отброшено %d записей из %d", skipped, len(rows))
    return out


def summary(state: dict) -> str:
    """Чем отчитаться человеку. Значений кук здесь нет и быть не может.

    Показать значение куки входа на экране — то же самое, что положить его на
    стол: это и есть вход. Поэтому только счёт и имена, да и имена лишь затем,
    чтобы человек увидел знакомое и понял, что приехало именно то.
    """
    cookies = (state or {}).get("cookies") or []
    names = [c.get("name") for c in cookies if isinstance(c, dict) and c.get("name")]
    shown = ", ".join(sorted(names)[:6])
    tail = f" и ещё {len(names) - 6}" if len(names) > 6 else ""
    return f"Принято кук: {len(names)}{(' — ' + shown + tail) if shown else ''}."


def looks_logged_in(chain: str, state: dict) -> bool | None:
    """Похоже ли, что это вход, а не банка гостя. None — «не знаем».

    None здесь честнее False. Имя куки входа достоверно известно ТОЛЬКО у
    Магнита; у остальных пяти сетей в signals.AUTH_COOKIE пустые списки, и
    объявить их банку гостевой было бы выдумкой ровно такой же силы, как
    объявить её входом.
    """
    from app.shopbrowser import signals

    want = signals.AUTH_COOKIE.get(chain) or ()
    if not want:
        return None
    have = {c.get("name") for c in (state or {}).get("cookies") or [] if isinstance(c, dict)}
    return any(name in have for name in want)


__all__ = ["parse", "summary", "looks_logged_in", "Rejected",
           "MAX_COOKIES", "MAX_VALUE", "MAX_TEXT"]
