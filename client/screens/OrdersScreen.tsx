import React, { useState } from 'react';
import { goods, num, rub } from '../../src/shared/format';
import { ChainMark, Icon, MoneyWhole, ProductPic } from '../components/Icons';
import type { OrderEntry } from '../types';

interface OrdersScreenProps {
  groupedOrders: Array<[string, OrderEntry[]]>;
  repeatedKey: string | null;
  onRepeatOrder: (order: OrderEntry) => void;
  onBack: () => void;
  onOpenSettings?: () => void;
}

export const OrdersScreen: React.FC<OrdersScreenProps> = ({
  groupedOrders,
  repeatedKey,
  onRepeatOrder,
  onBack,
  onOpenSettings,
}) => {
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({});

  const toggle = (key: string) => {
    setExpandedKeys((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="spa-screen">
      <div className="ph-body">
        <header className="top">
          <div className="top-left">
            <button type="button" className="back" onClick={onBack} aria-label="Назад к корзине">
              <Icon name="back" />
            </button>
            <h1>История заказов</h1>
          </div>
        </header>

        {groupedOrders.length === 0 ? (
          <div className="card empty">
            <h2>Заказов пока нет</h2>
            <p>
              Подключите «Мои чеки» ФНС или вставьте текст чека в Настройках — история покупок и привычные товары появятся здесь.
            </p>
            {onOpenSettings && (
              <button type="button" className="bt sm lime" onClick={onOpenSettings}>
                <Icon name="receipt" />
                <span>Подключить чеки в Настройках</span>
              </button>
            )}
          </div>
        ) : (
          groupedOrders.map(([month, orders]) => (
            <section key={month} className="sec">
              <div className="sh">
                <h2>{month}</h2>
              </div>
              <div className="ords">
                {orders.map((ord) => {
                  const isExpanded = Boolean(expandedKeys[ord.key]);
                  const isRepeated = repeatedKey === ord.key;
                  return (
                    <article key={ord.key} className="card ord">
                      <div className="oh">
                        <ChainMark code={ord.store_code} name={ord.store_name} />
                        <div className="who">
                          <div className="nm">{ord.store_name}</div>
                          <div className="sub">
                            {ord.day} · {goods(ord.lines.length)} · {ord.source}
                          </div>
                        </div>
                        <div className="sum">
                          <MoneyWhole value={ord.total} />
                        </div>
                      </div>

                      <button
                        type="button"
                        className="thumbs-btn"
                        onClick={() => toggle(ord.key)}
                        aria-expanded={isExpanded}
                      >
                        <div className="thumbs">
                          {ord.lines.slice(0, 5).map((ln, i) => (
                            <ProductPic key={i} photo={ln.photo} title={ln.name} />
                          ))}
                          {ord.lines.length > 5 && (
                            <span className="tm">+{ord.lines.length - 5}</span>
                          )}
                        </div>
                        <Icon name="down" className={`i chev${isExpanded ? ' rot' : ''}`} />
                      </button>

                      {isExpanded && (
                        <div className="sp-l">
                          {ord.lines.map((ln, idx) => (
                            <div key={idx} className="ln">
                              <span className="n">
                                {ln.name}
                                {ln.qty !== 1 && <small> × {num(ln.qty)}</small>}
                              </span>
                              <span className="p">{rub(ln.total)}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      <div className="of">
                        <button
                          type="button"
                          className={`bt sm${isRepeated ? ' spa-lime-btn' : ''}`}
                          onClick={() => onRepeatOrder(ord)}
                        >
                          {isRepeated ? (
                            <>
                              <Icon name="check" />
                              <span>В корзине</span>
                            </>
                          ) : (
                            <span>Повторить заказ</span>
                          )}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          ))
        )}
      </div>
    </div>
  );
};
