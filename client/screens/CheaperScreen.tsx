import React, { useState } from 'react';
import {
  buildStoreSearchUrl,
  goods,
  qtyLabel,
  rub,
  wholeText,
} from '../../src/shared/format';
import type { StoreBreakdown } from '../../src/types';
import { ChainMark, Icon, MoneyExact, MoneyWhole, ProductPic } from '../components/Icons';
import { MobileTabs } from '../components/MobileTabs';
import { KorzinaBridge } from '../device';
import { buildStorePartMeta, type StorePartMeta } from '../engine';
import type { BoardPlace, ClientBoard, WorkspaceSnapshot } from '../types';

interface CheaperScreenProps {
  ws: WorkspaceSnapshot;
  basketId: number | null;
  board: ClientBoard;
  itemsCount?: number;
  embedded?: boolean;
  applyBonuses?: boolean;
  activeCollecting?: string[];
  onToggleApplyBonuses?: (next: boolean) => void;
  onPickPlace: (index: number) => void;
  onBack?: () => void;
  onOpenSettings?: () => void;
  onRefreshLive?: () => void;
  onConnectStore: (code: string) => void;
  onNotify: (msg: string) => void;
}

function formatOrderAmount(amount: number) {
  return Number.isInteger(amount) ? wholeText(amount) : rub(amount);
}

export const CheaperScreen: React.FC<CheaperScreenProps> = ({
  ws,
  basketId,
  board,
  itemsCount = 0,
  embedded = false,
  applyBonuses = true,
  activeCollecting = [],
  onToggleApplyBonuses,
  onPickPlace,
  onBack,
  onOpenSettings,
  onRefreshLive,
  onConnectStore,
  onNotify,
}) => {
  const [showMore, setShowMore] = useState(false);
  const [openStoreCode, setOpenStoreCode] = useState<string | null>(null);
  const [orderedCodes, setOrderedCodes] = useState<Record<string, boolean>>({});

  const chosen = board.chosenVariant;
  const parts: StorePartMeta[] = chosen
    ? chosen.stores.map((sb) => buildStorePartMeta(ws, sb))
    : [];
  const bestTotal = board.places.length > 0 ? board.places[0].total : null;
  const storesByCode = new Map(ws.stores.map((s) => [s.code, s]));

  const renderMinimumOrder = (part: StoreBreakdown, showStoreName = false) => {
    const minimum = storesByCode.get(part.store_code)?.min_order;
    if (!part.below_min_order || !minimum) return null;
    const shortfall = Math.round((minimum - part.subtotal) * 100) / 100;
    if (shortfall <= 0) return null;
    return (
      <div key={part.store_code} className="note warn spa-order-minimum">
        {showStoreName && <strong>{part.store_name} · </strong>}
        <span>Заказ от {formatOrderAmount(minimum)}</span>
        {' · '}
        <span>добавьте товаров на {formatOrderAmount(shortfall)}</span>
      </div>
    );
  };

  const hasAnyBonuses =
    Object.values(ws.storePerks || {}).some((p) => (p.bonus_balance || 0) > 0) ||
    Object.values(ws.storeQuotes || {}).some((q) => (q.bonus_balance || 0) > 0) ||
    parts.some((p) => p.breakdown.bonus_available > 0 || p.breakdown.bonus_applied > 0);
  const chosenBonusApplied = parts.reduce((acc, p) => acc + (p.breakdown.bonus_applied || 0), 0);
  const chosenBonusAvail = parts.reduce((acc, p) => acc + (p.breakdown.bonus_available || 0), 0);

  const handleOrder = (part: StorePartMeta) => {
    if (KorzinaBridge.available()) {
      const linesPayload = part.breakdown.lines.map((ln) => ({
        product_id: ln.product_id,
        name: ln.product_name,
        qty: ln.qty,
        unit: ln.unit,
      }));
      if (part.needsLogin) {
        KorzinaBridge.login(part.code);
        onNotify(`Открываем вход ${part.where}…`);
        return;
      }
      const accepted = KorzinaBridge.ordered({
        store: part.code,
        basket: basketId,
        lines: linesPayload,
      });
      if (!accepted) {
        KorzinaBridge.openStore(part.code);
      }
      setOrderedCodes((prev) => ({ ...prev, [part.code]: true }));
      onNotify(`Заказ передан в приложение: ${part.name}`);
      return;
    }

    if (part.needsLogin) {
      onConnectStore(part.code);
      return;
    }

    setOrderedCodes((prev) => ({ ...prev, [part.code]: true }));
    if (navigator.clipboard?.writeText) {
      const summary = part.breakdown.lines
        .map((l) => `${l.product_name} — ${qtyLabel(l.qty, l.unit)}`)
        .join('\n');
      void navigator.clipboard.writeText(summary).catch(() => {});
    }
    onNotify(`Список для ${part.name} скопирован (${goods(part.count)}). Переходите к оформлению.`);
  };

  const renderPlaceRow = (p: BoardPlace) => {
    const isBest = p.best;
    const isOn = p.on;
    const stores = board.allVariants[p.n]?.stores ?? [];
    return (
      <li key={p.n} className={`pl${isBest ? ' best' : ''}${isOn ? ' on' : ''}`}>
        <button
          type="button"
          className="pl-in"
          onClick={() => onPickPlace(p.n)}
          aria-pressed={isOn}
        >
          <span className="rk">{p.rank}</span>
          <div className="st">
            <span className="lgs">
              {p.counts.map(([code, cnt]) => (
                <span key={code} className="lg-c">
                  <ChainMark code={code} />
                  {p.counts.length > 1 && <span className="c">{cnt}</span>}
                </span>
              ))}
            </span>
            <div className="who">
              <div className="nm">{p.title}</div>
              <div className={`ok${p.full ? '' : ' lack'}`}>
                <Icon name={p.full ? 'check' : 'alert'} />
                <span>
                  {p.full
                    ? `${p.of} из ${p.of}`
                    : `${p.of - p.lack} из ${p.of}`}
                </span>
              </div>
            </div>
          </div>
          <div className="pr">
            <div className="sum">
              <MoneyWhole value={p.total} big />
            </div>
            <div className="df">
              {p.best ? (
                board.baseline > p.total + 5 ? (
                  <>−<MoneyWhole value={board.baseline - p.total} /></>
                ) : (
                  'выгоднее всех'
                )
              ) : p.diff > 0 ? (
                <>+<MoneyWhole value={p.diff} /></>
              ) : p.diff < 0 ? (
                <>−<MoneyWhole value={Math.abs(p.diff)} /></>
              ) : (
                'так же'
              )}
            </div>
          </div>
        </button>
        {stores.map((part) => renderMinimumOrder(part, stores.length > 1))}
      </li>
    );
  };

  return (
    <div className={`spa-screen${embedded ? ' spa-embedded' : ''}`}>
      <div className="ph-body">
        <header className="top">
          <div className="top-left">
            {!embedded && onBack && (
              <button type="button" className="back" onClick={onBack} aria-label="Назад к корзине">
                <Icon name="back" />
              </button>
            )}
            <h1>Где дешевле</h1>
          </div>
          <div className="top-actions">
            {board.positions > 0 && <span className="cnt">{goods(board.positions)}</span>}
            {onRefreshLive && board.positions > 0 && (
              <button
                type="button"
                className="bt sm"
                onClick={onRefreshLive}
                title="Обновить цены и живые корзины"
              >
                Обновить
              </button>
            )}
          </div>
        </header>

        {board.places.length === 0 ? (
          <div className="card empty">
            <h2>Пока нечего сравнивать</h2>
            <p>Добавьте товары в корзину — здесь сразу появится рейтинг магазинов с учётом доставки, спецпредложений, бонусов и кэшбэка.</p>
          </div>
        ) : (
          <>
            {activeCollecting.length > 0 && (
              <div className="spa-live-pill" role="status">
                <span className="spa-pulse" aria-hidden="true" />
                <span>
                  Собираем корзины в фоне:{' '}
                  {activeCollecting
                    .slice(0, 3)
                    .map((c) => storesByCode.get(c)?.name || c)
                    .join(', ')}
                  {activeCollecting.length > 3 ? ` +${activeCollecting.length - 3}` : ''}
                </span>
              </div>
            )}

            {hasAnyBonuses && onToggleApplyBonuses && (
              <button
                type="button"
                className={`spa-bonus-bar${applyBonuses ? ' on' : ''}`}
                role="switch"
                aria-checked={applyBonuses}
                onClick={() => onToggleApplyBonuses(!applyBonuses)}
              >
                <div className="who">
                  <div className="nm">Списывать бонусы магазинов</div>
                  <div className="sub">
                    {applyBonuses
                      ? chosenBonusApplied > 0
                        ? `Снижают выбранный заказ на ${rub(chosenBonusApplied)}`
                        : 'Бонусы сетей учтены в рейтинге'
                      : chosenBonusAvail > 0
                        ? `Доступно до ${rub(chosenBonusAvail)} бонусами`
                        : 'Сейчас считаем без списания бонусов'}
                  </div>
                </div>
                <span className={`spa-switch${applyBonuses ? ' on' : ''}`} aria-hidden="true">
                  <i />
                </span>
              </button>
            )}

            <ol className="board">{board.places.map(renderPlaceRow)}</ol>

            {board.morePlaces.length > 0 && (
              <div className="more-box">
                <button
                  type="button"
                  className="more-bt"
                  onClick={() => setShowMore((v) => !v)}
                >
                  <span>
                    {showMore
                      ? 'Скрыть остальные варианты'
                      : `Ещё ${board.morePlaces.length} ${board.morePlaces.length === 1 ? 'вариант' : 'варианта'}`}
                  </span>
                  <Icon name="down" className={`i${showMore ? ' rot' : ''}`} />
                </button>
                {showMore && (
                  <ol className="board more-list">
                    {board.morePlaces.map(renderPlaceRow)}
                  </ol>
                )}
              </div>
            )}

            {chosen && parts.length > 0 && (
              <section className="card split">
                {parts.map((part) => {
                  const sb = part.breakdown;
                  const expanded = openStoreCode === part.code || parts.length === 1;
                  return (
                    <div key={part.code} className="sp-s">
                      <button
                        type="button"
                        className="sp-h"
                        onClick={() =>
                          setOpenStoreCode((cur) => (cur === part.code ? null : part.code))
                        }
                      >
                        <ChainMark code={part.code} name={part.name} />
                        <div className="who">
                          <div className="nm">{part.name}</div>
                          <div className="sub">
                            {goods(part.count)}
                            {sb.promo_discount > 0 && (
                              <> · акция −<MoneyWhole value={sb.promo_discount} /></>
                            )}
                            {sb.bonus_applied > 0 && (
                              <> · бонусы −<MoneyWhole value={sb.bonus_applied} /></>
                            )}
                            {sb.card_name && sb.discount > 0 && (
                              <> · кэшбэк −<MoneyWhole value={sb.discount} /></>
                            )}
                          </div>
                        </div>
                        <div className="sum">
                          <MoneyWhole value={sb.total} />
                        </div>
                        <Icon name="down" className={`i chev${expanded ? ' rot' : ''}`} />
                      </button>

                      {renderMinimumOrder(sb)}

                      <div className="thumbs">
                        {part.thumbs.map((t, i) => (
                          <ProductPic key={i} photo={t.photo} title={t.name} />
                        ))}
                        {part.more > 0 && <span className="tm">+{part.more}</span>}
                      </div>

                      {expanded && (
                        <div className="sp-l">
                          {sb.live_verified && (
                            <div className="spa-verified-row">
                              <Icon name="check" />
                              <span>
                                {sb.address_checked
                                  ? `Проверено в живой корзине (${sb.address_checked})`
                                  : 'Цены, наличие и доставка из живой корзины'}
                              </span>
                            </div>
                          )}

                          {sb.lines.map((ln) => (
                            <div key={ln.product_id} className="ln">
                              <span className="n">
                                {ln.product_name}
                                <small> · {qtyLabel(ln.qty, ln.unit)}</small>
                              </span>
                              <span className="p">
                                <MoneyExact value={ln.price} />
                              </span>
                            </div>
                          ))}

                          <div className="ln meta">
                            <span className="n">
                              <Icon name="truck" /> Доставка
                              {sb.live_verified && <small> · по адресу</small>}
                            </span>
                            <span className="p">
                              {sb.delivery > 0 ? <MoneyExact value={sb.delivery} /> : 'Бесплатно'}
                            </span>
                          </div>

                          {sb.service_fee > 0 && (
                            <div className="ln meta">
                              <span className="n">Сервисный сбор</span>
                              <span className="p">
                                <MoneyExact value={sb.service_fee} />
                              </span>
                            </div>
                          )}

                          {sb.promo_discount > 0 && (
                            <div className="ln meta disc">
                              <span className="n">
                                {sb.promo_label || 'Спецпредложение магазина'}
                              </span>
                              <span className="p">{rub(-sb.promo_discount)}</span>
                            </div>
                          )}

                          {sb.bonus_applied > 0 ? (
                            <div className="ln meta disc">
                              <span className="n">Бонусы магазина</span>
                              <span className="p">{rub(-sb.bonus_applied)}</span>
                            </div>
                          ) : sb.bonus_available > 0 && !applyBonuses ? (
                            <div className="ln meta">
                              <span className="n">Бонусы магазина (выкл.)</span>
                              <span className="p">до {rub(sb.bonus_available)}</span>
                            </div>
                          ) : null}

                          {sb.discount > 0 && (
                            <div className="ln meta disc">
                              <span className="n">
                                Кэшбэк{sb.card_name ? ` (${sb.card_name})` : ''}
                              </span>
                              <span className="p">{rub(-sb.discount)}</span>
                            </div>
                          )}

                        </div>
                      )}
                    </div>
                  );
                })}

                {chosen.missing_products.length > 0 && (
                  <div className="note warn">
                    Нет в наличии: {chosen.missing_products.join(', ')}
                  </div>
                )}

                <div className="sp-meta">
                  <span>Пересчитано в {board.when}</span>
                  <span>
                    {ws.address ? ws.address : 'Базовые цены сети'}
                  </span>
                </div>
              </section>
            )}

            {embedded && chosen && parts.length > 0 && (
              <div className="spa-cta-wrap">
                {parts.map((part, idx) => {
                  const done = orderedCodes[part.code];
                  return (
                    <div key={part.code} className="spa-cta-row">
                      <button
                        type="button"
                        className={`cta${idx > 0 ? ' alt' : ''}`}
                        onClick={() => handleOrder(part)}
                      >
                        <span>
                          {done
                            ? `✓ Готово для ${part.name}`
                            : part.needsLogin
                              ? `Подключить ${part.whom} и заказать`
                              : `Заказать ${part.where}`}
                        </span>
                        <span className="r">
                          <MoneyWhole value={part.total} />
                        </span>
                      </button>
                      {!KorzinaBridge.available() && !part.needsLogin && (
                        <a
                          href={buildStoreSearchUrl(part.code, part.breakdown.lines)}
                          target="_blank"
                          rel="noreferrer"
                          className="spa-ext-link"
                          title={`Открыть сайт ${part.name}`}
                        >
                          <Icon name="out" />
                        </a>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>

      {!embedded && (
        <div className="ph-foot">
          {chosen && parts.length > 0 && (
            <div className="spa-cta-wrap">
              {parts.map((part, idx) => {
                const done = orderedCodes[part.code];
                return (
                  <div key={part.code} className="spa-cta-row">
                    <button
                      type="button"
                      className={`cta${idx > 0 ? ' alt' : ''}`}
                      onClick={() => handleOrder(part)}
                    >
                      <span>
                        {done
                          ? `✓ Готово для ${part.name}`
                          : part.needsLogin
                            ? `Подключить ${part.whom} и заказать`
                            : `Заказать ${part.where}`}
                      </span>
                      <span className="r">
                        <MoneyWhole value={part.total} />
                      </span>
                    </button>
                    {!KorzinaBridge.available() && !part.needsLogin && (
                      <a
                        href={buildStoreSearchUrl(part.code, part.breakdown.lines)}
                        target="_blank"
                        rel="noreferrer"
                        className="spa-ext-link"
                        title={`Открыть сайт ${part.name}`}
                      >
                        <Icon name="out" />
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <MobileTabs
            active="cheaper"
            itemsCount={itemsCount}
            bestTotal={bestTotal}
            onSelect={(tab) => {
              if (tab === 'basket' && onBack) onBack();
              else if (tab === 'settings' && onOpenSettings) onOpenSettings();
            }}
          />
        </div>
      )}
    </div>
  );
};
