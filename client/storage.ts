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

let pendingSnapshot: WorkspaceSnapshot | null = null;
let snapshotTimer: number | null = null;
let flushListenersBound = false;

export function flushPendingSnapshot(): void {
  if (snapshotTimer !== null) {
    window.clearTimeout(snapshotTimer);
    snapshotTimer = null;
  }
  if (!pendingSnapshot) return;
  const toWrite = pendingSnapshot;
  pendingSnapshot = null;
  try {
    localStorage.setItem(SNAPSHOT_PREFIX + toWrite.phone, JSON.stringify(toWrite));
  } catch {
    // ignore storage quota errors
  }
}

function bindFlushListeners(): void {
  if (flushListenersBound || typeof window === 'undefined') return;
  flushListenersBound = true;
  window.addEventListener('pagehide', flushPendingSnapshot);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushPendingSnapshot();
  });
}

export function saveCachedSnapshot(snapshot: WorkspaceSnapshot): void {
  pendingSnapshot = null;
  if (snapshotTimer !== null) {
    window.clearTimeout(snapshotTimer);
    snapshotTimer = null;
  }
  try {
    localStorage.setItem(SNAPSHOT_PREFIX + snapshot.phone, JSON.stringify(snapshot));
  } catch {
    // ignore storage quota errors
  }
}

export function scheduleSaveCachedSnapshot(snapshot: WorkspaceSnapshot, delayMs = 260): void {
  if (typeof window === 'undefined') return;
  bindFlushListeners();
  pendingSnapshot = snapshot;
  if (snapshotTimer !== null) window.clearTimeout(snapshotTimer);
  snapshotTimer = window.setTimeout(() => {
    snapshotTimer = null;
    flushPendingSnapshot();
  }, delayMs);
}

export function clearCachedSnapshot(phone: string): void {
  if (pendingSnapshot?.phone === phone) pendingSnapshot = null;
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
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

export function saveBasketViewMode(mode: 'grid' | 'list'): void {
  try {
    localStorage.setItem(VIEW_KEY, mode);
  } catch {
    // ignore
  }
}
