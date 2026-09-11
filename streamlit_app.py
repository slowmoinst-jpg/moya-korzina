"""Точка входа для Streamlit Community Cloud.

Cloud по умолчанию ищет streamlit_app.py в корне репозитория, поэтому здесь
лежит тонкая обёртка: весь интерфейс живёт в app/ui/main.py, импорт этого
модуля и запускает приложение.

Локально можно запускать любым из двух способов:
    .venv\\Scripts\\python.exe -m streamlit run streamlit_app.py
    .venv\\Scripts\\python.exe -m streamlit run app/ui/main.py
"""
import app.ui.main  # noqa: F401  — импорт выполняет модуль и рисует интерфейс
