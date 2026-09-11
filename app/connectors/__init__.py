"""Слой коннекторов цен. Публичный контракт для матчера, оптимизатора и UI.

    from app.connectors import get_connector
    c = get_connector('magnit')          # 'magnit' | 'vkusvill' | 'pyaterochka' | 'stub'
    c.search('страчателла', limit=3)     # -> list[Candidate] с заполненным score
    c.get_prices(['magnit-strachatella-200'])   # -> list[PriceSnapshot]

Ни search(), ни get_prices() не выбрасывают исключений наружу: при сбое сети или
непонятном ответе API коннектор пишет logging.warning и отдаёт резервные цены
из data/fallback_prices.csv либо пустой список.
"""
from app.connectors.base import (  # noqa: F401
    Connector,
    ConnectorError,
    HttpCatalogConnector,
    available_codes,
    get_connector,
    similarity,
)
from app.connectors.magnit import MagnitConnector  # noqa: F401
from app.connectors.stub import StubConnector  # noqa: F401
from app.connectors.vkusvill import VkusvillConnector  # noqa: F401

__all__ = [
    "Connector",
    "ConnectorError",
    "HttpCatalogConnector",
    "MagnitConnector",
    "StubConnector",
    "VkusvillConnector",
    "available_codes",
    "get_connector",
    "similarity",
]
