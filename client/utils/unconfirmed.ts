import type {
  Store,
  StoreCartQuote,
  StoreConnection,
  StoreQuoteReason,
} from '../../src/types';

export interface UnconfirmedStoreInfo {
  storeCode: string;
  storeName: string;
  reason: StoreQuoteReason;
  label: string;
  needsSettings: boolean;
}

const SHORT_NOTE_MAX = 72;

function trimNote(note: string | null | undefined, fallback: string): string {
  const clean = (note || '').replace(/\s+/g, ' ').trim();
  if (!clean) return fallback;
  return clean.length > SHORT_NOTE_MAX
    ? `${clean.slice(0, SHORT_NOTE_MAX - 1).trimEnd()}…`
    : clean;
}

export function getUnconfirmedStores(
  stores: Store[],
  connections: Record<string, StoreConnection> | undefined,
  storeQuotes: Record<string, StoreCartQuote> | undefined,
  activeCollecting: string[],
  itemsCount: number
): UnconfirmedStoreInfo[] {
  if (itemsCount <= 0) return [];
  const collectingSet = new Set(activeCollecting);
  const out: UnconfirmedStoreInfo[] = [];

  for (const st of stores) {
    if (collectingSet.has(st.code)) continue;
    const conn = connections?.[st.code];
    if (!conn || (!conn.connected && !conn.again)) continue;

    const q = storeQuotes?.[st.code];
    if (q?.status === 'collecting' || q?.status === 'ready') continue;

    if (conn.again || q?.reason === 'login') {
      out.push({
        storeCode: st.code,
        storeName: st.name,
        reason: 'login',
        label: 'нужен повторный вход',
        needsSettings: true,
      });
      continue;
    }

    if (!q) continue;

    if (q.reason === 'refused') {
      out.push({
        storeCode: st.code,
        storeName: st.name,
        reason: 'refused',
        label: 'временное ограничение запросов',
        needsSettings: false,
      });
    } else if (q.reason === 'empty') {
      out.push({
        storeCode: st.code,
        storeName: st.name,
        reason: 'empty',
        label: 'нет этих товаров в сети',
        needsSettings: false,
      });
    } else if (q.reason === 'cant') {
      out.push({
        storeCode: st.code,
        storeName: st.name,
        reason: 'cant',
        label: trimNote(q.note, 'сборка в эту сеть недоступна'),
        needsSettings: false,
      });
    } else if (q.reason === 'lost') {
      out.push({
        storeCode: st.code,
        storeName: st.name,
        reason: 'lost',
        label: 'сборка прервалась, повторим при обновлении',
        needsSettings: false,
      });
    } else if (q.status === 'error' || q.reason === 'failed') {
      out.push({
        storeCode: st.code,
        storeName: st.name,
        reason: 'failed',
        label: trimNote(q.note, 'не удалось подтвердить корзину'),
        needsSettings: false,
      });
    }
  }

  return out;
}
