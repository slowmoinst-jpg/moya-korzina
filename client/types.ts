import type {
  Basket,
  BasketItem,
  Card,
  Offer,
  Product,
  PurchaseRow,
  ReceiptRecord,
  Store,
  StoreCartQuote,
  StoreConnection,
  StorePerk,
  StoreProductPrice,
  UnitType,
  Variant,
} from '../src/types';

export interface ApkUpdate {
  version: string;
  canDownload: boolean;
  url: string;
}

export interface AuthState {
  authenticated: boolean;
  phone: string | null;
  phoneLabel: string;
  isDemo: boolean;
  pinRequired: boolean;
}

export interface WorkspaceSnapshot {
  phone: string;
  phoneLabel: string;
  isDemo: boolean;
  address: string | null;
  point: [number, number] | null;
  stores: Store[];
  products: Product[];
  nextProductId: number;
  prices: Record<string, Record<string, StoreProductPrice>>;
  cards: Card[];
  offers: Offer[];
  baskets: Basket[];
  nextBasketId: number;
  basketItems: Record<string, BasketItem[]>;
  history: PurchaseRow[];
  receipts: ReceiptRecord[];
  otherReceipts?: { count: number; recent: Pick<ReceiptRecord, 'key' | 'date' | 'store' | 'total'>[] };
  connections: Record<string, StoreConnection>;
  storeQuotes?: Record<string, StoreCartQuote>;
  storePerks?: Record<string, StorePerk>;
  applyBonuses?: boolean;
  lastBasketId: number | null;
  repeated: { key: string; basket: number } | null;
  fnsConnected: boolean;
  fnsPhone: string | null;
  fnsSyncedAt: string | null;
  syncedAt: string;
}

export interface BoardPlace {
  n: number;
  title: string;
  total: number;
  codes: string[];
  counts: Array<[string, number]>;
  lack: number;
  of: number;
  full: boolean;
  best: boolean;
  on: boolean;
  rank: number;
  diff: number;
}

export interface ClientBoard {
  places: BoardPlace[];
  morePlaces: BoardPlace[];
  allVariants: Variant[];
  chosenVariant: Variant | null;
  bestIndex: number;
  onIndex: number;
  baseline: number;
  positions: number;
  pricesByPid: Record<number, number>;
  when: string;
}

export interface HabitItem {
  pid: number;
  name: string;
  unit: UnitType;
  why: string;
  qty: number;
  photo: string | null;
  times: number;
  cycle: number;
  since: number;
}

export interface OrderLineView {
  product_id: number | null;
  repeatable: boolean;
  name: string;
  qty: number;
  total: number;
  photo: string | null;
}

export interface OrderEntry {
  key: string;
  date: string;
  day: string;
  store_code: string;
  store_name: string;
  source: string;
  total: number;
  lines: OrderLineView[];
}

export interface SearchResultItem {
  available?: boolean;
  product: Product;
  prices: Record<string, StoreProductPrice>;
  chainsCount: number;
  small: string;
}

export interface AddressSuggestion {
  label: string;
  full: string;
  lat: number;
  lon: number;
}

export type SpaScreen =
  | 'basket'
  | 'cheaper'
  | 'orders'
  | 'baskets'
  | 'prices'
  | 'settings';

export interface KorzinaDeviceBridge {
  ready?: () => boolean;
  info?: () => { version?: string; server?: string; has?: string[] };
  login?: (code: string) => void;
  openStore?: (code: string, url?: string) => void;
  collect?: (payload: unknown) => void;
  ordered?: (code: string, basketId: number | null, lines: unknown[]) => void;
}

declare global {
  interface Window {
    KorzinaDevice?: KorzinaDeviceBridge;
    __KORZINA_API__?: string;
    kzRunsChanged?: () => void;
    L?: {
      map: (el: HTMLElement, opts?: Record<string, unknown>) => LeafletMap;
      tileLayer: (url: string, opts?: Record<string, unknown>) => { addTo: (m: LeafletMap) => void };
      marker: (latlng: [number, number], opts?: Record<string, unknown>) => LeafletMarker;
      divIcon: (opts: Record<string, unknown>) => unknown;
    };
  }
}

export interface LeafletMap {
  setView: (latlng: [number, number], zoom?: number) => void;
  on: (event: string, cb: (e: { latlng: { lat: number; lng: number } }) => void) => void;
  invalidateSize: () => void;
  remove: () => void;
}

export interface LeafletMarker {
  addTo: (m: LeafletMap) => LeafletMarker;
  setLatLng: (latlng: [number, number]) => void;
  getLatLng: () => { lat: number; lng: number };
  on: (event: string, cb: () => void) => void;
}
