import React, { useEffect, useState } from 'react';
import {
  cheques,
  goods,
  PHONE_CHAINS,
  rub,
} from '../../src/shared/format';
import { AddressPicker } from '../components/AddressPicker';
import { ChainMark, Icon, MoneyWhole } from '../components/Icons';
import { MobileTabs } from '../components/MobileTabs';
import { KorzinaBridge } from '../device';
import type { WorkspaceSnapshot } from '../types';

import type { StorePerk } from '../../src/types';

const STORE_LOGIN_URLS: Record<string, string> = {
  vkusvill: 'https://vkusvill.ru/personal/',
  magnit: 'https://magnit.ru/',
  pyaterochka: 'https://5ka.ru/',
  lenta: 'https://lenta.com/',
  dixy: 'https://dixy.ru/',
  samokat: 'https://samokat.ru/',
  metro: 'https://online.metro-cc.ru/',
  perekrestok: 'https://www.perekrestok.ru/',
  fixprice: 'https://fix-price.com/',
  vprok: 'https://www.vprok.ru/',
  monetka: 'https://monetka.ru/',
};

interface SettingsScreenProps {
  ws: WorkspaceSnapshot;
  storeTotals: Record<string, number>;
  itemsCount: number;
  bestTotal: number | null;
  isDesktop: boolean;
  activeCollecting?: string[];
  onToggleStore: (code: string, connect: boolean, mode?: 'session' | 'manual') => void;
  onUpdateStorePerk: (code: string, perk: StorePerk) => void;
  onSaveAddress: (address: string | null, point: [number, number] | null) => void;
  onToggleOffer: (offerId: number) => void;
  onAddOffer: (input: {
    bank: string;
    cardName: string;
    storeId: number;
    percent: number;
    capRub: number;
  }) => void;
  onDeleteOffer: (offerId: number) => void;
  onSyncFns: (phone: string, keys?: string) => Promise<void>;
  onDisconnectFns: () => Promise<void>;
  onPasteReceipt: (storeCode: string, text: string) => Promise<void>;
  onOpenBasket: () => void;
  onOpenCheaper: () => void;
  onOpenPrices: () => void;
  onLogout: () => Promise<void>;
  initialAuthStore?: string | null;
  onClearInitialAuthStore?: () => void;
}

export const SettingsScreen: React.FC<SettingsScreenProps> = ({
  ws,
  storeTotals,
  itemsCount,
  bestTotal,
  isDesktop,
  activeCollecting = [],
  onToggleStore,
  onUpdateStorePerk,
  onSaveAddress,
  onToggleOffer,
  onAddOffer,
  onDeleteOffer,
  onSyncFns,
  onDisconnectFns,
  onPasteReceipt,
  onOpenBasket,
  onOpenCheaper,
  onOpenPrices,
  onLogout,
  initialAuthStore,
  onClearInitialAuthStore,
}) => {
  const [authStoreCode, setAuthStoreCode] = useState<string | null>(null);
  const [storeSessionInput, setStoreSessionInput] = useState('');
  const [perkLabel, setPerkLabel] = useState('');
  const [perkPct, setPerkPct] = useState('');
  const [perkRub, setPerkRub] = useState('');
  const [perkBonus, setPerkBonus] = useState('');
  const [perkBonusMaxPct, setPerkBonusMaxPct] = useState('30');

  const isAndroidApp = KorzinaBridge.available();

  const populatePerkForm = (code: string) => {
    const existing = ws.storePerks?.[code];
    const quote = ws.storeQuotes?.[code];
    setPerkLabel(existing?.promo_label || quote?.promo_label || '');
    setPerkPct(existing?.promo_percent ? String(existing.promo_percent) : '');
    setPerkRub(existing?.promo_rub ? String(existing.promo_rub) : '');
    const bal = existing?.bonus_balance || quote?.bonus_balance || 0;
    setPerkBonus(bal > 0 ? String(bal) : '');
    setPerkBonusMaxPct(String(existing?.bonus_max_pct ?? 30));
  };

  useEffect(() => {
    if (initialAuthStore) {
      if (isAndroidApp && !ws.connections[initialAuthStore]?.connected) {
        KorzinaBridge.login(initialAuthStore);
      } else {
        setAuthStoreCode(initialAuthStore);
        setStoreSessionInput('');
        populatePerkForm(initialAuthStore);
      }
      onClearInitialAuthStore?.();
    }
  }, [initialAuthStore, onClearInitialAuthStore, isAndroidApp]);

  const [showAddOffer, setShowAddOffer] = useState(false);
  const [offerBank, setOfferBank] = useState('Т-Банк');
  const [offerCard, setOfferCard] = useState('Black');
  const [offerStoreId, setOfferStoreId] = useState<number>(ws.stores[0]?.id || 1);
  const [offerPct, setOfferPct] = useState('5');
  const [offerCap, setOfferCap] = useState('3000');

  const [showReceiptForm, setShowReceiptForm] = useState(false);
  const [receiptStore, setReceiptStore] = useState('pyaterochka');
  const [receiptText, setReceiptText] = useState('');
  const [receiptBusy, setReceiptBusy] = useState(false);
  const [receiptError, setReceiptError] = useState<string | null>(null);

  const [showFnsModal, setShowFnsModal] = useState(false);
  const [fnsPhoneInput, setFnsPhoneInput] = useState(ws.fnsPhone || ws.phoneLabel || '');
  const [fnsKeysInput, setFnsKeysInput] = useState('');
  const [fnsBusy, setFnsBusy] = useState(false);
  const [fnsError, setFnsError] = useState<string | null>(null);

  const storeByCode = new Map(ws.stores.map((s) => [s.code, s]));
  const storeById = new Map(ws.stores.map((s) => [s.id, s]));
  const cardById = new Map(ws.cards.map((c) => [c.id, c]));

  const connectedCodes: string[] = [];
  const waitingCodes: string[] = [];
  for (const code of PHONE_CHAINS) {
    if (ws.connections[code]?.connected) connectedCodes.push(code);
    else waitingCodes.push(code);
  }

  const totalChains = PHONE_CHAINS.length;
  const connectedCount = connectedCodes.length;
  const progressPct = Math.round((connectedCount / totalChains) * 100);

  const openStoreModal = (code: string) => {
    setAuthStoreCode(code);
    setStoreSessionInput('');
    populatePerkForm(code);
  };

  const handleStoreTap = (code: string, isConnected: boolean) => {
    if (!isConnected && isAndroidApp) {
      // Native Android APK: open real store login directly inside WebView — no cookie modal!
      KorzinaBridge.login(code);
      return;
    }
    openStoreModal(code);
  };

  const closeStoreModal = () => {
    setAuthStoreCode(null);
    setStoreSessionInput('');
  };

  const handleSaveStoreModal = (connectNow: boolean) => {
    if (!authStoreCode) return;
    onUpdateStorePerk(authStoreCode, {
      store_code: authStoreCode,
      promo_label: perkLabel.trim() || null,
      promo_percent: Number(perkPct.replace(',', '.')) || 0,
      promo_rub: Number(perkRub.replace(',', '.')) || 0,
      bonus_balance: Number(perkBonus.replace(',', '.')) || 0,
      bonus_max_pct: Number(perkBonusMaxPct.replace(',', '.')) || 30,
    });
    if (connectNow && !ws.connections[authStoreCode]?.connected) {
      const hasSession = isAndroidApp || storeSessionInput.trim().length > 0;
      onToggleStore(authStoreCode, true, hasSession ? 'session' : 'manual');
    }
    closeStoreModal();
  };

  const handleAddOfferSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const percent = Math.max(0.5, Math.min(50, Number(offerPct.replace(',', '.')) || 5));
    const capRub = Math.max(0, Number(offerCap.replace(',', '.')) || 3000);
    onAddOffer({
      bank: offerBank.trim() || 'Банк',
      cardName: offerCard.trim() || 'Карта',
      storeId: offerStoreId,
      percent,
      capRub,
    });
    setShowAddOffer(false);
  };

  const handlePasteSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setReceiptError(null);
    setReceiptBusy(true);
    try {
      await onPasteReceipt(receiptStore, receiptText);
      setReceiptText('');
      setShowReceiptForm(false);
    } catch (err) {
      setReceiptError(err instanceof Error ? err.message : 'Не удалось разобрать чек');
    } finally {
      setReceiptBusy(false);
    }
  };

  const openFnsAuthModal = () => {
    setFnsPhoneInput(ws.fnsPhone || ws.phoneLabel || '');
    setFnsKeysInput('');
    setFnsError(null);
    setShowFnsModal(true);
  };

  const handleFnsSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFnsError(null);
    const trimmedKeys = fnsKeysInput.trim();
    if (!ws.fnsConnected && !isAndroidApp && trimmedKeys.length < 8) {
      setFnsError('Вставьте ключ кабинета ФНС (JSON lkdr-keys или refreshToken из lkdr.nalog.ru), либо загрузите чек текстом ниже.');
      return;
    }
    if (trimmedKeys.startsWith('{')) {
      try {
        JSON.parse(trimmedKeys);
      } catch {
        setFnsError('Некорректный JSON ключа. Скопируйте объект полностью от { до }.');
        return;
      }
    }
    setFnsBusy(true);
    try {
      await onSyncFns(fnsPhoneInput.trim() || ws.phoneLabel, trimmedKeys || undefined);
      setShowFnsModal(false);
      setFnsKeysInput('');
    } catch (err) {
      setFnsError(err instanceof Error ? err.message : 'Не удалось подключить кабинет ФНС');
    } finally {
      setFnsBusy(false);
    }
  };

  const handleFnsQuickSync = async () => {
    setFnsBusy(true);
    try {
      await onSyncFns(ws.fnsPhone || ws.phoneLabel);
    } catch (err) {
      setFnsError(err instanceof Error ? err.message : 'Не удалось обновить чеки');
      setShowFnsModal(true);
    } finally {
      setFnsBusy(false);
    }
  };

  const handleFnsDisconnect = async () => {
    setFnsBusy(true);
    setFnsError(null);
    try {
      await onDisconnectFns();
      setShowFnsModal(false);
    } catch (err) {
      setFnsError(err instanceof Error ? err.message : 'Не удалось отключить кабинет ФНС');
    } finally {
      setFnsBusy(false);
    }
  };

  const totalSpent = ws.receipts.reduce((acc, r) => acc + r.total, 0);
  const authStore = authStoreCode ? storeByCode.get(authStoreCode) : null;
  const authStoreLoginUrl = authStoreCode ? STORE_LOGIN_URLS[authStoreCode] || 'https://vkusvill.ru/' : '';

  return (
    <div className="spa-screen">
      <div className="ph-body">
        <header className="top">
          <h1>Настройки</h1>
        </header>

        {ws.isDemo && (
          <div className="note spa-demo-note">
            Вы в демо-режиме: все правки работают мгновенно прямо в браузере, но не отправляются на сервер. Войдите по своему номеру, чтобы синхронизировать корзину между устройствами.
          </div>
        )}

        <section className="sec">
          <div className="sh">
            <h2>Магазины</h2>
            <span className="cnt">{connectedCount} из {totalChains}</span>
          </div>

          <div className="card sum">
            <div className="sum-h">
              <div>
                <div className="n">{connectedCount} из {totalChains}</div>
                <div className="sub">
                  {isAndroidApp
                    ? 'Нажмите на сеть, чтобы войти прямо в приложении — сессия сохранится на телефоне, и корзины будут собираться в фоне'
                    : 'Все 11 сетей участвуют в сравнении. Нажмите на магазин, чтобы задать спецпредложения, бонусы или статус входа'}
                </div>
              </div>
            </div>
            <div className="bar" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100}>
              <i style={{ width: `${progressPct}%` }} />
            </div>
          </div>

          {connectedCodes.length > 0 && (
            <div className="card list-c spa-mt">
              {connectedCodes.map((code) => {
                const st = storeByCode.get(code);
                const conn = ws.connections[code];
                const perk = ws.storePerks?.[code];
                const quote = ws.storeQuotes?.[code];
                const sum = storeTotals[code];
                const isCollecting = activeCollecting.includes(code) || quote?.status === 'collecting';
                const bonusVal = Math.max(perk?.bonus_balance || 0, quote?.bonus_balance || 0);
                const hasPromo =
                  (perk?.promo_percent || 0) > 0 ||
                  (perk?.promo_rub || 0) > 0 ||
                  (quote?.promo_discount || 0) > 0;

                return (
                  <button
                    key={code}
                    type="button"
                    className="row spa-store-row spa-row-btn"
                    onClick={() => openStoreModal(code)}
                  >
                    <ChainMark code={code} name={st?.name} />
                    <div className="who">
                      <div className="nm">{st?.name || code}</div>
                      <div className="sub">
                        {isCollecting ? (
                          'Проверяем цены и наличие в фоне…'
                        ) : quote?.status === 'ready' ? (
                          <>
                            Живая корзина: {quote.available_count}/{quote.total_count}
                            {quote.delivery_fee !== null && (
                              <> · доставка {quote.delivery_fee > 0 ? rub(quote.delivery_fee) : '0 ₽'}</>
                            )}
                          </>
                        ) : conn?.mode === 'session' ? (
                          sum && sum > 0 ? (
                            <>Сессия активна · корзина <MoneyWhole value={sum} /></>
                          ) : (
                            'Сессия активна · сбор в фоне'
                          )
                        ) : sum && sum > 0 ? (
                          <>Корзина на <MoneyWhole value={sum} /></>
                        ) : (
                          'Участвует в расчёте'
                        )}
                      </div>
                      {(bonusVal > 0 || hasPromo) && (
                        <div className="spa-perk-badges">
                          {hasPromo && (
                            <span className="spa-perk-chip">
                              {perk?.promo_label || quote?.promo_label || 'Скидка'}
                              {perk?.promo_percent ? ` −${perk.promo_percent}%` : ''}
                              {perk?.promo_rub ? ` −${perk.promo_rub} ₽` : ''}
                            </span>
                          )}
                          {bonusVal > 0 && (
                            <span className="spa-perk-chip bonus">
                              Бонусы {Math.round(bonusVal)} ₽
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <Icon name="next" className="i chev" />
                  </button>
                );
              })}
            </div>
          )}

          {waitingCodes.length > 0 && (
            <div className="wg spa-mt">
              {waitingCodes.map((code) => {
                const st = storeByCode.get(code);
                const perk = ws.storePerks?.[code];
                const bonusVal = perk?.bonus_balance || 0;
                return (
                  <button
                    key={code}
                    type="button"
                    className="wt"
                    onClick={() => handleStoreTap(code, false)}
                  >
                    <ChainMark code={code} name={st?.name} />
                    <div className="who">
                      <div className="nm">{st?.name || code}</div>
                      <div className="sub">
                        {isAndroidApp
                          ? 'Войти в приложении'
                          : bonusVal > 0
                            ? `Бонусы ${Math.round(bonusVal)} ₽`
                            : 'Войти / бонусы'}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <section className="sec">
          <div className="sh">
            <h2>Карты и кэшбэк</h2>
            <button
              type="button"
              className="bt sm"
              onClick={() => setShowAddOffer((v) => !v)}
            >
              <Icon name={showAddOffer ? 'x' : 'plus'} />
              <span>{showAddOffer ? 'Закрыть' : 'Добавить'}</span>
            </button>
          </div>

          {showAddOffer && (
            <form className="card spa-offer-form" onSubmit={handleAddOfferSubmit}>
              <div className="spa-form-grid">
                <label>
                  <span>Банк</span>
                  <input
                    type="text"
                    value={offerBank}
                    onChange={(e) => setOfferBank(e.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>Карта</span>
                  <input
                    type="text"
                    value={offerCard}
                    onChange={(e) => setOfferCard(e.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>Магазин</span>
                  <select
                    value={offerStoreId}
                    onChange={(e) => setOfferStoreId(Number(e.target.value))}
                  >
                    {ws.stores.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Кэшбэк, %</span>
                  <input
                    type="number"
                    step="0.5"
                    min="0.5"
                    max="50"
                    value={offerPct}
                    onChange={(e) => setOfferPct(e.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>Лимит, ₽/мес</span>
                  <input
                    type="number"
                    step="100"
                    min="0"
                    value={offerCap}
                    onChange={(e) => setOfferCap(e.target.value)}
                  />
                </label>
              </div>
              <button type="submit" className="bt sm spa-lime-btn">
                Сохранить предложение
              </button>
            </form>
          )}

          <div className="card list-c">
            {ws.offers.length === 0 ? (
              <div className="spa-search-empty">
                Добавьте повышенный кэшбэк по вашим картам — алгоритм сразу учтёт его в рейтинге магазинов.
              </div>
            ) : (
              ws.offers.map((o) => {
                const st = storeById.get(o.store_id);
                const card = cardById.get(o.card_id);
                return (
                  <div key={o.id} className="row spa-offer-row">
                    {st && <ChainMark code={st.code} name={st.name} />}
                    <div className="who">
                      <div className="nm">
                        {st?.name || 'Сеть'} · {o.percent}%
                      </div>
                      <div className="sub">
                        {card ? `${card.bank} ${card.name}` : 'Карта'}
                        {o.cap_rub > 0 ? ` · до ${o.cap_rub} ₽` : ''}
                      </div>
                    </div>
                    <button
                      type="button"
                      className={`bt sm${o.activated ? ' spa-lime-btn' : ''}`}
                      onClick={() => onToggleOffer(o.id)}
                    >
                      {o.activated ? 'Активен' : 'Выкл'}
                    </button>
                    <button
                      type="button"
                      className="ibtn"
                      aria-label="Удалить предложение"
                      onClick={() => onDeleteOffer(o.id)}
                    >
                      <Icon name="trash" />
                    </button>
                  </div>
                );
              })
            )}
          </div>
        </section>

        <section className="sec">
          <div className="sh">
            <h2>Мои чеки</h2>
            <span className="cnt">{cheques(ws.receipts.length)}</span>
          </div>

          <div className="card spa-fns-card">
            <div className="spa-fns-top">
              <div className="who">
                <div className="nm">
                  <Icon name="receipt" /> Чеки из ФНС
                </div>
                <div className="sub">
                  {ws.fnsConnected
                    ? `Подключено (${ws.fnsPhone || ws.phoneLabel}) · ${cheques(ws.receipts.length)} · ${goods(ws.history.length)} на ${rub(totalSpent)}`
                    : 'Подтянем покупки из «Мои чеки онлайн» ФНС, чтобы знать привычные товары и реальные прошлые цены'}
                </div>
              </div>
              {ws.fnsConnected ? (
                <button
                  type="button"
                  className="bt sm lime"
                  onClick={handleFnsQuickSync}
                  disabled={fnsBusy}
                >
                  {fnsBusy ? 'Обновляем…' : 'Обновить'}
                </button>
              ) : (
                <button
                  type="button"
                  className="bt sm lime"
                  onClick={() => {
                    if (isAndroidApp) {
                      KorzinaBridge.openFns();
                    } else {
                      openFnsAuthModal();
                    }
                  }}
                  disabled={fnsBusy}
                >
                  Подключить
                </button>
              )}
            </div>

            <div className="spa-fns-actions">
              <button
                type="button"
                className="bt sm"
                onClick={() => setShowReceiptForm((v) => !v)}
              >
                <Icon name={showReceiptForm ? 'x' : 'plus'} />
                <span>{showReceiptForm ? 'Скрыть форму' : 'Вставить чек текстом'}</span>
              </button>
              {ws.fnsConnected && (
                <button
                  type="button"
                  className="bt sm"
                  onClick={openFnsAuthModal}
                >
                  Ключ / отключить
                </button>
              )}
            </div>

            {showReceiptForm && (
              <form className="spa-receipt-form" onSubmit={handlePasteSubmit}>
                <label className="spa-field">
                  <span>Магазин</span>
                  <select
                    value={receiptStore}
                    onChange={(e) => setReceiptStore(e.target.value)}
                  >
                    {ws.stores.map((s) => (
                      <option key={s.code} value={s.code}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="spa-field">
                  <span>Строки чека (Название; кол-во; цена)</span>
                  <textarea
                    rows={4}
                    placeholder={'Молоко Простоквашино 2.5%; 2; 89.90\nХлеб Дарницкий; 1; 54'}
                    value={receiptText}
                    onChange={(e) => setReceiptText(e.target.value)}
                    required
                  />
                </label>
                {receiptError && <div className="note warn">{receiptError}</div>}
                <button type="submit" className="bt sm spa-lime-btn" disabled={receiptBusy}>
                  {receiptBusy ? 'Сохраняем…' : 'Добавить в историю'}
                </button>
              </form>
            )}
          </div>
        </section>

        <section className="sec">
          <div className="sh">
            <h2>Адрес доставки</h2>
          </div>
          <div className="card spa-pad">
            <AddressPicker
              address={ws.address}
              point={ws.point}
              onSave={onSaveAddress}
            />
          </div>
        </section>

        <section className="sec">
          <div className="sh">
            <h2>Аккаунт</h2>
          </div>
          <div className="card list-c">
            <div className="row">
              <Icon name="phone" />
              <div className="who">
                <div className="nm">{ws.phoneLabel}</div>
                <div className="sub">
                  {ws.isDemo ? 'Демо-сессия' : 'Данные хранятся на устройстве и синхронизируются с сервером'}
                </div>
              </div>
              <button type="button" className="bt sm" onClick={onLogout}>
                Выйти
              </button>
            </div>
            <button type="button" className="row spa-link-row spa-row-btn" onClick={onOpenPrices}>
              <Icon name="list" />
              <div className="who">
                <div className="nm">Таблица цен и условия сетей</div>
                <div className="sub">Сравнение цен по всем 11 магазинам и пороги доставки</div>
              </div>
              <Icon name="next" />
            </button>
          </div>
        </section>
      </div>

      {!isDesktop && (
        <div className="ph-foot">
          <MobileTabs
            active="settings"
            itemsCount={itemsCount}
            bestTotal={bestTotal}
            onSelect={(tab) => {
              if (tab === 'basket') onOpenBasket();
              else if (tab === 'cheaper') onOpenCheaper();
            }}
          />
        </div>
      )}

      {/* Store Settings, Native Login & Perks Modal */}
      {authStoreCode && (
        <div
          className="spa-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`Настройки сети ${authStore?.name || authStoreCode}`}
          onClick={closeStoreModal}
        >
          <div className="card spa-modal" onClick={(e) => e.stopPropagation()}>
            <div className="spa-modal-head">
              <div className="spa-modal-title">
                <ChainMark code={authStoreCode} name={authStore?.name} />
                <div>
                  <h3>{authStore?.name || authStoreCode}</h3>
                  <small>
                    {ws.connections[authStoreCode]?.connected
                      ? 'Аккаунт подключён · сбор корзин и цен'
                      : 'Вход, спецпредложения и бонусы сети'}
                  </small>
                </div>
              </div>
              <button
                type="button"
                className="ibtn"
                aria-label="Закрыть"
                onClick={closeStoreModal}
              >
                <Icon name="x" />
              </button>
            </div>

            <div className="spa-modal-body">
              {isAndroidApp ? (
                <div className="spa-store-auth-box">
                  <p className="spa-modal-desc">
                    Сессия хранится на вашем телефоне. После входа приложение само собирает корзину в фоне при каждом изменении, проверяет наличие по адресу, доставку, скидки и бонусы.
                  </p>
                  <button
                    type="button"
                    className="cta"
                    onClick={() => {
                      KorzinaBridge.login(authStoreCode);
                      closeStoreModal();
                    }}
                  >
                    <span>
                      {ws.connections[authStoreCode]?.connected
                        ? `Войти заново в ${authStore?.name || authStoreCode}`
                        : `Войти в ${authStore?.name || authStoreCode}`}
                    </span>
                    <Icon name="out" />
                  </button>
                </div>
              ) : (
                <div className="spa-store-auth-box">
                  <p className="spa-modal-desc">
                    На телефоне (в Android-приложении) вход в {authStore?.name || authStoreCode} проходит напрямую в один клик, а сессия хранится в защищённом хранилище для фоновой сборки корзин. В браузерной версии вы можете сразу настроить спецпредложения и бонусы сети ниже.
                  </p>
                  <a
                    href={authStoreLoginUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="bt spa-full-btn"
                  >
                    <span>Открыть сайт {authStore?.name || authStoreCode}</span>
                    <Icon name="out" />
                  </a>
                </div>
              )}

              <div className="spa-store-perks">
                <div className="spa-perks-title">Спецпредложения и бонусы сети</div>
                <label className="spa-field">
                  <span>Акция или промокод магазина</span>
                  <input
                    type="text"
                    placeholder="Например: Любимый продукт или промокод −15%"
                    value={perkLabel}
                    onChange={(e) => setPerkLabel(e.target.value)}
                  />
                </label>
                <div className="spa-form-grid">
                  <label>
                    <span>Скидка, %</span>
                    <input
                      type="number"
                      min="0"
                      max="90"
                      step="1"
                      placeholder="0"
                      value={perkPct}
                      onChange={(e) => setPerkPct(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Скидка, ₽</span>
                    <input
                      type="number"
                      min="0"
                      step="10"
                      placeholder="0"
                      value={perkRub}
                      onChange={(e) => setPerkRub(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Бонусы сети, ₽</span>
                    <input
                      type="number"
                      min="0"
                      step="10"
                      placeholder="0"
                      value={perkBonus}
                      onChange={(e) => setPerkBonus(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Списание бонусов, %</span>
                    <input
                      type="number"
                      min="1"
                      max="100"
                      step="5"
                      value={perkBonusMaxPct}
                      onChange={(e) => setPerkBonusMaxPct(e.target.value)}
                    />
                  </label>
                </div>
              </div>

              {!isAndroidApp && (
                <details className="spa-details">
                  <summary>Дополнительно: передать токен сессии вручную</summary>
                  <label className="spa-field spa-mt">
                    <textarea
                      rows={2}
                      placeholder="Необязательно — для отладки на компьютере"
                      value={storeSessionInput}
                      onChange={(e) => setStoreSessionInput(e.target.value)}
                    />
                  </label>
                </details>
              )}

              <div className="spa-modal-actions">
                {ws.connections[authStoreCode]?.connected ? (
                  <button
                    type="button"
                    className="bt danger"
                    onClick={() => {
                      onToggleStore(authStoreCode, false);
                      closeStoreModal();
                    }}
                  >
                    Отключить
                  </button>
                ) : (
                  <button type="button" className="bt" onClick={closeStoreModal}>
                    Отмена
                  </button>
                )}
                <button
                  type="button"
                  className="bt lime"
                  onClick={() => handleSaveStoreModal(!ws.connections[authStoreCode]?.connected)}
                >
                  {ws.connections[authStoreCode]?.connected
                    ? 'Сохранить'
                    : 'Сохранить и включить'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* FNS Receipts Authorization Modal */}
      {showFnsModal && (
        <div
          className="spa-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Подключение чеков ФНС"
          onClick={() => setShowFnsModal(false)}
        >
          <div className="card spa-modal" onClick={(e) => e.stopPropagation()}>
            <div className="spa-modal-head">
              <div className="spa-modal-title">
                <span className="spa-brand-dot">
                  <Icon name="receipt" />
                </span>
                <div>
                  <h3>Чеки из ФНС</h3>
                  <small>Кабинет «Мои чеки онлайн» (lkdr.nalog.ru)</small>
                </div>
              </div>
              <button
                type="button"
                className="ibtn"
                aria-label="Закрыть"
                onClick={() => setShowFnsModal(false)}
              >
                <Icon name="x" />
              </button>
            </div>

            <div className="spa-modal-body">
              {ws.isDemo ? (
                <div className="note warn">
                  В демо-режиме личный кабинет ФНС не подключается — выйдите из демо и войдите по своему номеру телефона.
                </div>
              ) : (
                <form className="spa-modal-form" onSubmit={handleFnsSubmit}>
                  {isAndroidApp ? (
                    <>
                      <p className="spa-modal-desc">
                        Откройте кабинет ФНС во встроенном окне и войдите по номеру телефона и СМС — приложение само подхватит ключи и загрузит чеки.
                      </p>
                      <button
                        type="button"
                        className="cta"
                        onClick={() => KorzinaBridge.openFns()}
                      >
                        <span>Открыть кабинет ФНС (lkdr.nalog.ru)</span>
                        <Icon name="out" />
                      </button>
                    </>
                  ) : (
                    <>
                      <p className="spa-modal-desc">
                        Для загрузки реальных чеков в веб-версии откройте официальный кабинет ФНС и вставьте ключ сессии (<code>lkdr-keys</code> JSON или <code>refreshToken</code>). В Android-приложении вход проходит напрямую по СМС.
                      </p>
                      <a
                        href="https://lkdr.nalog.ru/"
                        target="_blank"
                        rel="noreferrer"
                        className="bt spa-full-btn"
                      >
                        <span>Открыть «Мои чеки онлайн» ФНС</span>
                        <Icon name="out" />
                      </a>
                    </>
                  )}

                  <label className="spa-field">
                    <span>Телефон в кабинете ФНС</span>
                    <input
                      type="tel"
                      value={fnsPhoneInput}
                      onChange={(e) => setFnsPhoneInput(e.target.value)}
                      placeholder="+7 900 000-00-00"
                    />
                  </label>

                  <label className="spa-field">
                    <span>Ключ кабинета ФНС (JSON или refreshToken)</span>
                    <textarea
                      rows={3}
                      value={fnsKeysInput}
                      onChange={(e) => setFnsKeysInput(e.target.value)}
                      placeholder='{"source": "lkdr-keys", "token": "…"}'
                    />
                  </label>

                  {fnsError && <div className="note warn">{fnsError}</div>}

                  <div className="spa-modal-actions">
                    {ws.fnsConnected ? (
                      <button
                        type="button"
                        className="bt danger"
                        disabled={fnsBusy}
                        onClick={handleFnsDisconnect}
                      >
                        Отключить ФНС
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="bt"
                        onClick={() => {
                          setShowFnsModal(false);
                          setShowReceiptForm(true);
                        }}
                      >
                        Вставить чек текстом
                      </button>
                    )}
                    <button type="submit" className="bt lime" disabled={fnsBusy}>
                      {fnsBusy
                        ? 'Проверяем…'
                        : ws.fnsConnected
                          ? 'Обновить'
                          : 'Подключить ФНС'}
                    </button>
                  </div>
                </form>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
