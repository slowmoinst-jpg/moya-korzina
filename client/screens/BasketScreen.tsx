import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  goods,
  num,
  qtyLabel,
  stepForUnit,
  unitLabel,
} from '../../src/shared/format';
import { searchServerCatalog } from '../api';
import { ChainMark, Icon, MoneyWhole, ProductPic } from '../components/Icons';
import { MobileTabs } from '../components/MobileTabs';
import type { BasketItemView } from '../engine';
import { localSearchProducts } from '../engine';
import { getUnconfirmedStores } from '../utils/unconfirmed';
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
  const [warnOpen, setWarnOpen] = useState(false);
  const [activeTileId, setActiveTileId] = useState<number | null>(null);
  const searchDebounceRef = useRef<number | null>(null);
  const tileTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (searchDebounceRef.current) window.clearTimeout(searchDebounceRef.current);
      if (tileTimerRef.current) window.clearTimeout(tileTimerRef.current);
    };
  }, []);

  const openTileOverlay = useCallback((pid: number) => {
    setActiveTileId(pid);
    if (tileTimerRef.current) window.clearTimeout(tileTimerRef.current);
    tileTimerRef.current = window.setTimeout(() => {
      setActiveTileId(null);
      tileTimerRef.current = null;
    }, 3000);
  }, []);

  const bumpTileOverlayTimer = useCallback((pid: number) => {
    setActiveTileId(pid);
    if (tileTimerRef.current) window.clearTimeout(tileTimerRef.current);
    tileTimerRef.current = window.setTimeout(() => {
      setActiveTileId(null);
      tileTimerRef.current = null;
    }, 3000);
  }, []);

  const storeNameByCode = useMemo(
    () => new Map(ws.stores.map((s) => [s.code, s.name])),
    [ws.stores]
  );

  const minPriceByPid = useMemo(() => {
    const map = new Map<number, { price: number; storeName: string }>();
    for (const [pidKey, pMap] of Object.entries(ws.prices)) {
      if (!pMap) continue;
      let best: { price: number; storeName: string } | null = null;
      for (const [code, snap] of Object.entries(pMap)) {
        if (!snap.in_stock) continue;
        if (!best || snap.price < best.price) {
          best = { price: snap.price, storeName: storeNameByCode.get(code) || code };
        }
      }
      if (best) map.set(Number(pidKey), best);
    }
    return map;
  }, [ws.prices, storeNameByCode]);

  const minPriceInfo = (pid: number): { price: number; storeName: string } | null =>
    minPriceByPid.get(pid) || null;

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

  const inBasketMap = useMemo(
    () => new Map(items.map((it) => [it.product_id, it.qty])),
    [items]
  );

  const stepItem = (productId: number, currentQty: number, unit: 'pcs' | 'kg', delta: number) => {
    const step = stepForUnit(unit);
    const raw = Math.round((currentQty + delta * step) * 1000) / 1000;
    const nextQty = raw <= 0.0001 ? 0 : raw;
    onUpdateQty(productId, nextQty);
    if (nextQty <= 0 && activeTileId === productId) {
      setActiveTileId(null);
    }
  };

  const unconfirmedStores = useMemo(
    () =>
      getUnconfirmedStores(
        ws.stores,
        ws.connections,
        ws.storeQuotes,
        activeCollecting,
        items.length
      ),
    [ws.stores, ws.connections, ws.storeQuotes, activeCollecting, items.length]
  );

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
              <span className="spa-title-sub" aria-label={goods(items.length)}>
                {isDesktop ? goods(items.length) : items.length}
              </span>
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
                <Icon name="table" />
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

        {isDesktop && activeCollecting.length > 0 && items.length > 0 && (
          <div className="spa-live-pill" role="status">
            <span className="lv-spin" aria-hidden="true" />
            <span>
              Проверяем сети:{' '}
              {activeCollecting
                .slice(0, 3)
                .map((c) => storeNameByCode.get(c) || c)
                .join(', ')}
              {activeCollecting.length > 3 ? ` +${activeCollecting.length - 3}` : ''}
            </span>
          </div>
        )}

        {unconfirmedStores.length > 0 && (
          <div className="spa-warn-bar" role="status">
            <button
              type="button"
              className="spa-warn-head"
              aria-expanded={warnOpen}
              onClick={() => setWarnOpen((v) => !v)}
            >
              <span className="spa-warn-title">
                {unconfirmedStores.length === 1
                  ? `${unconfirmedStores[0].storeName} — ${unconfirmedStores[0].label}`
                  : `Не подтвердили корзину: ${unconfirmedStores
                      .slice(0, 2)
                      .map((s) => s.storeName)
                      .join(', ')}${
                      unconfirmedStores.length > 2
                        ? ` и ещё ${unconfirmedStores.length - 2}`
                        : ''
                    }`}
              </span>
              <Icon name="down" className={`i spa-warn-chev${warnOpen ? ' open' : ''}`} />
            </button>

            {warnOpen && (
              <ul className="spa-warn-list">
                {unconfirmedStores.map((st) => (
                  <li key={st.storeCode} className="spa-warn-item">
                    <span className="spa-warn-store">
                      <ChainMark code={st.storeCode} />
                      <span className="spa-warn-txt">
                        <strong>{st.storeName}</strong>
                        <span>{st.label}</span>
                      </span>
                    </span>
                    {st.needsSettings && (
                      <button
                        type="button"
                        className="bt sm"
                        onClick={onOpenSettings}
                      >
                        Войти
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
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
                const isEditing = activeTileId === item.product_id;
                return (
                  <div
                    key={item.product_id}
                    className={`it tile${isEditing ? ' editing' : ''}`}
                  >
                    <div className="tile-photo-wrap">
                      <button
                        type="button"
                        className="tile-trigger"
                        onClick={() => openTileOverlay(item.product_id)}
                        aria-label={`Изменить количество: ${item.name} (${qtyLabel(item.qty, item.unit)})`}
                      >
                        <ProductPic photo={item.photo} title={item.name} />
                        {!isEditing && (
                          <span className="qty-badge">{qtyLabel(item.qty, item.unit)}</span>
                        )}
                      </button>

                      {isEditing && (
                        <div className="qty-overlay" role="group" aria-label={`Количество: ${item.name}`}>
                          <button
                            type="button"
                            className={isLastUnit ? 'del' : ''}
                            aria-label={isLastUnit ? `Убрать ${item.name}` : `Уменьшить ${item.name}`}
                            onClick={() => {
                              stepItem(item.product_id, item.qty, item.unit, -1);
                              if (!isLastUnit) bumpTileOverlayTimer(item.product_id);
                            }}
                          >
                            <Icon name={isLastUnit ? 'trash' : 'minus'} />
                          </button>
                          <span className="n">{num(item.qty)}</span>
                          <button
                            type="button"
                            aria-label={`Добавить ${item.name}`}
                            onClick={() => {
                              stepItem(item.product_id, item.qty, item.unit, 1);
                              bumpTileOverlayTimer(item.product_id);
                            }}
                          >
                            <Icon name="plus" />
                          </button>
                        </div>
                      )}
                    </div>

                    {displayPrice !== undefined && (
                      <div className="pr">
                        <MoneyWhole value={displayPrice} />
                      </div>
                    )}
                    <div className="nm">{item.name}</div>
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
                {activeCollecting.length > 0 ? (
                  <span className="lv-progress" role="status">
                    <span className="lv-spin" aria-hidden="true" />
                    <span>
                      Считаем{' '}
                      {Math.max(
                        0,
                        Object.values(ws.connections).filter((c) => c.connected).length -
                          activeCollecting.length
                      )}
                      /
                      {Math.max(
                        activeCollecting.length,
                        Object.values(ws.connections).filter((c) => c.connected).length
                      )}
                    </span>
                  </span>
                ) : (
                  <span>Где дешевле сейчас</span>
                )}
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
