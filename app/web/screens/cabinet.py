"""Кабинет магазина: его страница, показанная внутри нашего приложения.

ЧТО ЗДЕСЬ ПРОИСХОДИТ. На сервере поднимается настоящий Chromium, открывает сайт
сети, и его снимок приезжает в наш экран. Нажатия и набранный текст едут обратно
в ту же страницу. Для человека это выглядит как окно магазина внутри приложения —
и по сути им и является: страница настоящая, вход настоящий, аккаунт его.

ПОЧЕМУ НЕ IFRAME, РАЗ СТРАНИЦА И ТАК ЧУЖАЯ. Все шесть сетей запрещают показывать
себя в рамке (X-Frame-Options и Content-Security-Policy: frame-ancestors) — рамка
осталась бы пустой. Да и куки в ней были бы куками браузера человека, а нам нужны
куки того браузера, которым потом будет наполняться корзина.

ПОЧЕМУ СНИМКАМИ, А НЕ ПОТОКОМ. Поток кадров требует WebSocket, а приложение стоит
на waitress, который их не умеет. Снимок на каждое нажатие — медленнее, но
достаточно: вход занимает десяток нажатий, а не тысячу. Зато не появляется ни
второго сервера, ни второго протокола.

ГРАНИЦЫ, КОТОРЫЕ ЗДЕСЬ ПРОВЕДЕНЫ.

    ЧЕЛОВЕЧЕСКИЕ ШАГИ — ЧЕЛОВЕКУ. Капчу проходит он, код из СМС вводит он:
    разгадывания проверок и подбора кодов здесь не написано. Встретила проверка —
    она приедет ему снимком, как приехала бы на его собственный экран.

    ЧУЖОЙ АДРЕС НЕ ОТКРЫВАЕТСЯ. Код сети и адрес приходят формой, а форма приходит
    откуда угодно. Открыть по такой просьбе произвольный сайт значило бы сделать
    из приложения пересыльщика, которым ходят куда угодно с нашего адреса.

    ПАРОЛЬ И КОД НИГДЕ НЕ ОСЕДАЮТ. Они едут нажатиями в чужую страницу и в наших
    журналах не появляются: то, что человек набирает, мы не разбираем и не пишем.
    Сохраняется только состояние входа ПОСЛЕ него (app/shopbrowser/store.py).
"""
from __future__ import annotations

import base64
import logging
import os

from flask import g, jsonify, render_template, request

from app import collector, repo, store_accounts
from app.shopbrowser import driver, handoff, signals, store as shopstore
from app.web import auth
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PATH = "/cabinet"

# Сколько ждём поручения браузеру. Открытие сети — самое долгое: поднять Chromium,
# пройти её собственную проверку, дорисовать витрину.
OPEN_TIMEOUT = 90


# Закладка «Передать вход» лежит в docs рядом с приложением. Путь считается от
# ЭТОГО файла, а не от config.ROOT: в тестах корень подменяется на временную
# папку, и закладка «пропала бы» ровно там, где её проверяют (тот же приём — в
# app/web/screens/receipts.py).
BOOKMARKLET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "docs", "hand.min.txt")


def _bookmarklet() -> str:
    """Строка закладки. Пусто — не собрана, и экран скажет это вслух.

    Без неё вся дорога «вход с телефона» остаётся описанием: поле для вставки
    есть, а взять вставляемое человеку негде. Молчаливое отсутствие здесь хуже
    любой ошибки — оно выглядит как работающая возможность.
    """
    try:
        with open(BOOKMARKLET, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        log.warning("закладка «Передать вход» не собрана: %s", BOOKMARKLET)
        return ""


def _chain() -> str:
    """Код сети из запроса. Незнакомый — отказ, а не догадка."""
    code = (request.values.get("store") or "").strip().lower()
    return code if code in driver.LOGIN_URL else ""


def _phone() -> str:
    return getattr(g, "phone", None) or auth.current_phone() or ""


def _as_data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _typed_words(where: dict) -> str:
    """Что сказать человеку про его набор. Молчать здесь нельзя.

    Набор уходит туда, где на чужой странице стоит курсор, и курсор туда ставили
    не мы. Пока экран отвечал одним снимком, набор «в никуда» выглядел в точности
    как удачный: картинка обновилась, строка очистилась, поля пустые. Человек
    набирает код из СМС второй раз, третий — а код живёт минуту.
    """
    if where.get("kind") == "frame":
        # Набрали внутрь чужой рамки — и это ровно то, что нужно проверке с
        # картинкой: поле у неё своё, внутри. А вот если в рамке была галочка
        # «я не робот», буквы ушли в пустоту, и об этом надо сказать сразу же,
        # пока человек не набрал код из СМС по третьему разу. Что именно в
        # рамке, снаружи не видно — рамка с чужого сайта, — поэтому называем
        # оба исхода и не выдаём догадку за знание.
        return ("Набрано в окошко проверки — туда, где стоит курсор. Если в поле на "
                "снимке буквы не появились, нажмите прямо в него и наберите снова: "
                "набранное осталось в строке.")
    if where.get("typed"):
        label = (where.get("label") or "").strip()
        return f"Набрано в поле «{label}»." if label else "Набрано в поле на странице магазина."
    return ("Набирать некуда: на странице не выбрано ни одного поля. Нажмите в нужное "
            "поле прямо на снимке и наберите снова — набранное осталось в строке.")


def _frame(shot: driver.Shot | None, *, note: str, typed: bool) -> dict:
    """Короткий ответ на набор: только новый кадр и что с набранным.

    Признак «partial» говорит экрану не трогать всё остальное — адрес страницы,
    вердикт про вход, кнопку «Запомнить вход». Пришли они здесь пустыми, экран
    стёр бы их на каждую букву: человек набирает код, а строка над окном мигает
    между «вы не вошли» и пустотой.
    """
    body = {"ok": True, "partial": True, "note": note, "typed": typed,
            "note_kind": "" if typed else "bad"}
    if shot is not None:
        body["shot"] = _as_data_url(shot.jpeg)
        body["width"] = shot.width
        body["height"] = shot.height
    return body


def _answer(chain: str, shot: driver.Shot | None, *, note: str = "",
            saved: bool | None = None, typed: bool | None = None) -> dict:
    """Ответ экрану: снимок, что видно на странице и что мы про это думаем.

    Признак входа приезжает вместе со снимком нарочно. Человек смотрит на ту же
    страницу и в спорном случае решает сам — наше «похоже, вы вошли» здесь
    подсказка, а не приговор (см. app/shopbrowser/signals.py).
    """
    seen = {}
    try:
        seen = driver.look(chain, _phone())
    except driver.BrowserUnavailable:
        seen = {}
    says, kind = seen.get("says") or "", seen.get("says_kind") or ""
    if not says:
        # Разбор не состоялся — браузер как раз моргнул. Молчать тут нельзя:
        # пустая строка на месте вердикта читается как «всё в порядке», а мы про
        # вход не знаем ровно ничего. Так и говорим.
        says, kind = signals.verdict_words(None)
    body = {
        "ok": True,
        "note": note,
        "url": seen.get("url") or (shot.url if shot else ""),
        "title": shot.title if shot else "",
        "logged_in": seen.get("logged_in"),
        "guarded": bool(seen.get("guarded")),
        "account": seen.get("account"),
        "points": seen.get("points"),
        # Словами про вход отвечает разбор, а не экран (signals.verdict_words).
        # Написанные в JavaScript, эти же слова нельзя ни проверить без браузера,
        # ни удержать в согласии с признаками входа: поправили бы признак здесь, а
        # фраза в экране осталась бы вчерашней — и человек читал бы «вы не вошли»
        # там, где разбор уже говорит «не разобрал».
        "says": says,
        "says_kind": kind,
        "saved": shopstore.about(chain) is not None if saved is None else saved,
    }
    if typed is not None:
        # Экран очищает свою строку только тогда, когда набранное ДОШЛО. Иначе
        # человек остался бы и без текста, и без результата.
        body["typed"] = typed
        # Отказ по последнему действию красится сам: стоящий вердикт про вход
        # («вы пока не вошли») в эту минуту человеку не нужен, ему нужен шаг.
        body["note_kind"] = "" if typed else "bad"
    if shot is not None:
        body["shot"] = _as_data_url(shot.jpeg)
        body["width"] = shot.width
        body["height"] = shot.height
    return body


def page():
    """Рамка экрана. Саму страницу магазина в неё приносит уже JavaScript.

    Открытие сети НЕ делается при показе страницы нарочно: перезагрузка экрана
    тогда уводила бы человека обратно на главную сети, теряя место, где он был, —
    посреди ввода кода из СМС это особенно обидно.
    """
    chain = _chain()
    # Сети берём из справочника человека, а не из LOGIN_URL: там коды («vkusvill»),
    # и сеть, которой в справочнике не нашлось, попадала на кнопку сырым кодом.
    # Раньше здесь стояло `if code in known or True` — условие, которое всегда
    # истинно, то есть отбора не было вовсе. Справочник сетей заводится при
    # создании рабочего места (app/db.py), так что пустым этот список не бывает.
    known = {s.code: s for s in repo.list_stores()}
    rows = [{"code": code, "name": known[code].name,
             "saved": shopstore.about(code) is not None}
            for code in driver.LOGIN_URL if code in known]

    return render_template(
        "cabinet.html",
        screen=SCREEN_BY_KEY["accounts"],
        chain=chain,
        chain_name=(known[chain].name if chain in known else chain),
        stores=rows,
        width=driver.WIDTH,
        height=driver.HEIGHT,
        have_browser=driver.available(),
        saved=shopstore.about(chain) if chain else None,
        # Шаги входа приходят из разбора чужих страниц, а не из шаблона: они —
        # знание о вёрстке сети и меняются вместе с ней (app/shopbrowser/signals.py).
        entrance=signals.entrance(chain) if chain else None,
        bookmarklet=_bookmarklet(),
    )


def _act():
    """Одно нажатие человека в чужой странице.

    Всё сюда и обратно ходит одним запросом: и действие, и новый снимок. Два
    запроса (сделать, потом забрать картинку) разъезжались бы во времени, и
    человек видел бы страницу ДО своего нажатия — то есть нажимал бы вслепую.
    """
    chain = _chain()
    if not chain:
        return jsonify({"ok": False, "error": "Эта сеть приложению незнакома."}), 400

    phone = _phone()
    body = request.get_json(silent=True) or {}
    do = str(body.get("do") or "shot")

    try:
        if do == "ensure":
            # Сеанс уже открыт — не трогаем: человек мог дойти до ввода кода.
            if driver.session(chain, phone):
                return jsonify(_answer(chain, driver.shot(chain, phone)))
            state = shopstore.load(chain)
            shot = driver.open_store(chain, phone, state=state)
            note = ("Прежний вход подставлен — если сеть его ещё помнит, вы уже внутри."
                    if state else "")
            return jsonify(_answer(chain, shot, note=note))

        if do == "shot":
            return jsonify(_answer(chain, driver.shot(chain, phone)))

        if do == "click":
            x = int(float(body.get("x") or 0))
            y = int(float(body.get("y") or 0))
            return jsonify(_answer(chain, driver.click(chain, phone, x, y)))

        if do == "drag":
            # Протяжка мышью: сети ставят проверки не только с нажатием, но и с
            # ползунком («Разверните картинку горизонтально» у Перекрёстка). Без
            # неё окно приводит человека к задаче, которую он в нём не решит.
            return jsonify(_answer(chain, driver.drag(
                chain, phone,
                int(float(body.get("x1") or 0)), int(float(body.get("y1") or 0)),
                int(float(body.get("x2") or 0)), int(float(body.get("y2") or 0)))))

        if do == "type":
            # Что именно набирает человек, мы не разбираем и не пишем в журнал:
            # это может быть код из СМС или пароль. А вот КУДА оно попало — говорим
            # вслух: молчаливый набор в никуда человек принимает за успех.
            shot, where = driver.type_text(chain, phone, body.get("text") or "")
            # ОТВЕТ НА НАБОР — КОРОТКИЙ. Полный ответ заново разбирает страницу
            # (driver.look читает весь её текст и куки), а это второй поход в
            # браузер на каждую букву. Про вход буква ничего не меняет: вердикт
            # на экране остаётся тот, что приехал с прошлым нажатием.
            return jsonify(_frame(shot, note=_typed_words(where),
                                  typed=bool(where.get("typed"))))

        if do == "key":
            return jsonify(_answer(chain, driver.key(chain, phone, body.get("key") or "")))

        if do == "scroll":
            return jsonify(_answer(chain, driver.scroll(chain, phone, int(body.get("dy") or 400))))

        if do in ("back", "reload"):
            return jsonify(_answer(chain, driver.navigate(chain, phone, do)))

        if do == "save":
            return jsonify(_save(chain, phone))

        if do == "paste":
            # Вход, снятый на телефоне человека, а не добытый нашим сервером.
            # Единственная дорога туда, куда серверу хода нет: 20.09.2026 Магнит
            # встретил его капчей и «Аккаунт заблокирован», а тот же аккаунт с
            # телефона открывается как обычно. Разбор — app/shopbrowser/handoff.
            return jsonify(_paste(chain, body.get("text") or ""))

        if do == "forget":
            shopstore.forget(chain)
            store_accounts.forget(chain)
            return jsonify(_answer(chain, None, note="Сохранённый вход забыт.", saved=False))

        if do == "close":
            driver.close(chain, phone)
            return jsonify({"ok": True, "closed": True,
                            "note": "Окно магазина закрыто, память сервера освобождена."})

        return jsonify({"ok": False, "error": f"Непонятное действие «{do}»."}), 400

    except driver.BrowserUnavailable as err:
        # Человеку — словами. Он единственный, кто видит настоящую страницу, и
        # чужая ошибка про libnss3 ему ничего не объяснит.
        return jsonify({"ok": False, "error": str(err)}), 503
    except Exception as exc:  # noqa: BLE001
        log.exception("кабинет %s: нажатие не выполнилось", chain)
        return jsonify({"ok": False, "error": f"Не вышло: {exc}"}), 500


def _cookie_words(count: int) -> str:
    """«1 кука», «3 куки», «5 кук» — иначе в ответе видно машину, а не язык."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} кук"
    last = count % 10
    if last == 1:
        return f"{count} кука"
    return f"{count} куки" if 2 <= last <= 4 else f"{count} кук"


def _save(chain: str, phone: str) -> dict:
    """Запомнить вход: забрать состояние из браузера и положить в рабочее место.

    Состояние забирает поток запроса, а не поток браузера: у того нет базы
    человека и быть не должно (app/shopbrowser/driver.py, про contextvars).

    ГОСТЯ ЗДЕСЬ РАЗВОРАЧИВАЮТ, И ЭТО РЕШЕНИЕ, А НЕ ОСТОРОЖНОСТЬ. Прежде куки
    сохранялись по одной просьбе человека: он, мол, смотрит на ту же страницу и
    знает лучше. На деле сохранялось вот что: сети выдают куки и гостю (Магнит —
    mg_udi и shopCode), проверка «есть ли хоть одна» их принимала, и подключение
    отмечалось у кабинета, в который никто не входил. Дальше на «Кабинетах»
    вставало ✓, наряд корзины уезжал в сеанс гостя и молча ничего не клал. Отказ
    здесь стоит одного лишнего нажатия, а согласие — целой пустой передачи, о
    которой человек узнаёт, стоя у полки.

    А вот «НЕ РАЗОБРАЛ» — это НЕ отказ. Пять сетей из шести подписи входа нам не
    показывают вовсе (signals.AUTH_COOKIE), и запретить сохранение там значило бы
    запретить его почти везде. Сохраняем — и говорим прямо, что проверить вход не
    смогли.
    """
    seen = driver.look(chain, phone)
    if seen.get("logged_in") is False:
        return {"ok": False,
                "error": "Страница сети показывает, что вы ещё не вошли — сохранять пока "
                         "нечего. Войдите в окне выше (шаги над ним) и нажмите «Запомнить "
                         "вход» ещё раз."}

    state = driver.remember(chain, phone)
    cookies = len((state or {}).get("cookies") or [])
    if not cookies:
        return {"ok": False,
                "error": "Сеть не выдала ни одной куки — сохранять нечего. "
                         "Похоже, вход не завершён."}

    shopstore.save(chain, state, account=seen.get("account"), points=seen.get("points"))
    store_accounts.mark_connected(chain, account=seen.get("account"),
                                  points=seen.get("points"))
    note = (f"Вход сохранён: {_cookie_words(cookies)}. Корзина теперь уедет в этот кабинет "
            "без вашего участия.")
    if seen.get("logged_in") is None:
        note += (" Проверить по странице, что вы внутри, приложение не смогло — если вы "
                 "всё-таки не вошли, передача корзины скажет об этом и ничего не положит.")
    return _answer(chain, driver.shot(chain, phone), note=note, saved=True)


def _paste(chain: str, text: str) -> dict:
    """Принять вход, снятый закладкой на телефоне человека.

    ПОЧЕМУ ЭТО ОТДЕЛЬНОЕ ДЕЙСТВИЕ, А НЕ ВЕТКА В «ЗАПОМНИТЬ ВХОД». У того вход
    БЕРЁТСЯ из нашего браузера, и он же на той же странице проверяет, вошёл ли
    человек. Здесь брать неоткуда: страница входа осталась на телефоне, а к нам
    приехала только банка кук. Значит и проверки другие, и отказы другие, и
    смешивать их в одной ветке — верный способ однажды применить не ту.

    ПРОВЕРИТЬ ВХОД ПО СТРАНИЦЕ МЫ ЗДЕСЬ НЕ МОЖЕМ, И НЕ ДЕЛАЕМ ВИД. Открыть сеть
    нашим браузером, чтобы убедиться, — ровно то, что не получается: по адресу
    сервера сеть либо не пускает, либо встречает проверкой. Поэтому судим по
    тому единственному, что знаем наверняка, — по имени куки входа, а где оно
    нам неизвестно, говорим «не разобрал» вместо выдумки.
    """
    try:
        state = handoff.parse(chain, text)
    except handoff.Rejected as why:
        return {"ok": False, "error": str(why)}

    entered = handoff.looks_logged_in(chain, state)
    if entered is False:
        # Куки есть, а куки ВХОДА среди них нет. У Магнита это твёрдое «не
        # вошли»: mg_at снята живьём 17.09.2026 и известна наверняка. Принять
        # такую банку значило бы отметить подключённым кабинет, в который никто
        # не входил, — та же ошибка, что уже ловилась в «Запомнить вход».
        return {"ok": False,
                "error": "В переданном нет куки входа этой сети — похоже, на телефоне вы "
                         "ещё не вошли. Войдите в магазине на телефоне и повторите."}

    shopstore.save(chain, state)
    store_accounts.mark_connected(chain)
    note = "Вход принят с телефона. " + handoff.summary(state)
    if entered is None:
        note += (" Проверить, что это именно вход, приложение не смогло: имя куки входа у "
                 "этой сети нам достоверно неизвестно. Если вход окажется гостевым, "
                 "передача корзины скажет об этом и ничего не положит.")
    return _answer(chain, None, note=note, saved=True)


def install(flask_app) -> None:
    """Повесить экран и его единственное действие.

    Экран стоит НЕ в карте страниц (app/web/views.SCREENS): в меню ему делать
    нечего — в кабинет заходят с «Кабинетов», по конкретной сети. А действие
    живёт своим адресом, потому что отвечает не страницей, а снимком.
    """
    flask_app.add_url_rule(PATH, endpoint="screen.cabinet",
                           view_func=auth.needs_phone(page))
    flask_app.add_url_rule(f"{PATH}/act", endpoint="screen.cabinet_act",
                           view_func=auth.needs_phone(_act), methods=["POST"])


__all__ = ["page", "install", "PATH"]
