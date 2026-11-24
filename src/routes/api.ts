import { searchPhoton } from '../shared/addressSearch.js';
import { matchesProductQuery, purchasedProducts, rankProducts } from '../shared/searchRank.js';
import { Router, type NextFunction, type Request, type Response } from 'express';
import {
  DEMO_PHONE,
  formatPhoneDisplay,
  getWorkspace,
  importManualReceipt,
  PHONE_CHAINS,
  resolveLogin,
  SEED_STORES,
} from '../data/store.js';
import {
  checkLoginRateLimit,
  checkPin,
  pinIsSet,
  recordLoginFailure,
  signInCookie,
  signOutCookie,
  type AuthLocals,
} from '../middleware/auth.js';
import { SEARCH_URL_TEMPLATES } from '../shared/format.js';
import type {
  Basket,
  BasketItem,
  Card,
  Offer,
  Product,
  PurchaseRow,
  ReceiptRecord,
  Store,
  StoreCartQuote,
  StoreConnection,
  StorePerk,
  StoreProductPrice,
  Workspace,
} from '../types.js';

export const apiRouter = Router();

export interface SerializedWorkspace {
  phone: string;
  phoneLabel: string;
  isDemo: boolean;
  address: string | null;
  point: [number, number] | null;
  stores: Store[];
  products: Product[];
  nextProductId: number;
  /** product_id (as string key) -> store_code -> StoreProductPrice */
  prices: Record<string, Record<string, StoreProductPrice>>;
  cards: Card[];
  offers: Offer[];
  baskets: Basket[];
  nextBasketId: number;
  /** basket_id (as string key) -> BasketItem[] */
  basketItems: Record<string, BasketItem[]>;
  history: PurchaseRow[];
  receipts: ReceiptRecord[];
  connections: Record<string, StoreConnection>;
  storeQuotes: Record<string, StoreCartQuote>;
  storePerks: Record<string, StorePerk>;
  applyBonuses: boolean;
  lastBasketId: number | null;
  repeated: { key: string; basket: number } | null;
  fnsConnected: boolean;
  fnsPhone: string | null;
  fnsSyncedAt: string | null;
  syncedAt: string;
}

export function serializeWorkspace(ws: Workspace): SerializedWorkspace {
  const products = [...ws.products.values()];
  const prices: Record<string, Record<string, StoreProductPrice>> = {};
  for (const [pid, storeMap] of ws.prices.entries()) {
    const rec: Record<string, StoreProductPrice> = {};
    for (const [code, snap] of storeMap.entries()) {
      rec[code] = snap;
    }
    prices[String(pid)] = rec;
  }

  const basketItems: Record<string, BasketItem[]> = {};
  for (const [bid, items] of ws.basketItems.entries()) {
    basketItems[String(bid)] = items.map((it) => ({ ...it }));
  }

  const connections: Record<string, StoreConnection> = {};
  for (const code of PHONE_CHAINS) {
    const c = ws.connections.get(code);
    connections[code] = c
      ? { ...c }
      : { store_code: code, connected: false, again: false, last: null };
  }

  const storeQuotes: Record<string, StoreCartQuote> = {};
  for (const [code, q] of ws.storeQuotes.entries()) {
    storeQuotes[code] = { ...q, missing_names: [...q.missing_names] };
  }

  const storePerks: Record<string, StorePerk> = {};
  for (const [code, p] of ws.storePerks.entries()) {
    storePerks[code] = { ...p };
  }

  return {
    phone: ws.phone,
    phoneLabel: formatPhoneDisplay(ws.phone),
    isDemo: ws.phone === DEMO_PHONE,
    address: ws.address,
    point: ws.point,
    stores: SEED_STORES,
    products,
    nextProductId: ws.nextProductId,
    prices,
    cards: ws.cards.map((c) => ({ ...c })),
    offers: ws.offers.map((o) => ({ ...o })),
    baskets: ws.baskets.map((b) => ({ ...b })),
    nextBasketId: ws.nextBasketId,
    basketItems,
    history: ws.history.map((h) => ({ ...h })),
    receipts: ws.receipts.map((r) => ({ ...r })),
    connections,
    storeQuotes,
    storePerks,
    applyBonuses: ws.applyBonuses,
    lastBasketId: ws.lastBasketId,
    repeated: ws.repeated ? { ...ws.repeated } : null,
    fnsConnected: ws.fnsConnected,
    fnsPhone: ws.fnsPhone,
    fnsSyncedAt: ws.fnsSyncedAt,
    syncedAt: new Date().toISOString(),
  };
}

apiRouter.get('/health', (_req, res) => {
  res.json({ ok: true, mode: 'spa-api', time: new Date().toISOString() });
});

apiRouter.get('/auth/me', (_req, res) => {
  const auth = res.locals.auth as AuthLocals;
  res.json({
    ok: true,
    authenticated: Boolean(auth.phone && auth.workspace),
    phone: auth.phone,
    phoneLabel: auth.phoneLabel,
    isDemo: auth.isDemo,
    pinRequired: pinIsSet(),
  });
});

apiRouter.post('/auth/login', (req, res) => {
  const rawPhone = String(req.body?.phone || '').trim();
  const pin = String(req.body?.pin || '').trim();

  if (checkLoginRateLimit(req)) {
    res.status(429).json({
      ok: false,
      error: 'Слишком много неудачных попыток входа. Попробуйте через 15 минут.',
    });
    return;
  }

  if (pinIsSet() && !checkPin(pin)) {
    recordLoginFailure(req);
    res.status(401).json({ ok: false, error: 'Неверный PIN-код.' });
    return;
  }

  const [phone, err] = resolveLogin(rawPhone);
  if (!phone) {
    res.status(400).json({ ok: false, error: err || 'Введите номер телефона' });
    return;
  }

  const token = signInCookie(res, phone, req);
  const ws = getWorkspace(phone);
  res.json({
    ok: true,
    token,
    auth: {
      authenticated: true,
      phone,
      phoneLabel: formatPhoneDisplay(phone),
      isDemo: false,
      pinRequired: pinIsSet(),
    },
    snapshot: serializeWorkspace(ws),
  });
});

apiRouter.post('/auth/demo', (req, res) => {
  const token = signInCookie(res, DEMO_PHONE, req);
  const ws = getWorkspace(DEMO_PHONE);
  res.json({
    ok: true,
    token,
    auth: {
      authenticated: true,
      phone: DEMO_PHONE,
      phoneLabel: formatPhoneDisplay(DEMO_PHONE),
      isDemo: true,
      pinRequired: pinIsSet(),
    },
    snapshot: serializeWorkspace(ws),
  });
});

apiRouter.post('/auth/logout', (req, res) => {
  signOutCookie(res, req);
  res.json({ ok: true });
});

apiRouter.get('/address/suggest', async (req, res) => {
  try {
    res.json({ ok: true, items: await searchPhoton(String(req.query.q || '')) });
  } catch {
    res.status(503).json({ ok: false, error: 'Поиск адресов временно недоступен' });
  }
});

// Public reverse geocode proxy
apiRouter.get('/address/reverse', async (req, res) => {
  const lat = Number(req.query.lat);
  const lon = Number(req.query.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    res.status(400).json({ ok: false, error: 'Неверные координаты' });
    return;
  }
  try {
    const url = `https://nominatim.openstreetmap.org/reverse?format=json&addressdetails=1&lat=${lat}&lon=${lon}`;
    const r = await fetch(url, {
      headers: {
        'User-Agent': 'MoyaKorzina/1.0 (local-first-spa)',
        'Accept-Language': 'ru',
      },
      signal: AbortSignal.timeout(4500),
    });
    if (!r.ok) {
      res.json({ ok: true, address: `${lat.toFixed(5)}, ${lon.toFixed(5)}` });
      return;
    }
    const data = (await r.json()) as { display_name?: string; address?: Record<string, string> };
    const a = data.address || {};
    const city = a.city || a.town || a.village || a.state || '';
    const road = a.road || a.pedestrian || a.suburb || '';
    const house = a.house_number || '';
    const address = [city, road, house].filter(Boolean).join(', ') || data.display_name || `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    res.json({ ok: true, address });
  } catch {
    res.json({ ok: true, address: `${lat.toFixed(5)}, ${lon.toFixed(5)}` });
  }
});

// Public OSM Tile proxy
apiRouter.get('/map/tile/:z/:x/:y.png', async (req, res) => {
  const z = Number(req.params.z);
  const x = Number(req.params.x);
  const y = Number(req.params.y);
  if (!Number.isInteger(z) || !Number.isInteger(x) || !Number.isInteger(y) || z < 0 || z > 19) {
    res.status(400).end();
    return;
  }
  try {
    const r = await fetch(`https://tile.openstreetmap.org/${z}/${x}/${y}.png`, {
      headers: { 'User-Agent': 'MoyaKorzina/1.0 (local-first-spa)' },
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) {
      res.status(502).end();
      return;
    }
    res.setHeader('Content-Type', 'image/png');
    res.setHeader('Cache-Control', 'public, max-age=86400');
    const buf = Buffer.from(await r.arrayBuffer());
    res.send(buf);
  } catch {
    res.status(502).end();
  }
});

// Middleware: require authenticated session for remaining /api routes
apiRouter.use((req, res, next) => {
  const auth = res.locals.auth as AuthLocals;
  if (!auth.phone || !auth.workspace) {
    res.status(401).json({ ok: false, error: 'Требуется вход' });
    return;
  }
  next();
});

apiRouter.get('/sync', (_req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  res.json({ ok: true, snapshot: serializeWorkspace(ws) });
});

apiRouter.post('/sync', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  if (auth.isDemo) {
    res.status(403).json({ ok: false, error: 'Это демо — изменения хранятся только локально' });
    return;
  }

  const body = req.body || {};

  if (Array.isArray(body.baskets)) {
    ws.baskets = body.baskets.map((b: Basket) => ({
      id: Number(b.id),
      name: String(b.name || 'Корзина'),
      created_at: String(b.created_at || new Date().toISOString().slice(0, 10)),
      source: b.source === 'history' || b.source === 'average' ? b.source : 'manual',
    }));
  }

  if (typeof body.nextBasketId === 'number' && Number.isFinite(body.nextBasketId)) {
    ws.nextBasketId = Math.max(ws.nextBasketId, Math.trunc(body.nextBasketId));
  }

  if (body.basketItems && typeof body.basketItems === 'object') {
    const nextMap = new Map<number, BasketItem[]>();
    for (const [k, list] of Object.entries(body.basketItems as Record<string, BasketItem[]>)) {
      const bid = Number(k);
      if (!Number.isFinite(bid) || !Array.isArray(list)) continue;
      nextMap.set(
        bid,
        list
          .filter((it) => Number.isFinite(Number(it.product_id)) && Number(it.qty) > 0)
          .map((it) => ({
            basket_id: bid,
            product_id: Number(it.product_id),
            qty: Math.round(Number(it.qty) * 1000) / 1000,
          }))
      );
    }
    ws.basketItems = nextMap;
  }

  if (body.lastBasketId === null || typeof body.lastBasketId === 'number') {
    ws.lastBasketId = body.lastBasketId;
  }

  if (body.repeated === null || (body.repeated && typeof body.repeated === 'object')) {
    ws.repeated = body.repeated
      ? { key: String(body.repeated.key || ''), basket: Number(body.repeated.basket || 0) }
      : null;
  }

  if (body.address === null || typeof body.address === 'string') {
    ws.address = body.address ? body.address.trim() : null;
  }

  if (
    body.point === null ||
    (Array.isArray(body.point) &&
      body.point.length === 2 &&
      Number.isFinite(Number(body.point[0])) &&
      Number.isFinite(Number(body.point[1])))
  ) {
    ws.point = body.point ? [Number(body.point[0]), Number(body.point[1])] : null;
  }

  if (body.connections && typeof body.connections === 'object') {
    for (const [code, val] of Object.entries(body.connections as Record<string, StoreConnection>)) {
      if (!val || typeof val !== 'object') continue;
      ws.connections.set(code, {
        store_code: code,
        connected: Boolean(val.connected),
        again: Boolean(val.again),
        last: val.last ? String(val.last) : null,
        mode: val.mode === 'session' || val.mode === 'manual' ? val.mode : undefined,
      });
    }
  }

  if (Array.isArray(body.cards)) {
    ws.cards = body.cards.map((c: Card) => ({
      id: Number(c.id),
      bank: String(c.bank || ''),
      name: String(c.name || ''),
    }));
  }

  if (Array.isArray(body.offers)) {
    ws.offers = body.offers.map((o: Offer) => ({
      id: Number(o.id),
      card_id: Number(o.card_id),
      store_id: Number(o.store_id),
      percent: Number(o.percent || 0),
      cap_rub: Number(o.cap_rub || 0),
      min_check_rub: Number(o.min_check_rub || 0),
      valid_from: o.valid_from ? String(o.valid_from) : null,
      valid_to: o.valid_to ? String(o.valid_to) : null,
      requires_activation: Boolean(o.requires_activation),
      activated: Boolean(o.activated),
      cap_used: Number(o.cap_used || 0),
    }));
  }

  if (typeof body.fnsConnected === 'boolean') {
    ws.fnsConnected = body.fnsConnected;
    if (ws.fnsConnected && !ws.fnsSyncedAt) {
      ws.fnsSyncedAt = new Date().toISOString().slice(0, 16).replace('T', ' ');
    }
  }

  if (typeof body.fnsPhone === 'string' || body.fnsPhone === null) {
    ws.fnsPhone = body.fnsPhone;
  }

  if (typeof body.applyBonuses === 'boolean') {
    ws.applyBonuses = body.applyBonuses;
  }

  if (body.storePerks && typeof body.storePerks === 'object') {
    for (const [code, val] of Object.entries(body.storePerks as Record<string, StorePerk>)) {
      if (!val || typeof val !== 'object') continue;
      ws.storePerks.set(code, {
        store_code: code,
        promo_label: val.promo_label ? String(val.promo_label).trim() : null,
        promo_percent: Math.max(0, Math.min(90, Number(val.promo_percent) || 0)),
        promo_rub: Math.max(0, Number(val.promo_rub) || 0),
        bonus_balance: Math.max(0, Number(val.bonus_balance) || 0),
        bonus_max_pct: Math.max(0, Math.min(100, Number(val.bonus_max_pct) || 30)),
      });
    }
  }

  res.json({ ok: true, syncedAt: new Date().toISOString() });
});

apiRouter.get('/search', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  const q = String(req.query.q || '').trim().toLowerCase();
  if (q.length < 2) {
    res.json({ ok: true, results: [] });
    return;
  }
  const tokens = q.split(/\s+/).filter(Boolean);
  const store = String(req.query.store || '');
  const results: Array<{
    product: Product;
    prices: Record<string, StoreProductPrice>;
    chainsCount: number;
    small: string;
  }> = [];

  for (const p of ws.products.values()) {
    if (!p.active) continue;
    if (!matchesProductQuery(p, tokens, q)) continue;
    const pMap = ws.prices.get(p.id);
    const pricesRec: Record<string, StoreProductPrice> = {};
    if (pMap) {
      for (const [code, snap] of pMap.entries()) {
        pricesRec[code] = snap;
      }
    }
    if (store && !(pricesRec[store]?.in_stock && pricesRec[store].price > 0)) continue;
    const chainsCount = Object.values(pricesRec).filter(p => p.in_stock && p.price > 0).length;
    const parts: string[] = [];
    if (p.weight_g) parts.push(`${Math.round(p.weight_g)} г`);
    else parts.push(p.unit === 'kg' ? 'на вес' : 'шт');
    if (p.brand) parts.push(p.brand);
    if (chainsCount > 0) parts.push(`в ${chainsCount} ${chainsCount === 1 ? 'сети' : 'сетях'}`);
    results.push({
      product: p,
      prices: pricesRec,
      chainsCount,
      small: parts.join(' · '),
    });
  }

  res.json({ ok: true, results: rankProducts(results, purchasedProducts(ws.history), store).slice(0,30) });
});

apiRouter.route('/replacements').all((req, res) => {
  if (!['GET', 'POST'].includes(req.method)) { res.sendStatus(405); return; }
  const ws = (res.locals.auth as AuthLocals).workspace!;
  const stores = [req.query.store].flat().map(String);
  const unit = req.query.unit;
  const exclude = Number(req.query.exclude);
  if (!exclude || !['pcs','kg'].includes(String(unit)) || stores.length > 2 ||
      stores.some(code => !SEED_STORES.some(store => store.code === code))) {
    res.status(400).json({error:'Выберите вариант корзины заново'}); return;
  }
  const normalize = (name: string) => name.toLowerCase().replace(/яйцо/gu,'яйца')
    .replace(/c([012])/gu,'с$1').replace(/(\d+)\s*шт/gu,'$1 шт');
  const tokens = normalize(String(req.query.q || '')).trim().split(/\s+/);
  const bought = purchasedProducts(ws.history);
  const results = [];
  for (const product of ws.products.values()) {
    if (!product.active || product.unit !== unit || product.id === exclude) continue;
    if (!tokens.every(t => normalize(product.name).includes(t))) continue;
    const prices = Object.fromEntries([...(ws.prices.get(product.id) || [])].filter(([code,p]) =>
      stores.includes(code) && p.source === 'live' && p.in_stock && p.price > 0 &&
      Date.parse(p.fetched_at) >= Date.now() - 6 * 3600000));
    if (!Object.keys(prices).length) continue;
    results.push({product:{...product,previouslyBought:bought.has(product.id)},prices,
      available:true,chainsCount:Object.keys(prices).length,small:''});
  }
  if (req.method === 'POST') {
    const row = results.find(r => r.product.id === Number(req.query.product) && r.prices[stores[0]]?.sku === req.query.sku);
    if (!row) { res.status(409).json({error:'Магазин больше не подтверждает эту замену'}); return; }
    res.json({ok:true,item:row});
  } else {
    res.json({ok:true,results:rankProducts(results,bought).slice(0,30)});
  }
});

apiRouter.post('/receipts/paste', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  if (auth.isDemo) {
    res.status(403).json({ ok: false, error: 'Это демо — изменения не сохраняются на сервере' });
    return;
  }
  const storeCode = String(req.body?.store_code || 'pyaterochka');
  const rawText = String(req.body?.text || '');
  const result = importManualReceipt(ws, storeCode, rawText);
  if (result.error) {
    res.status(400).json({ ok: false, error: result.error });
    return;
  }
  res.json({
    ok: true,
    imported: result.imported,
    total: result.total,
    snapshot: serializeWorkspace(ws),
  });
});

apiRouter.post('/fns/sync', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  if (auth.isDemo) {
    res.status(403).json({ ok: false, error: 'В демо-режиме синхронизация ФНС уже показана на примере' });
    return;
  }
  const fnsPhone = String(req.body?.phone || '').trim() || formatPhoneDisplay(ws.phone);
  const rawKeys = typeof req.body?.keys === 'string' ? req.body.keys.trim() : '';

  if (!ws.fnsConnected && !rawKeys) {
    res.status(400).json({
      ok: false,
      error: 'Укажите ключ кабинета ФНС (JSON или refreshToken из lkdr.nalog.ru).',
    });
    return;
  }

  if (rawKeys) {
    if (rawKeys.length < 8) {
      res.status(400).json({
        ok: false,
        error: 'Ключ слишком короткий — скопируйте JSON или refreshToken из кабинета ФНС.',
      });
      return;
    }
    if (rawKeys.startsWith('{')) {
      try {
        JSON.parse(rawKeys);
      } catch {
        res.status(400).json({
          ok: false,
          error: 'Некорректный JSON ключа ФНС. Проверьте, что скопировали его полностью.',
        });
        return;
      }
    }
  }

  ws.fnsConnected = true;
  ws.fnsPhone = fnsPhone;
  ws.fnsSyncedAt = new Date().toISOString().slice(0, 16).replace('T', ' ');
  res.json({ ok: true, snapshot: serializeWorkspace(ws) });
});

apiRouter.post('/fns/disconnect', (_req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  if (auth.isDemo) {
    res.status(403).json({ ok: false, error: 'В демо-режиме кабинет ФНС нельзя отключить на сервере' });
    return;
  }
  ws.fnsConnected = false;
  ws.fnsSyncedAt = null;
  res.json({ ok: true, snapshot: serializeWorkspace(ws) });
});

// Fast live status endpoint polled by window.kzRunsChanged() after Android background collection
apiRouter.get('/runs', (_req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  const snap = serializeWorkspace(ws);
  res.json({
    ok: true,
    storeQuotes: snap.storeQuotes,
    connections: snap.connections,
    prices: snap.prices,
    storePerks: snap.storePerks,
    applyBonuses: snap.applyBonuses,
    syncedAt: snap.syncedAt,
  });
});

// Per-chain DOM automation rules for Android WebView cart assembly across all 11 retail networks
const CHAIN_CART_RULES: Record<
  string,
  { hosts: string[]; add_name: string[]; plus_name: string[]; gone: string[] }
> = {
  magnit: {
    hosts: ['magnit.ru', 'dostavka.magnit.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'разобрали', 'недоступен'],
  },
  pyaterochka: {
    hosts: ['5ka.ru', 'dostavka.5ka.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'разобрали', 'товар закончился'],
  },
  vkusvill: {
    hosts: ['vkusvill.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'разобрали', 'недоступно'],
  },
  lenta: {
    hosts: ['lenta.com'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'нет в выбранном магазине'],
  },
  dixy: {
    hosts: ['dixy.ru', 'dostavka.dixy.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'разобрали'],
  },
  samokat: {
    hosts: ['samokat.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['разобрали', 'нет в наличии', 'закончился'],
  },
  metro: {
    hosts: ['online.metro-cc.ru', 'metro-cc.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'разобрали', 'недоступно'],
  },
  perekrestok: {
    hosts: ['perekrestok.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'товар закончился'],
  },
  fixprice: {
    hosts: ['fix-price.com'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'недоступен'],
  },
  vprok: {
    hosts: ['vprok.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'товар закончился'],
  },
  monetka: {
    hosts: ['monetka.ru', 'dostavka.monetka.ru'],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+', 'увеличить'],
    gone: ['нет в наличии', 'разобрали'],
  },
};

function computePlanStamp(basketId: number, items: BasketItem[], address: string | null): string {
  const parts = items
    .map((it) => `${it.product_id}:${it.qty}`)
    .sort()
    .join(',');
  return `${basketId}|${parts}|${(address || '').trim().toLowerCase()}`;
}

function nowStamp(): string {
  return new Date().toISOString().slice(0, 16).replace('T', ' ');
}

function parseFiniteNum(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v.trim()) {
    const cleaned = v.replace(/\s+/g, '').replace(',', '.');
    const n = Number(cleaned);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// Android APK device bridge endpoints (full protocol for MainActivity.java across all 11 chains)
apiRouter.post('/device/connected', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  const store = String(req.body?.store || '').trim().toLowerCase();
  const connected = req.body?.connected !== false;
  if (!store) {
    res.status(400).json({ ok: false, error: 'Не указан код сети' });
    return;
  }
  ws.connections.set(store, {
    store_code: store,
    connected,
    again: false,
    last: connected ? nowStamp() : null,
    mode: connected ? 'session' : undefined,
  });
  res.json({ ok: true, store, connected });
});

apiRouter.post('/device/cartplan', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  const store = String(req.body?.store || '').trim().toLowerCase();
  const reqBasket = Number(req.body?.basket);
  const basketId = Number.isFinite(reqBasket) && reqBasket > 0
    ? reqBasket
    : ws.lastBasketId ?? ws.baskets[0]?.id ?? null;

  if (!store || basketId === null) {
    res.json({ ok: true, lines: [], search: [], drop: [], total: 0 });
    return;
  }

  const basketItems = ws.basketItems.get(basketId) || [];
  const searchTpl = SEARCH_URL_TEMPLATES[store] || 'https://magnit.ru/search?term=';
  const rules = CHAIN_CART_RULES[store] || {
    hosts: [],
    add_name: ['в корзину', 'добавить'],
    plus_name: ['+'],
    gone: ['нет в наличии', 'разобрали'],
  };

  let ourTotal = 0;
  const lines: Array<{ product_id: number; sku: string; name: string; url: string; clicks: number }> = [];
  const search: Array<{ product_id: number; name: string; url: string; search_alt: string; clicks: number }> = [];

  for (const item of basketItems) {
    const prod = ws.products.get(item.product_id);
    if (!prod) continue;
    const snap = ws.prices.get(item.product_id)?.get(store);
    const clicks = prod.unit === 'kg' ? Math.max(1, Math.round(item.qty * 2)) : Math.max(1, Math.round(item.qty));
    if (snap) {
      ourTotal += snap.price * item.qty;
    }
    const q = encodeURIComponent(prod.name);
    const shortQ = encodeURIComponent(prod.name.split(/\s+/).slice(0, 3).join(' '));
    search.push({
      product_id: prod.id,
      name: prod.name,
      url: `${searchTpl}${q}`,
      search_alt: `${searchTpl}${shortQ}`,
      clicks,
    });
  }

  const stamp = computePlanStamp(basketId, basketItems, ws.address);
  const existing = ws.storeQuotes.get(store);
  ws.storeQuotes.set(store, {
    store_code: store,
    status: 'collecting',
    stamp,
    updated_at: nowStamp(),
    address: ws.address,
    available_count: existing?.available_count ?? 0,
    total_count: basketItems.length,
    missing_names: existing?.missing_names ?? [],
    goods_total: existing?.goods_total ?? null,
    delivery_fee: existing?.delivery_fee ?? null,
    service_fee: existing?.service_fee ?? 0,
    promo_discount: existing?.promo_discount ?? 0,
    promo_label: existing?.promo_label ?? null,
    bonus_balance: existing?.bonus_balance ?? 0,
    bonus_max_use: existing?.bonus_max_use ?? 0,
    store_total: existing?.store_total ?? null,
    note: 'Проверяем цены и наличие…',
  });

  res.json({
    ok: true,
    basket: basketId,
    stamp,
    total: Math.round(ourTotal * 100) / 100,
    address: ws.address,
    point: ws.point,
    note: ws.address ? `Адрес: ${ws.address}` : '',
    rules,
    lines,
    search,
    drop: [],
  });
});

apiRouter.post('/device/cartprogress', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  const store = String(req.body?.store || '').trim().toLowerCase();
  if (!store) {
    res.json({ ok: true });
    return;
  }

  const existing = ws.storeQuotes.get(store);
  const items = Array.isArray(req.body?.items) ? (req.body.items as Array<{ ok?: boolean }>) : [];
  const doneCount = parseFiniteNum(req.body?.done) ?? items.filter((i) => i?.ok).length;
  const totalCount =
    parseFiniteNum(req.body?.total) ??
    (items.length > 0 ? items.length : existing?.total_count ?? 0);
  const note = typeof req.body?.note === 'string' && req.body.note.trim()
    ? req.body.note.trim()
    : `Собрано ${doneCount} из ${totalCount || '?'}`;

  ws.storeQuotes.set(store, {
    store_code: store,
    status: 'collecting',
    stamp: typeof req.body?.stamp === 'string' ? req.body.stamp : existing?.stamp ?? null,
    updated_at: nowStamp(),
    address: ws.address,
    available_count: doneCount,
    total_count: totalCount,
    missing_names: existing?.missing_names ?? [],
    goods_total: existing?.goods_total ?? null,
    delivery_fee: existing?.delivery_fee ?? null,
    service_fee: existing?.service_fee ?? 0,
    promo_discount: existing?.promo_discount ?? 0,
    promo_label: existing?.promo_label ?? null,
    bonus_balance: existing?.bonus_balance ?? 0,
    bonus_max_use: existing?.bonus_max_use ?? 0,
    store_total: existing?.store_total ?? null,
    note,
  });

  res.json({ ok: true });
});

apiRouter.post('/device/cartreport', (req, res) => {
  const auth = res.locals.auth as AuthLocals;
  const ws = auth.workspace!;
  const store = String(req.body?.store || '').trim().toLowerCase();
  if (!store) {
    res.status(400).json({ ok: false, error: 'Не указан код сети' });
    return;
  }

  const now = nowStamp();
  const reportItems = Array.isArray(req.body?.items)
    ? (req.body.items as Array<{
        product_id?: number;
        name?: string;
        sku?: string;
        ok?: boolean;
        why?: string;
        put?: number;
        times?: number;
        price?: number;
        unit_price?: number;
      }>)
    : [];

  // Match reported items to workspace products to update live availability & prices
  const productsByName = new Map<string, Product>();
  for (const p of ws.products.values()) {
    productsByName.set(p.name.trim().toLowerCase(), p);
  }

  let availableCount = 0;
  const missingNames: string[] = [];

  for (const it of reportItems) {
    const ok = Boolean(it.ok);
    if (ok) availableCount += 1;
    else if (it.name) missingNames.push(String(it.name));

    let prod: Product | undefined;
    if (typeof it.product_id === 'number' && ws.products.has(it.product_id)) {
      prod = ws.products.get(it.product_id);
    } else if (it.name) {
      const key = String(it.name).trim().toLowerCase();
      prod = productsByName.get(key);
      if (!prod) {
        for (const p of ws.products.values()) {
          if (p.name.toLowerCase().includes(key) || key.includes(p.name.toLowerCase())) {
            prod = p;
            break;
          }
        }
      }
    }

    if (prod) {
      let storeMap = ws.prices.get(prod.id);
      if (!storeMap) {
        storeMap = new Map();
        ws.prices.set(prod.id, storeMap);
      }
      const prev = storeMap.get(store);
      const rawUnit = parseFiniteNum(it.unit_price);
      const rawLine = parseFiniteNum(it.price);
      const qtyTimes = Math.max(1, parseFiniteNum(it.put) ?? parseFiniteNum(it.times) ?? 1);
      const resolvedPrice =
        rawUnit !== null && rawUnit > 0
          ? rawUnit
          : rawLine !== null && rawLine > 0
            ? Math.round((rawLine / qtyTimes) * 100) / 100
            : prev?.price ?? 0;

      if (resolvedPrice > 0 || prev) {
        const finalPrice = resolvedPrice > 0 ? resolvedPrice : prev!.price;
        storeMap.set(store, {
          store_code: store,
          sku: String(it.sku || prev?.sku || `${store}-${prod.id}`),
          raw_name: String(it.name || prev?.raw_name || prod.name),
          price: finalPrice,
          unit: prod.unit,
          weight_g: prod.weight_g,
          price_per_kg:
            prod.weight_g && prod.weight_g > 0
              ? Math.round((finalPrice / (prod.weight_g / 1000)) * 100) / 100
              : prev?.price_per_kg ?? null,
          in_stock: ok,
          fetched_at: now,
          source: 'live',
        });
      }
    }
  }

  // Extract live delivery, service fee, store promos/discounts, bonuses/points, and totals from READ_JS page_totals & api_totals
  let deliveryFee: number | null = null;
  let serviceFee = 0;
  let promoDiscount = 0;
  let promoLabel: string | null = null;
  let bonusBalance = 0;
  let bonusMaxUse = 0;
  let storeTotal: number | null = null;
  let goodsTotal: number | null = null;

  const pageTotals = Array.isArray(req.body?.page_totals)
    ? (req.body.page_totals as Array<{ kind?: string; label?: string; amount?: number }>)
    : [];

  for (const pt of pageTotals) {
    const kind = String(pt?.kind || '').toLowerCase();
    const amt = parseFiniteNum(pt?.amount);
    if (amt === null) continue;
    const absAmt = Math.abs(amt);
    if (kind === 'delivery') {
      deliveryFee = absAmt;
    } else if (kind === 'service') {
      serviceFee = absAmt;
    } else if (kind === 'discount') {
      promoDiscount = Math.round((promoDiscount + absAmt) * 100) / 100;
      if (pt.label && !promoLabel) promoLabel = String(pt.label).trim();
    } else if (kind === 'points') {
      bonusBalance = Math.max(bonusBalance, absAmt);
    } else if (kind === 'total') {
      storeTotal = absAmt;
    } else if (kind === 'goods') {
      goodsTotal = absAmt;
    }
  }

  const apiTotals =
    req.body?.api_totals && typeof req.body.api_totals === 'object'
      ? (req.body.api_totals as Record<string, unknown>)
      : null;

  if (apiTotals) {
    const apiDel = parseFiniteNum(apiTotals.delivery);
    if (apiDel !== null) deliveryFee = Math.abs(apiDel);
    const apiSvc = parseFiniteNum(apiTotals.service);
    if (apiSvc !== null) serviceFee = Math.abs(apiSvc);
    const apiDisc = parseFiniteNum(apiTotals.discount ?? apiTotals.promo_discount);
    if (apiDisc !== null && Math.abs(apiDisc) > promoDiscount) {
      promoDiscount = Math.abs(apiDisc);
    }
    if (typeof apiTotals.promo_label === 'string' && apiTotals.promo_label.trim()) {
      promoLabel = apiTotals.promo_label.trim();
    }
    const apiPts = parseFiniteNum(apiTotals.points ?? apiTotals.bonus ?? apiTotals.bonus_balance);
    if (apiPts !== null && Math.abs(apiPts) > bonusBalance) {
      bonusBalance = Math.abs(apiPts);
    }
    const apiMaxPts = parseFiniteNum(apiTotals.bonus_max ?? apiTotals.bonus_max_use);
    if (apiMaxPts !== null) bonusMaxUse = Math.abs(apiMaxPts);
    const apiTot = parseFiniteNum(apiTotals.total);
    if (apiTot !== null) storeTotal = Math.abs(apiTot);
    const apiGoods = parseFiniteNum(apiTotals.goods);
    if (apiGoods !== null) goodsTotal = Math.abs(apiGoods);
  }

  if (bonusBalance > 0 && bonusMaxUse <= 0) {
    const perkMaxPct = ws.storePerks.get(store)?.bonus_max_pct ?? 30;
    const baseForBonus = goodsTotal ?? parseFiniteNum(req.body?.our_total) ?? 0;
    bonusMaxUse =
      baseForBonus > 0
        ? Math.min(bonusBalance, Math.round(baseForBonus * (perkMaxPct / 100) * 100) / 100)
        : bonusBalance;
  }

  // Persist detected store bonuses into storePerks so future baskets remember them
  if (bonusBalance > 0) {
    const prevPerk = ws.storePerks.get(store);
    ws.storePerks.set(store, {
      store_code: store,
      promo_label: prevPerk?.promo_label ?? promoLabel,
      promo_percent: prevPerk?.promo_percent ?? 0,
      promo_rub: prevPerk?.promo_rub ?? 0,
      bonus_balance: bonusBalance,
      bonus_max_pct: prevPerk?.bonus_max_pct ?? 30,
    });
  }

  const quote: StoreCartQuote = {
    store_code: store,
    status: 'ready',
    stamp: typeof req.body?.stamp === 'string' ? req.body.stamp : null,
    updated_at: now,
    address: ws.address,
    available_count: availableCount,
    total_count: reportItems.length,
    missing_names: missingNames,
    goods_total: goodsTotal,
    delivery_fee: deliveryFee,
    service_fee: serviceFee,
    promo_discount: promoDiscount,
    promo_label: promoLabel,
    bonus_balance: bonusBalance,
    bonus_max_use: bonusMaxUse,
    store_total: storeTotal,
    note:
      missingNames.length > 0
        ? `Нет по адресу: ${missingNames.slice(0, 2).join(', ')}`
        : 'Проверено в живой корзине',
  };

  ws.storeQuotes.set(store, quote);

  const prevConn = ws.connections.get(store);
  if (!prevConn?.connected) {
    ws.connections.set(store, {
      store_code: store,
      connected: true,
      again: false,
      last: now,
      mode: 'session',
    });
  }

  res.json({ ok: true, quote });
});

apiRouter.post('/device/find', (_req, res) => {
  res.json({ ok: true });
});

apiRouter.post('/device/cartlink', (_req, res) => {
  res.json({ ok: true });
});

apiRouter.post('/device/cards', (_req, res) => {
  res.json({ ok: true });
});

// Ensure unknown /api/* routes always return JSON, never HTML
apiRouter.use((_req, res) => {
  res.status(404).json({ ok: false, error: 'Маршрут API не найден' });
});

// Ensure unexpected errors on /api/* always return JSON, never Express HTML
apiRouter.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  const message = err instanceof Error ? err.message : 'Внутренняя ошибка сервера';
  res.status(500).json({ ok: false, error: message });
});
