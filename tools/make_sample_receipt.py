"""Собирает data/sample_receipt.pdf из data/sample_receipt.txt — PDF с текстовым слоем.

Запуск:  .venv\\Scripts\\python.exe tools/make_sample_receipt.py

Кириллица требует TTF-шрифта с юникодом: берём первый доступный из системных
(DejaVuSans / Arial / Verdana / Tahoma). Встроенный Helvetica (WinAnsi) кириллицу
не отдаёт, поэтому на него не откатываемся.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "sample_receipt.txt")
DST = os.path.join(ROOT, "data", "sample_receipt.pdf")

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\verdana.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _find_font() -> str | None:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def build_pdf(src: str = SRC, dst: str = DST) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_path = _find_font()
    if not font_path:
        raise RuntimeError("Не найден TTF-шрифт с кириллицей — PDF собрать нельзя")
    font_name = "ReceiptFont"
    pdfmetrics.registerFont(TTFont(font_name, font_path))

    with open(src, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    width, height = A4
    left, top, leading, size = 40, height - 50, 14, 9.5
    c = canvas.Canvas(dst, pagesize=A4)
    c.setTitle("Кассовый чек")
    c.setFont(font_name, size)
    y = top
    for line in lines:
        if y < 50:
            c.showPage()
            c.setFont(font_name, size)
            y = top
        c.drawString(left, y, line)
        y -= leading
    c.showPage()
    c.save()
    return dst


def main() -> int:
    try:
        path = build_pdf()
    except ImportError:
        print("reportlab не установлен: .venv\\Scripts\\python.exe -m pip install reportlab")
        return 1
    except Exception as exc:
        print(f"Не удалось собрать PDF: {exc}")
        return 1
    print(f"PDF собран: {path} ({os.path.getsize(path)} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
