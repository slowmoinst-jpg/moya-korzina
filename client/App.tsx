import { ApkUpdateBanner } from './components/ApkUpdateBanner';
import { MobileTabs } from './components/MobileTabs';
import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { humanDay, PHONE_CHAINS } from '../src/shared/format';
import type { StorePerk } from '../src/types';
import {
  ApiUnavailableError,
  fetchAuthMe,
  fetchLiveRuns,
  fetchSyncSnapshot,
  loginByPhone,
  loginDemo,
  logoutSession,
  pushSyncSnapshot,
  snapshotSyncBody,
} from './api';
import { KorzinaBridge } from './device';
import {
  buildLocalWorkspace,
  DEMO_PHONE,
  formatPhoneDisplayLocal,
  resolveLoginLocal,
} from './localSeed';
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
  ApkUpdate,
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
  if (raw.includes('/settings') || raw === '/phone/receipts') return 'settings';
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

const COLLECTION_BUDGET_MS = 5000;

interface CollectionWindow {
  deadline: number;
  starting: string[];
  pending: string[];
  failed: string[];
  stamp: string;
  expired: boolean;
  syncing: boolean;
}

export const App: React.FC = () => {
  const [apkUpdate, setApkUpdate] = useState<ApkUpdate | null>(null);
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
  const [collection, setCollection] = useState<CollectionWindow | null>(null);
  const [isDesktop, setIsDesktop] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 960 : false
  );

  const syncTimerRef = useRef<number | null>(null);
  const serverBasketStampRef = useRef<string | null>(ws?.basketStamp ?? null);
  const pricesVersionRef = useRef<number | null>(null);
  const activeBasketIdRef = useRef<number | null>(null);
  const collectTimerRef = useRef<number | null>(null);
  const lastCollectedKeyRef = useRef<string>('');
  const collectionRef = useRef<CollectionWindow | null>(null);
  const editedAtRef = useRef(0);
  const syncRef = useRef<{
    phone: string;
    body: string;
    request: ReturnType<typeof pushSyncSnapshot>;
  } | null>(null);

  const syncWorkspace = useCallback((snapshot: WorkspaceSnapshot) => {
    const body = snapshotSyncBody(snapshot);
    const current = syncRef.current;
    if (current?.phone === snapshot.phone && current.body === body) return current.request;
    // Serialize writes so a slower response cannot roll back a later edit.
    const request = Promise.resolve(current?.request)
      .catch(() => undefined)
      .then(() => pushSyncSnapshot(snapshot, body));
    const entry = { phone: snapshot.phone, body, request };
    syncRef.current = entry;
    void request.catch(() => {
      if (syncRef.current === entry) syncRef.current = null;
    });
    return request;
  }, []);

  const updateCollection = useCallback((windowState: CollectionWindow | null) => {
    collectionRef.current = windowState;
    setCollection(windowState ? { ...windowState } : null);
  }, []);
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
        const { apkUpdate: update, ...me } = await fetchAuthMe();
        if (!active) return;
        setApkUpdate(update ?? null);
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

    const checkUpdateOnReturn = () => {
      if (document.visibilityState !== 'visible') return;
      void fetchAuthMe().then(({ apkUpdate: update }) => {
        if (active) setApkUpdate(update ?? null);
      }).catch(() => {});
    };
    document.addEventListener('visibilitychange', checkUpdateOnReturn);
    return () => {
      active = false;
      document.removeEventListener('visibilitychange', checkUpdateOnReturn);
    };
  }, []);

  // Auto-flush dirty offline edits when network returns
  useEffect(() => {
    const onOnline = () => {
      if (ws && !ws.isDemo && isSyncDirty(ws.phone)) {
        void syncWorkspace(ws)
          .then(() => {
            setSyncDirty(ws.phone, false);
            notify('Изменения синхронизированы с сервером');
          })
          .catch(() => {});
      }
    };
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [ws, notify, syncWorkspace]);

  const commitWorkspace = useCallback(
    (updater: (prev: WorkspaceSnapshot) => WorkspaceSnapshot) => {
      editedAtRef.current = performance.now();
      setWs((prev) => {
        if (!prev) return prev;
        const next = updater(prev);
        scheduleSaveCachedSnapshot(next);

        if (!next.isDemo) {
          setSyncDirty(next.phone, true);
          if (syncTimerRef.current) window.clearTimeout(syncTimerRef.current);
          syncTimerRef.current = window.setTimeout(() => {
            void syncWorkspace(next)
              .then(() => setSyncDirty(next.phone, false))
              .catch(() => {
                // Keep dirty flag for next online sync
              });
          }, 450);
        }
        return next;
      });
    },
    [syncWorkspace]
  );

  // Derived data calculated locally in <3ms
  const activeBasket = useMemo(() => (ws ? getActiveBasket(ws) : null), [ws]);
  const activeBasketId = activeBasket?.id ?? null;
  if (activeBasketIdRef.current !== activeBasketId) pricesVersionRef.current = null;
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
    const checking = collectionRef.current;
    try {
      const requestedBasket = activeBasketIdRef.current;
      const requestedPhone = ws?.phone;
      const live = await fetchLiveRuns({
        basketId: requestedBasket,
        pricesVersion: pricesVersionRef.current,
      });
      if (requestedBasket !== activeBasketIdRef.current || requestedPhone !== loadCachedAuth()?.phone) return;
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
      if (checking && checking === collectionRef.current && checking.stamp) {
        checking.pending = checking.starting.filter((code) => {
          const quote = live.storeQuotes?.[code];
          return quote?.stamp !== checking.stamp || quote.status === 'collecting';
        });
        checking.failed = checking.starting.filter((code) => {
          const quote = live.storeQuotes?.[code];
          return quote?.stamp === checking.stamp && quote.status === 'error';
        });
        updateCollection(checking);
      }
    } catch {
      // The five-second deadline still applies when the API is unreachable.
    }
  }, [updateCollection, ws?.phone]);

  useEffect(() => {
    if (!collection || collection.expired || (!collection.syncing && !collection.pending.length)) return;
    const checking = collectionRef.current;
    if (!checking) return;
    const alarm = window.setTimeout(() => {
      if (checking !== collectionRef.current) return;
      checking.expired = true;
      updateCollection(checking);
    }, Math.max(0, checking.deadline - performance.now()));
    const poll = collection.syncing ? undefined : window.setInterval(() => void refreshLiveRuns(), 700);
    return () => {
      window.clearTimeout(alarm);
      if (poll !== undefined) window.clearInterval(poll);
    };
  }, [collection, refreshLiveRuns, updateCollection]);

  const returnStore = new URLSearchParams(window.location.search).get('store');
  const loginVisible = !!(returnStore && ws?.connections[returnStore]?.connected);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!auth?.authenticated || screen !== 'settings' || params.get('saved') !== 'connected') return;
    if (!returnStore || loginVisible) return;
    // Android confirms the login asynchronously, sometimes after opening Settings.
    const timers = [0, 700, 2000, 5000].map(delay => window.setTimeout(refreshLiveRuns, delay));
    return () => timers.forEach(window.clearTimeout);
  }, [auth?.authenticated, screen, returnStore, loginVisible, refreshLiveRuns]);

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

  const pendingChains = collection?.expired ? 0 : collection?.pending.length || 0;

  // Adaptive live polling while any chain (server LINK or Android WebView) is actively collecting
  useEffect(() => {
    if (!auth?.authenticated || pendingChains === 0) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void refreshLiveRuns();
      }
    }, 2500);
    return () => window.clearInterval(id);
  }, [auth?.authenticated, pendingChains, refreshLiveRuns]);

  // Trigger Android background cart assembly automatically after any basket or address change across all 11 chains
  const triggerBackgroundCollect = useCallback(
    async (force = false) => {
      if (!ws || ws.isDemo || activeBasketId === null || !items.length || !basketStamp) return;
      if (!KorzinaBridge.available()) return;

      const collectKey = `${basketStamp}::${ws.basketStamp || ""}::${connectedSig}`;
      if (!force && lastCollectedKeyRef.current === collectKey) return;
      lastCollectedKeyRef.current = collectKey;

      const started = force ? 0 : editedAtRef.current;
      editedAtRef.current = 0;
      const checking: CollectionWindow = {
        deadline: (started || performance.now()) + COLLECTION_BUDGET_MS,
        starting: [],
        pending: [],
        failed: [],
        stamp: '',
        expired: false,
        syncing: true,
      };
      updateCollection(checking);

      try {
        const knownStamp = !isSyncDirty(ws.phone) && ws.basketStamp && syncRef.current?.body === snapshotSyncBody(ws) ? ws.basketStamp : null;
        const synced = knownStamp ? { basketStamp: knownStamp } : await syncWorkspace(ws);
        if (checking !== collectionRef.current) return;
        setSyncDirty(ws.phone, false);
        const remaining = Math.floor(checking.deadline - performance.now());
        if (remaining <= 0) {
          checking.expired = true;
          updateCollection(checking);
          return;
        }
        const chains = PHONE_CHAINS.map((code) => ({
          code,
          login: ws.connections[code]?.connected ? 'in' : 'connect',
          cookies: code === 'magnit' ? ['mg_at'] : [],
          sent: false,
        }));

        checking.stamp = synced.basketStamp;
        lastCollectedKeyRef.current = `${basketStamp}::${checking.stamp}::${connectedSig}`;
        const result = KorzinaBridge.collect({
          basket: activeBasketId,
          stamp: checking.stamp,
          address: ws.address || '',
          point: ws.point,
          budget_ms: remaining,
          chains,
        }) as { starting?: string[] } | null;
        const busy = KorzinaBridge.busy();
        checking.starting = result?.starting || [...new Set([...busy.going, ...busy.soon])];
        checking.pending = checking.starting;
        checking.syncing = false;
        updateCollection(checking);
        if (checking.pending.length) void refreshLiveRuns();
      } catch {
        if (checking !== collectionRef.current) return;
        checking.expired = true;
        updateCollection(checking);
      }
    },
    [ws, activeBasketId, items.length, basketStamp, connectedSig, refreshLiveRuns, syncWorkspace, updateCollection]
  );

  useEffect(() => {
    if (!KorzinaBridge.available()) return;
    if (!ws || activeBasketId === null || !items.length || !basketStamp) {
      lastCollectedKeyRef.current = '';
      updateCollection(null);
      return;
    }
    if (collectTimerRef.current) window.clearTimeout(collectTimerRef.current);
    collectTimerRef.current = window.setTimeout(() => {
      void triggerBackgroundCollect(false);
    }, 150);
    return () => {
      if (collectTimerRef.current) window.clearTimeout(collectTimerRef.current);
    };
  }, [ws, activeBasketId, items.length, basketStamp, connectedSig, triggerBackgroundCollect, updateCollection]);

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
    KorzinaBridge.leaveReceiptProfile();
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
    (res: SearchResultItem, store?: string, replaceId?: number) => {
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
        const oldList = next.basketItems[key] || [];
        const replaced = replaceId === undefined ? undefined : oldList.find(row => row.product_id === replaceId);
        if (replaceId !== undefined && !replaced) return prev;
        const qty = replaced?.qty ?? 1;
        const list = oldList.filter(row => row.product_id !== replaceId);
        const idx = list.findIndex((i) => i.product_id === res.product.id);
        if (idx !== -1) {
          list[idx] = { ...list[idx], qty: Math.round((list[idx].qty + qty) * 1000) / 1000,
            preferred_store: replaced && store ? store : list[idx].preferred_store };
        } else {
          list.push({ basket_id: basketId, product_id: res.product.id, preferred_store: store, qty });
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
      const lines = order.lines.filter((line) => line.repeatable && line.product_id !== null);
      if (!lines.length) return;
      commitWorkspace((prev) => {
        const id = prev.nextBasketId;
        const basket = {
          id,
          name: `${order.store_name} · ${order.day}`,
          created_at: new Date().toISOString().slice(0, 10),
          source: 'history' as const,
        };
        const newItems = lines.map((ln) => ({
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
            disconnectSeen: connect ? null : prev.connections[code]?.connectedAt,
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

  const refreshReceipts = useCallback(async () => {
    const fresh = await fetchSyncSnapshot();
    setWs(prev => {
      if (!prev || prev.phone !== fresh.phone) return prev;
      const ids = new Set(prev.products.map(p => p.id));
      const next = { ...prev, history: fresh.history, receipts: fresh.receipts, otherReceipts: fresh.otherReceipts,
        products: [...prev.products, ...fresh.products.filter(p => !ids.has(p.id))],
        fnsConnected: fresh.fnsConnected, fnsPhone: fresh.fnsPhone, fnsSyncedAt: fresh.fnsSyncedAt };
      saveCachedSnapshot(next);
      return next;
    });
  }, []);

  useEffect(() => {
    if (screen !== 'orders' || !ws?.phone || ws.isDemo) return;
    let updated: string | undefined, checked = 0, pending = false;
    const refresh = async () => {
      if (document.hidden || pending) return;
      const device = KorzinaBridge.receiptStatus(ws.phone);
      const revision = device.updated;
      if (Date.now() - checked < 60000 && !(device.phase === 'done' && revision !== updated)) return;
      pending = true;
      try {
        await refreshReceipts();
        updated = revision;
        checked = Date.now();
      } catch { checked = Date.now(); }
      finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 4000);
    document.addEventListener('visibilitychange', refresh);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', refresh); };
  }, [screen, ws?.phone, ws?.isDemo, refreshReceipts]);

  const handleChangeView = useCallback((mode: 'grid' | 'list') => {
    setViewMode(mode);
    saveBasketViewMode(mode);
  }, []);

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
        apkUpdate={apkUpdate}
        onLogin={handleLogin}
        onDemo={handleDemo}
      />
      </Suspense>
    );
  }

  const bestTotal = board.places.length > 0 ? board.places[0].total : null;

  const applyBonuses = ws.applyBonuses !== false;
  const collectingChains = collection?.expired ? [] : collection?.pending || [];
  const effectiveBasketStamp =
    (!ws.isDemo && (ws.basketStamp || serverBasketStampRef.current)) || basketStamp;

  const leftScreenContent = (() => {
    if (screen === 'cheaper') {
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
          otherReceipts={ws.otherReceipts}
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
          isDesktop={isDesktop}
          onUpdateQty={handleUpdateQty}
          onOpenBasket={() => navigate('basket')}
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
          activeCollecting={collectingChains}
          onToggleStore={handleToggleStore}
          onUpdateStorePerk={handleUpdateStorePerk}
          onSaveAddress={handleSaveAddress}
          onToggleOffer={handleToggleOffer}
          onAddOffer={handleAddOffer}
          onDeleteOffer={handleDeleteOffer}
          onRefreshReceipts={refreshReceipts}
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
          navigate('cheaper');
        }}
        onOpenOrders={() => navigate('orders')}
        onOpenBaskets={() => navigate('baskets')}
        onOpenPrices={() => navigate('prices')}
        onOpenSettings={() => navigate('settings')}
      />
    );
  })();

  if (isDesktop) {
    const fullWidthScreen = ['basket', 'cheaper', 'prices'].includes(screen);
    return (
      <div className="spa-desk-shell">
        {apkUpdate && <ApkUpdateBanner update={apkUpdate} />}
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
      {apkUpdate && <ApkUpdateBanner update={apkUpdate} />}
      {leftScreenContent}
      {screen !== 'basket' && (
        <MobileTabs
          active={screen === 'cheaper' || screen === 'settings' ? screen : 'basket'}
          itemsCount={items.length}
          bestTotal={bestTotal}
          onSelect={navigate}
        />
      )}
      {toast && (
        <div className="spa-toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
};
