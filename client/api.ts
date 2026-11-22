import { KorzinaBridge } from './device';
import { loadCachedAuth, loadSessionToken, saveSessionToken } from './storage';
import type {
  AddressSuggestion,
  AuthState,
  SearchResultItem,
  WorkspaceSnapshot,
} from './types';

function resolveApiBase(): string {
  if (typeof window === 'undefined') return '';
  if (typeof window.__KORZINA_API__ === 'string' && window.__KORZINA_API__) {
    return window.__KORZINA_API__.replace(/\/+$/, '');
  }
  if (window.location.protocol === 'file:') {
    const srv = KorzinaBridge.info()?.server;
    if (srv) return srv.replace(/\/+$/, '');
  }
  return '';
}

export function apiUrl(path: string): string {
  return `${resolveApiBase()}${path}`;
}

export class ApiUnavailableError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiUnavailableError';
    this.status = status;
  }
}

async function fetchOnce(path: string, init?: RequestInit): Promise<Response> {
  const token = loadSessionToken();
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init?.headers as Record<string, string> | undefined),
  };
  return fetch(apiUrl(path), {
    ...init,
    credentials: 'include',
    headers,
  });
}

let reauthInFlight: Promise<boolean> | null = null;

async function trySilentReauth(): Promise<boolean> {
  const cached = loadCachedAuth();
  if (!cached?.authenticated || !cached.phone || cached.pinRequired) return false;
  if (reauthInFlight) return reauthInFlight;

  reauthInFlight = (async () => {
    try {
      const endpoint = cached.isDemo ? '/api/auth/demo' : '/api/auth/login';
      const body = cached.isDemo ? '{}' : JSON.stringify({ phone: cached.phone, pin: '' });
      const res = await fetch(apiUrl(endpoint), {
        method: 'POST',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body,
      });
      if (!res.ok) return false;
      const raw = await res.text();
      if (!raw || raw.trimStart().startsWith('<')) return false;
      const parsed = JSON.parse(raw) as { ok?: boolean; token?: string };
      if (parsed.ok && parsed.token) {
        saveSessionToken(parsed.token);
        return true;
      }
      return Boolean(parsed.ok);
    } catch {
      return false;
    } finally {
      reauthInFlight = null;
    }
  })();

  return reauthInFlight;
}

async function requestJson<T>(
  path: string,
  init?: RequestInit,
  allowReauth = true
): Promise<T> {
  let res: Response;
  try {
    res = await fetchOnce(path, init);
    if ([502, 503, 504].includes(res.status)) {
      await new Promise((r) => window.setTimeout(r, 650));
      res = await fetchOnce(path, init);
    }
  } catch (err) {
    throw new ApiUnavailableError(
      err instanceof Error ? err.message : 'Нет соединения с сервером'
    );
  }

  // If session cookie/token expired on the server while user is logged in locally, silently re-issue Bearer token once
  if (res.status === 401 && allowReauth && !path.startsWith('/api/auth/')) {
    const recovered = await trySilentReauth();
    if (recovered) {
      return requestJson<T>(path, init, false);
    }
  }

  const contentType = (res.headers.get('content-type') || '').toLowerCase();
  const rawText = await res.text();
  let data: Record<string, unknown> | null = null;
  if (rawText && !rawText.trimStart().startsWith('<')) {
    try {
      data = JSON.parse(rawText) as Record<string, unknown>;
    } catch {
      data = null;
    }
  }

  // Non-JSON response (e.g. proxy HTML redirect /__cookie_check.html or static host fallback)
  if (!data) {
    if (res.redirected || contentType.includes('text/html') || [404, 405, 502, 503, 504].includes(res.status) || res.ok) {
      throw new ApiUnavailableError(
        `API недоступен (${res.status})`,
        res.status
      );
    }
    const err = new Error(
      `Сервер ответил с ошибкой ${res.status}. Попробуйте повторить через пару секунд.`
    ) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }

  if (!res.ok) {
    const serverMsg = typeof data.error === 'string' ? data.error : null;
    const err = new Error(
      serverMsg || `Сервер ответил с ошибкой ${res.status}.`
    ) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }

  return data as T;
}

export async function fetchAuthMe(): Promise<AuthState> {
  const data = await requestJson<AuthState & { ok: boolean }>('/api/auth/me');
  return {
    authenticated: Boolean(data.authenticated),
    phone: data.phone ?? null,
    phoneLabel: data.phoneLabel || '',
    isDemo: Boolean(data.isDemo),
    pinRequired: Boolean(data.pinRequired),
  };
}

export async function loginByPhone(
  phone: string,
  pin?: string
): Promise<{ auth: AuthState; snapshot: WorkspaceSnapshot }> {
  const data = await requestJson<{ token?: string; auth: AuthState; snapshot: WorkspaceSnapshot }>(
    '/api/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ phone, pin: pin || '' }),
    }
  );
  if (data.token) saveSessionToken(data.token);
  return { auth: data.auth, snapshot: data.snapshot };
}

export async function loginDemo(): Promise<{ auth: AuthState; snapshot: WorkspaceSnapshot }> {
  const data = await requestJson<{ token?: string; auth: AuthState; snapshot: WorkspaceSnapshot }>(
    '/api/auth/demo',
    {
      method: 'POST',
      body: JSON.stringify({}),
    }
  );
  if (data.token) saveSessionToken(data.token);
  return { auth: data.auth, snapshot: data.snapshot };
}

export async function logoutSession(): Promise<void> {
  try {
    await requestJson('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) });
  } finally {
    saveSessionToken(null);
  }
}

export async function fetchSyncSnapshot(): Promise<WorkspaceSnapshot> {
  const data = await requestJson<{ ok: boolean; snapshot: WorkspaceSnapshot }>('/api/sync');
  return data.snapshot;
}

export async function pushSyncSnapshot(
  snapshot: WorkspaceSnapshot
): Promise<{ ok: boolean; syncedAt: string; basketStamp?: string; serverStarted?: string[] }> {
  return requestJson('/api/sync', {
    method: 'POST',
    body: JSON.stringify({
      products: snapshot.products,
      baskets: snapshot.baskets,
      nextBasketId: snapshot.nextBasketId,
      basketItems: snapshot.basketItems,
      lastBasketId: snapshot.lastBasketId,
      repeated: snapshot.repeated,
      address: snapshot.address,
      point: snapshot.point,
      connections: snapshot.connections,
      storePerks: snapshot.storePerks || {},
      applyBonuses: snapshot.applyBonuses !== false,
      cards: snapshot.cards,
      offers: snapshot.offers,
      fnsConnected: snapshot.fnsConnected,
      fnsPhone: snapshot.fnsPhone,
    }),
  });
}

export async function fetchLiveRuns(): Promise<{
  storeQuotes: NonNullable<WorkspaceSnapshot['storeQuotes']>;
  connections: WorkspaceSnapshot['connections'];
  prices: WorkspaceSnapshot['prices'];
  storePerks: NonNullable<WorkspaceSnapshot['storePerks']>;
  applyBonuses: boolean;
  syncedAt: string;
}> {
  return requestJson('/api/runs');
}

export async function searchServerCatalog(q: string): Promise<SearchResultItem[]> {
  const data = await requestJson<{ ok: boolean; results: SearchResultItem[] }>(
    `/api/search?q=${encodeURIComponent(q)}`
  );
  return data.results || [];
}

export async function submitManualReceipt(
  storeCode: string,
  text: string
): Promise<{ imported: number; total: number; snapshot: WorkspaceSnapshot }> {
  return requestJson('/api/receipts/paste', {
    method: 'POST',
    body: JSON.stringify({ store_code: storeCode, text }),
  });
}

export async function syncFnsServer(
  phone?: string,
  keys?: string
): Promise<{ snapshot: WorkspaceSnapshot }> {
  return requestJson('/api/fns/sync', {
    method: 'POST',
    body: JSON.stringify({ phone: phone || '', keys: keys || '' }),
  });
}

export async function disconnectFnsServer(): Promise<{ snapshot: WorkspaceSnapshot }> {
  return requestJson('/api/fns/disconnect', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

async function suggestAddressDirect(q: string): Promise<AddressSuggestion[]> {
  try {
    const r = await fetch(
      `https://photon.komoot.io/api/?limit=6&q=${encodeURIComponent(q)}`,
      { headers: { Accept: 'application/json' } }
    );
    if (!r.ok) return [];
    const data = (await r.json()) as {
      features?: Array<{
        geometry?: { coordinates?: [number, number] };
        properties?: Record<string, string>;
      }>;
    };
    const seen = new Set<string>();
    const out: AddressSuggestion[] = [];
    for (const f of data.features || []) {
      const p = f.properties || {};
      if (p.countrycode && p.countrycode.toUpperCase() !== 'RU') continue;
      const city = p.city || p.town || p.village || p.state || '';
      const street = p.street || (p.name && p.name !== city ? p.name : '');
      const house = p.housenumber || '';
      const label = [city, street, house].filter(Boolean).join(', ') || p.name || q;
      const key = label.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      const coords = f.geometry?.coordinates || [0, 0];
      const full = [p.state, p.city, p.district, street, house].filter(Boolean).join(', ') || label;
      out.push({ label, full, lat: Number(coords[1]), lon: Number(coords[0]) });
    }
    return out.slice(0, 6);
  } catch {
    return [];
  }
}

export async function suggestAddress(q: string): Promise<AddressSuggestion[]> {
  try {
    const data = await requestJson<{ ok: boolean; items: AddressSuggestion[] }>(
      `/api/address/suggest?q=${encodeURIComponent(q)}`,
      undefined,
      false
    );
    if (data.items && data.items.length > 0) return data.items;
  } catch {
    // fallback to browser-side Photon below
  }
  return suggestAddressDirect(q);
}

export async function reverseGeocode(lat: number, lon: number): Promise<string> {
  try {
    const data = await requestJson<{ ok: boolean; address: string }>(
      `/api/address/reverse?lat=${encodeURIComponent(String(lat))}&lon=${encodeURIComponent(String(lon))}`,
      undefined,
      false
    );
    if (data.address) return data.address;
  } catch {
    // fallback to direct Nominatim
    try {
      const r = await fetch(
        `https://nominatim.openstreetmap.org/reverse?format=json&addressdetails=1&accept-language=ru&lat=${lat}&lon=${lon}`
      );
      if (r.ok) {
        const j = (await r.json()) as { display_name?: string; address?: Record<string, string> };
        const a = j.address || {};
        const short = [a.city || a.town || a.village || a.state, a.road || a.pedestrian, a.house_number]
          .filter(Boolean)
          .join(', ');
        if (short || j.display_name) return short || j.display_name || `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
      }
    } catch {
      // ignore
    }
  }
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}
