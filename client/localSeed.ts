import demoBasketRaw from '../data/demo_basket.csv?raw';
import fallbackPricesRaw from '../data/fallback_prices.csv?raw';
import seedOffersRaw from '../data/seed_offers.csv?raw';
import seedProductsRaw from '../data/seed_products.csv?raw';
import seedReceiptRaw from '../data/seed_receipt.csv?raw';
import { humanDay, PHONE_CHAINS } from '../src/shared/format';
import type {
  Basket,
  BasketItem,
  Card,
  Offer,
  Product,
  PurchaseRow,
  ReceiptRecord,
  Store,
  StoreConnection,
  StoreProductPrice,
} from '../src/types';
import type { WorkspaceSnapshot } from './types';

export const DEMO_PHONE = 'demo';

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

const STORE_BY_CODE = new Map(SEED_STORES.map((s) => [s.code, s]));

export function normalizePhoneLocal(raw: string | null | undefined): string | null {
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

export function resolveLoginLocal(raw: string | null | undefined): [string | null, string | null] {
  const trimmed = (raw || '').trim();
  if (!trimmed) return [null, 'Введите номер телефона'];
  if (trimmed.toLowerCase() === DEMO_PHONE) {
    return [null, 'Чтобы посмотреть пример, нажмите «Посмотреть демо-корзину»'];
  }
  const normalized = normalizePhoneLocal(trimmed);
  if (!normalized) {
    return [null, 'Не похож на российский номер: нужно 10 или 11 цифр, например +7 999 123-45-67'];
  }
  return [normalized, null];
}

export function formatPhoneDisplayLocal(phone: string): string {
  if (phone === DEMO_PHONE) return 'Демо';
  if (phone.length === 11 && phone.startsWith('7')) {
    return `+7 ${phone.slice(1, 4)} ${phone.slice(4, 7)}-${phone.slice(7, 9)}-${phone.slice(9, 11)}`;
  }
  return phone;
}

function parseCsvText(rawText: string): Array<Record<string, string>> {
  const raw = rawText.replace(/^\uFEFF/, '');
  const lines = raw.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (!lines.length) return [];

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

export function buildLocalWorkspace(phone: string): WorkspaceSnapshot {
  const isDemo = phone === DEMO_PHONE;
  const now = new Date();
  const nowIso = now.toISOString().slice(0, 19);

  const seedProducts = parseCsvText(seedProductsRaw);
  const demoBasket = parseCsvText(demoBasketRaw);
  const fallbackPrices = parseCsvText(fallbackPricesRaw);
  const seedOffers = parseCsvText(seedOffersRaw);
  const seedReceipt = parseCsvText(seedReceiptRaw);

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
    const title = row.name;
    if (!title || seenNames.has(title.toLowerCase())) continue;
    seenNames.add(title.toLowerCase());
    products.push({
      id: pid++,
      name: title,
      brand: null,
      weight_g: null,
      unit: /весов/i.test(title) ? 'kg' : 'pcs',
      category: 'Из каталога',
      active: true,
    });
  }

  const prices: Record<string, Record<string, StoreProductPrice>> = {};
  const matchProduct = (rawName: string): Product | null => {
    const low = rawName.toLowerCase();
    for (const p of products) {
      const words = p.name.toLowerCase().split(/\s+/).filter((w) => w.length >= 4);
      if (words.length > 0 && words.slice(0, Math.min(2, words.length)).every((w) => low.includes(w))) {
        return p;
      }
    }
    return null;
  };

  for (const r of fallbackPrices) {
    const prod = matchProduct(r.name);
    if (!prod) continue;
    const key = String(prod.id);
    if (!prices[key]) prices[key] = {};
    const price = Number(r.price);
    prices[key][r.store_code] = {
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
    };
    if (r.store_code === 'pyaterochka' && !prices[key].lenta) {
      prices[key].lenta = {
        store_code: 'lenta',
        sku: r.sku.replace('pyaterochka-', 'lenta-'),
        raw_name: r.name,
        price: Math.round(price * 0.96 * 100) / 100,
        unit: r.unit === 'kg' ? 'kg' : 'pcs',
        weight_g: r.weight_g ? Number(r.weight_g) : null,
        price_per_kg: r.price_per_kg ? Math.round(Number(r.price_per_kg) * 0.96 * 100) / 100 : null,
        in_stock: true,
        fetched_at: nowIso,
        source: 'fallback',
      };
    }
  }

  const connections: Record<string, StoreConnection> = {};
  for (const code of PHONE_CHAINS) {
    connections[code] = {
      store_code: code,
      connected: isDemo && (code === 'vkusvill' || code === 'lenta' || code === 'magnit'),
      again: false,
      last: null,
    };
  }

  const cards: Card[] = [];
  const offers: Offer[] = [];
  const baskets: Basket[] = [];
  const basketItems: Record<string, BasketItem[]> = {};
  const history: PurchaseRow[] = [];
  const receipts: ReceiptRecord[] = [];
  let nextBasketId = 1;

  if (isDemo) {
    const validFrom = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
    const validTo = new Date(now.getFullYear(), now.getMonth() + 2, 0).toISOString().slice(0, 10);
    const cardByKey = new Map<string, Card>();
    let cardId = 1;
    let offerId = 1;

    for (const o of seedOffers) {
      if (!o.bank || !o.card || !o.store_code) continue;
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
        percent: Number(o.percent || 0),
        cap_rub: Number(o.cap_rub || 0),
        min_check_rub: Number(o.min_check_rub || 0),
        valid_from: validFrom,
        valid_to: validTo,
        requires_activation: o.requires_activation === '1',
        activated: o.activated !== '0',
        cap_used: Number(o.cap_used || 0),
      });
    }

    const productByName = new Map(products.map((p) => [p.name, p]));
    const receiptDate = new Date(now.getTime() - 4 * 86400_000).toISOString().slice(0, 10);
    const prevDate = new Date(now.getTime() - 18 * 86400_000).toISOString().slice(0, 10);
    const st = STORE_BY_CODE.get('pyaterochka')!;
    const vv = STORE_BY_CODE.get('vkusvill')!;
    const key1 = `demo:${receiptDate}:pyaterochka`;
    const key2 = `demo:${prevDate}:vkusvill`;

    let total1 = 0;
    let rowId = 1;
    const itemsForBasket: BasketItem[] = [];
    for (const r of seedReceipt) {
      if (!r.name) continue;
      const qty = Number(r.qty || 1);
      const unitPrice = Number(r.unit_price || 0);
      const prod = productByName.get(r.name);
      const lineTotal = Math.round(qty * unitPrice * 100) / 100;
      total1 += lineTotal;
      history.push({
        id: rowId++,
        date: receiptDate,
        store_id: st.id,
        store_code: st.code,
        store_name: st.name,
        product_id: prod ? prod.id : null,
        raw_name: r.name,
        qty,
        unit_price: unitPrice,
        total: lineTotal,
        receipt_key: key1,
      });
      if (prod) {
        itemsForBasket.push({ basket_id: 1, product_id: prod.id, qty });
      }
    }
    receipts.push({
      key: key1,
      date: receiptDate,
      store: st.name,
      total: Math.round(total1 * 100) / 100,
      rows: seedReceipt.length,
      source: 'lkdr',
    });

    let total2 = 0;
    const repeatSubset = seedReceipt.slice(0, 6);
    for (const r of repeatSubset) {
      if (!r.name) continue;
      const qty = Number(r.qty || 1);
      const unitP = Math.round(Number(r.unit_price || 0) * 0.95 * 100) / 100;
      const prod = productByName.get(r.name);
      const lineTotal = Math.round(qty * unitP * 100) / 100;
      total2 += lineTotal;
      history.push({
        id: rowId++,
        date: prevDate,
        store_id: vv.id,
        store_code: vv.code,
        store_name: vv.name,
        product_id: prod ? prod.id : null,
        raw_name: r.name,
        qty,
        unit_price: unitP,
        total: lineTotal,
        receipt_key: key2,
      });
    }
    receipts.push({
      key: key2,
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
    basketItems[String(basketId)] = itemsForBasket;
  }

  return {
    phone,
    phoneLabel: formatPhoneDisplayLocal(phone),
    isDemo,
    address: isDemo ? 'Санкт-Петербург, Невский проспект, 28' : null,
    point: isDemo ? [59.9356, 30.3259] : null,
    stores: SEED_STORES,
    products,
    nextProductId: pid,
    prices,
    cards,
    offers,
    baskets,
    nextBasketId,
    basketItems,
    history,
    receipts,
    connections,
    storeQuotes: {},
    storePerks: isDemo
      ? {
          vkusvill: {
            store_code: 'vkusvill',
            promo_label: 'Разнообразное питание',
            promo_percent: 5,
            promo_rub: 0,
            bonus_balance: 85,
            bonus_max_pct: 100,
          },
          magnit: {
            store_code: 'magnit',
            promo_label: 'Купон Магнит Плюс',
            promo_percent: 0,
            promo_rub: 50,
            bonus_balance: 140,
            bonus_max_pct: 50,
          },
          lenta: {
            store_code: 'lenta',
            promo_label: 'Карта №1',
            promo_percent: 5,
            promo_rub: 0,
            bonus_balance: 90,
            bonus_max_pct: 50,
          },
        }
      : {},
    applyBonuses: true,
    lastBasketId: baskets[0]?.id ?? null,
    repeated: null,
    fnsConnected: isDemo,
    fnsPhone: isDemo ? '+7 999 000-00-00' : null,
    fnsSyncedAt: isDemo ? now.toISOString().slice(0, 16).replace('T', ' ') : null,
    syncedAt: now.toISOString(),
  };
}

export function applyManualReceiptLocal(
  prev: WorkspaceSnapshot,
  storeCode: string,
  rawText: string
): { next: WorkspaceSnapshot; imported: number; total: number } {
  const lines = rawText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) {
    throw new Error('Вставьте хотя бы одну строку чека.');
  }

  const st = prev.stores.find((s) => s.code === storeCode) || prev.stores[2];
  const today = new Date().toISOString().slice(0, 10);
  const key = `local:${Date.now()}:${st.code}`;
  const products = [...prev.products];
  let nextPid = prev.nextProductId;
  const history = [...prev.history];
  let total = 0;
  let imported = 0;

  for (const line of lines) {
    const parts = line.split(/[;|]/).map((s) => s.trim());
    const name = parts[0];
    if (!name) continue;
    const qty = parts[1] ? Math.max(0.01, Number(parts[1].replace(',', '.')) || 1) : 1;
    const price = parts[2] ? Math.max(0, Number(parts[2].replace(',', '.')) || 0) : 100;
    let prod = products.find((p) => p.name.toLowerCase() === name.toLowerCase());
    if (!prod) {
      prod = {
        id: nextPid++,
        name,
        brand: null,
        weight_g: null,
        unit: 'pcs',
        category: 'Из чека',
        active: true,
      };
      products.push(prod);
    }
    const lineTotal = Math.round(qty * price * 100) / 100;
    total += lineTotal;
    history.unshift({
      id: history.length + 1,
      date: today,
      store_id: st.id,
      store_code: st.code,
      store_name: st.name,
      product_id: prod.id,
      raw_name: name,
      qty,
      unit_price: price,
      total: lineTotal,
      receipt_key: key,
    });
    imported++;
  }

  if (imported === 0) {
    throw new Error('Не удалось распознать товары в тексте чека.');
  }

  const roundedTotal = Math.round(total * 100) / 100;
  return {
    imported,
    total: roundedTotal,
    next: {
      ...prev,
      products,
      nextProductId: nextPid,
      history,
      receipts: [
        {
          key,
          date: today,
          store: st.name,
          total: roundedTotal,
          rows: imported,
          source: 'paste',
        },
        ...prev.receipts,
      ],
    },
  };
}
