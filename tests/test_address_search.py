import pytest
import requests

from app import address_search


def place(city, street, number, country="RU"):
    return {"properties": {"countrycode": country, "city": city, "street": street,
                           "housenumber": number, "state": city}, "geometry": {"coordinates": [30.2, 60.0]}}


@pytest.mark.parametrize("query,expected", [
    ("санкт-петербург королева 20", ["20", "20 к1", "20 к2"]),
    ("СПб, пр-кт Королёва, д. 20 корп. 1", ["20 к1"]),
    ("г. Санкт-Петербург проспект Королёва 20к2", ["20 к2"]),
    ("санкт-петербург королева 200", []),
])
def test_delivery_house(monkeypatch, query, expected):
    city = "Санкт-Петербург"
    road = "проспект Королёва"
    features = [place(city, "набережная реки Мойки", "20"), place(city, "проспект Энгельса", "150 к1"),
                place("Москва", road, "20"), place(city, road, "20", "BY"), place(city, road, ""),
                *(place(city, road, n) for n in ("20 к1", "20 к2", "20", "20 к1", "201"))]
    calls = []
    class Reply:
        def raise_for_status(self): pass
        def json(self): return {"features": features}
    def fetch(url, **kwargs):
        calls.append((url, kwargs["params"]))
        return Reply()
    monkeypatch.setattr(address_search.geo, "token", lambda: None)
    monkeypatch.setattr(address_search.requests, "get", fetch)
    items = address_search.suggest(query)
    assert [i["label"] for i in items] == [f"{city}, {road}, {n}" for n in expected]
    assert all(i["lat"] == 60.0 and i["lon"] == 30.2 for i in items)
    assert calls[0][0].endswith("/structured")
    assert calls[0][1]["countrycode"] == "ru"


def test_provider_outage_differs_from_no_match(monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.Timeout()
    monkeypatch.setattr(address_search.geo, "token", lambda: None)
    monkeypatch.setattr(address_search.requests, "get", unavailable)
    with pytest.raises(address_search.SearchUnavailable):
        address_search.suggest("Казань Баумана 10")
