import React from 'react';
import { Icon } from './Icons';

interface MobileTabsProps {
  active: 'basket' | 'cheaper' | 'settings';
  itemsCount: number;
  bestTotal: number | null;
  onSelect: (tab: 'basket' | 'cheaper' | 'settings') => void;
}

export const MobileTabs: React.FC<MobileTabsProps> = ({
  active,
  itemsCount,
  bestTotal,
  onSelect,
}) => {
  return (
    <nav className="tabs" aria-label="Разделы">
      <button
        type="button"
        className={`tab${active === 'basket' ? ' on' : ''}`}
        aria-current={active === 'basket' ? 'page' : undefined}
        onClick={() => onSelect('basket')}
      >
        <span className="tab-ic">
          <Icon name="cart" />
          {itemsCount > 0 && <span className="tab-badge">{itemsCount}</span>}
        </span>
        <span>Корзина</span>
      </button>

      <button
        type="button"
        className={`tab${active === 'cheaper' ? ' on' : ''}`}
        aria-current={active === 'cheaper' ? 'page' : undefined}
        onClick={() => onSelect('cheaper')}
      >
        <span className="tab-ic">
          <Icon name="receipt" />
        </span>
        <span>
          {bestTotal !== null ? `${Math.round(bestTotal)} ₽` : 'Где дешевле'}
        </span>
      </button>

      <button
        type="button"
        className={`tab${active === 'settings' ? ' on' : ''}`}
        aria-current={active === 'settings' ? 'page' : undefined}
        onClick={() => onSelect('settings')}
      >
        <span className="tab-ic">
          <Icon name="gear" />
        </span>
        <span>Настройки</span>
      </button>
    </nav>
  );
};
