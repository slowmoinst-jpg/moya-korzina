import express from 'express';
import {
  buildBoard,
  buildOrdersList,
  buildRunsPayload,
  buildStoreLink,
  buildStorePart,
  createBasket,
  getBasketItems,
  getCurrentBasket,
  getHabits,
  getStore,
  groupByMonth,
  PHONE_CHAINS,
  searchProducts,
  setBasketItemQty,
  stepForUnit,
} from '../data/store.js';
import { cheques, humanDay, rub, where, whole } from '../helpers.js';
import { requirePhone, type AuthLocals } from '../middleware/auth.js';
import { KIND_BY_STORE } from '../optimizer.js';

export const phoneRouter = express.Router();

const FALLBACK_ADDRESSES = [
  'Санкт-Петербург, Невский проспект, 28',
  'Санкт-Петербург, Литейный проспект, 45',
  'Санкт-Петербург, Московский проспект, 112',
  'Москва, Ходынский бульвар, 4',
  'Москва, Ленинский проспект, 42',
  'Москва, Тверская улица, 14',
  'Екатеринбург, проспект Ленина, 36',
  'Казань, улица Баумана, 54',
  'Новосибирск, Красный проспект, 50',
];

const tileCache = new Map<string, { buf: Buffer; at: number }>();

function authOf(res: express.Response): AuthLocals {
  return res.locals.auth as AuthLocals;
}

phoneRouter.get('/', (_req, res) => {
  res.redirect('/phone/basket');
});

phoneRouter.get('/basket', requirePhone, (req, res) => {
  const { workspace: ws, isDemo, devicePhone } = authOf(res);
  if (!ws) return res.redirect('/login');

  const q = String(req.query.q || '').trim();
  const explicitId = req.query.id ? Number(req.query.id) : null;
  const basket = getCurrentBasket(ws, Number.isFinite(explicitId) ? explicitId : null);
  const rawItems = basket ? getBasketItems(ws, basket.id) : [];

  const board = basket && rawItems.length > 0 ? buildBoard(ws, basket.id) : null;
  const pricesByPid = board?.pricesByPid || {};

  const items = rawItems.map((it) => {
    const step = stepForUnit(it.unit);
    const pr = pricesByPid[it.product_id];
    return {
      pid: it.product_id,
      name: it.name,
      unit: it.unit,
      qty: it.qty,
      last: it.qty <= step + 1e-6,
      photo: it.photo,
      price: pr ? whole(pr) : '',
    };
  });

  const found =
    !isDemo && q.length >= 2
      ? searchProducts(ws, q, 25).map((h) => ({
          id: h.id,
          name: h.name,
          small: h.small,
          do: h.doVal,
        }))
      : [];

  const habits = getHabits(ws, basket ? basket.id : 0, 8);
  const top = board
    ? { places: board.places }
    : basket && rawItems.length > 0
      ? { places: [] }
      : null;

  const runs =
    basket && rawItems.length > 0 && !isDemo
      ? buildRunsPayload(ws, basket, devicePhone)
      : null;

  res.render('phone/basket', {
    tab: 'basket',
    q,
    basket,
    baskets_n: ws.baskets.length,
    no_address: !ws.address,
    items,
    found,
    habits,
    top,
    runs,
  });
});

phoneRouter.post('/basket', requirePhone, (req, res) => {
  const { workspace: ws, devicePhone } = authOf(res);
  if (!ws) return res.redirect('/login');

  const q = String(req.body.q || '').trim();
  const action = String(req.body.do || '').trim();
  const explicitId = req.body.basket ? Number(req.body.basket) : null;
  let basket = getCurrentBasket(ws, Number.isFinite(explicitId) ? explicitId : null);
  if (!basket) {
    basket = createBasket(ws, 'Корзина', 'manual');
  }

  let focusPid: number | null = null;
  const [verb, pidStr] = action.split(':');
  const pid = Number(pidStr);
  if (Number.isFinite(pid) && pid > 0) {
    focusPid = pid;
    const prod = ws.products.get(pid);
    const step = stepForUnit(prod?.unit);
    const existing = (ws.basketItems.get(basket.id) || []).find((i) => i.product_id === pid);
    const cur = existing ? existing.qty : 0;

    if (verb === 'add' || verb === 'catalog') {
      setBasketItemQty(ws, basket.id, pid, cur + step);
    } else if (verb === 'plus') {
      setBasketItemQty(ws, basket.id, pid, cur + step);
    } else if (verb === 'minus') {
      setBasketItemQty(ws, basket.id, pid, cur <= step + 1e-6 ? 0 : cur - step);
    } else if (verb === 'remove' || verb === 'drop') {
      setBasketItemQty(ws, basket.id, pid, 0);
    }
  }

  const qs = q ? `?q=${encodeURIComponent(q)}` : '';
  const anchor = focusPid && (verb === 'plus' || verb === 'minus') ? `#it-${focusPid}` : '';
  const back = `/phone/basket${qs}${anchor}`;

  if (req.accepts(['html', 'json']) === 'json') {
    const runs = buildRunsPayload(ws, basket, devicePhone);
    return res.json({ ok: true, back, collect: runs.collect });
  }
  return res.redirect(303, back);
});

phoneRouter.get('/basket/runs', requirePhone, (req, res) => {
  const { workspace: ws, devicePhone } = authOf(res);
  if (!ws) return res.status(401).json({ ok: false });

  const basket = getCurrentBasket(ws);
  const runs = basket ? buildRunsPayload(ws, basket, devicePhone) : null;
  res.render('phone/_runs', { runs }, (err, html) => {
    if (err) return res.status(500).json({ html: '', going: false, stamp: '' });
    return res.json({
      html,
      going: runs?.going ?? false,
      stamp: runs?.stamp ?? '',
      collect: runs?.collect ?? null,
    });
  });
});

phoneRouter.get('/basket/top', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.status(401).json({ ready: false });

  const basket = getCurrentBasket(ws);
  const rawItems = basket ? getBasketItems(ws, basket.id) : [];
  const board = basket && rawItems.length > 0 ? buildBoard(ws, basket.id) : null;
  const top = board ? { places: board.places } : { places: [] };

  const prices: Record<string, string> = {};
  if (board) {
    for (const [pid, val] of Object.entries(board.pricesByPid)) {
      prices[pid] = whole(val);
    }
  }

  res.render('phone/_live', { top, items: rawItems }, (err, html) => {
    if (err) return res.status(500).json({ ready: false });
    return res.json({ ready: true, html, prices });
  });
});

phoneRouter.get('/baskets', requirePhone, (_req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const current = getCurrentBasket(ws);
  const baskets = ws.baskets.map((b) => ({
    id: b.id,
    name: b.name,
    day: humanDay(b.created_at),
    count: getBasketItems(ws, b.id).length,
    open: Boolean(current && b.id === current.id),
  }));

  res.render('phone/baskets', {
    tab: 'basket',
    baskets,
  });
});

phoneRouter.post('/baskets/:id', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const id = Number(req.params.id);
  if (ws.baskets.some((b) => b.id === id)) {
    ws.lastBasketId = id;
  }
  res.redirect(303, '/phone/basket');
});

phoneRouter.get('/cheaper', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const basket = getCurrentBasket(ws);
  const rawItems = basket ? getBasketItems(ws, basket.id) : [];
  const pickRaw = req.query.pick !== undefined ? Number(req.query.pick) : null;
  const pick = pickRaw !== null && Number.isFinite(pickRaw) ? pickRaw : null;

  const board = basket && rawItems.length > 0 ? buildBoard(ws, basket.id, pick) : null;
  const chosenVariant = board?.chosenVariant || null;

  let chosen: Record<string, unknown> | null = null;
  if (chosenVariant && board) {
    const subtotal =
      Math.round(chosenVariant.stores.reduce((acc, s) => acc + s.subtotal, 0) * 100) / 100;
    const delivery =
      Math.round(chosenVariant.stores.reduce((acc, s) => acc + s.delivery, 0) * 100) / 100;
    const discount =
      Math.round(chosenVariant.stores.reduce((acc, s) => acc + s.discount, 0) * 100) / 100;

    const parts = chosenVariant.stores.map((s) => buildStorePart(ws, s));
    const orderPayload = {
      basket: basket!.id,
      stores: parts.map((p) => ({
        store: p.code,
        lines: JSON.parse(p.lines_json),
      })),
    };

    chosen = {
      title: chosenVariant.title,
      stores: chosenVariant.stores.length,
      subtotal,
      delivery,
      discount,
      total: chosenVariant.total,
      whole: chosenVariant.missing_products.length === 0,
      missing: chosenVariant.missing_products,
      live_at: board.bundle.when,
      net: null,
      parts,
      order_json: JSON.stringify(orderPayload),
    };
  }

  const unpriced = rawItems
    .filter((it) => {
      const m = ws.prices.get(it.product_id);
      return !m || m.size === 0;
    })
    .map((it) => it.name);

  res.render('phone/cheaper', {
    tab: 'basket',
    basket,
    variants: board?.bundle.variants || [],
    places: board?.places || [],
    more_places: board?.morePlaces || [],
    chosen,
    pick: board ? board.onIndex : null,
    no_address: !ws.address,
    sent: req.query.sent ? String(req.query.sent) : null,
    left: req.query.left ? Number(req.query.left) : 0,
    why: req.query.why ? String(req.query.why) : null,
    why_store: req.query.why_store ? String(req.query.why_store) : null,
    prices: null,
    building: null,
    unpriced,
  });
});

phoneRouter.post('/cheaper', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const storeCode = String(req.body.store || '').trim();
  const pick = req.body.pick !== undefined ? Number(req.body.pick) : null;
  const basket = getCurrentBasket(ws);
  if (!basket || !storeCode) {
    return res.redirect(303, '/phone/cheaper');
  }

  const board = buildBoard(ws, basket.id, pick !== null && Number.isFinite(pick) ? pick : null);
  const chosenVariant = board.chosenVariant;
  const part = chosenVariant?.stores.find((s) => s.store_code === storeCode);
  if (!part || !part.lines.length) {
    return res.redirect(
      303,
      `/phone/cheaper?why=gone&why_store=${encodeURIComponent(storeCode)}`
    );
  }

  if (KIND_BY_STORE[storeCode] === 'link') {
    const link = buildStoreLink(storeCode, part.lines);
    return res.redirect(302, link);
  }

  const conn = ws.connections.get(storeCode);
  if (!conn || !conn.connected) {
    return res.redirect(303, `/phone/connect/${encodeURIComponent(storeCode)}`);
  }

  const qs = new URLSearchParams({ sent: storeCode });
  if (pick !== null && Number.isFinite(pick)) qs.set('pick', String(pick));
  return res.redirect(303, `/phone/cheaper?${qs.toString()}`);
});

phoneRouter.get('/orders', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const current = getCurrentBasket(ws);
  const activeKey =
    current && ws.repeated && ws.repeated.basket === current.id ? ws.repeated.key : null;

  const allOrders = buildOrdersList(ws).map((o) => ({
    ...o,
    count: o.lines.length,
    thumbs: o.lines.slice(0, 5),
    thumbs_more: Math.max(0, o.lines.length - 5),
    head: o.lines.slice(0, 5),
    rest: o.lines.slice(5),
    in_basket: Boolean(activeKey && o.key === activeKey),
  }));

  const limit = Math.max(10, Math.min(200, Number(req.query.n || 20)));
  const shown = allOrders.slice(0, limit);
  const more = Math.max(0, allOrders.length - shown.length);

  res.render('phone/orders', {
    tab: 'basket',
    orders: shown,
    months: groupByMonth(shown),
    more,
    next_n: limit + 20,
  });
});

phoneRouter.post('/orders', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const key = String(req.body.key || '').trim();
  const order = buildOrdersList(ws).find((o) => o.key === key);
  if (!order) {
    return res.redirect(303, '/phone/orders');
  }

  const basket = createBasket(ws, `${order.store_name} · ${order.day}`, 'history');
  for (const ln of order.lines) {
    let pid = ln.product_id;
    if (!pid) {
      const id = ws.nextProductId++;
      ws.products.set(id, {
        id,
        name: ln.name,
        brand: null,
        weight_g: null,
        unit: 'pcs',
        category: 'Из заказа',
        active: true,
      });
      pid = id;
    }
    const existing = (ws.basketItems.get(basket.id) || []).find((i) => i.product_id === pid);
    setBasketItemQty(ws, basket.id, pid, (existing ? existing.qty : 0) + ln.qty);
  }

  ws.repeated = { key: order.key, basket: basket.id };
  ws.lastBasketId = basket.id;
  return res.redirect(303, '/phone/basket');
});

phoneRouter.get('/settings', requirePhone, (req, res) => {
  const { workspace: ws, isDemo } = authOf(res);
  if (!ws) return res.redirect('/login');

  const stores = PHONE_CHAINS.map((code) => {
    const store = getStore(code)!;
    const conn = ws.connections.get(code);
    return {
      code,
      name: store.name,
      connected: Boolean(conn?.connected),
      again: Boolean(conn?.again),
      last: conn?.last || null,
    };
  });

  const connected = stores.filter((s) => s.connected);
  const waiting = stores.filter((s) => !s.connected);

  const basket = getCurrentBasket(ws);
  const basketItems = basket ? getBasketItems(ws, basket.id) : [];
  const board = basket && basketItems.length > 0 ? buildBoard(ws, basket.id) : null;
  const boardBy: Record<string, { ready: boolean; sum: number | null; next: string | null; act: string | null }> = {};
  if (board) {
    for (const v of board.bundle.places) {
      if (v.stores.length === 1) {
        const s = v.stores[0];
        if (!boardBy[s.store_code]) {
          boardBy[s.store_code] = {
            ready: v.missing_products.length === 0,
            sum: s.total,
            next:
              v.missing_products.length > 0
                ? `не хватает ${v.missing_products.length}`
                : null,
            act: null,
          };
        }
      }
    }
  }

  const disconnectedCode = req.query.disconnected ? String(req.query.disconnected) : null;
  const connectedCode = req.query.connected ? String(req.query.connected) : null;
  const connectCode = req.query.connect ? String(req.query.connect) : null;

  res.render('phone/settings', {
    tab: 'settings',
    stores,
    connected,
    waiting,
    board_by: boardBy,
    no_stores: stores.length === 0,
    first_run: !isDemo && connected.length === 0,
    saved: req.query.saved ? String(req.query.saved) : null,
    edit: req.query.edit ? String(req.query.edit) : null,
    connect: connectCode,
    nearby_text: req.query.nearby ? String(req.query.nearby) : null,
    disconnected_name: disconnectedCode ? getStore(disconnectedCode)?.name || null : null,
    connected_name: connectedCode ? getStore(connectedCode)?.name || null : null,
    fns: {
      connected: ws.fnsConnected,
      label: ws.fnsConnected ? cheques(ws.receipts.length) : '',
    },
    point_text: ws.point ? `${ws.point[0].toFixed(6)},${ws.point[1].toFixed(6)}` : '',
    center: ws.point,
    map_open: req.query.map === '1',
    tile_base: '/phone/map/tile',
  });
});

phoneRouter.post('/settings', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const addr = String(req.body.address || '').trim();
  const ptRaw = String(req.body.point || '').trim();

  ws.address = addr || null;
  if (ptRaw) {
    const [latStr, lonStr] = ptRaw.split(',');
    const lat = Number(latStr);
    const lon = Number(lonStr);
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      ws.point = [lat, lon];
    }
  } else if (!addr) {
    ws.point = null;
  }

  res.redirect(303, '/phone/settings?saved=address#address');
});

phoneRouter.post('/settings/disconnect', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const store = String(req.body.store || '').trim();
  const conn = ws.connections.get(store);
  if (conn) {
    conn.connected = false;
    conn.again = false;
  }
  res.redirect(303, `/phone/settings?saved=disconnected&disconnected=${encodeURIComponent(store)}`);
});

phoneRouter.get('/connect/:code', requirePhone, (req, res) => {
  const code = String(req.params.code || '').trim();
  const store = getStore(code);
  const ua = req.get('user-agent') || '';

  res.render('phone/connect', {
    tab: 'settings',
    code,
    name: store ? store.name : code,
    android: /Android/i.test(ua),
  });
});

phoneRouter.post('/connect/:code', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const code = String(req.params.code || '').trim();
  const conn = ws.connections.get(code);
  if (conn) {
    conn.connected = true;
    conn.again = false;
    conn.last = new Date().toISOString().slice(0, 16).replace('T', ' ');
  }
  res.redirect(303, `/phone/settings?connected=${encodeURIComponent(code)}`);
});

phoneRouter.get('/receipts', requirePhone, (req, res) => {
  const { workspace: ws, isDemo } = authOf(res);
  if (!ws) return res.redirect('/login');

  const totalSum = Math.round(ws.history.reduce((acc, r) => acc + r.total, 0) * 100) / 100;
  const dates = ws.history.map((h) => h.date).sort();
  let months = 0;
  if (dates.length >= 2) {
    const d1 = new Date(dates[0]);
    const d2 = new Date(dates[dates.length - 1]);
    months = Math.max(
      1,
      (d2.getFullYear() - d1.getFullYear()) * 12 + (d2.getMonth() - d1.getMonth()) + 1
    );
  } else if (dates.length === 1) {
    months = 1;
  }

  const flashes: Array<[string, string]> = [];
  const flashCode = String(req.query.flash || '');
  if (flashCode === 'connected') flashes.push(['ok', 'Кабинет ФНС подключён.']);
  if (flashCode === 'synced') flashes.push(['ok', 'Новые чеки проверены.']);
  if (flashCode === 'disconnected') flashes.push(['ok', 'Кабинет ФНС отключён. Загруженные чеки остались в истории.']);
  if (flashCode === 'badkey') flashes.push(['bad', 'Ключ не похож на JSON кабинета ФНС.']);

  res.render('phone/receipts', {
    tab: 'settings',
    demo: isDemo,
    connected: ws.fnsConnected,
    connected_phone: ws.fnsPhone,
    synced: ws.fnsSyncedAt,
    last: null,
    have: {
      receipts: ws.receipts.length,
      rows: ws.history.length,
      total: totalSum,
      months,
    },
    left: { receipts: 0 },
    loose: 0,
    headline: ws.receipts.length
      ? `В истории ${cheques(ws.receipts.length)} на ${rub(totalSum)}`
      : 'Чеков пока нет',
    job: null,
    refresh: 5,
    full_url: '/receipts',
    flashes,
  });
});

phoneRouter.post('/receipts', requirePhone, (req, res) => {
  const { workspace: ws, phoneLabel } = authOf(res);
  if (!ws) return res.redirect('/login');

  const action = String(req.body.do || '').trim();
  if (action === 'disconnect') {
    ws.fnsConnected = false;
    return res.redirect(303, '/phone/receipts?flash=disconnected');
  }
  if (action === 'sync') {
    ws.fnsSyncedAt = new Date().toISOString().slice(0, 16).replace('T', ' ');
    return res.redirect(303, '/phone/receipts?flash=synced');
  }
  if (action === 'keys') {
    const raw = String(req.body.keys || '').trim();
    if (!raw || raw.length < 4) {
      return res.redirect(303, '/phone/receipts?flash=badkey');
    }
    ws.fnsConnected = true;
    ws.fnsPhone = phoneLabel || ws.phone;
    ws.fnsSyncedAt = new Date().toISOString().slice(0, 16).replace('T', ' ');
    return res.redirect(303, '/phone/receipts?flash=connected');
  }

  return res.redirect(303, '/phone/receipts');
});

export async function suggestAddressItems(query: string): Promise<string[]> {
  const q = query.trim();
  if (q.length < 3) return [];
  const low = q.toLowerCase();
  const local = FALLBACK_ADDRESSES.filter((a) => a.toLowerCase().includes(low));

  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 3000);
    const url =
      `https://nominatim.openstreetmap.org/search?format=json&addressdetails=1&countrycodes=ru&limit=6&accept-language=ru&q=${encodeURIComponent(q)}`;
    const r = await fetch(url, {
      headers: { 'User-Agent': 'moya-korzina/1.0 (personal-grocery-app)' },
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!r.ok) return local;
    const rows = (await r.json()) as Array<{ display_name?: string }>;
    const items = rows
      .map((x) => String(x.display_name || '').trim())
      .filter(Boolean);
    return items.length ? items.slice(0, 6) : local;
  } catch {
    return local;
  }
}

phoneRouter.get('/address/suggest', requirePhone, async (req, res) => {
  const q = String(req.query.q || '').trim();
  const items = await suggestAddressItems(q);
  res.json({ items });
});

phoneRouter.get('/address/reverse', requirePhone, async (req, res) => {
  const lat = Number(req.query.lat);
  const lon = Number(req.query.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return res.json({ address: null });
  }
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 3500);
    const url =
      `https://nominatim.openstreetmap.org/reverse?format=json&accept-language=ru&lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}`;
    const r = await fetch(url, {
      headers: { 'User-Agent': 'moya-korzina/1.0 (personal-grocery-app)' },
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!r.ok) return res.json({ address: null });
    const data = (await r.json()) as {
      address?: Record<string, string>;
      display_name?: string;
    };
    const a = data.address;
    if (a) {
      const city = a.city || a.town || a.village || a.state;
      const road = a.road || a.pedestrian;
      const house = a.house_number;
      const compact = [city, road, house].filter(Boolean).join(', ');
      return res.json({ address: compact || data.display_name || null });
    }
    return res.json({ address: data.display_name || null });
  } catch {
    return res.json({ address: null });
  }
});

phoneRouter.get('/map/tile/:z/:x/:y.png', requirePhone, async (req, res) => {
  const z = Number(req.params.z);
  const x = Number(req.params.x);
  const y = Number(req.params.y);
  if (
    !Number.isInteger(z) ||
    !Number.isInteger(x) ||
    !Number.isInteger(y) ||
    z < 0 ||
    z > 19 ||
    x < 0 ||
    y < 0
  ) {
    return res.status(400).end();
  }

  const key = `${z}/${x}/${y}`;
  const cached = tileCache.get(key);
  if (cached && Date.now() - cached.at < 7 * 86400_000) {
    res.setHeader('Content-Type', 'image/png');
    res.setHeader('Cache-Control', 'public, max-age=604800');
    return res.send(cached.buf);
  }

  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4500);
    const r = await fetch(`https://tile.openstreetmap.org/${z}/${x}/${y}.png`, {
      headers: { 'User-Agent': 'moya-korzina/1.0 (personal-grocery-app)' },
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!r.ok) return res.status(502).end();
    const buf = Buffer.from(await r.arrayBuffer());
    // Check PNG magic header
    if (buf.length < 8 || buf[0] !== 0x89 || buf[1] !== 0x50 || buf[2] !== 0x4e || buf[3] !== 0x47) {
      return res.status(502).end();
    }
    if (tileCache.size > 400) {
      const oldestKey = tileCache.keys().next().value;
      if (oldestKey) tileCache.delete(oldestKey);
    }
    tileCache.set(key, { buf, at: Date.now() });
    res.setHeader('Content-Type', 'image/png');
    res.setHeader('Cache-Control', 'public, max-age=604800');
    return res.send(buf);
  } catch {
    return res.status(502).end();
  }
});

// Keep helpers available for templates via res.locals
export { where };
