import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const NNBSP = '\u202f';
export const NBSP = '\u00a0';

export const WHERE: Record<string, string> = {
  magnit: 'в Магните',
  vkusvill: 'во ВкусВилле',
  pyaterochka: 'в Пятёрочке',
  lenta: 'в Ленте',
  dixy: 'в Дикси',
  samokat: 'в Самокате',
  metro: 'в METRO',
  perekrestok: 'в Перекрёстке',
  fixprice: 'в FixPrice',
};

export const WHOM: Record<string, string> = {
  pyaterochka: 'Пятёрочку',
  lenta: 'Ленту',
};

const CHAIN_LETTERS: Record<string, string> = {
  magnit: 'М',
  pyaterochka: '5',
  vkusvill: 'В',
  lenta: 'Л',
  dixy: 'Д',
  samokat: 'С',
  metro: 'M',
  perekrestok: 'П',
  fixprice: 'F',
};

const MONTHS_GEN = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

const MONTHS_NOM = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

export function where(code?: string | null, name?: string | null): string {
  return (code && WHERE[code]) || `в ${name || code || 'магазине'}`;
}

export function whom(code?: string | null, name?: string | null): string {
  return (code && WHOM[code]) || name || code || 'магазин';
}

export function plural(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(Math.trunc(n));
  const tail = abs % 100;
  if (tail >= 11 && tail <= 14) return many;
  const last = abs % 10;
  return last === 1 ? one : last >= 2 && last <= 4 ? few : many;
}

export function goods(n: number): string {
  return `${n} ${plural(n, 'товар', 'товара', 'товаров')}`;
}

export function pluralItems(count: number): string {
  return `${count} ${plural(count, 'позиция', 'позиции', 'позиций')}`;
}

export function cheques(n: number): string {
  return `${n} ${plural(n, 'чек', 'чека', 'чеков')}`;
}

function groupThousands(intStr: string, sep: string): string {
  const neg = intStr.startsWith('-');
  const digits = neg ? intStr.slice(1) : intStr;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, sep);
  return (neg ? '−' : '') + grouped;
}

export function whole(value: number | string | null | undefined, big = false): string {
  if (value === null || value === undefined || value === '') return '—';
  const numVal = Number(value);
  if (!Number.isFinite(numVal)) return '—';
  const rounded = numVal >= 0 ? Math.floor(numVal + 0.5) : Math.ceil(numVal - 0.5);
  const text = groupThousands(String(rounded), NNBSP) + NNBSP;
  return big ? `${text}<span class="r">₽</span>` : `${text}₽`;
}

export function rub(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const numVal = Number(value);
  if (!Number.isFinite(numVal)) return '—';
  const fixed = Math.abs(numVal).toFixed(2);
  const [intPart, decPart] = fixed.split('.');
  const prefix = numVal < 0 ? '−' : '';
  return `${prefix}${groupThousands(intPart, NBSP)},${decPart}${NBSP}₽`;
}

export function num(value: number | string | null | undefined, digits = 3): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n - Math.round(n)) < 1e-9) {
    return String(Math.round(n));
  }
  return n.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '').replace('.', ',');
}

export function pct(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `${n.toFixed(1).replace('.', ',')}${NBSP}%`;
}

export function unitLabel(unit?: string | null): string {
  return (unit || 'pcs') === 'kg' ? 'кг' : 'шт';
}

export function qtyLabel(qty: number | string | null | undefined, unit?: string | null): string {
  return `${num(qty)} ${unitLabel(unit)}`;
}

export function humanDay(iso?: string | null): string {
  if (!iso) return '—';
  const parts = String(iso).slice(0, 10).split('-').map(Number);
  if (parts.length < 3 || parts.some((p) => !Number.isFinite(p))) return String(iso);
  const [year, month, day] = parts;
  const mName = MONTHS_GEN[month - 1];
  if (!mName) return String(iso);
  const text = `${day} ${mName}`;
  const curYear = new Date().getFullYear();
  return year === curYear ? text : `${text} ${year}`;
}

export function monthTitle(iso?: string | null): string {
  if (!iso || iso.length < 7) return 'Без даты';
  const year = Number(iso.slice(0, 4));
  const month = Number(iso.slice(5, 7));
  const mName = MONTHS_NOM[month - 1];
  if (!mName || !Number.isFinite(year)) return 'Без даты';
  const curYear = new Date().getFullYear();
  return year === curYear ? mName : `${mName} ${year}`;
}

const staticHashCache = new Map<string, string>();

export function staticV(filename: string): string {
  let stamp = staticHashCache.get(filename);
  if (!stamp) {
    try {
      const fullPath = path.resolve(process.cwd(), 'public', 'static', filename);
      const buf = fs.readFileSync(fullPath);
      stamp = crypto.createHash('sha256').update(buf).digest('hex').slice(0, 10);
    } catch {
      stamp = '1';
    }
    staticHashCache.set(filename, stamp);
  }
  return `/static/${filename}?v=${stamp}`;
}

export function escapeHtml(input: unknown): string {
  return String(input ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const ICON_PATHS: Record<string, string> = {
  cart: '<circle cx="9" cy="20" r="1.4"></circle><circle cx="18" cy="20" r="1.4"></circle><path d="M2.5 3.5h2.6l2.4 11.2a1.6 1.6 0 0 0 1.6 1.3h8.7a1.6 1.6 0 0 0 1.6-1.2l1.6-6.8H6.2"></path>',
  gear: '<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"></path>',
  clock: '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>',
  back: '<path d="M15 6l-6 6 6 6"></path>',
  next: '<path d="M9 6l6 6-6 6"></path>',
  down: '<path d="M6 9l6 6 6-6"></path>',
  check: '<path d="M4 12.5l5 5L20 6.5"></path>',
  x: '<path d="M6 6l12 12M18 6 6 18"></path>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"></rect><path d="M11 18.5h2"></path>',
  pin: '<path d="M12 21s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z"></path><circle cx="12" cy="9" r="2.5"></circle>',
  receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"></path><path d="M9 8h6M9 12h6M9 16h3"></path>',
  out: '<path d="M14 4h6v6"></path><path d="M20 4l-9 9"></path><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"></path>',
  minus: '<path d="M5 12h14"></path>',
  plus: '<path d="M12 5v14M5 12h14"></path>',
  trash: '<path d="M4.5 7h15"></path><path d="M9.5 7V4.5h5V7"></path><path d="M6.5 7l.9 12.1a1.8 1.8 0 0 0 1.8 1.7h5.6a1.8 1.8 0 0 0 1.8-1.7l.9-12.1"></path>',
  bag: '<path d="M5.5 8h13l-1 12.5a1 1 0 0 1-1 .9H7.5a1 1 0 0 1-1-.9z"></path><path d="M9 8V6.5a3 3 0 0 1 6 0V8"></path>',
  truck: '<path d="M2.5 6.5h11v9.5h-11z"></path><path d="M13.5 9.5h4l3 3.2v3.3h-7"></path><circle cx="6.5" cy="17.5" r="1.7"></circle><circle cx="17" cy="17.5" r="1.7"></circle>',
  alert: '<path d="M12 3.5 2.5 20h19z"></path><path d="M12 10v4.5M12 17.3v.2"></path>',
  grid: '<rect x="3.5" y="3.5" width="4.5" height="4.5" rx="1"></rect><rect x="9.75" y="3.5" width="4.5" height="4.5" rx="1"></rect><rect x="16" y="3.5" width="4.5" height="4.5" rx="1"></rect><rect x="3.5" y="9.75" width="4.5" height="4.5" rx="1"></rect><rect x="9.75" y="9.75" width="4.5" height="4.5" rx="1"></rect><rect x="16" y="9.75" width="4.5" height="4.5" rx="1"></rect><rect x="3.5" y="16" width="4.5" height="4.5" rx="1"></rect><rect x="9.75" y="16" width="4.5" height="4.5" rx="1"></rect><rect x="16" y="16" width="4.5" height="4.5" rx="1"></rect>',
  list: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"></path>',
  more: '<path d="M5.5 12h.01M12 12h.01M18.5 12h.01" stroke-width="3"></path>',
  search: '<circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.35-4.35"></path>',
};

export function icon(name: string, cls = 'i'): string {
  const body = ICON_PATHS[name] || '';
  return `<svg class="${escapeHtml(cls)}" viewBox="0 0 24 24" aria-hidden="true" data-icon="${escapeHtml(name)}">${body}</svg>`;
}

export function mark(code: string, name = ''): string {
  const letter = CHAIN_LETTERS[code] || (name || code || '?').slice(0, 1).toUpperCase();
  const safeCode = escapeHtml(code);
  return `<span class="lg s-${safeCode}" style="background: var(--s-${safeCode}, var(--ink2))" aria-hidden="true">${escapeHtml(letter)}</span>`;
}

export function pic(row: { photo?: string | null }): string {
  const url = row.photo && /^https?:\/\//i.test(row.photo) ? row.photo : null;
  if (url) {
    return `<span class="pic"><img src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()"></span>`;
  }
  return `<span class="pic">${icon('bag')}</span>`;
}
