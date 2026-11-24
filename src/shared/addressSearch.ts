// Прямой поиск для автономного веб-клиента и локального Node-сервера.
const aliases: Record<string, string> = {
  'спб': 'санкт-петербург', 'мск': 'москва', 'ул': 'улица', 'пр-кт': 'проспект',
  'пр-т': 'проспект', 'просп': 'проспект', 'пер': 'переулок', 'наб': 'набережная',
  'б-р': 'бульвар', 'пл': 'площадь', 'ш': 'шоссе',
};
const roadTypes = ['улица', 'проспект', 'переулок', 'набережная', 'бульвар', 'шоссе', 'площадь', 'проезд'];
const houseTail = /(?:^|[\s,])(?:д(?:ом)?\.?\s*)?(\d+[а-яa-z]?(?:\/\d+[а-я]?)?(?:\s*(?:корпус|корп|к|строение|стр|с|литера|лит)\.?\s*[\dа-я]+)*)$/u;
const words = (s: string): string[] => s.toLowerCase().replaceAll('ё', 'е').match(/[а-яa-z0-9]+/gu) || [];
const houseKey = (s: string) => s.toLowerCase().replace(/[\s.]/gu, '').replace(/корпус|корп/gu, 'к')
  .replace(/строение|стр/gu, 'с').replace(/литера/gu, 'лит');

interface Feature {
  properties?: Record<string, string>;
  geometry?: { coordinates?: number[] };
}

export async function searchPhoton(query: string) {
  const text = query.trim().toLowerCase().replace(/\s+/gu, ' ')
    .replace(/[а-яё]+(?:-[а-яё]+)*\.?/gu, word => aliases[word.replace(/\.$/u, '')] || word);
  if (text.length < 3 || text.length > 250) return [];
  const tail = houseTail.exec(text);
  const house = houseKey(tail?.[1] || '');
  const names = (tail ? text.slice(0, tail.index) : text).trim().replace(/^г(?:ород)?\.?\s+/u, '').replace(/,$/u, '');
  const needed = words(names).filter(word => !['город', 'г', 'россия'].includes(word));
  if (!needed.length) return [];
  const split = names.includes(',') ? names.indexOf(',') :
    (names.match(new RegExp('\\s+(?=(?:' + roadTypes.join('|') + ')(?:\\s|$))', 'u'))?.index ?? names.indexOf(' '));
  const attempts: [string, Record<string, string>][] = [];
  if (split > 0 && !roadTypes.includes(names.slice(0, split))) {
    attempts.push(['structured', { city: names.slice(0, split), street: names.slice(split + 1).trim(), housenumber: house }]);
  }
  attempts.push(['api/', { q: text }]);
  let answered = false;
  for (const [path, fields] of attempts) {
    try {
      const params = new URLSearchParams({ ...fields, countrycode: 'ru', limit: '24' });
      const res = await fetch(`https://photon.komoot.io/${path}?${params}`, { signal: AbortSignal.timeout(4500) });
      if (!res.ok) continue;
      const { features } = await res.json() as { features: Feature[] };
      const seen = new Set<string>();
      const hits = features.flatMap(({ properties: p, geometry }) => {
        const coords = geometry?.coordinates;
        if (p?.countrycode?.toUpperCase() !== 'RU' || coords?.length !== 2 || !coords.every(Number.isFinite)) return [];
        const city = p.city || p.town || p.village || p.state || '';
        const street = p.street || p.name || '';
        const number = houseKey(p.housenumber || '');
        const tokens = words(city + ' ' + street);
        if (!needed.every((w, i) => tokens.includes(w) || (!house && i === needed.length - 1 && tokens.some(t => t.startsWith(w))))) return [];
        if (house && number !== house && !(/^\d+$/u.test(house) && new RegExp('^' + house + '(?:к|с|лит)[а-я0-9]+$', 'u').test(number))) return [];
        const label = [...new Set([city, street, p.housenumber].filter(Boolean))].join(', ');
        const full = [...new Set([p.state, p.district].filter(v => v && v !== city))].join(', ') || label;
        const key = label + '|' + full;
        if (seen.has(key)) return [];
        seen.add(key);
        const extra = new Set(tokens.filter(t => !needed.includes(t) && !roadTypes.includes(t))).size;
        return [{ extra, exact: house === number, item: { label, full, lat: coords[1], lon: coords[0] } }];
      });
      answered = true;
      if (hits.length) return hits.sort((a, b) => a.extra - b.extra || Number(b.exact) - Number(a.exact)).slice(0, 6).map(h => h.item);
    } catch {
      // При сбое первого запроса пробуем следующую форму адреса.
    }
  }
  if (!answered) throw new Error('Поиск адресов временно недоступен');
  return [];
}
