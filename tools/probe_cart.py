"""Разведка последнего шага: что сеть говорит про вход и про корзину.

Зачем отдельный инструмент, а не разовые команды. Ответ сети зависит от того,
ОТКУДА спрошено: с машины владельца и из встроенного браузера российские витрины
видны иначе, чем с боевого сервера, и половина прежних выводов была сделана не про
сеть, а про место замера. Поэтому замер живёт в репозитории и запускается на
сервере тем же слоем http_get, которым ходят сборщики: с браузерным UA, с паузой
и с повтором при 403 — чтобы «сбавь темп» не читалось как «закрыто».

Запросы здесь ТОЛЬКО ЧИТАЮЩИЕ. В чужую корзину ничего не кладётся: задача замера —
узнать, существует ли у сети канал и что он отвечает без входа, а не наполнить
что-то. Наполняет корзину человек, войдя в свой аккаунт (app/shopbrowser/cart.py).

    python -m tools.probe_cart            все сети
    python -m tools.probe_cart metro      одну
"""
from __future__ import annotations

import json
import re
import sys

import requests

from app.catalog.crawlers import USER_AGENT, Pace, http_get

# Что спрашиваем у каждой сети. Адреса взяты из публичных мест: спецификация METRO
# выложена ею самой (api.metro-cc.ru/docs), остальные — то, чем живёт витрина сети.
PROBES: dict[str, list[tuple[str, str]]] = {
    "metro": [
        ("спецификация", "https://api.metro-cc.ru/docs"),
        ("торговые центры", "https://api.metro-cc.ru/api/v1/tradecenters"),
        ("корзина без токена", "https://api.metro-cc.ru/api/v1/16/eshop/basket/"),
        ("витрина", "https://online.metro-cc.ru/"),
    ],
    "perekrestok": [
        ("витрина", "https://www.perekrestok.ru/"),
        ("поиск", "https://www.perekrestok.ru/api/customer/1.4/catalog/search?page=1&perPage=3"),
        ("корзина", "https://www.perekrestok.ru/api/customer/1.4/basket"),
        ("кто я", "https://www.perekrestok.ru/api/customer/1.4/user/current"),
    ],
    "vprok": [
        ("витрина", "https://www.vprok.ru/"),
        ("корзина", "https://www.vprok.ru/web/api/v1/cart"),
        ("кто я", "https://www.vprok.ru/web/api/v1/user/current"),
        # Сторож для 401: если ерунда отвечает тем же кодом, значит 401 говорит не
        # «маршрут есть, нужен вход», а «сюда вообще не ходят», и вывод был бы ложным.
        ("выдуманный адрес", "https://www.vprok.ru/web/api/v1/net-takogo-metoda"),
    ],
    "monetka": [
        ("витрина", "https://monetka.ru/"),
        ("карточка", "https://monetka.ru/product/sok-dobryjj-yabloko-1l-rossiya-810000639/"),
        ("вход", "https://monetka.ru/personal/"),
    ],
}


def look(title: str, url: str, pace: Pace) -> None:
    """Один читающий запрос. Ошибка сети — тоже ответ, поэтому наружу не бросаем."""
    try:
        # Свой Accept сюда не передаём: http_get собирает заголовки сам, и второй
        # набор уходит в requests.get вторым значением того же имени — TypeError.
        response = http_get(url, pace=pace, timeout=25.0, retries=2)
    except requests.RequestException as exc:
        print(f"  {title:22} не дозвонились: {type(exc).__name__}")
        return
    except Exception as exc:  # noqa: BLE001 — CrawlBlocked и прочее: это тоже ответ
        print(f"  {title:22} отказ: {type(exc).__name__}: {str(exc)[:120]}")
        return

    body = response.text or ""
    kind = (response.headers.get("Content-Type") or "").split(";")[0]
    print(f"  {title:22} код {response.status_code}  {len(response.content):>8} байт  {kind}")

    if "json" in kind:
        try:
            data = json.loads(body)
        except ValueError:
            print(f"      {body[:200]}")
            return
        keys = list(data)[:12] if isinstance(data, dict) else f"список из {len(data)}"
        print(f"      ключи: {keys}")
        print(f"      {json.dumps(data, ensure_ascii=False)[:300]}")
    else:
        flat = " ".join(body.split())
        print(f"      {flat[:220]}")


def metro_hash() -> None:
    """Главный вопрос METRO: чья это корзина и увидит ли её человек в браузере.

    Спецификация зовёт user_hash «уникальным хешем пользователя, владеющего
    корзиной», и по нему корзина и находится. Значит всё решает одно: берёт ли
    витрина тот же хеш из куки. Если да — корзину, наполненную с сервера, человек
    откроет у себя; если нет — она останется нашей и человеку бесполезной.
    """
    print("\n=== metro: чей это хеш ===")
    api = requests.Session()
    api.headers["User-Agent"] = USER_AGENT
    basket = "https://api.metro-cc.ru/api/v1/16/eshop/basket/"

    first = api.get(basket, timeout=25).json().get("data") or {}
    print(f"  первый запрос   user_hash={first.get('user_hash')}")
    print(f"  куки после него {api.cookies.get_dict() or '—'}")

    second = api.get(basket, timeout=25).json().get("data") or {}
    print(f"  второй запрос   user_hash={second.get('user_hash')}")
    print("  хеш тот же" if first.get("user_hash") == second.get("user_hash")
          else "  хеш КАЖДЫЙ РАЗ НОВЫЙ — сам по себе он корзину не удержит")

    given = first.get("user_hash")
    named = api.get(basket, params={"user_hash": given}, timeout=25).json().get("data") or {}
    print(f"  спросили по хешу user_hash={named.get('user_hash')}  "
          f"{'тот же — хеш принимается' if named.get('user_hash') == given else 'ДРУГОЙ — хеш не принят'}")

    site = requests.Session()
    site.headers["User-Agent"] = USER_AGENT
    page = site.get("https://online.metro-cc.ru/", timeout=30)
    jar = site.cookies.get_dict()
    print(f"  витрина: код {page.status_code}, куки {list(jar)}")
    for name, value in jar.items():
        if len(value) == 32 and all(c in "0123456789abcdef" for c in value):
            print(f"      кука {name} похожа на хеш: {value}")
    where = page.text.find("user_hash")
    print(f"      слово user_hash в разметке: {'есть, ' + page.text[where:where + 80] if where >= 0 else 'нет'}")


def metro_put() -> None:
    """Проверка того, на чём всё держится: кладётся ли товар в корзину METRO.

    Кладём В СВОЮ гостевую корзину — ту, которую сеть завела этому запросу секунду
    назад, — один товар, смотрим, виден ли он, и убираем за собой. Ни аккаунта, ни
    заказа, ни оплаты тут нет: это ровно то, что делает браузер любого посетителя,
    и то единственное, чем можно отличить «метод описан» от «метод работает».
    """
    print("\n=== metro: кладётся ли товар ===")
    api = requests.Session()
    api.headers["User-Agent"] = USER_AGENT

    # Код точки берём У САМОЙ СЕТИ, а не с потолка. Первый замер шёл с выдуманным
    # «16» и получил 400 «Basket not found» — а у METRO id торгового центра и его
    # store_id РАЗНЫЕ поля (в перечне: {"id": 1, "store_id": 10, …}), и корзина
    # живёт у пары «хозяин + точка». Пока точка выдумана, отказ ничего не значит.
    centres = (api.get("https://api.metro-cc.ru/api/v1/tradecenters", timeout=30)
               .json().get("data") or [])
    print(f"  торговых центров у сети: {len(centres)}")
    for row in centres[:3]:
        print(f"      id={row.get('id')} store_id={row.get('store_id')} {row.get('name')}")
    store = str((centres[0] if centres else {}).get("store_id") or "10")

    basket = f"https://api.metro-cc.ru/api/v1/{store}/eshop/basket/"
    data = api.get(basket, timeout=25).json().get("data") or {}
    user_hash = data.get("user_hash")
    print(f"  точка {store}: хеш {user_hash}, позиций {len(data.get('articles') or [])}, "
          f"eshop_basket_id={data.get('eshop_basket_id')!r}")
    for cookie in api.cookies:
        print(f"  кука {cookie.name}: домен {cookie.domain}, HttpOnly="
              f"{bool(cookie.has_nonstandard_attr('HttpOnly'))}")

    # Артикул берём из перечня товаров самой сети, а не из головы: выдуманный номер
    # ответил бы «не найдено», и мы не узнали бы, работает метод или нет.
    #
    # Разбор ответа — общий с коннектором (metro._rows), и это не лень. Свой,
    # написанный по памяти, уже соврал здесь один раз: перечень пришёл, а замер
    # напечатал «товаров нет» — сеть заворачивает список в data.data, а параметр
    # страницы у неё page, а не per_page.
    from app.connectors import metro

    payload = api.get(f"https://api.metro-cc.ru/api/v1/{store}/products",
                      params={"page": 1}, timeout=45).json()
    rows = [r for r in metro._rows(payload) if str(r.get("article") or "").isdigit()]
    if not rows:
        print("  перечень товаров пуст — класть нечего")
        return
    # Берём товар, который сеть показывает в наличии: «нет на складе» отказом на
    # запись не является, но и подтверждением тоже — замер вышел бы пустым.
    live = [r for r in rows if (r.get("stock") or {}).get("value")] or rows
    article = live[0].get("article")
    print(f"  берём артикул {article}: {str(live[0].get('name'))[:60]}")

    put = api.post(basket, params={"user_hash": user_hash},
                   json={"articles": [{"article": int(article), "count": 1}]}, timeout=30)
    print(f"  положили: код {put.status_code}  {put.text[:220]}")

    # «Basket not found» значит, что корзины у этой пары ещё нет, а метода
    # «создать корзину» в спецификации НЕТ. Единственный описанный метод, который
    # мог бы её завести, — установка доставки: проверяем и это, иначе вывод
    # «канал не работает» был бы сделан, не дойдя до конца описанного сетью.
    if put.status_code != 200:
        made = api.post(f"{basket}set-delivery-type", params={"user_hash": user_hash,
                                                              "pick_up": 1}, timeout=30)
        print(f"  задали самовывоз: код {made.status_code}  {made.text[:200]}")
        put = api.post(basket, params={"user_hash": user_hash},
                       json={"articles": [{"article": int(article), "count": 1}]}, timeout=30)
        print(f"  положили снова: код {put.status_code}  {put.text[:220]}")

    after = api.get(basket, params={"user_hash": user_hash}, timeout=25).json().get("data") or {}
    lines = after.get("articles") or []
    print(f"  в корзине позиций {len(lines)}, сумма {after.get('total_cost')}")
    if lines:
        print(f"      {json.dumps(lines[0], ensure_ascii=False)[:300]}")

    for line in lines:
        eshop_id = line.get("eshop_product_id") or line.get("id")
        gone = api.delete(basket, params={"user_hash": user_hash,
                                          "eshop_products_id[][eshop_product_id]": eshop_id},
                          timeout=25)
        print(f"  убрали за собой {eshop_id}: код {gone.status_code}")


def api_paths(url: str) -> None:
    """Какими адресами витрина говорит со своим сервером — по её же разметке.

    Гадать пути бесполезно и вредно: у Впрока выдуманный адрес ответил тем же 401,
    что и «корзина», то есть отказ там общий на всю ветку и о существовании метода
    не говорит ничего. Настоящие адреса лежат в самой странице — их и берём.
    """
    print(f"\n=== адреса внутри {url} ===")
    page = http_get(url, pace=Pace(0.25), timeout=40.0, retries=2)
    found = sorted(set(re.findall(r"[\"'](/(?:web/)?api/[a-zA-Z0-9_\-/.{}]{2,60})[\"'?]", page.text)))
    print(f"  страница {len(page.content)} байт, разных адресов {len(found)}")
    for path in found[:40]:
        print(f"      {path}")
    for word in ("cart", "basket", "корзин"):
        hits = len(re.findall(word, page.text, re.I))
        print(f"  слово {word!r} встречается {hits} раз")


def around(url: str, word: str) -> None:
    """Что стоит рядом со словом в разметке. Когда путей в HTML нет, они в скриптах."""
    print(f"\n=== {word!r} внутри {url} ===")
    page = http_get(url, pace=Pace(0.25), timeout=40.0, retries=2)
    text = page.text
    seen: set[str] = set()
    for match in re.finditer(word, text, re.I):
        piece = " ".join(text[max(0, match.start() - 70):match.end() + 70].split())
        if piece not in seen:
            seen.add(piece)
            print(f"      …{piece}…")
        if len(seen) >= 18:
            break
    scripts = sorted(set(re.findall(r'src="(/_next/[^"]+\.js)"', text)))
    print(f"  скриптов страницы: {len(scripts)}")
    for src in scripts[:12]:
        print(f"      {src}")


def metro_window(article: str = "117189",
                 slug: str = "aro-sakhar-pesok-ts2-belyy-kristallicheskiy-1kg",
                 store: str = "10") -> None:
    """Кто заводит корзину METRO, если метода «создать» в спецификации нет.

    ЗАЧЕМ ЭТОТ ЗАМЕР. POST наполнения отвечает 400 «Basket not found» и на
    выдуманной точке, и на настоящей (store_id=10, снят из перечня самой сети).
    Метода создания корзины в спецификации НЕТ. Значит остаётся один вопрос, и он
    решает судьбу канала: заводит ли корзину сама витрина, когда человек кладёт в
    неё первый товар. Если да — наш POST будет доливать уже существующую корзину,
    и перед передачей человеку надо положить одну позицию руками. Если нет —
    запись закрыта без именного токена, и это надо знать точно, а не гадать.

    Кладём ОДНУ позицию в гостевую корзину нашего же серверного браузера, тем же
    кодом, которым идёт передача (app/shopbrowser/cart._put_one). Ни аккаунта, ни
    заказа, ни оплаты.
    """
    from app.connectors import metro_cart
    from app.shopbrowser import cart, driver

    phone = "79990000042"
    url = f"https://online.metro-cc.ru/products/{slug}"
    print(f"\n=== metro: заводит ли корзину сама витрина ===\n  {url}")

    driver.open_store("metro", phone)
    before = driver.remember("metro", phone)
    hash_before = metro_cart.hash_of(before)
    print(f"  хеш до нажатия: {hash_before}")

    item = {"sku": article, "qty": 1, "url": url, "name": "Сахар aro 1 кг", "unit": "pcs"}
    done, why, mark, put = driver.run(
        "metro", phone, lambda page: cart._put_one(page, "metro", item), timeout=120)
    print(f"  нажали «в корзину»: легло={done} положено={put} причина={why!r}")

    after = driver.remember("metro", phone)
    hash_after = metro_cart.hash_of(after)
    print(f"  хеш после нажатия: {hash_after} "
          f"({'тот же' if hash_after == hash_before else 'ДРУГОЙ'})")

    # Точку витрина держит в своей куке. Спрашивать корзину у ЧУЖОЙ точки
    # бессмысленно: корзина у METRO живёт у пары «хозяин + торговый центр», и
    # пустой ответ сказал бы не «не завела», а «смотрим не туда».
    jar = {c.get("name"): c.get("value") for c in (after.get("cookies") or [])
           if isinstance(c, dict)}
    chosen = jar.get("metroStoreId")
    print(f"  точка, выбранная витриной: metroStoreId={chosen!r}")

    for where in dict.fromkeys([chosen, store, "16"]):
        if not where or not hash_after:
            continue
        basket = metro_cart.read(str(where), hash_after)
        print(f"  корзина в точке {where}: позиций {basket.count}, сумма {basket.total}, "
              f"eshop_basket_id={basket.raw.get('eshop_basket_id')!r}")
        if basket.count:
            print(f"      {json.dumps(basket.lines[0], ensure_ascii=False)[:300]}")
            print(f"  ВЫВОД: корзину заводит витрина (точка {where}), наш POST будет доливать её.")
            break
    else:
        # Витрина могла и не положить: «легло» у нас читается по состоянию кнопки,
        # и на чужой вёрстке это бывает ложным успехом. Единственный честный
        # свидетель — САМА страница корзины, глазами. look() сюда не годится: он
        # отдаёт разбор, а не текст.
        # Адрес корзины НЕ УГАДЫВАЕМ: /basket ответил «Страница не найдена», и ещё
        # три попытки наугад стоили бы столько же и сказали бы столько же. Ссылку
        # на свою корзину витрина рисует сама — её и спрашиваем у страницы.
        def find_basket(page) -> dict:
            links = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.getAttribute('href'))")
            cartish = sorted({h for h in links if h and
                              any(w in h.lower() for w in ("cart", "basket", "korzin"))})
            head = " ".join((page.inner_text("header") or "").split())[:200]
            return {"ссылки": cartish[:10], "шапка": head}

        print(f"  {driver.run('metro', phone, find_basket, timeout=90)}")

        # Последний честный вопрос: а САМА витрина каким адресом наполняет корзину?
        # Способ тот же, которым снята docs/design/store-apis.md: спросить у
        # браузера список адресов, по которым сходила страница. Ничего не
        # перехватываем и не подменяем — сеть сама называет свои адреса тому, кто
        # её открыл.
        def own_calls(page) -> dict:
            urls = page.evaluate(
                "performance.getEntriesByType('resource').map(e => e.name)")
            api = sorted({u.split("?")[0] for u in urls if "api.metro-cc.ru" in u})
            cart = sorted({u for u in urls if "basket" in u.lower() or "cart" in u.lower()})
            return {"к интерфейсу сети": api[:12], "про корзину": cart[:8],
                    "всего адресов": len(urls)}

        print(f"  {driver.run('metro', phone, own_calls, timeout=90)}")
        print("  ВЫВОД: у интерфейса корзины нет. Чем живёт витрина — выше.")
    driver.close("metro", phone)


def main(argv: list[str]) -> int:
    if argv[:1] == ["metro-window"]:
        metro_window(*argv[1:])
        return 0
    if argv[:1] == ["around"]:
        around(argv[1], argv[2])
        return 0
    if argv[:1] == ["paths"]:
        for url in argv[1:]:
            api_paths(url)
        return 0
    if argv[:1] == ["metro-put"]:
        metro_put()
        return 0
    if argv[:1] == ["metro-hash"]:
        metro_hash()
        return 0
    chains = argv or list(PROBES)
    pace = Pace(0.25)                     # один запрос в четыре секунды: темп покупателя
    for chain in chains:
        probes = PROBES.get(chain)
        if not probes:
            print(f"{chain}: такой сети в замере нет")
            continue
        print(f"\n=== {chain} ===")
        for title, url in probes:
            look(title, url, pace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
