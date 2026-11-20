import React, { useState } from 'react';
import { Icon } from '../components/Icons';

interface LoginScreenProps {
  pinRequired: boolean;
  onLogin: (phone: string, pin: string) => Promise<void>;
  onDemo: () => Promise<void>;
}

export const LoginScreen: React.FC<LoginScreenProps> = ({
  pinRequired,
  onLogin,
  onDemo,
}) => {
  const [phone, setPhone] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await onLogin(phone, pin);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось войти');
    } finally {
      setBusy(false);
    }
  };

  const handleDemo = async () => {
    setError(null);
    setBusy(true);
    try {
      await onDemo();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось открыть демо');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="spa-login-wrap">
      <div className="ph spa-login-card">
        <div className="ph-body">
          <div className="spa-brand-head">
            <span className="spa-brand-badge">
              <Icon name="cart" />
            </span>
            <div>
              <h1>Моя корзина</h1>
              <p className="sub">Где дешевле купить ваш набор продуктов с учётом доставки и кэшбэка</p>
            </div>
          </div>

          <form className="card spa-login-form" onSubmit={handleSubmit}>
            <div className="spa-field">
              <label htmlFor="login-phone">Номер телефона</label>
              <input
                id="login-phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder="+7 999 123-45-67"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                disabled={busy}
                required
              />
              <small>У каждого номера своя корзина, чеки и подключённые магазины. Работает прямо в браузере и офлайн.</small>
            </div>

            {pinRequired && (
              <div className="spa-field">
                <label htmlFor="login-pin">PIN-код</label>
                <input
                  id="login-pin"
                  type="password"
                  inputMode="numeric"
                  placeholder="Введите PIN"
                  value={pin}
                  onChange={(e) => setPin(e.target.value)}
                  disabled={busy}
                />
              </div>
            )}

            {error && <div className="note warn">{error}</div>}

            <button type="submit" className="cta" disabled={busy}>
              <span>{busy ? 'Входим…' : 'Войти'}</span>
            </button>
          </form>

          <div className="spa-login-alt">
            <button
              type="button"
              className="cta alt"
              onClick={handleDemo}
              disabled={busy}
            >
              <span>Посмотреть демо-корзину</span>
            </button>
            <p className="spa-login-hint">
              В демо уже собрана реальная корзина из чека, подключены карты с кэшбэком и видны цены сетей.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
