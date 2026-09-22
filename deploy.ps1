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

# Инструменты берём из System32 явно, а не по имени из PATH: если скрипт запущен из
# Git Bash, по имени найдётся GNU tar из Git, который принимает «C:» за имя хоста
# («Cannot connect to C: resolve failed»), и ssh из Git вместо штатного.
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$scp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'

if ($Test) {
    $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { Write-Host 'Тесты красные, выкладка отменена' -ForegroundColor Red; exit 1 }
}

# Ровно то, что нужно образу: см. COPY в Dockerfile. Без .git, .venv, тестов, базы и кэша.
$files = @('Dockerfile', '.dockerignore', 'requirements.txt', 'config.yaml', 'README.md', 'DEPLOY.md',
           'app', 'tools', 'docs/grab.min.txt', 'docs/grab-prices.min.txt', 'docs/hand.min.txt',
           'data/fallback_prices.csv', 'data/seed_products.csv', 'data/seed_offers.csv', 'data/seed_receipt.csv')
$archive = Join-Path $env:TEMP 'korzina-src.tgz'
Write-Host 'Упаковываю рабочее дерево...' -ForegroundColor Cyan
& $tar -czf $archive --exclude='__pycache__' --exclude='*.pyc' @files
if ($LASTEXITCODE -ne 0) { throw 'tar не собрал архив' }

# Две выкладки могут идти одновременно (несколько сессий работают в одном репозитории
# и каждая выкладывает по ходу дела). Без очереди вторая удаляет папку исходников
# из-под сборки первой («getwd: no such file or directory»). Поэтому архив у каждой
# выкладки свой, а серверная часть идёт под замком flock — вторая просто ждёт первую.
$stamp = (Get-Date -Format 'yyyyMMddHHmmss') + '-' + (Get-Random -Maximum 9999)
$remote = "/opt/korzina/src-$stamp.tgz"

Write-Host "Заливаю на $Target..." -ForegroundColor Cyan
& $ssh -o BatchMode=yes $Target 'mkdir -p /opt/korzina'
if ($LASTEXITCODE -ne 0) { throw "нет доступа к $Target по ssh; проверьте ~/.ssh/config" }
& $scp -o BatchMode=yes -q $archive "${Target}:$remote"
if ($LASTEXITCODE -ne 0) { throw 'scp не залил архив' }

Write-Host 'Собираю и перезапускаю на сервере...' -ForegroundColor Cyan
$job = "rm -rf /opt/korzina/src && mkdir -p /opt/korzina/src && tar -xzf $remote -C /opt/korzina/src && rm -f $remote && bash /opt/korzina/src/tools/server-deploy.sh"
& $ssh -o BatchMode=yes $Target "flock -w 900 /opt/korzina/deploy.lock sh -c '$job'"
if ($LASTEXITCODE -ne 0) { Write-Host 'Выкладка не удалась, смотрите вывод выше' -ForegroundColor Red; exit 1 }

if ($Seed) {
    Write-Host 'Пересоздаю демо-данные...' -ForegroundColor Cyan
    & $ssh -o BatchMode=yes $Target 'docker exec korzina python tools/seed.py demo'
    if ($LASTEXITCODE -ne 0) { throw 'seed.py завершился с ошибкой' }
}

$server = ((& $ssh -G $Target | Select-String '^hostname ').Line -split ' ')[1]
Write-Host "Готово: http://$server/" -ForegroundColor Green
