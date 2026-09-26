#!/usr/bin/env python3
"""Проверка подбора точек для двух адресов СПБ.

Запуск:  python tools/check_spb_points.py
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import geo
from app.connectors import metro, lenta

ADDRS = [
    "Санкт-Петербург, проспект Королёва, 20",
    "Санкт-Петербург, Ушаковская набережная, 5",
]

def main():
    results = {}
    for addr in ADDRS:
        print(f"\n{'='*60}")
        print(f"АДРЕС: {addr}")
        coords = geo.coords(addr)
        print(f"  Координаты: {coords}")
        info = {"coords": coords}

        # METRO
        try:
            store = metro.nearest_store(addr)
            print(f"  METRO: {store}")
            info["metro"] = store
        except Exception as e:
            print(f"  METRO: ошибка — {e}")
            info["metro"] = str(e)

        # Лента
        try:
            hub = lenta.delivery_hub(addr)
            resolved = lenta.resolve_address(addr)
            print(f"  Лента hub: {hub}")
            suggested = (resolved or {}).get("suggested")
            if isinstance(suggested, dict):
                delivery = suggested.get("delivery")
                print(f"  Лента delivery: {delivery}")
            else:
                print(f"  Лента suggested: {suggested}")
            info["lenta_hub"] = hub
            info["lenta_resolved"] = resolved
        except Exception as e:
            print(f"  Лента: ошибка — {e}")
            info["lenta"] = str(e)

        # Магнит — отдельно, т.к. таймаутит
        try:
            from app.connectors.magnit import nearest_store, stores_near
            if coords:
                stores = stores_near(*coords, radius_km=3.0)
                print(f"  Магнит: найдено {len(stores)} магазинов")
                for s in stores[:3]:
                    print(f"    - код {s['code']}, формат {s['format']}, "
                          f"{s['address']}, {s['distance']:.0f} м")
                info["magnit_stores"] = stores[:5]
            else:
                print("  Магнит: нет координат")
        except Exception as e:
            print(f"  Магнит: ошибка — {e}")
            info["magnit"] = str(e)

        results[addr] = info

    # Сравнение
    print(f"\n{'='*60}")
    print("СРАВНЕНИЕ:")
    addrs_list = list(results.keys())
    if len(addrs_list) == 2:
        a, b = addrs_list
        ra, rb = results[a], results[b]

        # METRO
        ma = ra.get("metro") or {}
        mb = rb.get("metro") or {}
        mc_a = ma.get("code") if isinstance(ma, dict) else "?"
        mc_b = mb.get("code") if isinstance(mb, dict) else "?"
        same_metro = mc_a == mc_b
        print(f"  METRO: {mc_a} vs {mc_b} — {'ОДИНАКОВЫЙ' if same_metro else 'РАЗНЫЕ'} "
              f"(расст. {ma.get('distance_km', '?')} vs {mb.get('distance_km', '?')} км)")

        # Лента
        la = ra.get("lenta_hub")
        lb = rb.get("lenta_hub")
        same_lenta = la == lb
        print(f"  Лента hub: {la} vs {lb} — {'ОДИНАКОВЫЙ' if same_lenta else 'РАЗНЫЕ'}")

        # Магнит
        sa = ra.get("magnit_stores", [])
        sb = rb.get("magnit_stores", [])
        ca = sa[0]["code"] if sa else "?"
        cb = sb[0]["code"] if sb else "?"
        same_magnit = ca == cb
        print(f"  Магнит ближайший: {ca} vs {cb} — {'ОДИНАКОВЫЙ' if same_magnit else 'РАЗНЫЕ'}")


if __name__ == "__main__":
    main()
