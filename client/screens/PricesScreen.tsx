import React, { useMemo, useState } from 'react';
import {
  goods,
  num,
  rub,
  stepForUnit,
  unitLabel,
} from '../../src/shared/format';
import { ChainMark, Icon, MoneyExact, MoneyWhole, ProductPic } from '../components/Icons';
import { MobileTabs } from '../components/MobileTabs';
import type { BasketItemView } from '../engine';
import type { WorkspaceSnapshot } from '../types';

interface PricesScreenProps {
  ws: WorkspaceSnapshot;
  items: BasketItemView[];
  bestTotal: number | null;
  isDesktop: boolean;
  onUpdateQty: (productId: number, nextQty: number) => void;
  onOpenBasket: () => void;
  onOpenCheaper: () => void;
  onOpenSettings: () => void;
}

type FilterTab = 'basket' | 'all' | 'stores';

export const PricesScreen: React.FC<PricesScreenProps> = ({
  ws,
  items,
  bestTotal,
  isDesktop,
  onUpdateQty,
  onOpenBasket,
  onOpenCheaper,
  onOpenSettings,
}) => {
  const [tab, setTab] = useState<FilterTab>(() => (items.length > 0 ? 'basket' : 'all'));
  const [filterText, setFilterText] = useState('');

  const inBasketMap = useMemo(
    () => new Map(items.map((it) => [it.product_id, it.qty])),
    [items]
  );

  const visibleStores = useMemo(() => {
    const withPrices = new Set<string>();
    for (const pMap of Object.values(ws.prices)) {
      for (const code of Object.keys(pMap)) withPrices.add(code);
    }
    return ws.stores.filter((s) => withPrices.has(s.code) || ws.connections[s.code]?.connected);
  }, [ws.stores, ws.prices, ws.connections]);

  const rows = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    const base =
      tab === 'basket'
        ? ws.products.filter((p) => inBasketMap.has(p.id))
        : ws.products.filter((p) => p.active);

    const filtered = q
      ? base.filter(
          (p) =>
            p.name.toLowerCase().includes(q) ||
            (p.brand && p.brand.toLowerCase().includes(q)) ||
            (p.category && p.category.toLowerCase().includes(q))
        )
      : base;

    return filtered
      .map((p) => {
        const pMap = ws.prices[String(p.id)] || {};
        const availablePrices = Object.values(pMap)
          .filter((sp) => sp.in_stock && sp.price > 0)
          .map((sp) => sp.price);
        const minPrice = availablePrices.length > 0 ? Math.min(...availablePrices) : null;
        const maxPrice = availablePrices.length > 0 ? Math.max(...availablePrices) : null;
        return {
          product: p,
          pMap,
          minPrice,
          maxPrice,
          spread: minPrice !== null && maxPrice !== null ? maxPrice - minPrice : 0,
          qty: inBasketMap.get(p.id) || 0,
        };
      })
      .sort((a, b) => {
        if (b.qty > 0 !== a.qty > 0) return b.qty > 0 ? 1 : -1;
        if (b.spread !== a.spread) return b.spread - a.spread;
        return a.product.name.localeCompare(b.product.name, 'ru');
      });
  }, [ws.products, ws.prices, tab, filterText, inBasketMap]);

  const stepItem = (productId: number, currentQty: number, unit: 'pcs' | 'kg', delta: number) => {
    const step = stepForUnit(unit);
    const raw = Math.round((currentQty + delta * step) * 1000) / 1000;
    onUpdateQty(productId, raw <= 0.0001 ? 0 : raw);
  };

  return (
    <div className="spa-screen">
      <div className="ph-body">
        <header className="top">
          <div className="top-left">
            {!isDesktop && (
              <button type="button" className="back" onClick={onOpenBasket} aria-label="Назад к корзине">
                <Icon name="back" />
              </button>
            )}
            <h1>Таблица цен</h1>
            {tab !== 'stores' && <span className="spa-title-sub">{goods(rows.length)}</span>}
          </div>
          {ws.address && (
            <span className="spa-addr-chip" title={ws.address}>
              <Icon name="pin" />
              <span>{ws.address}</span>
            </span>
          )}
        </header>

        <div className="spa-seg-bar" role="tablist" aria-label="Режим таблицы цен">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'basket'}
            className={`spa-seg-btn${tab === 'basket' ? ' on' : ''}`}
            onClick={() => setTab('basket')}
          >
            В корзине{items.length > 0 ? ` (${items.length})` : ''}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'all'}
            className={`spa-seg-btn${tab === 'all' ? ' on' : ''}`}
            onClick={() => setTab('all')}
          >
            Все товары ({ws.products.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'stores'}
            className={`spa-seg-btn${tab === 'stores' ? ' on' : ''}`}
            onClick={() => setTab('stores')}
          >
            Условия сетей ({ws.stores.length})
          </button>
        </div>

        {tab === 'stores' ? (
          <div className="card list-c">
            {ws.stores.map((st) => {
              const conn = ws.connections[st.code];
              const perk = ws.storePerks?.[st.code];
              return (
                <div key={st.code} className="row spa-store-cond-row">
                  <ChainMark code={st.code} name={st.name} />
                  <div className="who">
                    <div className="nm">
                      {st.name}
                      {conn?.connected && <span className="spa-dot-on" title="Подключён" />}
                    </div>
                    <div className="sub">
                      Доставка: {st.delivery_fee > 0 ? rub(st.delivery_fee) : '0 ₽'}
                      {st.free_delivery_from > 0 && ` · бесплатно от ${Math.round(st.free_delivery_from)} ₽`}
                      {st.min_order > 0 && ` · мин. заказ ${Math.round(st.min_order)} ₽`}
                    </div>
                    {(perk?.promo_label || (perk?.bonus_balance || 0) > 0) && (
                      <div className="spa-perk-badges">
                        {perk?.promo_label && <span className="spa-perk-chip">{perk.promo_label}</span>}
                        {(perk?.bonus_balance || 0) > 0 && (
                          <span className="spa-perk-chip bonus">
                            Бонусы {Math.round(perk!.bonus_balance)} ₽
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  <button type="button" className="bt sm" onClick={onOpenSettings}>
                    Настроить
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <>
            <div className="spa-search-box">
              <div className="spa-search-input">
                <Icon name="search" />
                <input
                  type="search"
                  value={filterText}
                  onChange={(e) => setFilterText(e.target.value)}
                  placeholder="Фильтр по названию товара…"
                  aria-label="Фильтр по товарам"
                />
                {filterText && (
                  <button
                    type="button"
                    className="spa-search-clear"
                    onClick={() => setFilterText('')}
                    aria-label="Сбросить фильтр"
                  >
                    <Icon name="x" />
                  </button>
                )}
              </div>
            </div>

            {rows.length === 0 ? (
              <div className="card empty">
                <h2>{tab === 'basket' ? 'Корзина пока пустая' : 'Товары не найдены'}</h2>
                <p>
                  {tab === 'basket'
                    ? 'Добавьте продукты в корзину или переключитесь на «Все товары», чтобы сравнить цены по сетям.'
                    : 'Попробуйте изменить запрос или добавьте товары через поиск на экране Корзины.'}
                </p>
                {tab === 'basket' && (
                  <button type="button" className="bt sm lime" onClick={() => setTab('all')}>
                    Показать все товары
                  </button>
                )}
              </div>
            ) : isDesktop ? (
              <div className="card spa-matrix-wrap">
                <table className="spa-matrix">
                  <thead>
                    <tr>
                      <th className="spa-mx-prod">Товар</th>
                      <th className="spa-mx-qty">Корзина</th>
                      {visibleStores.map((st) => (
                        <th key={st.code} className="spa-mx-st">
                          <div className="spa-mx-st-h">
                            <ChainMark code={st.code} name={st.name} />
                            <span>{st.name}</span>
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(({ product, pMap, minPrice, qty }) => (
                      <tr key={product.id}>
                        <td className="spa-mx-prod">
                          <div className="spa-mx-pinfo">
                            <ProductPic photo={product.photo} title={product.name} />
                            <div className="who">
                              <div className="nm">{product.name}</div>
                              <div className="sub">
                                {unitLabel(product.unit)}
                                {product.weight_g ? ` · ${product.weight_g} г` : ''}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="spa-mx-qty">
                          {qty > 0 ? (
                            <div className="qty">
                              <button
                                type="button"
                                aria-label="Уменьшить"
                                onClick={() => stepItem(product.id, qty, product.unit, -1)}
                              >
                                <Icon name={qty <= stepForUnit(product.unit) ? 'trash' : 'minus'} />
                              </button>
                              <span className="n">{num(qty)}</span>
                              <button
                                type="button"
                                aria-label="Увеличить"
                                onClick={() => stepItem(product.id, qty, product.unit, 1)}
                              >
                                <Icon name="plus" />
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="bt sm"
                              onClick={() => stepItem(product.id, 0, product.unit, 1)}
                            >
                              <Icon name="plus" />
                            </button>
                          )}
                        </td>
                        {visibleStores.map((st) => {
                          const snap = pMap[st.code];
                          if (!snap) {
                            return (
                              <td key={st.code} className="spa-mx-cell miss">
                                —
                              </td>
                            );
                          }
                          if (!snap.in_stock) {
                            return (
                              <td key={st.code} className="spa-mx-cell out">
                                нет
                              </td>
                            );
                          }
                          const isBest = minPrice !== null && Math.abs(snap.price - minPrice) < 0.01;
                          return (
                            <td key={st.code} className={`spa-mx-cell${isBest ? ' best' : ''}`}>
                              <MoneyExact value={snap.price} />
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="spa-price-cards">
                {rows.map(({ product, pMap, minPrice, qty }) => {
                  const entries = visibleStores
                    .map((st) => ({ st, snap: pMap[st.code] }))
                    .filter((e) => Boolean(e.snap))
                    .sort((a, b) => {
                      if (a.snap!.in_stock !== b.snap!.in_stock) return a.snap!.in_stock ? -1 : 1;
                      return a.snap!.price - b.snap!.price;
                    });
                  return (
                    <article key={product.id} className="card spa-pcard">
                      <div className="spa-pcard-head">
                        <ProductPic photo={product.photo} title={product.name} />
                        <div className="who">
                          <div className="nm">{product.name}</div>
                          <div className="sub">
                            {minPrice !== null ? (
                              <>
                                от <MoneyWhole value={minPrice} />
                              </>
                            ) : (
                              'Нет свежих цен'
                            )}
                          </div>
                        </div>
                        {qty > 0 ? (
                          <div className="qty">
                            <button
                              type="button"
                              aria-label="Уменьшить"
                              onClick={() => stepItem(product.id, qty, product.unit, -1)}
                            >
                              <Icon name={qty <= stepForUnit(product.unit) ? 'trash' : 'minus'} />
                            </button>
                            <span className="n">{num(qty)}</span>
                            <button
                              type="button"
                              aria-label="Увеличить"
                              onClick={() => stepItem(product.id, qty, product.unit, 1)}
                            >
                              <Icon name="plus" />
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="bt sm lime"
                            onClick={() => stepItem(product.id, 0, product.unit, 1)}
                          >
                            <Icon name="plus" />
                          </button>
                        )}
                      </div>
                      {entries.length > 0 && (
                        <div className="spa-pcard-chips">
                          {entries.map(({ st, snap }) => {
                            const isBest =
                              snap!.in_stock &&
                              minPrice !== null &&
                              Math.abs(snap!.price - minPrice) < 0.01;
                            return (
                              <span
                                key={st.code}
                                className={`spa-pchip${isBest ? ' best' : ''}${!snap!.in_stock ? ' out' : ''}`}
                              >
                                <ChainMark code={st.code} name={st.name} />
                                <span>{st.name}</span>
                                <strong>
                                  {snap!.in_stock ? <MoneyWhole value={snap!.price} /> : 'нет'}
                                </strong>
                              </span>
                            );
                          })}
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>

      {!isDesktop && (
        <div className="ph-foot">
          <MobileTabs
            active="basket"
            itemsCount={items.length}
            bestTotal={bestTotal}
            onSelect={(t) => {
              if (t === 'basket') onOpenBasket();
              else if (t === 'cheaper') onOpenCheaper();
              else if (t === 'settings') onOpenSettings();
            }}
          />
        </div>
      )}
    </div>
  );
};
