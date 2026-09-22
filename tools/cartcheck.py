"""Живая проверка браузерного наряда: открывается ли сеть и где у неё корзина.

ЗАЧЕМ. Наряд (app/shopbrowser/cart.py) считает позицию положенной по НАЖАТИЮ:
нашлась кнопка, нажалась — значит легло. 19.09.2026 выяснилось, чего это стоит.
На METRO нажатие прошло, витрина товар приняла (её собственный рекламный пиксель
ушёл с product_add_to_cart), а корзина, которую мы умели читать, осталась пустой —
и мы едва не записали «канал не работает» там, где не работало только чтение.
Обратная ошибка дороже: нажатие может «пройти» на кнопке, которая ничего не
кладёт, и человек прочитает «передано 16», а придёт к пустой корзине.

Лечится это одним: ПОСЛЕ передачи смотреть в корзину сети глазами браузера. Чтобы
так уметь, надо знать у каждой сети две вещи — открывается ли она нашему серверу
вообще и по какому адресу у неё корзина. Ни того, ни другого выдумывать нельзя, и
этот инструмент их СНИМАЕТ, а не угадывает: адрес корзины берётся из ссылок,
которые витрина сама нарисовала у себя в шапке.

    python -m tools.cartcheck            все сети
    python -m tools.cartcheck metro      одну

Запросов на запись здесь нет: инструмент только открывает страницы и читает их.
"""
from __future__ import annotations

import sys

from app.shopbrowser import driver, signals

PHONE = "79990000042"

# Слова, по которым узнаётся ссылка на корзину в чужой шапке. Ищем и по адресу, и
# по подписи: у части сетей ссылка подписана словом, а путь у неё безымянный.
CART_WORDS = ("cart", "basket", "korzin", "корзин")


def look_at(chain: str) -> dict:
    """Одна сеть: открылась ли, не встретила ли проверкой, где у неё корзина."""
    out: dict = {"сеть": chain}
    try:
        driver.open_store(chain, PHONE)
    except Exception as exc:  # noqa: BLE001 — недоступный браузер тоже ответ
        out["итог"] = f"окно не открылось: {type(exc).__name__}: {str(exc)[:120]}"
        return out

    seen = driver.look(chain, PHONE)
    out["адрес"] = seen.get("url")
    out["проверка"] = seen.get("guarded")
    out["вошёл"] = seen.get("logged_in")

    def links(page) -> dict:
        found = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => [e.getAttribute('href'), (e.innerText||'').trim().slice(0,30)])")
        cart = [(href, text) for href, text in found
                if href and any(w in (href + " " + text).lower() for w in CART_WORDS)]
        # Не у всех сетей корзина — ссылка: бывает кнопка, открывающая ящик сбоку.
        # Ищем и их, иначе «ссылок про корзину нет» прочиталось бы как «корзины нет».
        buttons = page.eval_on_selector_all(
            "button, [role=button]",
            "els => els.map(e => ((e.getAttribute('aria-label')||'') + ' ' +"
            " (e.innerText||'')).trim().slice(0,40))")
        cart_btn = [b for b in buttons if any(w in b.lower() for w in CART_WORDS)]
        # И самый честный свидетель: что вообще написано на странице. Ноль ссылок
        # значит либо «не витрина», либо «витрина ещё не отрисовалась», и отличить
        # одно от другого можно только текстом.
        text = " ".join((page.inner_text("body") or "").split())
        return {"всего ссылок": len(found), "про корзину": cart[:6],
                "кнопки про корзину": cart_btn[:4],
                "на странице": text[:260] or "(пусто)"}

    try:
        out.update(driver.run(chain, PHONE, links, timeout=60))
    except Exception as exc:  # noqa: BLE001
        out["ссылки"] = f"не прочитались: {type(exc).__name__}"
    finally:
        try:
            driver.close(chain, PHONE)
        except Exception:  # noqa: BLE001
            pass
    return out


def counter(chain: str, url: str) -> None:
    """Меняется ли счётчик корзины от нашего нажатия — единственная честная проверка.

    Наряд сегодня верит кнопке: нашлась, нажалась — «легло». На METRO 19.09.2026
    это едва не стоило неверного вывода в обе стороны сразу. Счётчик в шапке —
    то, что показывает сама сеть, и подделать его нажатием нельзя.

    Замер: читаем счётчик, кладём одну позицию тем же кодом, что и наряд, читаем
    снова. Разница и есть ответ.
    """
    from app.shopbrowser import cart

    print(f"\n=== {chain}: меняется ли счётчик корзины ===\n  {url}")
    driver.open_store(chain, PHONE)

    def badge(page) -> list[str]:
        return [b for b in page.eval_on_selector_all(
            "a, button, [role=button]",
            "els => els.map(e => ((e.getAttribute('aria-label')||'') + ' ' +"
            " (e.innerText||'')).replace(/\\s+/g,' ').trim().slice(0,40))")
            if "корзин" in b.lower()]

    print(f"  до нажатия:  {driver.run(chain, PHONE, badge, timeout=60)}")

    item = {"sku": "x", "qty": 1, "url": url, "name": "проверка", "unit": "pcs"}
    done, why, _mark, put = driver.run(
        chain, PHONE, lambda page: cart._put_one(page, chain, item), timeout=150)
    print(f"  нажали: легло={done} положено={put} причина={why!r}")

    print(f"  после нажатия: {driver.run(chain, PHONE, badge, timeout=60)}")

    # Счётчик у части сетей появляется только когда корзина не пуста, и тогда
    # «числа нет» не отличить от «не положилось». Единственный свидетель, который
    # не умеет молчать, — сама страница корзины. Ссылку на неё берём у витрины,
    # а не выдумываем: /basket у METRO уже отвечал «Страница не найдена».
    def cart_page(page) -> str:
        # Ищем ПУТЬ корзины, а не слово «корзина» где попало. Первая версия
        # проверяла текст ссылки — и попалась на плитку товара: у неё внутри
        # написано «В корзину», то есть плитка выглядела ссылкой на корзину и
        # увела замер на чужую карточку.
        href = page.evaluate("""() => {
            for (const a of document.querySelectorAll('a[href]')) {
                let path = '';
                try { path = new URL(a.href, location.href).pathname; } catch (e) { continue; }
                if (/^\\/(cart|basket|korzina)\\/?$/i.test(path)) return a.href;
            }
            return null;
        }""")
        if href:
            page.goto(href, wait_until="domcontentloaded", timeout=45000)
        else:
            # У Магнита корзина не ссылка, а КНОПКА «Перейти в корзину»: ссылки с
            # путём /cart на странице нет вовсе. Нажимаем её так же, как нажал бы
            # человек, — иначе пришлось бы гадать адрес, а гадание уже подводило.
            hit = page.evaluate("""() => {
                for (const e of document.querySelectorAll('a,button,[role=button]')) {
                    const t = ((e.getAttribute('aria-label')||'') + ' ' +
                               (e.innerText||'')).replace(/\\s+/g,' ').trim();
                    // Без \\b: в JS граница слова считается только по латинице,
                    // и после «корзину» её нет — условие не срабатывало никогда.
                    if (/^(перейти в корзину|корзина)/i.test(t)) {
                        e.setAttribute('data-korzina-open', '1');
                        return t;
                    }
                }
                return null;
            }""")
            if not hit:
                return "ни ссылки, ни кнопки корзины витрина не показала"
            node = page.query_selector("[data-korzina-open]")
            node.click(timeout=8000)
        driver._settle(page, 2.5)
        return f"{page.url} -> " + " ".join((page.inner_text("body") or "").split())[:400]

    print(f"  страница корзины: {driver.run(chain, PHONE, cart_page, timeout=120)}")
    driver.close(chain, PHONE)


def buttons(chain: str, url: str) -> None:
    """Где на карточке кнопки «в корзину» и какая из них — кнопка ЭТОГО товара.

    Наряд берёт ПЕРВУЮ подходящую кнопку на странице. На карточке Магнита их
    шесть десятков: одна принадлежит товару, остальные — каруселям «похожие» и
    «с этим покупают» внизу. Карусель живёт своей жизнью, перерисовывается под
    пальцем, и нажатие падает с «карточка перерисовалась» — что и происходило.

    Замер отвечает на один вопрос: годится ли «самая верхняя» как правило выбора.
    """
    print(f"\n=== {chain}: какая кнопка чья ===\n  {url}")
    driver.open_store(chain, PHONE)

    def survey(page) -> dict:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        driver._settle(page, 1.2)
        found = page.evaluate("""() => {
            const want = /^(в корзину|добавить в корзину|купить)$/i;
            const h1 = document.querySelector('h1');
            const title = h1 ? h1.getBoundingClientRect().top + window.scrollY : null;
            const out = [];
            for (const e of document.querySelectorAll('button,[role=button]')) {
                const name = ((e.getAttribute('aria-label')||'') + ' ' +
                              (e.innerText||'')).replace(/\\s+/g,' ').trim();
                if (!want.test(name)) continue;
                const r = e.getBoundingClientRect();
                out.push({имя: name, верх: Math.round(r.top + window.scrollY),
                          видна: r.width > 0 && r.height > 0,
                          в_main: !!e.closest('main'),
                          у_h1: h1 ? !!(h1.closest('section,article,div[class]') || {}).contains?.(e) : null});
            }
            return {заголовок: h1 ? h1.innerText.slice(0, 60) : null, верх_h1: title,
                    высота_страницы: document.body.scrollHeight, кнопки: out.slice(0, 12),
                    всего: out.length};
        }""")
        return found

    for key, value in driver.run(chain, PHONE, survey, timeout=120).items():
        if key == "кнопки":
            for b in value:
                print(f"      {b}")
        else:
            print(f"  {key}: {value}")
    driver.close(chain, PHONE)


def main(argv: list[str]) -> int:
    if argv[:1] == ["buttons"]:
        buttons(argv[1], argv[2])
        return 0
    if argv[:1] == ["counter"]:
        counter(argv[1], argv[2])
        return 0
    chains = argv or list(driver.LOGIN_URL)
    print(f"браузер доступен: {driver.available()}\n")
    for chain in chains:
        if chain not in driver.LOGIN_URL:
            print(f"{chain}: такой сети окно не знает")
            continue
        print(f"=== {chain} ({driver.LOGIN_URL[chain]}) ===")
        for key, value in look_at(chain).items():
            print(f"  {key}: {value}")
        print(f"  кнопка в корзину описана: {signals.ADD_TO_CART.get(chain) or 'по имени'}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
