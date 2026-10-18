# Сохранённые подмены третьего круга проверки единого каталога

Копии файлов `fixplug*.py` из временной папки сессии единого каталога (`scratchpad/verify3_<метка>/`), где скептики проверяли исправления подменой функций в памяти процесса. Описание находок, проверенных вариантов и того, чего делать нельзя, — в [../edinyi-katalog-dorabotki-2026-10-18.md](../edinyi-katalog-dorabotki-2026-10-18.md). Ни одно исправление в приложении не применено.

| Папка | Находки (факты GBrain) |
|---|---|
| `verify3_negsp1`, `verify3_negsp2`, `verify3_negsp3` | #3776, #3779: «НЕ ГАЗ», «СЛ/ГАЗ» |
| `verify3_negdot_sk1` | #3780, #3781, #3782: «Н.ГАЗ», «Б.САХ» |
| `verify3_merge_sk2` | #3783, #3784: импорт сводит противоположные разновидности |
| `verify3_screens`, `verify3_stubrej_sk1`, `verify3_stubrej_sk2`, `verify3_stubrej_sk3` | #3769, #3797, #3800: подтверждение заглушки снимает отказ |
| `verify3_packwords_sk1` (вместе с `variants.py`), `verify3_packwords_sk2`, `verify3_upakword_sk1` | #3785, #3786: упаковка словами |
| `verify3_plusnopack_sk1` | `fixplug_counted.py` — #3788 (яйца); `fixplug_refined.py`, `fixplug_durable.py` — #3801 («＋» без фасовки) |
| `verify3_zmzhves_sk1` | #3789: «с змж», «весовые» |
| `verify3_btnrun_sk1` | #3803: кнопка и `_RUNNING` |
| `verify3_stubdrop_sk1` (вместе с `fix_templates/`) | #3804: подписи и действия для заглушек |
| `verify3_sk1_itemlast`, `verify3_weakwrite_sk1` | #3768, #3775: чистка `item_last`, запись `_WEAK` |
| `verify3_revdisp_sk2` | #3770, #3774: варианты `link._disputed` |
| `verify3_sameitem_sk1` | #3798: исправлять не нужно, оставлено для протокола |

Соответствие выведено по названиям папок и докстрингам; точные ссылки на папки — в строках «Источник» приложения основного документа.

Подключение (из корня репозитория):

```bash
PYTHONPATH=docs/design/edinyi-katalog-dorabotki-2026-10-18/verify3_negsp2 python -m pytest -p fixplug tests/test_unified_catalog_guards.py -q
```

Режим подмены задаёт переменная окружения, имя которой указано в начале файла. Пути к рабочей копии заменены на «.». Файлы написаны против коммита 497e86f.
