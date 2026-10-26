param(
    [string]$Target = 'korzina'
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot\..

$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$scp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'

# Версия из build.gradle уезжает рядом с APK в data/apk/version.txt: по ней сайт
# показывает старым приложениям плашку «Вышла версия …» (app/web/apk.py).
$gradleContent = Get-Content -Raw android/app/app/build.gradle
if ($gradleContent -match 'versionName\s+"(\d+(?:\.\d+)*)"') {
    $appVersion = $Matches[1]
} else {
    throw 'Не удалось определить versionName из android/app/app/build.gradle'
}

Write-Host "Упаковываю android/app..." -ForegroundColor Cyan
$archive = Join-Path $env:TEMP 'android-src.tgz'
# КАТАЛОГИ СБОРКИ НЕ ВЕЗЁМ (19.10.2026). В android/app лежит app/build от местных
# прогонов, и вместе с ним на сервер уезжал готовый APK. Gradle на сервере принимал
# его за своё прежнее изделие и дописывал поверх: в APK оставались «дыры» от прошлой
# упаковки — 136 КБ вместо 71 КБ. Собирать надо только из исходников.
& $tar -czf $archive --exclude=app/build --exclude=.gradle -C android/app .
if ($LASTEXITCODE -ne 0) { throw 'tar не собрал архив' }

$stamp = (Get-Date -Format 'yyyyMMddHHmmss') + '-' + (Get-Random -Maximum 9999)
$remote = "/opt/korzina/android-$stamp.tgz"

# АДРЕС СЕРВЕРА ЗАШИВАЕТСЯ ПРИ СБОРКЕ, ИНАЧЕ ПРИЛОЖЕНИЕ БЕСПОЛЕЗНО. build.gradle берёт
# его из -PkorzinaServer или переменной KORZINA_SERVER; без них в APK уезжает пустая
# строка, и приложение показывает «Адрес сервера не задан при сборке». До 18.10.2026
# этот скрипт адрес не передавал вовсе — то есть кнопка «Скачать APK» раздавала сборку,
# которая никуда не ходит. Хост берём из ssh-псевдонима: там боевой сервер.
#
# С 1.18 (26.10.2026) АДРЕС — HTTPS-ИМЯ. Приложение не пускает открытый трафик к серверу
# (res/xml/network_security_config.xml), и сборка с http://<IP> никуда бы не ходила. Имя
# то же, что в /etc/caddy/conf.d/korzina.caddy на сервере (DEPLOY.md). Сборка под эмулятор
# с этим скриптом уехала бы в боевой data/apk вместе с version.txt; эмулятор собирается
# местно (android/app/README.md, «Как собрать»).
$serverHost = ((& $ssh -G $Target | Select-String '^hostname ').Line -split ' ')[1]
$serverUrl = "https://korzina.$serverHost.sslip.io"
Write-Host "Версия приложения: $appVersion, адрес сервера: $serverUrl" -ForegroundColor Cyan

Write-Host "Заливаю на $Target..." -ForegroundColor Cyan
& $scp -o BatchMode=yes -q $archive "${Target}:$remote"
if ($LASTEXITCODE -ne 0) { throw 'scp не залил архив' }

# КУДА КЛАСТЬ ГОТОВУЮ СБОРКУ — В ТОМ ДАННЫХ, А НЕ В ИСХОДНИКАХ. Кнопка «Скачать APK»
# (app/web/apk.py) отдаёт data/apk/moya-korzina.apk из тома korzina-data; файл в
# app/web/static — запасной и живёт в образе, то есть пропадает при каждой пересборке
# и заменяется тем, что было в рабочем дереве. 18.10.2026 это и вышло: приложение
# собралось новое (66 909 байт), а сервер раздавал старые 21 025 из тома.
#
# КЛЮЧ ПОДПИСИ — ТОТ ЖЕ, ЧТО У СБОРОК НА МАШИНЕ (19.10.2026). Раньше контейнер сборщика
# на сервере поднимался без ~/.android: Gradle рожал новый debug.keystore на каждый
# прогон, и каждая скачанная сборка подписывалась новым ключом — поверх прежней она не
# ставилась, приходилось удалять приложение и терять входы в магазины (замер 03.10.2026).
# Теперь /opt/korzina/.android (копия постоянного ключа владельца, лежит вне репозитория)
# монтируется в контейнер: скачанная сборка ставится поверх прежней и сохраняет входы.
#
# КАТАЛОГ СБОРКИ — С НУЛЯ. Он переживает прогоны, и инкрементная упаковка однажды
# оставила в APK 65 КБ мусора от прошлой сборки (136 КБ вместо 71 КБ, замер 19.10.2026).
# Чистый каталог — чистый APK; зависимостей у приложения нет, сборка и так быстрая.
$buildCmd = "rm -rf /opt/korzina/android-build && mkdir -p /opt/korzina/android-build /opt/korzina/.android && tar -xzf $remote -C /opt/korzina/android-build && rm -f $remote && docker run --rm -e KORZINA_SERVER=$serverUrl -v /opt/korzina/.android:/root/.android -v /opt/korzina/android-build:/src korzina-apk && cp /opt/korzina/android-build/app/build/outputs/apk/debug/app-debug.apk /opt/korzina/src/app/web/static/moya-korzina.apk && cp /opt/korzina/android-build/app/build/outputs/apk/debug/app-debug.apk /opt/korzina/apk-$stamp && docker exec $Target mkdir -p /app/data/apk && docker cp /opt/korzina/apk-$stamp ${Target}:/app/data/apk/moya-korzina.apk && rm -f /opt/korzina/apk-$stamp && echo $appVersion > /opt/korzina/version-$stamp.txt && docker cp /opt/korzina/version-$stamp.txt ${Target}:/app/data/apk/version.txt && rm -f /opt/korzina/version-$stamp.txt && docker exec $Target ls -l /app/data/apk/moya-korzina.apk /app/data/apk/version.txt"
& $ssh -n -o BatchMode=yes $Target "sh -c '$buildCmd'"
if ($LASTEXITCODE -ne 0) { throw 'Сборка APK в Docker не удалась' }

Write-Host "Скачиваю готовый APK локально..." -ForegroundColor Cyan
# Локальная копия — в android/*.apk: этот путь в .gitignore. В репозиторий свежий APK
# не кладём никогда: в нём зашит адрес сервера, а репозиторий публичный (android/app/README.md).
& $scp -o BatchMode=yes -q "${Target}:/opt/korzina/src/app/web/static/moya-korzina.apk" android/moya-korzina-debug.apk

Write-Host "Готово! APK обновлён на сервере: /static/moya-korzina.apk" -ForegroundColor Green
