"""Замер: слушает ли витрина куки выбранной точки — и уходит ли окно выбора.

ЗАЧЕМ. app/shopbrowser/point.py переносит в окно магазина ту же точку, по которой
считали, куками shopCode / x_shop_type / nmg_dt. Что эти куки выбирают магазин,
замерено 12.09.2026 — но замерено на ШЛЮЗЕ (magnit.ru/webgate), а наряд ходит по
ВИТРИНЕ. Это разные ответы, и подставлять одно вместо другого нельзя: витрина
могла бы держать свой выбор в localStorage и куки не читать вовсе.

Инструмент открывает сеть дважды — без кук и с куками — и сравнивает ровно то,
что мешало наряду: держит ли витрина окно «Выберите магазин или адрес» и называет
ли она в шапке наш магазин.

    python -m tools.pointcheck                 точка из расчёта
    python -m tools.pointcheck magnit 995025   заданный код

Запросов на запись здесь нет: инструмент только открывает страницы и читает их.
"""
from __future__ import annotations

import sys

from app.models import Location
from app.shopbrowser import driver, point, signals

# Два разных «человека» нарочно: у драйвера сеанс заводится на пару сеть+телефон,
# и открыв обе пробы под одним, мы сравнили бы контекст сам с собой.
BARE = "79990000051"
WITH = "79990000052"


def _see(chain: str, phone: str, state: dict | None, url: str | None = None) -> dict:
    """Открыть сеть и снять то, что решает судьбу наряда."""
    out: dict = {}
    try:
        driver.open_store(chain, phone, state=state, url=url)
    except Exception as exc:  # noqa: BLE001 — недоступный браузер тоже ответ
        return {"итог": f"окно не открылось: {type(exc).__name__}: {str(exc)[:140]}"}

    def read(page) -> dict:
        text = page.inner_text("body")[:20000]
        # ЗНАЧЕНИЯ КУК, А НЕ ИХ ИМЕНА. Первый заход мерил имена — и намерил
        # пустое: витрина ставит shopCode / x_shop_type / nmg_dt САМА, своим
        # магазином по умолчанию, поэтому «куки на месте» верно и без нас.
        # Вопрос, ради которого всё затевалось, звучит иначе: чей магазин в них
        # остался после загрузки — наш или её.
        ours = {c["name"]: c["value"] for c in page.context.cookies()
                if c["name"] in ("shopCode", "x_shop_type", "nmg_dt")}
        return {"текст": " ".join(text.split())[:200],
                "куки": ours,
                "ждёт магазин": signals.needs_store(text)}

    try:
        out.update(driver.run(chain, phone, read, timeout=45))
    except Exception as exc:  # noqa: BLE001
        out["итог"] = f"страница не прочиталась: {type(exc).__name__}: {str(exc)[:140]}"
    return out


def check(chain: str, code: str | None = None, url: str | None = None) -> None:
    place = Location(store_id=code, shop_type="ME", delivery=True) if code else None
    cookies = point.cookies_for(chain, place)
    want = {c["name"]: c["value"] for c in cookies}
    print(f"=== {chain} ===")
    print(f"куки точки: {want or 'нет — точка неизвестна'}")
    if url:
        print(f"страница: {url}")
    if not cookies:
        print("СТОП: без кода точки мерить нечего — расчёт её не дал.")
        return

    bare = _see(chain, BARE, None, url)
    with_point = _see(chain, WITH, point.with_point(None, chain, place), url)

    for name, seen in (("БЕЗ кук", bare), ("С куками", with_point)):
        print(f"\n--- {name} ---")
        for key, value in seen.items():
            print(f"  {key}: {value}")

    print("\n--- вывод ---")
    got = with_point.get("куки") or {}
    # Главный вопрос — пережил ли НАШ магазин загрузку страницы. Витрина ставит
    # эти же куки своим магазином по умолчанию, и «куки на месте» ничего не
    # доказывает: доказывает только совпадение значения с тем, что мы положили.
    survived = got.get("shopCode") == want.get("shopCode")
    print(f"наш магазин пережил загрузку: {survived}"
          f"  (положили {want.get('shopCode')}, осталось {got.get('shopCode')})")
    print(f"витрина сама выбрала: {(bare.get('куки') or {}).get('shopCode')}")
    if bare.get("ждёт магазин") and not with_point.get("ждёт магазин"):
        print("ОКНО ВЫБОРА УШЛО: куки его закрывают.")
    elif bare.get("ждёт магазин"):
        print("ОКНО ВЫБОРА ОСТАЛОСЬ и с куками — витрина слушает не их.")
    else:
        print("ОКНО ВЫБОРА не встретилось ни в одном заходе — на этой странице его нет.")


def dump(chain: str, url: str | None = None) -> None:
    """Всё, чем витрина помнит выбор: куки и localStorage, именами и значениями.

    Понадобилось 20.09.2026, когда замер с рабочей машины и замер с боевого
    сервера разошлись на одной и той же странице: локально окно «Выберите
    магазин или адрес» не появлялось вовсе, а с сервера оно стоит даже с нашей
    кукой точки. Значит витрина держит согласие где-то ещё, и найти это «ещё»
    можно только сравнив два состояния целиком — гадать тут не на чем.

        python -m tools.pointcheck dump magnit
    """
    phone = "79990000053"
    try:
        driver.open_store(chain, phone, url=url)
    except Exception as exc:  # noqa: BLE001
        print(f"окно не открылось: {type(exc).__name__}: {str(exc)[:160]}")
        return

    def read(page) -> dict:
        text = page.inner_text("body")[:20000]
        local = page.evaluate(
            "() => { const o = {}; try { for (let i = 0; i < localStorage.length; i++) {"
            " const k = localStorage.key(i); o[k] = (localStorage.getItem(k)||'').slice(0, 120); } }"
            " catch (e) { o['—'] = 'закрыт'; } return o; }")
        return {"куки": {c["name"]: c["value"][:80] for c in page.context.cookies()},
                "localStorage": local,
                "ждёт магазин": signals.needs_store(text)}

    try:
        seen = driver.run(chain, phone, read, timeout=45)
    except Exception as exc:  # noqa: BLE001
        print(f"страница не прочиталась: {type(exc).__name__}: {str(exc)[:160]}")
        return
    print(f"=== {chain} {url or ''} ===")
    print(f"ждёт магазин: {seen['ждёт магазин']}")
    for where in ("куки", "localStorage"):
        print(f"\n--- {where} ({len(seen[where])}) ---")
        for name, value in sorted(seen[where].items()):
            print(f"  {name} = {value}")


def dismiss(chain: str, code: str, url: str) -> None:
    """Проверка: закрывает ли «Не сейчас» окно выбора, оставляя НАШ магазин.

    ПОЧЕМУ ЭТО НЕ «ВЫБОР ЗА ЧЕЛОВЕКА». Окно просит выбрать магазин — а он уже
    выбран, нашей кукой, той самой точкой, по которой считали. «Не сейчас»
    означает ровно «оставить как есть», то есть отказ выбирать, а не выбор. Если
    после него магазин остался наш и кнопка «в корзину» на месте, наряд проходит
    сам, без человека, и собирает корзину в точке расчёта.

    А если после «Не сейчас» магазин подменяется или кнопка исчезает — значит
    так нельзя, и это надо знать до того, как приложение начнёт так делать.

        python -m tools.pointcheck dismiss magnit 264856 <адрес карточки>
    """
    place = Location(store_id=code, shop_type="ME", delivery=True)
    phone = "79990000054"
    try:
        driver.open_store(chain, phone, state=point.with_point(None, chain, place), url=url)
    except Exception as exc:  # noqa: BLE001
        print(f"окно не открылось: {type(exc).__name__}: {str(exc)[:160]}")
        return

    def read(page) -> dict:
        text = page.inner_text("body")[:20000]
        return {"ждёт магазин": signals.needs_store(text),
                "shopCode": next((c["value"] for c in page.context.cookies()
                                  if c["name"] == "shopCode"), None),
                "кнопки": page.eval_on_selector_all(
                    "button, [role=button]",
                    "els => els.map(e => (e.innerText||'').trim())"
                    ".filter(t => /^(в корзину|добавить в корзину|не сейчас|выбрать)$/i.test(t))"),
                "текст": " ".join(text.split())[:160]}

    def press(page) -> str:
        node = page.get_by_role("button", name="Не сейчас").first
        node.click(timeout=8000)
        driver._settle(page, 1.2)
        return "нажато"

    try:
        before = driver.run(chain, phone, read, timeout=45)
        print("=== до нажатия ===")
        for key, value in before.items():
            print(f"  {key}: {value}")
        if not before["ждёт магазин"]:
            print("\nокна выбора нет — нажимать нечего")
            return
        print("\n" + driver.run(chain, phone, press, timeout=30))
        after = driver.run(chain, phone, read, timeout=45)
    except Exception as exc:  # noqa: BLE001
        print(f"не вышло: {type(exc).__name__}: {str(exc)[:200]}")
        return

    print("=== после «Не сейчас» ===")
    for key, value in after.items():
        print(f"  {key}: {value}")
    print("\n--- вывод ---")
    kept = after["shopCode"] == before["shopCode"]
    can = any("корзин" in b.lower() for b in after["кнопки"])
    print(f"окно ушло: {not after['ждёт магазин']}; магазин остался наш: {kept}; "
          f"кнопка «в корзину» на месте: {can}")


def naryad(chain: str, code: str, url: str) -> None:
    """Сквозная проверка: настоящий наряд, настоящая карточка, счётчик сети.

    Мерит не куски, а весь путь целиком — тот самый, которым пойдёт передача
    человека: точка расчёта уходит кукой, окно выбора закрывается «Не сейчас»,
    карточка открывается, кнопка нажимается, и последнее слово говорит счётчик
    корзины САМОЙ СЕТИ. Иначе легко объявить рабочим то, что отчитывается
    успехом и не кладёт ничего, — так уже было с METRO 19.09.2026.

    Кладётся один товар в ГОСТЕВУЮ корзину сеанса: ни заказа, ни оплаты,
    ни чужого кабинета. Сеанс закрывается вместе с окном.

        python -m tools.pointcheck naryad magnit 264856 <адрес карточки>
    """
    from app.cartplan import CartPlan, PlanLine
    from app.shopbrowser import cart
    from app.shopbrowser import store as shopstore

    phone = "79990000055"
    place = Location(store_id=code, shop_type="ME", delivery=True)
    # Точка приходит не из рабочего места, а из строки запуска: инструмент
    # должен мерить заданную точку, а не ту, что случилась на этом сервере.
    import app.location as client_place
    client_place.for_store = lambda _code: place            # noqa: E731
    # Гостевой сеанс вместо сохранённого входа. Наряд требует входа и правильно
    # делает: без него корзина у сети своя на каждое устройство. Здесь нам ровно
    # это и нужно — своя, гостевая, чтобы ничего чужого не тронуть.
    shopstore.load = lambda _chain: {"cookies": [], "origins": []}   # noqa: E731

    # И ОДНА ПОДМЕНА, КОТОРУЮ НАДО НАЗВАТЬ ВСЛУХ. Наряд не идёт к невошедшему, и
    # правильно делает: у гостя корзина своя на каждое устройство. Но здесь
    # меряется НЕ вход, а всё, что после него, — точка, окно выбора, карточка,
    # нажатие, счётчик. Поэтому проверка входа здесь объявляется пройденной, и
    # только она. Значит и ответ этой пробы говорит ровно об этом куске пути:
    # «вошедшему наряд сработает» она НЕ доказывает, это проверяется сеансом
    # самого человека.
    look = driver.look
    driver.look = lambda *a, **kw: {**look(*a, **kw), "logged_in": True}  # noqa: E731

    plan = CartPlan(store_code=chain, lines=[
        PlanLine(sku="проба", qty=1, name="проба наряда", unit="pcs", price=None, url=url)])

    print(f"=== сквозной наряд, {chain}, точка {code} ===")
    got = cart.deliver(chain, phone, plan)
    print(f"легло: {got['ok']}")
    print(f"не легло: {got['failed']}")
    print(f"итог: {got['note']}")



# Адрес корзины у сетей, где она открывается ссылкой. Снят с самой витрины
# (tools/cartcheck.py показывает ссылки её шапки), а не выдуман: у Магнита
# такой ссылки нет вовсе, у ВкусВилла она есть.
CART_PAGE = {"vkusvill": "https://vkusvill.ru/cart/"}


def _buttons(page) -> str:
    """Подписи видимых кнопок страницы — по ним видно, что нарисовала витрина."""
    names = page.eval_on_selector_all(
        "button, [role=button]",
        r"els => els.filter(e => e.offsetParent !== null)"
        r".map(e => ((e.getAttribute('aria-label')||'') + ' ' + (e.innerText||''))"
        r".replace(/\s+/g, ' ').trim()).filter(t => t && t.length < 30)")
    return " | ".join(dict.fromkeys(names))


def counter(chain: str, code: str, url: str) -> None:
    """Что именно читает счётчик корзины и откуда берётся его число.

    Понадобилось 20.09.2026: сквозной наряд положил ОДИН товар, а счётчик
    отчитался «208 позиц.». Число в отчёте — последнее слово, которое человек
    читает перед тем, как пойти оформлять заказ, и соврать им хуже, чем
    промолчать. Поэтому смотрим не итог, а все узлы-кандидаты целиком.

        python -m tools.pointcheck counter magnit 264856 <адрес карточки>
    """
    place = Location(store_id=code, shop_type="ME", delivery=True)
    phone = "79990000056"
    driver.open_store(chain, phone, state=point.with_point(None, chain, place), url=url)

    # Смотреть надо на НЕПУСТУЮ корзину. Пустую Магнит подписывает «Корзина» без
    # числа, и по ней не отличить «счётчик без числа» от «счётчика не нашли» —
    # а разница между ними решает, будет ли у наряда последняя проверка вообще.
    def fill(page) -> str:
        from app.shopbrowser import cart as naryad_cart

        page.get_by_role("button", name="Не сейчас").first.click(timeout=8000)
        driver._settle(page, 1.2)
        node = naryad_cart._add_button(page, chain, again=False)
        if node is None:
            return "кнопки «в корзину» не нашлось"
        node.click(timeout=8000)
        driver._settle(page, 2.0)
        return "товар положен"

    try:
        print(driver.run(chain, phone, fill, timeout=60))
    except Exception as exc:  # noqa: BLE001
        print(f"положить не вышло: {type(exc).__name__}: {str(exc)[:160]}")

    def read(page):
        return page.evaluate(r"""() => {
            const out = [];
            for (const e of document.querySelectorAll('a,button,[role=button]')) {
                const label = (e.getAttribute('aria-label') || '');
                const text = (e.innerText || '');
                const t = (label + ' ' + text).replace(/\s+/g, ' ').trim();
                if (!/корзин/i.test(t)) continue;
                out.push({tag: e.tagName, label: label.slice(0, 60),
                          text: text.replace(/\s+/g, ' ').trim().slice(0, 90),
                          children: e.querySelectorAll('*').length});
            }
            return out;
        }""")

    for row in driver.run(chain, phone, read, timeout=45):
        print(f"  <{row['tag']}> детей={row['children']}")
        print(f"     aria-label: {row['label']!r}")
        print(f"     текст: {row['text']!r}")

    def open_cart(page) -> str:
        """Легла ли позиция — по САМОЙ КАРТОЧКЕ, а не по шапке.

        Шапку пробовали, и она не годится: кнопка корзины Магнита числа не
        показывает ни при пустой корзине, ни при полной, а на карточке она к
        тому же невидима (замер 20.09.2026: узел один, видимых ноль). Страницы
        /cart у него нет — по этому адресу отвечает «Здесь ничего не нашлось».

        Зато у самой карточки есть признак, который ни с чем не спутать: у
        ПОЛОЖЕННОГО товара витрина рисует счётчик «− 1 +» вместо кнопки
        «В корзину». Перезагружаем карточку и смотрим, что на ней теперь.
        """
        out = ["сразу после нажатия: " + _buttons(page)]
        page.reload(wait_until="domcontentloaded", timeout=45000)
        driver._settle(page, 2.5)
        out.append("после перезагрузки карточки: " + _buttons(page))
        return chr(10).join(out)
        try:
            node.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            node.click(timeout=8000, force=True)
            out.append(f"обычное нажатие не прошло ({type(exc).__name__}), нажал силой")
        driver._settle(page, 2.0)
        out.append("после нажатия на корзину:")
        out.append(" ".join(page.inner_text("body")[:1200].split()))
        return chr(10).join(out)

    try:
        print("\n--- страница корзины ---")
        print(driver.run(chain, phone, open_cart, timeout=60)[:2200])
    except Exception as exc:  # noqa: BLE001
        print(f"корзина не открылась: {type(exc).__name__}: {str(exc)[:160]}")


def one(chain: str, code: str, url: str) -> None:
    """Одна позиция через настоящий _put_one, с числами внутри него.

    Нужен потому, что сквозной наряд отвечает «легло», а корзина пуста, и по
    его ответу не видно, на каком шаге теряется правда: поставился ли счётчик
    запросов, сколько их насчиталось, что вернула сама укладка.

        python -m tools.pointcheck one magnit 264856 <адрес карточки>
    """
    from app.shopbrowser import cart

    place = Location(store_id=code, shop_type="ME", delivery=True)
    phone = "79990000057"
    driver.open_store(chain, phone, state=point.with_point(None, chain, place), url=url)

    def work(page):
        out = {}
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        driver._settle(page, 1.0)
        out["ждёт магазин"] = signals.needs_store(page.inner_text("body")[:20000])
        if out["ждёт магазин"]:
            out["окно закрыто"] = cart._keep_point(page)
        out["счётчик поставлен"] = cart._watch_cart(page, chain)
        out["ответы до"] = cart._cart_calls(page, chain)
        node = cart._add_button(page, chain, again=False)
        out["кнопка"] = (node.get_attribute("aria-label") or node.inner_text() or "") if node else None
        if node:
            node.scroll_into_view_if_needed(timeout=5000)
            node.click(timeout=8000)
        driver._settle(page, 3.0)
        out["ответы своей корзины"] = cart._cart_calls(page, chain)
        out["отказ"] = cart._refused(out["ответы своей корзины"] or [])
        out["всё, что видела страница"] = [
            (str(u)[:70], c) for u, c in (page.evaluate("() => window.__kzSeen") or [])]
        # И последнее слово — сама карточка после перезагрузки: у положенного
        # товара витрина рисует счётчик вместо кнопки «В корзину».
        page.reload(wait_until="domcontentloaded", timeout=45000)
        driver._settle(page, 2.5)
        out["кнопки после перезагрузки"] = _buttons(page)
        # У сетей, чья корзина живёт по своему адресу, последнее слово за ней:
        # это единственный способ отличить «нажали» от «легло». У Магнита такого
        # адреса нет — /cart отвечает «Здесь ничего не нашлось».
        where = CART_PAGE.get(chain)
        if where:
            page.goto(where, wait_until="domcontentloaded", timeout=45000)
            driver._settle(page, 2.5)
            out["страница корзины"] = " ".join(page.inner_text("body")[:700].split())
        return out

    for key, value in driver.run(chain, phone, work, timeout=90).items():
        print(f"  {key}: {value!r}")


def spy(chain: str, code: str, url: str) -> None:
    """Что именно сеть шлёт и получает по корзине: адреса, коды и тело ответа.

    Понадобилось 20.09.2026: запрос к корзине уходит и получает 200, а карточка
    после перезагрузки снова предлагает «Добавить в корзину». Дальше гадать
    нельзя — надо посмотреть, что лежит в ответах самой сети.

    Обёртка ставится через add_init_script, а не evaluate: переход на страницу
    стирает всё, что вписано в окно, и загрузочный запрос корзины прошёл бы мимо.

        python -m tools.pointcheck spy magnit 264856 <адрес карточки>
    """
    from app.shopbrowser import cart

    place = Location(store_id=code, shop_type="ME", delivery=True)
    phone = "79990000058"
    driver.open_store(chain, phone, state=point.with_point(None, chain, place))

    hook = r"""
    (() => {
      // Пишем ВСЁ, а не только совпавшее по слову: адрес корзины у незнакомой
      // сети как раз и надо найти, а отбор по заранее выбранным словам показал
      // бы только то, что мы уже угадали. Чужие счётчики отсеет глаз.
      window.__spy = [];
      const hit = () => true;
      const real = window.fetch;
      window.fetch = function (...a) {
        const first = a[0];
        const u = (typeof first === 'string') ? first : (first && first.url) || '';
        const m = (a[1] && a[1].method) || (first && first.method) || 'GET';
        let body = '';
        try { body = (a[1] && a[1].body) ? String(a[1].body).slice(0, 160) : ''; } catch (e) {}
        const p = real.apply(this, a);
        if (hit(u) && !/yandex|kaspersky|bumlam|google|criteo|vk\.com|mail\.ru|doubleclick/i.test(u)) {
          p.then(async (r) => {
            let text = '';
            try { text = (await r.clone().text()).slice(0, 400); } catch (e) { text = '<не прочитано>'; }
            window.__spy.push([m, String(u).slice(0, 120), r.status, body, text]);
          }, () => window.__spy.push([m, String(u).slice(0, 120), 0, body, '<отказ сети>']));
        }
        return p;
      };
    })();
    """

    def work(page):
        page.add_init_script(hook)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        driver._settle(page, 1.5)
        if signals.needs_store(page.inner_text("body")[:20000]):
            cart._keep_point(page)
        node = cart._add_button(page, chain, again=False)
        if node is None:
            return [["", "кнопки не нашлось", 0, "", ""]]
        node.scroll_into_view_if_needed(timeout=5000)
        node.click(timeout=8000)
        driver._settle(page, 3.0)
        after_add = list(page.evaluate("() => window.__spy || []"))
        # Перезагрузка: витрина сама перечитает корзину, и в её ответе будет
        # видно, лежит ли там наш товар. Это и есть искомое доказательство.
        page.reload(wait_until="domcontentloaded", timeout=45000)
        driver._settle(page, 3.0)
        return after_add + [["--- после перезагрузки ---", "", 0, "", ""]] +             list(page.evaluate("() => window.__spy || []"))

    for method, address, status, body, text in driver.run(chain, phone, work, timeout=120):
        print(f"\n  {method} {status} {address}")
        if body:
            print(f"    отправлено: {body}")
        if text:
            print(f"    получено: {text}")


def main(argv: list[str]) -> int:
    if argv and argv[0] == "spy":
        spy(argv[1], argv[2], argv[3])
        return 0
    if argv and argv[0] == "one":
        one(argv[1], argv[2], argv[3])
        return 0
    if argv and argv[0] == "counter":
        counter(argv[1], argv[2], argv[3])
        return 0
    if argv and argv[0] == "naryad":
        naryad(argv[1], argv[2], argv[3])
        return 0
    if argv and argv[0] == "dismiss":
        dismiss(argv[1], argv[2], argv[3])
        return 0
    if argv and argv[0] == "dump":
        dump(argv[1] if len(argv) > 1 else "magnit",
             argv[2] if len(argv) > 2 else None)
        return 0
    chain = argv[0] if argv else "magnit"
    code = argv[1] if len(argv) > 1 else None
    url = argv[2] if len(argv) > 2 else None
    check(chain, code, url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
