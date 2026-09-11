r"""Точка входа для Streamlit Community Cloud.

Cloud по умолчанию ищет streamlit_app.py в корне репозитория, поэтому здесь
лежит тонкая обёртка: весь интерфейс живёт в app/ui/main.py.

Важно вызывать main() явно, а не полагаться на побочный эффект импорта:
модуль импортируется один раз, а скрипт Streamlit выполняется заново на каждом
действии пользователя — иначе после первого прогона страница станет пустой.

Локально работают оба пути:
    .venv\Scripts\python.exe -m streamlit run streamlit_app.py
    .venv\Scripts\python.exe -m streamlit run app/ui/main.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ui.main import main  # noqa: E402

main()
