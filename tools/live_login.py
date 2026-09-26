"""Вход в личный кабинет магазина через живое окно браузера (нативно, без задержек).

Использование:
    python tools/live_login.py magnit
    python tools/live_login.py dixy --phone 79990000001
"""
from __future__ import annotations

import argparse
import sys
import time

from app import users
from app.shopbrowser import live


def main():
    parser = argparse.ArgumentParser(description="Авторизация в магазине через живое окно Chrome")
    parser.add_argument("store", help="Код магазина: magnit, lenta, pyaterochka, samokat, dixy, metro, vkusvill, perekrestok")
    parser.add_argument("--phone", default=None, help="Номер телефона рабочего места (по умолчанию demo)")
    args = parser.parse_args()

    phone = args.phone or users.DEMO
    chain = args.store.lower().strip()

    print(f"🚀 Запускаю живое окно для «{chain}» (рабочее место: {phone})...")
    res = live.start_live_login(chain, phone)
    if not res.get("ok"):
        print(f"❌ Ошибка запуска: {res.get('error')}")
        sys.exit(1)

    print("✅ Окно браузера открыто на вашем экране.")
    print("👉 Войдите в свой аккаунт (телефон, капча, код из СМС).")
    print("⏳ Ожидаю завершения авторизации (нажмите Ctrl+C для отмены)...")

    try:
        while True:
            time.sleep(1.5)
            st = live.get_live_status(chain, phone)
            status = st.get("status")

            if status == "saved":
                print("\n🎉 Авторизация успешно завершена!")
                if st.get("account"):
                    print(f"👤 Аккаунт: {st.get('account')}")
                if st.get("points") is not None:
                    print(f"💳 Баллы: {st.get('points')}")
                print(f"🍪 Сохранено кук: {st.get('cookies_count')}")
                print("Сессия сохранена в базе. Окно закрыто.")
                break
            elif status == "closed":
                print("\n⚠️ Окно браузера было закрыто до завершения авторизации.")
                break
            elif status == "error":
                print(f"\n❌ Ошибка: {st.get('error')}")
                break
            else:
                sys.stdout.write(".")
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nПрерывание... закрываю окно.")
        live.stop_live_login(chain, phone)


if __name__ == "__main__":
    main()
