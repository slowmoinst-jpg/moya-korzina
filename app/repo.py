"""Доступ к данным. Единственный слой, через который UI и модули ходят в SQLite."""
from __future__ import annotations

import math
from datetime import datetime

from app.db import get_conn, init_db  # noqa: F401  (init_db реэкспортируется для UI)
from app.models import Card, Offer, Product, Store


def NOW() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- products ----------
def _product(r) -> Product:
    # по именам, а не по порядку: порядок колонок в таблице и полей в датаклассе
    # разошёлся, когда добавился штрихкод, и позиционная сборка молча всё перепутала
    keys = r.keys()
    return Product(
        id=r["id"],
        name=r["name"],
        brand=r["brand"],
        barcode=r["barcode"] if "barcode" in keys else None,
        weight_g=r["weight_g"],
        unit=r["unit"],
        category=r["category"],
        active=bool(r["active"]),
    )


def list_products(active_only: bool = True) -> list[Product]:
    sql = "SELECT * FROM products" + (" WHERE active = 1" if active_only else "") + " ORDER BY name"
    with get_conn() as c:
        return [_product(r) for r in c.execute(sql)]


def get_product(product_id: int) -> Product | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return _product(r) if r else None


def upsert_product(p: Product) -> int:
    with get_conn() as c:
        if p.id:
            c.execute(
                "UPDATE products SET name=?, barcode=?, brand=?, weight_g=?, unit=?, category=?, active=?"
                " WHERE id=?",
                (p.name, p.barcode, p.brand, p.weight_g, p.unit, p.category, int(p.active), p.id),
            )
            c.commit()
            return p.id
        cur = c.execute(
            "INSERT INTO products (name, barcode, brand, weight_g, unit, category, active)"
            " VALUES (?,?,?,?,?,?,?)",
            (p.name, p.barcode, p.brand, p.weight_g, p.unit, p.category, int(p.active)),
        )
        c.commit()
        return cur.lastrowid


def find_product_by_name(name: str) -> Product | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM products WHERE lower(name) = lower(?)", (name,)).fetchone()
    return _product(r) if r else None


# ---------- stores ----------
def _store(r) -> Store:
    return Store(r["id"], r["code"], r["name"], r["delivery_fee"], r["free_delivery_from"],
                 r["min_order"], r["connector_type"])


def list_stores() -> list[Store]:
    with get_conn() as c:
        return [_store(r) for r in c.execute("SELECT * FROM stores ORDER BY id")]


def get_store(code_or_id: str | int) -> Store | None:
    field = "code" if isinstance(code_or_id, str) else "id"
    with get_conn() as c:
        r = c.execute("SELECT * FROM stores WHERE " + field + " = ?", (code_or_id,)).fetchone()
    return _store(r) if r else None


# ---------- cards & offers ----------
def list_cards() -> list[Card]:
    with get_conn() as c:
        return [Card(r["id"], r["bank"], r["name"]) for r in c.execute("SELECT * FROM cards ORDER BY bank, name")]


def get_card(card_id: int) -> Card | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    return Card(r["id"], r["bank"], r["name"]) if r else None


def upsert_card(card: Card) -> int:
    with get_conn() as c:
        if card.id:
            c.execute("UPDATE cards SET bank=?, name=? WHERE id=?", (card.bank, card.name, card.id))
            c.commit()
            return card.id
        cur = c.execute("INSERT INTO cards (bank, name) VALUES (?,?)", (card.bank, card.name))
        c.commit()
        return cur.lastrowid


def delete_card(card_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM cards WHERE id=?", (card_id,))
        c.commit()


def _offer(r) -> Offer:
    return Offer(r["id"], r["card_id"], r["store_id"], r["percent"], r["cap_rub"], r["min_check_rub"],
                 r["valid_from"], r["valid_to"], bool(r["requires_activation"]), bool(r["activated"]), r["cap_used"])


def list_offers(store_id: int | None = None) -> list[Offer]:
    sql = "SELECT * FROM offers" + (" WHERE store_id = ?" if store_id else "") + " ORDER BY percent DESC"
    with get_conn() as c:
        return [_offer(r) for r in c.execute(sql, (store_id,) if store_id else ())]


def offers_for_store(store_id: int, day: str | None = None) -> list[Offer]:
    """Действующие акции магазина: учтены срок, активация и остаток лимита."""
    return [o for o in list_offers(store_id) if o.is_valid_on(day) and o.cap_left > 0]


def upsert_offer(o: Offer) -> int:
    vals = (o.card_id, o.store_id, o.percent, o.cap_rub, o.min_check_rub, o.valid_from, o.valid_to,
            int(o.requires_activation), int(o.activated), o.cap_used)
    with get_conn() as c:
        if o.id:
            c.execute("UPDATE offers SET card_id=?, store_id=?, percent=?, cap_rub=?, min_check_rub=?,"
                      " valid_from=?, valid_to=?, requires_activation=?, activated=?, cap_used=? WHERE id=?",
                      vals + (o.id,))
            c.commit()
            return o.id
        cur = c.execute("INSERT INTO offers (card_id, store_id, percent, cap_rub, min_check_rub, valid_from,"
                        " valid_to, requires_activation, activated, cap_used) VALUES (?,?,?,?,?,?,?,?,?,?)", vals)
        c.commit()
        return cur.lastrowid


def delete_offer(offer_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM offers WHERE id=?", (offer_id,))
        c.commit()


# ---------- store_products / mapping / prices ----------
def upsert_store_product(store_id: int, sku: str, raw_name: str, weight_g: float | None = None,
                         unit: str | None = None, url: str | None = None,
                         ean: str | None = None, keep_known: bool = False) -> int:
    """Товар сети в базу человека. keep_known — не затирать известные фасовку,
    единицу и адрес пустыми: так пишет единая база, у части сетей этих полей нет
    вовсе (у Дикси пуст unit, METRO его не задаёт), а коннектор их уже знал."""
    keep = (" weight_g=COALESCE(excluded.weight_g, store_products.weight_g),"
            " unit=COALESCE(excluded.unit, store_products.unit),"
            " url=COALESCE(excluded.url, store_products.url),") if keep_known else (
            " weight_g=excluded.weight_g, unit=excluded.unit, url=excluded.url,")
    with get_conn() as c:
        c.execute("INSERT INTO store_products (store_id, sku, raw_name, weight_g, unit, url, ean)"
                  " VALUES (?,?,?,?,?,?,?)"
                  " ON CONFLICT(store_id, sku) DO UPDATE SET raw_name=excluded.raw_name,"
                  + keep +
                  " ean=COALESCE(excluded.ean, store_products.ean)",
                  (store_id, sku, raw_name, weight_g, unit, url, ean))
        c.commit()
        return c.execute("SELECT id FROM store_products WHERE store_id=? AND sku=?", (store_id, sku)).fetchone()["id"]


def confirm_mapping(product_id: int, store_product_id: int, confirmed: bool = True,
                    method: str | None = None) -> None:
    """Записать сопоставление. method — откуда оно: None — человек или живой поиск,
    «master» — из единой базы (app/catalog/link.py).

    Подтверждение не базой (method=None) кандидата, которого записала база, делает
    связь выбором человека: метка «master» снимается. Иначе следующее сопоставление
    сочло бы связь своей и, если сама база в ней не уверена, сняло бы подтверждение
    (ревью 18.10.2026). Уже подтверждённую базой связь повторное подтверждение (живой
    поиск «Узнать цены по магазинам» нашёл тот же артикул) не трогает.
    """
    with get_conn() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(product_mapping)")}
        if method is None:
            fresh = "excluded.confirmed=1 AND product_mapping.confirmed=0"
            mark = (f", method=CASE WHEN {fresh} THEN NULL ELSE product_mapping.method END"
                    if "method" in have else "")
            # Снимок названия — при подтверждении, по нынешнему названию товара сети.
            seen = (", seen_name=CASE WHEN excluded.confirmed=1 AND (product_mapping.confirmed=0"
                    " OR product_mapping.seen_name IS NULL)"
                    " THEN (SELECT raw_name FROM store_products WHERE id=excluded.store_product_id)"
                    " ELSE product_mapping.seen_name END" if "seen_name" in have else "")
            first = ("INSERT INTO product_mapping (product_id, store_product_id, confirmed,"
                     " confirmed_at, seen_name) VALUES (?,?,?,?,"
                     " CASE WHEN ?=1 THEN (SELECT raw_name FROM store_products WHERE id=?) END)"
                     if "seen_name" in have else
                     "INSERT INTO product_mapping (product_id, store_product_id, confirmed,"
                     " confirmed_at) VALUES (?,?,?,?)")
            args = ((product_id, store_product_id, int(confirmed), NOW() if confirmed else None,
                     int(confirmed), store_product_id) if "seen_name" in have else
                    (product_id, store_product_id, int(confirmed), NOW() if confirmed else None))
            c.execute(first + " ON CONFLICT(product_id, store_product_id)"
                      " DO UPDATE SET confirmed=excluded.confirmed, confirmed_at=excluded.confirmed_at"
                      + mark + seen, args)
        else:
            # Запись единой базы — одним оператором, чтобы SQLite сам проверил, что за
            # время прохода человек ничего не решил: не перебивать его подтверждение и
            # не писать в сеть, от которой он отказался (гонка с фоновым проходом,
            # ревью 18.10.2026).
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                             " AND name='link_rejects'").fetchone():
                c.execute("CREATE TABLE IF NOT EXISTS link_rejects (product_id INTEGER NOT NULL,"
                          " store_id INTEGER NOT NULL, sku TEXT NOT NULL, at TEXT NOT NULL,"
                          " PRIMARY KEY (product_id, store_id, sku))")
            c.execute("INSERT INTO product_mapping (product_id, store_product_id, confirmed,"
                      " confirmed_at, method) SELECT ?,?,?,?,? WHERE NOT EXISTS ("
                      "SELECT 1 FROM link_rejects lr JOIN store_products sp ON sp.store_id = lr.store_id"
                      " WHERE lr.product_id=? AND sp.id=?)"
                      " ON CONFLICT(product_id, store_product_id)"
                      " DO UPDATE SET confirmed=excluded.confirmed, confirmed_at=excluded.confirmed_at,"
                      " method=excluded.method"
                      " WHERE NOT (product_mapping.method IS NULL AND product_mapping.confirmed=1)",
                      (product_id, store_product_id, int(confirmed), NOW() if confirmed else None,
                       method, product_id, store_product_id))
        c.commit()


CHAIN_REJECT = "*"      # отметка отказа от сети целиком в link_rejects
STUB_MARK = "stub:"     # отметка убранной заглушки: отказ от цены, не от сети


def drop_mapping(product_id: int, store_id: int) -> None:
    """Убрать связь товара с сетью и ЗАПОМНИТЬ отказ: единая база её не вернёт.

    Отказ — от СЕТИ, а не от одного артикула: экран обещает «до нового
    подтверждения этот товар в расчёт по этой сети не попадёт», а у единого товара
    в сети бывает несколько карточек, и сеть перевыкладывает товар под новым
    артикулом (ревью 18.10.2026). Убранные артикулы пишутся рядом — для истории.
    """
    from app.catalog.model import placeholder_sku

    with get_conn() as c:
        code = (c.execute("SELECT code FROM stores WHERE id=?", (store_id,)).fetchone() or {"code": ""})["code"]
        held = c.execute(
            "SELECT sp.id, sp.sku, m.confirmed FROM product_mapping m"
            " JOIN store_products sp ON sp.id = m.store_product_id"
            " WHERE m.product_id=? AND sp.store_id=?", (product_id, store_id)).fetchall()
        real = [r for r in held if r["confirmed"] and not placeholder_sku(r["sku"], code)]
        stubs = [r["id"] for r in held if r["confirmed"] and placeholder_sku(r["sku"], code)]
        if stubs and not real:
            # Убирают заглушку (цену из чека или прайса), а не связь с сетью: отказа от
            # сети нет, кандидаты единой базы остаются на «Связях» (ревью 18.10.2026).
            c.executemany("DELETE FROM product_mapping WHERE product_id=? AND store_product_id=?",
                          [(product_id, sp_id) for sp_id in stubs])
            # Убранную заглушку помним отдельной отметкой «stub:<артикул>»: автоподбор не
            # вернёт её, но сеть целиком это не закрывает (chain_rejected её не видит).
            try:
                c.executemany("INSERT OR REPLACE INTO link_rejects (product_id, store_id, sku, at)"
                              " VALUES (?,?,?,?)",
                              [(product_id, store_id, STUB_MARK + r["sku"], NOW())
                               for r in held if r["id"] in stubs])
            except Exception:  # noqa: BLE001 — база прежней версии: таблицы ещё нет
                pass
            c.commit()
            return
        skus = [r["sku"] for r in held]
        c.execute("DELETE FROM product_mapping WHERE product_id=? AND store_product_id IN"
                  " (SELECT id FROM store_products WHERE store_id=?)", (product_id, store_id))
        try:
            c.executemany("INSERT OR REPLACE INTO link_rejects (product_id, store_id, sku, at)"
                          " VALUES (?,?,?,?)",
                          [(product_id, store_id, s, NOW()) for s in [*skus, CHAIN_REJECT]])
        except Exception:  # noqa: BLE001 — база прежней версии: таблицы ещё нет
            pass
        c.commit()


def rejected_skus(product_id: int, store_id: int) -> set[str]:
    """Артикулы этой сети, которые человек у товара убрал (и отметка «*» — вся сеть).

    Убранные заглушки (отметка «stub:…») сюда не входят: это отказ от цены, не от сети.
    """
    return {s for s in _reject_marks(product_id, store_id) if not s.startswith(STUB_MARK)}


def dropped_stubs(product_id: int, store_id: int) -> set[str]:
    """Артикулы-заглушки (hist-, list-, …), которые человек убрал у товара в этой сети."""
    return {s[len(STUB_MARK):] for s in _reject_marks(product_id, store_id)
            if s.startswith(STUB_MARK)}


def _reject_marks(product_id: int, store_id: int) -> set[str]:
    with get_conn() as c:
        try:
            return {r["sku"] for r in c.execute(
                "SELECT sku FROM link_rejects WHERE product_id=? AND store_id=?",
                (product_id, store_id))}
        except Exception:  # noqa: BLE001 — таблицы ещё нет: отказов не было
            return set()


def chain_rejected(product_id: int, store_id: int) -> bool:
    """Человек убирал связь товара с этой сетью: автоматике сюда хода нет."""
    return bool(rejected_skus(product_id, store_id))


def clear_rejects(product_id: int, store_id: int) -> None:
    """Человек сам подтвердил связь с сетью — отказ от неё снят («до нового подтверждения»)."""
    with get_conn() as c:
        try:
            c.execute("DELETE FROM link_rejects WHERE product_id=? AND store_id=?",
                      (product_id, store_id))
            c.commit()
        except Exception:  # noqa: BLE001 — таблицы ещё нет: и снимать нечего
            pass


def real_sku_sql(sp: str = "sp", s: str = "s") -> str:
    """Условие SQL «артикул настоящий»: не заглушка — ни чека, ни прайса, ни стенда.

    Правило одно с app/catalog/model.placeholder_sku и живёт рядом с ним же
    (model.placeholder_sql): у правила два выражения, Python и SQL, и они уже
    расходились — SQL не знал формы «<сеть>-preview-N» в чужой сети и различал регистр
    иначе, чем Python (adversarial-проверка 18.10.2026, T11). Алиасы sp и s — те, что в
    запросе: store_products и stores.
    """
    from app.catalog.model import placeholder_sql

    return f"NOT {placeholder_sql(f'{sp}.sku', f'{s}.code')}"


def confirmed_mapping(product_id: int, store_id: int, real_only: bool = False) -> dict | None:
    """Подтверждённое сопоставление эталона в магазине или None.

    real_only — только настоящий артикул сети, без заглушек строки чека (hist-),
    прайса (list-) и справочника («<сеть>-…»): это источники цены, а в корзину сети
    по ним ничего не положить.
    """
    skip = f" AND {real_sku_sql()}" if real_only else ""
    with get_conn() as c:
        r = c.execute(
            "SELECT sp.* FROM product_mapping m JOIN store_products sp ON sp.id = m.store_product_id"
            " JOIN stores s ON s.id = sp.store_id"
            f" WHERE m.product_id=? AND sp.store_id=? AND m.confirmed=1{skip}"
            # Подтверждений у товара в одном магазине может быть несколько: прежнее
            # на артикул-заглушку из резервного CSV и новое на настоящий код сети.
            # Без порядка побеждало произвольное — на деле старое, и ссылка на корзину
            # не строилась, потому что сеть своего же товара по заглушке не узнаёт.
            " ORDER BY m.confirmed_at DESC, sp.id DESC LIMIT 1", (product_id, store_id)).fetchone()
    return dict(r) if r else None


def product_mappings(product_id: int) -> list[dict]:
    """Все подтверждённые сопоставления товара по сетям: артикул, код сети, откуда.

    method читается, если колонка уже есть: база, которую после обновления ещё не
    открывали (приёмная дверь не зовёт init_db), отвечает без него.
    """
    with get_conn() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(product_mapping)")}
        method = "m.method" if "method" in have else "NULL"
        seen = "m.seen_name" if "seen_name" in have else "NULL"
        rows = c.execute(
            f"SELECT sp.id AS store_product_id, sp.sku, sp.ean, sp.store_id, s.code AS store_code,"
            f" sp.raw_name, m.confirmed_at, {method} AS method, {seen} AS seen_name"
            " FROM product_mapping m"
            " JOIN store_products sp ON sp.id = m.store_product_id"
            " JOIN stores s ON s.id = sp.store_id"
            " WHERE m.product_id=? AND m.confirmed=1 ORDER BY m.confirmed_at DESC, sp.id DESC",
            (product_id,)).fetchall()
    return [dict(r) for r in rows]


def mapping_method(product_id: int, store_product_id: int) -> str | None:
    """Откуда сопоставление: «master» — из единой базы, None — человек или прежнее."""
    with get_conn() as c:
        try:
            r = c.execute("SELECT method FROM product_mapping WHERE product_id=? AND store_product_id=?",
                          (product_id, store_product_id)).fetchone()
        except Exception:  # noqa: BLE001 — база прежней версии: колонки ещё нет
            return None
    return r["method"] if r else None


def unconfirm_mapping(product_id: int, store_product_id: int) -> None:
    """Снять подтверждение (строка остаётся: история цен по артикулу не пропадает)."""
    with get_conn() as c:
        c.execute("UPDATE product_mapping SET confirmed=0, confirmed_at=NULL"
                  " WHERE product_id=? AND store_product_id=?", (product_id, store_product_id))
        c.commit()


def drop_base_candidates(product_id: int, store_id: int, keep: int | None = None) -> None:
    """Убрать неподтверждённых кандидатов единой базы в сети, кроме keep.

    Кандидат базы в сети один — нынешний; прежние (база сменила строку, сеть сняла
    товар) только путали бы на «Связях». Товар сети и его цены остаются: удаляется
    одна запись сопоставления.
    """
    with get_conn() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(product_mapping)")}
        if "method" not in have:
            return
        c.execute("DELETE FROM product_mapping WHERE product_id=? AND confirmed=0"
                  " AND method='master' AND store_product_id != ? AND store_product_id IN"
                  " (SELECT id FROM store_products WHERE store_id=?)",
                  (product_id, keep if keep is not None else -1, store_id))
        c.commit()


def drop_base_links(product_id: int) -> None:
    """Убрать всё, что единая база записала товару: правила больше не находят его в ней.

    Выбор человека и живого поиска (method не «master») не трогается.
    """
    with get_conn() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(product_mapping)")}
        if "method" not in have:
            return
        c.execute("DELETE FROM product_mapping WHERE product_id=? AND method='master'", (product_id,))
        c.commit()


def raw_names(product_id: int, limit: int = 3) -> list[str]:
    """Строки чеков, которыми товар приходил, свежие первыми.

    Название эталона — уже нормализованное и теряет голову и процент строки чека:
    «МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5%» становится «Паст простоквашино 2 5». Искать
    товар в единой базе и судить о жирности надо по исходной строке (17.10.2026).
    """
    with get_conn() as c:
        rows = c.execute("SELECT raw_name, MAX(date) AS last FROM purchase_history"
                         " WHERE product_id=? AND raw_name IS NOT NULL AND raw_name != ''"
                         " GROUP BY raw_name ORDER BY last DESC LIMIT ?",
                         (product_id, int(limit))).fetchall()
    return [r["raw_name"] for r in rows]


def master_of(product_id: int) -> dict | None:
    """Привязка товара к единой базе: {master_id, method, at} или None."""
    with get_conn() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(products)")}
        if "master_id" not in have:
            return None
        r = c.execute("SELECT master_id, master_method, master_at FROM products WHERE id=?",
                      (product_id,)).fetchone()
    if not r or r["master_id"] is None:
        return None
    return {"master_id": int(r["master_id"]), "method": r["master_method"], "at": r["master_at"]}


def set_master(product_id: int, master_id: int | None, method: str | None) -> None:
    """Привязать товар к единому товару (или снять привязку, master_id=None)."""
    with get_conn() as c:
        c.execute("UPDATE products SET master_id=?, master_method=?, master_at=? WHERE id=?",
                  (master_id, method if master_id is not None else None,
                   NOW() if master_id is not None else None, product_id))
        c.commit()


def coverage(basket_id: int) -> list[dict]:
    """Какие товары корзины не уедут в корзину каждой сети: [{code, name, known, of, unknown}].

    «Уедет» — есть подтверждённый НАСТОЯЩИЙ артикул: только по нему товар попадёт в
    корзину сети (cartplan.build). Один запрос на всю корзину, а не по соединению на
    каждую пару «товар — сеть». Порядок — как в справочнике магазинов.
    """
    items = basket_items(basket_id)
    ids = sorted({int(it["product_id"]) for it in items if it.get("product_id")})
    known: set[tuple[int, int]] = set()
    if ids:
        marks = ",".join("?" for _ in ids)
        with get_conn() as c:
            known = {(int(r[0]), int(r[1])) for r in c.execute(
                "SELECT DISTINCT m.product_id, sp.store_id FROM product_mapping m"
                " JOIN store_products sp ON sp.id = m.store_product_id"
                " JOIN stores s ON s.id = sp.store_id"
                f" WHERE m.confirmed=1 AND m.product_id IN ({marks}) AND {real_sku_sql()}", ids)}
    out: list[dict] = []
    for store in list_stores():
        unknown = [it.get("name") or "—" for it in items
                   if (it.get("product_id"), store.id) not in known]
        out.append({"code": store.code, "name": store.name, "known": len(items) - len(unknown),
                    "of": len(items), "unknown": unknown})
    return out


def base_candidates(product_id: int, store_id: int) -> list[dict]:
    """Кандидаты единой базы в этой сети: записаны ею без подтверждения (слабая связь).

    Их показывают на «Связях» с кнопкой «Подтвердить»: в корзину они уедут только
    после выбора человека.
    """
    with get_conn() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(product_mapping)")}
        if "method" not in have:
            return []
        rows = c.execute(
            "SELECT sp.* FROM product_mapping m JOIN store_products sp ON sp.id = m.store_product_id"
            " WHERE m.product_id=? AND sp.store_id=? AND m.confirmed=0 AND m.method='master'"
            " ORDER BY sp.id DESC", (product_id, store_id)).fetchall()
    return [dict(r) for r in rows]


def mapping_matrix(real_only: bool = False) -> dict[tuple[int, int], bool]:
    """(product_id, store_id) -> confirmed. Для экрана Номенклатура.

    real_only — подтверждённой считается только связь с настоящим артикулом сети;
    пара, где подтверждена одна заглушка, получает False: что-то есть, но в корзину
    сети не уедет. Так считают «Корзина» и «Связи», чтобы их подсказки не расходились
    с нарядом (ревью 18.10.2026).
    """
    real = (f"CASE WHEN m.confirmed=1 AND {real_sku_sql()} THEN 1 ELSE 0 END"
            if real_only else "m.confirmed")
    with get_conn() as c:
        rows = c.execute(f"SELECT m.product_id, sp.store_id, MAX({real}) AS confirmed"
                         " FROM product_mapping m"
                         " JOIN store_products sp ON sp.id = m.store_product_id"
                         " JOIN stores s ON s.id = sp.store_id"
                         " GROUP BY m.product_id, sp.store_id").fetchall()
    return {(r["product_id"], r["store_id"]): bool(r["confirmed"]) for r in rows}


def save_price(store_product_id: int, price: float, price_per_kg: float | None = None,
               in_stock: bool = True, fetched_at: str | None = None,
               source: str | None = None, *, point_key: str | None = None,
               mode: str | None = None) -> None:
    """Снимок цены — только вставка, история не перезаписывается.

    source — откуда цена: None для живой, "fallback" для справочной из CSV.
    point_key/mode — где и как снята цена (см. store_prices в app/db.py). Оба
    именованные: прежние вызовы пишут снимок как раньше, без новых колонок, и
    принимаются базой, которую после обновления ещё не открывали.
    """
    columns = ["store_product_id", "price", "price_per_kg", "in_stock", "fetched_at"]
    values: list = [store_product_id, price, price_per_kg, int(in_stock), fetched_at or NOW()]
    if source is not None:
        columns.append("source")
        values.append(source)
    if point_key is not None:
        columns.append("point_key")
        values.append(point_key)
    if mode is not None:
        columns.append("mode")
        values.append(mode)
    marks = ",".join("?" * len(columns))
    with get_conn() as c:
        c.execute(f"INSERT INTO store_prices ({', '.join(columns)}) VALUES ({marks})", values)
        c.commit()


def latest_price(store_product_id: int, point_key: str | None = None) -> dict | None:
    """Последний снимок цены товара сети.

    point_key задан — сначала снимок этой точки, а если его нет — общий (point_key
    IS NULL). Снимок ЧУЖОЙ точки не подставляется никогда: это цена другого
    магазина. Без point_key — прежнее поведение: самый свежий снимок любой точки.
    """
    with get_conn() as c:
        if point_key is None:
            r = c.execute(
                "SELECT * FROM store_prices WHERE store_product_id=?"
                " ORDER BY fetched_at DESC, id DESC LIMIT 1", (store_product_id,)).fetchone()
        else:
            r = c.execute(
                "SELECT * FROM store_prices WHERE store_product_id=? AND point_key=?"
                " ORDER BY fetched_at DESC, id DESC LIMIT 1", (store_product_id, point_key)).fetchone()
            if r is None:
                r = c.execute(
                    "SELECT * FROM store_prices WHERE store_product_id=? AND point_key IS NULL"
                    " ORDER BY fetched_at DESC, id DESC LIMIT 1", (store_product_id,)).fetchone()
    return dict(r) if r else None


def latest_price_for(product_id: int, store_id: int,
                     point_key: str | None = None) -> dict | None:
    """Актуальная цена эталона в магазине по подтверждённому сопоставлению.

    К снимку приложены единица, фасовка и название товара сети (sp_unit,
    sp_weight_g, sp_name): без них цену упаковки не отличить от цены килограмма.
    point_key — см. latest_price: цена этой точки, иначе общий снимок.
    """
    sp = confirmed_mapping(product_id, store_id)
    if not sp:
        return None
    snap = latest_price(sp["id"], point_key=point_key)
    if snap is None:
        return None
    snap["sp_unit"] = sp.get("unit")
    snap["sp_weight_g"] = sp.get("weight_g")
    snap["sp_name"] = sp.get("raw_name")
    return snap


def last_price_moment(store_id: int, product_ids: list[int] | None = None,
                      point_key: str | None = None) -> str | None:
    """Когда сеть последний раз отдавала ЖИВУЮ цену — «цена от 14:32» на экране.

    Живая — это снятая с витрины, а не справочная из data/fallback_prices.csv
    (source=freshness.REFERENCE): у справочной дата «сейчас» — дата попытки, а не
    факт с полки, и подпись «цена от …» на ней была бы выдумкой.

    product_ids — считать только снимки этих товаров (товары корзины); None —
    любые. Пустой список — товаров нет, и момента тоже нет.

    point_key — считать только снимки этой точки; None — только снимки БЕЗ точки.
    Снимок чужой точки не подставляется так же, как в freshness.price_for: у
    Ленты, Магнита и METRO цена своя в каждом магазине, и «витрина у адреса» по
    цене другого адреса была бы чужой ценой.

    Старая форма last_price_moment(store_id) работает: это общие снимки сети.
    """
    from app import freshness

    if product_ids is not None and not product_ids:
        return None

    sql = ["SELECT MAX(p.fetched_at) AS moment FROM store_prices p",
           " JOIN store_products sp ON sp.id = p.store_product_id",
           " WHERE sp.store_id=?",
           " AND (p.source IS NULL OR p.source != ?)"]
    params: list = [store_id, freshness.REFERENCE]
    if product_ids is not None:
        marks = ",".join("?" * len(product_ids))
        sql.append(" AND p.store_product_id IN (SELECT m.store_product_id"
                   " FROM product_mapping m WHERE m.product_id IN (" + marks + "))")
        params.extend(int(pid) for pid in product_ids)
    if point_key is None:
        sql.append(" AND p.point_key IS NULL")
    else:
        sql.append(" AND p.point_key = ?")
        params.append(point_key)

    with get_conn() as c:
        row = c.execute("".join(sql), tuple(params)).fetchone()
    return row["moment"] if row and row["moment"] else None


def price_history(product_id: int, store_id: int) -> list[dict]:
    sp = confirmed_mapping(product_id, store_id)
    if not sp:
        return []
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM store_prices WHERE store_product_id=? ORDER BY fetched_at", (sp["id"],))]


# ---------- baskets ----------
def create_basket(name: str, source: str = "manual") -> int:
    with get_conn() as c:
        cur = c.execute("INSERT INTO baskets (name, created_at, source) VALUES (?,?,?)", (name, NOW(), source))
        c.commit()
        return cur.lastrowid


def list_baskets() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM baskets ORDER BY created_at DESC")]


def whole_pieces(qty: float) -> float:
    """Штучный товар покупают целыми штуками: 1,3 → 1, 1,5 → 2, 0,3 → 1.

    Дробная штука считалась ценой ×1,5, а в магазин уезжала округлённой —
    итог расчёта и чек расходились. Округление к ближайшему, но не ниже одной
    штуки: средняя корзина по истории даёт «1,3 батона в неделю», и это батон.
    """
    qty = float(qty)
    if qty <= 0:
        return 0.0
    return float(max(1, math.floor(qty + 0.5)))


def set_basket_item(basket_id: int, product_id: int, qty: float) -> None:
    with get_conn() as c:
        if qty > 0:
            row = c.execute("SELECT unit FROM products WHERE id=?", (product_id,)).fetchone()
            if row is not None and (row["unit"] or "pcs") == "pcs":
                qty = whole_pieces(qty)
        if qty <= 0:
            c.execute("DELETE FROM basket_items WHERE basket_id=? AND product_id=?", (basket_id, product_id))
        else:
            c.execute("INSERT INTO basket_items (basket_id, product_id, qty) VALUES (?,?,?)"
                      " ON CONFLICT(basket_id, product_id) DO UPDATE SET qty=excluded.qty",
                      (basket_id, product_id, qty))
        c.commit()


def basket_items(basket_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT bi.product_id, bi.qty, p.name, p.unit, p.weight_g, p.brand FROM basket_items bi"
            " JOIN products p ON p.id = bi.product_id WHERE bi.basket_id=? ORDER BY p.name", (basket_id,))]


def delete_basket(basket_id: int) -> None:
    """Удаляет корзину целиком вместе с позициями.

    Нужна автонабору: сборка из истории заводит СВОЮ корзину, а наполняем мы
    текущую — без уборки список корзин копил бы по мусорной записи на каждое
    открытие экрана.
    """
    with get_conn() as c:
        c.execute("DELETE FROM basket_items WHERE basket_id=?", (basket_id,))
        c.execute("DELETE FROM baskets WHERE id=?", (basket_id,))
        c.commit()


def clear_basket(basket_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM basket_items WHERE basket_id=?", (basket_id,))
        c.commit()


# ---------- purchase history ----------
def add_history_row(date: str, store_id: int | None, product_id: int | None, raw_name: str,
                    qty: float, unit_price: float, total: float,
                    receipt_key: str | None = None) -> int:
    with get_conn() as c:
        cur = c.execute("INSERT INTO purchase_history"
                        " (date, store_id, product_id, raw_name, qty, unit_price, total, receipt_key)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (date, store_id, product_id, raw_name, qty, unit_price, total, receipt_key))
        c.commit()
        return cur.lastrowid


def list_history(date_from: str | None = None, date_to: str | None = None,
                 store_id: int | None = None) -> list[dict]:
    sql = ("SELECT h.*, s.name AS store_name, s.code AS store_code,"
           " p.name AS product_name, p.unit AS unit FROM purchase_history h"
           " LEFT JOIN stores s ON s.id = h.store_id LEFT JOIN products p ON p.id = h.product_id WHERE 1=1")
    params: list = []
    if date_from:
        sql += " AND h.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND h.date <= ?"
        params.append(date_to)
    if store_id:
        sql += " AND h.store_id = ?"
        params.append(store_id)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY h.date DESC, h.id", tuple(params))]


# ---------- settings ----------
# Пары «ключ — значение» для того немногого, что человек задаёт один раз и надолго:
# адрес доставки и разрешённые из него точки магазинов. В config.yaml им не место —
# там настройки установки, а это ответ пользователя, и он обязан пережить перезапуск.
def get_setting(key: str, default: str | None = None) -> str | None:
    with get_conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str | None) -> None:
    """Пустое значение стирает ключ: «адреса нет» и «адрес пустая строка» — одно и то же."""
    with get_conn() as c:
        if value is None or not str(value).strip():
            c.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            c.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, str(value).strip()))
        c.commit()


# ---------- учёт чеков ----------
# Зачем отдельная таблица, когда есть purchase_history. История — это позиции, а
# человеку нужно отвечать про ЧЕКИ: сколько загружено и сколько ещё лежит в кабинете
# непрочитанным. Поэтому чек здесь заводится в двух состояниях: «виден» (ключ пришёл
# из списка кабинета, позиций нет) и «загружен» (позиции легли в историю). Разница
# между этими двумя множествами и есть ответ «что ещё можно загрузить».
def note_receipt(key: str, date: str | None = None, store: str | None = None,
                 total: float | None = None, source: str = "lkdr") -> None:
    """Чек попался на глаза. Уже загруженный не трогаем: у него данные точнее."""
    if not key:
        return
    with get_conn() as c:
        c.execute(
            "INSERT INTO receipts (key, date, store, total, rows, source, seen_at, imported_at)"
            " VALUES (?,?,?,?,NULL,?,?,NULL)"
            " ON CONFLICT(key) DO UPDATE SET"
            "   date = COALESCE(receipts.date, excluded.date),"
            "   store = COALESCE(receipts.store, excluded.store),"
            "   total = COALESCE(receipts.total, excluded.total)",
            (key, date, store, total, source, NOW()))
        c.commit()


def mark_receipt_imported(key: str, date: str | None, store: str | None,
                          total: float | None, rows: int, source: str = "lkdr") -> None:
    if not key:
        return
    with get_conn() as c:
        c.execute(
            "INSERT INTO receipts (key, date, store, total, rows, source, seen_at, imported_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET"
            "   date = excluded.date, store = excluded.store, total = excluded.total,"
            "   rows = excluded.rows, source = excluded.source, imported_at = excluded.imported_at",
            (key, date, store, total, rows, source, NOW(), NOW()))
        c.commit()


def imported_receipt_keys() -> set[str]:
    with get_conn() as c:
        return {r["key"] for r in c.execute("SELECT key FROM receipts WHERE imported_at IS NOT NULL")}


NO_DATA = -1   # в поле rows: состава этого чека у источника больше нет


def mark_receipt_unavailable(key: str) -> None:
    """Состав чека взять неоткуда, и уже не будет откуда.

    ФНС хранит содержимое чека ограниченный срок: на чеках 2018 года кабинет
    отвечает `receipt.fiscal.data.unavailable` — сам чек в ленте виден, а позиций
    за ним уже нет. Такой чек не «ещё не загружен», а «загружен не будет»: спрашивать
    его в каждую проверку значит зря дёргать ФНС и зря обещать человеку, что вот-вот
    догрузится. Отмечаем rows = -1 — чек остаётся в описи и в счётчике «видно в
    кабинете», но из очереди уходит.
    """
    if not key:
        return
    with get_conn() as c:
        c.execute("UPDATE receipts SET rows = ? WHERE key = ? AND imported_at IS NULL",
                  (NO_DATA, key))
        c.commit()


def unavailable_receipt_keys() -> set[str]:
    """Чеки, по которым источник уже сказал «состава нет». Второй раз не спрашиваем."""
    with get_conn() as c:
        return {r["key"] for r in c.execute(
            "SELECT key FROM receipts WHERE imported_at IS NULL AND rows = ?", (NO_DATA,))}


def pending_receipts() -> list[dict]:
    """Чеки, которые в кабинете есть, у нас ещё нет и которые ещё можно взять.

    Чеки с отметкой «состава нет» и чеки других магазинов сюда не попадают:
    «не загружено» должно означать «осталось работы», иначе счётчик навсегда замрёт
    на числе, которое не уменьшится.
    """
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM receipts WHERE imported_at IS NULL AND (rows IS NULL OR rows >= 0)"
            " ORDER BY date DESC, key")]


def unavailable_receipts() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM receipts WHERE imported_at IS NULL AND rows = ? ORDER BY date DESC, key",
            (NO_DATA,))]


OTHER_STORE = -2   # в поле rows: чек магазина, где мы не собираем заказы (app/chains.py)


def mark_receipt_other_store(key: str, date: str | None, store: str | None,
                             total: float | None, source: str = "lkdr") -> None:
    """Чек из магазина, где приложение не собирает заказы: в заказы он не идёт.

    Запоминаем его, а не выбрасываем: чтобы следующая загрузка не спрашивала его
    состав у ФНС заново и чтобы человек видел, что чек не потерян, а отложен —
    «Не добавлены: N чеков». Уже загруженный чек не трогаем.
    """
    if not key:
        return
    with get_conn() as c:
        c.execute(
            "INSERT INTO receipts (key, date, store, total, rows, source, seen_at, imported_at)"
            " VALUES (?,?,?,?,?,?,?,NULL)"
            " ON CONFLICT(key) DO UPDATE SET rows = excluded.rows,"
            "   store = COALESCE(receipts.store, excluded.store)"
            " WHERE receipts.imported_at IS NULL",
            (key, date, store, total, OTHER_STORE, source, NOW()))
        c.commit()


def other_store_receipts() -> list[dict]:
    """Отложенные чеки других магазинов — для строки «Не добавлены»."""
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM receipts WHERE imported_at IS NULL AND rows = ? ORDER BY date DESC, key",
            (OTHER_STORE,))]


def other_store_receipt_keys() -> set[str]:
    return {r["key"] for r in other_store_receipts()}


def imported_receipts() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM receipts WHERE imported_at IS NOT NULL ORDER BY date DESC, key")]


def forget_receipt(key: str) -> None:
    """Убирает чек из учёта вместе с его позициями в истории."""
    with get_conn() as c:
        c.execute("DELETE FROM purchase_history WHERE receipt_key = ?", (key,))
        c.execute("DELETE FROM receipts WHERE key = ?", (key,))
        c.commit()
