"""Запасной разбор страницы языковой моделью.

Саму модель здесь не зовём никогда: проверяется наша обвязка — когда мы к ней
обращаемся, что отдаём, как понимаем ответ и, главное, что без неё всё работает
ровно как раньше.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config  # noqa: E402
from app.connectors import cache, get_connector, smart_extract, stub  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Чистое состояние и молчащий JSON-шлюз Магнита.

    Шлюз здесь молчит нарочно: запасной разбор страницы моделью — это последняя
    ступень, до которой очередь доходит, только когда шлюз не ответил. Не заглушить
    его значило бы проверять живые цены Магнита вместо собственной обвязки — и тест
    бесшумно ушёл бы в сеть.
    """
    from app.connectors.magnit import MagnitConnector

    monkeypatch.setattr(MagnitConnector, "_gateway",
                        lambda self, url, body=None, params=None: (None, 0))
    cache.cache_clear()
    cache.reset_throttle()
    stub.reload_rows()
    yield
    cache.cache_clear()
    cache.reset_throttle()


@pytest.fixture
def turned_on(monkeypatch):
    """Считаем, что библиотека стоит и ключ есть."""
    monkeypatch.setattr(config, "get", lambda key, default=None: {
        "connectors.smart_extract.enabled": True,
        "connectors.smart_extract.model": "openai/gpt-4o-mini",
        "connectors.smart_extract.max_chars": 20000,
    }.get(key, default))
    monkeypatch.setattr(smart_extract, "installed", lambda: True)
    monkeypatch.setenv("OPENAI_API_KEY", "тестовый-ключ")


# ---------- когда не зовём ----------
def test_off_by_default(monkeypatch):
    monkeypatch.setattr(config, "get", lambda key, default=None: default)
    ok, why = smart_extract.available()

    assert ok is False and "config.yaml" in why


def test_says_plainly_that_the_library_is_missing(monkeypatch):
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        True if key.endswith("enabled") else default)
    monkeypatch.setattr(smart_extract, "installed", lambda: False)
    ok, why = smart_extract.available()

    assert ok is False and "pip install scrapegraphai" in why


def test_refuses_without_a_key_instead_of_failing_mid_calculation(monkeypatch):
    monkeypatch.setattr(config, "get", lambda key, default=None: {
        "connectors.smart_extract.enabled": True,
        "connectors.smart_extract.model": "openai/gpt-4o-mini",
    }.get(key, default))
    monkeypatch.setattr(smart_extract, "installed", lambda: True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ok, why = smart_extract.available()

    assert ok is False and "OPENAI_API_KEY" in why


def test_local_model_needs_no_key(monkeypatch):
    monkeypatch.setattr(config, "get", lambda key, default=None: {
        "connectors.smart_extract.enabled": True,
        "connectors.smart_extract.model": "ollama/llama3.1",
    }.get(key, default))
    monkeypatch.setattr(smart_extract, "installed", lambda: True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert smart_extract.available()[0] is True


def test_silent_when_switched_off():
    assert smart_extract.price_from_html("magnit", "<div>420 ₽</div>") is None


# ---------- OpenRouter ----------
def _openrouter(monkeypatch, model="openrouter/deepseek/deepseek-v4-flash", base_url=None):
    monkeypatch.setattr(config, "get", lambda key, default=None: {
        "connectors.smart_extract.enabled": True,
        "connectors.smart_extract.model": model,
        "connectors.smart_extract.base_url": base_url,
    }.get(key, default))
    monkeypatch.setattr(smart_extract, "installed", lambda: True)


def test_openrouter_asks_for_its_own_key(monkeypatch):
    _openrouter(monkeypatch)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    ok, why = smart_extract.available()

    assert ok is False and "OPENROUTER_API_KEY" in why


def test_openrouter_speaks_to_the_library_as_openai(monkeypatch):
    """Библиотека такого поставщика не знает, а OpenAI знает: меняются адрес и ключ."""
    _openrouter(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "тестовый-ключ")
    llm = smart_extract._llm_config()["llm"]

    assert llm["model"] == "openai/deepseek/deepseek-v4-flash"
    assert llm["base_url"] == smart_extract.OPENROUTER_BASE
    assert llm["api_key"] == "тестовый-ключ"


def test_reasoning_is_switched_off_or_the_answer_never_comes(monkeypatch):
    """Без этого канал молчит, и молчит бесшумно.

    Замер 19.09.2026 на живой модели: deepseek-v4-flash на вопрос «найди цену»
    потратила все 400 токенов ответа на рассуждение и вернула ПУСТУЮ строку с
    finish_reason=length. Для приложения это неотличимо от «цены на странице нет»:
    price_from_html наружу не бросает никогда. Поэтому сторож смотрит именно сюда.
    """
    _openrouter(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "тестовый-ключ")
    llm = smart_extract._llm_config()["llm"]

    assert llm["extra_body"] == {"reasoning": {"enabled": False}}


def test_window_is_named_or_the_page_is_silently_cut(monkeypatch):
    """Своей таблички на эту модель у библиотеки нет — промолчит и обрежет до 8192."""
    _openrouter(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "тестовый-ключ")

    assert smart_extract._llm_config()["llm"]["model_tokens"] >= 20000


def test_own_address_wins_over_the_default(monkeypatch):
    """Прокси или другой шлюз задаётся в конфиге и не должен перебиваться умолчанием."""
    _openrouter(monkeypatch, base_url="http://127.0.0.1:8080/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "тестовый-ключ")

    assert smart_extract._llm_config()["llm"]["base_url"] == "http://127.0.0.1:8080/v1"


# ---------- что отдаём модели ----------
def test_scripts_and_styles_are_thrown_away():
    page = "<script>var a=1</script><style>.x{}</style><div>Цена 420 ₽</div>"
    trimmed = smart_extract.trim(page)

    assert "var a=1" not in trimmed and ".x{}" not in trimmed
    assert "420" in trimmed


def test_long_page_is_cut_around_the_price():
    page = "ш" * 60000 + "<div>цена 420 ₽</div>" + "ш" * 60000
    trimmed = smart_extract.trim(page, max_chars=2000)

    assert len(trimmed) <= 2000
    assert "420" in trimmed, "обрезать надо вокруг цены, а не с начала страницы"


def test_short_page_is_left_alone():
    assert smart_extract.trim("<div>цена 420 ₽</div>") == "<div>цена 420 ₽</div>"


# ---------- как понимаем ответ ----------
def _answer(monkeypatch, value):
    class Graph:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self):
            return value

    module = type(sys)("scrapegraphai.graphs")
    module.SmartScraperGraph = Graph
    monkeypatch.setitem(sys.modules, "scrapegraphai", type(sys)("scrapegraphai"))
    monkeypatch.setitem(sys.modules, "scrapegraphai.graphs", module)
    monkeypatch.setattr(smart_extract, "_schema", lambda: None)


def test_reads_a_dictionary_answer(turned_on, monkeypatch):
    _answer(monkeypatch, {"price": 420.9, "unit": "шт", "in_stock": True})
    result = smart_extract.price_from_html("dixy", "<div>420.90</div>")

    assert result == {"price": 420.9, "unit": "pcs", "in_stock": True}


def test_reads_a_json_string_answer(turned_on, monkeypatch):
    _answer(monkeypatch, '{"price": 149, "unit": "кг", "in_stock": false}')
    result = smart_extract.price_from_html("dixy", "<div>149</div>")

    assert result == {"price": 149.0, "unit": "kg", "in_stock": False}


def test_no_price_means_no_answer(turned_on, monkeypatch):
    _answer(monkeypatch, {"price": None})
    assert smart_extract.price_from_html("dixy", "<div>нет цены</div>") is None


def test_zero_price_is_not_a_price(turned_on, monkeypatch):
    _answer(monkeypatch, {"price": 0})
    assert smart_extract.price_from_html("dixy", "<div>0</div>") is None


def test_a_broken_model_does_not_break_the_calculation(turned_on, monkeypatch):
    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("модель недоступна")

    module = type(sys)("scrapegraphai.graphs")
    module.SmartScraperGraph = Boom
    monkeypatch.setitem(sys.modules, "scrapegraphai", type(sys)("scrapegraphai"))
    monkeypatch.setitem(sys.modules, "scrapegraphai.graphs", module)
    monkeypatch.setattr(smart_extract, "_schema", lambda: None)

    assert smart_extract.price_from_html("magnit", "<div>420</div>") is None


def test_success_is_logged_as_a_warning(turned_on, monkeypatch, caplog):
    """Сработавший запасной разбор — сигнал чинить регулярку, а не повод расслабиться."""
    _answer(monkeypatch, {"price": 420.9, "unit": "шт"})
    with caplog.at_level("WARNING"):
        smart_extract.price_from_html("dixy", "<div>420.90</div>", "артикул 1")

    assert "чинить" in caplog.text


# ---------- связка с коннекторами ----------
def test_magnit_asks_the_model_only_when_markup_broke(monkeypatch):
    """Разобралось по-старому — модель не трогаем: она стоит денег и секунд."""
    asked = []
    monkeypatch.setattr(smart_extract, "price_from_html",
                        lambda *a, **k: asked.append(a) or None)
    page = ('<script type="application/ld+json">{"@type":"Product",'
            '"offers":{"@type":"Offer","price":175.99}}</script><title>Чудо</title>')
    monkeypatch.setattr("app.connectors.magnit.MagnitConnector._get_html",
                        lambda self, url, params=None: (page, 200))
    snaps = get_connector("magnit").get_prices(["1000070784"])

    assert snaps[0].price == 175.99
    assert asked == [], "цена нашлась разбором — модель звать незачем"


def test_magnit_falls_to_the_model_before_the_csv(monkeypatch):
    monkeypatch.setattr(smart_extract, "price_from_html",
                        lambda *a, **k: {"price": 199.0, "unit": "pcs", "in_stock": True})
    monkeypatch.setattr("app.connectors.magnit.MagnitConnector._get_html",
                        lambda self, url, params=None: ("<div>вёрстка сменилась</div>", 200))
    snaps = get_connector("magnit").get_prices(["1000070784"])

    assert snaps and snaps[0].price == 199.0


def test_magnit_still_lands_on_the_csv_when_the_model_is_off(monkeypatch):
    """Модель выключена — поведение ровно прежнее, это главное."""
    monkeypatch.setattr("app.connectors.magnit.MagnitConnector._get_html",
                        lambda self, url, params=None: ("<div>вёрстка сменилась</div>", 200))
    snaps = get_connector("magnit").get_prices(["magnit-ogurcy-450"])

    assert snaps and snaps[0].price > 0
