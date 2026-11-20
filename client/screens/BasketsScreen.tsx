import React, { useState } from 'react';
import { goods, humanDay } from '../../src/shared/format';
import { Icon } from '../components/Icons';
import type { WorkspaceSnapshot } from '../types';

interface BasketsScreenProps {
  ws: WorkspaceSnapshot;
  activeBasketId: number | null;
  onSelectBasket: (id: number) => void;
  onCreateBasket: (name: string) => void;
  onDeleteBasket: (id: number) => void;
  onBack: () => void;
}

export const BasketsScreen: React.FC<BasketsScreenProps> = ({
  ws,
  activeBasketId,
  onSelectBasket,
  onCreateBasket,
  onDeleteBasket,
  onBack,
}) => {
  const [name, setName] = useState('');

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    onCreateBasket(name.trim() || `Корзина от ${humanDay(new Date().toISOString().slice(0, 10))}`);
    setName('');
  };

  return (
    <div className="spa-screen">
      <div className="ph-body">
        <header className="top">
          <div className="top-left">
            <button type="button" className="back" onClick={onBack} aria-label="Назад к корзине">
              <Icon name="back" />
            </button>
            <h1>Мои корзины</h1>
          </div>
        </header>

        <form className="card spa-new-basket" onSubmit={handleCreate}>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Название новой корзины (например, На неделю)"
          />
          <button type="submit" className="bt sm spa-lime-btn">
            <Icon name="plus" />
            <span>Создать</span>
          </button>
        </form>

        <div className="card list-c">
          {ws.baskets.length === 0 ? (
            <div className="spa-search-empty">Пока нет корзин</div>
          ) : (
            ws.baskets.map((b) => {
              const count = (ws.basketItems[String(b.id)] || []).length;
              const isActive = b.id === activeBasketId;
              return (
                <div key={b.id} className={`row spa-basket-row${isActive ? ' active' : ''}`}>
                  <button
                    type="button"
                    className="spa-basket-pick"
                    onClick={() => onSelectBasket(b.id)}
                  >
                    <div className="who">
                      <div className="nm">
                        {b.name}
                        {isActive && <span className="spa-dot-on" aria-label="Активная" />}
                      </div>
                      <div className="sub">
                        {goods(count)} · {humanDay(b.created_at)}
                      </div>
                    </div>
                  </button>
                  {ws.baskets.length > 1 && (
                    <button
                      type="button"
                      className="ibtn"
                      aria-label={`Удалить корзину ${b.name}`}
                      onClick={() => onDeleteBasket(b.id)}
                    >
                      <Icon name="trash" />
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};
