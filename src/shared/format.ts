export const NNBSP = '\u202f';
export const NBSP = '\u00a0';

export const PHONE_CHAINS = [
  'magnit',
  'pyaterochka',
  'vkusvill',
  'lenta',
  'dixy',
  'samokat',
  'metro',
  'perekrestok',
  'fixprice',
  'vprok',
  'monetka',
] as const;

export const ORDERABLE_STORES = new Set([
  'magnit',
  'vkusvill',
  'pyaterochka',
  'lenta',
  'dixy',
  'samokat',
  'metro',
  'perekrestok',
  'fixprice',
  'vprok',
  'monetka',
]);

export const SEARCH_URL_TEMPLATES: Record<string, string> = {
  pyaterochka: 'https://5ka.ru/search/?text={q}',
  samokat: 'https://samokat.ru/search?q={q}',
  magnit: 'https://magnit.ru/search?q={q}',
  dixy: 'https://dixy.ru/search/?q={q}',
  metro: 'https://online.metro-cc.ru/search?q={q}',
  vkusvill: 'https://vkusvill.ru/search/?q={q}',
  lenta: 'https://lenta.com/search/?searchText={q}',
  perekrestok: 'https://www.perekrestok.ru/cat/search?search={q}',
  fixprice: 'https://fix-price.com/catalog?q={q}',
  vprok: 'https://www.vprok.ru/catalog/search?text={q}',
  monetka: 'https://monetka.ru/catalog?search={q}',
};

export const STORE_HOME_URLS: Record<string, string> = {
  magnit: 'https://magnit.ru/',
  pyaterochka: 'https://5ka.ru/',
  vkusvill: 'https://vkusvill.ru/',
  lenta: 'https://lenta.com/',
  dixy: 'https://dixy.ru/',
  samokat: 'https://samokat.ru/',
  metro: 'https://online.metro-cc.ru/',
  perekrestok: 'https://www.perekrestok.ru/',
  fixprice: 'https://fix-price.com/',
  vprok: 'https://www.vprok.ru/',
  monetka: 'https://monetka.ru/',
};

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
  vprok: 'во Впроке',
  monetka: 'в Монетке',
};

export const WHOM: Record<string, string> = {
  pyaterochka: 'Пятёрочку',
  lenta: 'Ленту',
  monetka: 'Монетку',
};

export const CHAIN_LETTERS: Record<string, string> = {
  magnit: 'М',
  pyaterochka: '5',
  vkusvill: 'В',
  lenta: 'Л',
  dixy: 'Д',
  samokat: 'С',
  metro: 'M',
  perekrestok: 'П',
  fixprice: 'F',
  vprok: 'Вп',
  monetka: 'Мн',
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

export function chainLetter(code: string, name = ''): string {
  return CHAIN_LETTERS[code] || (name || code || '?').slice(0, 1).toUpperCase();
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

export function wholeNumberStr(value: number | string | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  const numVal = Number(value);
  if (!Number.isFinite(numVal)) return null;
  const rounded = numVal >= 0 ? Math.floor(numVal + 0.5) : Math.ceil(numVal - 0.5);
  return groupThousands(String(rounded), NNBSP);
}

export function wholeText(value: number | string | null | undefined): string {
  const s = wholeNumberStr(value);
  return s === null ? '—' : `${s}${NNBSP}₽`;
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

export function stepForUnit(unit?: string | null): number {
  return unit === 'kg' ? 0.1 : 1;
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

export function buildStoreSearchUrl(
  storeCode: string,
  lines: Array<{ product_name: string }>
): string {
  const q = lines.slice(0, 5).map((l) => l.product_name).join(', ');
  const tmpl = SEARCH_URL_TEMPLATES[storeCode];
  if (tmpl) {
    return tmpl.replace('{q}', encodeURIComponent(q || 'продукты'));
  }
  return STORE_HOME_URLS[storeCode] || `https://${storeCode}.ru/`;
}
