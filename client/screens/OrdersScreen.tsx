import React, { useState } from 'react';
import { receiptLines, otherReceiptPage, type ReceiptLine, type ReceiptSummary } from '../api';
import { goods, num, rub } from '../../src/shared/format';
import { ChainMark, Icon, MoneyWhole, ProductPic } from '../components/Icons';
import type { OrderEntry, WorkspaceSnapshot } from '../types';

interface OrdersScreenProps {
  groupedOrders: Array<[string, OrderEntry[]]>;
  otherReceipts?: WorkspaceSnapshot['otherReceipts'];
  repeatedKey: string | null;
  onRepeatOrder: (order: OrderEntry) => void;
  onBack: () => void;
  onOpenSettings?: () => void;
}

export const OrdersScreen: React.FC<OrdersScreenProps> = ({
  groupedOrders,
  otherReceipts,
  repeatedKey,
  onRepeatOrder,
  onBack,
  onOpenSettings,
}) => {
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({});

  const [older, setOlder] = useState<ReceiptSummary[]>([]);
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState(false);
  const [contents, setContents] = useState<Record<string, ReceiptLine[] | 'error'>>({});
  const archived = [...new Map([...(otherReceipts?.recent || []), ...older].map(r => [r.key, r])).values()];
  const loadMore = async () => {
    if (moreBusy) return;
    setMoreBusy(true); setMoreError(false);
    try { const rows = await otherReceiptPage(archived.length); setOlder(prev => [...prev, ...rows]); }
    catch { setMoreError(true); }
    finally { setMoreBusy(false); }
  };
  const openReceipt = async (key: string) => {
    try { const lines = await receiptLines(key); setContents(prev => ({ ...prev, [key]: lines })); }
    catch { setContents(prev => ({ ...prev, [key]: 'error' })); }
  };

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

        {groupedOrders.length === 0 && !otherReceipts?.count ? (
          <div className="card empty">
            <h2>Заказов пока нет</h2>
            <p>
              Здесь появятся покупки из ваших чеков ФНС.
            </p>
            {onOpenSettings && (
              <button type="button" className="bt sm lime" onClick={onOpenSettings}>
                <Icon name="receipt" />
                <span>Перейти в настройки</span>
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

                      {ord.lines.some((line) => line.repeatable) && <div className="of">
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
                      </div>}
                    </article>
                  );
                })}
              </div>
            </section>
          ))
        )}
        {!!otherReceipts?.count && (
          <section className="sec">
            <div className="sh"><h2>Другие магазины</h2><span className="cnt">{otherReceipts.count}</span></div>
            <div className="card list-c">
              {archived.map(receipt => {
                const lines = contents[receipt.key];
                return (
                  <details key={receipt.key} className="other-receipts"
                    onToggle={e => { if (e.currentTarget.open) void openReceipt(receipt.key); }}>
                    <summary className="row">
                      <span className="who"><span className="nm">{receipt.store}</span>
                        <small className="sub"> · {receipt.date?.split('-').reverse().join('.')}</small></span>
                      <MoneyWhole value={receipt.total} /><Icon name="next" className="i chev" />
                    </summary>
                    {Array.isArray(lines) && lines.length ? <ul>{lines.map((line, i) => <li key={i}>
                      <span>{line.name} · {num(line.quantity)} × {rub(line.price)}</span><b>{rub(line.total)}</b>
                    </li>)}</ul> : <div className="sub spa-pad" role="status">{lines === 'error' ? 'Не удалось открыть чек.'
                      : lines ? 'Состав чека пока недоступен.' : 'Открываем чек…'}</div>}
                  </details>
                );
              })}
            </div>
            {archived.length < otherReceipts.count && <button className="bt sm" disabled={moreBusy}
              onClick={loadMore}>{moreBusy ? 'Загружаем…' : 'Показать ещё'}</button>}
            {moreError && <div className="sub" role="status">Не удалось загрузить следующие чеки.</div>}
          </section>
        )}
      </div>
    </div>
  );
};
