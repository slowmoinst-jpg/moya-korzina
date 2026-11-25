import React from 'react';
import { Icon } from './Icons';

interface MobileTabsProps {
  active: 'basket' | 'orders' | 'settings';
  itemsCount: number;
  onSelect: (tab: 'basket' | 'orders' | 'settings') => void;
}

export const MobileTabs: React.FC<MobileTabsProps> = ({
  active,
  itemsCount,
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
        className={`tab${active === 'orders' ? ' on' : ''}`}
        aria-current={active === 'orders' ? 'page' : undefined}
        onClick={() => onSelect('orders')}
      >
        <span className="tab-ic">
          <Icon name="receipt" />
        </span>
        <span>Заказы</span>
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
