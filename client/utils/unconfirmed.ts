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

export function reportFromCollection(
  quote: StoreCartQuote | undefined,
  stamp: string,
  previous: StoreCartQuote | undefined
): boolean {
  if (!stamp || !quote || quote.stamp !== stamp || quote.status === 'collecting') return false;
  // A confirmed cart can be reused. An old failure is not evidence of a new attempt.
  return quote.status === 'ready' || !previous || previous.stamp !== stamp ||
    previous.updated_at !== quote.updated_at || previous.status !== quote.status ||
    previous.reason !== quote.reason || previous.note !== quote.note;
}

export function reportTime(value?: string | null): string {
  if (!value) return '';
  // Older snapshots contain UTC server timestamps without an explicit offset.
  const time = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : value + 'Z');
  if (Number.isNaN(time.getTime())) return '';
  return time.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export function getUnconfirmedStores(
  stores: Store[],
  connections: Record<string, StoreConnection> | undefined,
  storeQuotes: Record<string, StoreCartQuote> | undefined,
  reportStores: string[],
  itemsCount: number
): UnconfirmedStoreInfo[] {
  if (itemsCount <= 0) return [];
  const reported = new Set(reportStores);
  const out: UnconfirmedStoreInfo[] = [];

  for (const st of stores) {
    if (!reported.has(st.code)) continue;
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
        label: q.note?.trim() || 'сборка в эту сеть недоступна',
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
        label: q.note?.trim() || 'не удалось подтвердить корзину',
        needsSettings: false,
      });
    }
  }

  return out;
}
