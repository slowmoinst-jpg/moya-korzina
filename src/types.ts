export type UnitType = 'pcs' | 'kg';

export interface Product {
  id: number;
  name: string;
  brand: string | null;
  weight_g: number | null;
  unit: UnitType;
  category: string | null;
  active: boolean;
  photo?: string | null;
}

export interface Store {
  id: number;
  code: string;
  name: string;
  delivery_fee: number;
  free_delivery_from: number;
  min_order: number;
  connector_type: string;
}

export interface Card {
  id: number;
  bank: string;
  name: string;
}

export interface Offer {
  id: number;
  card_id: number;
  store_id: number;
  percent: number;
  cap_rub: number;
  min_check_rub: number;
  valid_from: string | null;
  valid_to: string | null;
  requires_activation: boolean;
  activated: boolean;
  cap_used: number;
}

export interface StoreProductPrice {
  store_code: string;
  sku: string;
  raw_name: string;
  price: number;
  unit: UnitType;
  weight_g: number | null;
  price_per_kg: number | null;
  in_stock: boolean;
  fetched_at: string;
  source: 'live' | 'fallback';
}

export interface Basket {
  id: number;
  name: string;
  created_at: string;
  source: 'manual' | 'average' | 'history';
}

export interface BasketItem {
  basket_id: number;
  product_id: number;
  qty: number;
  fallback_product_id?: number | null;
}

export interface BasketLine {
  product_id: number;
  name: string;
  title: string;
  unit: UnitType;
  qty: number;
  prices: Record<string, number>;
  in_stock: Record<string, boolean>;
  notes: Record<string, string | null>;
  adjust: Record<string, number>;
}

export interface VariantLine {
  store_code: string;
  store_name: string;
  card_id: number | null;
  card_name: string | null;
  product_id: number;
  product_name: string;
  qty: number;
  unit: UnitType;
  price: number;
  discount: number;
  note: string | null;
}

export interface StoreBreakdown {
  store_code: string;
  store_name: string;
  subtotal: number;
  delivery: number;
  discount: number;
  total: number;
  card_id: number | null;
  card_name: string | null;
  offer_id: number | null;
  lines: VariantLine[];
  below_min_order: boolean;
}

export interface Variant {
  stores: StoreBreakdown[];
  total: number;
  baseline: number;
  penalty: number;
  handover: number;
  pack_extra: number;
  missing_products: string[];
  savings_rub: number;
  savings_pct: number;
  title: string;
  effort_total: number;
}

export interface PurchaseRow {
  id: number;
  date: string;
  store_id: number;
  store_code: string;
  store_name: string;
  product_id: number | null;
  raw_name: string;
  qty: number;
  unit_price: number;
  total: number;
  receipt_key: string;
}

export interface ReceiptRecord {
  key: string;
  date: string;
  store: string;
  total: number;
  rows: number;
  source: string;
}

export interface StoreConnection {
  store_code: string;
  connected: boolean;
  again: boolean;
  last: string | null;
}

export interface Workspace {
  phone: string;
  address: string | null;
  point: [number, number] | null;
  products: Map<number, Product>;
  nextProductId: number;
  /** product_id -> store_code -> StoreProductPrice */
  prices: Map<number, Map<string, StoreProductPrice>>;
  cards: Card[];
  offers: Offer[];
  baskets: Basket[];
  nextBasketId: number;
  /** basket_id -> BasketItem[] */
  basketItems: Map<number, BasketItem[]>;
  history: PurchaseRow[];
  receipts: ReceiptRecord[];
  connections: Map<string, StoreConnection>;
  lastBasketId: number | null;
  repeated: { key: string; basket: number } | null;
  fnsConnected: boolean;
  fnsPhone: string | null;
  fnsSyncedAt: string | null;
}

export interface ScreenMeta {
  key: string;
  path: string;
  title: string;
  eyebrow: string;
  section: string;
}
