import type {
  BasketLine,
  Card,
  Offer,
  Store,
  StoreBreakdown,
  StoreCartQuote,
  StorePerk,
  Variant,
  VariantLine,
} from './types.js';

const MIN_ORDER_FINE = 1e9;
const BRUTE_FORCE_LIMIT = 20;
const MAX_COMBOS = 250000;

export const EXTRA_ORDER_PENALTY_RUB = 150.0;
export const SPLIT_THRESHOLD_RUB = 250.0;

export const KIND_BY_STORE: Record<string, 'link' | 'items' | 'search' | 'list'> = {
  vkusvill: 'link',
  magnit: 'items',
  lenta: 'link',
  pyaterochka: 'search',
  dixy: 'items',
  samokat: 'search',
  metro: 'items',
  perekrestok: 'items',
  fixprice: 'items',
  vprok: 'items',
  monetka: 'items',
};

export const HANDOVER_PENALTY_RUB: Record<string, number> = {
  link: 0,
  items: 90,
  search: 160,
  list: 220,
};

export function handoverByStore(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [code, kind] of Object.entries(KIND_BY_STORE)) {
    out[code] = HANDOVER_PENALTY_RUB[kind] ?? 0;
  }
  return out;
}

function cents(value: number): number {
  return Math.round(Number(value) * 100);
}

function round2(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

export function isOfferValid(offer: Offer, day?: string): boolean {
  const today = day || new Date().toISOString().slice(0, 10);
  if (offer.requires_activation && !offer.activated) return false;
  if (offer.valid_from && today < offer.valid_from) return false;
  if (offer.valid_to && today > offer.valid_to) return false;
  return true;
}

export function offerCapLeft(offer: Offer): number {
  return Math.max(0, offer.cap_rub - offer.cap_used);
}

export function offerDiscount(subtotal: number, offer: Offer | null): number {
  if (!offer) return 0;
  if (subtotal < offer.min_check_rub) return 0;
  return Math.min((subtotal * offer.percent) / 100, offerCapLeft(offer));
}

export function bestOffer(subtotal: number, offers: Offer[]): [Offer | null, number] {
  let best: Offer | null = null;
  let bestDiscount = 0;
  for (const offer of offers) {
    if (!isOfferValid(offer)) continue;
    const d = offerDiscount(subtotal, offer);
    if (d <= 0) continue;
    if (d > bestDiscount + 1e-9) {
      best = offer;
      bestDiscount = d;
    } else if (best && Math.abs(d - bestDiscount) <= 1e-9) {
      if (offer.percent > best.percent || (offer.percent === best.percent && offer.id < best.id)) {
        best = offer;
        bestDiscount = d;
      }
    }
  }
  return [best, round2(bestDiscount)];
}

export function storeTotal(subtotal: number, store: Store, offer: Offer | null): [number, number, number] {
  const delivery = round2(subtotal >= store.free_delivery_from ? 0 : store.delivery_fee);
  const discount = round2(offerDiscount(subtotal, offer));
  const total = round2(subtotal + delivery - discount);
  return [delivery, discount, total];
}

function isAvailable(line: BasketLine, store: Store): boolean {
  const price = line.prices[store.code];
  if (price === undefined || price === null) return false;
  return line.in_stock[store.code] !== false;
}

interface StorePricingExtras {
  quote?: StoreCartQuote | null;
  perk?: StorePerk | null;
  applyBonuses?: boolean;
}

function computeStorePromoAndBonus(
  subtotal: number,
  extras?: StorePricingExtras
): {
  promoDiscount: number;
  promoLabel: string | null;
  bonusApplied: number;
  bonusAvailable: number;
  serviceFee: number;
  deliveryOverride: number | null;
} {
  const quote = extras?.quote ?? null;
  const perk = extras?.perk ?? null;
  const applyBonuses = extras?.applyBonuses !== false;

  const serviceFee = quote && quote.service_fee > 0 ? round2(quote.service_fee) : 0;
  const deliveryOverride =
    quote && quote.status === 'ready' && quote.delivery_fee !== null
      ? round2(Math.max(0, quote.delivery_fee))
      : null;

  let promoDiscount = 0;
  let promoLabel: string | null = null;
  if (quote && quote.status === 'ready' && quote.promo_discount > 0) {
    promoDiscount = Math.min(subtotal, round2(quote.promo_discount));
    promoLabel = quote.promo_label || 'Скидка магазина';
  } else if (perk) {
    const pctDisc = perk.promo_percent > 0 ? (subtotal * perk.promo_percent) / 100 : 0;
    const rubDisc = perk.promo_rub > 0 ? perk.promo_rub : 0;
    promoDiscount = Math.min(subtotal, round2(pctDisc + rubDisc));
    if (promoDiscount > 0) {
      promoLabel =
        perk.promo_label ||
        (perk.promo_percent > 0 ? `Спецпредложение −${perk.promo_percent}%` : 'Скидка сети');
    }
  }

  const afterPromo = Math.max(0, round2(subtotal - promoDiscount));
  const quoteBonus = quote && quote.bonus_balance > 0 ? quote.bonus_balance : 0;
  const perkBonus = perk && perk.bonus_balance > 0 ? perk.bonus_balance : 0;
  const bonusAvailable = round2(Math.max(quoteBonus, perkBonus));

  let bonusApplied = 0;
  if (applyBonuses && bonusAvailable > 0 && afterPromo > 0) {
    const maxPct = perk && perk.bonus_max_pct > 0 ? perk.bonus_max_pct : 50;
    const maxByPct = round2((afterPromo * Math.min(100, maxPct)) / 100);
    const maxByQuote =
      quote && quote.bonus_max_use > 0 ? round2(quote.bonus_max_use) : Number.POSITIVE_INFINITY;
    bonusApplied = round2(Math.min(bonusAvailable, maxByPct, maxByQuote, afterPromo));
  }

  return {
    promoDiscount,
    promoLabel,
    bonusApplied,
    bonusAvailable,
    serviceFee,
    deliveryOverride,
  };
}

function makeCostFn(
  store: Store,
  offers: Offer[],
  handover = 0,
  extras?: StorePricingExtras
): (subtotalCents: number) => number {
  const freeFrom = cents(store.free_delivery_from);
  const fee = cents(store.delivery_fee);
  const minOrder = cents(store.min_order);
  const handoverCents = cents(handover);
  const active = offers
    .filter((o) => isOfferValid(o))
    .map((o) => ({
      rate: o.percent / 100,
      capLeft: cents(offerCapLeft(o)),
      minCheck: cents(o.min_check_rub),
    }));

  return (subtotalCents: number): number => {
    const subtotalRub = subtotalCents / 100;
    const ex = computeStorePromoAndBonus(subtotalRub, extras);
    const deliveryCents =
      ex.deliveryOverride !== null
        ? cents(ex.deliveryOverride)
        : subtotalCents >= freeFrom
          ? 0
          : fee;
    let val =
      subtotalCents +
      deliveryCents +
      cents(ex.serviceFee) -
      cents(ex.promoDiscount) -
      cents(ex.bonusApplied);
    if (subtotalCents > 0 && subtotalCents < minOrder) {
      val += MIN_ORDER_FINE;
    }
    const payableCents = Math.max(
      0,
      subtotalCents - cents(ex.promoDiscount) - cents(ex.bonusApplied)
    );
    let discount = 0;
    for (const a of active) {
      if (payableCents >= a.minCheck) {
        const d = Math.min(payableCents * a.rate, a.capLeft);
        if (d > discount) discount = d;
      }
    }
    return Math.max(0, val - discount) + handoverCents;
  };
}

function scoreCombo(
  subs: number[],
  counts: number[],
  costs: Array<(s: number) => number>,
  penaltyCents: number
): number {
  let total = 0;
  for (let j = 0; j < counts.length; j++) {
    if (!counts[j]) return Number.POSITIVE_INFINITY;
    total += costs[j](subs[j]);
  }
  return total + penaltyCents * (counts.length - 1);
}

function bruteForcePair(
  prices: number[][],
  subs: number[],
  counts: number[],
  costs: Array<(s: number) => number>,
  penaltyCents: number,
  adjs: number[][]
): number[] | null {
  const k = prices.length;
  const p0 = prices.map((r) => r[0]);
  const p1 = prices.map((r) => r[1]);
  const a0 = adjs.map((r) => r[0]);
  const a1 = adjs.map((r) => r[1]);

  let sub0 = subs[0];
  let sub1 = subs[1];
  let cnt0 = counts[0] + k;
  let cnt1 = counts[1];
  let extra = 0;
  for (let i = 0; i < k; i++) {
    sub0 += p0[i];
    extra += a0[i];
  }

  const score = (s0: number, c0: number, s1: number, c1: number, add: number): number => {
    if (!c0 || !c1) return Number.POSITIVE_INFINITY;
    return costs[0](s0) + costs[1](s1) + penaltyCents + add;
  };

  let mask = 0;
  let bestScore = score(sub0, cnt0, sub1, cnt1, extra);
  let bestMask: number | null = Number.isFinite(bestScore) ? 0 : null;

  const limit = 1 << k;
  for (let step = 1; step < limit; step++) {
    const lsb = step & -step;
    const b = Math.clz32(1) - Math.clz32(lsb);
    if ((mask >> b) & 1) {
      mask &= ~(1 << b);
      sub1 -= p1[b];
      cnt1 -= 1;
      sub0 += p0[b];
      cnt0 += 1;
      extra += a0[b] - a1[b];
    } else {
      mask |= 1 << b;
      sub0 -= p0[b];
      cnt0 -= 1;
      sub1 += p1[b];
      cnt1 += 1;
      extra += a1[b] - a0[b];
    }
    const val = score(sub0, cnt0, sub1, cnt1, extra);
    if (val < bestScore - 1e-9 || (bestMask === null && Number.isFinite(val))) {
      bestScore = val;
      bestMask = mask;
    }
  }

  if (bestMask === null) return null;
  const out: number[] = [];
  for (let pos = 0; pos < k; pos++) {
    out.push((bestMask >> pos) & 1);
  }
  return out;
}

function bruteForceAny(
  free: Array<{ idx: number; options: number[] }>,
  prices: Array<Array<number | null>>,
  subs: number[],
  counts: number[],
  costs: Array<(s: number) => number>,
  penaltyCents: number,
  adjs: number[][]
): number[] | null {
  const k = free.length;
  const choice = new Array<number>(k);
  let bestChoice: number[] | null = null;
  let bestScore = Number.POSITIVE_INFINITY;

  const curSubs = [...subs];
  const curCounts = [...counts];

  function dfs(pos: number, extra: number): void {
    if (pos === k) {
      const val = scoreCombo(curSubs, curCounts, costs, penaltyCents) + extra;
      if (val < bestScore - 1e-9) {
        bestScore = val;
        bestChoice = [...choice];
      }
      return;
    }
    for (const j of free[pos].options) {
      const p = prices[pos][j];
      if (p === null) continue;
      choice[pos] = j;
      curSubs[j] += p;
      curCounts[j] += 1;
      dfs(pos + 1, extra + adjs[pos][j]);
      curSubs[j] -= p;
      curCounts[j] -= 1;
    }
  }

  dfs(0, 0);
  return bestChoice;
}

function localSearch(
  free: Array<{ idx: number; options: number[] }>,
  prices: Array<Array<number | null>>,
  subs: number[],
  counts: number[],
  costs: Array<(s: number) => number>,
  penaltyCents: number,
  adjs: number[][]
): number[] | null {
  const n = costs.length;
  const k = free.length;
  const minOpt = (pos: number) =>
    free[pos].options.reduce((best, j) =>
      (prices[pos][j] ?? Infinity) < (prices[pos][best] ?? Infinity) ? j : best
    );

  const starts: number[][] = [free.map((_, pos) => minOpt(pos))];
  for (let target = 0; target < n; target++) {
    starts.push(
      free.map((f, pos) => (f.options.includes(target) ? target : minOpt(pos)))
    );
  }

  let bestChoice: number[] | null = null;
  let bestVal = Number.POSITIVE_INFINITY;

  for (const start of starts) {
    const choice = [...start];
    const curSubs = [...subs];
    const curCounts = [...counts];
    let extra = 0;
    for (let pos = 0; pos < k; pos++) {
      const j = choice[pos];
      curSubs[j] += prices[pos][j] ?? 0;
      curCounts[j] += 1;
      extra += adjs[pos][j];
    }
    let best = scoreCombo(curSubs, curCounts, costs, penaltyCents) + extra;
    let improved = true;
    while (improved) {
      improved = false;
      for (let pos = 0; pos < k; pos++) {
        let cur = choice[pos];
        for (const j of free[pos].options) {
          if (j === cur) continue;
          const pCur = prices[pos][cur] ?? 0;
          const pNext = prices[pos][j] ?? 0;
          curSubs[cur] -= pCur;
          curCounts[cur] -= 1;
          curSubs[j] += pNext;
          curCounts[j] += 1;
          const moved = extra - adjs[pos][cur] + adjs[pos][j];
          const val = scoreCombo(curSubs, curCounts, costs, penaltyCents) + moved;
          if (val < best - 1e-9) {
            best = val;
            choice[pos] = j;
            cur = j;
            extra = moved;
            improved = true;
          } else {
            curSubs[j] -= pNext;
            curCounts[j] -= 1;
            curSubs[cur] += pCur;
            curCounts[cur] += 1;
          }
        }
      }
    }
    if (best < bestVal - 1e-9) {
      bestVal = best;
      bestChoice = choice;
    }
  }

  return Number.isFinite(bestVal) ? bestChoice : null;
}

function bestAssignment(
  lines: BasketLine[],
  combo: Store[],
  costs: Array<(s: number) => number>,
  penaltyCents: number
): { assign: Array<number | null>; missing: number[] } | null {
  const n = combo.length;
  const assign = new Array<number | null>(lines.length).fill(null);
  const missing: number[] = [];
  const free: Array<{ idx: number; options: number[] }> = [];
  const subs = new Array<number>(n).fill(0);
  const counts = new Array<number>(n).fill(0);

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const options: number[] = [];
    for (let j = 0; j < n; j++) {
      if (isAvailable(line, combo[j])) options.push(j);
    }
    if (options.length === 0) {
      missing.push(i);
    } else if (options.length === 1) {
      const j = options[0];
      assign[i] = j;
      subs[j] += cents(line.prices[combo[j].code]);
      counts[j] += 1;
    } else {
      free.push({ idx: i, options });
    }
  }

  if (free.length === 0) {
    return counts.every((c) => c > 0) ? { assign, missing } : null;
  }

  const prices = free.map((f) =>
    combo.map((s, j) => (f.options.includes(j) ? cents(lines[f.idx].prices[s.code]) : null))
  );
  const adjs = free.map((f) =>
    combo.map((s, j) => (f.options.includes(j) ? cents(lines[f.idx].adjust[s.code] ?? 0) : 0))
  );

  let chosen: number[] | null;
  if (n === 2 && free.length <= BRUTE_FORCE_LIMIT && prices.every((r) => r[0] !== null && r[1] !== null)) {
    chosen = bruteForcePair(prices as number[][], subs, counts, costs, penaltyCents, adjs);
  } else if (Math.pow(n, free.length) <= MAX_COMBOS && free.length <= BRUTE_FORCE_LIMIT) {
    chosen = bruteForceAny(free, prices, subs, counts, costs, penaltyCents, adjs);
  } else {
    chosen = localSearch(free, prices, subs, counts, costs, penaltyCents, adjs);
  }

  if (!chosen) return null;
  for (let pos = 0; pos < free.length; pos++) {
    assign[free[pos].idx] = chosen[pos];
  }
  return { assign, missing };
}

function coveredBaseline(
  lines: BasketLine[],
  missing: number[],
  baseline: number,
  baselineLines: Record<number, number> | null
): number {
  let base = baseline;
  for (const i of missing) {
    const line = lines[i];
    let part = 0;
    if (baselineLines && baselineLines[line.product_id] !== undefined) {
      part = baselineLines[line.product_id];
    } else {
      const known = Object.values(line.prices).filter((p) => Number.isFinite(p));
      part = known.length ? Math.min(...known) : 0;
    }
    base -= part;
  }
  return round2(Math.max(0, base));
}

function buildVariant(
  lines: BasketLine[],
  combo: Store[],
  assign: Array<number | null>,
  missing: number[],
  offers: Map<number, Offer[]>,
  cardsById: Map<number, Card>,
  baseline: number,
  penalty: number,
  handoverMap: Record<string, number>,
  baselineLines: Record<number, number> | null,
  storeQuotes?: Record<string, StoreCartQuote> | null,
  storePerks?: Record<string, StorePerk> | null,
  applyBonuses = true
): Variant | null {
  const groups = new Map<number, number[]>();
  for (let i = 0; i < assign.length; i++) {
    const j = assign[i];
    if (j !== null) {
      const arr = groups.get(j) || [];
      arr.push(i);
      groups.set(j, arr);
    }
  }
  if (groups.size === 0) return null;

  const breakdowns: StoreBreakdown[] = [];
  for (const j of [...groups.keys()].sort((a, b) => a - b)) {
    const store = combo[j];
    const idxs = groups.get(j)!;
    const subtotal = round2(idxs.reduce((acc, i) => acc + lines[i].prices[store.code], 0));
    const quote = storeQuotes ? storeQuotes[store.code] ?? null : null;
    const perk = storePerks ? storePerks[store.code] ?? null : null;
    const ex = computeStorePromoAndBonus(subtotal, { quote, perk, applyBonuses });

    const payableBeforeCashback = Math.max(0, round2(subtotal - ex.promoDiscount - ex.bonusApplied));
    const [offer] = bestOffer(payableBeforeCashback, offers.get(store.id) || []);
    const [defaultDelivery, discount] = storeTotal(payableBeforeCashback, store, offer);
    const delivery = ex.deliveryOverride !== null ? ex.deliveryOverride : defaultDelivery;
    const total = round2(
      Math.max(0, subtotal + delivery + ex.serviceFee - ex.promoDiscount - ex.bonusApplied - discount)
    );
    const cardName = offer ? (() => {
      const card = cardsById.get(offer.card_id);
      return card ? `${card.bank} · ${card.name} (−${Math.round(offer.percent)}%)` : null;
    })() : null;

    const vlines: VariantLine[] = idxs.map((i) => {
      const ln = lines[i];
      const price = round2(ln.prices[store.code]);
      const share = subtotal ? round2((discount * price) / subtotal) : 0;
      return {
        store_code: store.code,
        store_name: store.name,
        card_id: offer ? offer.card_id : null,
        card_name: cardName,
        product_id: ln.product_id,
        product_name: ln.title || ln.name,
        qty: ln.qty,
        unit: ln.unit,
        price,
        discount: share,
        note: ln.notes[store.code] ?? null,
      };
    });

    const residue = round2(discount - vlines.reduce((acc, v) => acc + v.discount, 0));
    if (vlines.length && residue) {
      vlines[vlines.length - 1].discount = round2(vlines[vlines.length - 1].discount + residue);
    }

    breakdowns.push({
      store_code: store.code,
      store_name: store.name,
      subtotal,
      delivery,
      service_fee: ex.serviceFee,
      promo_discount: ex.promoDiscount,
      promo_label: ex.promoLabel,
      bonus_applied: ex.bonusApplied,
      bonus_available: ex.bonusAvailable,
      discount,
      total,
      card_id: offer ? offer.card_id : null,
      card_name: cardName,
      offer_id: offer ? offer.id : null,
      lines: vlines,
      below_min_order: store.min_order > 0 && subtotal < store.min_order,
      address_checked: quote?.address ?? null,
      live_verified: quote?.status === 'ready',
    });
  }

  const variantPenalty = round2(penalty * (breakdowns.length - 1));
  const total = round2(breakdowns.reduce((acc, b) => acc + b.total, 0));
  const handover = round2(
    breakdowns.reduce((acc, b) => acc + (handoverMap[b.store_code] ?? 0), 0)
  );
  let packExtra = 0;
  for (const [j, idxs] of groups.entries()) {
    const code = combo[j].code;
    for (const i of idxs) {
      packExtra += lines[i].adjust[code] ?? 0;
    }
  }
  packExtra = round2(packExtra);

  const covBase = coveredBaseline(lines, missing, baseline, baselineLines);
  const savingsRub = round2(covBase - total);
  const savingsPct = covBase ? round2((savingsRub / covBase) * 100) : 0;
  const effortTotal = round2(total + variantPenalty + handover + packExtra);

  return {
    stores: breakdowns,
    total,
    baseline: covBase,
    penalty: variantPenalty,
    handover,
    pack_extra: packExtra,
    missing_products: missing.map((i) => lines[i].title || lines[i].name),
    savings_rub: savingsRub,
    savings_pct: savingsPct,
    title: breakdowns.map((s) => s.store_name).join(' + '),
    effort_total: effortTotal,
  };
}

function combinations<T>(items: T[], k: number): T[][] {
  const result: T[][] = [];
  function walk(start: number, current: T[]): void {
    if (current.length === k) {
      result.push([...current]);
      return;
    }
    for (let i = start; i < items.length; i++) {
      current.push(items[i]);
      walk(i + 1, current);
      current.pop();
    }
  }
  walk(0, []);
  return result;
}

export function rankKey(v: Variant): [number, number, number] {
  return [
    v.missing_products.length,
    v.stores.filter((b) => b.below_min_order).length,
    v.effort_total,
  ];
}

export function compareVariants(a: Variant, b: Variant): number {
  const ka = rankKey(a);
  const kb = rankKey(b);
  if (ka[0] !== kb[0]) return ka[0] - kb[0];
  if (ka[1] !== kb[1]) return ka[1] - kb[1];
  return ka[2] - kb[2];
}

export function optimize(params: {
  lines: BasketLine[];
  stores: Store[];
  offers: Map<number, Offer[]>;
  cards: Card[];
  baseline: number;
  penalty?: number;
  topN?: number;
  maxStores?: number;
  handover?: Record<string, number>;
  baselineLines?: Record<number, number> | null;
  storeQuotes?: Record<string, StoreCartQuote> | null;
  storePerks?: Record<string, StorePerk> | null;
  applyBonuses?: boolean;
}): Variant[] {
  const {
    lines,
    stores,
    offers,
    cards,
    baseline,
    penalty = EXTRA_ORDER_PENALTY_RUB,
    topN = 3,
    maxStores = 2,
    handover = handoverByStore(),
    baselineLines = null,
    storeQuotes = null,
    storePerks = null,
    applyBonuses = true,
  } = params;
  if (!lines.length || !stores.length) return [];

  const cardsById = new Map(cards.map((c) => [c.id, c]));
  const costsByStore = new Map<string, (s: number) => number>();
  for (const s of stores) {
    costsByStore.set(
      s.code,
      makeCostFn(s, offers.get(s.id) || [], handover[s.code] ?? 0, {
        quote: storeQuotes ? storeQuotes[s.code] ?? null : null,
        perk: storePerks ? storePerks[s.code] ?? null : null,
        applyBonuses,
      })
    );
  }
  const penaltyCents = cents(penalty);
  const limit = Math.max(1, Math.min(maxStores, stores.length));
  const bestByKey = new Map<string, Variant>();

  for (let size = 1; size <= limit; size++) {
    for (const combo of combinations(stores, size)) {
      const costs = combo.map((s) => costsByStore.get(s.code)!);
      const found = bestAssignment(lines, combo, costs, penaltyCents);
      if (!found) continue;
      const variant = buildVariant(
        lines,
        combo,
        found.assign,
        found.missing,
        offers,
        cardsById,
        baseline,
        penalty,
        handover,
        baselineLines,
        storeQuotes,
        storePerks,
        applyBonuses
      );
      if (!variant) continue;
      const key = [...variant.stores.map((b) => b.store_code)].sort().join('+');
      const existing = bestByKey.get(key);
      if (!existing || variant.effort_total < existing.effort_total) {
        bestByKey.set(key, variant);
      }
    }
  }

  return [...bestByKey.values()].sort(compareVariants).slice(0, Math.max(0, topN));
}

export function chooseBestVariant(
  variants: Variant[],
  mode?: string | null
): {
  best: Variant;
  switchInfo: {
    split: { label: string; on: boolean };
    single: { label: string; on: boolean };
    gain: number;
    threshold: number;
    worth: boolean;
  } | null;
} {
  const split = variants.find((v) => v.stores.length > 1) || null;
  const single = variants.find((v) => v.stores.length === 1) || null;
  if (!split || !single) {
    return { best: variants[0], switchInfo: null };
  }
  const gain = round2(single.total - split.total);
  const threshold = SPLIT_THRESHOLD_RUB;
  let chosenMode = mode === 'split' || mode === 'single' ? mode : null;
  if (!chosenMode) {
    chosenMode = variants.indexOf(split) < variants.indexOf(single) ? 'split' : 'single';
    if (
      chosenMode === 'split' &&
      gain < threshold &&
      single.missing_products.length <= split.missing_products.length
    ) {
      chosenMode = 'single';
    }
  }
  return {
    best: chosenMode === 'split' ? split : single,
    switchInfo: {
      split: { label: `Разделить · ${split.total.toFixed(2).replace('.', ',')} ₽`, on: chosenMode === 'split' },
      single: { label: `Один магазин · ${single.total.toFixed(2).replace('.', ',')} ₽`, on: chosenMode === 'single' },
      gain,
      threshold,
      worth: gain >= threshold,
    },
  };
}
