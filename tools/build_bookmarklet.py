"""Сборка букмарклета из читаемого исходника docs/grab.src.js.

Букмарклет — это одна строка «javascript:…» в адресе закладки. Писать его руками
невозможно, а держать в репозитории нечитаемым комком нельзя: через месяц никто
не поймёт, что он делает. Поэтому источник правды — обычный файл с комментариями,
а эта сборка снимает комментарии, склеивает строки и подставляет результат прямо
в docs/grab.html между метками.

    python tools/build_bookmarklet.py

Проверяет заодно две вещи, на которых легко обжечься: что в собранной строке не
осталось переводов строк (адрес закладки однострочный) и что метки в HTML на
месте, иначе правка молча никуда не попадёт.
"""
from __future__ import annotations

import io
import os
import re
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "grab.src.js")
PAGE = os.path.join(ROOT, "docs", "grab.html")
PLAIN = os.path.join(ROOT, "docs", "grab.min.txt")
MARK_OPEN = "<!-- БУКМАРКЛЕТ:НАЧАЛО -->"
MARK_CLOSE = "<!-- БУКМАРКЛЕТ:КОНЕЦ -->"


def minify(source: str) -> str:
    """Снимает комментарии и лишние пробелы. Без агрессии: код не переименовывается."""
    body = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    kept = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        kept.append(stripped)
    joined = " ".join(kept)
    return re.sub(r"\s{2,}", " ", joined).strip()


# Закладок стало две, и обе собираются одним и тем же способом.
#
# «Забрать чеки» — кабинет ФНС, история покупок. «Забрать цены» — витрина
# Пятёрочки и Самоката: их цены видит только браузер человека (разбор —
# в шапке docs/grab-prices.src.js), и пакет уезжает в дверь POST /api/prices.
#
# Отдельного сборщика второй закладке не завели нарочно: правила сборки у них
# общие — снять комментарии, склеить в одну строку, проверить, что переводов
# строки не осталось. Два сборщика разошлись бы в первый же месяц.
BOOKMARKLETS = {
    "cheki": {"src": SRC, "page": PAGE, "plain": PLAIN, "title": "Забрать чеки"},
    "tseny": {"src": os.path.join(ROOT, "docs", "grab-prices.src.js"),
              "page": os.path.join(ROOT, "docs", "grab-prices.html"),
              "plain": os.path.join(ROOT, "docs", "grab-prices.min.txt"),
              "title": "Забрать цены"},
    # «Передать вход» — третья, и появилась она из стены, а не из удобства:
    # 20.09.2026 сервер не смог войти в Магнит (капча, потом «Аккаунт
    # заблокирован»), а телефон владельца входит как обычно. Разбор того, что
    # она кладёт в буфер, — app/shopbrowser/handoff.py.
    "vhod": {"src": os.path.join(ROOT, "docs", "hand.src.js"),
             "page": os.path.join(ROOT, "docs", "hand.html"),
             "plain": os.path.join(ROOT, "docs", "hand.min.txt"),
             "title": "Передать вход"},
}


def build(src: str = SRC) -> str:
    with open(src, encoding="utf-8") as fh:
        code = minify(fh.read())
    if "\n" in code:
        raise SystemExit("в собранном коде остался перевод строки — адрес закладки должен быть одной строкой")
    return "javascript:" + urllib.parse.quote(code, safe="")


def put_into_page(href: str, page_path: str = PAGE, title: str = "Забрать чеки") -> int:
    with open(page_path, encoding="utf-8") as fh:
        page = fh.read()
    if MARK_OPEN not in page or MARK_CLOSE not in page:
        raise SystemExit(f"в {page_path} нет меток {MARK_OPEN} / {MARK_CLOSE}")
    head, _, rest = page.partition(MARK_OPEN)
    _, _, tail = rest.partition(MARK_CLOSE)
    link = (f'<a class="bm" href="{href}" onclick="return false;" '
            f'title="Перетащите эту ссылку на панель закладок">{title}</a>')
    with open(page_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + MARK_OPEN + link + MARK_CLOSE + tail)
    return len(href)


def main(argv: list[str] | None = None) -> int:
    names = [a for a in (argv if argv is not None else sys.argv[1:]) if not a.startswith("-")]
    chosen = names or list(BOOKMARKLETS)
    for name in chosen:
        spec = BOOKMARKLETS.get(name)
        if not spec:
            raise SystemExit(f"закладки «{name}» нет; есть: {', '.join(BOOKMARKLETS)}")
        href = build(spec["src"])
        size = put_into_page(href, spec["page"], spec["title"])
        # Тот же адрес отдельным файлом: его читает приложение, чтобы показать
        # закладку прямо на экране, и на него же ссылается страница для тех, кто
        # заводит закладку руками. Забыть его — значит раздавать прошлую версию.
        with open(spec["plain"], "w", encoding="utf-8", newline="\n") as fh:
            fh.write(href)
        print(f"{name}: собрано {size} символов -> "
              f"{os.path.relpath(spec['page'], ROOT)}, {os.path.relpath(spec['plain'], ROOT)}")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
