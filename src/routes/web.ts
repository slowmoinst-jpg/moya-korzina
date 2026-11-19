import express from 'express';
import {
  buildDesktopBasketView,
  buildOrdersList,
  buildStoreLink,
  calculateBasket,
  createBasket,
  DEMO_PHONE,
  getBasketItems,
  getCurrentBasket,
  getHabits,
  getStore,
  importManualReceipt,
  PHONE_CHAINS,
  resolveLogin,
  searchProducts,
  SEED_STORES,
  setBasketItemQty,
  stepForUnit,
} from '../data/store.js';
import { cheques, humanDay, rub } from '../helpers.js';
import {
  checkLoginRateLimit,
  checkPin,
  pinIsSet,
  recordLoginFailure,
  requirePhone,
  safeNextPath,
  signInCookie,
  signOutCookie,
  type AuthLocals,
} from '../middleware/auth.js';
import {
  chooseBestVariant,
  KIND_BY_STORE,
} from '../optimizer.js';
import type { ScreenMeta, Store, Variant } from '../types.js';
import { suggestAddressItems } from './phone.js';

export const webRouter = express.Router();

export const SCREENS: ScreenMeta[] = [
  { key: 'home', path: '/', title: 'Главная', eyebrow: 'Что это и четыре настройки', section: 'Настройка' },
  { key: 'stores', path: '/stores', title: 'Магазины', eyebrow: 'Откуда берутся цены', section: 'Настройка' },
  { key: 'accounts', path: '/accounts', title: 'Кабинеты', eyebrow: 'Вход в свои аккаунты сетей', section: 'Настройка' },
  { key: 'receipts', path: '/receipts', title: 'Мои чеки', eyebrow: 'Загрузка из сервиса ФНС', section: 'Настройка' },
  { key: 'cards', path: '/cards', title: 'Карты и акции', eyebrow: 'Условия банков и магазинов', section: 'Настройка' },
  { key: 'about', path: '/about', title: 'О продукте', eyebrow: 'Что умеет и чего не умеет', section: 'Настройка' },

  { key: 'basket', path: '/basket', title: 'Корзина', eyebrow: 'Что покупаем', section: 'Корзина' },
  { key: 'result', path: '/result', title: 'Результат', eyebrow: 'Разбиение и экономия', section: 'Корзина' },
  { key: 'carts', path: '/carts', title: 'Сборка корзин', eyebrow: 'Корзина в каждой подключённой сети', section: 'Корзина' },
  { key: 'orders', path: '/orders', title: 'Заказы', eyebrow: 'Покупки из чеков ФНС и добавленные вручную', section: 'Корзина' },
  { key: 'history', path: '/history', title: 'История', eyebrow: 'Покупки семьи', section: 'Корзина' },

  { key: 'products', path: '/products', title: 'Товары', eyebrow: 'Что покупаем и где это лежит', section: 'Каталог' },
  { key: 'prices', path: '/prices', title: 'Цены', eyebrow: 'Что отдают сети прямо сейчас', section: 'Каталог' },
  { key: 'compare', path: '/compare', title: 'Сравнение', eyebrow: 'Один товар — все доставки', section: 'Каталог' },

  { key: 'links', path: '/links', title: 'Связи', eyebrow: 'Один товар — разные названия в сетях', section: 'Связи' },
];

export const SECTIONS = ['Настройка', 'Корзина', 'Каталог', 'Связи'] as const;
export const SCREEN_BY_KEY = new Map(SCREENS.map((s) => [s.key, s]));

export function screensOf(section: string): ScreenMeta[] {
  return SCREENS.filter((s) => s.section === section);
}

function authOf(res: express.Response): AuthLocals {
  return res.locals.auth as AuthLocals;
}

webRouter.get('/api/health', (_req, res) => {
  res.json({ ok: true });
});

webRouter.get('/login', (req, res) => {
  const { phone } = authOf(res);
  const next = safeNextPath(req.query.next);
  if (phone) return res.redirect(next);
  res.render('web/login', {
    screen: null,
    next,
    typed: '',
    error: null,
    pin_required: pinIsSet(),
  });
});

webRouter.post('/login', (req, res) => {
  const next = safeNextPath(req.body.next);
  const typed = String(req.body.phone || '');
  const pin = String(req.body.pin || '');

  if (checkLoginRateLimit(req)) {
    return res.render('web/login', {
      screen: null,
      next,
      typed,
      error: 'Слишком много неверных попыток. Попробуйте через 15 минут.',
      pin_required: pinIsSet(),
    });
  }

  const [phone, err] = resolveLogin(typed);
  if (err || !phone) {
    recordLoginFailure(req);
    return res.render('web/login', {
      screen: null,
      next,
      typed,
      error: err || 'Проверьте номер телефона',
      pin_required: pinIsSet(),
    });
  }

  if (!checkPin(pin)) {
    recordLoginFailure(req);
    return res.render('web/login', {
      screen: null,
      next,
      typed,
      error: 'Неверный PIN установки',
      pin_required: pinIsSet(),
    });
  }

  signInCookie(res, phone);
  return res.redirect(303, next);
});

webRouter.post('/demo', (req, res) => {
  const next = safeNextPath(req.body.next || '/');
  signInCookie(res, DEMO_PHONE);
  return res.redirect(303, next);
});

webRouter.post('/logout', (_req, res) => {
  signOutCookie(res);
  return res.redirect(303, '/login');
});

webRouter.get('/', requirePhone, (req, res) => {
  const { workspace: ws, devicePhone } = authOf(res);
  if (!ws) return res.redirect('/login');

  if (devicePhone && req.query.web !== '1') {
    return res.redirect('/phone/basket');
  }

  const connectedCount = [...ws.connections.values()].filter((c) => c.connected).length;
  const basket = getCurrentBasket(ws);
  const checked =
    req.query.check === '1' && ws.address
      ? [
          { chain: 'Лента', shop: 'Гипермаркет · по адресу доставки', far: null },
          { chain: 'Магнит', shop: 'Магнит у дома · ближайший к адресу', far: '0,4 км' },
          { chain: 'ВкусВилл', shop: 'Даркстор доставки', far: '1,1 км' },
          { chain: 'Пятёрочка', shop: 'Универсам · рядом с адресом', far: '0,3 км' },
        ]
      : null;

  const steps = [
    {
      n: 2,
      title: 'Чеки и покупки',
      done: ws.receipts.length > 0,
      now: ws.receipts.length
        ? `${cheques(ws.receipts.length)}, ${ws.history.length} строк`
        : 'Чеки не загружены',
      why: 'Из чеков собираются привычные товары и базовая цена, с которой сравниваем экономию.',
      href: '/receipts',
    },
    {
      n: 3,
      title: 'Карты и кэшбэк',
      done: ws.offers.length > 0,
      now: ws.offers.length
        ? `${ws.cards.length} карт, ${ws.offers.length} акций`
        : 'Кэшбэк банков не задан',
      why: 'Учитываем повышенный кэшбэк и лимиты по каждой карте при делении корзины.',
      href: '/cards',
    },
    {
      n: 4,
      title: 'Магазины и кабинеты',
      done: connectedCount > 0,
      now: connectedCount
        ? `Подключено сетей: ${connectedCount}`
        : 'Ни одна сеть не подключена',
      why: 'Показывает точные цены вашей точки и позволяет положить корзину в кабинет сети.',
      href: '/accounts',
    },
    {
      n: 5,
      title: 'Корзина и расчёт',
      done: Boolean(basket && getBasketItems(ws, basket.id).length > 0),
      now: basket
        ? `${basket.name} · ${getBasketItems(ws, basket.id).length} поз.`
        : 'Корзина пока не создана',
      why: 'Наберите список руками или повторите прошлый заказ — сразу увидите выгодный вариант.',
      href: '/basket',
    },
  ];

  res.render('web/home', {
    screen: SCREEN_BY_KEY.get('home'),
    saved: req.query.saved === '1',
    note: null,
    checked,
    steps,
    ready: Boolean(ws.address && ws.receipts.length > 0 && ws.offers.length > 0),
    fresh: req.query.saved === '1' ? basket : null,
  });
});

webRouter.post('/address', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');
  const addr = String(req.body.address || '').trim();
  ws.address = addr || null;
  res.redirect(303, '/?saved=1#address');
});

webRouter.get('/address/suggest', requirePhone, async (req, res) => {
  const q = String(req.query.q || '').trim();
  const items = await suggestAddressItems(q);
  res.json({ items });
});

webRouter.get('/basket', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const explicitId = req.query.id ? Number(req.query.id) : null;
  const basket = getCurrentBasket(ws, Number.isFinite(explicitId) ? explicitId : null);
  const missing =
    explicitId && Number.isFinite(explicitId) && !ws.baskets.some((b) => b.id === explicitId)
      ? explicitId
      : null;

  const query = String(req.query.q || '').trim();
  const habit = req.query.f === 'due' ? 'due' : 'often';

  const latestReceipt = ws.receipts[0];
  const allHabits = getHabits(ws, basket ? basket.id : 0, 14);
  const due = allHabits
    .filter((h) => h.why === 'пора докупить')
    .map((h) => ({ product_id: h.pid, ...h }));
  const regulars = allHabits
    .filter((h) => h.why === 'часто берёте')
    .map((h) => ({ product_id: h.pid, ...h }));

  const view = basket ? buildDesktopBasketView(ws, basket.id) : null;
  const matches = query.length >= 2 ? searchProducts(ws, query, 20) : [];
  const suggestions = matches.map((m) => ({
    id: m.id,
    name: m.name,
    unit: m.unit === 'kg' ? 'кг' : 'шт',
    brand: m.brand,
    ready: m.chainsCount,
  }));

  res.render('web/basket', {
    screen: SCREEN_BY_KEY.get('basket'),
    missing,
    basket,
    baskets: ws.baskets,
    usual: {
      count: ws.history.length,
      date: latestReceipt ? humanDay(latestReceipt.date) : '',
      mine: Math.min(allHabits.length, 10),
    },
    query,
    habit,
    due,
    regulars,
    suggestions,
    suggestions_n: suggestions.length,
    stores_n: SEED_STORES.length,
    items: view ? view.items : [],
    approx: view ? view.approx : { total: 0, priced: 0, blind: 0, count: 0 },
    totals: view ? view.totals : [],
    job: null,
  });
});

webRouter.get('/basket/suggest', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.status(401).json({ q: '', items: [] });

  const q = String(req.query.q || '').trim();
  const items =
    q.length >= 2
      ? searchProducts(ws, q, 12).map((h) => ({
          id: h.id,
          name: h.name,
          small: h.small,
          do: h.doVal,
        }))
      : [];
  res.json({ q, items });
});

webRouter.post('/basket', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const action = String(req.body.do || '').trim();
  const q = String(req.body.q || '').trim();
  const explicitId = req.body.basket ? Number(req.body.basket) : null;
  let basket = getCurrentBasket(ws, Number.isFinite(explicitId) ? explicitId : null);

  // Sync typed qty inputs first
  if (basket) {
    for (const [k, v] of Object.entries(req.body || {})) {
      if (k.startsWith('qty:')) {
        const pid = Number(k.slice(4));
        const val = Number(String(v).replace(',', '.'));
        if (Number.isFinite(pid) && Number.isFinite(val)) {
          setBasketItemQty(ws, basket.id, pid, val);
        }
      }
    }
  }

  if (action === 'new' || action.startsWith('new:')) {
    const sub = action.split(':')[1] || 'manual';
    const customName = String(req.body.name || '').trim();
    const label =
      customName ||
      (sub === 'history'
        ? 'По прошлой покупке'
        : sub === 'average'
          ? 'По среднему за месяц'
          : sub === 'usual'
            ? 'Обычная корзина'
            : `Корзина #${ws.nextBasketId}`);
    basket = createBasket(ws, label, sub === 'average' ? 'average' : 'manual');
    if (sub === 'history' || sub === 'average' || sub === 'usual') {
      const seen = new Set<number>();
      for (const h of ws.history) {
        if (h.product_id && !seen.has(h.product_id)) {
          seen.add(h.product_id);
          setBasketItemQty(ws, basket.id, h.product_id, h.qty);
        }
      }
    }
  } else if (action.startsWith('fill:')) {
    if (!basket) basket = createBasket(ws, 'Корзина', 'manual');
    const seen = new Set<number>();
    for (const h of ws.history) {
      if (h.product_id && !seen.has(h.product_id)) {
        seen.add(h.product_id);
        setBasketItemQty(ws, basket.id, h.product_id, h.qty);
      }
    }
  } else if (action === 'clear' && basket) {
    ws.basketItems.set(basket.id, []);
  } else if (action === 'calc' || action === 'fresh') {
    const idParam = basket ? `?basket=${basket.id}` : '';
    return res.redirect(303, `/result${idParam}`);
  } else if (action.includes(':')) {
    if (!basket) basket = createBasket(ws, 'Корзина', 'manual');
    const [verb, pidStr] = action.split(':');
    const pid = Number(pidStr);
    if (Number.isFinite(pid) && pid > 0) {
      const prod = ws.products.get(pid);
      const step = stepForUnit(prod?.unit);
      const existing = (ws.basketItems.get(basket.id) || []).find((i) => i.product_id === pid);
      const cur = existing ? existing.qty : 0;
      if (verb === 'add' || verb === 'catalog' || verb === 'plus') {
        setBasketItemQty(ws, basket.id, pid, cur + step);
      } else if (verb === 'minus') {
        setBasketItemQty(ws, basket.id, pid, cur <= step + 1e-6 ? 0 : cur - step);
      } else if (verb === 'drop') {
        setBasketItemQty(ws, basket.id, pid, 0);
      }
    }
  }

  const params = new URLSearchParams();
  if (basket) params.set('id', String(basket.id));
  if (q) params.set('q', q);
  return res.redirect(303, `/basket?${params.toString()}`);
});

function formatDesktopVariant(
  ws: NonNullable<AuthLocals['workspace']>,
  variant: Variant,
  rankIndex: number,
  bundlePositions: number,
  fromReceiptsCount: number
) {
  const base = variant.baseline || variant.total;
  const saved = Math.max(0, Math.round((base - variant.total) * 100) / 100);
  const pct = base > 0 ? Math.round((saved / base) * 1000) / 10 : 0;
  const paidWidth = base > 0 ? Math.min(100, Math.max(0, Math.round((variant.total / base) * 100))) : 100;
  const savedWidth = Math.max(0, 100 - paidWidth);
  const handoverNotes: Record<string, { openLabel: string; note: string }> = {
    link: {
      openLabel: 'открыть →',
      note: 'Сеть формирует корзину по прямой ссылке.',
    },
    items: {
      openLabel: 'найти →',
      note: 'Товары открываются на сайте или в приложении сети.',
    },
    search: {
      openLabel: 'поиск →',
      note: 'Откройте поиск по названию товара в магазине.',
    },
    list: {
      openLabel: 'открыть →',
      note: 'Список позиций для ручной сборки.',
    },
  };

  const stores = variant.stores.map((s) => {
    const conn = ws.connections.get(s.store_code);
    const kind = KIND_BY_STORE[s.store_code] || 'list';
    const hoMeta = handoverNotes[kind] || handoverNotes.list;
    const handoverLines = s.lines.map((l) => ({
      name: l.product_name,
      qty: l.qty,
      unit: l.unit === 'kg' ? 'кг' : 'шт',
      url: `https://${s.store_code}.ru/search?q=${encodeURIComponent(l.product_name)}`,
    }));

    return {
      code: s.store_code,
      name: s.store_name,
      subtotal: s.subtotal,
      delivery: s.delivery,
      discount: s.discount,
      total: s.total,
      card_name: s.card_name,
      below_min: s.below_min_order,
      asks_link: kind === 'link',
      trouble: false,
      going: null,
      resend: null,
      open_label: hoMeta.openLabel,
      note: hoMeta.note,
      handover: handoverLines,
      text: handoverLines.map((x) => `${x.name} — ${x.qty} ${x.unit}`).join('\n'),
      lines: s.lines.map((l) => ({
        name: l.product_name,
        qty: l.qty,
        unit: l.unit === 'kg' ? 'кг' : 'шт',
        price: l.price,
        note: l.note,
      })),
      plan: conn?.connected
        ? {
            can_fill: true,
            total: s.total,
            lines: s.lines.map((l) => ({
              name: l.product_name,
              qty: l.qty,
              unit: l.unit === 'kg' ? 'кг' : 'шт',
              price: l.price,
              rounded: null,
            })),
            unknown: [],
            note: null,
          }
        : null,
    };
  });

  const have = Math.max(0, bundlePositions - variant.missing_products.length);
  return {
    n: rankIndex,
    variant,
    stores,
    penalty: variant.penalty,
    complete: { have, of: bundlePositions, full: variant.missing_products.length === 0 },
    missing: variant.missing_products,
    figures: { total: variant.total, saved, pct, base, paid_width: paidWidth, saved_width: savedWidth },
    base: {
      label: fromReceiptsCount === bundlePositions ? 'По вашим чекам' : 'Средняя база',
      mixed: fromReceiptsCount > 0 && fromReceiptsCount < bundlePositions,
      unseen: bundlePositions - fromReceiptsCount,
      of: bundlePositions,
    },
  };
}

webRouter.get('/result', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const explicitId = req.query.basket ? Number(req.query.basket) : null;
  const basket = getCurrentBasket(ws, Number.isFinite(explicitId) ? explicitId : null);
  const rawItems = basket ? getBasketItems(ws, basket.id) : [];

  if (!basket || rawItems.length === 0) {
    return res.render('web/result', {
      screen: SCREEN_BY_KEY.get('result'),
      basket,
      variants: [],
      mode: null,
      bundle: null,
      best: null,
      others: [],
      switchInfo: null,
      effortless: null,
      doubts: null,
    });
  }

  const bundle = calculateBasket(ws, basket.id);
  const modeParam = req.query.mode === 'single' || req.query.mode === 'split' ? req.query.mode : null;
  const { best: pickedVariant, switchInfo } = chooseBestVariant(
    bundle.variants.length ? bundle.variants : bundle.places,
    modeParam
  );

  const fromReceiptsCount = bundle.fromReceipts.size;
  const best = formatDesktopVariant(ws, pickedVariant, 1, bundle.positions, fromReceiptsCount);

  const others = bundle.places
    .filter((v) => v !== pickedVariant)
    .slice(0, 6)
    .map((v, i) => formatDesktopVariant(ws, v, i + 2, bundle.positions, fromReceiptsCount));

  const modeResolved = switchInfo ? (switchInfo.split.on ? 'split' : 'single') : modeParam;

  res.render('web/result', {
    screen: SCREEN_BY_KEY.get('result'),
    basket,
    variants: bundle.variants,
    mode: modeResolved,
    bundle,
    best,
    others,
    switchInfo,
    effortless: null,
    doubts: null,
  });
});

webRouter.post('/result', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const basketId = Number(req.body.basket || 0);
  const mode = String(req.body.mode || '');
  const action = String(req.body.do || '');

  if (action.startsWith('link:')) {
    const storeCode = action.slice(5);
    const basket = getCurrentBasket(ws, basketId || null);
    if (basket) {
      const bundle = calculateBasket(ws, basket.id);
      const { best } = chooseBestVariant(
        bundle.variants.length ? bundle.variants : bundle.places,
        mode === 'single' || mode === 'split' ? mode : null
      );
      const st = best.stores.find((s) => s.store_code === storeCode);
      if (st && KIND_BY_STORE[storeCode] === 'link') {
        return res.redirect(302, buildStoreLink(storeCode, st.lines));
      }
      if (st) {
        return res.redirect(303, `/accounts?store=${encodeURIComponent(storeCode)}&sent=1`);
      }
    }
  }

  const params = new URLSearchParams();
  if (basketId) params.set('basket', String(basketId));
  if (mode) params.set('mode', mode);
  return res.redirect(303, `/result?${params.toString()}`);
});

webRouter.get('/orders', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const orders = buildOrdersList(ws);
  res.render('web/orders', {
    screen: SCREEN_BY_KEY.get('orders'),
    orders,
  });
});

webRouter.post('/orders', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const key = String(req.body.key || '');
  const order = buildOrdersList(ws).find((o) => o.key === key);
  if (order) {
    const b = createBasket(ws, `${order.store_name} · ${order.day}`, 'history');
    for (const ln of order.lines) {
      if (ln.product_id) {
        setBasketItemQty(ws, b.id, ln.product_id, ln.qty);
      }
    }
    ws.lastBasketId = b.id;
    ws.repeated = { key: order.key, basket: b.id };
    return res.redirect(303, `/basket?id=${b.id}`);
  }
  return res.redirect(303, '/orders');
});

webRouter.get('/history', requirePhone, (_req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  res.render('web/history', {
    screen: SCREEN_BY_KEY.get('history'),
    rows: ws.history.slice(0, 120),
    total: Math.round(ws.history.reduce((a, b) => a + b.total, 0) * 100) / 100,
  });
});

webRouter.get('/receipts', requirePhone, (req, res) => {
  const { workspace: ws, isDemo } = authOf(res);
  if (!ws) return res.redirect('/login');

  res.render('web/receipts', {
    screen: SCREEN_BY_KEY.get('receipts'),
    receipts: ws.receipts,
    stores: SEED_STORES,
    fnsConnected: ws.fnsConnected,
    fnsPhone: ws.fnsPhone,
    fnsSyncedAt: ws.fnsSyncedAt,
    isDemo,
    note: req.query.note ? String(req.query.note) : null,
    err: req.query.err ? String(req.query.err) : null,
  });
});

webRouter.post('/receipts', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const action = String(req.body.do || '');
  if (action === 'paste') {
    const storeCode = String(req.body.store || 'pyaterochka');
    const text = String(req.body.text || '');
    const out = importManualReceipt(ws, storeCode, text);
    if (out.error) {
      return res.redirect(303, `/receipts?err=${encodeURIComponent(out.error)}`);
    }
    return res.redirect(
      303,
      `/receipts?note=${encodeURIComponent(`Загружено строк: ${out.imported} на ${rub(out.total)}`)}`
    );
  }
  if (action === 'sync') {
    ws.fnsConnected = true;
    ws.fnsSyncedAt = new Date().toISOString().slice(0, 16).replace('T', ' ');
    return res.redirect(303, '/receipts?note=' + encodeURIComponent('Чеки обновлены'));
  }
  return res.redirect(303, '/receipts');
});

webRouter.get('/cards', requirePhone, (_req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const cardById = new Map(ws.cards.map((c) => [c.id, c]));
  const storeById = new Map(SEED_STORES.map((s) => [s.id, s]));

  const offersView = ws.offers.map((o) => {
    const c = cardById.get(o.card_id);
    const s = storeById.get(o.store_id);
    return {
      ...o,
      bank: c?.bank || 'Банк',
      card_name: c?.name || 'Карта',
      store_name: s?.name || 'Магазин',
      store_code: s?.code || '',
    };
  });

  res.render('web/cards', {
    screen: SCREEN_BY_KEY.get('cards'),
    cards: ws.cards,
    offers: offersView,
    stores: SEED_STORES,
  });
});

webRouter.post('/cards', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const bank = String(req.body.bank || '').trim();
  const cardName = String(req.body.name || '').trim();
  const storeId = Number(req.body.store_id || 1);
  const percent = Number(req.body.percent || 5);
  const capRub = Number(req.body.cap_rub || 500);
  const minCheck = Number(req.body.min_check_rub || 0);

  if (bank && cardName && Number.isFinite(percent) && percent > 0) {
    let card = ws.cards.find(
      (c) => c.bank.toLowerCase() === bank.toLowerCase() && c.name.toLowerCase() === cardName.toLowerCase()
    );
    if (!card) {
      card = { id: ws.cards.length + 1, bank, name: cardName };
      ws.cards.push(card);
    }
    const now = new Date();
    ws.offers.push({
      id: ws.offers.length + 1,
      card_id: card.id,
      store_id: storeId,
      percent,
      cap_rub: Math.max(0, capRub),
      min_check_rub: Math.max(0, minCheck),
      valid_from: new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10),
      valid_to: new Date(now.getFullYear(), now.getMonth() + 2, 0).toISOString().slice(0, 10),
      requires_activation: false,
      activated: true,
      cap_used: 0,
    });
  }

  return res.redirect(303, '/cards');
});

webRouter.get('/stores', requirePhone, (_req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const stores = SEED_STORES.map((s: Store) => {
    const conn = ws.connections.get(s.code);
    return {
      ...s,
      connected: Boolean(conn?.connected),
    };
  });

  res.render('web/stores', {
    screen: SCREEN_BY_KEY.get('stores'),
    stores,
  });
});

webRouter.get(['/accounts', '/cabinet'], requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const highlight = req.query.store ? String(req.query.store) : null;
  const stores = PHONE_CHAINS.map((code) => {
    const st = getStore(code)!;
    const conn = ws.connections.get(code);
    return {
      code,
      name: st.name,
      connected: Boolean(conn?.connected),
      again: Boolean(conn?.again),
      last: conn?.last || null,
    };
  });

  res.render('web/accounts', {
    screen: SCREEN_BY_KEY.get('accounts'),
    stores,
    highlight,
    sent: req.query.sent === '1',
  });
});

webRouter.post(['/accounts', '/cabinet'], requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const code = String(req.body.store || '').trim();
  const action = String(req.body.do || '').trim();
  const conn = ws.connections.get(code);
  if (conn) {
    if (action === 'connect') {
      conn.connected = true;
      conn.again = false;
      conn.last = new Date().toISOString().slice(0, 16).replace('T', ' ');
    } else if (action === 'disconnect') {
      conn.connected = false;
      conn.again = false;
    }
  }
  return res.redirect(303, '/accounts');
});

webRouter.get('/carts', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const explicitId = req.query.basket ? Number(req.query.basket) : null;
  const basket = getCurrentBasket(ws, Number.isFinite(explicitId) ? explicitId : null);
  const bundle = basket ? calculateBasket(ws, basket.id) : null;

  const singleTotals = new Map<string, Variant>();
  if (bundle) {
    for (const v of bundle.places) {
      if (v.stores.length === 1) {
        singleTotals.set(v.stores[0].store_code, v);
      }
    }
  }

  const chains = PHONE_CHAINS.map((code) => {
    const st = getStore(code)!;
    const conn = ws.connections.get(code);
    const v = singleTotals.get(code) || null;
    return {
      code,
      name: st.name,
      connected: Boolean(conn?.connected),
      total: v ? v.total : null,
      have: v ? v.stores[0].lines.length : 0,
      missing: v ? v.missing_products.length : 0,
    };
  });

  res.render('web/carts', {
    screen: SCREEN_BY_KEY.get('carts'),
    basket,
    chains,
    sentAll: req.query.all === '1',
  });
});

webRouter.post('/carts', requirePhone, (req, res) => {
  const basketId = Number(req.body.basket || 0);
  return res.redirect(303, `/carts?all=1${basketId ? `&basket=${basketId}` : ''}`);
});

webRouter.get('/products', requirePhone, (req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const q = String(req.query.q || '').trim().toLowerCase();
  const rows = [...ws.products.values()]
    .filter((p) => !q || p.name.toLowerCase().includes(q) || (p.category || '').toLowerCase().includes(q))
    .map((p) => {
      const m = ws.prices.get(p.id);
      const vals = m ? [...m.values()].filter((x) => x.in_stock).map((x) => x.price) : [];
      return {
        ...p,
        storesCount: m ? m.size : 0,
        minPrice: vals.length ? Math.min(...vals) : null,
        maxPrice: vals.length ? Math.max(...vals) : null,
      };
    });

  res.render('web/products', {
    screen: SCREEN_BY_KEY.get('products'),
    q,
    products: rows,
  });
});

webRouter.get('/prices', requirePhone, (_req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const rows: Array<{
    product: string;
    store_code: string;
    store_name: string;
    raw_name: string;
    price: number;
    in_stock: boolean;
    source: string;
  }> = [];

  for (const [pid, m] of ws.prices.entries()) {
    const p = ws.products.get(pid);
    if (!p) continue;
    for (const [code, snap] of m.entries()) {
      rows.push({
        product: p.name,
        store_code: code,
        store_name: getStore(code)?.name || code,
        raw_name: snap.raw_name,
        price: snap.price,
        in_stock: snap.in_stock,
        source: snap.source,
      });
    }
  }

  res.render('web/prices', {
    screen: SCREEN_BY_KEY.get('prices'),
    rows: rows.slice(0, 120),
    totalRows: rows.length,
  });
});

webRouter.get('/compare', requirePhone, (_req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const rows = [...ws.products.values()]
    .filter((p) => ws.prices.has(p.id))
    .map((p) => {
      const m = ws.prices.get(p.id)!;
      const byCode: Record<string, { price: number; in_stock: boolean }> = {};
      let minP = Infinity;
      for (const [code, s] of m.entries()) {
        byCode[code] = { price: s.price, in_stock: s.in_stock };
        if (s.in_stock && s.price < minP) minP = s.price;
      }
      return { id: p.id, name: p.name, byCode, minP: Number.isFinite(minP) ? minP : null };
    });

  res.render('web/compare', {
    screen: SCREEN_BY_KEY.get('compare'),
    stores: SEED_STORES.slice(0, 7),
    rows,
  });
});

webRouter.get('/links', requirePhone, (_req, res) => {
  const { workspace: ws } = authOf(res);
  if (!ws) return res.redirect('/login');

  const rows: Array<{ product: string; matches: Array<{ code: string; name: string; raw_name: string; sku: string }> }> = [];
  for (const p of ws.products.values()) {
    const m = ws.prices.get(p.id);
    if (!m || m.size === 0) continue;
    const matches = [...m.entries()].map(([code, snap]) => ({
      code,
      name: getStore(code)?.name || code,
      raw_name: snap.raw_name,
      sku: snap.sku,
    }));
    rows.push({ product: p.name, matches });
  }

  res.render('web/links', {
    screen: SCREEN_BY_KEY.get('links'),
    rows,
  });
});

webRouter.get('/about', requirePhone, (_req, res) => {
  res.render('web/about', {
    screen: SCREEN_BY_KEY.get('about'),
  });
});
