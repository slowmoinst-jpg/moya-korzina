import type { AuthState, WorkspaceSnapshot } from './types';

const SNAPSHOT_PREFIX = 'korzina.spa.ws.v1.';
const AUTH_KEY = 'korzina.spa.auth.v1';
const TOKEN_KEY = 'korzina.spa.token.v1';
const VIEW_KEY = 'korzina.basket.view';
const DIRTY_PREFIX = 'korzina.spa.dirty.v1.';

export function loadSessionToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY) || null;
  } catch {
    return null;
  }
}

export function saveSessionToken(token: string | null): void {
  try {
    if (!token) localStorage.removeItem(TOKEN_KEY);
    else localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // ignore
  }
}

export function loadCachedAuth(): AuthState | null {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthState;
    if (parsed && typeof parsed.authenticated === 'boolean') return parsed;
  } catch {
    return null;
  }
  return null;
}

export function saveCachedAuth(auth: AuthState | null): void {
  try {
    if (!auth || !auth.authenticated) {
      localStorage.removeItem(AUTH_KEY);
    } else {
      localStorage.setItem(AUTH_KEY, JSON.stringify(auth));
    }
  } catch {
    // ignore storage quota errors
  }
}

export function loadCachedSnapshot(phone: string): WorkspaceSnapshot | null {
  try {
    const raw = localStorage.getItem(SNAPSHOT_PREFIX + phone);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as WorkspaceSnapshot;
    if (parsed && Array.isArray(parsed.stores) && Array.isArray(parsed.baskets)) {
      return parsed;
    }
  } catch {
    return null;
  }
  return null;
}

export function saveCachedSnapshot(snapshot: WorkspaceSnapshot): void {
  try {
    localStorage.setItem(SNAPSHOT_PREFIX + snapshot.phone, JSON.stringify(snapshot));
  } catch {
    // ignore storage quota errors
  }
}

export function clearCachedSnapshot(phone: string): void {
  try {
    localStorage.removeItem(SNAPSHOT_PREFIX + phone);
    localStorage.removeItem(DIRTY_PREFIX + phone);
  } catch {
    // ignore
  }
}

export function isSyncDirty(phone: string): boolean {
  try {
    return localStorage.getItem(DIRTY_PREFIX + phone) === '1';
  } catch {
    return false;
  }
}

export function setSyncDirty(phone: string, dirty: boolean): void {
  try {
    if (dirty) localStorage.setItem(DIRTY_PREFIX + phone, '1');
    else localStorage.removeItem(DIRTY_PREFIX + phone);
  } catch {
    // ignore
  }
}

export function loadBasketViewMode(): 'grid' | 'list' {
  try {
    return localStorage.getItem(VIEW_KEY) === 'grid' ? 'grid' : 'list';
  } catch {
    return 'list';
  }
}

export function saveBasketViewMode(mode: 'grid' | 'list'): void {
  try {
    localStorage.setItem(VIEW_KEY, mode);
  } catch {
    // ignore
  }
}
