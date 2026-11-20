import React, { useEffect, useRef, useState } from 'react';
import {
  goods,
  num,
  stepForUnit,
  unitLabel,
} from '../../src/shared/format';
import { searchServerCatalog } from '../api';
import { ChainMark, Icon, MoneyWhole, ProductPic } from '../components/Icons';
import { MobileTabs } from '../components/MobileTabs';
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
  basketsCount: number;
  items: BasketItemView[];
  habits: HabitItem[];
  board: ClientBoard;
  viewMode: 'grid' | 'list';
  isDesktop: boolean;
  activeCollecting?: string[];
  onChangeView: (mode: 'grid' | 'list') => void;
  onUpdateQty: (productId: number, nextQty: number) => void;
  onAddFromSearch: (item: SearchResultItem) => void;
  onAddHabit: (habit: HabitItem) => void;
  onAddAllHabits?: () => void;
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
  basketsCount,
  items,
  habits,
  board,
  viewMode,
  isDesktop,
  activeCollecting = [],
  onChangeView,
  onUpdateQty,
  onAddFromSearch,
  onAddHabit,
  onAddAllHabits,
  onClearBasket,
  onOpenCheaper,
  onOpenOrders,
  onOpenBaskets,
  onOpenPrices,
  onOpenSettings,
}) => {
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const searchDebounceRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (searchDebounceRef.current) window.clearTimeout(searchDebounceRef.current);
    };
  }, []);

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
    setQuery(next);
    const trimmed = next.trim();
    if (searchDebounceRef.current) window.clearTimeout(searchDebounceRef.current);
    if (trimmed.length < 2) {
      setSearchResults([]);
      setSearching(false);
      return;
    }

    const localHits = localSearchProducts(ws, trimmed);
    setSearchResults(localHits);

    setSearching(true);
    searchDebounceRef.current = window.setTimeout(async () => {
      try {
        const remote = await searchServerCatalog(trimmed);
        setSearchResults((prev) => {
          const seen = new Set(remote.map((r) => r.product.id));
          const merged = [...remote];
          for (const loc of prev) {
            if (!seen.has(loc.product.id)) merged.push(loc);
          }
          return merged.slice(0, 30);
        });
      } catch {
        // Offline — keep local hits silently
      } finally {
        setSearching(false);
      }
    }, 180);
  };

  const inBasketMap = new Map(items.map((it) => [it.product_id, it.qty]));

  const stepItem = (productId: number, currentQty: number, unit: 'pcs' | 'kg', delta: number) => {
    const step = stepForUnit(unit);
    const raw = Math.round((currentQty + delta * step) * 1000) / 1000;
    const nextQty = raw <= 0.0001 ? 0 : raw;
    onUpdateQty(productId, nextQty);
  };

  const bestTotal = board.places.length > 0 ? board.places[0].total : null;
  const colsClass =
    board.places.length === 1
      ? 'cols-1'
      : board.places.length === 2
        ? 'cols-2'
        : '';

  return (
    <div className="spa-screen">
      <div className="ph-body">
        <header className="top">
          <div className="top-left">
            <button
              type="button"
              className="spa-title-btn"
              onClick={onOpenBaskets}
              title="Мои корзины"
            >
              <h1>{basketsCount > 1 ? basketName : 'Корзина'}</h1>
              {basketsCount > 1 && <Icon name="down" className="i" />}
            </button>
            {items.length > 0 && (
              <span className="spa-title-sub">{goods(items.length)}</span>
            )}
          </div>

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
                aria-label="Таблица цен"
                title="Таблица цен"
              >
                <Icon name="list" />
              </button>
            )}
            <button
              type="button"
              className="ibtn"
              onClick={onOpenOrders}
              aria-label="История заказов"
              title="История заказов"
            >
              <Icon name="clock" />
            </button>
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
          <div className="spa-search-input">
            <Icon name="search" />
            <input
              type="search"
              value={query}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder="Найти и добавить товар…"
              aria-label="Поиск товара"
            />
            {query && (
              <button
                type="button"
                className="spa-search-clear"
                onClick={() => handleSearchChange('')}
                aria-label="Очистить поиск"
              >
                <Icon name="x" />
              </button>
            )}
          </div>

          {query.trim().length >= 2 && (
            <div className="card spa-search-results">
              {searchResults.length === 0 ? (
                <div className="spa-search-empty">
                  {searching ? 'Ищем в каталоге…' : 'Ничего не нашлось'}
                </div>
              ) : (
                <ul>
                  {searchResults.map((res) => {
                    const qty = inBasketMap.get(res.product.id) || 0;
                    const priceVals = Object.values(res.prices)
                      .filter((p) => p.in_stock)
                      .map((p) => p.price);
                    const minPrice = priceVals.length ? Math.min(...priceVals) : null;
                    return (
                      <li key={res.product.id} className="spa-search-row">
                        <div className="spa-search-info">
                          <div className="nm">{res.product.name}</div>
                          <small>
                            {res.small}
                            {minPrice !== null && (
                              <>
                                {' · от '}
                                <MoneyWhole value={minPrice} />
                              </>
                            )}
                          </small>
                        </div>
                        {qty > 0 ? (
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
                            className="bt sm lime"
                            onClick={() => onAddFromSearch(res)}
                          >
                            <Icon name="plus" />
                            <span>Добавить</span>
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

        {items.length > 0 ? (
          <div className={`items ${viewMode === 'list' ? 'list' : 'grid'}`}>
            {items.map((item) => {
              const boardPrice = board.pricesByPid[item.product_id];
              const minInfo = minPriceInfo(item.product_id);
              const displayPrice =
                boardPrice !== undefined
                  ? boardPrice
                  : minInfo
                    ? minInfo.price * item.qty
                    : undefined;
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
                          {minInfo && <span>· {minInfo.storeName}</span>}
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
            })}
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
              {onAddAllHabits && habits.length > 1 && (
                <button
                  type="button"
                  className="bt sm"
                  onClick={onAddAllHabits}
                >
                  <Icon name="plus" />
                  <span>Добавить все</span>
                </button>
              )}
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

      {!isDesktop && (
        <div className="ph-foot">
          {board.places.length > 0 && (
            <div className="live">
              <div className="lv-h">
                <span>Где дешевле сейчас</span>
                <button
                  type="button"
                  className="lv-all"
                  onClick={() => onOpenCheaper(board.bestIndex)}
                >
                  <span>Все варианты</span>
                  <Icon name="next" />
                </button>
              </div>
              <div className={`lv-r ${colsClass}`}>
                {board.places.map((p) => (
                  <button
                    key={p.n}
                    type="button"
                    className={`lv${p.best ? ' best' : ''}`}
                    onClick={() => onOpenCheaper(p.n)}
                  >
                    <div className="top-l">
                      <span className="lgs">
                        {p.codes.slice(0, 2).map((code) => (
                          <ChainMark key={code} code={code} />
                        ))}
                      </span>
                      <span className="nm">{p.title}</span>
                    </div>
                    <div className="pr">
                      <MoneyWhole value={p.total} />
                    </div>
                    <div className="df">
                      {p.best ? (
                        p.full ? 'лучший' : `${p.of - p.lack}/${p.of}`
                      ) : p.diff > 0 ? (
                        <>+<MoneyWhole value={p.diff} /></>
                      ) : p.diff < 0 ? (
                        <>−<MoneyWhole value={Math.abs(p.diff)} /></>
                      ) : (
                        'так же'
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          <MobileTabs
            active="basket"
            itemsCount={items.length}
            bestTotal={bestTotal}
            onSelect={(tab) => {
              if (tab === 'cheaper') onOpenCheaper(board.bestIndex);
              else if (tab === 'settings') onOpenSettings();
            }}
          />
        </div>
      )}
    </div>
  );
};
