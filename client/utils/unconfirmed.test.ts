import test from 'node:test';
import assert from 'node:assert/strict';
import { getUnconfirmedStores, reportFromCollection, reportTime } from './unconfirmed';
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

  const res = getUnconfirmedStores(STORES, conns, quotes, STORES.map(s => s.code), 3);
  assert.equal(res.length, 3);
  assert.deepEqual(
    res.map((r) => [r.storeCode, r.reason, r.needsSettings]),
    [
      ['pyaterochka', 'login', true],
      ['magnit', 'refused', false],
      ['dixy', 'empty', false],
    ]
  );

  // Only stores reporting in this attempt can appear in the warning.
  const resWithCollecting = getUnconfirmedStores(STORES, conns, quotes, ['pyaterochka', 'dixy'], 3);
  assert.deepEqual(
    resWithCollecting.map((r) => r.storeCode),
    ['pyaterochka', 'dixy']
  );
});


test('keeps the latest failure detail and excludes stale reports', () => {
  const note = 'Сеть не успела подтвердить корзину за 5 секунд. Последний сбой запроса: Пятёрочка: запрос отменён (212 мс)';
  const old = baseQuote('pyaterochka', { status: 'error', stamp: 'a', updated_at: '2026-11-27T06:56:46', note });
  const fresh = { ...old, updated_at: '2026-11-27T07:39:12' };
  assert.equal(reportFromCollection(old, 'b', undefined), false);
  assert.equal(reportFromCollection(old, 'a', old), false);
  assert.equal(reportFromCollection(fresh, 'a', old), true);
  assert.equal(reportTime(fresh.updated_at), reportTime(fresh.updated_at + 'Z'));
  assert.equal(reportTime('invalid'), '');
  const conns = { pyaterochka: {store_code: 'pyaterochka', connected: true, again: false, last: null} };
  assert.equal(getUnconfirmedStores(STORES, conns, {pyaterochka: fresh}, [], 7).length, 0);
  assert.equal(getUnconfirmedStores(STORES, conns, {pyaterochka: fresh}, ['pyaterochka'], 7)[0].label, note);
});
