# Запуск оптимизатора продуктовой корзины одной командой.
# Использование:  .\run.ps1            — поднять UI
#                 .\run.ps1 -Seed      — пересоздать демо-данные и поднять UI
#                 .\run.ps1 -Test      — прогнать тесты и выйти
param(
    [switch]$Seed,
    [switch]$Test,
    [int]$Port = 8501
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Host 'Виртуальное окружение не найдено, создаю...' -ForegroundColor Yellow
    py -3.12 -m venv .venv
    & $py -m pip install -q --upgrade pip
    & $py -m pip install -q -r requirements.txt
}

if ($Test) {
    & $py -m pytest -q
    exit $LASTEXITCODE
}

& $py -c "from app.db import init_db; init_db()"

if ($Seed) {
    & $py tools/seed.py
}

Write-Host "UI: http://localhost:$Port" -ForegroundColor Green
& $py -m streamlit run app/ui/main.py --server.port $Port
