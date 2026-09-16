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


def build() -> str:
    with open(SRC, encoding="utf-8") as fh:
        code = minify(fh.read())
    if "\n" in code:
        raise SystemExit("в собранном коде остался перевод строки — адрес закладки должен быть одной строкой")
    return "javascript:" + urllib.parse.quote(code, safe="")


def put_into_page(href: str) -> int:
    with open(PAGE, encoding="utf-8") as fh:
        page = fh.read()
    if MARK_OPEN not in page or MARK_CLOSE not in page:
        raise SystemExit(f"в {PAGE} нет меток {MARK_OPEN} / {MARK_CLOSE}")
    head, _, rest = page.partition(MARK_OPEN)
    _, _, tail = rest.partition(MARK_CLOSE)
    link = (f'<a class="bm" href="{href}" onclick="return false;" '
            'title="Перетащите эту ссылку на панель закладок">Забрать чеки</a>')
    with open(PAGE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + MARK_OPEN + link + MARK_CLOSE + tail)
    return len(href)


def main() -> int:
    href = build()
    size = put_into_page(href)
    # Тот же адрес отдельным файлом: его читает приложение, чтобы показать закладку
    # прямо на экране «Мои чеки», и на него же ссылается grab.html для тех, кто
    # заводит закладку руками. Забыть его — значит раздавать прошлую версию.
    with open(PLAIN, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(href)
    print(f"собрано: {size} символов, вставлено в docs/grab.html и docs/grab.min.txt")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
