"""Что лежит в состоянии витрины Fix Price — разовый разбор перед сборщиком.

Витрина на Nuxt: сервер отрисовывает страницу и кладёт рядом её состояние в
window.__NUXT__. Там товары целиком — с ценой, артикулом и наличием, — то есть
разбирать разметку не нужно вовсе. Этот разбор снимает форму данных один раз,
чтобы сборщик писался по фактам, а не по догадкам о полях.

    python tools/nuxtdump.py                       раздел «Продукты и напитки»
    python tools/nuxtdump.py --path /catalog/...   любой раздел
    python tools/nuxtdump.py --tree                дерево разделов
"""
from __future__ import annotations

import argparse
import json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SITE = "https://fix-price.com"

# Ищем в состоянии списки словарей, похожие на товары: у товара обязаны быть имя
# и цена. Перебором, а не по известному пути, — путь в Nuxt меняется от сборки к
# сборке, а форма товара живёт дольше.
FIND_JS = """
() => {
  const out = [];
  const seen = new Set();
  const looksLikeProduct = (o) =>
    o && typeof o === 'object' && !Array.isArray(o) &&
    ('price' in o || 'specialPrice' in o) && ('title' in o || 'name' in o);
  const walk = (node, path, depth) => {
    if (depth > 8 || node === null || typeof node !== 'object') return;
    if (seen.has(node)) return;
    seen.add(node);
    if (Array.isArray(node)) {
      if (node.length && looksLikeProduct(node[0])) {
        out.push({path, count: node.length, sample: node[0]});
        return;
      }
      node.slice(0, 40).forEach((v, i) => walk(v, path + '[' + i + ']', depth + 1));
      return;
    }
    for (const k of Object.keys(node)) walk(node[k], path + '.' + k, depth + 1);
  };
  walk(window.__NUXT__, '__NUXT__', 0);
  return out.slice(0, 6);
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Форма данных витрины Fix Price")
    parser.add_argument("--path", default="/catalog/produkty-i-napitki")
    parser.add_argument("--tree", action="store_true", help="снять дерево разделов")
    parser.add_argument("--wait", type=float, default=7.0)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow",
                                      user_agent=UA,
                                      viewport={"width": 1366, "height": 900})
        page = context.new_page()

        # Дерево разделов витрина берёт у себя же — слушаем её собственный ответ,
        # снаружи этот адрес закрыт CORS и Turnstile.
        caught: dict = {}

        def listen(response) -> None:
            if "/v1/category" in response.url and "menu" not in response.url:
                try:
                    caught["tree"] = response.json()
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", listen)
        page.goto(SITE + args.path, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(int(args.wait * 1000))

        if args.tree:
            tree = caught.get("tree")
            print(f"разделов поймано: {len(tree) if isinstance(tree, list) else '—'}")
            if isinstance(tree, list) and tree:
                print("поля раздела:", sorted(tree[0]))
                for row in tree[:10]:
                    print(f"  {row.get('alias'):40} {str(row.get('title'))[:40]}")
            browser.close()
            return 0

        found = page.evaluate(FIND_JS)
        print(f"найдено списков товаров: {len(found)}")
        for hit in found:
            print(f"\n--- {hit['path']}  ({hit['count']} шт) ---")
            print("поля:", sorted(hit["sample"]))
            print(json.dumps(hit["sample"], ensure_ascii=False, indent=2)[:1500])
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
