import type { SearchResultItem } from '../types';
import { ProductPic } from './Icons';

export function SearchPic({ item }: { item: SearchResultItem }) {
  return <span className="search-pic"><ProductPic photo={item.product.photo} /></span>;
}
