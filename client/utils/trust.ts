import type { Product, PurchaseRow, StoreProductPrice, UnitType } from '../../src/types';
import type { BasketItemView } from '../engine';
import type { SearchResultItem, WorkspaceSnapshot } from '../types';

export type TrustTier = 'bought' | 'brand' | 'price_band' | 'cheaper';

export interface TrustProfile {
  boughtCounts: Map<number, number>;
  trustedBrands: Set<string>;
  trustedBrandsByCategory: Map<string, Set<string>>;
  /** Categories where the user historically buys only 1 brand (2+ times) — high substitution cost. */
  rigidCategories: Set<string>;
  /** Categories where the user buys 2+ different brands — flexible substitution. */
  flexibleCategories: Set<string>;
}

export interface RankedReplacement<T> {
  row: T;
  tier: TrustTier;
  tierRank: number;
  label: string;
  price: number;
}

export interface SuggestedReplacement {
  item: SearchResultItem;
  storeCode: string;
  unitPrice: number;
  lineTotal: number;
  tier: TrustTier;
  label: string;
}

export interface SingleStoreEstimate {
  storeCode: string;
  storeName: string;
  exactCount: number;
  totalItems: number;
  boughtCount: number;
  brandCount: number;
  bandCount: number;
  cheaperCount: number;
  unresolvedCount: number;
  estimatedTotal: number;
  isComplete: boolean;
  suggestions: Map<number, SuggestedReplacement>;
  summaryLine: string;
}

const TRUST_LABEL: Record<TrustTier, string> = {
  bought: 'Вы покупали',
  brand: 'Ваш бренд',
  price_band: 'В вашей цене',
  cheaper: 'Похожий',
};

const TIER_RANK: Record<TrustTier, number> = {
  bought: 0,
  brand: 1,
  price_band: 2,
  cheaper: 3,
};

const STOP_WORDS = new Set([
  'г', 'гр', 'кг', 'мл', 'л', 'шт', 'уп', 'пач', 'б', 'бут', 'ст',
  'без', 'с', 'и', 'в', 'на', 'из', 'для', 'от', 'до', 'по',
  'гост', 'бзмж', 'змж', 'ассортименте',
]);

export function normalizeBrand(brand: string | null | undefined): string {
  return (brand || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[^0-9a-zа-я]+/g, ' ')
    .trim();
}

export function normalizeCategory(cat: string | null | undefined): string {
  return (cat || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .trim();
}

export function extractKeywords(name: string): string[] {
  const clean = name
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\bяйц[ао]\b/g, 'яйца')
    .replace(/[^0-9a-zа-я]+/g, ' ')
    .trim();
  if (!clean) return [];
  return clean
    .split(/\s+/)
    .filter((w) => w.length >= 3 && !/^\d+$/.test(w) && !STOP_WORDS.has(w));
}

export function buildTrustProfile(
  history: PurchaseRow[],
  products: Product[]
): TrustProfile {
  const byId = new Map(products.map((p) => [p.id, p]));
  const boughtCounts = new Map<number, number>();
  const trustedBrands = new Set<string>();
  const trustedBrandsByCategory = new Map<string, Set<string>>();
  const brandHitsByCategory = new Map<string, Map<string, number>>();

  for (const row of history) {
    if (row.product_id === null || row.qty <= 0 || row.total < 0 || row.is_service) continue;
    boughtCounts.set(row.product_id, (boughtCounts.get(row.product_id) || 0) + 1);
    const prod = byId.get(row.product_id);
    if (!prod) continue;
    const brand = normalizeBrand(prod.brand);
    if (!brand) continue;
    trustedBrands.add(brand);
    const cat = normalizeCategory(prod.category);
    if (cat) {
      let set = trustedBrandsByCategory.get(cat);
      if (!set) {
        set = new Set<string>();
        trustedBrandsByCategory.set(cat, set);
      }
      set.add(brand);

      let hits = brandHitsByCategory.get(cat);
      if (!hits) {
        hits = new Map<string, number>();
        brandHitsByCategory.set(cat, hits);
      }
      hits.set(brand, (hits.get(brand) || 0) + 1);
    }
  }

  const rigidCategories = new Set<string>();
  const flexibleCategories = new Set<string>();
  for (const [cat, hits] of brandHitsByCategory.entries()) {
    if (hits.size >= 2) {
      flexibleCategories.add(cat);
    } else if (hits.size === 1) {
      const onlyCount = Array.from(hits.values())[0];
      if (onlyCount >= 2) rigidCategories.add(cat);
    }
  }

  return {
    boughtCounts,
    trustedBrands,
    trustedBrandsByCategory,
    rigidCategories,
    flexibleCategories,
  };
}

function normalizedUnitPrice(price: number, weightG: number | null | undefined, unit: UnitType): number {
  if (unit === 'kg') return price;
  if (weightG && weightG >= 30) return (price / weightG) * 1000;
  return price;
}

export function classifyTrustTier(
  candidate: Product,
  candidatePrice: number,
  target: { category?: string | null; referencePrice?: number | null; weight_g?: number | null; unit: UnitType },
  profile: TrustProfile
): { tier: TrustTier; label: string; bandDistance: number } {
  const bought = Boolean(candidate.previouslyBought) || (profile.boughtCounts.get(candidate.id) || 0) > 0;
  if (bought) {
    return { tier: 'bought', label: TRUST_LABEL.bought, bandDistance: 0 };
  }

  const candBrand = normalizeBrand(candidate.brand);
  const targetCat = normalizeCategory(target.category || candidate.category);
  const catBrands = targetCat ? profile.trustedBrandsByCategory.get(targetCat) : undefined;
  if (candBrand && (catBrands?.has(candBrand) || profile.trustedBrands.has(candBrand))) {
    return { tier: 'brand', label: TRUST_LABEL.brand, bandDistance: 0 };
  }

  if (target.referencePrice && target.referencePrice > 0 && candidatePrice > 0) {
    const refNorm = normalizedUnitPrice(target.referencePrice, target.weight_g, target.unit);
    const candNorm = normalizedUnitPrice(candidatePrice, candidate.weight_g, candidate.unit);
    const ratio = Math.abs(candNorm - refNorm) / refNorm;
    if (ratio <= 0.25) {
      return { tier: 'price_band', label: TRUST_LABEL.price_band, bandDistance: ratio };
    }
  }

  return { tier: 'cheaper', label: TRUST_LABEL.cheaper, bandDistance: Number.POSITIVE_INFINITY };
}

export function rankTrustedReplacements<
  T extends { product: Product; prices: Record<string, StoreProductPrice> }
>(
  rows: T[],
  target: { category?: string | null; referencePrice?: number | null; weight_g?: number | null; unit: UnitType },
  profile: TrustProfile,
  storeCode?: string
): RankedReplacement<T>[] {
  const scored: Array<RankedReplacement<T> & { boughtHits: number; catBrandMatch: boolean; bandDistance: number }> = [];
  const targetCat = normalizeCategory(target.category);
  const catBrands = targetCat ? profile.trustedBrandsByCategory.get(targetCat) : undefined;

  for (const row of rows) {
    const offers = storeCode
      ? [row.prices[storeCode]].filter((o): o is StoreProductPrice => Boolean(o && o.in_stock && o.price > 0))
      : Object.values(row.prices).filter((o) => o.in_stock && o.price > 0);
    if (!offers.length) continue;
    const price = Math.min(...offers.map((o) => o.price));
    const { tier, label, bandDistance } = classifyTrustTier(row.product, price, target, profile);
    const candBrand = normalizeBrand(row.product.brand);
    scored.push({
      row,
      tier,
      tierRank: TIER_RANK[tier],
      label,
      price,
      boughtHits: profile.boughtCounts.get(row.product.id) || (row.product.previouslyBought ? 1 : 0),
      catBrandMatch: Boolean(candBrand && catBrands?.has(candBrand)),
      bandDistance,
    });
  }

  scored.sort((a, b) => {
    if (a.tierRank !== b.tierRank) return a.tierRank - b.tierRank;
    if (a.tier === 'bought' && a.boughtHits !== b.boughtHits) return b.boughtHits - a.boughtHits;
    if (a.tier === 'brand' && a.catBrandMatch !== b.catBrandMatch) return Number(b.catBrandMatch) - Number(a.catBrandMatch);
    if (a.tier === 'price_band' && Math.abs(a.bandDistance - b.bandDistance) > 0.01) {
      return a.bandDistance - b.bandDistance;
    }
    return a.price - b.price || a.row.product.name.localeCompare(b.row.product.name, 'ru');
  });

  return scored.map(({ row, tier, tierRank, label, price }) => ({ row, tier, tierRank, label, price }));
}

function similarityScore(target: Product, candidate: Product): number {
  const targetCat = normalizeCategory(target.category);
  const candCat = normalizeCategory(candidate.category);
  const targetWords = extractKeywords(target.title || target.name);
  const candWords = new Set(extractKeywords(candidate.title || candidate.name));

  let overlap = 0;
  for (const w of targetWords) {
    if (candWords.has(w)) overlap += 1;
    else {
      for (const cw of candWords) {
        if (w.length >= 4 && cw.length >= 4 && (cw.startsWith(w.slice(0, 4)) || w.startsWith(cw.slice(0, 4)))) {
          overlap += 0.6;
          break;
        }
      }
    }
  }

  const sameFirstWord =
    targetWords.length > 0 &&
    extractKeywords(candidate.title || candidate.name)[0] === targetWords[0];

  if (overlap >= 1) return overlap + (sameFirstWord ? 1 : 0) + (targetCat && targetCat === candCat ? 0.5 : 0);
  if (targetCat && targetCat === candCat && targetCat !== 'прочее' && targetCat !== 'другое') return 0.5;
  return 0;
}

function referenceUnitPriceFor(
  pid: number,
  ws: WorkspaceSnapshot
): number | null {
  const hist = ws.history.find((r) => r.product_id === pid && r.unit_price > 0);
  if (hist) return hist.unit_price;
  const pMap = ws.prices[String(pid)];
  if (!pMap) return null;
  const live = Object.values(pMap)
    .filter((o) => o.in_stock && o.price > 0)
    .map((o) => o.price);
  if (!live.length) return null;
  return live.reduce((a, b) => a + b, 0) / live.length;
}

export function estimateSingleStoreWithReplacements(
  storeCode: string,
  storeName: string,
  exactTotal: number,
  exactCount: number,
  missingItems: BasketItemView[],
  ws: WorkspaceSnapshot,
  profile: TrustProfile
): SingleStoreEstimate {
  const totalItems = exactCount + missingItems.length;
  const byId = new Map(ws.products.map((p) => [p.id, p]));
  const suggestions = new Map<number, SuggestedReplacement>();
  const usedIds = new Set<number>();

  let addedTotal = 0;
  let boughtCount = 0;
  let brandCount = 0;
  let bandCount = 0;
  let cheaperCount = 0;
  let unresolvedCount = 0;

  for (const miss of missingItems) {
    const targetProd = byId.get(miss.product_id) || {
      id: miss.product_id,
      name: miss.name,
      brand: null,
      weight_g: miss.weight_g,
      unit: miss.unit,
      category: null,
      active: true,
    };
    const refPrice = referenceUnitPriceFor(miss.product_id, ws);

    const candidates: Array<{ item: SearchResultItem; sim: number }> = [];
    for (const prod of ws.products) {
      if (!prod.active || prod.id === miss.product_id || usedIds.has(prod.id)) continue;
      if (prod.unit !== miss.unit) continue;
      const offer = ws.prices[String(prod.id)]?.[storeCode];
      if (!offer || !offer.in_stock || offer.price <= 0) continue;
      const sim = similarityScore(targetProd, prod);
      if (sim < 0.5) continue;
      candidates.push({
        sim,
        item: {
          available: true,
          product: {
            ...prod,
            previouslyBought: prod.previouslyBought || (profile.boughtCounts.get(prod.id) || 0) > 0,
          },
          prices: { [storeCode]: offer },
          chainsCount: 1,
          small: storeName,
        },
      });
    }

    if (!candidates.length) {
      unresolvedCount += 1;
      continue;
    }

    // Prefer strong keyword similarity first when available, then trust ladder inside matching pool
    const strong = candidates.filter((c) => c.sim >= 1);
    const pool = (strong.length ? strong : candidates).map((c) => c.item);
    const ranked = rankTrustedReplacements(
      pool,
      {
        category: targetProd.category,
        referencePrice: refPrice,
        weight_g: targetProd.weight_g,
        unit: miss.unit,
      },
      profile,
      storeCode
    );

    const top = ranked[0];
    if (!top) {
      unresolvedCount += 1;
      continue;
    }

    usedIds.add(top.row.product.id);
    const lineTotal = Math.round(top.price * miss.qty * 100) / 100;
    addedTotal += lineTotal;

    if (top.tier === 'bought') boughtCount += 1;
    else if (top.tier === 'brand') brandCount += 1;
    else if (top.tier === 'price_band') bandCount += 1;
    else cheaperCount += 1;

    suggestions.set(miss.product_id, {
      item: {
        ...top.row,
        trustTier: top.tier,
        trustLabel: top.label,
      },
      storeCode,
      unitPrice: top.price,
      lineTotal,
      tier: top.tier,
      label: top.label,
    });
  }

  const trustedCount = boughtCount + brandCount;
  const fallbackCount = bandCount + cheaperCount;
  const parts: string[] = [];
  if (exactCount > 0) parts.push(`${exactCount} ваших`);
  if (trustedCount > 0) parts.push(`+${trustedCount} из истории`);
  if (fallbackCount > 0) parts.push(`+${fallbackCount} аналог`);
  if (unresolvedCount > 0) parts.push(`${unresolvedCount} подобрать`);

  return {
    storeCode,
    storeName,
    exactCount,
    totalItems,
    boughtCount,
    brandCount,
    bandCount,
    cheaperCount,
    unresolvedCount,
    estimatedTotal: Math.round((exactTotal + addedTotal) * 100) / 100,
    isComplete: unresolvedCount === 0,
    suggestions,
    summaryLine: parts.join(' · '),
  };
}
