interface RawKorzinaBridge {
  info?: () => string;
  updateApp?: () => boolean;
  openStore?: (code: string) => boolean;
  login?: (code: string) => boolean;
  openApp?: (path: string) => void;
  receiptStatus?: (phone: string) => string;
  leaveReceiptProfile?: () => void;
  openFns?: () => boolean;
  collect?: (json: string) => string;
  busy?: () => string;
  ordered?: (json: string) => boolean;
}

declare global {
  interface Window {
    Korzina?: RawKorzinaBridge;
  }
}

function rawBridge(): RawKorzinaBridge | null {
  if (typeof window === 'undefined') return null;
  return typeof window.Korzina !== 'undefined' ? window.Korzina : null;
}

export const KorzinaBridge = {
  available(): boolean {
    return rawBridge() !== null;
  },

  supportsInAppUpdate(): boolean {
    return typeof rawBridge()?.updateApp === 'function';
  },

  updateApp(): boolean {
    try {
      return rawBridge()?.updateApp?.() === true;
    } catch {
      return false;
    }
  },

  info(): { version?: string; server?: string } | null {
    const k = rawBridge();
    if (!k || typeof k.info !== 'function') return null;
    try {
      return JSON.parse(k.info()) as { version?: string; server?: string };
    } catch {
      return null;
    }
  },

  openStore(code: string): boolean {
    const k = rawBridge();
    if (!k || typeof k.openStore !== 'function') return false;
    try {
      return Boolean(k.openStore(String(code || '')));
    } catch {
      return false;
    }
  },

  login(code: string): boolean {
    const k = rawBridge();
    if (k && typeof k.login === 'function') {
      try {
        return Boolean(k.login(String(code || '')));
      } catch {
        return false;
      }
    }
    return this.openStore(code);
  },

  supportsReceiptLogin(): boolean {
    return typeof rawBridge()?.receiptStatus === 'function';
  },

  receiptStatus(phone: string): { connected?: boolean; updated?: string; phase?: string; checked?: number; storedCount?: number; position?: number; total?: number; advanced?: number; millisPerReceipt?: number } {
    try { return JSON.parse(rawBridge()?.receiptStatus?.(phone) || '{}'); }
    catch { return {}; }
  },

  leaveReceiptProfile(): void {
    rawBridge()?.leaveReceiptProfile?.();
  },

  openFns(): boolean {
    const k = rawBridge();
    if (!k || typeof k.receiptStatus !== 'function' || typeof k.openFns !== 'function') return false;
    try {
      k.openFns();
      return true;
    } catch {
      return false;
    }
  },

  collect(payload: unknown): unknown {
    const k = rawBridge();
    if (!k || typeof k.collect !== 'function') return null;
    try {
      return JSON.parse(k.collect(JSON.stringify(payload || {})));
    } catch {
      return null;
    }
  },

  busy(): { going: string[]; soon: string[] } {
    const k = rawBridge();
    if (!k || typeof k.busy !== 'function') return { going: [], soon: [] };
    try {
      const parsed = JSON.parse(k.busy()) as { going?: string[]; soon?: string[] };
      return {
        going: Array.isArray(parsed.going) ? parsed.going : [],
        soon: Array.isArray(parsed.soon) ? parsed.soon : [],
      };
    } catch {
      return { going: [], soon: [] };
    }
  },

  ordered(payload: unknown): boolean {
    const k = rawBridge();
    if (!k || typeof k.ordered !== 'function') return false;
    try {
      const s = typeof payload === 'string' ? payload : JSON.stringify(payload || {});
      return Boolean(k.ordered(s));
    } catch {
      return false;
    }
  },
};
