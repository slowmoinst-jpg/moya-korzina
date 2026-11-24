import React, { useEffect, useRef, useState } from 'react';
import { searchServerCatalog } from '../api';
import { purchasedProducts, rankProducts } from '../../src/shared/searchRank';
import { Icon, MoneyExact } from './Icons';
import { SearchPic } from './SearchPic';
import type { SearchResultItem, WorkspaceSnapshot } from '../types';

interface Props {
  ws: WorkspaceSnapshot;
  code: string;
  name: string;
  quantities: Map<number, number>;
  add: (item: SearchResultItem, store: string) => void;
  close: () => void;
}

export function StoreProductPicker({ ws, code, name, quantities, add, close }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState('');
  const [remote, setRemote] = useState<SearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    let current = true;
    setRemote([]);
    setFailed(false);
    const search = query.trim();
    setLoading(search.length >= 2);
    if (search.length < 2) return;
    const timer = window.setTimeout(async () => {
      try {
        const rows = await searchServerCatalog(search, code);
        if (current) setRemote(rows.filter(row => row.prices[code]?.in_stock));
      } catch { if (current) setFailed(true); }
      finally { if (current) setLoading(false); }
    }, 180);
    return () => { current = false; clearTimeout(timer); };
  }, [code, query]);

  const matches = new Map(remote.map(row => [row.product.id, row]));
  for (const product of ws.products) {
    const prices = ws.prices[product.id] || {};
    const title = product.title || product.name;
    if (!product.active || !prices[code]?.in_stock || matches.has(product.id) ||
        !title.toLowerCase().includes(query.trim().toLowerCase())) continue;
    matches.set(product.id, { product, prices, small: '', chainsCount: Object.keys(prices).length });
  }
  const results = rankProducts([...matches.values()], purchasedProducts(ws.history), code).slice(0,30);
  return <dialog ref={dialog} className="store-picker" onClose={close} aria-label={'Ассортимент · ' + name}>
    <div className="picker-head"><h2>{name}</h2><button className="ibtn" aria-label="Закрыть" onClick={close}>×</button></div>
    <input type="search" aria-label={'Найти товар · ' + name} placeholder="Найти товар в магазине" value={query} onChange={e => setQuery(e.target.value)} />
    <div className="picker-list">{loading && <p role="status">Ищем…</p>}
      {failed && <p role="status">Поиск по сети недоступен. Показаны сохранённые товары.</p>}
      {!loading && !results.length && <p>{query.length < 2 ? 'Введите хотя бы две буквы названия' : 'Товаров не найдено'}</p>}
      {results.map(row => <div className="picker-row" key={row.product.id}>
        <SearchPic item={row} /><div>
          {row.prices[code].price > 0 && <div className="search-price"><MoneyExact value={row.prices[code].price} /></div>}
          <strong>{row.product.title || row.prices[code].raw_name || row.product.name}</strong>
        </div>
        <button className="search-add" disabled={quantities.has(row.product.id)}
          aria-label={(quantities.has(row.product.id) ? 'В корзине · ' : 'Добавить ') + row.product.name}
          onClick={() => { add(row, code); close(); }}>
          {quantities.has(row.product.id) ? '✓' : <Icon name="plus" />}</button>
      </div>)}</div>
  </dialog>;
}
