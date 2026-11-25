import { getUnconfirmedStores } from '../utils/unconfirmed';
import React, { useEffect, useRef, useState } from 'react';
import {
  goods,
  whom,
  num,
  stepForUnit,
  unitLabel,
} from '../../src/shared/format';
import { searchServerCatalog, searchReplacements, confirmReplacement } from '../api';
import { purchasedProducts, rankProducts } from '../../src/shared/searchRank';
import { loadBasketLayout } from '../storage';
import { StoreProductPicker } from '../components/StoreProductPicker';
import { SearchPic } from '../components/SearchPic';
import { ChainMark, Icon, MoneyExact, MoneyWhole, ProductPic } from '../components/Icons';
import type { BasketItemView } from '../engine';
import { localSearchProducts } from '../engine';
import type {
  ClientBoard,
  HabitItem,
  SearchResultItem,
  WorkspaceSnapshot,
} from '../types';

interface BasketScreenProps {
  ws: WorkspaceSnapshot;
  basketName: string;
  items: BasketItemView[];
  habits: HabitItem[];
  board: ClientBoard;
  viewMode: 'grid' | 'list';
  isDesktop: boolean;
  collectionNotice: string;
  activeCollecting?: string[];
  onChangeView: (mode: 'grid' | 'list') => void;
  onUpdateQty: (productId: number, nextQty: number) => void;
  mode: 'best' | 'single' | null;
  onMode: (mode: 'best' | 'single' | null) => void;
  onAddFromSearch: (item: SearchResultItem, store?: string, replaceId?: number) => void;
  onAddHabit: (habit: HabitItem) => void;
  onClearBasket?: () => void;
  onOpenCheaper: (placeIndex: number) => void;
  onOpenOrders: () => void;
  onOpenBaskets: () => void;
  onOpenPrices: () => void;
  onOpenSettings: () => void;
}

export const BasketScreen: React.FC<BasketScreenProps> = ({
  ws,
  basketName,
  mode, onMode,
  items,
  habits,
  board,
  viewMode,
  isDesktop,
  activeCollecting = [],
  collectionNotice,
  onChangeView,
  onUpdateQty,
  onAddFromSearch,
  onAddHabit,
  onClearBasket,
  onOpenCheaper,
  onOpenOrders,
  onOpenBaskets,
  onOpenPrices,
  onOpenSettings,
}) => {
  const [warnOpen, setWarnOpen] = useState(false);
  const unconfirmed = React.useMemo(() => getUnconfirmedStores(
    ws.stores, ws.connections, ws.storeQuotes || {}, activeCollecting, items.length
  ), [ws.stores, ws.connections, ws.storeQuotes, activeCollecting, items.length]);
  const [layout] = useState(loadBasketLayout);
  const [catalog, setCatalog] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [replacing, setReplacing] = useState<BasketItemView | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [observedStores, setObservedStores] = useState<string[] | null>(null);
  const [searchError, setSearchError] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [retry, setRetry] = useState(0);
  const searchRequestRef = useRef(0);
  const applying = useRef(false);
  const workspace = useRef(ws);
  workspace.current = ws;

  const storeNameByCode = new Map(ws.stores.map((s) => [s.code, s.name]));

  const minPriceInfo = (pid: number): { price: number; storeName: string } | null => {
    const pMap = ws.prices[String(pid)];
    if (!pMap) return null;
    let best: { price: number; storeName: string } | null = null;
    for (const [code, snap] of Object.entries(pMap)) {
      if (!snap.in_stock) continue;
      if (!best || snap.price < best.price) {
        best = { price: snap.price, storeName: storeNameByCode.get(code) || code };
      }
    }
    return best;
  };

  const handleSearchChange = (next: string) => {
    searchRequestRef.current += 1;
    setSearchResults([]);
    setSearchError('');
    setSearching(next.trim().length >= 2);
    setQuery(next);
  };

  const inBasketMap = new Map(items.map((it) => [it.product_id, it.qty]));
  const findReplacement = (item: BasketItemView) => {
    setReplacing(item);
    setRetry(v => v + 1);
    const grade = /(?:^|[\s,])([сc][012ов])(?=[\s,.]|$)/iu.exec(item.name);
    const count = /(\d+)\s*шт/iu.exec(item.name);
    const term = /яйц/iu.test(item.name) && grade
      ? ['Яйца', grade[1].replace(/c/iu, 'С'), count?.[0]].filter(Boolean).join(' ') : item.name;
    handleSearchChange(term);
    searchInput.current?.focus();
    searchInput.current?.closest('.spa-search-box')?.scrollIntoView({ block: 'start' });
  };

  const stepItem = (productId: number, currentQty: number, unit: 'pcs' | 'kg', delta: number) => {
    const step = stepForUnit(unit);
    const raw = Math.round((currentQty + delta * step) * 1000) / 1000;
    const nextQty = raw <= 0.0001 ? 0 : raw;
    onUpdateQty(productId, nextQty);
  };

  const preferred = board.allVariants[0];
  const single = preferred && preferred.stores.length > 1
    ? board.allVariants.find(v => v.stores.length === 1) : undefined;
  const itemIds = (variant: NonNullable<typeof preferred>) =>
    variant.stores.flatMap(store => store.lines.map(line => line.product_id)).sort((a,b) => a-b).join(',');
  const sameItems = preferred && single && itemIds(preferred) === itemIds(single);
  const bestLabel = single && preferred.total > single.total
    ? (preferred.missing_products.length < single.missing_products.length ? 'Больше товаров' : 'Лучший вариант')
    : 'Дешевле всего';
  const activeMode = mode === 'single' && !single ? 'best' : mode;
  const chosen = activeMode === 'best' ? preferred : activeMode === 'single' ? single : undefined;
  const scope = replacing ? chosen?.stores.map(s => s.store_code).sort().join(',') || '' : '';
  useEffect(() => {
    const request = ++searchRequestRef.current;
    setObservedStores(null);
    const q = query.trim();
    const snapshot = workspace.current;
    setSearchError('');
    setSearchResults([]);
    if (q.length < 2 || (replacing && !scope)) {
      setSearching(false);
      return;
    }
    let current = true;
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        let rows: SearchResultItem[];
        if (replacing) {
          const stores = scope.split(',');
          if (snapshot.isDemo) {
            rows = localSearchProducts(snapshot, q, Infinity).filter(r => r.product.id !== replacing.product_id &&
              r.product.unit === replacing.unit).map(r => {
              const prices = Object.fromEntries(Object.entries(r.prices).filter(([code,p]) =>
                stores.includes(code) && p.in_stock && p.price > 0));
              return { ...r, prices, available: true };
            }).filter(r => Object.keys(r.prices).length > 0);
            rows = rankProducts(rows, purchasedProducts(snapshot.history)).slice(0,30);
          } else {
            const found = await searchReplacements(q, stores, replacing.unit, replacing.product_id);
            rows = found.results;
            if (current && request === searchRequestRef.current) setObservedStores(found.observedStores ?? null);
          }
        } else {
          rows = await searchServerCatalog(q);
          if (snapshot.isDemo) {
            const seen = new Set(rows.map(r => r.product.id));
            const local = localSearchProducts(snapshot, q).filter(r => !seen.has(r.product.id));
            rows = rankProducts([...rows, ...local], purchasedProducts(snapshot.history)).slice(0,30);
          }
        }
        if (current && request === searchRequestRef.current) setSearchResults(rows);
      } catch {
        if (!current || request !== searchRequestRef.current) return;
        if (replacing) setSearchError(`Не удалось проверить наличие. Магазины варианта: ${names(storesInVariant)}.`);
        else setSearchResults(localSearchProducts(snapshot, q));
      } finally {
        if (current && request === searchRequestRef.current) setSearching(false);
      }
    }, 180);
    return () => { current = false; searchRequestRef.current++; window.clearTimeout(timer); };
  }, [query, scope, replacing, ws.phone, ws.address, retry]);

  const storesInVariant = chosen?.stores || [];
  const names = (stores: typeof storesInVariant) => stores.map(s => `«${s.store_name}»`).join(', ');
  const noData = observedStores && storesInVariant.filter(s => !observedStores.includes(s.store_code));
  const checked = storesInVariant.filter(s => !noData?.includes(s));
  const replacementEmpty = !storesInVariant.length ? 'Выберите вариант корзины для поиска замены.'
    : [checked.length && `${names(checked)} — подходящей замены с подтверждённой ценой нет.`,
       noData?.length && `${names(noData)} — нет свежих данных о наличии по вашему адресу.`].filter(Boolean).join(' ');
  const visibleResults = replacing
    ? searchResults.filter(row => Object.keys(row.prices).some(code => scope.split(',').includes(code))) : searchResults;
  const applyReplacement = async (row: SearchResultItem, store: string) => {
    if (!replacing || applying.current) return;
    const request = searchRequestRef.current;
    applying.current = true;
    setConfirming(true);
    try {
      const accepted = ws.isDemo ? row : await confirmReplacement(row, store, replacing.product_id);
      if (request !== searchRequestRef.current) return;
      onAddFromSearch(accepted, store, replacing.product_id);
      setReplacing(null);
      handleSearchChange('');
      searchInput.current?.blur();
    } catch (error) {
      if (request !== searchRequestRef.current) return;
      const stale = (error as {status?: number}).status === 409;
      if (stale) setSearchResults(rows => rows.filter(r => r.product.id !== row.product.id));
      setSearchError(stale ? 'Магазин больше не подтверждает эту замену' : 'Не удалось проверить наличие');
    } finally {
      applying.current = false;
      setConfirming(false);
    }
  };
  const byId = new Map(items.map(item => [item.product_id, item]));
  const assigned = new Set(chosen?.stores.flatMap(store => store.lines.map(line => line.product_id)));
  const missing = items.filter(item => !assigned.has(item.product_id));
  const panel = !query.trim() && preferred && (
    <section className="basket-plans" aria-label="Где дешевле">
      <div className="plan-heading"><strong>Где дешевле</strong>
        {chosen && <button className="plan-checkout" type="button" onClick={() => onOpenCheaper(board.allVariants.indexOf(chosen))}>К оформлению <Icon name="next" /></button>}
      </div>
      <div className="plan-options">{([['best', preferred], ['single', single]] as const).map(([key, variant]) =>
        variant && <button type="button" key={key} className={key === 'best' ? 'best' : ''}
          aria-pressed={activeMode === key} onClick={() => onMode(activeMode === key ? null : key)}>
          <strong>{key === 'best' ? bestLabel : 'Один заказ'}</strong>
          <b><MoneyWhole value={variant.total} /></b>
          <small>{variant.stores.length === 1 ? variant.stores[0].store_name : variant.stores.length + ' магазина'} · {items.length - variant.missing_products.length} из {items.length}</small>
          {key === 'single' && sameItems && variant.total > preferred.total && <small>Доплата <MoneyWhole value={variant.total - preferred.total} /></small>}
        </button>)}</div>
    </section>
  );
  const renderItem = (item: BasketItemView, price?: number, lineTotal = false) => {
    const displayPrice = price === undefined ? undefined : lineTotal ? price : price * item.qty;
    const isLastUnit = item.qty <= stepForUnit(item.unit) + 1e-9;

    if (viewMode === 'grid') {
      return (
        <div key={item.product_id} className="it">
          <div className="it-top">
            <ProductPic photo={item.photo} title={item.name} />
            {displayPrice !== undefined && (
              <div className="pr">
                <MoneyWhole value={displayPrice} />
              </div>
            )}
          </div>
          <div className="nm">{item.name}</div>
          <div className="qty">
            <button
              type="button"
              className={isLastUnit ? 'del' : ''}
              aria-label={isLastUnit ? 'Убрать' : 'Уменьшить'}
              onClick={() => stepItem(item.product_id, item.qty, item.unit, -1)}
            >
              <Icon name={isLastUnit ? 'trash' : 'minus'} />
            </button>
            <span className="n">
              {num(item.qty)}
              {item.unit === 'kg' ? ' кг' : ''}
            </span>
            <button
              type="button"
              aria-label="Добавить"
              onClick={() => stepItem(item.product_id, item.qty, item.unit, 1)}
            >
              <Icon name="plus" />
            </button>
          </div>
        </div>
      );
    }

    return (
      <div key={item.product_id} className="it">
        <ProductPic photo={item.photo} title={item.name} />
        <div className="info">
          <div className="nm">{item.name}</div>
          <div className="sub">
            {displayPrice !== undefined ? (
              <>
                <span className="pr-val">
                  <MoneyWhole value={displayPrice} />
                </span>

              </>
            ) : (
              <span>{unitLabel(item.unit)}</span>
            )}
          </div>
        </div>
        <div className="qty">
          <button
            type="button"
            className={isLastUnit ? 'del' : ''}
            aria-label={isLastUnit ? 'Убрать из корзины' : 'Уменьшить'}
            onClick={() => stepItem(item.product_id, item.qty, item.unit, -1)}
          >
            <Icon name={isLastUnit ? 'trash' : 'minus'} />
          </button>
          <span className="n">
            {num(item.qty)}
            {item.unit === 'kg' ? ' кг' : ''}
          </span>
          <button
            type="button"
            aria-label="Добавить ещё"
            onClick={() => stepItem(item.product_id, item.qty, item.unit, 1)}
          >
            <Icon name="plus" />
          </button>
        </div>
      </div>
    );
  };
  const itemClass = 'items ' + viewMode;
  const grouped = chosen && <>
    {chosen.stores.map(store => {
      const add = <button type="button" className="store-add" onClick={() => setCatalog(store.store_code)}>
        <svg aria-hidden="true"><rect rx="12" /></svg>＋ Добавить товар в {whom(store.store_code, store.store_name)}
      </button>;
      return <section key={store.store_code} className="basket-group">
        <header><h2><ChainMark code={store.store_code} />{store.store_name}</h2><MoneyWhole value={store.total} /></header>
        {layout.add === 'first' && add}
        <div className={itemClass}>{store.lines.map(line => {
          const item = byId.get(line.product_id);
          return item && renderItem(item, line.price, true);
        })}</div>
        {layout.add === 'last' && add}
        <div className="group-delivery">Доставка <MoneyWhole value={store.delivery} />
          {store.below_min_order && <span> · До минимума <MoneyWhole value={Math.max(0,(ws.stores.find(s => s.code === store.store_code)?.min_order || 0) - store.subtotal)} /></span>}
        </div>
      </section>;
    })}
    {missing.length > 0 && <section className="basket-group unavailable"><header><h2>Нет в этом варианте</h2></header>
      <div className={itemClass}>{missing.map(item => <div key={item.product_id} className="missing-item">
        {renderItem(item)}
        <button type="button" className="find-replacement" onClick={() => findReplacement(item)}>Подобрать замену</button>
      </div>)}</div></section>}
  </>;
  return (
    <div className="spa-screen">
      {layout.panel === 'top' && panel}
      <div className="ph-body">
        <header className="spa-basket-top">
          <div className="top">
            <h1>Корзина</h1>
            <div className="top-actions">
              {items.length > 0 && onClearBasket && (
                confirmClear ? (
                  <button
                    type="button"
                    className="bt sm danger"
                    onClick={() => {
                      setConfirmClear(false);
                      onClearBasket();
                    }}
                    onBlur={() => setConfirmClear(false)}
                  >
                    Очистить?
                  </button>
                ) : (
                  <button
                    type="button"
                    className="ibtn"
                    onClick={() => setConfirmClear(true)}
                    aria-label="Очистить корзину"
                    title="Очистить корзину"
                  >
                    <Icon name="trash" />
                  </button>
                )
              )}
              {items.length > 0 && (
                <button
                  type="button"
                  className="ibtn"
                  onClick={() => onChangeView(viewMode === 'list' ? 'grid' : 'list')}
                  aria-label={viewMode === 'list' ? 'Показать плиткой' : 'Показать списком'}
                  title={viewMode === 'list' ? 'Плитка' : 'Список'}
                >
                  <Icon name={viewMode === 'list' ? 'grid' : 'list'} />
                </button>
              )}
              {!isDesktop && (
                <button
                  type="button"
                  className="ibtn"
                  onClick={onOpenPrices}
                  aria-label="Цены по магазинам"
                  title="Цены по магазинам"
                >
                  <Icon name="tag" />
                </button>
              )}
              {isDesktop && (
                <button
                  type="button"
                  className="ibtn"
                  onClick={onOpenOrders}
                  aria-label="История заказов"
                  title="История заказов"
                >
                  <Icon name="clock" />
                </button>
              )}
            </div>
          </div>

          <div className="spa-basket-context">
            <button
              type="button"
              className="spa-basket-switch"
              onClick={onOpenBaskets}
              title={basketName}
              aria-label={`Мои корзины. Текущая корзина: ${basketName}`}
            >
              <span>{basketName === 'Корзина' ? 'Мои корзины' : basketName}</span>
              <Icon name="next" />
            </button>
            {items.length > 0 && (
              <span className="spa-title-sub">{goods(items.length)}</span>
            )}
          </div>
        </header>

        {!ws.address && (
          <button
            type="button"
            className="spa-addr-prompt"
            onClick={onOpenSettings}
          >
            <Icon name="pin" />
            <span>Укажите адрес доставки — цены и наличие зависят от него</span>
            <Icon name="next" />
          </button>
        )}

        <div className="spa-search-box">
          {replacing && <div className="replacement-for">Замена для «{replacing.name}»<small>{chosen?.stores.map(s => s.store_name).join(' · ')}</small></div>}
          <div className="spa-search-input">
            <Icon name="search" />
            <input
              type="search"
              ref={searchInput}
              value={query}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder="Найти и добавить товар…"
              aria-label="Поиск товара"
            />
            {(query || replacing) && (
              <button
                type="button"
                className="spa-search-clear"
                onClick={() => { setReplacing(null); handleSearchChange(''); }}
                aria-label={replacing ? 'Отменить замену' : 'Очистить поиск'}
              >
                <Icon name="x" />
              </button>
            )}
          </div>

          {query.trim().length >= 2 && (
            <div className="card spa-search-results">
              {searchError && <div className="spa-search-empty" role="status">{searchError} <button type="button" className="bt sm" onClick={() => setRetry(v => v + 1)}>Повторить</button></div>}
              {visibleResults.length === 0 ? (
                <div className="spa-search-empty">
                  {searchError ? null : searching ? 'Ищем в каталоге…' : replacing ? replacementEmpty : 'Ничего не нашлось'}
                </div>
              ) : (
                <ul>
                  {visibleResults.map((res) => {
                    const qty = inBasketMap.get(res.product.id) || 0;
                    const offers = Object.values(res.prices)
                      .filter(p => p.in_stock && p.price > 0 && (!replacing || scope.split(',').includes(p.store_code)))
                      .sort((a, b) => a.price - b.price);
                    const bestOffer = replacing ? offers[0] : undefined;
                    return (
                      <li key={res.product.id} className="spa-search-row">
                        <SearchPic item={res} />
                        <div className="spa-search-info">
                          {offers.length > 0 && <div className="search-price">
                            {!replacing && offers.some(p => p.price !== offers[0].price) && <span>от </span>}
                            <MoneyExact value={offers[0].price} />
                          </div>}
                          <div className="nm">{res.product.title || res.product.name}</div>
                          <small>
                            {bestOffer ? [res.product.previouslyBought || purchasedProducts(ws.history).has(res.product.id) ? 'Вы покупали' : '',
                              storeNameByCode.get(bestOffer.store_code) || bestOffer.store_code].filter(Boolean).join(' · ') : res.small}
                          </small>
                        </div>
                        {replacing ? (
                          <button type="button" className="bt sm lime" disabled={confirming || !bestOffer}
                            onClick={() => bestOffer && void applyReplacement(res, bestOffer.store_code)}>Заменить</button>
                        ) : qty > 0 ? (
                          <div className="qty">
                            <button
                              type="button"
                              aria-label="Уменьшить"
                              onClick={() =>
                                stepItem(res.product.id, qty, res.product.unit, -1)
                              }
                            >
                              <Icon name={qty <= stepForUnit(res.product.unit) ? 'trash' : 'minus'} />
                            </button>
                            <span className="n">{num(qty)}</span>
                            <button
                              type="button"
                              aria-label="Увеличить"
                              onClick={() =>
                                stepItem(res.product.id, qty, res.product.unit, 1)
                              }
                            >
                              <Icon name="plus" />
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="search-add"
                            aria-label={'Добавить ' + (res.product.title || res.product.name)}
                            onClick={() => onAddFromSearch(res)}
                          >
                            <Icon name="plus" />
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
        </div>

        {collectionNotice && items.length > 0 && (
          <div className="note warn" role="status">
            {unconfirmed.length > 0 ? <>
              <button type="button" className="spa-warn-head" aria-expanded={warnOpen} onClick={() => setWarnOpen(!warnOpen)}>
                {collectionNotice}<Icon name="down" />
              </button>
              {warnOpen && unconfirmed.map(store => <div key={store.storeCode}>{store.storeName} · {store.label}</div>)}
            </> : collectionNotice}
          </div>
        )}

        {activeCollecting.length > 0 && items.length > 0 && (
          <div className="spa-live-pill" role="status">
            <span className="spa-pulse" aria-hidden="true" />
            <span>
              Проверяем цены, наличие и доставку:{' '}
              {activeCollecting
                .slice(0, 3)
                .map((c) => storeNameByCode.get(c) || c)
                .join(', ')}
              {activeCollecting.length > 3 ? ` и ещё ${activeCollecting.length - 3}` : ''}
            </span>
          </div>
        )}

        {chosen ? grouped : items.length > 0 ? (
          <div className={`items ${viewMode === 'list' ? 'list' : 'grid'}`}>
            {items.map(item => renderItem(item, minPriceInfo(item.product_id)?.price))}
          </div>
        ) : (
          <div className="empty-slim">
            <div className="empty-slim-txt">
              <strong>Корзина пока пустая</strong>
              <span>Добавьте товары ниже — сразу покажем, где дешевле</span>
            </div>
            <button type="button" className="bt sm" onClick={onOpenOrders}>
              <Icon name="clock" />
              <span>История</span>
            </button>
          </div>
        )}

        {habits.length > 0 && (
          <section className="sec">
            <div className="sh">
              <h2>{items.length === 0 ? 'Быстро добавить' : 'Из привычного'}</h2>
            </div>
            <div className={items.length === 0 ? 'hab-grid' : 'hab-row'}>
              {habits.map((h) => {
                const info = minPriceInfo(h.pid);
                const isDue = h.why === 'пора докупить';
                return (
                  <button
                    key={h.pid}
                    type="button"
                    className="hc"
                    onClick={() => onAddHabit(h)}
                  >
                    <div className="hc-top">
                      <ProductPic photo={h.photo} title={h.name} />
                      <span className={`hc-tag${isDue ? ' due' : ''}`}>
                        {h.why}
                      </span>
                    </div>
                    <div className="nm">{h.name}</div>
                    <div className="hc-foot">
                      <span className="hc-price">
                        {info ? (
                          <>от <MoneyWhole value={info.price} /></>
                        ) : (
                          unitLabel(h.unit)
                        )}
                      </span>
                      <span className="hc-plus" aria-hidden="true">
                        <Icon name="plus" />
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        )}
      </div>

      {layout.panel === 'bottom' && panel}
      {catalog && <StoreProductPicker ws={ws} code={catalog} name={storeNameByCode.get(catalog) || catalog}
        quantities={inBasketMap} add={onAddFromSearch} close={() => setCatalog(null)} />}
    </div>
  );
};
