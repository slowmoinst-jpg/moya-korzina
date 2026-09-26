#!/usr/bin/env python3
"""Полное сравнение двух адресов СПБ: точки, цены, остатки.

Запуск:  python tools/compare_spb_addresses.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import geo
from app.connectors import metro, lenta, mcp_client

ADDRS = [
    "Санкт-Петербург, проспект Королёва, 20",
    "Санкт-Петербург, Ушаковская набережная, 5",
]

SEARCH_QUERY = "молоко"


def lenta_search(store_id, query=SEARCH_QUERY, limit=5):
    result = mcp_client.call_tool(
        "https://mcp.lenta.com/mcp", "lenta",
        "storefront_products_search",
        {"query": query, "storeId": store_id, "channel": "lo", "limit": limit},
    )
    payload = mcp_client.ok_payload(result)
    return (payload or {}).get("items", [])


def metro_search(store_id, query=SEARCH_QUERY, limit=5):
    """Товары METRO через API."""
    import requests

    url = f"https://api.metro-cc.ru/api/v1/{store_id}/search"
    params = [("q", query), ("page", "1"), ("perPage", str(limit))]
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", {})
        if isinstance(rows, dict):
            return rows.get("data", [])
        return []
    except Exception as e:
        print(f"  METRO search error: {e}")
        return []


def main():
    results = {}

    for addr in ADDRS:
        print(f"\n{'='*70}")
        print(f"АДРЕС: {addr}")
        coords = geo.coords(addr)
        print(f"  Координаты: {coords}")

        # METRO
        m_store = metro.nearest_store(addr)
        m_code = m_store.get("code") if m_store else None
        print(f"  METRO: ТЦ {m_code} — {m_store.get('address', '?')} ({m_store.get('distance_km', '?')} км)" if m_store else "  METRO: не найден")

        # Лента
        hub = lenta.delivery_hub(addr)
        info = lenta.hub_info(addr)
        l_label = info.get("address", "?") if info else "?"
        l_dist = info.get("distance", "?") if info else "?"
        print(f"  Лента: ТК{hub} — {l_label} ({l_dist} м)" if hub else "  Лента: не найден")

        # --- Цены ---
        print(f"\n  Поиск «{SEARCH_QUERY}»:")

        # Лента prices
        if hub:
            items = lenta_search(hub)
            print(f"  Лента (ТК{hub}): {len(items)} товаров")
            for it in items[:5]:
                name = (it.get("name") or "?")[:45]
                price = it.get("price", "?")
                stock = it.get("stock", "?")
                regular = it.get("priceRegular", "")
                discount = f" (было {regular})" if regular and regular != price else ""
                print(f"    {name:<45}  {price:>7}r  x{stock}{discount}")

        # METRO prices
        if m_code:
            items = metro_search(m_code)
            print(f"  METRO (ТЦ{m_code}): {len(items)} товаров")
            for it in items[:5]:
                name = (it.get("name") or "?")[:45]
                prices = it.get("prices", {})
                if isinstance(prices, dict):
                    price_val = prices.get("price", "?")
                    stock_val = it.get("stocks", [{}])
                    stock_num = stock_val[0].get("count", "?") if stock_val else "?"
                else:
                    price_val = "?"
                    stock_num = "?"
                print(f"    {name:<45}  {price_val:>7}r  x{stock_num}")

        results[addr] = {"coords": coords, "metro": m_code, "lenta_hub": hub}

    # --- Итог ---
    print(f"\n{'='*70}")
    print("ИТОГ СРАВНЕНИЯ:")
    a1, a2 = ADDRS
    r1, r2 = results.get(a1, {}), results.get(a2, {})
    print(f"\n  {'':40} {'Королёва 20':>15} {'Ушаковская 5':>15}")
    print(f"  {'Координаты':40} {str(r1.get('coords','?')):>15} {str(r2.get('coords','?')):>15}")
    print(f"  {'METRO ТЦ':40} {str(r1.get('metro','?')):>15} {str(r2.get('metro','?')):>15}")
    print(f"  {'Лента ТК':40} {str(r1.get('lenta_hub','?')):>15} {str(r2.get('lenta_hub','?')):>15}")

    same_metro = r1.get("metro") == r2.get("metro")
    same_lenta = r1.get("lenta_hub") == r2.get("lenta_hub")
    print(f"\n  METRO: {'ОДИНАКОВЫЙ' if same_metro else 'РАЗНЫЕ'} ТЦ")
    print(f"  Лента: {'ОДИНАКОВЫЙ' if same_lenta else 'РАЗНЫЕ'} хаб")

    if not same_lenta:
        print("\n  → Лента: разные точки = разные цены и остатки ✓")
    if same_metro:
        print("  → METRO: один ТЦ, но остатки внутри него могут отличаться по позициям")


if __name__ == "__main__":
    main()
