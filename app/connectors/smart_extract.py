"""Запасной разбор страницы языковой моделью — на случай, когда вёрстка сменилась.

Раздел 9 спецификации предупреждает: разметка магазина может смениться в любой
момент. Сейчас на этот случай у нас одна реакция — молча взять цену из
data/fallback_prices.csv, то есть показать человеку не ту цифру. Это худший
исход из возможных: приложение уверенно врёт.

Здесь появляется промежуточная ступень. Если страницу мы получили, а разобрать
не смогли — спрашиваем модель: «вот HTML, найди цену». ScrapeGraphAI умеет
принимать готовый HTML вместо адреса (source, не начинающийся с http, он считает
локальным содержимым), поэтому ходить за страницей второй раз не нужно и вся наша
возня с куками, кодом магазина и способом получения остаётся в силе.

ЧЕГО ЭТА ШТУКА НЕ ДЕЛАЕТ. Она не открывает закрытые сайты. Капчу не решает,
геоблок не обходит, страницу не достаёт. Её работа начинается там, где страница
уже лежит у нас в руках, и заканчивается на «вот число».

ПОЧЕМУ ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНА. Во-первых, библиотека тянет за собой два десятка
зависимостей вместе с браузером — в requirements.txt такому не место, ставится
отдельно:

    pip install scrapegraphai

Во-вторых, вызов модели стоит денег и секунд, а цен мы спрашиваем по одной на
товар на магазин. Держать на этом весь расчёт — расточительно и медленно. Это
страховка, а не основной путь.

В-третьих, и это главное: когда запасной разбор срабатывает, в журнал уходит
предупреждение с куском разметки. Значит регулярное выражение в коннекторе
пора чинить. Модель затыкает дыру на сегодня, а чинит её человек.

Настройка в config.yaml:

    connectors:
      smart_extract:
        enabled: true
        model: openrouter/deepseek/deepseek-v4-flash
        base_url:                     # для ollama: http://localhost:11434
        max_chars: 20000              # сколько разметки отдаём модели

Поставщик — первое слово до косой черты: openrouter, openai, anthropic, ollama.
OpenRouter говорит на языке OpenAI, поэтому библиотеке он так и представляется,
а меняются только адрес и ключ; всё, что идёт после «openrouter/», — название
модели в его собственном перечне. Выбран deepseek-v4-flash: 0,04 $ за миллион
входных токенов, то есть страница обходится примерно в две сотых цента.

Ключ берётся из окружения: OPENROUTER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
и так далее — в коде и в конфиге ему не место, иначе он уедет в git.
"""
from __future__ import annotations

import logging
import os
import re

from app import config

log = logging.getLogger(__name__)

DEFAULT_MODEL = "openrouter/deepseek/deepseek-v4-flash"
DEFAULT_MAX_CHARS = 20000
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
PROMPT = ("Со страницы товара интернет-магазина достань текущую цену за единицу — ту, "
          "которую покупатель заплатит сегодня. Если на странице есть и старая цена, и "
          "цена по акции, нужна та, по которой продают сейчас. Верни число в рублях, "
          "единицу измерения (шт или кг) и признак наличия. Если цены на странице нет, "
          "верни price = null.")

# ключ окружения по названию поставщика в модели («openai/gpt-4o-mini» -> OPENAI_API_KEY)
_ENV_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    # Проверено с боевого сервера 19.09.2026: openrouter.ai, api.openai.com и
    # api.anthropic.com отвечают ему 403 по адресу, а api.deepseek.com и
    # api.mistral.ai — 401, то есть «нет ключа», и связь с ними есть. Поэтому
    # эти двое здесь не для полноты списка: это единственные, до кого сервер
    # сегодня дотягивается. Библиотека знает deepseek своим поставщиком, так что
    # «deepseek/deepseek-chat» работает без всякой подмены адреса.
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
}
_SCRIPTS = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_SPACE = re.compile(r"\s{2,}")


def _cfg(key: str, default=None):
    return config.get(f"connectors.smart_extract.{key}", default)


def enabled() -> bool:
    return bool(_cfg("enabled", False))


def installed() -> bool:
    try:
        import scrapegraphai  # noqa: F401
    except Exception:  # noqa: BLE001  — важно только «есть или нет»
        return False
    return True


def available() -> tuple[bool, str]:
    """Можно ли звать модель и, если нет, почему. Второе нужно интерфейсу и журналу."""
    if not enabled():
        return False, "выключено в config.yaml (connectors.smart_extract.enabled)"
    if not installed():
        return False, "библиотека не установлена: pip install scrapegraphai"
    model = str(_cfg("model", DEFAULT_MODEL) or DEFAULT_MODEL)
    provider = model.split("/", 1)[0]
    env_key = _ENV_KEYS.get(provider)
    if env_key and not os.environ.get(env_key):
        return False, f"нет ключа в переменной окружения {env_key}"
    return True, ""


def trim(page: str, max_chars: int | None = None) -> str:
    """Убирает скрипты и стили и оставляет кусок вокруг цены.

    Страницы магазинов весят сотни килобайт, из которых девять десятых — разметка
    аналитики. Отдавать это модели и дорого, и вредно: нужное тонет.
    """
    limit = int(max_chars or _cfg("max_chars", DEFAULT_MAX_CHARS) or DEFAULT_MAX_CHARS)
    clean = _SPACE.sub(" ", _SCRIPTS.sub(" ", page or ""))
    if len(clean) <= limit:
        return clean
    anchor = max(clean.lower().find("price"), clean.find("₽"), clean.find("руб"))
    if anchor < 0:
        return clean[:limit]
    start = max(0, anchor - limit // 2)
    return clean[start:start + limit]


def _llm_config() -> dict:
    model = str(_cfg("model", DEFAULT_MODEL) or DEFAULT_MODEL)
    provider, _, rest = model.partition("/")
    base_url = _cfg("base_url")
    llm: dict = {"model": model}
    if provider == "openrouter":
        # Для библиотеки это обычный OpenAI, просто по другому адресу и с другим
        # ключом: «openrouter/deepseek/deepseek-v4-flash» -> «openai/deepseek/
        # deepseek-v4-flash», а название после первой косой черты OpenRouter
        # разбирает сам.
        llm["model"] = f"openai/{rest}"
        base_url = base_url or OPENROUTER_BASE
        # РАССУЖДЕНИЕ ВЫКЛЮЧАЕМ, и без этого канал молчит. deepseek-v4-flash —
        # модель рассуждающая: на вопрос «найди цену» она тратит сотни токенов
        # на размышление и упирается в потолок ответа РАНЬШЕ, чем скажет хоть
        # слово. Замер 19.09.2026: max_tokens=400 -> reasoning_tokens=400,
        # content пустой, finish_reason=length. С reasoning.enabled=false тот же
        # вопрос стоит 0,00000449 $ вместо 0,00003526 $ и отвечает числом.
        llm["extra_body"] = {"reasoning": {"enabled": False}}
        # Иначе библиотека не найдёт модель в своей табличке, предупредит в
        # журнал и молча обрежет страницу до 8192 токенов.
        llm.setdefault("model_tokens", int(_cfg("model_tokens", 65536) or 65536))
    env_key = _ENV_KEYS.get(provider)
    if env_key and os.environ.get(env_key):
        llm["api_key"] = os.environ[env_key]
    if base_url:
        llm["base_url"] = str(base_url)
    if provider == "ollama":
        llm.setdefault("model_tokens", 8192)
    return {"llm": llm, "verbose": False, "headless": True}


def _schema():
    """Схема ответа. Собирается лениво: pydantic приходит вместе с библиотекой."""
    from pydantic import BaseModel, Field

    class Price(BaseModel):
        price: float | None = Field(None, description="текущая цена в рублях")
        unit: str | None = Field(None, description="единица измерения: шт или кг")
        in_stock: bool | None = Field(None, description="есть ли товар в наличии")

    return Price


def price_from_html(store_code: str, page: str, note: str = "") -> dict | None:
    """Цена со страницы глазами модели: {'price', 'unit', 'in_stock'} или None.

    Наружу не бросает никогда. Не смогла — значит не смогла, у коннектора есть
    чем закрыть этот случай.
    """
    ok, why = available()
    if not ok:
        log.debug("%s: запасной разбор не задействован — %s", store_code, why)
        return None
    if not page:
        return None
    try:
        from scrapegraphai.graphs import SmartScraperGraph

        graph = SmartScraperGraph(prompt=PROMPT, source=trim(page),
                                  config=_llm_config(), schema=_schema())
        answer = graph.run()
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: запасной разбор не сработал (%s)", store_code, exc)
        return None

    result = _as_dict(answer)
    price = result.get("price") if result else None
    try:
        price = round(float(price), 2)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    log.warning("%s: цену нашла модель, а не разбор вёрстки%s — значит разметка сменилась "
                "и регулярное выражение в коннекторе пора чинить", store_code,
                f" ({note})" if note else "")
    unit = str(result.get("unit") or "").strip().lower()
    return {
        "price": price,
        "unit": "kg" if unit.startswith(("кг", "kg")) else "pcs",
        "in_stock": bool(result.get("in_stock", True)),
    }


def _as_dict(answer) -> dict:
    """Ответ бывает объектом схемы, словарём или строкой с JSON — приводим к словарю."""
    if hasattr(answer, "model_dump"):
        return answer.model_dump()
    if isinstance(answer, dict):
        return answer
    if isinstance(answer, str):
        import json

        try:
            parsed = json.loads(answer)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
