# Выкладка «Моей корзины» на сервер одной командой.
# Использование:  .\deploy.ps1            — упаковать рабочее дерево, собрать образ на сервере, перезапустить
#                 .\deploy.ps1 -Test      — сначала прогнать тесты, выкладывать только если зелёные
#                 .\deploy.ps1 -Seed      — после выкладки пересоздать демо-данные в базе на сервере
# Сервер задаётся псевдонимом в ~/.ssh/config (Host korzina): адрес, пользователь и ключ там,
# в репозитории их нет. Серверную часть делает tools/server-deploy.sh.
param(
    [switch]$Test,
    [switch]$Seed,
    [string]$Target = 'korzina'
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if ($Test) {
    $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { Write-Host 'Тесты красные, выкладка отменена' -ForegroundColor Red; exit 1 }
}

# Ровно то, что нужно образу: см. COPY в Dockerfile. Без .git, .venv, тестов, базы и кэша.
$files = @('Dockerfile', '.dockerignore', 'requirements.txt', 'config.yaml', 'README.md', 'DEPLOY.md',
           '.streamlit/config.toml', 'app', 'tools',
           'data/fallback_prices.csv', 'data/seed_products.csv', 'data/seed_offers.csv', 'data/seed_receipt.csv')
$archive = Join-Path $env:TEMP 'korzina-src.tgz'
Write-Host 'Упаковываю рабочее дерево...' -ForegroundColor Cyan
& tar.exe -czf $archive --exclude='__pycache__' --exclude='*.pyc' @files
if ($LASTEXITCODE -ne 0) { throw 'tar не собрал архив' }

Write-Host "Заливаю на $Target..." -ForegroundColor Cyan
& ssh $Target 'mkdir -p /opt/korzina'
if ($LASTEXITCODE -ne 0) { throw "нет доступа к $Target по ssh; проверьте ~/.ssh/config" }
& scp -q $archive "${Target}:/opt/korzina/src.tgz"
if ($LASTEXITCODE -ne 0) { throw 'scp не залил архив' }

Write-Host 'Собираю и перезапускаю на сервере...' -ForegroundColor Cyan
& ssh $Target 'rm -rf /opt/korzina/src && mkdir -p /opt/korzina/src && tar -xzf /opt/korzina/src.tgz -C /opt/korzina/src && bash /opt/korzina/src/tools/server-deploy.sh'
if ($LASTEXITCODE -ne 0) { Write-Host 'Выкладка не удалась, смотрите вывод выше' -ForegroundColor Red; exit 1 }

if ($Seed) {
    Write-Host 'Пересоздаю демо-данные...' -ForegroundColor Cyan
    & ssh $Target 'docker exec korzina python tools/seed.py'
    if ($LASTEXITCODE -ne 0) { throw 'seed.py завершился с ошибкой' }
}

$server = ((& ssh -G $Target | Select-String '^hostname ').Line -split ' ')[1]
Write-Host "Готово: http://$server/" -ForegroundColor Green
