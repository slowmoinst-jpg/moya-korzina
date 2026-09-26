#!/usr/bin/env python3
"""Проверка работы подбора точек для сетей из BY_POINT.

Запуск:  python tools/verify_points.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import places

ADDR = "Санкт-Петербург, Невский проспект, 1"

def main():
    print(f"BY_POINT chains: {places.BY_POINT}")
    print(f"Тестовый адрес: {ADDR}\n")
    all_ok = True
    for chain in places.BY_POINT:
        resolver = places._RESOLVERS.get(chain)
        if not resolver:
            print(f"[-] {chain}: нет резолвера в places._RESOLVERS!")
            all_ok = False
            continue
        try:
            pt = resolver(ADDR)
            if pt and pt.code:
                print(f"[+] {chain:12} -> код: {pt.code:<15} ({pt.label or pt.address})")
            else:
                print(f"[-] {chain:12} -> точка не найдена")
                all_ok = False
        except Exception as exc:
            print(f"[-] {chain:12} -> ошибка: {exc}")
            all_ok = False

    print("\nИТОГ: " + ("ВСЕ СЕТИ РАБОТАЮТ" if all_ok else "ЕСТЬ ОШИБКИ"))
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
