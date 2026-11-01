---
# gstack: design-md-format=spec
name: Моя корзина
description: Утилитарная витрина-ответ для одного покупателя — белый экран, чёрные действия и одна жёлтая полка, на которой видно, где сегодня дешевле.
colors:
  primary: "#141414"            # действие, выбранное место, основной текст
  primary-pressed: "#2C2C2B"
  on-primary: "#FFFFFF"
  on-primary-muted: "#BDBCBA"   # сумма в подписи главной кнопки
  background: "#FFFFFF"
  surface: "#F5F5F4"            # карточки, места, счётчики, кнопки-призраки
  surface-pressed: "#ECECEA"
  text: "#141414"
  text-muted: "#5C5B5A"         # единственный второй цвет текста
  line: "#EDEDEC"
  line-strong: "#DAD9D7"
  divider: "#E7E7E5"
  accent: "#FFE7A8"             # полка-ответ: одна на экран
  success: "#17703A"
  success-soft: "#E8F5EC"
  warning: "#5C4A12"
  warning-soft: "#FFF3D1"
  error: "#C40D00"
  error-soft: "#FEEBEA"
  chain-magnit: "#D8452B"
  chain-vkusvill: "#2E9E5B"
  chain-pyaterochka: "#E2A32B"
  chain-lenta: "#3B6FD4"
  chain-dixy: "#C8562F"
  chain-samokat: "#7A5CD0"
  chain-metro: "#1F3A93"
  chain-perekrestok: "#0E7C66"
  chain-fixprice: "#5B6573"
typography:
  display:
    fontFamily: Onest
    fontWeight: 800
    fontSize: 34px
    lineHeight: 1.1
    letterSpacing: -0.01em
    fontFeature: tnum
  title:
    fontFamily: Onest
    fontWeight: 800
    fontSize: 22px
    lineHeight: 1.15
    letterSpacing: -0.01em
  section:
    fontFamily: Onest
    fontWeight: 800
    fontSize: 19px
    lineHeight: 1.2
    letterSpacing: -0.005em
  cta:
    fontFamily: Onest
    fontWeight: 600
    fontSize: 16px
    lineHeight: 1
  body:
    fontFamily: Onest
    fontWeight: 400
    fontSize: 15px
    lineHeight: 1.35
  dense:
    fontFamily: Onest
    fontWeight: 400
    fontSize: 14px
    lineHeight: 1.35
  small:
    fontFamily: Onest
    fontWeight: 500
    fontSize: 13px
    lineHeight: 1.35
  label:
    fontFamily: Onest
    fontWeight: 600
    fontSize: 12px
    lineHeight: 1
  numeric:
    fontFamily: Onest
    fontFeature: tnum
rounded:
  mark: 7px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  full: 9999px
spacing:
  hairline: 2px    # только внутри плашек и строк значков
  xs: 4px
  inline: 6px      # только внутри плашек и строк значков
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  2xl: 32px
components:
  button:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.sm}"
    height: 32px
  button-pressed:
    backgroundColor: "{colors.primary-pressed}"
  button-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    height: 32px
  button-ghost-pressed:
    backgroundColor: "{colors.surface-pressed}"
  button-big:
    height: 36px
  cta:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
    height: 48px
  cta-sub:
    textColor: "{colors.on-primary-muted}"
  cta-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    height: 48px
  input:
    backgroundColor: "{colors.background}"
    borderColor: "{colors.line-strong}"
    rounded: "{rounded.sm}"
    height: 36px
  input-focus:
    borderColor: "{colors.primary}"
  shelf:
    backgroundColor: "{colors.accent}"
    rounded: "{rounded.xl}"
    padding: 16px
  receipt:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.md}"
  place:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    minHeight: 60px
  place-pressed:
    backgroundColor: "{colors.surface-pressed}"
  place-selected:
    backgroundColor: "{colors.background}"
    borderColor: "{colors.primary}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
  order-card:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.lg}"
  habit-card:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.lg}"
    width: 132px
  row:
    minHeight: 48px
    borderColor: "{colors.divider}"
  product-row:
    minHeight: 56px
    borderColor: "{colors.divider}"
  photo:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    size: 44px
  stepper:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    height: 32px
  chain-mark:
    textColor: "{colors.on-primary}"
    rounded: "{rounded.mark}"
    size: 22px
  badge:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
    rounded: "{rounded.full}"
    height: 22px
  badge-recommended:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text}"
    rounded: "{rounded.full}"
  badge-warning:
    backgroundColor: "{colors.warning-soft}"
    textColor: "{colors.warning}"
    rounded: "{rounded.full}"
  chip:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.text}"
    rounded: "{rounded.full}"
    height: 26px
  note:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
  note-ok:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
  note-warning:
    backgroundColor: "{colors.warning-soft}"
    textColor: "{colors.warning}"
  note-error:
    backgroundColor: "{colors.error-soft}"
    textColor: "{colors.error}"
  progress:
    backgroundColor: "{colors.divider}"
    rounded: "{rounded.full}"
    height: 6px
  bottom-bar:
    backgroundColor: "{colors.background}"
    borderColor: "{colors.line}"
  tab:
    textColor: "{colors.text-muted}"
    height: 56px
  tab-active:
    textColor: "{colors.text}"
---

# Моя корзина

## Overview

**Creative North Star:** витрина-ответ. Белый экран, чёрные действия и одна жёлтая полка, на которой приложение отвечает цифрой, где сегодня дешевле. Владелец меряет приложение деньгами и временем, поэтому экран отвечает числом со значком, а не абзацем.

**Product context:** личное приложение для покупки продуктов. Оно сравнивает корзину по сетям (Магнит, ВкусВилл, Перекрёсток, Пятёрочка, Лента, Дикси, METRO, Самокат, Fix Price) и собирает её в кабинетах сетей. Пользователь — владелец, на пробе — его друзья. Главная поверхность — телефон: Android-приложение с WebView поверх Flask и Jinja (`app/web/templates/phone/*`, `app/web/static/phone.css`). Веб на компьютере (`korzina.css`, Rubik и Unbounded, направление «Цвет магазина») — старая система, см. Decisions Log.

Что должно запомниться, словами владельца: «Видны топ 3 варианта корзин откуда и что выгоднее заказать исходя из того, что пользователь хочет купить».

**Mode per surface:**
- «Где дешевле» — Operate: ответ и три места видны сразу, заказ одной кнопкой.
- «Корзина» — Operate: набрать список быстро — поиск, счётчики, полка «Из привычного».
- «История заказов» — Read: сверить прошлые заказы и повторить заказ.
- «Настройки» — Operate: подключить магазины, чеки ФНС, телефон и адрес.

**Reference sites:** конкурентов не исследовали (решение 01.11.2026). Вид экранов взят «по мотивам Магнита» (22.10.2026): серые подложки, фото из каталога, без красного.

**Key characteristics:**
- Сначала значок с числом (✓16/16, грузовик 0 ₽, часы 21:40), текст — только для исключений.
- Одна жёлтая полка на экран, и на ней ответ приложения.
- Деньги крупно, целыми рублями, табличными цифрами.
- Цвет означает только состояние (всё есть, не хватает, не вышло) или сеть (метка).
- Компактно: одна колонка, внизу прибита панель с главным действием и двумя вкладками.

## Colors

**Strategy:** Restrained. Почти всё чёрно-белое: `primary` — это действие и выбранное, `surface` — всё, на что можно нажать или что сгруппировано, `accent` — только полка с ответом. Состояния — пары «текст на мягком фоне»: `success`, `warning`, `error`. Цвета сетей живут только в метках сетей.

**Light or dark:** только светлая тема (решение 01.11.2026). Экран смотрят днём в магазине, дома и на кухне, а одна тема — вдвое меньше вёрстки и проверок. В шаблонах стоит `<meta name="color-scheme" content="light">`, тёмных вариантов токенов нет.

Контраст по WCAG 2.2: все пары текста не ниже AA, худшая — `error` на `error-soft`, 5,4 : 1. Буквы в метках сетей местами ниже 4,5 : 1 (ВкусВилл 3,4). Это допустимо как логотип: рядом всегда название сети, а для чтения с экрана метка немая (`aria-hidden`). У Пятёрочки буква чёрная — белая на жёлтом даёт 2,2.

Состояние никогда не передаётся одним цветом. «Всё есть» — галочка, «не хватает» — треугольник с числом, «не вышло» — треугольник и текст.

Соответствие токенов переменным `phone.css`:

| Токен | Переменная |
|---|---|
| `primary`, `text` | `--ink` |
| `primary-pressed` | новая `--ink-press` (сейчас литерал в `.bt:hover`) |
| `on-primary-muted` | новая `--ink-sub` (сейчас литерал в `.cta .sub`) |
| `text-muted` | `--ink2`; `--ink3` убирается, его места переходят на `--ink2` |
| `surface`, `surface-pressed` | `--tint`, `--tint2` |
| `line`, `line-strong`, `divider` | `--line`, `--line2`, `--soft` |
| `accent` | `--shelf` |
| `success`, `success-soft` | `--green` (и `--green-ink`), `--green-soft` |
| `warning`, `warning-soft` | `--amber-ink`, `--amber-soft` |
| `error`, `error-soft` | `--red`, `--red-soft` |
| `chain-*` | `--s-*` |

Дорожка шкалы «Магазины 3 из 8» берёт `divider`, литерал в `.bar` уходит.

## Typography

Один шрифт — Onest (Google Fonts, лицензия OFL, авторы Дмитрий Волошин и Андрей Кудрявцев). Кириллица своя, а не дорисованная; знаки ₽ (U+20BD), узкий неразрывный пробел (U+202F) и минус (U+2212) в шрифте есть. Грузим начертания 400–800 с `display=swap` в фоне (`media=print` → `all`): пока шрифт не пришёл, экран пишется системным и не стоит пустым.

Восемь размеров, полуразмеров нет:
- `display` — цена-ответ на полке;
- `title` — заголовок экрана (был 26 px, стал 22: экономит строку над ответом);
- `section` — заголовки разделов, название места на полке, суммы мест;
- `cta` — главная кнопка и поля ввода (16 px не даёт iOS увеличивать страницу при фокусе);
- `body` — основной текст, название места в списке (600);
- `dense` — строки товаров, строки заказов, ссылка «назад», кнопки в строках;
- `small` — значки с числами, подписи, мини-чек;
- `label` — плашки и вкладки.

Деньги. У Onest цифры по умолчанию пропорциональные, поэтому каждая сумма идёт табличными цифрами (`numeric`, класс `.num`). Правила записи:
- целые рубли везде, кроме перечней: цена-ответ, суммы мест, подпись главной кнопки, итог заказа;
- копейки только в перечнях, где сумму сверяют по строкам: мини-чек на полке и строки заказа в истории;
- между разрядами и перед ₽ — узкий неразрывный пробел U+202F, число не рвётся и читается одним словом;
- от 19 px и крупнее знак ₽ на 70 % кегля, мельче — полного размера;
- скидка пишется настоящим минусом U+2212: «−84,90 ₽».

Rubik и Unbounded остаются только в старом вебе на компьютере, в телефон их не переносим.

## Layout

Одна колонка на всю ширину WebView (360–430 px). Поля экрана — `lg`, на сером холсте («История заказов») — `md`. Между блоками экрана `md`, между соседями (места, кнопки, карточки) `sm`, между значком и числом `xs`. Плотно, без поясняющих абзацев.

Низ экрана прибит: строка действий (поля `sm` по вертикали и `lg` по бокам) и под ней две вкладки, «Корзина» и «Настройки». Низ учитывает `safe-area-inset-bottom`, экран — минимум `100dvh`.

Проверяемое правило: на 390×844 полка-ответ и все три места «Где дешевле» видны без прокрутки. В превью третье место кончается на 474 px, кнопки начинаются с 723 px.

На экране не больше трёх мест, остальные — под «Ещё варианты». Порядок: сначала места, где есть все товары, среди них — дешевле.

Медиа-запросов по ширине в телефонной части нет и не нужно, кроме двух поведенческих: `(hover: hover)` и `(prefers-reduced-motion: reduce)`.

## Elevation & Depth

Плоско. Глубину дают подложки: белый фон → `surface` → `surface-pressed` при касании. Выбранное место выделено рамкой `primary` в 2 px, а не тенью. Тень есть у двух вещей:
- прибитый низ — линия `line` сверху, `0 -1px 0`;
- подсказки адреса — единственный плавающий слой, мягкая тень `0 8px 24px` чёрного с прозрачностью 14 %.

Метки сетей в стопке перекрываются на 6 px и отделяются кольцом в 2 px цвета подложки, на которой лежат: на полке — `accent`, на месте — `surface`, на выбранном месте — белое.

## Shapes

Иерархия скруглений:
- `mark` — метка сети, квадрат 22 px;
- `sm` — кнопки, поля, счётчик;
- `md` — главная кнопка, заметки, фото товара, мини-чек;
- `lg` — карточки, места, заказы, карточки «Из привычного»;
- `xl` — полка;
- `full` — плашки, чипы сетей, шкала.

Вложенные предметы (мини-чек на полке, фото в карточке) держат `md`: это отдельные вещи на подложке, а не вырезы в ней.

## Components

Общие состояния:
- наведение только внутри `@media (hover: hover)`, на телефоне `:hover` залипает после касания;
- у всего, на что нажимают, есть `:active`;
- фокус с клавиатуры — контур 2 px `primary` с отступом 2 px (`:focus-visible`);
- недоступное — прозрачность 0,45, без отклика на нажатие.

**Кнопка** (`.bt`): `button`, текст `dense` 600. Касание — `button-pressed` и сжатие до 0,97. Кнопка-призрак (`button-ghost`) на сером (карточка, холст истории) становится белой и при касании темнеет до `surface`. Большая (`button-big`) — рядом с полем ввода той же высоты и «Повторить» в истории, текст `body`.

**Главная кнопка** (`.cta`): `cta` во всю ширину, справа после «·» сумма цветом `cta-sub` табличными цифрами. Призрак (`cta-ghost`) — «История заказов» со значком часов, текст `body`. Для составного места кнопок две, по одной на сеть: «Заказать в Магните · 1 920 ₽».

**Полка-ответ** (`.shelf`): одна на экран. Сверху стопка меток и название места (`section`), под ним цена `display` и справа белая плашка «Рекомендуем», если это лучшее место. Ниже строка значков: ✓16/16, грузовик с доставкой, часы со временем цен, значок чека справа. Касание строки раскрывает мини-чек (`receipt`, `small`, копейки, итог через линию `line-strong`).

**Место** (`.opt`): слева название (`body` 600, одна строка с многоточием), под ним метки сетей — у составного места каждая метка со своим числом товаров, «[М] 9 · [В] 7», — и значок полноты. Справа сумма `section` 800 табличными цифрами, все суммы одним столбцом. Выбранное — `place-selected` и `aria-current`. Лучшее, но не выбранное, получает белую плашку «Рекомендуем». Касание места только темнит (`place-pressed`), без сжатия.

**Значок с числом** (`.sig`): значок 16 px и число `small` табличными цифрами. «Не хватает» — цвет `warning` и треугольник, время цен — `text-muted`. Пока сети отвечают, первым идёт кольцо-спиннер с «N/M».

**Метка сети** (`.lg`): `chain-mark` цвета сети, буква `label` 800. У Пятёрочки «5» чёрная, у METRO латинская «M». Метка немая, название сети всегда рядом.

**Строка товара** (`.it`): `product-row`, фото `photo` (значок сумки, если фото нет), название `dense`, счётчик `stepper`. На последней штуке минус становится корзиной для мусора; число — `dense` 600 шириной не меньше 44 px.

**«Из привычного»** (`.habits`): вторая разрешённая полка `accent` — подсказка приложения, а не ответ, поэтому стоит под товарами корзины. Карточки `habit-card` листаются вбок с прилипанием, фото 84 px, название в две строки, причина `small` `text-muted`, призрак «В корзину».

**Заказ** (`.ord`): `order-card` на сером холсте. В шапке метка, сеть (700), дата `small`, итог 16 px 800 целыми рублями. Под шапкой число товаров и «Повторить» или зелёная плашка «В корзине». Дальше первые пять строк (`dense`; количество `small` `text-muted` в колонке 48 px; цена 600 в колонке 80 px с копейками) и раскрытие «Ещё N товаров».

**Настройки**: карточка «Магазины 3 из 8» (число `title` табличными цифрами), шкала `progress` с заливкой `primary`, подключённые сети — `chip` с меткой. Строки `row`: метка, название, кнопка-призрак. Чеки ФНС — строка со значком чека и зелёным числом чеков.

**Заметка** (`.note`): только для исключений, текст `small` с межстрочным 1,4, всегда со значком. Варианты: `note-ok`, `note-warning`, `note-error`.

**Плашка** (`.badge`): `badge` (`label`), варианты `badge-recommended` и `badge-warning`.

**Вкладки** (`.tabs`): две, `tab`, значок 20 px над подписью `label`. Активная — `tab-active`, начертание 700, `aria-current="page"`.

**Поле ввода**: `input`, текст `cta` (16 px). Фокус — `input-focus` и кольцо 1 px того же цвета.

## Do's and Don'ts

- Do: сначала значок с числом, текст — только если без него не понять (исключения, ошибки).
- Do: деньги табличными цифрами, целыми рублями вне перечней, с U+202F в разрядах и перед ₽, ₽ на 70 % от 19 px.
- Do: одна полка `accent` на экран — там ответ приложения; вторая допустима только для «Из привычного» под товарами.
- Do: каждый `:hover` — внутри `@media (hover: hover)`, у каждой нажимаемой вещи — `:active`.
- Do: на 390×844 ответ и три места видны без прокрутки — проверять при любой правке «Где дешевле».
- Don't: поясняющие абзацы в интерфейсе и сравнение с прошлым заказом («Выгода», зачёркнутая цена): разница с прошлым заказом меряет инфляцию, а не выбор приложения.
- Don't: цвета сетей вне меток — ни цветных строк, ни цветных цен, ни цветных кнопок.
- Don't: второй серый для текста (бывший `--ink3`), новые шрифты, тёмная тема.
- Don't: тени для иерархии и полупиксельные размеры (13,5 / 14,5).
- Don't: анимировать что-то кроме нажатия и перестановки мест; раскрытия (`details`) открываются сразу.

## Motion

- **Approach:** minimal-functional: нажатие плюс одна авторская анимация.
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out).
- **Duration:** нажатие 100 ms; появление нового места 180 ms; сдвиг места 240 ms.
- **The one authored moment:** места встают по местам, пока сети отвечают (FLIP). Существующее место едет к новой позиции 240 ms ease-in-out. Новое въезжает снизу на 8 px с проявлением за 180 ms ease-out. Пока палец на списке, порядок стоит: новые места встают в конец, пересортировка — через 0,8 с после отпускания. Пока идут цены, экран перезагружается каждые 5 с, поэтому позиции мест хранятся в `sessionStorage` по `data-place`, и сдвиг идёт от прошлых позиций.
- Нажатие: кнопки и главная кнопка темнеют и сжимаются до 0,97, строки и места только темнеют.
- `prefers-reduced-motion: reduce`: всё сразу, без сдвигов и сжатия.

## Migration from phone.css

Что поменять в `app/web/static/phone.css` и шаблонах, чтобы экран совпал с этим файлом (сверено 01.11.2026):
1. Заголовок экрана 26 → 22 px.
2. Полуразмеры и размеры вне шкалы: кнопка 13,5 → 14; место 14,5 → 15; строка товара 14,5 → 14; число счётчика 13,5 → 14; большая кнопка 14,5 → 15; месяц в истории 17 → 19; название на полке 18 → 19; вкладка 11/500 → 12/600; поле в настройках 15 → 16.
3. Убрать `--ink3` (места переходят на `--ink2`); завести `--ink-press` и `--ink-sub`; дорожку `.bar` перевести на `--soft`.
4. Отступы на шаг 4 px: тело экрана 14 → 12 сверху, промежутки 10 → 12; карточка «Магазины» 14 → 12; заказ 12/14/6 → 12/16/4; заметка 10/12 → 8/12; место 10/12 → 8/12; строки значков 6/14 → 6/16; ширина числа счётчика 46 → 44; колонка цены в истории 78 → 80; пустой экран 28 → 24; строка сборки 54 → 56; радиус большой кнопки 10 → 8.
5. Наведение — в `@media (hover: hover)`; `:active` с переходом 100 ms; блок `prefers-reduced-motion`.
6. `whole()` в `app/web/phone/common.py`: U+202F вместо U+00A0; ₽ от 19 px обернуть в `<span class="r">` на 70 %.
7. Место: суммы столбцом `section` 800, метки с числом товаров у составного места (`place_row` в `cheaper.html`).
8. Полка: «Рекомендуем» — в строку цены, название места больше не обрезается.
9. Перестановка мест: скрипт FLIP с `sessionStorage` и заморозкой под пальцем.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-11-01 | Initial design system created | Создано /design-consultation по живому `phone.css` и шаблонам `app/web/templates/phone`, без исследования конкурентов; превью `design/mobile/system-preview.html` утверждено владельцем |
| 2026-11-01 | Главное — топ-3 места: откуда и что выгоднее заказать | Слова владельца; под это — полка-ответ, суммы столбцом и правило «три места без прокрутки» |
| 2026-11-01 | Только светлая тема | Экран смотрят днём; одна тема — меньше вёрстки и проверок |
| 2026-11-01 | Onest, восемь размеров, заголовок 22 px | Один шрифт с родной кириллицей и ₽; без полуразмеров; строка над ответом дешевле |
| 2026-11-01 | Второй серый #666564 убран | Два серых текста читались одинаково; остался `text-muted` |
| 2026-11-01 | ₽ на 70 % и U+202F от 19 px (риск 1) | Крупная сумма читается одним словом; утверждено по превью |
| 2026-11-01 | Суммы мест столбцом 19/800, метки с числом товаров (риск 2) | Три суммы сравниваются взглядом вниз, видно, сколько берём в каждой сети |
| 2026-11-01 | Перестановка мест FLIP с заморозкой под пальцем (риск 3) | Ответ уточняется на глазах, а не скачет; под пальцем ничего не двигается |
| 2026-11-01 | Наведение только при мыши, нажатие темнит и сжимает | В WebView `:hover` залипает после касания, без `:active` нажатие не ощущается |
| 2026-11-01 | Целые рубли вне перечней, копейки в мини-чеке и строках заказа | Решение «без слов» от 01.11.2026: цена крупно целыми, копейки по касанию |
| 2026-11-01 | Телефон — канон; веб на компьютере переходит на эти токены, когда правят его экраны | `korzina.css` (Rubik, Unbounded, «Цвет магазина») — старая система, отдельно её не развиваем |
