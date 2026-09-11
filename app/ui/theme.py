"""Оформление интерфейса: направление «тёплый продуктовый».

Тёплая бумага вместо серого фона Streamlit, засечная Literata на суммах и заголовках,
Golos Text в интерфейсе. Зелёный означает только выгоду. Магазины различаются цветом точки.
Токены совпадают с макетами канваса и с .streamlit/config.toml.
"""
from __future__ import annotations

import html

import streamlit as st

FONTS = ("https://fonts.googleapis.com/css2?"
         "family=Golos+Text:wght@400;500;600;700"
         "&family=Literata:opsz,wght@7..72,400;7..72,600;7..72,700&display=swap")

# store_code -> css-переменная с цветом магазина
STORE_VAR = {"magnit": "--red", "vkusvill": "--green", "pyaterochka": "--amber"}

CSS = """
<style>
@import url('__FONTS__');

:root{
  --paper:#FBF7F1; --surface:#FFFFFF; --tint:#F5EFE6;
  --ink:#2A241E; --ink2:#6E6459; --ink3:#9C9288;
  --line:#EAE2D6; --line2:#DACFBF;
  --green:oklch(0.58 0.12 150); --green-soft:oklch(0.955 0.028 150);
  --warm:oklch(0.60 0.13 48);  --warm-soft:oklch(0.955 0.033 48);
  --red:oklch(0.58 0.13 28);   --red-soft:oklch(0.955 0.033 28);
  --amber:oklch(0.63 0.12 78); --amber-soft:oklch(0.955 0.038 78);
}

html, body, .stApp, [data-testid="stSidebar"], button, input, textarea, select{
  font-family:"Golos Text","Segoe UI",system-ui,sans-serif;
  font-feature-settings:"tnum" 1,"lnum" 1;
}
.stApp{background:var(--paper);color:var(--ink);}
.block-container{padding-top:2.2rem;max-width:1500px;}

h1,h2,h3{font-family:"Literata",Georgia,"Times New Roman",serif;letter-spacing:-0.02em;color:var(--ink);}
h1{font-weight:600;font-size:2.45rem;line-height:1.08;}
h2{font-weight:600;font-size:1.55rem;}
h3{font-weight:600;font-size:1.2rem;}

/* убираем кнопку Deploy — приложение локальное, на одного пользователя */
[data-testid="stAppDeployButton"]{display:none;}

/* боковой панели нет — навигация сверху */
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"]{display:none !important;}

/* собственная шапка Streamlit перекрывала бы навигацию (z-index 999990) */
[data-testid="stHeader"]{display:none;}

/* верхняя панель: во всю ширину, содержимое по колонке страницы */
.st-key-topbar{
  position:sticky;top:0;z-index:1000;
  /* элемент — flex-child шириной 100%, поэтому одних отрицательных полей мало */
  width:calc(100% + 160px) !important;max-width:none !important;
  margin:-2.2rem -80px 26px !important;padding:13px 80px;
  background:var(--surface);border-bottom:1px solid var(--line);
  box-shadow:0 1px 2px rgba(42,36,30,0.03), 0 12px 30px -24px rgba(42,36,30,0.5);
}
@media (max-width:1200px){
  .st-key-topbar{width:calc(100% + 4rem) !important;
    margin-left:-2rem !important;margin-right:-2rem !important;
    padding-left:2rem;padding-right:2rem;}
}
.st-key-topbar [role="radiogroup"]{gap:4px;flex-wrap:wrap;}
.st-key-topbar [data-testid="stRadioOption"]{
  padding:8px 15px;border-radius:999px;transition:background 140ms ease, color 140ms ease;
}
.st-key-topbar [data-testid="stRadioOption"]:hover{background:var(--tint);}
.st-key-topbar [data-testid="stRadioOption"][data-selected="true"]{background:var(--warm-soft);}
.st-key-topbar [data-testid="stRadioOption"][data-selected="true"] p{font-weight:600;color:var(--ink);}
.st-key-topbar [data-testid="stRadioOption"] p{font-size:0.92rem;color:var(--ink2);white-space:nowrap;}
/* кружок радиокнопки — див прямо перед подписью; в навигации он лишний */
.st-key-topbar [data-testid="stRadioOption"] div:has(+ [data-testid="stMarkdownContainer"]){display:none;}

/* шапка экрана */
.mk-pagehead{
  display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  flex-wrap:wrap;margin:0 0 22px;
}
.mk-pagehead h1{margin:0;font-size:2.6rem;line-height:1.04;}

/* кнопки */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button{
  border-radius:10px;border:1px solid var(--line2);background:var(--surface);
  color:var(--ink);font-weight:500;padding:0.5rem 1rem;transition:border-color 120ms ease;
}
.stButton > button:hover, .stFormSubmitButton > button:hover{border-color:var(--warm);color:var(--ink);}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"]{
  background:var(--warm);border-color:var(--warm);color:#FFFFFF;font-weight:600;
}
.stButton > button[kind="primary"]:hover{background:var(--ink);border-color:var(--ink);color:#FFFFFF;}

/* поля ввода */
[data-baseweb="input"], [data-baseweb="select"] > div, .stNumberInput > div > div{
  border-radius:10px;
}

/* показатели */
[data-testid="stMetricValue"]{
  font-family:"Literata",Georgia,serif;font-weight:600;letter-spacing:-0.02em;
  font-size:1.7rem;color:var(--ink);
}
[data-testid="stMetricLabel"] p{
  font-size:0.7rem;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:var(--ink3);
}

/* карточки-контейнеры и таблицы */
[data-testid="stVerticalBlockBorderWrapper"]{
  border-radius:16px;border-color:var(--line);background:var(--surface);
  box-shadow:0 1px 2px rgba(42,36,30,0.03), 0 14px 34px -26px rgba(42,36,30,0.45);
}
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within{
  border-color:var(--warm) !important;box-shadow:0 0 0 3px var(--warm-soft) !important;
}
.streamlit-expanderHeader, details summary{border-radius:10px;}
[data-testid="stDataFrame"], [data-testid="stTable"]{
  border-radius:13px;overflow:hidden;border:1px solid var(--line);
}
hr, [data-testid="stDivider"]{border-color:var(--line);}
.stTabs [data-baseweb="tab-list"]{gap:22px;border-bottom:1px solid var(--line);}
.stTabs [data-baseweb="tab"]{padding:9px 0;color:var(--ink2);font-weight:500;}
.stTabs [aria-selected="true"]{color:var(--ink);}
.stTabs [data-baseweb="tab-highlight"]{background:var(--warm);}

a{color:var(--warm);text-decoration:none;}
a:hover{color:var(--ink);text-decoration:underline;}

/* блоки, которые рисуем сами */
.mk-eyebrow{font-size:11px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:var(--ink3);}
.mk-serif{font-family:"Literata",Georgia,serif;font-weight:600;letter-spacing:-0.025em;}
.mk-card{background:var(--surface);border:1px solid var(--line);border-radius:16px;
  box-shadow:0 1px 2px rgba(42,36,30,0.03), 0 14px 34px -26px rgba(42,36,30,0.45);}
.mk-card > div[style*="grid"]:hover{background:rgba(245,239,230,0.45);}
.mk-dot{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:middle;}
.mk-row{display:flex;justify-content:space-between;gap:16px;padding:6px 0;font-size:13px;}
.mk-row + .mk-row{border-top:1px solid var(--line);}
.mk-pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:20px;
  font-size:11px;font-weight:600;letter-spacing:0.04em;background:var(--surface);
  border:1px solid var(--line2);color:var(--ink2);}
.mk-bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--tint);}
</style>
"""


def inject() -> None:
    """Подключает шрифты и стили. Вызывается один раз из main.py."""
    st.markdown(CSS.replace("__FONTS__", FONTS), unsafe_allow_html=True)


def logo() -> str:
    """Марка в боковой панели — рисованная иконка вместо эмодзи."""
    return """
<div style="display:flex;align-items:center;gap:11px;white-space:nowrap;">
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" style="color:var(--warm);">
    <path d="M3 6h2.2l2 11.2a1.6 1.6 0 0 0 1.6 1.3h8.1a1.6 1.6 0 0 0 1.6-1.3L20 9H6.4"></path>
    <path d="M10 13h5"></path>
  </svg>
  <span style="font-weight:600;font-size:16px;letter-spacing:-0.012em;">Моя корзина</span>
</div>
"""


def store_color(store_code: str | None) -> str:
    return f"var({STORE_VAR.get(store_code or '', '--ink3')})"


def dot(store_code: str | None) -> str:
    return f'<span class="mk-dot" style="background:{store_color(store_code)};"></span>'


def esc(value) -> str:
    return html.escape(str(value if value is not None else "—"))

# ---------- переиспользуемые блоки разметки ----------
CHECK_SVG = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="3" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M5 12.5 10 17.5 19 7"></path></svg>')
CROSS_SVG = ('<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"></path></svg>')


def mark(ok: bool) -> str:
    """Галочка или прочерк статуса сопоставления."""
    if ok:
        return ('<span style="display:inline-flex;align-items:center;justify-content:center;width:22px;'
                'height:22px;border-radius:50%;background:var(--green-soft);color:var(--green);">'
                f'{CHECK_SVG}</span>')
    return ('<span style="display:inline-flex;align-items:center;justify-content:center;width:22px;'
            'height:22px;border-radius:50%;background:var(--tint);color:var(--ink3);">'
            f'{CROSS_SVG}</span>')


def bar(used: float, cap: float, color: str = "var(--warm)") -> str:
    """Полоса выбранного лимита."""
    pct_ = 0.0 if cap <= 0 else max(0.0, min(100.0, used / cap * 100.0))
    return ('<div style="height:7px;border-radius:4px;background:var(--tint);overflow:hidden;">'
            f'<div style="width:{pct_:.1f}%;height:100%;background:{color};"></div></div>')


def pill(text: str, tone: str = "neutral") -> str:
    tones = {
        "neutral": ("var(--tint)", "var(--ink2)"),
        "green": ("var(--green-soft)", "var(--green)"),
        "warm": ("var(--warm-soft)", "var(--warm)"),
    }
    bg, fg = tones.get(tone, tones["neutral"])
    return (f'<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:20px;'
            f'font-size:11px;font-weight:600;letter-spacing:0.04em;background:{bg};color:{fg};">{text}</span>')


def table(grid: str, header: list[str], rows: list[list[str]],
          foot: list[str] | None = None, aligns: list[str] | None = None) -> str:
    """Таблица в стиле макета. Ячейки — готовый HTML, экранирует вызывающий."""
    aligns = aligns or ["left"] * len(header)

    def line(cells: list[str], *, head: bool = False, footer: bool = False) -> str:
        style = (f"display:grid;grid-template-columns:{grid};gap:16px;align-items:center;padding:9px 20px;")
        if head:
            style += "background:var(--tint);padding-top:12px;padding-bottom:12px;"
        elif footer:
            style += "background:var(--tint);padding-top:14px;padding-bottom:14px;border-top:1px solid var(--line);"
        else:
            style += "border-top:1px solid var(--line);"
        out = [f'<div style="{style}">']
        for i, cell in enumerate(cells):
            align = aligns[i] if i < len(aligns) else "left"
            cls = ' class="mk-eyebrow"' if head else ""
            out.append(f'<span{cls} style="text-align:{align};min-width:0;">{cell}</span>')
        out.append("</div>")
        return "".join(out)

    parts = ['<div class="mk-card" style="overflow:hidden;">', line(header, head=True)]
    parts += [line(r) for r in rows]
    if foot:
        parts.append(line(foot, footer=True))
    parts.append("</div>")
    return "".join(parts)


def block(html_: str) -> None:
    """Вывести готовый блок разметки."""
    st.markdown(html_, unsafe_allow_html=True)


def heading(eyebrow: str, title: str | None = None) -> None:
    out = f'<div class="mk-eyebrow" style="margin:8px 0 6px;">{esc(eyebrow)}</div>'
    if title:
        out += f'<div class="mk-serif" style="font-size:22px;margin-bottom:8px;">{esc(title)}</div>'
    st.markdown(out, unsafe_allow_html=True)


def page_header(title: str, eyebrow: str | None = None, right: str = "") -> None:
    """Шапка экрана: надзаголовок, крупный засечный заголовок и блок справа."""
    left = ""
    if eyebrow:
        left += f'<div class="mk-eyebrow" style="margin-bottom:7px;">{esc(eyebrow)}</div>'
    left += f"<h1>{esc(title)}</h1>"
    st.markdown(
        f'<div class="mk-pagehead"><div>{left}</div><div>{right}</div></div>',
        unsafe_allow_html=True,
    )


def stat_chips(pairs) -> str:
    """Сводка справа в шапке экрана: [(подпись, значение), ...]."""
    cells = []
    for label, value in pairs:
        if value in (None, ""):
            continue
        cells.append(
            '<div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;">'
            f'<span class="mk-eyebrow">{esc(label)}</span>'
            f'<span class="mk-serif" style="font-size:19px;">{value}</span></div>'
        )
    if not cells:
        return ""
    return ('<div style="display:flex;align-items:flex-end;gap:28px;flex-wrap:wrap;'
            'padding:12px 18px;background:var(--surface);border:1px solid var(--line);'
            'border-radius:14px;">' + "".join(cells) + "</div>")
