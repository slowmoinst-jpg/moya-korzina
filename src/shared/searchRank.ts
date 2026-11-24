import type { Product, PurchaseRow, StoreProductPrice } from '../types.js';

type SearchRow = {
  product: Product;
  prices: Record<string, StoreProductPrice>;
  available?: boolean;
};

export function matchesProductQuery(product: Product, tokens: string[], query: string): boolean {
  const normalize = (value: string) => value.toLowerCase().replace(/яйц[ао](?![а-яё])/gu, 'яйца');
  const name = normalize(`${product.name} ${product.brand || ''}`);
  return tokens.every(token => name.includes(normalize(token))) || normalize(product.category || '') === normalize(query);
}

export function purchasedProducts(history: PurchaseRow[]): Set<number> {
  return new Set(history.flatMap(row =>
    row.product_id !== null && row.qty > 0 && row.total >= 0 && !row.is_service ? [row.product_id] : []));
}

export function rankProducts<T extends SearchRow>(rows: T[], purchased: Set<number>, store?: string): T[] {
  const ranked = rows.map(row => {
    const offers = store ? [row.prices[store]].filter(Boolean) : Object.values(row.prices);
    const available = row.available ?? offers.some(offer => offer.in_stock);
    const prices = offers.filter(offer => offer.price > 0 && (!available || offer.in_stock));
    return {row, available, bought: row.product.previouslyBought || purchased.has(row.product.id),
      price: Math.min(...prices.map(offer => offer.price))};
  });
  ranked.sort((a,b) => Number(b.bought) - Number(a.bought) || Number(b.available) - Number(a.available)
    || (a.price === b.price ? 0 : a.price - b.price));
  return ranked.map(item => item.row);
}
