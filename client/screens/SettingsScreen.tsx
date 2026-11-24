import { loadBasketLayout, saveBasketLayout, type BasketLayout } from '../storage';
import { getFnsStatus } from '../api';
import React, { useEffect, useState } from 'react';
import {
  cheques,
  PHONE_CHAINS,
  rub,
} from '../../src/shared/format';
import { AddressPicker } from '../components/AddressPicker';
import { ChainMark, Icon, MoneyWhole } from '../components/Icons';
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
  onRefreshReceipts: () => Promise<void>;
  onOpenPrices: () => void;
  onLogout: () => Promise<void>;
  initialAuthStore?: string | null;
  onClearInitialAuthStore?: () => void;
}

export const SettingsScreen: React.FC<SettingsScreenProps> = ({
  ws,
  storeTotals,
  activeCollecting = [],
  onToggleStore,
  onUpdateStorePerk,
  onSaveAddress,
  onToggleOffer,
  onAddOffer,
  onDeleteOffer,
  onRefreshReceipts,
  onOpenPrices,
  onLogout,
  initialAuthStore,
  onClearInitialAuthStore,
}) => {
  const [layout, setLayout] = useState(loadBasketLayout);
  const updateLayout = (next: BasketLayout) => { setLayout(next); saveBasketLayout(next); };
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

  const [needsLogin, setNeedsLogin] = useState(false);
  const [deviceReceipt, setDeviceReceipt] = useState<ReturnType<typeof KorzinaBridge.receiptStatus>>({});
  const [serverState, setServerState] = useState('');
  const [loginError, setLoginError] = useState('');
  useEffect(() => {
    if (window.location.pathname === '/phone/receipts')
      requestAnimationFrame(() => document.getElementById('my-receipts')?.scrollIntoView());
    if (ws.isDemo) return;
    let active = true;
    let revision: string | undefined, refreshedAt = 0, refreshing = false;
    const check = async () => {
      if (document.hidden || !active) return;
      const device = KorzinaBridge.receiptStatus(ws.phone);
      setDeviceReceipt(device);
      if (revision === undefined) { revision = device.updated; return; }
      if (!refreshing && revision !== device.updated && (device.phase === 'done' || Date.now() - refreshedAt >= 60000)) {
        refreshing = true;
        refreshedAt = Date.now();
        try { await onRefreshReceipts(); revision = device.updated; } catch { /* Retry on the next refresh. */ }
        finally { refreshing = false; }
      }
    };
    const serverStatus = async () => {
      if (!ws.fnsConnected || document.hidden) return;
      try {
        const state = await getFnsStatus();
        if (!active) return;
        setNeedsLogin(Boolean(state.needsConnection));
        setServerState(state.running ? 'Проверяем чеки…' : state.failed ? state.message : '');
        if (state.lastSync?.replace('T', ' ').slice(0, 16) !== ws.fnsSyncedAt && state.lastSync)
          await onRefreshReceipts();
      } catch { /* Keep the last known connection state while offline. */ }
    };
    void check(); void serverStatus();
    const timer = window.setInterval(check, 3000);
    const serverTimer = window.setInterval(serverStatus, 60000);
    const resume = () => { void check(); void serverStatus(); };
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    return () => {
      active = false;
      clearInterval(timer); clearInterval(serverTimer);
      window.removeEventListener('focus', resume);
      document.removeEventListener('visibilitychange', resume);
    };
  }, [ws.phone, ws.fnsConnected, ws.fnsSyncedAt, ws.isDemo, onRefreshReceipts]);
  const receiptsConnected = deviceReceipt.connected || (ws.fnsConnected && !needsLogin);
  const checked = deviceReceipt.connected ? deviceReceipt.checked : ws.fnsSyncedAt;
  const checkedDate = checked ? new Date(typeof checked === 'string' ? checked.replace(' ', 'T') : checked) : null;
  const lastCheck = checkedDate && !Number.isNaN(checkedDate.getTime())
    ? 'Последняя проверка ' + checkedDate.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
    : 'Ожидаем фоновую проверку.';
  const position = deviceReceipt.position || 0;
  const totalReceipts = deviceReceipt.total || 0;
  const average = deviceReceipt.millisPerReceipt || 0;
  const remainingMinutes = totalReceipts > position && average > 0
    ? Math.ceil((totalReceipts - position) * average / 60000) : null;
  const scanning = deviceReceipt.phase === 'running';
  const showPosition = deviceReceipt.connected && position > 0 && deviceReceipt.phase !== 'done';
  const progressTime = deviceReceipt.advanced ? new Date(deviceReceipt.advanced)
    .toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const receiptProgress = deviceReceipt.connected ? ({
    running: position ? 'Загружаем чеки.' : 'Открываем список чеков…', denied: 'ФНС отклонила загрузку. Проверим позже.',
    partial: 'Часть чеков ещё не загружена. Повторим автоматически.',
    resuming: 'Открываем список на месте остановки…',
    account: 'Проверяем подключение к вашему профилю. Повторим автоматически.',
    upload: 'Не удалось сохранить чек на сервере. Повторим автоматически.',
    cabinet: 'Кабинет ФНС не ответил. Повторим автоматически.',
    retry: 'Проверка не завершилась. Повторим автоматически.', waiting: 'Ожидаем фоновую проверку.'
  } as Record<string, string>)[deviceReceipt.phase || ''] || lastCheck : serverState || lastCheck;
  const signInToReceipts = () => {
    if (!KorzinaBridge.openFns())
      setLoginError('Для входа откройте настройки в приложении на Android.');
  };

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

  const authStore = authStoreCode ? storeByCode.get(authStoreCode) : null;
  const authStoreLoginUrl = authStoreCode ? STORE_LOGIN_URLS[authStoreCode] || 'https://vkusvill.ru/' : '';

  return (
    <div className="spa-screen">
      <div className="ph-body">
        <header className="top">
          <h1>Настройки</h1>
        </header>

        <section className="sec basket-layout">
          <div className="sh"><h2>Вид корзины</h2></div>
          <div className="card">
            <div className="layout-setting" role="radiogroup" aria-labelledby="basket-panel-title">
              <h3 id="basket-panel-title">Панель «Где дешевле»</h3>
              <div className="layout-options">
                {(['top', 'bottom'] as const).map(value => (
                  <label key={value}>
                    <input type="radio" name="basket-panel" checked={layout.panel === value}
                      onChange={() => updateLayout({ ...layout, panel: value })} />
                    <span>{value === 'top' ? 'Над списком' : 'Под списком'}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="layout-setting" role="radiogroup" aria-labelledby="basket-add-title">
              <h3 id="basket-add-title">Строка «Добавить товар»</h3>
              <div className="layout-options">
                {(['first', 'last'] as const).map(value => (
                  <label key={value}>
                    <input type="radio" name="basket-add" checked={layout.add === value}
                      onChange={() => updateLayout({ ...layout, add: value })} />
                    <span>{value === 'first' ? 'В начале группы' : 'В конце группы'}</span>
                  </label>
                ))}
              </div>
            </div>
          </div>
        </section>

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
          <div className="sh" id="my-receipts">
            <h2>Мои чеки</h2>
            <span className="cnt">{cheques(Math.max(ws.receipts.length + (ws.otherReceipts?.count || 0), deviceReceipt.connected ? deviceReceipt.storedCount || 0 : 0))}</span>
          </div>

          <div className="card spa-fns-card">
            <div className="spa-fns-top">
              <div className="who">
                <div className="nm"><Icon name="receipt" /> Чеки из ФНС</div>
                <div className="sub" role="status">
                  {ws.isDemo ? 'Пример истории покупок'
                    : receiptsConnected ? receiptProgress
                    : !isAndroidApp ? 'Войдите в приложении на Android, чтобы получать чеки.'
                    : !KorzinaBridge.supportsReceiptLogin() ? 'Для автоматической загрузки чеков нужна версия 1.75 или новее.'
                    : 'После входа чеки появятся в заказах автоматически.'}
                  {showPosition && <div>Чек {position.toLocaleString('ru-RU')}{totalReceipts > 0
                    ? ` из ${totalReceipts.toLocaleString('ru-RU')} в открытом списке` : ' · общее число пока неизвестно'}.</div>}
                  {showPosition && scanning && !totalReceipts && average > 0 &&
                    <div>Скорость · {cheques(Math.max(1, Math.round(60000 / average)))} в минуту.</div>}
                  {showPosition && scanning && remainingMinutes !== null &&
                    <div>{remainingMinutes === 1 ? 'До конца списка меньше минуты.' : `До конца списка около ${remainingMinutes} мин при текущей скорости.`}</div>}
                  {showPosition && !scanning && progressTime && <div>Последнее продвижение · {progressTime}</div>}
                </div>
              </div>
              {!ws.isDemo && !receiptsConnected && KorzinaBridge.supportsReceiptLogin() && (
                <button type="button" className="bt sm lime" onClick={signInToReceipts}>Войти</button>
              )}

            </div>
            {loginError && <div className="sub" role="alert">{loginError}</div>}
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
    </div>
  );
};
