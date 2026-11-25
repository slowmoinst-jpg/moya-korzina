import {
  chooseBestVariant,
  handoverByStore,
  KIND_BY_STORE,
  optimize,
} from '../src/optimizer';
import {
  humanDay,
  monthTitle,
  ORDERABLE_STORES,
  where,
  whom,
} from '../src/shared/format';
import type {
  Basket,
  BasketLine,
  Offer,
  Product,
  StoreBreakdown,
  UnitType,
  Variant,
} from '../src/types';
import type {
  BoardPlace,
  ClientBoard,
  HabitItem,
  OrderEntry,
  SearchResultItem,
  WorkspaceSnapshot,
} from './types';

export function getActiveBasket(ws: WorkspaceSnapshot): Basket | null {
  if (!ws.baskets.length) return null;
  if (ws.lastBasketId !== null) {
    const found = ws.baskets.find((b) => b.id === ws.lastBasketId);
    if (found) return found;
  }
  return ws.baskets[0];
}

export interface BasketItemView {
  product_id: number;
  name: string;
  unit: UnitType;
  weight_g: number | null;
  qty: number;
  photo: string | null;
}

export function computeBasketStamp(
  basketId: number | null,
  items: Array<{ product_id: number; qty: number }>,
  address: string | null
): string {
  if (basketId === null || !items.length) return '';
  const parts = items
    .map((it) => `${it.product_id}:${it.qty}`)
    .sort()
    .join(',');
  return `${basketId}|${parts}|${(address || '').trim().toLowerCase()}`;
}

export function getActiveBasketItems(ws: WorkspaceSnapshot, basketId: number | null): BasketItemView[] {
  if (basketId === null) return [];
  const raw = ws.basketItems[String(basketId)] || [];
  const byId = new Map(ws.products.map((p) => [p.id, p]));
  return raw.map((it) => {
    const prod = byId.get(it.product_id);
    return {
      product_id: it.product_id,
      name: prod?.name || 'Товар',
      unit: prod?.unit || 'pcs',
      weight_g: prod?.weight_g ?? null,
      qty: it.qty,
      photo: prod?.photo ?? null,
    };
  });
}

export function buildBoardClient(
  ws: WorkspaceSnapshot,
  basketId: number | null,
  pickedIndex: number | null
): ClientBoard {
  const now = new Date();
  const when = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

  if (basketId === null) {
    return {
      places: [],
      morePlaces: [],
      allVariants: [],
      chosenVariant: null,
      bestIndex: 0,
      onIndex: 0,
      baseline: 0,
      positions: 0,
      pricesByPid: {},
      when,
    };
  }

  const items = getActiveBasketItems(ws, basketId);
  if (!items.length) {
    return {
      places: [],
      morePlaces: [],
      allVariants: [],
      chosenVariant: null,
      bestIndex: 0,
      onIndex: 0,
      baseline: 0,
      positions: 0,
      pricesByPid: {},
      when,
    };
  }

  const receiptPriceByProduct = new Map<number, number>();
  for (const row of ws.history) {
    if (row.product_id && row.unit_price > 0 && !receiptPriceByProduct.has(row.product_id)) {
      receiptPriceByProduct.set(row.product_id, row.unit_price);
    }
  }

  const lines: BasketLine[] = [];
  const baselineLines: Record<number, number> = {};
  let baseline = 0;

  for (const item of items) {
    const storePrices = ws.prices[String(item.product_id)] || {};
    const prices: Record<string, number> = {};
    const inStock: Record<string, boolean> = {};
    const notes: Record<string, string | null> = {};
    const adjust: Record<string, number> = {};

    for (const [code, snap] of Object.entries(storePrices)) {
      prices[code] = Math.round(snap.price * item.qty * 100) / 100;
      inStock[code] = snap.in_stock;
      notes[code] = null;
      adjust[code] = 0;
    }

    const rPrice = receiptPriceByProduct.get(item.product_id);
    let baseItem = 0;
    if (rPrice !== undefined) {
      baseItem = Math.round(rPrice * item.qty * 100) / 100;
    } else {
      const vals = Object.values(prices);
      baseItem = vals.length
        ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100) / 100
        : 0;
    }
    baselineLines[item.product_id] = baseItem;
    baseline += baseItem;

    lines.push({
      product_id: item.product_id,
      name: item.name,
      title: item.name,
      unit: item.unit,
      qty: item.qty,
      prices,
      in_stock: inStock,
      notes,
      adjust,
    });
  }

  baseline = Math.round(baseline * 100) / 100;
  const positions = lines.length;

  const offersMap = new Map<number, Offer[]>();
  for (const o of ws.offers) {
    const arr = offersMap.get(o.store_id) || [];
    arr.push(o);
    offersMap.set(o.store_id, arr);
  }
  const handover = handoverByStore();

  const applyBonuses = ws.applyBonuses !== false;
  const variants = optimize({
    lines,
    stores: ws.stores,
    offers: offersMap,
    cards: ws.cards,
    baseline,
    topN: 3,
    maxStores: 2,
    handover,
    baselineLines,
    storeQuotes: ws.storeQuotes ?? null,
    storePerks: ws.storePerks ?? null,
    applyBonuses,
  });

  const singles = optimize({
    lines,
    stores: ws.stores,
    offers: offersMap,
    cards: ws.cards,
    baseline,
    topN: ws.stores.length,
    maxStores: 1,
    handover,
    baselineLines,
    storeQuotes: ws.storeQuotes ?? null,
    storePerks: ws.storePerks ?? null,
    applyBonuses,
  });

  const seen = new Set(variants.map((v) => v.stores.map((s) => s.store_code).sort().join('+')));
  const every: Variant[] = [...variants];
  for (const s of singles) {
    const key = s.stores.map((x) => x.store_code).sort().join('+');
    if (seen.has(key) || s.missing_products.length >= positions) continue;
    seen.add(key);
    every.push(s);
  }
  // Sort variants by completeness (fewest missing items first), then deliverability, then actual total price
  every.sort((a, b) => {
    if (a.missing_products.length !== b.missing_products.length) {
      return a.missing_products.length - b.missing_products.length;
    }
    const aMin = a.stores.filter((s) => s.below_min_order).length;
    const bMin = b.stores.filter((s) => s.below_min_order).length;
    if (aMin !== bMin) return aMin - bMin;
    return a.total - b.total;
  });

  if (!every.length) {
    return {
      places: [],
      morePlaces: [],
      allVariants: [],
      chosenVariant: null,
      bestIndex: 0,
      onIndex: 0,
      baseline,
      positions,
      pricesByPid: {},
      when,
    };
  }

  const { best } = chooseBestVariant(variants.length ? variants : every, null);
  const bestIndex = Math.max(0, every.indexOf(best));
  const onIndex =
    pickedIndex !== null && pickedIndex >= 0 && pickedIndex < every.length
      ? pickedIndex
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
      of: positions,
      full: lack === 0,
      best: n === bestIndex,
      on: n === onIndex,
      rank: 0,
      diff: 0,
    };
  });

  const fewest = Math.min(...rawPlaces.map((p) => p.lack));
  // Only defer strictly inferior *incomplete* variants (fewer items AND more expensive than a fuller option)
  const strictlyInferiorIncomplete = (p: BoardPlace) =>
    p.lack > fewest &&
    rawPlaces.some((q) => q !== p && q.lack < p.lack && q.total <= p.total);

  const primaryRaw = rawPlaces.filter(
    (p) => p.on || p.best || (p.lack <= fewest + 2 && !strictlyInferiorIncomplete(p))
  );
  const secondaryRaw = rawPlaces.filter((p) => !primaryRaw.includes(p));

  const bestPlace = primaryRaw.find((p) => p.best) || null;
  const onPlace = primaryRaw.find((p) => p.on) || null;
  const orderedShown: BoardPlace[] = [];
  if (bestPlace) orderedShown.push(bestPlace);
  if (onPlace && onPlace !== bestPlace) orderedShown.push(onPlace);
  for (const p of primaryRaw) {
    if (!orderedShown.includes(p)) orderedShown.push(p);
  }

  const places = orderedShown.slice(0, 3);
  const morePlaces = [...orderedShown.slice(3), ...secondaryRaw];
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
    places,
    morePlaces,
    allVariants: every,
    chosenVariant: every[onIndex] || null,
    bestIndex,
    onIndex,
    baseline,
    positions,
    pricesByPid,
    when,
  };
}

export function getHabitsClient(
  ws: WorkspaceSnapshot,
  basketId: number | null,
  limit = 10
): HabitItem[] {
  const inBasket = new Set(
    basketId !== null ? (ws.basketItems[String(basketId)] || []).map((i) => i.product_id) : []
  );
  const byId = new Map(ws.products.map((p) => [p.id, p]));
  const byProd = new Map<number, { days: Map<string, number>; prod: Product }>();

  for (const row of ws.history) {
    if (!row.product_id) continue;
    const prod = byId.get(row.product_id);
    if (!prod) continue;
    let entry = byProd.get(row.product_id);
    if (!entry) {
      entry = { days: new Map(), prod };
      byProd.set(row.product_id, entry);
    }
    entry.days.set(row.date, (entry.days.get(row.date) || 0) + row.qty);
  }

  const todayMs = Date.now();
  const due: Array<HabitItem & { overdue: number }> = [];
  const regs: HabitItem[] = [];

  for (const [pid, entry] of byProd.entries()) {
    if (inBasket.has(pid)) continue;
    const dates = [...entry.days.keys()].sort();
    const times = dates.length;
    const lastMs = Date.parse(dates[dates.length - 1]);
    const since = Math.max(0, Math.floor((todayMs - lastMs) / 86400_000));
    const qtys = [...entry.days.values()].sort((a, b) => a - b);
    const qty = qtys[Math.floor(qtys.length / 2)] || 1;
    let cycle = 7;
    if (dates.length >= 2) {
      const firstMs = Date.parse(dates[0]);
      cycle = Math.max(1, Math.round((lastMs - firstMs) / 86400_000 / (dates.length - 1)));
    }
    if (times >= 2 && since >= cycle) {
      due.push({
        pid,
        name: entry.prod.name,
        unit: entry.prod.unit,
        why: 'пора докупить',
        qty,
        photo: entry.prod.photo ?? null,
        times,
        cycle,
        since,
        overdue: since / cycle,
      });
    } else if (times >= 1) {
      regs.push({
        pid,
        name: entry.prod.name,
        unit: entry.prod.unit,
        why: 'часто берёте',
        qty,
        photo: entry.prod.photo ?? null,
        times,
        cycle,
        since,
      });
    }
  }

  due.sort((a, b) => b.overdue - a.overdue);
  regs.sort((a, b) => b.times - a.times);

  const out: HabitItem[] = [];
  const seen = new Set<number>();
  for (const item of [...due, ...regs]) {
    if (seen.has(item.pid)) continue;
    seen.add(item.pid);
    out.push(item);
    if (out.length >= limit) break;
  }

  // If user has no history yet, surface popular priced catalog items so the shelf is immediately helpful
  if (!out.length) {
    for (const p of ws.products) {
      if (!p.active || inBasket.has(p.id)) continue;
      const pMap = ws.prices[String(p.id)];
      if (!pMap || Object.keys(pMap).length === 0) continue;
      out.push({
        pid: p.id,
        name: p.name,
        unit: p.unit,
        why: 'популярное',
        qty: 1,
        photo: p.photo ?? null,
        times: 0,
        cycle: 7,
        since: 0,
      });
      if (out.length >= Math.min(limit, 8)) break;
    }
  }

  return out;
}

export function buildOrdersClient(ws: WorkspaceSnapshot): OrderEntry[] {
  const sources = new Map(ws.receipts.map((r) => [r.key, r.source]));
  const byId = new Map(ws.products.map((p) => [p.id, p]));
  const map = new Map<string, OrderEntry>();

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
    const prod = row.product_id ? byId.get(row.product_id) : undefined;
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

export function groupOrdersByMonth(orders: OrderEntry[]): Array<[string, OrderEntry[]]> {
  const groups: Array<[string, OrderEntry[]]> = [];
  for (const o of orders) {
    const title = monthTitle(o.date);
    if (!groups.length || groups[groups.length - 1][0] !== title) {
      groups.push([title, []]);
    }
    groups[groups.length - 1][1].push(o);
  }
  return groups;
}

export function localSearchProducts(ws: WorkspaceSnapshot, query: string, limit = 25): SearchResultItem[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const tokens = q.split(/\s+/).filter(Boolean);
  const out: SearchResultItem[] = [];
  for (const p of ws.products) {
    if (!p.active) continue;
    const hay = `${p.name} ${p.brand || ''} ${p.category || ''}`.toLowerCase();
    if (!tokens.every((t) => hay.includes(t))) continue;
    const prices = ws.prices[String(p.id)] || {};
    const chainsCount = Object.keys(prices).length;
    const parts: string[] = [];
    if (p.weight_g) parts.push(`${Math.round(p.weight_g)} г`);
    else parts.push(p.unit === 'kg' ? 'на вес' : 'шт');
    if (p.brand) parts.push(p.brand);
    if (chainsCount > 0) parts.push(`в ${chainsCount} ${chainsCount === 1 ? 'сети' : 'сетях'}`);
    out.push({
      product: p,
      prices,
      chainsCount,
      small: parts.join(' · '),
    });
    if (out.length >= limit) break;
  }
  return out;
}

export interface StorePartMeta {
  code: string;
  name: string;
  where: string;
  whom: string;
  total: number;
  count: number;
  byLink: boolean;
  thumbs: Array<{ name: string; photo: string | null }>;
  more: number;
  needsLogin: boolean;
  again: boolean;
  breakdown: StoreBreakdown;
}

export function buildStorePartMeta(
  ws: WorkspaceSnapshot,
  breakdown: StoreBreakdown,
  productsById?: Map<number, Product>
): StorePartMeta {
  const byId = productsById || new Map(ws.products.map((p) => [p.id, p]));
  const code = breakdown.store_code;
  const kind = KIND_BY_STORE[code] || 'list';
  const byLink = kind === 'link';
  const conn = ws.connections[code];
  const needsLogin = byLink ? false : !(conn && conn.connected);
  const again = Boolean(conn && conn.again);
  const thumbs = breakdown.lines.slice(0, 5).map((ln) => ({
    name: ln.product_name,
    photo: byId.get(ln.product_id)?.photo ?? null,
  }));

  return {
    code,
    name: breakdown.store_name,
    where: where(code, breakdown.store_name),
    whom: whom(code, breakdown.store_name),
    total: breakdown.total,
    count: breakdown.lines.length,
    byLink,
    thumbs,
    more: Math.max(0, breakdown.lines.length - 5),
    needsLogin,
    again,
    breakdown,
  };
}

export function computeSingleStoreTotals(
  ws: WorkspaceSnapshot,
  basketId: number | null
): Record<string, number> {
  if (basketId === null) return {};
  const items = getActiveBasketItems(ws, basketId);
  const totals: Record<string, number> = {};
  for (const it of items) {
    const pMap = ws.prices[String(it.product_id)] || {};
    for (const [code, snap] of Object.entries(pMap)) {
      if (!snap.in_stock) continue;
      totals[code] = Math.round(((totals[code] || 0) + snap.price * it.qty) * 100) / 100;
    }
  }
  return totals;
}
