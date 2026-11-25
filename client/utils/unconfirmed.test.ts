import test from 'node:test';
import assert from 'node:assert/strict';
import { getUnconfirmedStores } from './unconfirmed';
import type { Store, StoreCartQuote, StoreConnection } from '../../src/types';

const STORES: Store[] = [
  { id: 1, code: 'pyaterochka', name: 'Пятёрочка', delivery_fee: 0, free_delivery_from: 0, min_order: 0, connector_type: 'phone' },
  { id: 2, code: 'magnit', name: 'Магнит', delivery_fee: 0, free_delivery_from: 0, min_order: 0, connector_type: 'browser' },
  { id: 3, code: 'dixy', name: 'Дикси', delivery_fee: 0, free_delivery_from: 0, min_order: 0, connector_type: 'phone' },
  { id: 4, code: 'lenta', name: 'Лента', delivery_fee: 0, free_delivery_from: 0, min_order: 0, connector_type: 'phone' },
];

function baseQuote(store_code: string, overrides: Partial<StoreCartQuote>): StoreCartQuote {
  return {
    store_code,
    status: 'idle',
    stamp: null,
    updated_at: null,
    address: null,
    available_count: 0,
    total_count: 0,
    missing_names: [],
    goods_total: null,
    delivery_fee: null,
    service_fee: 0,
    promo_discount: 0,
    promo_label: null,
    bonus_balance: 0,
    bonus_max_use: 0,
    store_total: null,
    note: null,
    ...overrides,
  };
}

test('returns empty when basket has no items', () => {
  const conns: Record<string, StoreConnection> = {
    pyaterochka: { store_code: 'pyaterochka', connected: false, again: true, last: null },
  };
  assert.deepEqual(getUnconfirmedStores(STORES, conns, {}, [], 0), []);
});

test('maps login, refused, empty, and skips ready/collecting/disconnected chains', () => {
  const conns: Record<string, StoreConnection> = {
    pyaterochka: { store_code: 'pyaterochka', connected: false, again: true, last: null },
    magnit: { store_code: 'magnit', connected: true, again: false, last: null },
    dixy: { store_code: 'dixy', connected: true, again: false, last: null },
    lenta: { store_code: 'lenta', connected: true, again: false, last: null },
  };
  const quotes: Record<string, StoreCartQuote> = {
    pyaterochka: baseQuote('pyaterochka', { status: 'error', reason: 'login' }),
    magnit: baseQuote('magnit', { status: 'error', reason: 'refused' }),
    dixy: baseQuote('dixy', { status: 'idle', reason: 'empty' }),
    lenta: baseQuote('lenta', { status: 'ready', available_count: 3, total_count: 3 }),
  };

  const res = getUnconfirmedStores(STORES, conns, quotes, [], 3);
  assert.equal(res.length, 3);
  assert.deepEqual(
    res.map((r) => [r.storeCode, r.reason, r.needsSettings]),
    [
      ['pyaterochka', 'login', true],
      ['magnit', 'refused', false],
      ['dixy', 'empty', false],
    ]
  );

  // Actively collecting store is excluded from unconfirmed warnings
  const resWithCollecting = getUnconfirmedStores(STORES, conns, quotes, ['magnit'], 3);
  assert.deepEqual(
    resWithCollecting.map((r) => r.storeCode),
    ['pyaterochka', 'dixy']
  );
});
