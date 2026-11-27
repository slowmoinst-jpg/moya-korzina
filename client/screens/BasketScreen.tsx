import React, { useEffect, useMemo, useRef, useState } from 'react';
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
import { getUnconfirmedStores, reportTime } from '../utils/unconfirmed';
import {
  buildTrustProfile,
  estimateSingleStoreWithReplacements,
  rankTrustedReplacements,
} from '../utils/trust';

interface BasketScreenProps {
  ws: WorkspaceSnapshot;
  basketName: string;
  items: BasketItemView[];
  habits: HabitItem[];
  board: ClientBoard;
  viewMode: 'grid' | 'list';
  isDesktop: boolean;
  collectionNotice: string;
  reportStores: string[];
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
  onConnectStore?: (storeCode: string) => void;
}

export const BasketScreen: React.FC<BasketScreenProps> = ({
  ws,
  basketName,
  mode,
  onMode,
  items,
  habits,
  board,
  viewMode,
  isDesktop,
  activeCollecting = [],
  collectionNotice,
  reportStores,
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
  onConnectStore,
}) => {
  const [warnOpen, setWarnOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [layout] = useState(loadBasketLayout);
  const [catalog, setCatalog] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [replacing, setReplacing] = useState<BasketItemView | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
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

  const unconfirmed = useMemo(
    () =>
      getUnconfirmedStores(
        ws.stores,
        ws.connections,
        ws.storeQuotes || {},
        reportStores,
        items.length
      ),
    [ws.stores, ws.connections, ws.storeQuotes, reportStores, items.length]
  );

  const loginNeeded = useMemo(() => unconfirmed.filter((s) => s.needsSettings), [unconfirmed]);
  const passiveUnconfirmed = useMemo(() => unconfirmed.filter((s) => !s.needsSettings), [unconfirmed]);

  const trustProfile = useMemo(
    () => buildTrustProfile(ws.history, ws.products),
    [ws.history, ws.products]
  );

  const productsById = useMemo(
    () => new Map(ws.products.map((p) => [p.id, p])),
    [ws.products]
  );

  const storeNameByCode = useMemo(
    () => new Map(ws.stores.map((s) => [s.code, s.name])),
    [ws.stores]
  );

  // Close overflow menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (ev: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(ev.target as Node)) {
        setMenuOpen(false);
        setConfirmClear(false);
      }
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [menuOpen]);

  const showSearchBox = items.length === 0 || searchOpen || Boolean(replacing) || query.trim().length > 0;

  const openSearch = () => {
    setSearchOpen(true);
    setMenuOpen(false);
    window.setTimeout(() => {
      searchInput.current?.focus();
    }, 20);
  };

  const closeSearch = () => {
    setReplacing(null);
    handleSearchChange('');
    setSearchOpen(false);
  };

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
    setSearchOpen(true);
    setRetry((v) => v + 1);
    const grade = /(?:^|[\s,])([сc][012ов])(?=[\s,.]|$)/iu.exec(item.name);
    const count = /(\d+)\s*шт/iu.exec(item.name);
    const term =
      /яйц/iu.test(item.name) && grade
        ? ['Яйца', grade[1].replace(/c/iu, 'С'), count?.[0]].filter(Boolean).join(' ')
        : item.name;
    handleSearchChange(term);
    window.setTimeout(() => {
      searchInput.current?.focus();
      searchInput.current?.closest('.spa-search-box')?.scrollIntoView({ block: 'start' });
    }, 30);
  };

  const stepItem = (productId: number, currentQty: number, unit: 'pcs' | 'kg', delta: number) => {
    const step = stepForUnit(unit);
    const raw = Math.round((currentQty + delta * step) * 1000) / 1000;
    const nextQty = raw <= 0.0001 ? 0 : raw;
    onUpdateQty(productId, nextQty);
  };

  const preferred = board.allVariants[0];
  const single =
    preferred && preferred.stores.length > 1
      ? board.allVariants.find((v) => v.stores.length === 1)
      : undefined;

  const itemIds = (variant: NonNullable<typeof preferred>) =>
    variant.stores
      .flatMap((store) => store.lines.map((line) => line.product_id))
      .sort((a, b) => a - b)
      .join(',');

  const sameItems = Boolean(preferred && single && itemIds(preferred) === itemIds(single));
  const preferredFull = Boolean(preferred && preferred.missing_products.length === 0);
  const singleFull = Boolean(single && single.missing_products.length === 0);

  // Honest full-basket estimation for Single Store when some items are missing
  const singleEstimate = useMemo(() => {
    if (!single || single.missing_products.length === 0 || !single.stores[0]) return null;
    const singleStore = single.stores[0];
    const singleAssigned = new Set(singleStore.lines.map((l) => l.product_id));
    const singleMissing = items.filter((it) => !singleAssigned.has(it.product_id));
    return estimateSingleStoreWithReplacements(
      singleStore.store_code,
      singleStore.store_name,
      single.total,
      singleStore.lines.length,
      singleMissing,
      ws,
      trustProfile
    );
  }, [single, items, ws, trustProfile]);

  const bestLabel = preferredFull
    ? singleFull
      ? 'Дешевле всего'
      : `Все ${items.length}/${items.length}`
    : single && preferred && preferred.total > single.total
      ? preferred.missing_products.length < single.missing_products.length
        ? 'Больше товаров'
        : 'Лучший вариант'
      : 'Лучший вариант';

  const activeMode = mode === 'single' && !single ? 'best' : mode;
  const chosen = activeMode === 'best' ? preferred : activeMode === 'single' ? single : undefined;
  const scope = replacing ? chosen?.stores.map((s) => s.store_code).sort().join(',') || '' : '';

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
          const targetProd = productsById.get(replacing.product_id);
          const refPrice = minPriceInfo(replacing.product_id)?.price ?? null;
          if (snapshot.isDemo) {
            const raw = localSearchProducts(snapshot, q, Infinity)
              .filter((r) => r.product.id !== replacing.product_id && r.product.unit === replacing.unit)
              .map((r) => {
                const prices = Object.fromEntries(
                  Object.entries(r.prices).filter(
                    ([code, p]) => stores.includes(code) && p.in_stock && p.price > 0
                  )
                );
                return { ...r, prices, available: true };
              })
              .filter((r) => Object.keys(r.prices).length > 0);
            rows = rankTrustedReplacements(
              raw,
              {
                category: targetProd?.category,
                referencePrice: refPrice,
                weight_g: replacing.weight_g,
                unit: replacing.unit,
              },
              trustProfile
            )
              .slice(0, 30)
              .map((r) => ({ ...r.row, trustTier: r.tier, trustLabel: r.label }));
          } else {
            const found = await searchReplacements(q, stores, replacing.unit, replacing.product_id);
            rows = found.results;
            if (current && request === searchRequestRef.current) {
              setObservedStores(found.observedStores ?? null);
            }
          }
        } else {
          rows = await searchServerCatalog(q);
          if (snapshot.isDemo) {
            const seen = new Set(rows.map((r) => r.product.id));
            const local = localSearchProducts(snapshot, q).filter((r) => !seen.has(r.product.id));
            rows = rankProducts([...rows, ...local], purchasedProducts(snapshot.history)).slice(0, 30);
          }
        }
        if (current && request === searchRequestRef.current) setSearchResults(rows);
      } catch {
        if (!current || request !== searchRequestRef.current) return;
        if (replacing) {
          setSearchError(`Не удалось проверить наличие. Магазины варианта: ${names(storesInVariant)}.`);
        } else {
          setSearchResults(localSearchProducts(snapshot, q));
        }
      } finally {
        if (current && request === searchRequestRef.current) setSearching(false);
      }
    }, 180);
    return () => {
      current = false;
      searchRequestRef.current++;
      window.clearTimeout(timer);
    };
  }, [query, scope, replacing, ws.phone, ws.address, retry, trustProfile, productsById]);

  const storesInVariant = chosen?.stores || [];
  const names = (stores: typeof storesInVariant) => stores.map((s) => `«${s.store_name}»`).join(', ');
  const noData = observedStores && storesInVariant.filter((s) => !observedStores.includes(s.store_code));
  const checked = storesInVariant.filter((s) => !noData?.includes(s));
  const replacementEmpty = !storesInVariant.length
    ? 'Выберите вариант корзины для поиска замены.'
    : [
        checked.length && `${names(checked)} — подходящей замены с подтверждённой ценой нет.`,
        noData?.length && `${names(noData)} — нет свежих данных о наличии по вашему адресу.`,
      ]
        .filter(Boolean)
        .join(' ');

  const visibleResults = replacing
    ? searchResults.filter((row) =>
        Object.keys(row.prices).some((code) => scope.split(',').includes(code))
      )
    : searchResults;

  const applyReplacement = async (
    row: SearchResultItem,
    store: string,
    targetItem: BasketItemView | null = replacing
  ) => {
    if (!targetItem || applying.current) return;
    const request = searchRequestRef.current;
    applying.current = true;
    setConfirming(true);
    try {
      const accepted = ws.isDemo ? row : await confirmReplacement(row, store, targetItem.product_id);
      if (request !== searchRequestRef.current && targetItem === replacing) return;
      onAddFromSearch(accepted, store, targetItem.product_id);
      if (targetItem === replacing) {
        setReplacing(null);
        handleSearchChange('');
        setSearchOpen(false);
        searchInput.current?.blur();
      }
    } catch (error) {
      if (request !== searchRequestRef.current && targetItem === replacing) return;
      const stale = (error as { status?: number }).status === 409;
      if (stale) setSearchResults((rows) => rows.filter((r) => r.product.id !== row.product.id));
      setSearchError(stale ? 'Магазин больше не подтверждает эту замену' : 'Не удалось проверить наличие');
    } finally {
      applying.current = false;
      setConfirming(false);
    }
  };

  const byId = new Map(items.map((item) => [item.product_id, item]));
  const assigned = new Set(chosen?.stores.flatMap((store) => store.lines.map((line) => line.product_id)));
  const missing = items.filter((item) => !assigned.has(item.product_id));

  const chosenVariantIdx = chosen ? board.allVariants.indexOf(chosen) : 0;
  const ctaVariant = chosen || preferred;
  const ctaTotal =
    activeMode === 'single' && singleEstimate?.isComplete
      ? singleEstimate.estimatedTotal
      : ctaVariant?.total;

  // Compact honest decision bar (no redundant header row, honest full-basket single-store pricing)
  const panel = !query.trim() && preferred && (
    <section className="basket-plans compact" aria-label="Где дешевле">
      <div className="plan-options">
        <button
          type="button"
          className="best"
          aria-pressed={activeMode === 'best'}
          onClick={() => onMode(activeMode === 'best' ? null : 'best')}
        >
          <span className="plan-top-line">
            <strong>{bestLabel}</strong>
            <span className="plan-marks">
              {preferred.stores.map((s) => (
                <ChainMark key={s.store_code} code={s.store_code} />
              ))}
            </span>
          </span>
          <b><MoneyWhole value={preferred.total} /></b>
          <small>
            {preferred.stores.length === 1
              ? preferred.stores[0].store_name
              : `${preferred.stores.length} магазина`}{' '}
            · {items.length - preferred.missing_products.length} из {items.length}
          </small>
        </button>

        {single && (
          <button
            type="button"
            className={singleFull ? '' : 'plan-partial'}
            aria-pressed={activeMode === 'single'}
            onClick={() => onMode(activeMode === 'single' ? null : 'single')}
          >
            <span className="plan-top-line">
              <strong>Один заказ</strong>
              <span className="plan-marks">
                <ChainMark code={single.stores[0].store_code} />
              </span>
            </span>
            {singleFull ? (
              <>
                <b><MoneyWhole value={single.total} /></b>
                <small>
                  {single.stores[0].store_name}
                  {sameItems && single.total > preferred.total ? (
                    <> · +<MoneyWhole value={single.total - preferred.total} /></>
                  ) : (
                    ` · ${items.length} из ${items.length}`
                  )}
                </small>
              </>
            ) : singleEstimate?.isComplete ? (
              <>
                <b>~<MoneyWhole value={singleEstimate.estimatedTotal} /></b>
                <small>{singleEstimate.summaryLine}</small>
              </>
            ) : (
              <>
                <b className="plan-partial-title">
                  {items.length - single.missing_products.length} из {items.length}
                </b>
                <small>
                  {single.stores[0].store_name} · {single.missing_products.length} на замену →
                </small>
              </>
            )}
          </button>
        )}
      </div>

      {ctaVariant && (
        <button
          className="plan-checkout-bar"
          type="button"
          onClick={() => onOpenCheaper(Math.max(0, chosenVariantIdx))}
        >
          <span>
            {chosen
              ? `К оформлению · ${chosen.stores.length === 1 ? chosen.stores[0].store_name : `${chosen.stores.length} заказа`}`
              : 'Сравнить и оформить'}
          </span>
          <span className="plan-checkout-sum">
            {ctaTotal !== undefined && (
              <>
                {activeMode === 'single' && singleEstimate?.isComplete ? '~' : ''}
                <MoneyWhole value={ctaTotal} />
              </>
            )}
            <Icon name="next" />
          </span>
        </button>
      )}
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
              <span className="pr-val">
                <MoneyWhole value={displayPrice} />
              </span>
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

  const grouped = chosen && (
    <>
      {chosen.stores.map((store) => {
        const minGap = store.below_min_order
          ? Math.max(
              0,
              (ws.stores.find((s) => s.code === store.store_code)?.min_order || 0) - store.subtotal
            )
          : 0;
        return (
          <section key={store.store_code} className="basket-group">
            <header>
              <h2>
                <ChainMark code={store.store_code} />
                <span>{store.store_name}</span>
                <span className="group-count">{store.lines.length} шт</span>
              </h2>
              <div className="group-hd-right">
                <button
                  type="button"
                  className="store-add-inline"
                  onClick={() => setCatalog(store.store_code)}
                  aria-label={`Добавить товар в ${whom(store.store_code, store.store_name)}`}
                >
                  <Icon name="plus" />
                  <span>Товар</span>
                </button>
                <span className="group-total">
                  <MoneyWhole value={store.total} />
                </span>
              </div>
            </header>
            <div className={itemClass}>
              {store.lines.map((line) => {
                const item = byId.get(line.product_id);
                return item && renderItem(item, line.price, true);
              })}
            </div>
            <div className="group-delivery">
              <span>
                Доставка <MoneyWhole value={store.delivery} />
                {store.below_min_order && (
                  <span>
                    {' '}· До минимума <MoneyWhole value={minGap} />
                  </span>
                )}
              </span>
              {store.below_min_order && (
                <button
                  type="button"
                  className="group-topup-link"
                  onClick={() => setCatalog(store.store_code)}
                >
                  + Добить
                </button>
              )}
            </div>
          </section>
        );
      })}

      {missing.length > 0 && (
        <section className="basket-group unavailable">
          <header>
            <h2>
              <span>Нет в этом варианте</span>
              <span className="group-count">{missing.length} шт</span>
            </h2>
          </header>
          <div className={itemClass}>
            {missing.map((item) => {
              const suggestion =
                activeMode === 'single' ? singleEstimate?.suggestions.get(item.product_id) : undefined;
              return (
                <div key={item.product_id} className="missing-item">
                  {renderItem(item)}
                  {suggestion ? (
                    <div className="trust-suggest">
                      <div className="trust-suggest-top">
                        <span className={`trust-badge tier-${suggestion.tier}`}>
                          {suggestion.label}
                        </span>
                        <span className="trust-suggest-price">
                          <MoneyWhole value={suggestion.lineTotal} />
                        </span>
                      </div>
                      <div className="trust-suggest-name">
                        {suggestion.item.product.title || suggestion.item.product.name}
                      </div>
                      <div className="missing-actions">
                        <button
                          type="button"
                          className="bt sm lime"
                          disabled={confirming}
                          onClick={() =>
                            void applyReplacement(suggestion.item, suggestion.storeCode, item)
                          }
                        >
                          Заменить
                        </button>
                        <button
                          type="button"
                          className="bt sm ghost"
                          onClick={() => findReplacement(item)}
                        >
                          Другая
                        </button>
                        <button
                          type="button"
                          className="bt sm ghost"
                          onClick={() => onUpdateQty(item.product_id, 0)}
                        >
                          Убрать
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="missing-actions">
                      <button
                        type="button"
                        className="find-replacement"
                        onClick={() => findReplacement(item)}
                      >
                        Подобрать замену
                      </button>
                      <button
                        type="button"
                        className="bt sm ghost"
                        onClick={() => onUpdateQty(item.product_id, 0)}
                      >
                        Убрать
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </>
  );

  const warnBadgeCount = loginNeeded.length > 0 ? loginNeeded.length : unconfirmed.length;
  const headerTitle = basketName === 'Корзина' ? 'Корзина' : basketName;

  return (
    <div className="spa-screen spa-basket-screen">
      {layout.panel === 'top' && panel}
      <div className="ph-body">
        <header className="spa-basket-top compact">
          <div className="top">
            <button
              type="button"
              className="spa-basket-title-btn"
              onClick={onOpenBaskets}
              title={basketName}
              aria-label={`Мои корзины. Текущая корзина: ${basketName}`}
            >
              <h1>{headerTitle}</h1>
              <Icon name="down" />
              {items.length > 0 && <span className="spa-title-sub">{items.length}</span>}
            </button>

            <div className="top-actions">
              {items.length > 0 && (
                <button
                  type="button"
                  className={`ibtn${showSearchBox ? ' on' : ''}`}
                  onClick={() => (showSearchBox ? closeSearch() : openSearch())}
                  aria-label={showSearchBox ? 'Скрыть поиск' : 'Найти товар'}
                  aria-expanded={showSearchBox}
                  title="Найти товар"
                >
                  <Icon name="search" />
                </button>
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

              {collectionNotice && items.length > 0 && (
                <button
                  type="button"
                  className={`ibtn warn-pill${warnOpen ? ' on' : ''}`}
                  aria-expanded={warnOpen}
                  aria-label={collectionNotice}
                  title={collectionNotice}
                  onClick={() => setWarnOpen((open) => !open)}
                >
                  <Icon name="alert" />
                  {warnBadgeCount > 0 && <span>{warnBadgeCount}</span>}
                </button>
              )}

              <div className="spa-more-wrap" ref={menuRef}>
                <button
                  type="button"
                  className="ibtn"
                  aria-label="Ещё действия"
                  aria-expanded={menuOpen}
                  title="Ещё"
                  onClick={() => {
                    setMenuOpen((v) => !v);
                    setConfirmClear(false);
                  }}
                >
                  <Icon name="more" />
                </button>
                {menuOpen && (
                  <div className="spa-more-menu" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        onOpenBaskets();
                      }}
                    >
                      <Icon name="bag" />
                      <span>Мои корзины ({goods(items.length)})</span>
                    </button>
                    {!isDesktop && (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setMenuOpen(false);
                          onOpenPrices();
                        }}
                      >
                        <Icon name="tag" />
                        <span>Цены по магазинам</span>
                      </button>
                    )}
                    {isDesktop && (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setMenuOpen(false);
                          onOpenOrders();
                        }}
                      >
                        <Icon name="clock" />
                        <span>История заказов</span>
                      </button>
                    )}
                    {items.length > 0 && onClearBasket && (
                      confirmClear ? (
                        <button
                          type="button"
                          role="menuitem"
                          className="danger"
                          onClick={() => {
                            setConfirmClear(false);
                            setMenuOpen(false);
                            onClearBasket();
                          }}
                        >
                          <Icon name="trash" />
                          <span>Точно очистить корзину?</span>
                        </button>
                      ) : (
                        <button
                          type="button"
                          role="menuitem"
                          className="danger"
                          onClick={() => setConfirmClear(true)}
                        >
                          <Icon name="trash" />
                          <span>Очистить корзину</span>
                        </button>
                      )
                    )}
                  </div>
                )}
              </div>
            </div>
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

        {collectionNotice && items.length > 0 && warnOpen && (
          <div className="spa-warn-bar" role="status">
            <button
              type="button"
              className="spa-warn-head"
              aria-expanded={warnOpen}
              onClick={() => setWarnOpen(false)}
            >
              <span className="spa-warn-title">{collectionNotice}</span>
              <span className="spa-warn-chev open" aria-hidden="true">
                <Icon name="down" />
              </span>
            </button>
            {unconfirmed.length > 0 && (
              <div className="spa-warn-list">
                {loginNeeded.map((store) => (
                  <div key={store.storeCode} className="spa-warn-item">
                    <span className="spa-warn-store">
                      <ChainMark code={store.storeCode} />
                      <span className="spa-warn-txt">
                        <strong>{store.storeName}</strong>
                        <span>{[reportTime(ws.storeQuotes?.[store.storeCode]?.updated_at), store.label].filter(Boolean).join(' · ')}</span>
                      </span>
                    </span>
                    <button
                      type="button"
                      className="bt sm"
                      onClick={() =>
                        onConnectStore
                          ? onConnectStore(store.storeCode)
                          : onOpenSettings()
                      }
                    >
                      Войти
                    </button>
                  </div>
                ))}
                {passiveUnconfirmed.length > 0 && (
                  <div className="spa-warn-passive">
                    {passiveUnconfirmed.map((store) => (
                      <div key={store.storeCode} className="spa-warn-passive-row">
                        <ChainMark code={store.storeCode} />
                        <span>
                          <strong>{store.storeName}</strong> · {[reportTime(ws.storeQuotes?.[store.storeCode]?.updated_at), store.label].filter(Boolean).join(' · ')}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {showSearchBox && (
          <div className="spa-search-box">
            {replacing && (
              <div className="replacement-for">
                Замена для «{replacing.name}»
                <small>{chosen?.stores.map((s) => s.store_name).join(' · ')}</small>
              </div>
            )}
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
              {(query || replacing || items.length > 0) && (
                <button
                  type="button"
                  className="spa-search-clear"
                  onClick={closeSearch}
                  aria-label={replacing ? 'Отменить замену' : 'Закрыть поиск'}
                >
                  <Icon name="x" />
                </button>
              )}
            </div>

            {query.trim().length >= 2 && (
              <div className="card spa-search-results">
                {searchError && (
                  <div className="spa-search-empty" role="status">
                    {searchError}{' '}
                    <button type="button" className="bt sm" onClick={() => setRetry((v) => v + 1)}>
                      Повторить
                    </button>
                  </div>
                )}
                {visibleResults.length === 0 ? (
                  <div className="spa-search-empty">
                    {searchError
                      ? null
                      : searching
                        ? 'Ищем в каталоге…'
                        : replacing
                          ? replacementEmpty
                          : 'Ничего не нашлось'}
                  </div>
                ) : (
                  <ul>
                    {visibleResults.map((res) => {
                      const qty = inBasketMap.get(res.product.id) || 0;
                      const offers = Object.values(res.prices)
                        .filter(
                          (p) =>
                            p.in_stock &&
                            p.price > 0 &&
                            (!replacing || scope.split(',').includes(p.store_code))
                        )
                        .sort((a, b) => a.price - b.price);
                      const bestOffer = replacing ? offers[0] : undefined;
                      const trustTag =
                        res.trustLabel ||
                        (res.product.previouslyBought || purchasedProducts(ws.history).has(res.product.id)
                          ? 'Вы покупали'
                          : '');
                      return (
                        <li key={res.product.id} className="spa-search-row">
                          <SearchPic item={res} />
                          <div className="spa-search-info">
                            {offers.length > 0 && (
                              <div className="search-price">
                                {!replacing && offers.some((p) => p.price !== offers[0].price) && (
                                  <span>от </span>
                                )}
                                <MoneyExact value={offers[0].price} />
                              </div>
                            )}
                            <div className="nm">{res.product.title || res.product.name}</div>
                            <small>
                              {bestOffer
                                ? [
                                    trustTag,
                                    storeNameByCode.get(bestOffer.store_code) || bestOffer.store_code,
                                  ]
                                    .filter(Boolean)
                                    .join(' · ')
                                : res.small}
                            </small>
                          </div>
                          {replacing ? (
                            <button
                              type="button"
                              className="bt sm lime"
                              disabled={confirming || !bestOffer}
                              onClick={() => bestOffer && void applyReplacement(res, bestOffer.store_code)}
                            >
                              Заменить
                            </button>
                          ) : qty > 0 ? (
                            <div className="qty">
                              <button
                                type="button"
                                aria-label="Уменьшить"
                                onClick={() => stepItem(res.product.id, qty, res.product.unit, -1)}
                              >
                                <Icon name={qty <= stepForUnit(res.product.unit) ? 'trash' : 'minus'} />
                              </button>
                              <span className="n">{num(qty)}</span>
                              <button
                                type="button"
                                aria-label="Увеличить"
                                onClick={() => stepItem(res.product.id, qty, res.product.unit, 1)}
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
        )}

        {activeCollecting.length > 0 && items.length > 0 && (
          <div className="spa-live-pill" role="status">
            <span className="spa-pulse" aria-hidden="true" />
            <span>
              Проверяем цены и наличие:{' '}
              {activeCollecting
                .slice(0, 3)
                .map((c) => storeNameByCode.get(c) || c)
                .join(', ')}
              {activeCollecting.length > 3 ? ` и ещё ${activeCollecting.length - 3}` : ''}
            </span>
          </div>
        )}

        {chosen ? (
          grouped
        ) : items.length > 0 ? (
          <div className={`items ${viewMode === 'list' ? 'list' : 'grid'}`}>
            {items.map((item) => renderItem(item, minPriceInfo(item.product_id)?.price))}
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
                      <span className={`hc-tag${isDue ? ' due' : ''}`}>{h.why}</span>
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
      {catalog && (
        <StoreProductPicker
          ws={ws}
          code={catalog}
          name={storeNameByCode.get(catalog) || catalog}
          quantities={inBasketMap}
          add={onAddFromSearch}
          close={() => setCatalog(null)}
        />
      )}
    </div>
  );
};
