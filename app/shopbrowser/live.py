"""Живое окно входа в магазин: Chromium на экране пользователя.

В отличие от покадровой трансляции скриншотов, этот режим открывает настоящее
окно браузера прямо на мониторе пользователя (headless=False):
  - Страница рисуется с родной скоростью GPU (60+ FPS);
  - Интерактивные проверки (Yandex SmartCaptcha, Cloudflare) проходятся за секунды;
  - СМС и телефон вводятся нативной клавиатурой без задержек.

Как только пользователь вошёл:
  - Автоматический сторож замечает появление куки входа (или текста профиля);
  - Забирает cookies и localStorage (storage_state);
  - Сохраняет сессию в личную базу рабочего места;
  - Закрывает окно и сообщает интерфейсу об успехе.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app import store_accounts, users
from app.shopbrowser import driver, signals, store as shopstore

log = logging.getLogger(__name__)

# Хранилище активных живых сеансов: (chain, phone) -> dict
_SESSIONS: dict[tuple[str, str], dict[str, Any]] = {}
_LOCK = threading.Lock()


def is_live_available() -> bool:
    """Доступно ли живое окно (есть ли графическое окружение)."""
    import os
    # На Windows доступно всегда; на Linux — если есть DISPLAY
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY"))


def get_live_status(chain: str, phone: str) -> dict[str, Any]:
    """Текущий статус живого окна для пары сеть-телефон."""
    key = (chain.lower(), phone)
    with _LOCK:
        sess = _SESSIONS.get(key)
        if not sess:
            return {"ok": True, "status": "idle"}
        return {
            "ok": True,
            "status": sess.get("status", "idle"),
            "account": sess.get("account"),
            "points": sess.get("points"),
            "cookies_count": sess.get("cookies_count", 0),
            "error": sess.get("error"),
            "note": sess.get("note", ""),
        }


def start_live_login(chain: str, phone: str) -> dict[str, Any]:
    """Запустить живое окно браузера для авторизации."""
    chain = chain.lower()
    if chain not in driver.LOGIN_URL:
        return {"ok": False, "error": f"Сеть «{chain}» не поддерживается"}

    key = (chain, phone)
    with _LOCK:
        existing = _SESSIONS.get(key)
        if existing and existing.get("status") == "running":
            return {"ok": True, "status": "running", "already": True}

        sess_data: dict[str, Any] = {
            "chain": chain,
            "phone": phone,
            "status": "starting",
            "browser": None,
            "context": None,
            "page": None,
            "pw": None,
            "account": None,
            "points": None,
            "cookies_count": 0,
            "error": None,
            "note": "Открываю живое окно браузера...",
        }
        _SESSIONS[key] = sess_data

    thread = threading.Thread(
        target=_live_thread,
        args=(sess_data,),
        name=f"live-browser-{chain}",
        daemon=True,
    )
    thread.start()
    return {"ok": True, "status": "starting"}


def save_live_login(chain: str, phone: str) -> dict[str, Any]:
    """Принудительно сохранить вход из открытого живого окна."""
    key = (chain.lower(), phone)
    with _LOCK:
        sess = _SESSIONS.get(key)
        if not sess or sess.get("status") != "running":
            return {"ok": False, "error": "Живое окно сейчас не открыто"}

    return _capture_and_save(sess)


def stop_live_login(chain: str, phone: str) -> dict[str, Any]:
    """Закрыть живое окно браузера."""
    key = (chain.lower(), phone)
    with _LOCK:
        sess = _SESSIONS.pop(key, None)
        if not sess:
            return {"ok": True, "status": "closed"}

    sess["status"] = "closed"
    _close_resources(sess)
    return {"ok": True, "status": "closed"}


def _close_resources(sess: dict[str, Any]) -> None:
    """Безопасно закрыть окно и Playwright."""
    try:
        browser = sess.get("browser")
        if browser:
            browser.close()
    except Exception:
        pass
    try:
        pw = sess.get("pw")
        if pw:
            pw.stop()
    except Exception:
        pass


def _capture_and_save(sess: dict[str, Any]) -> dict[str, Any]:
    """Забрать куки из контекста и сохранить в базе."""
    chain = sess["chain"]
    phone = sess["phone"]
    context = sess.get("context")
    page = sess.get("page")

    if not context:
        return {"ok": False, "error": "Контекст браузера недоступен"}

    try:
        state = context.storage_state()
        cookies = state.get("cookies", [])
        if not cookies:
            return {"ok": False, "error": "В браузере нет кук — вход не завершён"}

        page_text = ""
        try:
            if page and not page.is_closed():
                page_text = page.inner_text("body", timeout=500)
        except Exception:
            pass

        account = signals.account(chain, page_text) if page_text else None
        points = signals.points(chain, page_text) if page_text else None

        users.open_workspace(phone)
        try:
            shopstore.save(chain, state, account=account, points=points)
            store_accounts.mark_connected(chain, account=account, points=points)
        finally:
            users.deactivate()

        sess["status"] = "saved"
        sess["account"] = account
        sess["points"] = points
        sess["cookies_count"] = len(cookies)
        sess["note"] = f"Вход успешно сохранён ({len(cookies)} кук)!"
        log.info("Живой вход в %s успешно сохранён для %s", chain, phone)

        # Закрываем браузер
        _close_resources(sess)
        return {"ok": True, "status": "saved", "account": account, "points": points}
    except Exception as exc:
        log.exception("Ошибка при сохранении живого входа %s: %s", chain, exc)
        return {"ok": False, "error": str(exc)}


def _live_thread(sess: dict[str, Any]) -> None:
    """Поток, управляющий живым окном Chromium."""
    from playwright.sync_api import sync_playwright

    chain = sess["chain"]
    phone = sess["phone"]
    target_url = driver.LOGIN_URL.get(chain) or f"https://{chain}.ru/"

    # Подгружаем сохранённый вход, если был
    users.open_workspace(phone)
    try:
        saved_state = shopstore.load(chain)
    finally:
        users.deactivate()

    try:
        pw = sync_playwright().start()
        sess["pw"] = pw

        launch_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        browser = None
        import os
        if os.name == "nt":
            try:
                # На Windows пробуем запустить установленный Google Chrome
                browser = pw.chromium.launch(
                    headless=False,
                    channel="chrome",
                    args=launch_args,
                )
            except Exception:
                browser = None

        if browser is None:
            browser = pw.chromium.launch(
                headless=False,
                args=launch_args,
            )
        sess["browser"] = browser

        context = browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport=None,  # Полноразмерное окно рабочего стола
            storage_state=saved_state or None,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        sess["context"] = context

        page = context.new_page()
        sess["page"] = page
        sess["status"] = "running"
        sess["note"] = f"Окно {chain} открыто. Войдите в свой аккаунт..."

        log.info("Открыто живое окно браузера для %s (%s)", chain, phone)
        try:
            page.goto(target_url, wait_until="commit", timeout=25000)
        except Exception as exc:
            log.warning("Предупреждение при переходе на %s: %s (продолжаем)", target_url, exc)

        # Цикл отслеживания входа
        while sess.get("status") == "running":
            time.sleep(1.5)

            if page.is_closed():
                log.info("Окно %s было закрыто пользователем", chain)
                # Проверим, не успел ли войти перед закрытием
                try:
                    cookies = context.cookies()
                    if signals.logged_in_by_cookies(chain, cookies) is True:
                        _capture_and_save(sess)
                        break
                except Exception:
                    pass
                sess["status"] = "closed"
                sess["note"] = "Окно было закрыто"
                break

            try:
                cookies = context.cookies()
                page_text = ""
                try:
                    page_text = page.inner_text("body", timeout=500)
                except Exception:
                    pass

                # Проверяем признаки входа
                logged_by_cookie = signals.logged_in_by_cookies(chain, cookies)
                logged_by_text = signals.logged_in(chain, page_text) if page_text else None

                if logged_by_cookie is True or logged_by_text is True:
                    log.info("Обнаружен вход в %s! Сохраняем сессию...", chain)
                    _capture_and_save(sess)
                    break
            except Exception as exc:
                if "Target page, context or browser has been closed" in str(exc) or page.is_closed():
                    sess["status"] = "closed"
                    sess["note"] = "Окно закрыто"
                    break
                log.warning("Ошибка проверки живого окна: %s", exc)

    except Exception as exc:
        log.exception("Ошибка в потоке живого браузера для %s: %s", chain, exc)
        sess["status"] = "error"
        sess["error"] = str(exc)
        sess["note"] = f"Ошибка запуска браузера: {exc}"
    finally:
        _close_resources(sess)
