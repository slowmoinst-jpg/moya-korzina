import fs from 'node:fs';
import path from 'node:path';
import {
  chooseBestVariant,
  handoverByStore,
  KIND_BY_STORE,
  optimize,
  SPLIT_THRESHOLD_RUB,
} from '../optimizer.js';
import { humanDay, monthTitle, rub, where, whom } from '../helpers.js';
import type {
  Basket,
  BasketItem,
  BasketLine,
  Card,
  Offer,
  Product,
  PurchaseRow,
  ReceiptRecord,
  Store,
  StoreConnection,
  StoreProductPrice,
  UnitType,
  Variant,
  Workspace,
} from '../types.js';

export const DEMO_PHONE = 'demo';

export const PHONE_CHAINS = [
  'magnit',
  'pyaterochka',
  'vkusvill',
  'lenta',
  'dixy',
  'samokat',
  'metro',
  'perekrestok',
  'fixprice',
];

export const ORDERABLE_STORES = new Set([
  'magnit',
  'vkusvill',
  'pyaterochka',
  'lenta',
  'dixy',
  'samokat',
  'metro',
]);

export const SEED_STORES: Store[] = [
  { id: 1, code: 'magnit', name: 'Магнит', delivery_fee: 149, free_delivery_from: 2000, min_order: 500, connector_type: 'magnit' },
  { id: 2, code: 'vkusvill', name: 'ВкусВилл', delivery_fee: 99, free_delivery_from: 1500, min_order: 400, connector_type: 'vkusvill' },
  { id: 3, code: 'pyaterochka', name: 'Пятёрочка', delivery_fee: 199, free_delivery_from: 2500, min_order: 600, connector_type: 'pyaterochka' },
  { id: 4, code: 'lenta', name: 'Лента', delivery_fee: 199, free_delivery_from: 3000, min_order: 1000, connector_type: 'lenta' },
  { id: 5, code: 'dixy', name: 'Дикси', delivery_fee: 149, free_delivery_from: 2000, min_order: 500, connector_type: 'dixy' },
  { id: 6, code: 'samokat', name: 'Самокат', delivery_fee: 0, free_delivery_from: 0, min_order: 0, connector_type: 'samokat' },
  { id: 7, code: 'metro', name: 'METRO', delivery_fee: 0, free_delivery_from: 5000, min_order: 1500, connector_type: 'metro' },
  { id: 8, code: 'perekrestok', name: 'Перекрёсток', delivery_fee: 199, free_delivery_from: 3000, min_order: 800, connector_type: 'stub' },
  { id: 9, code: 'fixprice', name: 'FixPrice', delivery_fee: 199, free_delivery_from: 2500, min_order: 500, connector_type: 'stub' },
  { id: 10, code: 'vprok', name: 'Впрок', delivery_fee: 199, free_delivery_from: 3000, min_order: 800, connector_type: 'stub' },
  { id: 11, code: 'monetka', name: 'Монетка', delivery_fee: 199, free_delivery_from: 2500, min_order: 500, connector_type: 'stub' },
];

export const SEARCH_URL_TEMPLATES: Record<string, string> = {
  pyaterochka: 'https://5ka.ru/search/?text={q}',
  samokat: 'https://samokat.ru/search?q={q}',
  magnit: 'https://magnit.ru/search?q={q}',
  dixy: 'https://dixy.ru/search/?q={q}',
  metro: 'https://online.metro-cc.ru/search?q={q}',
  vkusvill: 'https://vkusvill.ru/search/?q={q}',
  lenta: 'https://lenta.com/search/?searchText={q}',
};

const STORE_BY_CODE = new Map(SEED_STORES.map((s) => [s.code, s]));

function parseCsv(filename: string): Array<Record<string, string>> {
  const fullPath = path.resolve(process.cwd(), 'data', filename);
  if (!fs.existsSync(fullPath)) return [];
  const raw = fs.readFileSync(fullPath, 'utf-8').replace(/^\uFEFF/, '');
  const lines = raw.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return [];

  const splitLine = (line: string): string[] => {
    const out: string[] = [];
    let cur = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (inQuotes && line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (ch === ',' && !inQuotes) {
        out.push(cur);
        cur = '';
      } else {
        cur += ch;
      }
    }
    out.push(cur);
    return out;
  };

  const headers = splitLine(lines[0]).map((h) => h.trim());
  const rows: Array<Record<string, string>> = [];
  for (let i = 1; i < lines.length; i++) {
    const vals = splitLine(lines[i]);
    const rec: Record<string, string> = {};
    for (let j = 0; j < headers.length; j++) {
      rec[headers[j]] = (vals[j] ?? '').trim();
    }
    rows.push(rec);
  }
  return rows;
}

interface CatalogSeed {
  products: Product[];
  pricesByProductName: Map<string, Map<string, StoreProductPrice>>;
  offers: Array<{
    bank: string;
    card: string;
    store_code: string;
    percent: number;
    cap_rub: number;
    min_check_rub: number;
    requires_activation: boolean;
    activated: boolean;
    cap_used: number;
  }>;
  receiptRows: Array<{ name: string; qty: number; unit_price: number }>;
}

let cachedSeed: CatalogSeed | null = null;

function loadSeed(): CatalogSeed {
  if (cachedSeed) return cachedSeed;
  const nowIso = new Date().toISOString().slice(0, 19);
  const seedProducts = parseCsv('seed_products.csv');
  const fallbackPrices = parseCsv('fallback_prices.csv');
  const seedOffers = parseCsv('seed_offers.csv');
  const seedReceipt = parseCsv('seed_receipt.csv');
  const demoBasket = parseCsv('demo_basket.csv');

  const products: Product[] = [];
  const seenNames = new Set<string>();
  let pid = 1;

  for (const row of seedProducts) {
    if (!row.name) continue;
    seenNames.add(row.name.toLowerCase());
    products.push({
      id: pid++,
      name: row.name,
      brand: row.brand || null,
      weight_g: row.weight_g ? Number(row.weight_g) : null,
      unit: row.unit === 'kg' ? 'kg' : 'pcs',
      category: row.category || null,
      active: true,
    });
  }

  for (const row of demoBasket) {
    const title = row.note && !row.note.includes(';') ? row.name : row.name;
    if (!title || seenNames.has(title.toLowerCase())) continue;
    seenNames.add(title.toLowerCase());
    const isKg = /весов/i.test(title);
    products.push({
      id: pid++,
      name: title,
      brand: null,
      weight_g: null,
      unit: isKg ? 'kg' : 'pcs',
      category: 'Из каталога',
      active: true,
    });
  }

  const pricesByProductName = new Map<string, Map<string, StoreProductPrice>>();

  const matchProductName = (rawName: string): string | null => {
    const low = rawName.toLowerCase();
    for (const p of products) {
      const words = p.name.toLowerCase().split(/\s+/).filter((w) => w.length >= 4);
      if (words.length > 0 && words.slice(0, Math.min(2, words.length)).every((w) => low.includes(w))) {
        return p.name;
      }
    }
    return null;
  };

  for (const r of fallbackPrices) {
    const matchedName = matchProductName(r.name);
    if (!matchedName) continue;
    let storeMap = pricesByProductName.get(matchedName);
    if (!storeMap) {
      storeMap = new Map();
      pricesByProductName.set(matchedName, storeMap);
    }
    const price = Number(r.price);
    storeMap.set(r.store_code, {
      store_code: r.store_code,
      sku: r.sku,
      raw_name: r.name,
      price,
      unit: r.unit === 'kg' ? 'kg' : 'pcs',
      weight_g: r.weight_g ? Number(r.weight_g) : null,
      price_per_kg: r.price_per_kg ? Number(r.price_per_kg) : null,
      in_stock: r.in_stock !== '0',
      fetched_at: nowIso,
      source: 'fallback',
    });

    // Also provide Lenta baseline prices for seeded items (slightly below Pyaterochka on bulk)
    if (r.store_code === 'pyaterochka' && !storeMap.has('lenta')) {
      const lentaPrice = Math.round((price * 0.96) * 100) / 100;
      storeMap.set('lenta', {
        store_code: 'lenta',
        sku: r.sku.replace('pyaterochka-', 'lenta-'),
        raw_name: r.name,
        price: lentaPrice,
        unit: r.unit === 'kg' ? 'kg' : 'pcs',
        weight_g: r.weight_g ? Number(r.weight_g) : null,
        price_per_kg: r.price_per_kg ? Math.round((Number(r.price_per_kg) * 0.96) * 100) / 100 : null,
        in_stock: true,
        fetched_at: nowIso,
        source: 'fallback',
      });
    }
  }

  const offers = seedOffers
    .filter((r) => r.bank && r.card && r.store_code)
    .map((r) => ({
      bank: r.bank,
      card: r.card,
      store_code: r.store_code,
      percent: Number(r.percent || 0),
      cap_rub: Number(r.cap_rub || 0),
      min_check_rub: Number(r.min_check_rub || 0),
      requires_activation: r.requires_activation === '1',
      activated: r.activated !== '0',
      cap_used: Number(r.cap_used || 0),
    }));

  const receiptRows = seedReceipt
    .filter((r) => r.name)
    .map((r) => ({
      name: r.name,
      qty: Number(r.qty || 1),
      unit_price: Number(r.unit_price || 0),
    }));

  cachedSeed = { products, pricesByProductName, offers, receiptRows };
  return cachedSeed;
}

const workspaces = new Map<string, Workspace>();

export function normalizePhone(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  if (trimmed.toLowerCase() === DEMO_PHONE) return DEMO_PHONE;
  const digits = trimmed.replace(/\D/g, '');
  if (digits.length === 10) return '7' + digits;
  if (digits.length === 11 && (digits.startsWith('7') || digits.startsWith('8'))) {
    return '7' + digits.slice(1);
  }
  return null;
}

export function resolveLogin(raw: string | null | undefined): [string | null, string | null] {
  const trimmed = (raw || '').trim();
  if (!trimmed) return [null, 'Введите номер телефона'];
  if (trimmed.toLowerCase() === DEMO_PHONE) {
    return [null, 'Чтобы посмотреть пример, нажмите «Посмотреть демо»'];
  }
  const normalized = normalizePhone(trimmed);
  if (!normalized) {
    return [null, 'Не похож на российский номер: нужно 10 или 11 цифр, например +7 999 123-45-67'];
  }
  return [normalized, null];
}

export function formatPhoneDisplay(phone: string): string {
  if (phone === DEMO_PHONE) return 'Демо';
  if (phone.length === 11 && phone.startsWith('7')) {
    return `+7 ${phone.slice(1, 4)} ${phone.slice(4, 7)}-${phone.slice(7, 9)}-${phone.slice(9, 11)}`;
  }
  return phone;
}

function buildWorkspace(phone: string): Workspace {
  const seed = loadSeed();
  const isDemo = phone === DEMO_PHONE;

  const products = new Map<number, Product>();
  const prices = new Map<number, Map<string, StoreProductPrice>>();
  let maxId = 0;

  for (const p of seed.products) {
    products.set(p.id, { ...p });
    if (p.id > maxId) maxId = p.id;
    const pMap = seed.pricesByProductName.get(p.name);
    if (pMap) {
      const copy = new Map<string, StoreProductPrice>();
      for (const [k, v] of pMap.entries()) copy.set(k, { ...v });
      prices.set(p.id, copy);
    }
  }

  const cards: Card[] = [];
  const cardByKey = new Map<string, Card>();
  const offers: Offer[] = [];
  const connections = new Map<string, StoreConnection>();
  for (const code of PHONE_CHAINS) {
    connections.set(code, {
      store_code: code,
      connected: isDemo && (code === 'vkusvill' || code === 'lenta' || code === 'magnit'),
      again: false,
      last: null,
    });
  }

  const now = new Date();
  const validFrom = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
  const validTo = new Date(now.getFullYear(), now.getMonth() + 2, 0).toISOString().slice(0, 10);

  if (isDemo) {
    let cardId = 1;
    let offerId = 1;
    for (const o of seed.offers) {
      const key = `${o.bank}|${o.card}`;
      let card = cardByKey.get(key);
      if (!card) {
        card = { id: cardId++, bank: o.bank, name: o.card };
        cardByKey.set(key, card);
        cards.push(card);
      }
      const st = STORE_BY_CODE.get(o.store_code);
      if (!st) continue;
      offers.push({
        id: offerId++,
        card_id: card.id,
        store_id: st.id,
        percent: o.percent,
        cap_rub: o.cap_rub,
        min_check_rub: o.min_check_rub,
        valid_from: validFrom,
        valid_to: validTo,
        requires_activation: o.requires_activation,
        activated: o.activated,
        cap_used: o.cap_used,
      });
    }
  }

  const baskets: Basket[] = [];
  const basketItems = new Map<number, BasketItem[]>();
  const history: PurchaseRow[] = [];
  const receipts: ReceiptRecord[] = [];
  let nextBasketId = 1;

  if (isDemo) {
    const productByName = new Map(seed.products.map((p) => [p.name, p]));
    const receiptDate = new Date(now.getTime() - 4 * 86400_000).toISOString().slice(0, 10);
    const prevDate = new Date(now.getTime() - 18 * 86400_000).toISOString().slice(0, 10);
    const st = STORE_BY_CODE.get('pyaterochka')!;
    const vv = STORE_BY_CODE.get('vkusvill')!;
    const receiptKey1 = `demo:${receiptDate}:pyaterochka`;
    const receiptKey2 = `demo:${prevDate}:vkusvill`;

    let total1 = 0;
    let rowId = 1;
    const itemsForBasket: BasketItem[] = [];
    for (const r of seed.receiptRows) {
      const prod = productByName.get(r.name);
      const lineTotal = Math.round(r.qty * r.unit_price * 100) / 100;
      total1 += lineTotal;
      history.push({
        id: rowId++,
        date: receiptDate,
        store_id: st.id,
        store_code: st.code,
        store_name: st.name,
        product_id: prod ? prod.id : null,
        raw_name: r.name,
        qty: r.qty,
        unit_price: r.unit_price,
        total: lineTotal,
        receipt_key: receiptKey1,
      });
      if (prod) {
        itemsForBasket.push({ basket_id: 1, product_id: prod.id, qty: r.qty });
      }
    }
    receipts.push({
      key: receiptKey1,
      date: receiptDate,
      store: st.name,
      total: Math.round(total1 * 100) / 100,
      rows: seed.receiptRows.length,
      source: 'lkdr',
    });

    // Second older receipt so habits ("часто берёте" / "пора докупить") trigger nicely
    let total2 = 0;
    const repeatSubset = seed.receiptRows.slice(0, 6);
    for (const r of repeatSubset) {
      const prod = productByName.get(r.name);
      const unitP = Math.round(r.unit_price * 0.95 * 100) / 100;
      const lineTotal = Math.round(r.qty * unitP * 100) / 100;
      total2 += lineTotal;
      history.push({
        id: rowId++,
        date: prevDate,
        store_id: vv.id,
        store_code: vv.code,
        store_name: vv.name,
        product_id: prod ? prod.id : null,
        raw_name: r.name,
        qty: r.qty,
        unit_price: unitP,
        total: lineTotal,
        receipt_key: receiptKey2,
      });
    }
    receipts.push({
      key: receiptKey2,
      date: prevDate,
      store: vv.name,
      total: Math.round(total2 * 100) / 100,
      rows: repeatSubset.length,
      source: 'lkdr',
    });

    const basketId = nextBasketId++;
    baskets.push({
      id: basketId,
      name: `Корзина из чека · ${humanDay(receiptDate)}`,
      created_at: receiptDate,
      source: 'history',
    });
    basketItems.set(basketId, itemsForBasket);
  }

  return {
    phone,
    address: isDemo ? 'Санкт-Петербург, Невский проспект, 28' : null,
    point: isDemo ? [59.9356, 30.3259] : null,
    products,
    nextProductId: maxId + 1,
    prices,
    cards,
    offers,
    baskets,
    nextBasketId,
    basketItems,
    history,
    receipts,
    connections,
    lastBasketId: baskets[0]?.id ?? null,
    repeated: null,
    fnsConnected: isDemo,
    fnsPhone: isDemo ? '+7 999 000-00-00' : null,
    fnsSyncedAt: isDemo ? new Date().toISOString().slice(0, 16).replace('T', ' ') : null,
  };
}

export function getWorkspace(phone: string): Workspace {
  let ws = workspaces.get(phone);
  if (!ws) {
    ws = buildWorkspace(phone);
    workspaces.set(phone, ws);
  }
  return ws;
}

export function getStore(code: string): Store | undefined {
  return STORE_BY_CODE.get(code);
}

export function getCurrentBasket(ws: Workspace, explicitId?: number | null): Basket | null {
  if (!ws.baskets.length) return null;
  if (explicitId) {
    return ws.baskets.find((b) => b.id === explicitId) ?? null;
  }
  if (ws.lastBasketId) {
    const remembered = ws.baskets.find((b) => b.id === ws.lastBasketId);
    if (remembered) return remembered;
  }
  return ws.baskets[0];
}

export function getBasketItems(ws: Workspace, basketId: number): Array<{
  product_id: number;
  name: string;
  title: string;
  unit: UnitType;
  weight_g: number | null;
  qty: number;
  photo: string | null;
}> {
  const items = ws.basketItems.get(basketId) || [];
  return items.map((it) => {
    const prod = ws.products.get(it.product_id);
    return {
      product_id: it.product_id,
      name: prod?.name || 'Товар',
      title: prod?.name || 'Товар',
      unit: prod?.unit || 'pcs',
      weight_g: prod?.weight_g ?? null,
      qty: it.qty,
      photo: prod?.photo ?? null,
    };
  });
}

export function setBasketItemQty(ws: Workspace, basketId: number, productId: number, qty: number): void {
  const list = ws.basketItems.get(basketId) || [];
  const idx = list.findIndex((x) => x.product_id === productId);
  if (qty <= 0) {
    if (idx !== -1) list.splice(idx, 1);
  } else if (idx !== -1) {
    list[idx].qty = Math.round(qty * 1000) / 1000;
  } else {
    list.push({ basket_id: basketId, product_id: productId, qty: Math.round(qty * 1000) / 1000 });
  }
  ws.basketItems.set(basketId, list);
  ws.lastBasketId = basketId;
}

export function createBasket(
  ws: Workspace,
  name: string,
  source: 'manual' | 'average' | 'history' = 'manual'
): Basket {
  const id = ws.nextBasketId++;
  const basket: Basket = {
    id,
    name: name.trim() || `Корзина #${id}`,
    created_at: new Date().toISOString().slice(0, 10),
    source,
  };
  ws.baskets.unshift(basket);
  ws.basketItems.set(id, []);
  ws.lastBasketId = id;
  return basket;
}

export function stepForUnit(unit?: string | null): number {
  return unit === 'kg' ? 0.1 : 1;
}

export function searchProducts(
  ws: Workspace,
  query: string,
  limit = 30
): Array<{ id: number; name: string; unit: UnitType; brand: string | null; chainsCount: number; doVal: string; small: string }> {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const tokens = q.split(/\s+/).filter(Boolean);
  const results: Array<{ id: number; name: string; unit: UnitType; brand: string | null; chainsCount: number; doVal: string; small: string }> = [];

  for (const p of ws.products.values()) {
    if (!p.active) continue;
    const hay = `${p.name} ${p.brand || ''} ${p.category || ''}`.toLowerCase();
    if (!tokens.every((t) => hay.includes(t))) continue;
    const pMap = ws.prices.get(p.id);
    const chainsCount = pMap ? pMap.size : 0;
    const parts: string[] = [];
    if (p.weight_g) parts.push(`${Math.round(p.weight_g)} г`);
    else parts.push(p.unit === 'kg' ? 'на вес' : 'шт');
    if (p.brand) parts.push(p.brand);
    if (chainsCount > 0) parts.push(`в ${chainsCount} ${chainsCount === 1 ? 'сети' : 'сетях'}`);
    results.push({
      id: p.id,
      name: p.name,
      unit: p.unit,
      brand: p.brand,
      chainsCount,
      doVal: `add:${p.id}`,
      small: parts.join(' · '),
    });
    if (results.length >= limit) break;
  }
  return results;
}

export function getHabits(ws: Workspace, basketId: number, limit = 8): Array<{
  pid: number;
  name: string;
  unit: UnitType;
  why: string;
  qty: number;
  photo: string | null;
  times: number;
  cycle: number;
  since: number;
}> {
  const inBasket = new Set((ws.basketItems.get(basketId) || []).map((i) => i.product_id));
  const byProd = new Map<number, { days: Map<string, number>; name: string; unit: UnitType }>();
  for (const row of ws.history) {
    if (!row.product_id) continue;
    const prod = ws.products.get(row.product_id);
    if (!prod) continue;
    let entry = byProd.get(row.product_id);
    if (!entry) {
      entry = { days: new Map(), name: prod.name, unit: prod.unit };
      byProd.set(row.product_id, entry);
    }
    entry.days.set(row.date, (entry.days.get(row.date) || 0) + row.qty);
  }

  const todayMs = Date.now();
  const due: Array<{ pid: number; name: string; unit: UnitType; why: string; qty: number; photo: string | null; times: number; cycle: number; since: number; overdue: number }> = [];
  const regs: Array<{ pid: number; name: string; unit: UnitType; why: string; qty: number; photo: string | null; times: number; cycle: number; since: number }> = [];

  for (const [pid, entry] of byProd.entries()) {
    if (inBasket.has(pid)) continue;
    const dates = [...entry.days.keys()].sort();
    const times = dates.length;
    const lastMs = Date.parse(dates[dates.length - 1]);
    const since = Math.max(0, Math.floor((todayMs - lastMs) / 86400_000));
    const qtys = [...entry.days.values()].sort((a, b) => a - b);
    const qty = qtys[Math.floor(qtys.length / 2)] || 1;
    const photo = ws.products.get(pid)?.photo ?? null;

    let cycle = 7;
    if (dates.length >= 2) {
      const firstMs = Date.parse(dates[0]);
      cycle = Math.max(1, Math.round((lastMs - firstMs) / 86400_000 / (dates.length - 1)));
    }
    if (times >= 2 && since >= cycle) {
      due.push({ pid, name: entry.name, unit: entry.unit, why: 'пора докупить', qty, photo, times, cycle, since, overdue: since / cycle });
    } else if (times >= 1) {
      regs.push({ pid, name: entry.name, unit: entry.unit, why: 'часто берёте', qty, photo, times, cycle, since });
    }
  }

  due.sort((a, b) => b.overdue - a.overdue);
  regs.sort((a, b) => b.times - a.times);

  const out: Array<{ pid: number; name: string; unit: UnitType; why: string; qty: number; photo: string | null; times: number; cycle: number; since: number }> = [];
  const seen = new Set<number>();
  for (const item of [...due, ...regs]) {
    if (seen.has(item.pid)) continue;
    seen.add(item.pid);
    out.push(item);
    if (out.length >= limit) break;
  }
  return out;
}

export function buildBasketLines(ws: Workspace, basketId: number): {
  lines: BasketLine[];
  baseline: number;
  baselineLines: Record<number, number>;
  fromReceipts: Set<number>;
} {
  const items = getBasketItems(ws, basketId);
  const receiptPriceByProduct = new Map<number, number>();
  for (const row of ws.history) {
    if (row.product_id && row.unit_price > 0 && !receiptPriceByProduct.has(row.product_id)) {
      receiptPriceByProduct.set(row.product_id, row.unit_price);
    }
  }

  const lines: BasketLine[] = [];
  const baselineLines: Record<number, number> = {};
  const fromReceipts = new Set<number>();
  let baseline = 0;

  for (const item of items) {
    const storePrices = ws.prices.get(item.product_id) || new Map<string, StoreProductPrice>();
    const prices: Record<string, number> = {};
    const inStock: Record<string, boolean> = {};
    const notes: Record<string, string | null> = {};
    const adjust: Record<string, number> = {};

    for (const [code, snap] of storePrices.entries()) {
      const value = Math.round(snap.price * item.qty * 100) / 100;
      prices[code] = value;
      inStock[code] = snap.in_stock;
      notes[code] = null;
      adjust[code] = 0;
    }

    const rPrice = receiptPriceByProduct.get(item.product_id);
    let baseItem = 0;
    if (rPrice !== undefined) {
      baseItem = Math.round(rPrice * item.qty * 100) / 100;
      fromReceipts.add(item.product_id);
    } else {
      const vals = Object.values(prices);
      baseItem = vals.length ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100) / 100 : 0;
    }
    baselineLines[item.product_id] = baseItem;
    baseline += baseItem;

    lines.push({
      product_id: item.product_id,
      name: item.name,
      title: item.title,
      unit: item.unit,
      qty: item.qty,
      prices,
      in_stock: inStock,
      notes,
      adjust,
    });
  }

  return {
    lines,
    baseline: Math.round(baseline * 100) / 100,
    baselineLines,
    fromReceipts,
  };
}

export interface CalculationBundle {
  variants: Variant[];
  places: Variant[];
  baseline: number;
  positions: number;
  fromReceipts: Set<number>;
  when: string;
}

export function calculateBasket(ws: Workspace, basketId: number): CalculationBundle {
  const { lines, baseline, baselineLines, fromReceipts } = buildBasketLines(ws, basketId);
  const positions = lines.length;
  const offersMap = new Map<number, Offer[]>();
  for (const o of ws.offers) {
    const arr = offersMap.get(o.store_id) || [];
    arr.push(o);
    offersMap.set(o.store_id, arr);
  }
  const handover = handoverByStore();

  const variants = optimize({
    lines,
    stores: SEED_STORES,
    offers: offersMap,
    cards: ws.cards,
    baseline,
    topN: 3,
    maxStores: 2,
    handover,
    baselineLines,
  });

  const singles = optimize({
    lines,
    stores: SEED_STORES,
    offers: offersMap,
    cards: ws.cards,
    baseline,
    topN: SEED_STORES.length,
    maxStores: 1,
    handover,
    baselineLines,
  });

  const seen = new Set(variants.map((v) => v.stores.map((s) => s.store_code).sort().join('+')));
  const places = [...variants];
  for (const s of singles) {
    const key = s.stores.map((x) => x.store_code).sort().join('+');
    if (seen.has(key) || s.missing_products.length >= positions) continue;
    seen.add(key);
    places.push(s);
  }
  places.sort((a, b) => a.missing_products.length - b.missing_products.length || a.total - b.total);

  const now = new Date();
  const when = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

  return {
    variants,
    places,
    baseline,
    positions,
    fromReceipts,
    when,
  };
}

export interface BoardPlace {
  n: number;
  title: string;
  total: number;
  codes: string[];
  counts: Array<[string, number]>;
  lack: number;
  of: number;
  full: boolean;
  best: boolean;
  on: boolean;
  rank: number;
  diff: number;
}

export function buildBoard(
  ws: Workspace,
  basketId: number,
  picked?: number | null
): {
  bundle: CalculationBundle;
  places: BoardPlace[];
  morePlaces: BoardPlace[];
  chosenVariant: Variant | null;
  bestIndex: number;
  onIndex: number;
  pricesByPid: Record<number, number>;
} {
  const bundle = calculateBasket(ws, basketId);
  const every = bundle.places;
  if (!every.length) {
    return {
      bundle,
      places: [],
      morePlaces: [],
      chosenVariant: null,
      bestIndex: 0,
      onIndex: 0,
      pricesByPid: {},
    };
  }

  const { best } = chooseBestVariant(bundle.variants.length ? bundle.variants : every, null);
  const bestIndex = Math.max(0, every.indexOf(best));
  const onIndex =
    picked !== null && picked !== undefined && picked >= 0 && picked < every.length
      ? picked
      : bestIndex;

  const rawPlaces: BoardPlace[] = every.map((variant, n) => {
    const lack = variant.missing_products.length;
    return {
      n,
      title: variant.title,
      total: variant.total,
      codes: variant.stores.map((s) => s.store_code),
      counts: variant.stores.map((s) => [s.store_code, s.lines.length]),
      lack,
      of: bundle.positions,
      full: lack === 0,
      best: n === bestIndex,
      on: n === onIndex,
      rank: 0,
      diff: 0,
    };
  });

  const fewest = Math.min(...rawPlaces.map((p) => p.lack));
  const beaten = (p: BoardPlace) =>
    rawPlaces.some(
      (q) =>
        q !== p &&
        q.lack <= p.lack &&
        q.total <= p.total &&
        (q.lack !== p.lack || q.total !== p.total)
    );

  const shownRaw = rawPlaces.filter(
    (p) => p.on || p.best || (p.lack <= fewest + 2 && !beaten(p))
  );
  const hiddenRaw = rawPlaces.filter((p) => !shownRaw.includes(p));

  const bestPlace = shownRaw.find((p) => p.best) || null;
  const onPlace = shownRaw.find((p) => p.on) || null;
  const orderedShown: BoardPlace[] = [];
  if (bestPlace) orderedShown.push(bestPlace);
  if (onPlace && onPlace !== bestPlace) orderedShown.push(onPlace);
  for (const p of shownRaw) {
    if (!orderedShown.includes(p)) orderedShown.push(p);
  }

  const places = orderedShown.slice(0, 3);
  const morePlaces = [...orderedShown.slice(3), ...hiddenRaw];
  const bestTotal = bestPlace ? bestPlace.total : 0;

  let rank = 1;
  for (const p of places) {
    p.rank = rank++;
    p.diff = p.best ? 0 : Math.round(p.total - bestTotal);
  }
  for (const p of morePlaces) {
    p.rank = rank++;
    p.diff = Math.round(p.total - bestTotal);
  }

  const pricesByPid: Record<number, number> = {};
  const bestVar = every[bestIndex];
  if (bestVar) {
    for (const s of bestVar.stores) {
      for (const ln of s.lines) {
        pricesByPid[ln.product_id] = ln.price;
      }
    }
  }

  return {
    bundle,
    places,
    morePlaces,
    chosenVariant: every[onIndex],
    bestIndex,
    onIndex,
    pricesByPid,
  };
}

export function buildOrdersList(ws: Workspace): Array<{
  key: string;
  date: string;
  day: string;
  store_code: string;
  store_name: string;
  source: string;
  total: number;
  lines: Array<{ product_id: number | null; name: string; qty: number; total: number; photo: string | null }>;
}> {
  const sources = new Map(ws.receipts.map((r) => [r.key, r.source]));
  const map = new Map<string, {
    key: string;
    date: string;
    day: string;
    store_code: string;
    store_name: string;
    source: string;
    total: number;
    lines: Array<{ product_id: number | null; name: string; qty: number; total: number; photo: string | null }>;
  }>();

  for (const row of ws.history) {
    if (!ORDERABLE_STORES.has(row.store_code)) continue;
    const key = row.receipt_key || `${row.date}|${row.store_code}`;
    let order = map.get(key);
    if (!order) {
      const src = sources.get(row.receipt_key) || '';
      order = {
        key,
        date: row.date,
        day: humanDay(row.date),
        store_code: row.store_code,
        store_name: row.store_name,
        source: ['lkdr', 'fns', 'cabinet'].includes(src.toLowerCase())
          ? 'Чек из ФНС'
          : 'Чек добавлен вручную',
        total: 0,
        lines: [],
      };
      map.set(key, order);
    }
    const prod = row.product_id ? ws.products.get(row.product_id) : undefined;
    order.lines.push({
      product_id: row.product_id,
      name: prod?.name || row.raw_name,
      qty: row.qty,
      total: row.total,
      photo: prod?.photo ?? null,
    });
    order.total = Math.round((order.total + row.total) * 100) / 100;
  }

  return [...map.values()].sort((a, b) => b.date.localeCompare(a.date));
}

export function groupByMonth<T extends { date: string }>(items: T[]): Array<[string, T[]]> {
  const groups: Array<[string, T[]]> = [];
  for (const item of items) {
    const title = monthTitle(item.date);
    if (!groups.length || groups[groups.length - 1][0] !== title) {
      groups.push([title, []]);
    }
    groups[groups.length - 1][1].push(item);
  }
  return groups;
}

export function buildStoreLink(storeCode: string, lines: Array<{ product_id: number; qty: number; product_name: string }>): string {
  const q = lines.slice(0, 5).map((l) => l.product_name).join(', ');
  const tmpl = SEARCH_URL_TEMPLATES[storeCode];
  if (tmpl) {
    return tmpl.replace('{q}', encodeURIComponent(q || 'продукты'));
  }
  return `https://${storeCode}.ru/`;
}

export function buildStorePart(
  ws: Workspace,
  breakdown: {
    store_code: string;
    store_name: string;
    total: number;
    lines: Array<{ product_id: number; product_name: string; qty: number; unit: UnitType }>;
  }
) {
  const code = breakdown.store_code;
  const kind = KIND_BY_STORE[code] || 'list';
  const byLink = kind === 'link';
  const conn = ws.connections.get(code);
  const needsLogin = byLink ? false : !(conn && conn.connected);
  const again = Boolean(conn && conn.again);
  const linesList = breakdown.lines.map((ln) => ({
    product_id: ln.product_id,
    name: ln.product_name,
    qty: ln.qty,
    unit: ln.unit,
  }));
  const thumbs = breakdown.lines.slice(0, 5).map((ln) => ({
    name: ln.product_name,
    photo: ws.products.get(ln.product_id)?.photo || '',
  }));

  return {
    code,
    name: breakdown.store_name,
    where: where(code, breakdown.store_name),
    whom: whom(code, breakdown.store_name),
    total: breakdown.total,
    count: breakdown.lines.length,
    by_link: byLink,
    thumbs,
    more: Math.max(0, breakdown.lines.length - 5),
    lines_json: JSON.stringify(linesList),
    needs_login: needsLogin,
    again,
  };
}

export function buildRunsPayload(ws: Workspace, basket: Basket | null, isPhoneDevice: boolean) {
  const basketId = basket ? basket.id : null;
  const items = basketId ? getBasketItems(ws, basketId) : [];
  const stamp = items.map((i) => `${i.product_id}:${i.qty}`).sort().join('|');
  const rows: Array<{
    code: string;
    name: string;
    state: string;
    words: string;
    now?: string;
    left?: string;
    note?: string;
    gone?: number;
    extra_note?: string;
    login?: string;
    total?: { sum: number; network: boolean } | null;
  }> = [];
  const idle: string[] = [];
  const chains: Array<{ code: string; login: string; cookies: string[]; sent: boolean }> = [];

  for (const code of PHONE_CHAINS) {
    const store = STORE_BY_CODE.get(code);
    if (!store) continue;
    const conn = ws.connections.get(code);
    const loginState = conn && conn.connected ? '' : conn?.again ? 'again' : 'connect';
    chains.push({ code, login: loginState, cookies: [], sent: false });
    if (loginState) {
      idle.push(store.name);
      continue;
    }
    rows.push({
      code,
      name: store.name,
      state: 'wait',
      words: isPhoneDevice ? 'соберу, пока открыта корзина' : 'соберёт приложение на телефоне',
      total: null,
    });
  }

  return {
    basket: basketId,
    stamp,
    rows,
    idle,
    going: false,
    collect: { basket: basketId, stamp, chains },
  };
}

export function buildDesktopBasketView(ws: Workspace, basketId: number) {
  const items = getBasketItems(ws, basketId);
  const { lines } = buildBasketLines(ws, basketId);
  const lineByPid = new Map(lines.map((l) => [l.product_id, l]));
  const totals: Record<string, number> = {};
  for (const s of SEED_STORES) totals[s.code] = 0;

  const viewItems = items.map((it) => {
    const line = lineByPid.get(it.product_id);
    const storePrices: Array<{
      code: string;
      name: string;
      value: number;
      fair: number;
      in_stock: boolean;
      best: boolean;
    }> = [];

    if (line) {
      const avail = SEED_STORES.filter((s) => line.prices[s.code] !== undefined);
      const minAvail = avail
        .filter((s) => line.in_stock[s.code] !== false)
        .map((s) => line.prices[s.code] + (line.adjust[s.code] || 0));
      const bestVal = minAvail.length ? Math.min(...minAvail) : null;

      for (const s of avail) {
        const val = line.prices[s.code];
        const inStock = line.in_stock[s.code] !== false;
        const fair = val + (line.adjust[s.code] || 0);
        if (inStock) totals[s.code] += val;
        storePrices.push({
          code: s.code,
          name: s.name,
          value: val,
          fair,
          in_stock: inStock,
          best: bestVal !== null && inStock && Math.abs(fair - bestVal) < 0.005,
        });
      }
    }

    const inStockSorted = storePrices.filter((p) => p.in_stock).sort((a, b) => a.fair - b.fair);
    const best = inStockSorted.length
      ? {
          code: inStockSorted[0].code,
          store: inStockSorted[0].name,
          value: inStockSorted[0].value,
          diff: inStockSorted.length > 1 ? Math.round((inStockSorted[1].fair - inStockSorted[0].fair) * 100) / 100 : 0,
          count: inStockSorted.length,
        }
      : null;

    return {
      pid: it.product_id,
      name: it.name,
      qty: it.qty,
      unit: it.unit,
      prices: storePrices,
      best,
    };
  });

  const priced = viewItems.filter((r) => r.best).length;
  const approxTotal = Math.round(
    viewItems.reduce((acc, r) => acc + (r.best ? r.best.value : 0), 0) * 100
  ) / 100;

  const storeTotals = SEED_STORES.filter((s) => totals[s.code] > 0)
    .map((s) => ({ code: s.code, name: s.name, value: Math.round(totals[s.code] * 100) / 100 }))
    .sort((a, b) => a.value - b.value);

  return {
    items: viewItems,
    approx: {
      total: approxTotal,
      priced,
      blind: viewItems.length - priced,
      count: viewItems.length,
    },
    totals: storeTotals,
  };
}

export function importManualReceipt(
  ws: Workspace,
  storeCode: string,
  rawText: string
): { imported: number; total: number; error?: string } {
  const store = STORE_BY_CODE.get(storeCode) || SEED_STORES[2];
  const lines = rawText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) {
    return { imported: 0, total: 0, error: 'Вставьте хотя бы одну строку чека.' };
  }

  const today = new Date().toISOString().slice(0, 10);
  const key = `manual:${Date.now()}:${store.code}`;
  let total = 0;
  let imported = 0;

  for (const line of lines) {
    // Supported formats: "Название; qty; price" or "Название — 129.90" or "Название"
    const parts = line.split(/[;|]/).map((s) => s.trim());
    const name = parts[0];
    if (!name) continue;
    const qty = parts[1] ? Math.max(0.01, Number(parts[1].replace(',', '.')) || 1) : 1;
    const price = parts[2] ? Math.max(0, Number(parts[2].replace(',', '.')) || 0) : 100;

    let prod = [...ws.products.values()].find(
      (p) => p.name.toLowerCase() === name.toLowerCase()
    );
    if (!prod) {
      const id = ws.nextProductId++;
      prod = {
        id,
        name,
        brand: null,
        weight_g: null,
        unit: 'pcs',
        category: 'Из чека',
        active: true,
      };
      ws.products.set(id, prod);
    }

    const lineTotal = Math.round(qty * price * 100) / 100;
    total += lineTotal;
    ws.history.unshift({
      id: ws.history.length + 1,
      date: today,
      store_id: store.id,
      store_code: store.code,
      store_name: store.name,
      product_id: prod.id,
      raw_name: name,
      qty,
      unit_price: price,
      total: lineTotal,
      receipt_key: key,
    });
    imported++;
  }

  if (imported > 0) {
    ws.receipts.unshift({
      key,
      date: today,
      store: store.name,
      total: Math.round(total * 100) / 100,
      rows: imported,
      source: 'paste',
    });
  }

  return { imported, total: Math.round(total * 100) / 100 };
}

export const SPLIT_THRESHOLD = SPLIT_THRESHOLD_RUB;
export const RUB_FORMAT = rub;
