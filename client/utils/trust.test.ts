import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildTrustProfile,
  estimateSingleStoreWithReplacements,
  rankTrustedReplacements,
} from './trust';
import type { Product, PurchaseRow, StoreProductPrice } from '../../src/types';
import type { WorkspaceSnapshot } from '../types';

function makeProduct(id: number, name: string, brand: string | null, category: string, weight_g = 900): Product {
  return { id, name, brand, weight_g, unit: 'pcs', category, active: true };
}

function makeOffer(store_code: string, price: number, in_stock = true): StoreProductPrice {
  return {
    store_code,
    sku: `sku-${store_code}-${price}`,
    raw_name: '',
    price,
    unit: 'pcs',
    weight_g: 900,
    price_per_kg: null,
    in_stock,
    fetched_at: '2026-11-26T10:00:00Z',
    source: 'live',
  };
}

function makeHistory(id: number, product_id: number, unit_price = 100): PurchaseRow {
  return {
    id,
    date: '2026-11-20',
    store_id: 1,
    store_code: 'magnit',
    store_name: 'Магнит',
    product_id,
    raw_name: 'item',
    qty: 1,
    unit_price,
    total: unit_price,
    receipt_key: `r-${id}`,
  };
}

test('buildTrustProfile identifies rigid vs flexible categories and trusted brands', () => {
  const products = [
    makeProduct(1, 'Кофе Lavazza 250г', 'Lavazza', 'Кофе', 250),
    makeProduct(2, 'Молоко Простоквашино 1л', 'Простоквашино', 'Молоко', 930),
    makeProduct(3, 'Молоко Домик в деревне 1л', 'Домик в деревне', 'Молоко', 930),
  ];
  const history = [
    makeHistory(1, 1, 550),
    makeHistory(2, 1, 560),
    makeHistory(3, 2, 95),
    makeHistory(4, 3, 99),
  ];

  const profile = buildTrustProfile(history, products);
  assert.equal(profile.rigidCategories.has('кофе'), true);
  assert.equal(profile.flexibleCategories.has('молоко'), true);
  assert.equal(profile.trustedBrands.has('простоквашино'), true);
  assert.equal(profile.trustedBrands.has('домик в деревне'), true);
  assert.equal(profile.boughtCounts.get(1), 2);
});

test('rankTrustedReplacements enforces 4-tier ladder: bought > trusted brand > price band > cheapest STM', () => {
  const boughtMilk = makeProduct(10, 'Молоко Простоквашино 2.5%', 'Простоквашино', 'Молоко', 930);
  const brandMilk = makeProduct(11, 'Молоко Домик в деревне 3.2%', 'Домик в деревне', 'Молоко', 930);
  const bandMilk = makeProduct(12, 'Молоко Село Зелёное 3.2%', 'Село Зелёное', 'Молоко', 930);
  const cheapStm = makeProduct(13, 'Молоко Моя цена 3.2%', 'Моя цена', 'Молоко', 930);
  const oldDomik = makeProduct(99, 'Кефир Домик в деревне', 'Домик в деревне', 'Молоко', 930);

  const profile = buildTrustProfile(
    [makeHistory(1, 10, 105), makeHistory(2, 99, 100)],
    [boughtMilk, brandMilk, bandMilk, cheapStm, oldDomik]
  );

  const candidates = [
    { product: cheapStm, prices: { magnit: makeOffer('magnit', 59) } }, // cheapest, outside ±25% of 100
    { product: bandMilk, prices: { magnit: makeOffer('magnit', 102) } }, // unknown brand, within ±25% of 100
    { product: brandMilk, prices: { magnit: makeOffer('magnit', 112) } }, // trusted brand from history
    { product: boughtMilk, prices: { magnit: makeOffer('magnit', 118) } }, // previously bought exact item
  ];

  const ranked = rankTrustedReplacements(
    candidates,
    { category: 'Молоко', referencePrice: 100, weight_g: 930, unit: 'pcs' },
    profile,
    'magnit'
  );

  assert.deepEqual(
    ranked.map((r) => [r.row.product.id, r.tier]),
    [
      [10, 'bought'],
      [11, 'brand'],
      [12, 'price_band'],
      [13, 'cheaper'],
    ]
  );
});

test('estimateSingleStoreWithReplacements calculates honest full-basket total with trusted replacements', () => {
  const targetCheese = makeProduct(1, 'Сыр Российский Лента 200г', 'Лента', 'Сыр', 200);
  const magnitTrustedCheese = makeProduct(2, 'Сыр Российский Брест-Литовск 200г', 'Брест-Литовск', 'Сыр', 200);
  const magnitCheapCheese = makeProduct(3, 'Сыр Российский Моя цена 200г', 'Моя цена', 'Сыр', 200);
  const prevBrest = makeProduct(4, 'Масло Брест-Литовск 180г', 'Брест-Литовск', 'Масло', 180);

  const products = [targetCheese, magnitTrustedCheese, magnitCheapCheese, prevBrest];
  const history = [makeHistory(1, 4, 180), makeHistory(2, 1, 210)];
  const profile = buildTrustProfile(history, products);

  const ws = {
    products,
    history,
    prices: {
      '1': { lenta: makeOffer('lenta', 210) },
      '2': { magnit: makeOffer('magnit', 225) },
      '3': { magnit: makeOffer('magnit', 120) },
    },
  } as unknown as WorkspaceSnapshot;

  const est = estimateSingleStoreWithReplacements(
    'magnit',
    'Магнит',
    999,
    6,
    [{ product_id: 1, name: targetCheese.name, unit: 'pcs', weight_g: 200, qty: 2, photo: null }],
    ws,
    profile
  );

  assert.equal(est.isComplete, true);
  assert.equal(est.brandCount, 1);
  assert.equal(est.unresolvedCount, 0);
  assert.equal(est.estimatedTotal, 999 + 225 * 2);
  assert.equal(est.suggestions.get(1)?.item.product.id, 2);
  assert.equal(est.suggestions.get(1)?.label, 'Ваш бренд');
});
