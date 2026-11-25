import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { humanDay, PHONE_CHAINS } from '../src/shared/format';
import type { StorePerk } from '../src/types';
import {
  ApiUnavailableError,
  disconnectFnsServer,
  fetchAuthMe,
  fetchLiveRuns,
  fetchSyncSnapshot,
  loginByPhone,
  loginDemo,
  logoutSession,
  pushSyncSnapshot,
  submitManualReceipt,
  syncFnsServer,
} from './api';
import { KorzinaBridge } from './device';
import { Icon } from './components/Icons';
import {
  buildBoardClient,
  buildOrdersClient,
  computeBasketStamp,
  computeSingleStoreTotals,
  getActiveBasket,
  getActiveBasketItems,
  getHabitsClient,
  groupOrdersByMonth,
} from './engine';
import { BasketScreen } from './screens/BasketScreen';
import { CheaperScreen } from './screens/CheaperScreen';
import {
  isSyncDirty,
  loadBasketViewMode,
  loadCachedAuth,
  loadCachedSnapshot,
  saveBasketViewMode,
  saveCachedAuth,
  saveCachedSnapshot,
  scheduleSaveCachedSnapshot,
  setSyncDirty,
} from './storage';
import type {
  AuthState,
  HabitItem,
  OrderEntry,
  SearchResultItem,
  SpaScreen,
  WorkspaceSnapshot,
} from './types';

const BasketsScreen = lazy(() =>
  import('./screens/BasketsScreen').then((m) => ({ default: m.BasketsScreen }))
);
const LoginScreen = lazy(() =>
  import('./screens/LoginScreen').then((m) => ({ default: m.LoginScreen }))
);
const OrdersScreen = lazy(() =>
  import('./screens/OrdersScreen').then((m) => ({ default: m.OrdersScreen }))
);
const PricesScreen = lazy(() =>
  import('./screens/PricesScreen').then((m) => ({ default: m.PricesScreen }))
);
const SettingsScreen = lazy(() =>
  import('./screens/SettingsScreen').then((m) => ({ default: m.SettingsScreen }))
);

function readScreenFromLocation(): SpaScreen {
  if (typeof window === 'undefined') return 'basket';
  const raw =
    window.location.protocol === 'file:'
      ? window.location.hash.replace(/^#/, '')
      : window.location.pathname;
  if (raw.includes('/cheaper')) return 'cheaper';
  if (raw.includes('/orders')) return 'orders';
  if (raw.includes('/baskets')) return 'baskets';
  if (raw.includes('/prices')) return 'prices';
  if (raw.includes('/settings')) return 'settings';
  return 'basket';
}

function pushScreenToHistory(screen: SpaScreen): void {
  if (typeof window === 'undefined') return;
  const pathMap: Record<SpaScreen, string> = {
    basket: '/phone/basket',
    cheaper: '/phone/cheaper',
    orders: '/phone/orders',
    baskets: '/phone/baskets',
    prices: '/phone/prices',
    settings: '/phone/settings',
  };
  const target = pathMap[screen];
  try {
    if (window.location.protocol === 'file:') {
      window.location.hash = target;
    } else if (window.location.pathname !== target) {
      window.history.pushState({ screen }, '', target);
    }
  } catch {
    // ignore history errors in sandboxed frames
  }
}

export const App: React.FC = () => {
  const [auth, setAuth] = useState<AuthState | null>(() => loadCachedAuth());
  const [ws, setWs] = useState<WorkspaceSnapshot | null>(() => {
    const cached = loadCachedAuth();
    return cached?.phone ? loadCachedSnapshot(cached.phone) : null;
  });
  const [booting, setBooting] = useState<boolean>(() => !loadCachedAuth());
  const [screen, setScreen] = useState<SpaScreen>(() => readScreenFromLocation());
  const [pickedPlace, setPickedPlace] = useState<number | null>(null);
  const [pendingAuthStore, setPendingAuthStore] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>(() => loadBasketViewMode());
  const [toast, setToast] = useState<string | null>(null);
  const [activeCollecting, setActiveCollecting] = useState<string[]>([]);
  const [isDesktop, setIsDesktop] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 960 : false
  );

  const syncTimerRef = useRef<number | null>(null);
  const syncInFlightRef = useRef<Promise<string | null> | null>(null);
  const serverBasketStampRef = useRef<string | null>(ws?.basketStamp ?? null);
  const pricesVersionRef = useRef<number | null>(null);
  const activeBasketIdRef = useRef<number | null>(null);
  const collectTimerRef = useRef<number | null>(null);
  const lastCollectedKeyRef = useRef<string>('');
  const toastTimerRef = useRef<number | null>(null);

  useEffect(() => {
    serverBasketStampRef.current = ws?.basketStamp ?? null;
  }, [ws?.phone, ws?.basketStamp]);

  const notify = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(() => setToast(null), 3200);
  }, []);

  // Track responsive breakpoint (mobile < 960px vs 2-column desktop >= 960px)
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 960px)');
    const onChange = () => setIsDesktop(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Browser back/forward navigation
  useEffect(() => {
    const onPop = () => setScreen(readScreenFromLocation());
    window.addEventListener('popstate', onPop);
    window.addEventListener('hashchange', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('hashchange', onPop);
    };
  }, []);

  const navigate = useCallback((next: SpaScreen) => {
    setScreen(next);
    pushScreenToHistory(next);
  }, []);

  // Background server hydration & reconciliation on mount (never blocks cached UI!)
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const me = await fetchAuthMe();
        if (!active) return;
        if (me.authenticated && me.phone) {
          setAuth(me);
          saveCachedAuth(me);
          const cached = loadCachedSnapshot(me.phone);
          if (cached && isSyncDirty(me.phone) && !me.isDemo) {
            try {
              await pushSyncSnapshot(cached);
              setSyncDirty(me.phone, false);
            } catch {
              // still offline or failed; keep local dirty state
            }
          }
          const fresh = await fetchSyncSnapshot();
          if (!active) return;
          setWs(fresh);
          saveCachedSnapshot(fresh);
        } else {
          // If user already has a valid Local-First session in localStorage, silently restore server session token so API calls never 401
          const cachedAuth = loadCachedAuth();
          const cachedWs = cachedAuth?.phone ? loadCachedSnapshot(cachedAuth.phone) : null;
          if (cachedAuth?.authenticated && cachedWs) {
            setAuth(cachedAuth);
            setWs(cachedWs);
            if (!cachedAuth.isDemo && !me.pinRequired && cachedAuth.phone) {
              try {
                await loginByPhone(cachedAuth.phone, '');
                if (isSyncDirty(cachedAuth.phone)) {
                  await pushSyncSnapshot(cachedWs);
                  setSyncDirty(cachedAuth.phone, false);
                }
              } catch {
                // offline or proxy-redirected; stay in Local-First mode seamlessly
              }
            }
          } else {
            setAuth(me);
            saveCachedAuth(me);
            setWs(null);
          }
        }
      } catch {
        // Offline or proxy-redirected startup: keep cached auth & snapshot if present
        if (!active) return;
        setAuth((prev) => prev || { authenticated: false, phone: null, phoneLabel: '', isDemo: false, pinRequired: false });
      } finally {
        if (active) setBooting(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Auto-flush dirty offline edits when network returns
  useEffect(() => {
    const onOnline = () => {
      if (ws && !ws.isDemo && isSyncDirty(ws.phone)) {
        void pushSyncSnapshot(ws)
          .then(() => {
            setSyncDirty(ws.phone, false);
            notify('Изменения синхронизированы с сервером');
          })
          .catch(() => {});
      }
    };
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [ws, notify]);

  const flushSync = useCallback((snapshot: WorkspaceSnapshot): Promise<string | null> => {
    if (snapshot.isDemo) return Promise.resolve(null);
    const p = pushSyncSnapshot(snapshot)
      .then((res) => {
        setSyncDirty(snapshot.phone, false);
        const nextStamp = res.basketStamp || null;
        if (nextStamp) {
          serverBasketStampRef.current = nextStamp;
          setWs((prev) => {
            if (!prev || prev.basketStamp === nextStamp) return prev;
            const updated = { ...prev, basketStamp: nextStamp };
            scheduleSaveCachedSnapshot(updated);
            return updated;
          });
        }
        if (res.serverStarted && res.serverStarted.length > 0) {
          setActiveCollecting((prev) => [...new Set([...prev, ...res.serverStarted!])]);
        }
        return nextStamp;
      })
      .catch(() => {
        // Keep dirty flag for next online sync
        return null;
      })
      .finally(() => {
        if (syncInFlightRef.current === p) {
          syncInFlightRef.current = null;
        }
      });
    syncInFlightRef.current = p;
    return p;
  }, []);

  // Persist locally debounced (never blocks rapid +/- clicks) and schedule fast background server sync
  const commitWorkspace = useCallback(
    (updater: (prev: WorkspaceSnapshot) => WorkspaceSnapshot) => {
      setWs((prev) => {
        if (!prev) return prev;
        const next = updater(prev);
        scheduleSaveCachedSnapshot(next);

        if (!next.isDemo) {
          setSyncDirty(next.phone, true);
          if (syncTimerRef.current) window.clearTimeout(syncTimerRef.current);
          syncTimerRef.current = window.setTimeout(() => {
            syncTimerRef.current = null;
            void flushSync(next);
          }, 220);
        }
        return next;
      });
    },
    [flushSync]
  );

  // Derived data calculated locally in <3ms
  const activeBasket = useMemo(() => (ws ? getActiveBasket(ws) : null), [ws]);
  const activeBasketId = activeBasket?.id ?? null;
  activeBasketIdRef.current = activeBasketId;

  const items = useMemo(
    () => (ws ? getActiveBasketItems(ws, activeBasketId) : []),
    [ws, activeBasketId]
  );

  const board = useMemo(
    () =>
      ws
        ? buildBoardClient(ws, activeBasketId, pickedPlace)
        : {
            places: [],
            morePlaces: [],
            allVariants: [],
            chosenVariant: null,
            bestIndex: 0,
            onIndex: 0,
            baseline: 0,
            positions: 0,
            pricesByPid: {},
            when: '',
          },
    [ws, activeBasketId, pickedPlace]
  );

  const habits = useMemo(
    () => (ws ? getHabitsClient(ws, activeBasketId, 10) : []),
    [ws, activeBasketId]
  );

  const orders = useMemo(
    () => (ws && screen === 'orders' ? buildOrdersClient(ws) : []),
    [ws, screen]
  );
  const groupedOrders = useMemo(
    () => (screen === 'orders' ? groupOrdersByMonth(orders) : []),
    [orders, screen]
  );
  const storeTotals = useMemo(
    () => (ws && screen === 'settings' ? computeSingleStoreTotals(ws, activeBasketId) : {}),
    [ws, activeBasketId, screen]
  );

  const basketStamp = useMemo(
    () => computeBasketStamp(activeBasketId, items, ws?.address ?? null),
    [activeBasketId, items, ws?.address]
  );

  const connectedSig = useMemo(() => {
    if (!ws) return '';
    return PHONE_CHAINS.filter((c) => ws.connections[c]?.connected).join(',');
  }, [ws]);

  // Pull live collection quotes, delta prices for active basket, connections, and detected bonuses from server
  const refreshLiveRuns = useCallback(async () => {
    const busy = KorzinaBridge.busy();
    setActiveCollecting([...new Set([...busy.going, ...busy.soon])]);
    try {
      const live = await fetchLiveRuns({
        basketId: activeBasketIdRef.current,
        pricesVersion: pricesVersionRef.current,
      });
      if (live.basketStamp) {
        serverBasketStampRef.current = live.basketStamp;
      }
      if (typeof live.pricesVersion === 'number') {
        pricesVersionRef.current = live.pricesVersion;
      }
      setWs((prev) => {
        if (!prev) return prev;
        const next: WorkspaceSnapshot = {
          ...prev,
          storeQuotes: live.storeQuotes,
          connections: { ...prev.connections, ...live.connections },
          prices: live.prices ? { ...prev.prices, ...live.prices } : prev.prices,
          storePerks: { ...(prev.storePerks || {}), ...live.storePerks },
          basketStamp: live.basketStamp || prev.basketStamp || null,
          syncedAt: live.syncedAt,
        };
        scheduleSaveCachedSnapshot(next);
        return next;
      });
      const collectingFromQuotes = Object.values(live.storeQuotes || {})
        .filter((q) => q.status === 'collecting')
        .map((q) => q.store_code);
      setActiveCollecting([...new Set([...busy.going, ...busy.soon, ...collectingFromQuotes])]);
    } catch {
      // ignore transient offline errors
    }
  }, []);

  // Listen for Android MainActivity.java tellPage() -> window.kzRunsChanged() and visibility return from WebView login
  useEffect(() => {
    if (!auth?.authenticated) return;
    window.kzRunsChanged = () => {
      void refreshLiveRuns();
    };
    const onFocusOrVisible = () => {
      if (document.visibilityState === 'visible') {
        void refreshLiveRuns();
      }
    };
    window.addEventListener('focus', onFocusOrVisible);
    document.addEventListener('visibilitychange', onFocusOrVisible);
    return () => {
      delete window.kzRunsChanged;
      window.removeEventListener('focus', onFocusOrVisible);
      document.removeEventListener('visibilitychange', onFocusOrVisible);
    };
  }, [auth?.authenticated, refreshLiveRuns]);

  // Adaptive live polling while any chain (server LINK or Android WebView) is actively collecting
  useEffect(() => {
    if (!auth?.authenticated || activeCollecting.length === 0) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void refreshLiveRuns();
      }
    }, 2500);
    return () => window.clearInterval(id);
  }, [auth?.authenticated, activeCollecting.length, refreshLiveRuns]);

  // Trigger Android background cart assembly automatically after any basket or address change across all 11 chains
  const triggerBackgroundCollect = useCallback(
    async (force = false) => {
      if (!ws || activeBasketId === null || !items.length || !basketStamp) return;
      if (!KorzinaBridge.available()) return;

      const collectKey = `${basketStamp}::${connectedSig}`;
      if (!force && lastCollectedKeyRef.current === collectKey) return;
      lastCollectedKeyRef.current = collectKey;

      let syncedStamp: string | null = null;
      if (!ws.isDemo) {
        if (syncTimerRef.current) {
          window.clearTimeout(syncTimerRef.current);
          syncTimerRef.current = null;
        }
        if (syncInFlightRef.current) {
          syncedStamp = await syncInFlightRef.current;
        }
        if (isSyncDirty(ws.phone)) {
          syncedStamp = (await flushSync(ws)) || syncedStamp;
        }
      }

      const stampForBridge =
        (!ws.isDemo && (syncedStamp || serverBasketStampRef.current || ws.basketStamp)) ||
        basketStamp;

      const chains = PHONE_CHAINS.map((code) => ({
        code,
        login: ws.connections[code]?.connected ? 'in' : 'connect',
        cookies: code === 'magnit' ? ['mg_at'] : [],
        sent: false,
      }));

      KorzinaBridge.collect({
        basket: activeBasketId,
        stamp: stampForBridge,
        address: ws.address || '',
        point: ws.point,
        chains,
      });

      const busy = KorzinaBridge.busy();
      setActiveCollecting((prev) => [...new Set([...prev, ...busy.going, ...busy.soon])]);
    },
    [ws, activeBasketId, items.length, basketStamp, connectedSig, flushSync]
  );

  useEffect(() => {
    if (!KorzinaBridge.available()) return;
    if (!ws || activeBasketId === null || !items.length || !basketStamp) return;
    if (collectTimerRef.current) window.clearTimeout(collectTimerRef.current);
    collectTimerRef.current = window.setTimeout(() => {
      void triggerBackgroundCollect(false);
    }, 250);
    return () => {
      if (collectTimerRef.current) window.clearTimeout(collectTimerRef.current);
    };
  }, [ws, activeBasketId, items.length, basketStamp, connectedSig, triggerBackgroundCollect]);

  // Auth handlers (server-first with seamless Local-First fallback if offline or proxy-redirected)
  const handleLogin = async (phone: string, pin: string) => {
    const digits = phone.replace(/\D/g, '');
    if (!digits) {
      throw new Error('Введите номер телефона');
    }

    try {
      const res = await loginByPhone(phone, pin);
      pricesVersionRef.current = null;
      setAuth(res.auth);
      saveCachedAuth(res.auth);
      setWs(res.snapshot);
      saveCachedSnapshot(res.snapshot);
      setPickedPlace(null);
      navigate('basket');
      return;
    } catch (err) {
      if (!(err instanceof ApiUnavailableError)) {
        throw err;
      }
      // Backend unreachable or intercepted by preview proxy redirect — open Local-First workspace immediately
      const seed = await import('./localSeed');
      const [normalized, validationErr] = seed.resolveLoginLocal(phone);
      if (!normalized) {
        throw new Error(validationErr || 'Введите номер телефона');
      }
      const localAuth: AuthState = {
        authenticated: true,
        phone: normalized,
        phoneLabel: seed.formatPhoneDisplayLocal(normalized),
        isDemo: false,
        pinRequired: false,
      };
      const localWs = loadCachedSnapshot(normalized) || seed.buildLocalWorkspace(normalized);
      pricesVersionRef.current = null;
      setAuth(localAuth);
      saveCachedAuth(localAuth);
      setWs(localWs);
      saveCachedSnapshot(localWs);
      setSyncDirty(normalized, true);
      setPickedPlace(null);
      navigate('basket');
      notify('Работаем локально на устройстве — синхронизация выполнится при подключении к серверу');
    }
  };

  const handleDemo = async () => {
    try {
      const res = await loginDemo();
      pricesVersionRef.current = null;
      setAuth(res.auth);
      saveCachedAuth(res.auth);
      setWs(res.snapshot);
      saveCachedSnapshot(res.snapshot);
      setPickedPlace(null);
      navigate('basket');
      return;
    } catch (err) {
      if (!(err instanceof ApiUnavailableError)) {
        throw err;
      }
      const seed = await import('./localSeed');
      const localAuth: AuthState = {
        authenticated: true,
        phone: seed.DEMO_PHONE,
        phoneLabel: 'Демо',
        isDemo: true,
        pinRequired: false,
      };
      const localWs = loadCachedSnapshot(seed.DEMO_PHONE) || seed.buildLocalWorkspace(seed.DEMO_PHONE);
      pricesVersionRef.current = null;
      setAuth(localAuth);
      saveCachedAuth(localAuth);
      setWs(localWs);
      saveCachedSnapshot(localWs);
      setPickedPlace(null);
      navigate('basket');
    }
  };

  const handleLogout = async () => {
    try {
      await logoutSession();
    } catch {
      // clear locally anyway
    }
    saveCachedAuth(null);
    setAuth({ authenticated: false, phone: null, phoneLabel: '', isDemo: false, pinRequired: Boolean(auth?.pinRequired) });
    setWs(null);
    setPickedPlace(null);
    navigate('basket');
  };

  // Basket mutations (100% local & instant)
  const ensureBasket = (prev: WorkspaceSnapshot): { next: WorkspaceSnapshot; basketId: number } => {
    const cur = getActiveBasket(prev);
    if (cur) return { next: prev, basketId: cur.id };
    const id = prev.nextBasketId;
    const basket = {
      id,
      name: `Корзина от ${humanDay(new Date().toISOString().slice(0, 10))}`,
      created_at: new Date().toISOString().slice(0, 10),
      source: 'manual' as const,
    };
    return {
      basketId: id,
      next: {
        ...prev,
        baskets: [basket, ...prev.baskets],
        nextBasketId: id + 1,
        basketItems: { ...prev.basketItems, [String(id)]: [] },
        lastBasketId: id,
      },
    };
  };

  const handleUpdateQty = useCallback(
    (productId: number, nextQty: number) => {
      commitWorkspace((prev) => {
        const { next, basketId } = ensureBasket(prev);
        const key = String(basketId);
        const list = [...(next.basketItems[key] || [])];
        const idx = list.findIndex((i) => i.product_id === productId);
        if (nextQty <= 0) {
          if (idx !== -1) list.splice(idx, 1);
        } else if (idx !== -1) {
          list[idx] = { ...list[idx], qty: nextQty };
        } else {
          list.push({ basket_id: basketId, product_id: productId, qty: nextQty });
        }
        return {
          ...next,
          basketItems: { ...next.basketItems, [key]: list },
          lastBasketId: basketId,
        };
      });
    },
    [commitWorkspace]
  );

  const handleAddFromSearch = useCallback(
    (res: SearchResultItem) => {
      commitWorkspace((prev) => {
        const { next, basketId } = ensureBasket(prev);
        const exists = next.products.some((p) => p.id === res.product.id);
        const products = exists ? next.products : [...next.products, res.product];
        const prices = {
          ...next.prices,
          [String(res.product.id)]: {
            ...(next.prices[String(res.product.id)] || {}),
            ...res.prices,
          },
        };
        const key = String(basketId);
        const list = [...(next.basketItems[key] || [])];
        const idx = list.findIndex((i) => i.product_id === res.product.id);
        if (idx !== -1) {
          list[idx] = { ...list[idx], qty: Math.round((list[idx].qty + 1) * 1000) / 1000 };
        } else {
          list.push({ basket_id: basketId, product_id: res.product.id, qty: 1 });
        }
        return {
          ...next,
          products,
          prices,
          basketItems: { ...next.basketItems, [key]: list },
          lastBasketId: basketId,
        };
      });
    },
    [commitWorkspace]
  );

  const handleAddHabit = useCallback(
    (habit: HabitItem) => {
      handleUpdateQty(habit.pid, habit.qty || 1);
      notify(`Добавлено: ${habit.name}`);
    },
    [handleUpdateQty, notify]
  );

  const handleAddTopupProduct = useCallback(
    (productId: number, productName: string) => {
      const existing = items.find((i) => i.product_id === productId);
      const nextQty = existing ? Math.round((existing.qty + 1) * 1000) / 1000 : 1;
      handleUpdateQty(productId, nextQty);
      notify(`Добавлено: ${productName}`);
    },
    [items, handleUpdateQty, notify]
  );

  const handleAddAllHabits = useCallback(() => {
    if (!habits.length) return;
    commitWorkspace((prev) => {
      const { next, basketId } = ensureBasket(prev);
      const key = String(basketId);
      const list = [...(next.basketItems[key] || [])];
      const existing = new Set(list.map((i) => i.product_id));
      for (const h of habits) {
        if (!existing.has(h.pid)) {
          list.push({ basket_id: basketId, product_id: h.pid, qty: h.qty || 1 });
          existing.add(h.pid);
        }
      }
      return {
        ...next,
        basketItems: { ...next.basketItems, [key]: list },
        lastBasketId: basketId,
      };
    });
    notify(`Добавлено ${habits.length} привычных товаров`);
  }, [habits, commitWorkspace, notify]);

  const handleClearBasket = useCallback(() => {
    if (activeBasketId === null) return;
    commitWorkspace((prev) => ({
      ...prev,
      basketItems: { ...prev.basketItems, [String(activeBasketId)]: [] },
    }));
    setPickedPlace(null);
    notify('Корзина очищена');
  }, [activeBasketId, commitWorkspace, notify]);

  const handleRefreshLive = useCallback(() => {
    void refreshLiveRuns();
    if (KorzinaBridge.available()) {
      void triggerBackgroundCollect(true);
      notify('Запущена проверка цен и корзин в сетях…');
    } else {
      notify('Данные обновлены');
    }
  }, [refreshLiveRuns, triggerBackgroundCollect, notify]);

  const handleRepeatOrder = useCallback(
    (order: OrderEntry) => {
      commitWorkspace((prev) => {
        const id = prev.nextBasketId;
        const basket = {
          id,
          name: `${order.store_name} · ${order.day}`,
          created_at: new Date().toISOString().slice(0, 10),
          source: 'history' as const,
        };
        const newItems = order.lines
          .filter((ln) => ln.product_id !== null)
          .map((ln) => ({
            basket_id: id,
            product_id: ln.product_id!,
            qty: ln.qty,
          }));
        return {
          ...prev,
          baskets: [basket, ...prev.baskets],
          nextBasketId: id + 1,
          basketItems: { ...prev.basketItems, [String(id)]: newItems },
          lastBasketId: id,
          repeated: { key: order.key, basket: id },
        };
      });
      setPickedPlace(null);
      notify(`Заказ из ${order.store_name} добавлен как новая корзина`);
      navigate('basket');
    },
    [commitWorkspace, notify, navigate]
  );

  const handleSelectBasket = useCallback(
    (id: number) => {
      commitWorkspace((prev) => ({ ...prev, lastBasketId: id }));
      setPickedPlace(null);
      navigate('basket');
    },
    [commitWorkspace, navigate]
  );

  const handleCreateBasket = useCallback(
    (name: string) => {
      commitWorkspace((prev) => {
        const id = prev.nextBasketId;
        const basket = {
          id,
          name,
          created_at: new Date().toISOString().slice(0, 10),
          source: 'manual' as const,
        };
        return {
          ...prev,
          baskets: [basket, ...prev.baskets],
          nextBasketId: id + 1,
          basketItems: { ...prev.basketItems, [String(id)]: [] },
          lastBasketId: id,
        };
      });
      setPickedPlace(null);
      navigate('basket');
    },
    [commitWorkspace, navigate]
  );

  const handleDeleteBasket = useCallback(
    (id: number) => {
      commitWorkspace((prev) => {
        const remaining = prev.baskets.filter((b) => b.id !== id);
        const nextItems = { ...prev.basketItems };
        delete nextItems[String(id)];
        return {
          ...prev,
          baskets: remaining,
          basketItems: nextItems,
          lastBasketId: prev.lastBasketId === id ? (remaining[0]?.id ?? null) : prev.lastBasketId,
        };
      });
      setPickedPlace(null);
    },
    [commitWorkspace]
  );

  const handleToggleStore = useCallback(
    (code: string, connect: boolean, mode?: 'session' | 'manual') => {
      commitWorkspace((prev) => ({
        ...prev,
        connections: {
          ...prev.connections,
          [code]: {
            store_code: code,
            connected: connect,
            again: false,
            last: connect ? new Date().toISOString().slice(0, 16).replace('T', ' ') : null,
            mode: connect ? mode || 'manual' : undefined,
          },
        },
      }));
      const storeName = ws?.stores.find((s) => s.code === code)?.name || code;
      notify(
        connect
          ? mode === 'session'
            ? `${storeName}: сессия подключена`
            : `${storeName}: отмечен подключённым`
          : `${storeName} отключён`
      );
    },
    [commitWorkspace, ws, notify]
  );

  const handleSaveAddress = useCallback(
    (address: string | null, point: [number, number] | null) => {
      commitWorkspace((prev) => ({ ...prev, address, point }));
      notify(address ? 'Адрес сохранён — проверяем наличие и доставку' : 'Адрес очищен');
    },
    [commitWorkspace, notify]
  );

  const handleUpdateStorePerk = useCallback(
    (code: string, perk: StorePerk) => {
      commitWorkspace((prev) => ({
        ...prev,
        storePerks: {
          ...(prev.storePerks || {}),
          [code]: {
            store_code: code,
            promo_label: perk.promo_label ? perk.promo_label.trim() : null,
            promo_percent: Math.max(0, Math.min(90, Number(perk.promo_percent) || 0)),
            promo_rub: Math.max(0, Number(perk.promo_rub) || 0),
            bonus_balance: Math.max(0, Number(perk.bonus_balance) || 0),
            bonus_max_pct: Math.max(0, Math.min(100, Number(perk.bonus_max_pct) || 30)),
          },
        },
      }));
      const storeName = ws?.stores.find((s) => s.code === code)?.name || code;
      notify(`${storeName}: скидки и бонусы обновлены`);
    },
    [commitWorkspace, ws, notify]
  );

  const handleToggleApplyBonuses = useCallback(
    (next: boolean) => {
      commitWorkspace((prev) => ({ ...prev, applyBonuses: next }));
    },
    [commitWorkspace]
  );

  const handleToggleOffer = useCallback(
    (offerId: number) => {
      commitWorkspace((prev) => ({
        ...prev,
        offers: prev.offers.map((o) =>
          o.id === offerId ? { ...o, activated: !o.activated } : o
        ),
      }));
    },
    [commitWorkspace]
  );

  const handleAddOffer = useCallback(
    (input: { bank: string; cardName: string; storeId: number; percent: number; capRub: number }) => {
      commitWorkspace((prev) => {
        let card = prev.cards.find(
          (c) =>
            c.bank.toLowerCase() === input.bank.toLowerCase() &&
            c.name.toLowerCase() === input.cardName.toLowerCase()
        );
        const cards = [...prev.cards];
        if (!card) {
          const nextCardId = cards.reduce((m, c) => Math.max(m, c.id), 0) + 1;
          card = { id: nextCardId, bank: input.bank, name: input.cardName };
          cards.push(card);
        }
        const nextOfferId = prev.offers.reduce((m, o) => Math.max(m, o.id), 0) + 1;
        const now = new Date();
        const validFrom = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
        const validTo = new Date(now.getFullYear(), now.getMonth() + 2, 0).toISOString().slice(0, 10);
        return {
          ...prev,
          cards,
          offers: [
            ...prev.offers,
            {
              id: nextOfferId,
              card_id: card.id,
              store_id: input.storeId,
              percent: input.percent,
              cap_rub: input.capRub,
              min_check_rub: 0,
              valid_from: validFrom,
              valid_to: validTo,
              requires_activation: false,
              activated: true,
              cap_used: 0,
            },
          ],
        };
      });
      notify('Кэшбэк добавлен — цены пересчитаны');
    },
    [commitWorkspace, notify]
  );

  const handleDeleteOffer = useCallback(
    (offerId: number) => {
      commitWorkspace((prev) => ({
        ...prev,
        offers: prev.offers.filter((o) => o.id !== offerId),
      }));
      notify('Предложение удалено');
    },
    [commitWorkspace, notify]
  );

  const handleSyncFns = useCallback(
    async (phone: string, keys?: string) => {
      if (ws?.isDemo) {
        notify('В демо-режиме чеки уже загружены');
        return;
      }
      try {
        const res = await syncFnsServer(phone, keys);
        setWs(res.snapshot);
        saveCachedSnapshot(res.snapshot);
        notify(keys ? 'Кабинет ФНС подключён' : 'Чеки ФНС обновлены');
      } catch (err) {
        if (err instanceof ApiUnavailableError) {
          commitWorkspace((prev) => ({
            ...prev,
            fnsConnected: true,
            fnsPhone: phone || prev.phoneLabel,
            fnsSyncedAt: new Date().toISOString().slice(0, 16).replace('T', ' '),
          }));
          notify('Подключение ФНС сохранено локально');
          return;
        }
        throw err;
      }
    },
    [ws, commitWorkspace, notify]
  );

  const handleDisconnectFns = useCallback(async () => {
    if (ws?.isDemo) return;
    try {
      const res = await disconnectFnsServer();
      setWs(res.snapshot);
      saveCachedSnapshot(res.snapshot);
      notify('Кабинет ФНС отключён');
    } catch (err) {
      if (err instanceof ApiUnavailableError) {
        commitWorkspace((prev) => ({
          ...prev,
          fnsConnected: false,
          fnsSyncedAt: null,
        }));
        notify('Кабинет ФНС отключён локально');
        return;
      }
      throw err;
    }
  }, [ws, commitWorkspace, notify]);

  const handlePasteReceipt = useCallback(
    async (storeCode: string, text: string) => {
      if (ws?.isDemo) {
        const seed = await import('./localSeed');
        let importedCount = 0;
        commitWorkspace((prev) => {
          const res = seed.applyManualReceiptLocal(prev, storeCode, text);
          importedCount = res.imported;
          return res.next;
        });
        notify(`Добавлено ${importedCount} поз. из чека (демо)`);
        return;
      }

      try {
        const res = await submitManualReceipt(storeCode, text);
        setWs(res.snapshot);
        saveCachedSnapshot(res.snapshot);
        notify(`Добавлено ${res.imported} поз. из чека`);
      } catch (err) {
        if (err instanceof ApiUnavailableError) {
          const seed = await import('./localSeed');
          let importedCount = 0;
          commitWorkspace((prev) => {
            const res = seed.applyManualReceiptLocal(prev, storeCode, text);
            importedCount = res.imported;
            return res.next;
          });
          notify(`Чек сохранён локально (${importedCount} поз.)`);
          return;
        }
        throw err;
      }
    },
    [ws, commitWorkspace, notify]
  );

  const handleChangeView = useCallback((mode: 'grid' | 'list') => {
    setViewMode(mode);
    saveBasketViewMode(mode);
  }, []);

  const collectingChains = useMemo(() => {
    const fromQuotes = Object.values(ws?.storeQuotes || {})
      .filter((q) => q.status === 'collecting')
      .map((q) => q.store_code);
    return [...new Set([...activeCollecting, ...fromQuotes])];
  }, [activeCollecting, ws?.storeQuotes]);

  if (booting) {
    return (
      <div className="spa-boot">
        <div className="spa-boot-mark">
          <Icon name="cart" />
        </div>
        <span>Открываем вашу корзину…</span>
      </div>
    );
  }

  const screenFallback = (
    <div className="spa-boot">
      <span>Загружаем…</span>
    </div>
  );

  if (!auth?.authenticated || !ws) {
    return (
      <Suspense fallback={screenFallback}>
        <LoginScreen
          pinRequired={Boolean(auth?.pinRequired)}
          onLogin={handleLogin}
          onDemo={handleDemo}
        />
      </Suspense>
    );
  }

  const bestTotal = board.places.length > 0 ? board.places[0].total : null;

  const applyBonuses = ws.applyBonuses !== false;

  const effectiveBasketStamp =
    (!ws.isDemo && (ws.basketStamp || serverBasketStampRef.current)) || basketStamp;

  const leftScreenContent = (() => {
    if (screen === 'cheaper' && !isDesktop) {
      return (
        <CheaperScreen
          ws={ws}
          basketId={activeBasketId}
          basketStamp={effectiveBasketStamp}
          board={board}
          itemsCount={items.length}
          applyBonuses={applyBonuses}
          activeCollecting={collectingChains}
          onToggleApplyBonuses={handleToggleApplyBonuses}
          onPickPlace={(idx) => setPickedPlace(idx)}
          onAddTopupProduct={handleAddTopupProduct}
          onBack={() => navigate('basket')}
          onOpenSettings={() => navigate('settings')}
          onRefreshLive={handleRefreshLive}
          onConnectStore={(code) => {
            if (KorzinaBridge.available()) {
              KorzinaBridge.login(code);
              notify('Открываем вход в магазин…');
              return;
            }
            setPendingAuthStore(code);
            navigate('settings');
          }}
          onNotify={notify}
        />
      );
    }

    if (screen === 'orders') {
      return (
        <Suspense fallback={screenFallback}>
          <OrdersScreen
            groupedOrders={groupedOrders}
            repeatedKey={ws.repeated?.key ?? null}
            onRepeatOrder={handleRepeatOrder}
            onBack={() => navigate('basket')}
            onOpenSettings={() => navigate('settings')}
          />
        </Suspense>
      );
    }

    if (screen === 'baskets') {
      return (
        <Suspense fallback={screenFallback}>
          <BasketsScreen
            ws={ws}
            activeBasketId={activeBasketId}
            onSelectBasket={handleSelectBasket}
            onCreateBasket={handleCreateBasket}
            onDeleteBasket={handleDeleteBasket}
            onBack={() => navigate('basket')}
          />
        </Suspense>
      );
    }

    if (screen === 'prices') {
      return (
        <Suspense fallback={screenFallback}>
          <PricesScreen
            ws={ws}
            items={items}
            bestTotal={bestTotal}
            isDesktop={isDesktop}
            onUpdateQty={handleUpdateQty}
            onOpenBasket={() => navigate('basket')}
            onOpenCheaper={() => navigate('cheaper')}
            onOpenSettings={() => navigate('settings')}
          />
        </Suspense>
      );
    }

    if (screen === 'settings') {
      return (
        <Suspense fallback={screenFallback}>
          <SettingsScreen
            ws={ws}
            storeTotals={storeTotals}
            itemsCount={items.length}
            bestTotal={bestTotal}
            isDesktop={isDesktop}
            activeCollecting={collectingChains}
            onToggleStore={handleToggleStore}
            onUpdateStorePerk={handleUpdateStorePerk}
            onSaveAddress={handleSaveAddress}
            onToggleOffer={handleToggleOffer}
            onAddOffer={handleAddOffer}
            onDeleteOffer={handleDeleteOffer}
            onSyncFns={handleSyncFns}
            onDisconnectFns={handleDisconnectFns}
            onPasteReceipt={handlePasteReceipt}
            onOpenBasket={() => navigate('basket')}
            onOpenCheaper={() => navigate('cheaper')}
            onOpenPrices={() => navigate('prices')}
            onLogout={handleLogout}
            initialAuthStore={pendingAuthStore}
            onClearInitialAuthStore={() => setPendingAuthStore(null)}
          />
        </Suspense>
      );
    }

    return (
      <BasketScreen
        ws={ws}
        basketName={activeBasket?.name || 'Корзина'}
        basketsCount={ws.baskets.length}
        items={items}
        habits={habits}
        board={board}
        viewMode={viewMode}
        isDesktop={isDesktop}
        activeCollecting={collectingChains}
        onChangeView={handleChangeView}
        onUpdateQty={handleUpdateQty}
        onAddFromSearch={handleAddFromSearch}
        onAddHabit={handleAddHabit}
        onAddAllHabits={handleAddAllHabits}
        onClearBasket={handleClearBasket}
        onOpenCheaper={(placeIdx) => {
          setPickedPlace(placeIdx);
          if (!isDesktop) navigate('cheaper');
        }}
        onOpenOrders={() => navigate('orders')}
        onOpenBaskets={() => navigate('baskets')}
        onOpenPrices={() => navigate('prices')}
        onOpenSettings={() => navigate('settings')}
      />
    );
  })();

  if (isDesktop) {
    const fullWidthScreen = screen === 'prices';
    return (
      <div className="spa-desk-shell">
        <header className="spa-desk-nav">
          <div className="spa-desk-nav-in">
            <button
              type="button"
              className="spa-desk-brand"
              onClick={() => navigate('basket')}
            >
              <span className="spa-brand-dot">
                <Icon name="cart" />
              </span>
              <strong>Моя корзина</strong>
              {ws.isDemo && <span className="spa-demo-tag">Демо</span>}
            </button>

            <nav className="spa-desk-links" aria-label="Навигация">
              <button
                type="button"
                className={screen === 'basket' || screen === 'cheaper' || screen === 'baskets' ? 'on' : ''}
                onClick={() => navigate('basket')}
              >
                <Icon name="cart" />
                <span>Корзина</span>
              </button>
              <button
                type="button"
                className={screen === 'prices' ? 'on' : ''}
                onClick={() => navigate('prices')}
              >
                <Icon name="list" />
                <span>Таблица цен</span>
              </button>
              <button
                type="button"
                className={screen === 'orders' ? 'on' : ''}
                onClick={() => navigate('orders')}
              >
                <Icon name="clock" />
                <span>Заказы</span>
              </button>
              <button
                type="button"
                className={screen === 'settings' ? 'on' : ''}
                onClick={() => navigate('settings')}
              >
                <Icon name="gear" />
                <span>Настройки</span>
              </button>
            </nav>

            <div className="spa-desk-user">
              <span>{ws.phoneLabel}</span>
            </div>
          </div>
        </header>

        <main className={`spa-desk-grid${fullWidthScreen ? ' full' : ''}`}>
          <div className="spa-desk-col spa-desk-left">{leftScreenContent}</div>
          {!fullWidthScreen && (
            <aside className="spa-desk-col spa-desk-right" aria-label="Где дешевле">
              <CheaperScreen
                ws={ws}
                basketId={activeBasketId}
                basketStamp={effectiveBasketStamp}
                board={board}
                embedded
                applyBonuses={applyBonuses}
                activeCollecting={collectingChains}
                onToggleApplyBonuses={handleToggleApplyBonuses}
                onPickPlace={(idx) => setPickedPlace(idx)}
                onAddTopupProduct={handleAddTopupProduct}
                onRefreshLive={handleRefreshLive}
                onConnectStore={(code) => {
                  if (KorzinaBridge.available()) {
                    KorzinaBridge.login(code);
                    notify('Открываем вход в магазин…');
                    return;
                  }
                  setPendingAuthStore(code);
                  navigate('settings');
                }}
                onNotify={notify}
              />
            </aside>
          )}
        </main>

        {toast && (
          <div className="spa-toast" role="status">
            {toast}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="ph spa-mobile-shell">
      {leftScreenContent}
      {toast && (
        <div className="spa-toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
};
