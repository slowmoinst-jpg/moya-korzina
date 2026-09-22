"""Карты и акции: условия, от которых зависит итог расчёта.

Это экран, где опечатка стоит дороже всего. Процент, потолок кэшбэка, минимальный
чек, срок и активация — не строки справочника, а ПАРАМЕТРЫ ФОРМУЛЫ
(app/optimizer/calc.py): по ним расчёт решает, в каком магазине собирать корзину и
стоит ли ради порога переплатить несколько рублей. Ошибка здесь ничего не роняет и
ничем не подсвечивается — она молча меняет ответ, и человек уезжает в другой
магазин, не узнав почему. Отсюда всё устройство этого файла.

ВВЕДЁННОЕ НЕ ПРОПАДАЕТ. Форма с ошибкой не перерисовывается пустой: значения
возвращаются в поля как есть, а рядом стоят претензии словами — что именно не так и
чем это обернётся в расчёте. У кого однажды «съело» десять заполненных полей, тот
второй раз их не заполнит.

ЧИСЛА ВВОДЯТСЯ ТЕКСТОМ, а не полем <input type="number">. Это не небрежность.
Поле типа number отдаёт серверу ПУСТУЮ строку, если в нём стоит «2,5» с запятой, —
а запятая это ровно то, что набирает русская раскладка и цифровая клавиатура
телефона. Вышло бы худшее из возможного: человек видит своё число на экране, сервер
не видит ничего и сохраняет ноль. Поэтому поле текстовое с inputmode="decimal"
(клавиатура на телефоне всё равно цифровая), а запятую, пробелы, знак рубля и
процента разбирает _number ниже.

ТИХИЕ НУЛИ НАЗЫВАЮТСЯ ВСЛУХ. Лимит 0 ₽ и «использовано» больше лимита — это не
описка, а полностью выключенная акция: cap_left уходит в ноль, и calc перестаёт
её начислять. Со стороны это выглядит как «почему-то невыгодно», а не как поломка.
Первое здесь встречает предупреждением, второе — отказом с объяснением.

СВОЙ АДРЕС У ТОГО, ЧТО РЕДАКТИРУЕТСЯ. В Streamlit выбранная карта жила в
session_state, и на «эту акцию» нельзя было дать ссылку. Здесь она в адресе:
/cards?offer=7. Ради этого переезд и затевался.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging

from flask import flash, redirect, render_template, request, url_for

from app import bank_reference, repo
from app.models import Card, Offer
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

# Колонки файла с акциями. Первые пять обязательны, остальные подставляются
# значениями по умолчанию: файл банка редко содержит всё сразу.
CSV_REQUIRED = ("bank", "card", "store_code", "percent", "cap_rub")
CSV_OPTIONAL = ("min_check_rub", "valid_from", "valid_to",
                "requires_activation", "activated", "cap_used")

# Сколько строк с претензиями показать после загрузки файла. Без предела отчёт по
# сбитому файлу занял бы экран целиком, и человек не увидел бы главного — сколько
# всё-таки загрузилось.
COMPLAINTS_SHOWN = 12

DA = {"1", "true", "да", "yes", "y", "on"}
NET = {"0", "false", "нет", "no", "n", ""}


# ---------- как показываем числа ----------
def rub(value) -> str:
    """Деньги с разрядами: «15 000 ₽», а не «15000 ₽».

    Живёт здесь, а не в каждом экране по копии: «О продукте» показывает те же
    пороги, что вводятся тут, и разойтись в написании они не должны — иначе одно и
    то же число на двух экранах читается как два разных.
    """
    try:
        return f"{float(value or 0):,.0f}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return "—"


def num(value) -> str:
    """Число человеческим письмом: 2,5 — а не 2.5 и не 2.500."""
    try:
        return f"{float(value):g}".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def many(count: int, one: str, few: str, rest: str) -> str:
    """«1 карта», «2 карты», «5 карт». Русский счёт, а не «3 карт».

    Мелочь, которую видно сразу: экран, который пишет «3 карт», читается как
    недоделанный, и доверия к числам на нём становится меньше — хотя числа верные.
    """
    tail, tens = count % 10, count % 100
    if tens in range(11, 15) or tail == 0 or tail > 4:
        word = rest
    elif tail == 1:
        word = one
    else:
        word = few
    return f"{count} {word}"


def day(value: str | None) -> str:
    """Дата как её пишут: 30.09.2026. Пусто — значит бессрочно, и так и скажем."""
    parsed = _as_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else "бессрочно"


# ---------- разбор того, что набрал человек ----------
def _as_date(value) -> dt.date | None:
    if not value:
        return None
    text = str(value).strip()
    for shape in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(text[:10], shape).date()
        except ValueError:
            continue
    return None


def _number(raw, field: str, *, minimum: float = 0.0, maximum: float | None = None,
            over: str | None = None) -> tuple[float | None, str | None]:
    """Число из набранного. Возвращает (значение, претензия) — ровно одно из двух.

    Терпимость здесь нарочная: человек пишет «2,5», «1 000», «5 %», «199 ₽» — и всё
    это одно и то же число. Отвергать такое значило бы требовать от него писать как
    удобно машине, а расплачивался бы он потерянной строкой.
    """
    text = (str(raw or "").strip()
            .replace(" ", "").replace(" ", "")
            .replace("₽", "").replace("%", "").replace(",", "."))
    if not text:
        return None, (f"«{field}» не заполнено. Пустое поле сохранилось бы нулём, "
                      "а ноль здесь меняет расчёт молча — поэтому впишите число.")
    try:
        value = float(text)
    except ValueError:
        return None, (f"«{field}»: «{str(raw).strip()}» — это не число. "
                      "Впишите цифры: 5 или 2,5.")
    if value < minimum:
        return None, f"«{field}» не бывает меньше {num(minimum)}."
    if maximum is not None and value > maximum:
        return None, over or f"«{field}» не бывает больше {num(maximum)}."
    return value, None


def _checked(name: str) -> bool:
    """Галочка в форме. Не отмеченная галочка браузером не отправляется вовсе."""
    return request.form.get(name) is not None


def _flag(value, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in DA:
        return True
    if text in NET:
        return False
    return default


# ---------- страница ----------
def page():
    """GET рисует экран, POST правит данные и переводит обратно на /cards.

    Почему POST приходит СЮДА ЖЕ, а не на отдельный адрес: экран один, и человек
    после сохранения должен оказаться там же, где был, — с тем же адресом в строке.
    Flask разрешает методы атрибутом функции (page.methods ниже), поэтому карту
    страниц (app/web/views.py) ради этого править не нужно.
    """
    if request.method == "POST":
        action = request.form.get("action", "")
        handler = {"card_save": _save_card, "card_delete": _delete_card,
                   "offer_save": _save_offer, "offer_delete": _delete_offer,
                   "csv": _load_csv, "bank_add": _add_from_reference}.get(action)
        if handler is None:                       # неизвестное действие — просто назад
            return redirect(url_for("screen.cards"))
        answer = handler()
        if not isinstance(answer, list):          # удалось: перевод на свой же адрес
            return answer
        return _draw(answer, action.split("_")[0])
    return _draw([], "")


page.methods = ("GET", "POST")


def _back():
    return redirect(url_for("screen.cards"))


# ---------- карты ----------
def _chosen_card() -> Card | None:
    """Какая карта редактируется. Берётся из адреса — чтобы на неё была ссылка."""
    raw = request.args.get("card")
    if not raw or not raw.isdigit():
        return None
    return repo.get_card(int(raw))


def _save_card():
    bank = (request.form.get("bank") or "").strip()
    name = (request.form.get("name") or "").strip()
    raw_id = (request.form.get("card_id") or "").strip()

    complaints = []
    if not bank:
        complaints.append("Впишите банк: по нему карта отличается от такой же чужой, "
                          "и с ним же сверяется справочник условий.")
    if not name:
        complaints.append("Впишите название карты — то, как она названа в приложении банка. "
                          "Акции привязываются к карте, и безымянных среди них не разобрать.")
    if complaints:
        return complaints

    card_id = int(raw_id) if raw_id.isdigit() else None
    repo.upsert_card(Card(card_id, bank, name))
    flash(("Карту сохранили. Условия — проценты, потолок и сроки — вписываются "
           "ниже, в «Акциях»: без них карта в расчёт не входит."), "ok")
    return _back()


def _delete_card():
    raw_id = (request.form.get("card_id") or "").strip()
    if not raw_id.isdigit():
        return _back()
    card = repo.get_card(int(raw_id))
    if card is None:
        return _back()
    gone = len([o for o in repo.list_offers() if o.card_id == card.id])
    repo.delete_card(card.id)
    flash(f"Карту «{card.bank} · {card.name}» удалили"
          + (f" вместе с её акциями ({gone})." if gone else "."), "ok")
    return _back()


# ---------- акции ----------
def _chosen_offer(offers) -> Offer | None:
    raw = request.args.get("offer")
    if not raw or not raw.isdigit():
        return None
    return next((o for o in offers if o.id == int(raw)), None)


def _save_offer():
    """Проверить условия акции и сохранить. Претензии — списком, ввод не теряется."""
    complaints: list[str] = []
    cards = {c.id: c for c in repo.list_cards()}
    stores = {s.id: s for s in repo.list_stores()}

    raw_card = (request.form.get("card_id") or "").strip()
    raw_store = (request.form.get("store_id") or "").strip()
    card = cards.get(int(raw_card)) if raw_card.isdigit() else None
    store = stores.get(int(raw_store)) if raw_store.isdigit() else None
    if card is None:
        complaints.append("Выберите карту: акция — это условие конкретной карты, "
                          "и без неё расчёту нечего применять.")
    if store is None:
        complaints.append("Выберите магазин: процент действует в своей сети, "
                          "а не во всех сразу.")

    percent, note = _number(
        request.form.get("percent"), "Процент кэшбэка", maximum=100.0,
        over="«Процент кэшбэка» больше ста: кэшбэк не бывает больше самого чека. "
             "Если банк обещает 30 % — впишите 30, а не 130 и не 0,3.")
    complaints += [note] if note else []

    cap_rub, note = _number(request.form.get("cap_rub"), "Лимит кэшбэка, ₽")
    complaints += [note] if note else []

    cap_used, note = _number(request.form.get("cap_used"), "Использовано, ₽")
    complaints += [note] if note else []

    min_check, note = _number(request.form.get("min_check_rub"), "Минимальный чек, ₽")
    complaints += [note] if note else []

    # Использовано больше лимита — не описка, а выключенная акция: cap_left станет
    # нулём, и расчёт перестанет её начислять, ничего об этом не сказав.
    if cap_rub is not None and cap_used is not None and cap_used > cap_rub:
        complaints.append(
            f"«Использовано» ({rub(cap_used)}) больше лимита ({rub(cap_rub)}). "
            "При таком вводе расчёт считает, что кэшбэк по акции исчерпан, и не "
            "начислит по ней ничего. Проверьте, какое из двух чисел вы имели в виду.")

    valid_from, valid_to = _as_date(request.form.get("valid_from")), None
    if (request.form.get("valid_from") or "").strip() and valid_from is None:
        complaints.append("«Действует с»: дату не разобрать. Пишите 30.09.2026 "
                          "или выберите её в календаре.")
    if (request.form.get("valid_to") or "").strip():
        valid_to = _as_date(request.form.get("valid_to"))
        if valid_to is None:
            complaints.append("«Действует по»: дату не разобрать. Пишите 30.09.2026 "
                              "или выберите её в календаре.")
    if valid_from and valid_to and valid_from > valid_to:
        complaints.append(
            f"Срок задан наоборот: «с» {valid_from.strftime('%d.%m.%Y')} позже, "
            f"чем «по» {valid_to.strftime('%d.%m.%Y')}. Такая акция не сработает "
            "ни в один день — расчёт просто будет её пропускать.")

    if complaints:
        return complaints

    raw_id = (request.form.get("offer_id") or "").strip()
    requires_activation, activated = _checked("requires_activation"), _checked("activated")
    repo.upsert_offer(Offer(
        id=int(raw_id) if raw_id.isdigit() else None,
        card_id=card.id, store_id=store.id,
        percent=percent, cap_rub=cap_rub, min_check_rub=min_check,
        valid_from=valid_from.isoformat() if valid_from else None,
        valid_to=valid_to.isoformat() if valid_to else None,
        requires_activation=requires_activation, activated=activated,
        cap_used=cap_used,
    ))
    flash("Акцию сохранили.", "ok")
    for warning in _quiet_zeros(percent, cap_rub, cap_used, valid_to,
                               requires_activation, activated):
        flash(warning, "warn")
    return _back()


def _quiet_zeros(percent, cap_rub, cap_used, valid_to, requires_activation, activated):
    """Что сохранено верно, но в расчёт сегодня не войдёт. Сказать надо всё равно.

    Это не ошибки ввода: человек вправе завести акцию впрок или обнулить потолок.
    Но выглядят они одинаково — «почему-то не начисляется», — и без этих строк
    разница между «не сработало» и «выключено» видна только в коде.
    """
    out = []
    if not percent:
        out.append("Процент — ноль: по этой акции расчёт не начислит ничего.")
    if not cap_rub:
        out.append("Лимит кэшбэка — 0 ₽. Это потолок за период, а не «без ограничения»: "
                   "при нуле акция не начисляет. Если потолка нет, впишите заведомо "
                   "большое число, например 100 000.")
    elif cap_used is not None and cap_used >= cap_rub:
        out.append("Лимит выбран полностью — до конца периода акция ничего не добавит.")
    if requires_activation and not activated:
        out.append("Акция помечена «требует активации» и не активирована: сегодня "
                   "расчёт её пропустит. Активируйте её в приложении банка и "
                   "поставьте здесь вторую галочку.")
    if valid_to and valid_to < dt.date.today():
        out.append(f"Срок акции истёк {valid_to.strftime('%d.%m.%Y')} — сегодня она "
                   "в расчёт не войдёт.")
    return out


def _delete_offer():
    raw_id = (request.form.get("offer_id") or "").strip()
    if raw_id.isdigit():
        repo.delete_offer(int(raw_id))
        flash("Акцию удалили. Карта осталась.", "ok")
    return _back()


# ---------- загрузка файла с акциями ----------
def _load_csv():
    """Разобрать файл с акциями и залить его. Каждая пропущенная строка названа.

    Молчаливый пропуск строки здесь — та же незаметная ошибка, что и опечатка в
    поле: итог посчитается, просто по неполным условиям. Поэтому загрузка всегда
    отчитывается двумя числами и списком того, что не взяла.
    """
    sent = request.files.get("offers_csv")
    if sent is None or not (sent.filename or "").strip():
        return ["Файл не выбран. Нажмите «Выберите файл» и укажите выгрузку с акциями."]

    raw = sent.read()
    if not raw:
        return [f"Файл «{sent.filename}» пустой."]

    columns, rows, complaint = _read_table(raw)
    if complaint:
        return [complaint]

    missing = [c for c in CSV_REQUIRED if c not in columns]
    if missing:
        return [f"В файле не хватает колонок: {', '.join(missing)}. "
                f"Обязательные: {', '.join(CSV_REQUIRED)}. "
                f"Необязательные: {', '.join(CSV_OPTIONAL)}.",
                f"Заголовок файла прочитался так: {', '.join(columns) or 'пусто'}."]

    created, updated, refused = _import_offers(rows)
    if created or updated:
        flash(f"Из файла «{sent.filename}»: добавлено {created}, обновлено {updated}.", "ok")
    if refused:
        head = "; ".join(refused[:COMPLAINTS_SHOWN])
        tail = f" …и ещё {len(refused) - COMPLAINTS_SHOWN}" if len(refused) > COMPLAINTS_SHOWN else ""
        flash(f"Не взято строк: {len(refused)}. {head}{tail}", "warn")
    if not created and not updated and not refused:
        flash(f"В файле «{sent.filename}» не нашлось ни одной строки с данными.", "warn")
    return _back()


def _read_table(raw: bytes) -> tuple[list[str], list[dict], str | None]:
    """Прочитать таблицу так, как её сохранил человек, а не как удобно нам.

    Две ловушки, из-за которых честный файл выглядел бы сломанным. Русский Excel
    сохраняет CSV в cp1251 — на utf-8 такой файл рассыпается на первой же букве «я».
    Он же ставит разделителем точку с запятой, и файл с одной колонкой отвечал бы
    «не хватает колонок: bank, card, …», хотя в нём есть всё.
    """
    text = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return [], [], ("Файл не читается ни как utf-8, ни как windows-1251. "
                        "Сохраните его из таблицы ещё раз в формате CSV.")

    lines = text.splitlines()
    head = lines[0] if lines else ""
    delimiter = max((";", ",", "\t"), key=head.count) if head else ","
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        # Пробел в заголовке («bank; card») — обычное дело у выгрузок из таблиц, и
        # без обрезки колонка «card» не нашлась бы при том, что она в файле есть.
        rows = [{(k or "").strip(): v for k, v in r.items()} for r in reader]
        columns = [(c or "").strip() for c in (reader.fieldnames or [])]
    except csv.Error as exc:
        return [], [], f"Файл не разбирается как таблица: {exc}"
    return columns, rows, None


def _import_offers(rows: list[dict]) -> tuple[int, int, list[str]]:
    """Строки файла в карты и акции. Одна карта+магазин — одна акция, как и было.

    Правило «одна акция на пару карта-магазин» досталось от прежнего экрана и
    сохранено нарочно: повторная загрузка того же файла должна обновлять условия,
    а не плодить их копии, иначе расчёт выберет из дублей произвольную.
    """
    created = updated = 0
    refused: list[str] = []

    for i, row in enumerate(rows):
        line = i + 2                              # +1 на заголовок, +1 на счёт с единицы
        code = str(row.get("store_code") or "").strip()
        store = repo.get_store(code) if code else None
        if store is None:
            refused.append(f"строка {line}: магазина «{code}» нет")
            continue

        bank = str(row.get("bank") or "").strip()
        card_name = str(row.get("card") or "").strip()
        if not bank or not card_name:
            refused.append(f"строка {line}: пустой банк или карта")
            continue

        percent, note = _number(row.get("percent"), "percent", maximum=100.0,
                                over="процент больше ста")
        if note:
            refused.append(f"строка {line}: {note}")
            continue
        cap_rub, note = _number(row.get("cap_rub"), "cap_rub")
        if note:
            refused.append(f"строка {line}: {note}")
            continue

        card = next((c for c in repo.list_cards()
                     if c.bank.lower() == bank.lower() and c.name.lower() == card_name.lower()), None)
        card_id = card.id if card else repo.upsert_card(Card(None, bank, card_name))

        existing = next((o for o in repo.list_offers(store.id) if o.card_id == card_id), None)
        repo.upsert_offer(Offer(
            id=existing.id if existing else None,
            card_id=card_id, store_id=store.id,
            percent=percent, cap_rub=cap_rub,
            min_check_rub=_number(row.get("min_check_rub"), "min_check_rub")[0] or 0.0,
            valid_from=_iso(row.get("valid_from")),
            valid_to=_iso(row.get("valid_to")),
            requires_activation=_flag(row.get("requires_activation")),
            activated=_flag(row.get("activated"), default=True),
            cap_used=_number(row.get("cap_used"), "cap_used")[0] or 0.0,
        ))
        if existing:
            updated += 1
        else:
            created += 1
    return created, updated, refused


def _iso(value) -> str | None:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else None


# ---------- справочник банков ----------
def _add_from_reference():
    """Завести карту по строке справочника. Условия человек вписывает сам.

    Справочник знает устройство программ, но не проценты: они меняются каждый месяц
    и у каждого свои (app/bank_reference.py). Поэтому отсюда заводится только карта,
    а не готовая акция — подставленный «средний процент» был бы выдумкой, которую
    расчёт принял бы за факт.
    """
    bank = (request.form.get("bank") or "").strip()
    title = (request.form.get("title") or "").strip()
    if not bank or not title:
        return _back()
    if any(c.bank == bank and c.name == title for c in repo.list_cards()):
        flash("Такая карта уже заведена.", "info")
        return _back()
    repo.upsert_card(Card(None, bank, title))
    flash(f"Карту «{bank} · {title}» завели. Проценты, потолок и срок — ваши: "
          "впишите их ниже, в «Акциях».", "ok")
    return _back()


# ---------- сборка страницы ----------
def _card_label(card) -> str:
    return "—" if card is None else f"{card.bank} · {card.name}"


def _state(offer: Offer, today: str) -> tuple[str, str]:
    """Почему акция сегодня работает или не работает. Причина, а не просто «нет».

    Три разных беды — не активирована, ещё не началась, срок истёк — выглядят в
    расчёте одинаково: акции просто нет в ответе. Назвать причину здесь дешевле,
    чем искать её потом в условиях.
    """
    if offer.requires_activation and not offer.activated:
        return "Не активирована", "var(--amber)"
    if offer.valid_from and today < offer.valid_from:
        return "Ещё не началась", "var(--ink3)"
    if offer.valid_to and today > offer.valid_to:
        return "Срок истёк", "var(--ink3)"
    # Нулевой потолок и выбранный потолок — разные беды с одинаковым следствием:
    # в первом случае условие просто не дозаполнено, во втором период уже отработан.
    if not offer.cap_rub:
        return "Потолок не задан", "var(--amber)"
    if offer.cap_left <= 0:
        return "Лимит выбран", "var(--amber)"
    return "Действует", "var(--green)"


def _offer_rows(offers, cards, stores, today):
    card_by_id = {c.id: c for c in cards}
    store_by_id = {s.id: s for s in stores}
    rows = []
    for o in offers:
        store = store_by_id.get(o.store_id)
        label, colour = _state(o, today)
        rows.append({
            "id": o.id,
            "card": _card_label(card_by_id.get(o.card_id)),
            "code": store.code if store else "",
            "store": store.name if store else f"магазин {o.store_id}",
            "percent": num(o.percent),
            "cap": rub(o.cap_rub),
            "used": rub(o.cap_used),
            "left": rub(o.cap_left),
            "share": min(100, round(o.cap_used / o.cap_rub * 100)) if o.cap_rub else 0,
            "min_check": rub(o.min_check_rub) if o.min_check_rub else None,
            "since": day(o.valid_from) if o.valid_from else None,
            "until": day(o.valid_to),
            "needs_activation": bool(o.requires_activation),
            "label": label,
            "colour": colour,
        })
    return rows


def _card_draft(scope: str, chosen: Card | None) -> dict:
    """Чем заполнить форму карты: набранным только что или тем, что в базе."""
    if scope == "card":
        return {"id": request.form.get("card_id", ""),
                "bank": request.form.get("bank", ""),
                "name": request.form.get("name", "")}
    if chosen:
        return {"id": str(chosen.id), "bank": chosen.bank, "name": chosen.name}
    return {"id": "", "bank": "", "name": ""}


def _offer_draft(scope: str, chosen: Offer | None) -> dict:
    if scope == "offer":
        return {"id": request.form.get("offer_id", ""),
                "card_id": request.form.get("card_id", ""),
                "store_id": request.form.get("store_id", ""),
                "percent": request.form.get("percent", ""),
                "cap_rub": request.form.get("cap_rub", ""),
                "cap_used": request.form.get("cap_used", ""),
                "min_check_rub": request.form.get("min_check_rub", ""),
                "valid_from": request.form.get("valid_from", ""),
                "valid_to": request.form.get("valid_to", ""),
                "requires_activation": _checked("requires_activation"),
                "activated": _checked("activated")}
    if chosen:
        return {"id": str(chosen.id), "card_id": str(chosen.card_id),
                "store_id": str(chosen.store_id),
                "percent": num(chosen.percent), "cap_rub": num(chosen.cap_rub),
                "cap_used": num(chosen.cap_used), "min_check_rub": num(chosen.min_check_rub),
                "valid_from": chosen.valid_from or "", "valid_to": chosen.valid_to or "",
                "requires_activation": bool(chosen.requires_activation),
                "activated": bool(chosen.activated)}
    # Новая акция активна по умолчанию: галочка «требует активации» не стоит, и
    # снятая «активирована» при ней означала бы выключённую акцию на ровном месте.
    # ?for=<карта> приходит с кнопки «Добавить акцию» на плитке карты: человек уже
    # сказал, чьё это условие, и переспрашивать в списке из десятка карт незачем.
    preset = request.args.get("for", "")
    return {"id": "", "card_id": preset if preset.isdigit() else "", "store_id": "",
            "percent": "", "cap_rub": "", "cap_used": "0", "min_check_rub": "0",
            "valid_from": "", "valid_to": "",
            "requires_activation": False, "activated": True}


def _how_many_offers(offers, card) -> str:
    """Сколько условий у карты. «Без акций» вместо «0 акций»: карта без условий в
    расчёт не входит вовсе, и сказать это словом честнее, чем нулём."""
    count = sum(1 for o in offers if o.card_id == card.id)
    return many(count, "акция", "акции", "акций") if count else "без акций"


def _banks(cards) -> list[dict]:
    mine = {(c.bank, c.name) for c in cards}
    out = []
    for row in bank_reference.all_banks():
        title = bank_reference.card_title(row)
        out.append({
            "bank": row.get("bank", ""),
            "title": title,
            "how": row.get("how_it_works") or "—",
            "categories": row.get("categories") or "—",
            "currency": row.get("currency") or "—",
            "money": bank_reference.is_money(row),
            "warnings": bank_reference.warnings(row),
            "note": row.get("note") or "",
            "already": (row.get("bank", ""), title) in mine,
        })
    return out


def _draw(complaints: list[str], scope: str):
    cards = repo.list_cards()
    stores = repo.list_stores()
    offers = repo.list_offers()
    today = dt.date.today().isoformat()

    return render_template(
        "cards.html",
        screen=SCREEN_BY_KEY["cards"],
        complaints=complaints,
        scope=scope,
        cards=cards,
        stores=stores,
        offers=_offer_rows(offers, cards, stores, today),
        counts={card.id: _how_many_offers(offers, card) for card in cards},
        how_many_cards=many(len(cards), "карта", "карты", "карт"),
        how_many_offers=many(len(offers), "акция", "акции", "акций"),
        card_draft=_card_draft(scope, _chosen_card()),
        offer_draft=_offer_draft(scope, _chosen_offer(offers)),
        total_cap=rub(sum(float(o.cap_rub or 0) for o in offers)),
        total_used=rub(sum(float(o.cap_used or 0) for o in offers)),
        banks=_banks(cards),
        csv_required=", ".join(CSV_REQUIRED),
        csv_optional=", ".join(CSV_OPTIONAL),
    )


__all__ = ["page", "rub", "num", "day", "many"]
