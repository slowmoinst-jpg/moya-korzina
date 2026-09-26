param(
    [string]$Target = 'korzina'
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot\..

$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$scp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'

Write-Host "Упаковываю android/app..." -ForegroundColor Cyan
$archive = Join-Path $env:TEMP 'android-src.tgz'
& $tar -czf $archive -C android/app .
if ($LASTEXITCODE -ne 0) { throw 'tar не собрал архив' }

$stamp = (Get-Date -Format 'yyyyMMddHHmmss') + '-' + (Get-Random -Maximum 9999)
$remote = "/opt/korzina/android-$stamp.tgz"

Write-Host "Заливаю на $Target..." -ForegroundColor Cyan
& $scp -o BatchMode=yes -q $archive "${Target}:$remote"
if ($LASTEXITCODE -ne 0) { throw 'scp не залил архив' }

Write-Host "Собираю APK в Docker на сервере..." -ForegroundColor Cyan
$buildCmd = "mkdir -p /opt/korzina/android-build && tar -xzf $remote -C /opt/korzina/android-build && rm -f $remote && docker run --rm -v /opt/korzina/android-build:/src korzina-apk && cp /opt/korzina/android-build/app/build/outputs/apk/debug/app-debug.apk /opt/korzina/src/app/web/static/moya-korzina.apk"
& $ssh -n -o BatchMode=yes $Target "sh -c '$buildCmd'"
if ($LASTEXITCODE -ne 0) { throw 'Сборка APK в Docker не удалась' }

Write-Host "Скачиваю готовый APK локально..." -ForegroundColor Cyan
& $scp -o BatchMode=yes -q "${Target}:/opt/korzina/src/app/web/static/moya-korzina.apk" app/web/static/moya-korzina.apk

Write-Host "Готово! APK обновлён: http://200.169.191.137/static/moya-korzina.apk" -ForegroundColor Green
