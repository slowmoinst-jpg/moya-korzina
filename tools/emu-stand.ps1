#Requires -Version 7
<#
    Стенд эмулятора «Моей корзины» одной командой.

    ЗАЧЕМ. Живой прогон сборки корзины Магнита состоит из шестнадцати шагов, и почти
    каждый из них уже сделан кем-то раньше: JDK и SDK стоят, эмулятор поднят владельцем,
    на 8611 слушает местный сервер, APK собран и даже установлен. Команда, которая
    делает всё заново, ломала бы работу владельца: второй экземпляр эмулятора, вторая
    копия сервера, лишняя сборка на полторы минуты. Поэтому каждый шаг сначала
    ПРОВЕРЯЕТ, сделан ли он, и только когда не сделан — делает. Повторный запуск
    ничего не ломает.

    План: docs/plans/sborka-korziny-magnit-emulyator-2026-10-18.md, разделы 2 (шаги),
    3 (интерфейс стенда), 6 (риски) и «Поправки оркестратора» — поправки главнее
    таблиц: эмулятор уже поднят (второй не поднимаем, снимок AVD не удаляем,
    -ColdBoot по умолчанию не применяем), порог наряда — 4 строки со ссылками Магнита.

    ЧЕЛОВЕЧЕСКИЙ ШАГ. Вход в magnit.ru внутри окна приложения человек проходит сам:
    номер, код из СМС, капча, выбор адреса. Стенд его не имитирует и полей входа не
    касается. Перед шагом 11 он печатает «НУЖЕН ЧЕЛОВЕК», снимает экран и либо ждёт
    Enter (-WaitHuman), либо выходит с кодом 20 и подсказкой продолжить с шага 11.
    После подтверждения проверяет не на слово: смотрит через отладочный протокол
    окна ТОЛЬКО имя куки mg_at. Значение куки не читается, не печатается и не
    сохраняется.

    ЧТО ГДЕ ЛЕЖИТ. Сырые файлы прогона (журнал сервера, logcat по тегу Korzina, снимки
    экрана, сырые ответы) — только в %LOCALAPPDATA%\korzina-emu\runs\<дата-время>\.
    В репозиторий ничего сырого не попадает: там номера, адреса и куки владельца.

    КОДЫ ВЫХОДА. 0 — готово; 10 — сбой стенда (чего-то нет или что-то не сошлось);
    20 — нужен человек; 30 — корзина не наполнилась (вердикт провала).

    ПРИМЕРЫ.
        pwsh -File tools/emu-stand.ps1 -Until 10             только стенд, без прогона
        pwsh -File tools/emu-stand.ps1                       весь прогон до вердикта
        pwsh -File tools/emu-stand.ps1 -From 11 -WaitHuman   продолжить после входа
#>
[CmdletBinding()]
param(
    # Имя AVD. Владелец держит его поднятым — стенд только проверяет.
    [string]$Avd = 'korzina',

    # Порт местного сервера (конфигурация web-emu из .claude/launch.json).
    [int]$Port = 8611,

    # Адрес, который зашивается в APK. 10.0.2.2 — это машина с сервером глазами эмулятора.
    [string]$Server = '',

    # С какого шага начинать (после человека — обычно 11).
    [int]$From = 1,

    # На каком шаге остановиться. -Until 10 — только стенд.
    [int]$Until = 16,

    # Разрешить ставить недостающее (JDK, пакеты SDK). Без ключа — только проверка и подсказка:
    # молча тянуть полтора гигабайта нельзя.
    [switch]$InstallMissing,

    # Собрать APK заново, даже если лежащий свежий.
    [switch]$Rebuild,

    # Разрешить удалить приложение, если подпись не совпала. Удаление стирает вход в Магнит
    # в окне приложения — поэтому только явным ключом.
    [switch]$AllowUninstall,

    # Ждать человека в консоли (Enter), а не выходить с кодом 20.
    [switch]$WaitHuman,

    # Загрузить эмулятор без снимка. По умолчанию НЕ применяется: в снимке живёт прежний вход.
    [switch]$ColdBoot
)

$ErrorActionPreference = 'Stop'

# Кодировка. Питон на этой машине пишет в поток вывода в кодировке консоли (cp866), и
# прочитанный как UTF-8 текст превращается в кашу: «НАРЯД magnit: строк 4» становится
# «����� magnit: ����� 4». Отсюда две меры: дочерним питонам велим писать UTF-8, а сами
# читаем их вывод как UTF-8. Разбирать человеческие строки стенд всё равно не имеет права —
# порог наряда берётся из JSON, который инструмент кладёт ключом --out, а не из текста.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ── Коды выхода. Одни и те же для человека и для того, кто запускает стенд из скрипта.
$EXIT_OK = 0
$EXIT_BREAK = 10
$EXIT_HUMAN = 20
$EXIT_NOT_FILLED = 30

$APP_ID = 'ru.moyakorzina.app'
$APP_ACTIVITY = 'ru.moyakorzina.app/.MainActivity'
$APK_REL = 'android\app\app\build\outputs\apk\debug\app-debug.apk'
$APK_META_REL = 'android\app\app\build\outputs\apk\debug\output-metadata.json'

if (-not $Server) { $Server = "http://10.0.2.2:$Port" }

# ─────────────────────────────────────────────────────────────────────────────
# Проверка адреса, который зашивается в APK. Стенд поднимает сервер на 127.0.0.1:$Port,
# и эмулятор видит эту машину как 10.0.2.2. Другой адрес — это либо опечатка, либо
# боевой сервер: в APK его зашивать нельзя (том данных владельца, живой Магнит), а
# стенд потом ждал бы 10.0.2.2:порт и звал человека. Отказываем сразу, до всякой работы.
# ─────────────────────────────────────────────────────────────────────────────

function Get-ServerProblem {
    param([string]$Address, [int]$Port)
    $u = $null
    try { $u = [uri]$Address } catch { return ("Адрес «{0}» не разбирается как адрес." -f $Address) }
    if ($u.Scheme -ne 'http') {
        return ("Схема «{0}» не годится: стенд поднимает открытый http на 127.0.0.1:{1}." -f $u.Scheme, $Port)
    }
    if ($u.Host -notin @('10.0.2.2', '127.0.0.1', 'localhost')) {
        return ("Хост «{0}» не годится: эмулятор видит машину с сервером как 10.0.2.2, и стенд ждёт 10.0.2.2:{1}. Чужой адрес в APK зашивать нельзя." -f $u.Host, $Port)
    }
    if ($u.Port -ne $Port) {
        return ("Порт {0} не совпадает с -Port {1}: приложение искало бы сервер не там, где он слушает." -f $u.Port, $Port)
    }
    if ($u.AbsolutePath -and $u.AbsolutePath -ne '/') {
        return ("Путь «{0}» в адресе сервера не нужен: приложение само ходит по своим путям." -f $u.AbsolutePath)
    }
    return ''
}

$serverProblem = Get-ServerProblem -Address $Server -Port $Port
if ($serverProblem) {
    Write-Host ''
    Write-Host 'ОСТАНОВКА: адрес сервера для APK не годится (код 10).' -ForegroundColor Red
    Write-Host $serverProblem -ForegroundColor Red
    Write-Host ("Разрешены: http://10.0.2.2:{0}, http://127.0.0.1:{0}, http://localhost:{0}" -f $Port) -ForegroundColor Gray
    exit $EXIT_BREAK
}

# ─────────────────────────────────────────────────────────────────────────────
# Вывод и журнал. Всё, что стенд говорит человеку, ложится ещё и в stand.log прогона:
# разговор в консоли исчезает, а разбор прогона без журнала невозможен.
# ─────────────────────────────────────────────────────────────────────────────

$script:Repo = Split-Path -Parent $PSScriptRoot
$script:RunDir = $null
$script:RunStarted = Get-Date
$script:StepsDone = New-Object System.Collections.Generic.List[string]
$script:StepsSkipped = New-Object System.Collections.Generic.List[string]

function Ensure-RunDir {
    if ($script:RunDir) { return $script:RunDir }
    $stamp = $script:RunStarted.ToString('yyyyMMdd-HHmmss')
    $dir = Join-Path (Join-Path $env:LOCALAPPDATA 'korzina-emu\runs') $stamp
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $dir 'screens') -Force | Out-Null
    # Пустой файл для ввода долгоживущих помощников (сервер, эмулятор). Без него они
    # наследуют поток ввода стенда, и запустивший его процесс висит, хотя стенд уже
    # отработал: дескриптор остаётся открытым у чужого процесса.
    New-Item -ItemType File -Path (Join-Path $dir 'empty.stdin') -Force | Out-Null
    $script:RunDir = $dir
    return $dir
}

function Get-JsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $text = ''
    try {
        # Сначала строгий UTF-8: так пишет JSON сам инструмент.
        $strict = New-Object System.Text.UTF8Encoding($false, $true)
        $text = $strict.GetString($bytes)
    } catch {
        # Если файл всё же записан в кодировке консоли — читаем как cp1251, а не теряем его.
        $text = [System.Text.Encoding]::GetEncoding(1251).GetString($bytes)
    }
    try { return $text | ConvertFrom-Json } catch { return $null }
}

# Адрес для журнала: схема, хост и путь. Строку запроса отбрасываем нарочно — в адресах
# входа и проверок браузера бывают code, state и токены, а журнал прогона читают люди и
# складывают рядом с доказательствами.
function Get-SafeUrl {
    param([string]$Url)
    if (-not $Url) { return '' }
    try {
        $u = [uri]$Url
        if (-not $u.Host) { return $u.Scheme }
        return ("{0}://{1}{2}" -f $u.Scheme, $u.Authority, $u.AbsolutePath)
    } catch {
        return '(адрес не разобрался)'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Замок от второго стенда. Два стенда рвут проброс 9222 друг у друга и оба жмут
# «Собрать» — это двойная нагрузка на Магнит (а в рамке сбора сказано: один поток на
# сеть) и смешанные вердикты. Замок живёт вне репозитория и снимается в конце прогона.
# ─────────────────────────────────────────────────────────────────────────────

function Get-StandLockPath {
    $dir = Join-Path $env:LOCALAPPDATA 'korzina-emu'
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    return (Join-Path $dir 'stand.lock')
}

function Enter-StandLock {
    $path = Get-StandLockPath
    $mine = [ordered]@{ pid = $PID; at = (Get-Date).ToString('s'); run = $script:RunDir } |
        ConvertTo-Json -Compress
    # Занимаем замок созданием файла: два запуска одновременно не пройдут оба.
    try {
        $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($mine)
            $stream.Write($bytes, 0, $bytes.Length)
        } finally { $stream.Dispose() }
        $script:LockPath = $path
        return
    } catch [System.IO.IOException] {
        # Замок есть. Чужой живой — отказ; оставшийся от упавшего прогона — забираем.
        $old = Get-JsonFile $path
        $oldPid = if ($old -and $old.pid) { [int]$old.pid } else { 0 }
        if ($oldPid -gt 0 -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
            Stop-Break ("Второй стенд уже идёт (pid {0}, начат {1}). Два стенда рвут проброс 9222 друг у друга и вдвоём жмут «Собрать» — это двойная нагрузка на Магнит. Дождись его окончания или останови его: Stop-Process -Id {0}" -f $oldPid, $old.at)
        }
        Write-Warn ("Замок прошлого прогона остался (pid {0} уже не работает) — забираю себе." -f $oldPid)
        $mine | Set-Content -LiteralPath $path -Encoding utf8
        $script:LockPath = $path
    }
}

function Exit-StandLock {
    if (-not $script:LockPath) { return }
    if (-not (Test-Path $script:LockPath)) { return }
    # Снимаем только свой замок: чужой трогать нельзя, его снимет его хозяин.
    $cur = Get-JsonFile $script:LockPath
    if ($cur -and [int]$cur.pid -eq $PID) {
        Remove-Item -LiteralPath $script:LockPath -Force -ErrorAction SilentlyContinue
    }
}

function Write-Log {
    param([string]$Text = '', [string]$Color = 'Gray')
    Write-Host $Text -ForegroundColor $Color
    Add-Content -LiteralPath (Join-Path (Ensure-RunDir) 'stand.log') -Value $Text -Encoding utf8
}

function Write-Step {
    param([int]$N, [string]$Name)
    Write-Log ''
    Write-Log ("── Шаг {0}: {1}" -f $N, $Name) 'Cyan'
}

function Write-Ok    { param([string]$T) Write-Log ("   ок:     " + $T) 'Green' }
function Write-Skip  { param([string]$T) Write-Log ("   уже:    " + $T) 'DarkGray' }
function Write-Warn  { param([string]$T) Write-Log ("   важно:  " + $T) 'Yellow' }

# ─────────────────────────────────────────────────────────────────────────────
# Остановки. Свой код выхода несёт само исключение: иначе «нужен человек» и «сбой
# стенда» слились бы в один ноль и человек не понял бы, что делать дальше.
# ─────────────────────────────────────────────────────────────────────────────

function Stop-Stand {
    param([int]$Code, [string]$Why)
    $e = New-Object System.Management.Automation.RuntimeException $Why
    $e.Data['stand_exit'] = $Code
    throw $e
}

function Stop-Break  { param([string]$Why) Stop-Stand -Code $script:EXIT_BREAK -Why $Why }
function Stop-Human  { param([string]$Why) Stop-Stand -Code $script:EXIT_HUMAN -Why $Why }

function Get-ResumeHint {
    param([int]$Step)
    $parts = @('pwsh -File tools/emu-stand.ps1', "-Avd $Avd", "-Port $Port", "-Server $Server", "-From $Step")
    if ($Script:InstallMissing) { $parts += '-InstallMissing' }
    return ($parts -join ' ')
}

# ─────────────────────────────────────────────────────────────────────────────
# Запуск чужих программ. Ни один сбой внешней команды не должен валить стенд молча:
# код возврата и вывод ловим сами, вывод кладём в файл прогона.
# ─────────────────────────────────────────────────────────────────────────────

function Invoke-Tool {
    param(
        [string]$Exe,
        [string[]]$ArgList = @(),
        [string]$LogFile = '',
        [int]$TimeoutSec = 0
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = ''
    try {
        if ($TimeoutSec -gt 0) {
            # Долгая команда с ограничением по времени: ждём файлом-признаком, а не опросом вывода.
            $tmpOut = [System.IO.Path]::GetTempFileName()
            $tmpErr = [System.IO.Path]::GetTempFileName()
            try {
                $proc = Start-Process -FilePath $Exe -ArgumentList $ArgList -NoNewWindow -PassThru `
                    -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
            } catch {
                Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
                throw
            }
            if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
                try { $proc.Kill($true) } catch { }
                $out = (Get-Content -LiteralPath $tmpOut -Raw -ErrorAction SilentlyContinue) +
                       (Get-Content -LiteralPath $tmpErr -Raw -ErrorAction SilentlyContinue)
                Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
                return [pscustomobject]@{ Код = 124; Вывод = ($out | Out-String) }
            }
            $out = (Get-Content -LiteralPath $tmpOut -Raw -ErrorAction SilentlyContinue) +
                   (Get-Content -LiteralPath $tmpErr -Raw -ErrorAction SilentlyContinue)
            $code = $proc.ExitCode
            Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
        } else {
            $out = & $Exe @ArgList 2>&1 | Out-String
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = 0 }
        }
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($LogFile) {
        "### $Exe $($ArgList -join ' ')" | Add-Content -LiteralPath $LogFile -Encoding utf8
        $out | Add-Content -LiteralPath $LogFile -Encoding utf8
    }
    return [pscustomobject]@{ Код = $code; Вывод = $out }
}

function Get-HttpProbe {
    param([string]$Url, [int]$TimeoutSec = 5)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -SkipHttpErrorCheck -MaximumRedirection 5
        return [pscustomobject]@{ Код = [int]$r.StatusCode; Тело = [string]$r.Content }
    } catch {
        return [pscustomobject]@{ Код = 0; Тело = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $prev
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Подготовка: где репозиторий, где SDK, где JDK, какой инструмент доступен.
#
# Windows отдаёт часть каталогов внутрь пакета приложения (MSIX-перенаправление):
# рядом с обычным %LOCALAPPDATA%\Android\Sdk живёт его копия, и запущенный adb
# оказался именно оттуда. Поэтому SDK ищем по всем разумным корням, а не в одном.
# ─────────────────────────────────────────────────────────────────────────────

function Get-SdkCandidates {
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($name in @('ANDROID_SDK_ROOT', 'ANDROID_HOME')) {
        $v = [Environment]::GetEnvironmentVariable($name)
        if ($v -and (Test-Path $v)) { $list.Add($v) }
    }
    $def = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
    if (Test-Path $def) { $list.Add($def) }
    $pkgs = Join-Path $env:LOCALAPPDATA 'Packages'
    if (Test-Path $pkgs) {
        Get-ChildItem $pkgs -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $p = Join-Path $_.FullName 'LocalCache\Local\Android\Sdk'
            if (Test-Path $p) { $list.Add($p) }
        }
    }
    return ($list | Select-Object -Unique)
}

function Find-SdkPiece {
    param([string]$Rel)
    foreach ($root in Get-SdkCandidates) {
        $p = Join-Path $root $Rel
        if (Test-Path $p) { return [pscustomobject]@{ Корень = $root; Путь = $p } }
    }
    return $null
}

function Get-JavaMajor {
    param([string]$JavaExe)
    if (-not (Test-Path $JavaExe)) { return 0 }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $txt = & $JavaExe -version 2>&1 | Out-String
    } catch {
        return 0
    } finally {
        $ErrorActionPreference = $prev
    }
    $m = [regex]::Match($txt, 'version "?(\d+)')
    if ($m.Success) { return [int]$m.Groups[1].Value }
    return 0
}

function Get-JdkCandidatePaths {
    $list = New-Object System.Collections.Generic.List[string]
    if ($env:JAVA_HOME) { $list.Add($env:JAVA_HOME) }
    $tmpJdk = Join-Path $env:TEMP 'jdk17'
    if (Test-Path $tmpJdk) {
        Get-ChildItem $tmpJdk -Directory -ErrorAction SilentlyContinue | ForEach-Object { $list.Add($_.FullName) }
    }
    foreach ($base in @(
            (Join-Path $env:ProgramFiles 'Eclipse Adoptium'),
            (Join-Path $env:ProgramFiles 'Microsoft'),
            (Join-Path $env:ProgramFiles 'Java'),
            (Join-Path ${env:ProgramFiles(x86)} 'Java'),
            (Join-Path $env:ProgramFiles 'Android\Android Studio\jbr'))) {
        if ($base -and (Test-Path $base)) {
            $self = Join-Path $base 'bin\java.exe'
            if (Test-Path $self) { $list.Add($base) }
            Get-ChildItem $base -Directory -ErrorAction SilentlyContinue | ForEach-Object { $list.Add($_.FullName) }
        }
    }
    return ($list | Select-Object -Unique)
}

function Ensure-Jdk {
    if ($script:JdkHome) { return $script:JdkHome }
    foreach ($dir in Get-JdkCandidatePaths) {
        $exe = Join-Path $dir 'bin\java.exe'
        $major = Get-JavaMajor $exe
        if ($major -ge 17) {
            $script:JdkHome = $dir
            $script:JdkMajor = $major
            return $dir
        }
    }
    if ($InstallMissing) {
        Write-Warn 'JDK 17 не найден ни в JAVA_HOME, ни в %TEMP%\jdk17, ни в Program Files.'
        Write-Warn 'Пробую поставить winget-пакетом Microsoft.OpenJDK.17 — это может занять несколько минут.'
        $wg = Get-Command winget -ErrorAction SilentlyContinue
        if ($wg) {
            Invoke-Tool $wg.Source @('install', '--id', 'Microsoft.OpenJDK.17', '--silent',
                '--accept-package-agreements', '--accept-source-agreements') `
                (Join-Path (Ensure-RunDir) 'winget-jdk.log') 900 | Out-Null
            foreach ($dir in Get-JdkCandidatePaths) {
                $major = Get-JavaMajor (Join-Path $dir 'bin\java.exe')
                if ($major -ge 17) { $script:JdkHome = $dir; $script:JdkMajor = $major; return $dir }
            }
        } else {
            Write-Warn 'winget на машине нет — поставить JDK сам не могу.'
        }
    }
    Stop-Break @'
JDK 17+ не найден. Стенду он нужен для sdkmanager и для сборки.
Что делать: положить JDK 17 в %TEMP%\jdk17 (как уже сделано у владельца) или задать
JAVA_HOME на каталог с JDK 17 и повторить запуск. Ключ -InstallMissing пробует
поставить его через winget.
'@
}

function Install-SdkPieces {
    param([string[]]$Names)
    $jdk = Ensure-Jdk
    $sdkman = Find-SdkPiece 'cmdline-tools\latest\bin\sdkmanager.bat'
    if (-not $sdkman) {
        Stop-Break "В SDK нет cmdline-tools\latest\bin\sdkmanager.bat — ставить пакеты нечем."
    }
    # sdkmanager — это сценарий Java, он берёт JDK из JAVA_HOME.
    $env:JAVA_HOME = $jdk
    $env:ANDROID_SDK_ROOT = $sdkman.Корень
    $env:ANDROID_HOME = $sdkman.Корень
    $env:PATH = (Join-Path $jdk 'bin') + ';' + $env:PATH
    $log = Join-Path (Ensure-RunDir) 'sdkmanager.log'
    Write-Log ("   ставлю пакеты: " + ($Names -join ', ')) 'Yellow'
    # Лицензии спрашивают «y» на каждый пакет: отвечаем заранее, иначе установка повиснет.
    $answers = ("y`n" * 40)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $answers | & $sdkman.Путь --sdk_root=$($sdkman.Корень) --licenses *>&1 |
            Add-Content -LiteralPath $log -Encoding utf8
    } finally { $ErrorActionPreference = $prev }
    $r = Invoke-Tool $sdkman.Путь @('--sdk_root=' + $sdkman.Корень, '--install') $log 1800
    if ($r.Код -ne 0) {
        Write-Warn "sdkmanager вернул код $($r.Код) — смотри $log"
    }
}

function Ensure-Sdk {
    if ($script:SdkReady) { return }
    $pieces = [ordered]@{
        'cmdline-tools (sdkmanager)'       = 'cmdline-tools\latest\bin\sdkmanager.bat'
        'platform-tools (adb)'             = 'platform-tools\adb.exe'
        'emulator'                         = 'emulator\emulator.exe'
        'system-images;android-34;google_apis;x86_64' = 'system-images\android-34\google_apis\x86_64\system.img'
    }
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($name in $pieces.Keys) {
        $found = Find-SdkPiece $pieces[$name]
        if ($found) {
            Write-Skip ("{0} — {1}" -f $name, $found.Путь)
        } else {
            $missing.Add($name)
        }
    }
    if ($missing.Count -gt 0) {
        if (-not $InstallMissing) {
            Stop-Break (@'
В Android SDK не хватает: {0}
Что делать: поставить это самому (sdkmanager) или повторить запуск с ключом
-InstallMissing. Стенд молча не тянет пакеты: system-image весит больше гигабайта.
'@ -f ($missing -join ', '))
        }
        Install-SdkPieces $missing.ToArray()
        foreach ($name in $missing) {
            $found = Find-SdkPiece $pieces[$name]
            if ($found) { Write-Ok ("поставлен {0} — {1}" -f $name, $found.Путь) }
            else { Stop-Break ("после установки всё ещё нет: " + $name + " (журнал: sdkmanager.log)") }
        }
    }
    $adb = Find-SdkPiece 'platform-tools\adb.exe'
    $emu = Find-SdkPiece 'emulator\emulator.exe'
    $sdkman = Find-SdkPiece 'cmdline-tools\latest\bin\sdkmanager.bat'
    $script:Adb = $adb.Путь
    $script:Emulator = $emu.Путь
    $emuDir = Split-Path $emu.Путь -Parent
    $check = Join-Path $emuDir 'emulator-check.exe'
    if (Test-Path $check) { $script:EmulatorCheck = $check } else { $script:EmulatorCheck = $emu.Путь }
    $script:SdkManager = $sdkman.Путь
    $script:SdkReady = $true
    Write-Ok ("adb: {0}" -f $script:Adb)
    Write-Ok ("эмулятор: {0}" -f $script:Emulator)
    # JDK подставляем в окружение, если владелец его не задал: sdkmanager и сборка
    # ищут его именно в JAVA_HOME.
    if (-not $env:JAVA_HOME) {
        $jdk = Ensure-Jdk
        $env:JAVA_HOME = $jdk
        $env:PATH = (Join-Path $jdk 'bin') + ';' + $env:PATH
        Write-Ok ("JAVA_HOME был пуст — подставил {0} (Java {1})" -f $jdk, $script:JdkMajor)
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Эмулятор. Владелец держит korzina:5554 поднятым, и это главный факт стенда:
# второй экземпляр не поднимаем, снимок не трогаем, -ColdBoot по умолчанию не применяем.
# ─────────────────────────────────────────────────────────────────────────────

function Get-EmulatorSerials {
    $r = Invoke-Tool $script:Adb @('devices')
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($r.Вывод -split "`r?`n")) {
        $m = [regex]::Match($line, '^(emulator-\d+)\s+device')
        if ($m.Success) { $out.Add($m.Groups[1].Value) }
    }
    return $out
}

function Get-AvdOfSerial {
    param([string]$Serial)
    $r = Invoke-Tool $script:Adb @('-s', $Serial, 'emu', 'avd', 'name')
    $first = ($r.Вывод -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -First 1
    if ($first) { return $first.Trim() }
    return ''
}

function Get-BootCompleted {
    param([string]$Serial)
    $r = Invoke-Tool $script:Adb @('-s', $Serial, 'shell', 'getprop', 'sys.boot_completed')
    return ($r.Вывод.Trim() -eq '1')
}

# Признак живого экземпляра AVD — процесс qemu, поднятый с этим AVD, а НЕ файл-замок.
# Замер 18.10.2026: после закрытия эмулятора замки multiinstance.lock и
# hardware-qemu.ini.lock остались на месте (от 10:48), а процесса уже не было — по ним
# стенд отказался бы поднимать эмулятор вовсе. Спрашиваем у системы, кто работает.
function Test-AvdRunning {
    try {
        $procs = Get-CimInstance Win32_Process `
            -Filter "Name='qemu-system-x86_64.exe' OR Name='emulator.exe'" -ErrorAction Stop
        foreach ($p in @($procs)) {
            $cmd = [string]$p.CommandLine
            if ($cmd -like "*$Avd.avd*" -or $cmd -like "*-avd $Avd*") { return $true }
        }
        return $false
    } catch {
        # Спросить не удалось (нет прав на сведения о процессах) — считаем, что эмулятор
        # мог быть поднят: второй экземпляр хуже отказа.
        return [bool](Get-Process qemu-system-x86_64, emulator -ErrorAction SilentlyContinue)
    }
}

function Ensure-Device {
    if ($script:Serial) { return $script:Serial }
    Ensure-Sdk
    $serial = ''
    foreach ($s in Get-EmulatorSerials) {
        $name = Get-AvdOfSerial $s
        if ($name -eq $Avd) { $serial = $s; break }
    }
    if ($serial) {
        Write-Skip ("эмулятор {0} уже поднят ({1}) — второй не поднимаю" -f $Avd, $serial)
        if ($ColdBoot) {
            # Холодная загрузка живого экземпляра — это его перезапуск: снимок AVD, в
            # котором живёт вход владельца, был бы потерян. Такой ключ не применяем молча.
            Stop-Break ("Ключ -ColdBoot при живом эмуляторе {0} не применяю: холодная загрузка означала бы перезапуск экземпляра владельца и потерю снимка AVD, в котором хранится вход в магазины. Убери ключ — эмулятор уже работает." -f $serial)
        }
    } else {
        if ($ColdBoot -and (Test-AvdRunning)) {
            Stop-Break ("Ключ -ColdBoot, но экземпляр AVD {0} уже поднят (процесс qemu работает), просто adb его не видит. Поднимать второй и трогать снимок нельзя: в нём вход владельца. Убери ключ и разберись с adb (adb kill-server; adb devices)." -f $Avd)
        }
        if (Test-AvdRunning) {
            Stop-Break ("Экземпляр AVD {0} уже поднят (процесс qemu работает), но adb его не видит. Второй экземпляр не поднимаю: это и двойная нагрузка, и потеря снимка. Разберись с adb: adb kill-server; adb devices." -f $Avd)
        }
        # Экземпляра нет — поднимаем сами. Снимок AVD при этом не удаляется:
        # в userdata живёт прежний вход владельца в магазины.
        Write-Log ("   поднимаю эмулятор {0}" -f $Avd) 'Yellow'
        $args = @('-avd', $Avd, '-no-boot-anim', '-gpu', 'auto')
        if ($ColdBoot) { $args += '-no-snapshot-load' }
        $run = Ensure-RunDir
        $emuLog = Join-Path $run 'emulator.log'
        Start-Process -FilePath $script:Emulator -ArgumentList $args -PassThru `
            -RedirectStandardInput (Join-Path $run 'empty.stdin') `
            -RedirectStandardOutput $emuLog -RedirectStandardError ($emuLog + '.err') | Out-Null
        $deadline = (Get-Date).AddMinutes(10)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            foreach ($s in Get-EmulatorSerials) {
                if ((Get-AvdOfSerial $s) -eq $Avd) { $serial = $s; break }
            }
            if ($serial) { break }
        }
        if (-not $serial) {
            Stop-Break ("Эмулятор {0} не появился за 10 минут. Журнал: {1}" -f $Avd, $emuLog)
        }
    }
    $script:Serial = $serial
    $deadline = (Get-Date).AddMinutes(10)
    while (-not (Get-BootCompleted $serial)) {
        if ((Get-Date) -gt $deadline) {
            Stop-Break ("Эмулятор {0} не догрузился за 10 минут (sys.boot_completed != 1)." -f $serial)
        }
        Start-Sleep -Seconds 3
    }
    Write-Ok ("устройство {0}, загрузка завершена" -f $script:Serial)
    return $script:Serial
}

# ─────────────────────────────────────────────────────────────────────────────
# Общие действия на устройстве: снимок экрана, разбор вида, касание по подписи.
# Снимок тянем файлом, а не потоком: PowerShell испортил бы двоичные данные.
# ─────────────────────────────────────────────────────────────────────────────

function Save-Screen {
    param([string]$Name)
    $remote = '/sdcard/korzina-screen.png'
    $local = Join-Path (Ensure-RunDir) ("screens\$Name.png")
    Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'screencap', '-p', $remote) | Out-Null
    Invoke-Tool $script:Adb @('-s', $script:Serial, 'pull', $remote, $local) | Out-Null
    Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'rm', '-f', $remote) | Out-Null
    return $local
}

function Get-UiTree {
    param([string]$Name)
    $remote = '/sdcard/korzina-ui.xml'
    $local = Join-Path (Ensure-RunDir) ("ui-$Name.xml")
    for ($i = 0; $i -lt 3; $i++) {
        Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'uiautomator', 'dump', '--compressed', $remote) | Out-Null
        Invoke-Tool $script:Adb @('-s', $script:Serial, 'pull', $remote, $local) | Out-Null
        if ((Test-Path $local) -and (Get-Item $local).Length -gt 0) {
            try { return [xml](Get-Content -LiteralPath $local -Raw -Encoding utf8) } catch { }
        }
        Start-Sleep -Milliseconds 800
    }
    return $null
}

function Get-UiText {
    param($Tree)
    if (-not $Tree) { return '' }
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($n in $Tree.SelectNodes('//node')) {
        $t = $n.GetAttribute('text')
        if ($t) { $parts.Add($t) }
    }
    return ($parts -join ' | ')
}

function Invoke-TapText {
    param([string]$Needle, [string]$Name)
    $tree = Get-UiTree $Name
    if (-not $tree) { return $false }
    foreach ($n in $tree.SelectNodes('//node')) {
        $t = $n.GetAttribute('text')
        if ($t -and $t.Contains($Needle)) {
            $m = [regex]::Match($n.GetAttribute('bounds'), '\[(\d+),(\d+)\]\[(\d+),(\d+)\]')
            if ($m.Success) {
                $x = [int](([int]$m.Groups[1].Value + [int]$m.Groups[3].Value) / 2)
                $y = [int](([int]$m.Groups[2].Value + [int]$m.Groups[4].Value) / 2)
                Write-Log ("   касание «{0}» в точке {1},{2}" -f $Needle, $x, $y) 'Yellow'
                Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'input', 'tap', "$x", "$y") | Out-Null
                return $true
            }
        }
    }
    return $false
}

function Save-Logcat {
    $r = Invoke-Tool $script:Adb @('-s', $script:Serial, 'logcat', '-d', '-v', 'time', '-s', 'Korzina:*')
    $r.Вывод | Set-Content -LiteralPath (Join-Path (Ensure-RunDir) 'logcat-korzina.txt') -Encoding utf8
    return $r.Вывод
}

# ─────────────────────────────────────────────────────────────────────────────
# Местный сервер. Порт занят — значит, сервер уже поднят (возможно, владельцем или
# прошлым прогоном): берём его же и второй не поднимаем.
# ─────────────────────────────────────────────────────────────────────────────

function Ensure-Server {
    if ($script:ServerReady) { return }
    $url = "http://127.0.0.1:$Port/login"
    $probe = Get-HttpProbe $url
    if ($probe.Код -eq 200 -and $probe.Тело -match 'Моя корзина') {
        Write-Skip ("на 127.0.0.1:{0} уже отвечает «Моя корзина» — беру этот сервер" -f $Port)
        $script:ServerReady = $true
        return
    }
    if ($probe.Код -ne 0) {
        Stop-Break ("Порт {0} занят чужим ответом (код {1}), а это должен быть наш сервер. Разберись, что слушает порт, и повтори." -f $Port, $probe.Код)
    }
    $py = Join-Path $script:Repo '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { Stop-Break ("Нет питона проекта: {0}" -f $py) }
    $run = Ensure-RunDir
    $outLog = Join-Path $run 'server.log'
    $errLog = Join-Path $run 'server.err.log'
    Write-Log ("   поднимаю сервер: {0} -m app.web.server --host 127.0.0.1 --port {1}" -f $py, $Port) 'Yellow'
    # 127.0.0.1, а не 0.0.0.0: вход в «Мою корзину» без кода не должен открываться в сеть.
    $proc = Start-Process -FilePath $py `
        -ArgumentList @('-m', 'app.web.server', '--host', '127.0.0.1', '--port', "$Port") `
        -WorkingDirectory $script:Repo -PassThru -WindowStyle Hidden `
        -RedirectStandardInput (Join-Path $run 'empty.stdin') `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if ($proc.HasExited) {
            $tail = (Get-Content -LiteralPath $errLog -Tail 12 -ErrorAction SilentlyContinue) -join "`n"
            Stop-Break ("Сервер вышел сразу (код {0}). Хвост {1}:`n{2}" -f $proc.ExitCode, $errLog, $tail)
        }
        $probe = Get-HttpProbe $url
        if ($probe.Код -eq 200) {
            $script:ServerPid = $proc.Id
            Write-Ok ("сервер слушает 127.0.0.1:{0} (pid {1}, журнал {2}) — не останавливаю: он нужен следующим шагам" -f $Port, $proc.Id, $outLog)
            break
        }
    }
    if (-not $script:ServerPid) {
        Stop-Break ("Сервер не ответил на {0} за 90 секунд. Журналы: {1}, {2}" -f $url, $outLog, $errLog)
    }
    $script:ServerReady = $true
}

# ─────────────────────────────────────────────────────────────────────────────
# Инструмент прогона tools/emucart.py (отдельная задача). Стенд его не подменяет:
# без него шаги 6 и 12–14 сделать нечем, и об этом надо сказать вслух.
# ─────────────────────────────────────────────────────────────────────────────

function Ensure-Emucart {
    if ($script:Emucart) { return $script:Emucart }
    $p = Join-Path $script:Repo 'tools\emucart.py'
    if (-not (Test-Path $p)) {
        Stop-Break @'
Нет tools/emucart.py — инструмента прогона (наряд, ожидание отчёта, снимок ответа
Магнита, вердикт). Он делается отдельной задачей; без него шаг 6 и шаги 12–14
сделать нечем, а подменять проверку догадкой нельзя.
'@
    }
    $script:Emucart = $p
    return $p
}

function Invoke-Emucart {
    param([string[]]$ArgList, [string]$StdoutFile = '', [int]$TimeoutSec = 0)
    Ensure-Emucart | Out-Null
    $py = Join-Path $script:Repo '.venv\Scripts\python.exe'
    $log = Join-Path (Ensure-RunDir) 'emucart.log'
    $r = Invoke-Tool $py (@('-m', 'tools.emucart') + $ArgList) $log $TimeoutSec
    if ($StdoutFile) { $r.Вывод | Set-Content -LiteralPath $StdoutFile -Encoding utf8 }
    return $r
}

# ─────────────────────────────────────────────────────────────────────────────
# APK: свежесть, зашитый адрес, версия. Пересборка — только когда есть причина.
# ─────────────────────────────────────────────────────────────────────────────

function Get-GradleVersion {
    $gradle = Join-Path $script:Repo 'android\app\app\build.gradle'
    $text = Get-Content -LiteralPath $gradle -Raw -Encoding utf8
    $vn = [regex]::Match($text, 'versionName\s+"([^"]+)"')
    $vc = [regex]::Match($text, 'versionCode\s+(\d+)')
    return [pscustomobject]@{
        Версия = if ($vn.Success) { $vn.Groups[1].Value } else { '' }
        Код    = if ($vc.Success) { [int]$vc.Groups[1].Value } else { 0 }
    }
}

function Get-ApkFacts {
    param([string]$ApkPath)
    $facts = [pscustomobject]@{
        Есть      = $false
        Адрес     = $false
        Версия    = ''
        Код       = 0
        Время     = $null
    }
    if (-not (Test-Path $ApkPath)) { return $facts }
    $facts.Есть = $true
    $facts.Время = (Get-Item $ApkPath).LastWriteTime
    $meta = Join-Path $script:Repo $APK_META_REL
    if (Test-Path $meta) {
        try {
            $j = Get-Content -LiteralPath $meta -Raw -Encoding utf8 | ConvertFrom-Json
            $el = @($j.elements)[0]
            if ($el) { $facts.Версия = [string]$el.versionName; $facts.Код = [int]$el.versionCode }
        } catch { }
    }
    # Адрес лежит строкой в классах APK. Распаковываем в каталог прогона: в репозиторий
    # распакованное не кладём, а искать в сжатом виде нельзя.
    $probe = Join-Path (Ensure-RunDir) 'apk-probe'
    Remove-Item $probe -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $probe | Out-Null
    Copy-Item $ApkPath (Join-Path $probe 'apk.zip') -Force
    try { Expand-Archive -LiteralPath (Join-Path $probe 'apk.zip') -DestinationPath (Join-Path $probe 'x') -Force } catch { }
    $host_ = ''
    try { $host_ = ([uri]$Server).Authority } catch { $host_ = $Server -replace '^https?://', '' }
    foreach ($dex in (Get-ChildItem (Join-Path $probe 'x') -Filter '*.dex' -ErrorAction SilentlyContinue)) {
        $txt = [System.IO.File]::ReadAllText($dex.FullName, [System.Text.Encoding]::ASCII)
        if ($txt.Contains($host_)) { $facts.Адрес = $true }
    }
    if ($facts.Код -eq 0) {
        $g = Get-GradleVersion
        $facts.Версия = $g.Версия
        $facts.Код = $g.Код
    }
    return $facts
}

function Get-NewestSourceTime {
    $roots = @(
        (Join-Path $script:Repo 'android\app\app\src'),
        (Join-Path $script:Repo 'android\app\app\build.gradle'),
        (Join-Path $script:Repo 'android\app\Dockerfile.build')
    )
    $newest = [datetime]'2000-01-01'
    foreach ($r in $roots) {
        if (Test-Path $r) {
            $item = Get-Item $r
            if ($item.PSIsContainer) {
                $f = Get-ChildItem $r -Recurse -File -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if ($f -and $f.LastWriteTime -gt $newest) { $newest = $f.LastWriteTime }
            } elseif ($item.LastWriteTime -gt $newest) {
                $newest = $item.LastWriteTime
            }
        }
    }
    return $newest
}

function Invoke-DockerBuild {
    param([string]$SourceDir, [string]$LogFile)
    $docker = (Get-Command docker -ErrorAction SilentlyContinue)
    if (-not $docker) {
        Stop-Break 'Docker не найден в PATH. Без него APK не собрать (Android SDK на машине не нужен).'
    }
    $ver = Invoke-Tool $docker.Source @('version', '--format', '{{.Server.Version}}') $LogFile
    if ($ver.Код -ne 0) {
        Write-Warn 'Docker Desktop не отвечает — пробую всё равно, но, скорее всего, сборка не пойдёт.'
    }
    $images = Invoke-Tool $docker.Source @('images', '--format', '{{.Repository}}:{{.Tag}}') $LogFile
    if (-not ($images.Вывод -match 'korzina-apk:latest')) {
        Write-Log '   собираю образ сборщика korzina-apk (первый раз это долго)' 'Yellow'
        $b = Invoke-Tool $docker.Source @('build', '-f', (Join-Path $SourceDir 'Dockerfile.build'), '-t', 'korzina-apk', $SourceDir) $LogFile
        if ($b.Код -ne 0) { Stop-Break ("docker build не собрался (код {0}). Журнал: {1}" -f $b.Код, $LogFile) }
    } else {
        Write-Skip 'образ сборщика korzina-apk уже есть — не пересобираю'
    }
    $androidDir = Join-Path $env:USERPROFILE '.android'
    if (-not (Test-Path $androidDir)) { New-Item -ItemType Directory -Path $androidDir -Force | Out-Null }
    # ~/.android монтируется ради постоянного debug.keystore: иначе каждая сборка
    # подписывалась бы новым ключом и не ставилась поверх прежней (замер 03.10.2026).
    $runArgs = @(
        'run', '--rm',
        '-e', "KORZINA_SERVER=$Server",
        '-v', 'korzina-gradle-cache:/root/.gradle',
        '-v', "${androidDir}:/root/.android",
        '-v', "${SourceDir}:/src",
        'korzina-apk'
    )
    return Invoke-Tool $docker.Source $runArgs $LogFile
}

function Copy-Tree {
    param([string]$From, [string]$To)
    $rc = Join-Path $env:SystemRoot 'System32\robocopy.exe'
    if (Test-Path $rc) {
        Invoke-Tool $rc @($From, $To, '/MIR', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') | Out-Null
    } else {
        Remove-Item $To -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item $From $To -Recurse -Force
    }
}

function Ensure-Apk {
    if ($script:ApkPath) { return $script:ApkPath }
    $apk = Join-Path $script:Repo $APK_REL
    $gradle = Get-GradleVersion
    $facts = Get-ApkFacts $apk
    $why = ''
    if ($Rebuild) {
        $why = 'дан ключ -Rebuild'
    } elseif (-not $facts.Есть) {
        $why = 'APK нет'
    } elseif (-not $facts.Адрес) {
        $why = ("в APK не зашит адрес {0}" -f $Server)
    } elseif ($facts.Версия -ne $gradle.Версия) {
        $why = ("версия APK {0}, а в исходниках {1}" -f $facts.Версия, $gradle.Версия)
    } else {
        $srcTime = Get-NewestSourceTime
        if ($facts.Время -lt $srcTime) {
            $why = ("исходники новее APK ({0:dd.MM.yyyy HH:mm} против {1:dd.MM.yyyy HH:mm})" -f $srcTime, $facts.Время)
        }
    }
    if (-not $why) {
        Write-Skip ("APK свежий: версия {0}, адрес {1} зашит ({2:dd.MM.yyyy HH:mm})" -f $facts.Версия, $Server, $facts.Время)
        $script:ApkPath = $apk
        return $apk
    }
    Write-Log ("   пересобираю APK: {0}" -f $why) 'Yellow'
    $run = Ensure-RunDir
    $log = Join-Path $run 'docker-apk.log'
    $appDir = Join-Path $script:Repo 'android\app'
    $res = Invoke-DockerBuild -SourceDir $appDir -LogFile $log
    if ($res.Код -ne 0) {
        # Кириллица в пути — известная беда Docker на Windows: контейнер не видит /src
        # или Gradle падает на самом пути. Тогда собираем из копии в %TEMP%.
        if ($script:Repo -match '[^\x00-\x7F]') {
            Write-Warn 'Сборка не пошла — путь репозитория кириллический. Пробую из копии в %TEMP%\korzina-apk-src.'
            $tmpSrc = Join-Path $env:TEMP 'korzina-apk-src'
            Copy-Tree -From $appDir -To $tmpSrc
            $res = Invoke-DockerBuild -SourceDir $tmpSrc -LogFile $log
            if ($res.Код -eq 0) {
                New-Item -ItemType Directory -Path (Split-Path $apk -Parent) -Force | Out-Null
                Copy-Item (Join-Path $tmpSrc 'app\build\outputs\apk\debug\app-debug.apk') $apk -Force
                $metaSrc = Join-Path $tmpSrc 'app\build\outputs\apk\debug\output-metadata.json'
                if (Test-Path $metaSrc) { Copy-Item $metaSrc (Join-Path $script:Repo $APK_META_REL) -Force }
                Write-Ok 'APK собран в копии и принесён в репозиторий'
            }
        }
    }
    if ($res.Код -ne 0) {
        Stop-Break ("APK не собрался (код {0}). Журнал: {1}" -f $res.Код, $log)
    }
    $facts = Get-ApkFacts $apk
    if (-not $facts.Есть) { Stop-Break ("После сборки APK не появился: {0}. Журнал: {1}" -f $apk, $log) }
    if (-not $facts.Адрес) { Stop-Break ("В собранном APK нет адреса {0}. Журнал: {1}" -f $Server, $log) }
    $script:ApkPath = $apk
    Write-Ok ("APK собран: версия {0}, адрес {1} зашит" -f $facts.Версия, $Server)
    return $apk
}

function Get-InstalledApp {
    $r = Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'dumpsys', 'package', $APP_ID)
    if ($r.Вывод -notmatch 'versionCode=') { return $null }
    $vc = [regex]::Match($r.Вывод, 'versionCode=(\d+)')
    $vn = [regex]::Match($r.Вывод, 'versionName=(\S+)')
    $upd = [regex]::Match($r.Вывод, 'lastUpdateTime=([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]+)')
    [datetime]$when = [datetime]::MinValue
    if ($upd.Success) {
        # Разбор строго по образцу: разбор «как принято в стране» на другой машине сломается.
        [datetime]::TryParseExact($upd.Groups[1].Value, 'yyyy-MM-dd HH:mm:ss',
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when) | Out-Null
    }
    return [pscustomobject]@{
        Код    = if ($vc.Success) { [int]$vc.Groups[1].Value } else { 0 }
        Версия = if ($vn.Success) { $vn.Groups[1].Value } else { '' }
        Обновлён = $when
    }
}

# Ставить ли APK заново — вопрос без часов. Часы эмулятора и машины идут врозь
# (у эмулятора они отстают на часы), поэтому «время APK против времени установки»
# сравнивать нельзя: выходило «ставить каждый раз». Сравниваем то, что не зависит
# от часов: отпечаток содержимого APK и то, что после нашей установки записано в
# закладке. Закладка лежит вне репозитория.
function Get-InstallMarkerPath {
    $dir = Join-Path $env:LOCALAPPDATA 'korzina-emu'
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    return (Join-Path $dir 'installed.json')
}

function Get-ApkFingerprint {
    param([string]$ApkPath)
    return (Get-FileHash -LiteralPath $ApkPath -Algorithm SHA256).Hash
}

# Отпечаток УСТАНОВЛЕННОГО приложения: путь к base.apk и его sha256. Это единственный
# честный ответ на вопрос «тот же APK стоит или другой»: сравнивать имена и время нельзя
# (часы эмулятора идут врозь), а версия у пересборок бывает одна и та же.
function Get-InstalledApkFingerprint {
    $pathOut = Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'pm', 'path', $APP_ID)
    $apkPath = ''
    foreach ($line in ($pathOut.Вывод -split "`r?`n")) {
        $line = $line.Trim()
        if ($line -like 'package:*base.apk') { $apkPath = $line.Substring('package:'.Length); break }
        if (-not $apkPath -and $line -like 'package:*') { $apkPath = $line.Substring('package:'.Length) }
    }
    if (-not $apkPath) { return $null }
    $sumOut = Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'sha256sum', $apkPath)
    $m = [regex]::Match($sumOut.Вывод, '\b([0-9a-fA-F]{64})\b')
    if (-not $m.Success) { return [pscustomobject]@{ Путь = $apkPath; Хеш = '' } }
    return [pscustomobject]@{ Путь = $apkPath; Хеш = $m.Groups[1].Value.ToUpper() }
}

function Test-AppRunning {
    $r = Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'pidof', $APP_ID)
    return [bool]($r.Вывод.Trim())
}

function Ensure-InstalledApp {
    if ($script:InstalledOk) { return }
    Ensure-Device | Out-Null
    $apk = Ensure-Apk
    $facts = Get-ApkFacts $apk
    $fingerprint = Get-ApkFingerprint $apk
    $inst = Get-InstalledApp
    $onDevice = if ($inst) { Get-InstalledApkFingerprint } else { $null }
    $same = ($inst -and $onDevice -and $onDevice.Хеш -and $onDevice.Хеш -eq $fingerprint)
    if ($same) {
        Write-Skip ("приложение уже стоит и это ровно тот же APK (версия {0}, код {1}, sha256 совпал) — не переставляю" -f $inst.Версия, $inst.Код)
        $script:InstalledOk = $true
        return
    }
    # APK на диске другой. Ставить поверх ЗАПУЩЕННОГО приложения нельзя: установка
    # убивает процесс на ходу, а вместе с ним теряется несохранённый вход (введённый
    # номер, код из СМС, капча). Это решает человек, и только ключом -Rebuild стенд
    # соглашается поставить поверх работающего.
    if ((Test-AppRunning) -and -not $Rebuild) {
        if ($onDevice -and -not $onDevice.Хеш) {
            Write-Warn ("Отпечаток установленного APK прочитать не удалось ({0}) — считаю, что стоит другой." -f $onDevice.Путь)
        }
        Stop-Human @'
Приложение сейчас запущено, а на диске лежит ДРУГОЙ APK. Ставить поверх работающего
нельзя: установка убьёт приложение на ходу, и несохранённый вход (номер, код из СМС,
капча) потеряется.
Что делать: закройте приложение на эмуляторе и повторите с шага 8, либо, если замена
поверх запущенного сейчас допустима, запустите стенд с ключом -Rebuild.
'@
    }
    $log = Join-Path (Ensure-RunDir) 'adb-install.txt'
    Write-Log ("   ставлю APK {0}" -f $apk) 'Yellow'
    $r = Invoke-Tool $script:Adb @('-s', $script:Serial, 'install', '-r', $apk) $log 600
    if ($r.Код -ne 0 -and $r.Вывод -match 'INSTALL_FAILED_UPDATE_INCOMPATIBLE') {
        if (-not $AllowUninstall) {
            Stop-Human @'
Приложение на эмуляторе подписано другим ключом, и поверх не встаёт
(INSTALL_FAILED_UPDATE_INCOMPATIBLE). Удаление сотрёт вход в Магнит в окне приложения,
поэтому само по себе оно не делается. Реши, что важнее: сохранить вход (тогда нужен
APK, подписанный тем же ключом, что и стоящий), или поставить свежий APK с нуля —
на это есть ключ -AllowUninstall, и после него вход в Магнит проходят заново.
'@
        }
        Write-Warn 'Подпись не совпала — удаляю приложение (ключ -AllowUninstall дан) и ставлю заново. Вход в Магазины в окне приложения будет потерян.'
        Invoke-Tool $script:Adb @('-s', $script:Serial, 'uninstall', $APP_ID) $log | Out-Null
        $r = Invoke-Tool $script:Adb @('-s', $script:Serial, 'install', '-r', $apk) $log 600
    }
    if ($r.Код -ne 0) {
        Stop-Break ("adb install не прошёл (код {0}). Журнал: {1}" -f $r.Код, $log)
    }
    $inst = Get-InstalledApp
    if (-not $inst -or $inst.Код -ne $facts.Код) {
        Stop-Break ("После установки версия на устройстве не та (нужна {0}). Журнал: {1}" -f $facts.Код, $log)
    }
    # Проверяем не только версию, но и содержимое: тот ли APK лёг на устройство.
    $onDevice = Get-InstalledApkFingerprint
    if (-not $onDevice -or -not $onDevice.Хеш) {
        Write-Warn 'Отпечаток установленного APK прочитать не удалось — сверяю только версию.'
    } elseif ($onDevice.Хеш -ne $fingerprint) {
        Stop-Break ("На устройстве оказался другой APK (sha256 не совпал с {0}). Журнал: {1}" -f $apk, $log)
    }
    # Закладку пишем после успеха: она и есть ответ «этот APK я уже ставил».
    $markerOut = [ordered]@{
        apk           = $fingerprint
        versionCode   = $inst.Код
        versionName   = $inst.Версия
        serial        = $script:Serial
        installedWhen = if ($inst.Обновлён) { $inst.Обновлён.ToString('s') } else { '' }
        at            = (Get-Date).ToString('s')
    }
    $markerOut | ConvertTo-Json | Set-Content -LiteralPath (Get-InstallMarkerPath) -Encoding utf8
    Write-Ok ("установлено: версия {0} (код {1})" -f $inst.Версия, $inst.Код)
    $script:InstalledOk = $true
}

# ─────────────────────────────────────────────────────────────────────────────
# Окно приложения и отладочный протокол (CDP). Через него стенд только СМОТРИТ:
# список страниц и имена кук. Значения кук не читаются и не сохраняются.
# ─────────────────────────────────────────────────────────────────────────────

function Ensure-AppRunning {
    if ($script:AppRunning) { return }
    Ensure-InstalledApp
    $r = Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'am', 'start', '-W', '-n', $APP_ACTIVITY)
    Start-Sleep -Seconds 3
    $focus = Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'dumpsys', 'window', 'windows')
    if ($focus.Вывод -notmatch [regex]::Escape($APP_ID)) {
        Save-Screen '09-app' | Out-Null
        Stop-Break ("Окно приложения не вышло на передний план. Смотри снимок screens\09-app.png и вывод am start в журнале.")
    }
    # Признак, что окно открыло наш сайт, а не сказало «адрес не задан» и не упало
    # на запрете открытого http.
    $tree = Get-UiTree '09-app'
    $text = Get-UiText $tree
    if ($text -match 'Адрес сервера не задан') {
        Stop-Break "Приложение говорит «Адрес сервера не задан» — APK собран без адреса. Пересобери с -Rebuild и адресом $Server."
    }
    if ($text -match 'ERR_CLEARTEXT_NOT_PERMITTED') {
        Stop-Break 'Приложение не пускает открытый http (ERR_CLEARTEXT_NOT_PERMITTED) — это дефект манифеста, стенд его не правит.'
    }
    $script:AppRunning = $true
    return $text
}

# Проброс порта 9222 на ЖИВОЙ сокет окна. За один прогон сокет меняется не раз:
# приложение перезапускается, уходят и приходят фоновые окна сетей (замер 18.10.2026 —
# пять разных номеров за прогон). Старый проброс при этом остаётся и отвечает тишиной,
# а это выглядит как «WebView не отвечает» при здоровом эмуляторе. Поэтому перед
# каждым шагом, которому нужен отладочный протокол, сверяем проброс с живым сокетом.
function Sync-CdpForward {
    Ensure-Device | Out-Null
    $uni = Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'cat', '/proc/net/unix')
    # Имя $appPid, а не $pid: $PID в PowerShell — служебная переменная, её не переписать.
    $appPid = (Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'pidof', $APP_ID)).Вывод.Trim()
    $sockets = New-Object System.Collections.Generic.List[string]
    foreach ($m in [regex]::Matches($uni.Вывод, 'webview_devtools_remote_(\d+)')) {
        if (-not $sockets.Contains($m.Groups[1].Value)) { $sockets.Add($m.Groups[1].Value) }
    }
    if ($sockets.Count -eq 0) {
        Write-Warn 'В окне приложения нет отладочного сокета (webview_devtools_remote): окно ещё не открылось или отладка выключена.'
        return $false
    }
    # Цепляемся ТОЛЬКО к окну нашего приложения. Отлаживаемых WebView на машине может
    # быть несколько (другое приложение, чужая отладка), и проброс на чужой сокет означал
    # бы, что стенд читает и уводит не своё окно. Не наш pid — не гадаем, а отказываемся.
    if (-not $appPid -or -not $sockets.Contains($appPid)) {
        Write-Warn ("Отладочный сокет окна приложения не найден: pid приложения «{0}», сокеты в системе: {1}. К чужому окну не цепляюсь." -f $appPid, ($sockets -join ', '))
        return $false
    }
    $socketPid = $appPid
    $socket = "localabstract:webview_devtools_remote_$socketPid"
    $fw = Invoke-Tool $script:Adb @('-s', $script:Serial, 'forward', '--list')
    $alive = Get-HttpProbe 'http://127.0.0.1:9222/json' 10
    if (($fw.Вывод -notmatch [regex]::Escape($socket)) -or $alive.Код -ne 200) {
        Invoke-Tool $script:Adb @('-s', $script:Serial, 'forward', '--remove', 'tcp:9222') | Out-Null
        $add = Invoke-Tool $script:Adb @('-s', $script:Serial, 'forward', 'tcp:9222', $socket)
        if ($add.Код -ne 0) {
            Write-Warn ("adb forward не встал: " + $add.Вывод.Trim())
            return $false
        }
        Write-Log ("   проброс 9222 обновлён на живой сокет {0}" -f $socketPid) 'Gray'
        $alive = Get-HttpProbe 'http://127.0.0.1:9222/json' 10
    }
    if ($alive.Код -ne 200) {
        Write-Warn ("Отладочный протокол на 127.0.0.1:9222 не отвечает (код {0}) — сокет {1}." -f $alive.Код, $socketPid)
        return $false
    }
    $script:CdpTargets = ConvertFrom-CdpPages $alive.Тело
    Save-CdpPages
    return $true
}

# Список страниц окна для файла прогона: адреса без строки запроса. Сами объекты в
# памяти держат полный адрес (по нему ищем наши страницы), но в файл и в журнал полный
# адрес не попадает: в нём бывают code, state и токены входа.
function ConvertFrom-CdpPages {
    param([string]$Body)
    try { return @($Body | ConvertFrom-Json) } catch { return @() }
}

function Save-CdpPages {
    $safe = @()
    foreach ($t in @($script:CdpTargets)) {
        $safe += [pscustomobject]@{
            type  = [string]$t.type
            title = [string]$t.title
            url   = Get-SafeUrl ([string]$t.url)
        }
    }
    $safe | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path (Ensure-RunDir) 'cdp-pages.json') -Encoding utf8
}

function Ensure-Cdp {
    param([switch]$Refresh)
    if ($script:CdpChecked -and -not $Refresh) { return $script:CdpReady }
    $script:CdpChecked = $true
    Ensure-AppRunning | Out-Null
    $script:CdpReady = Sync-CdpForward
    if (-not $script:CdpReady) {
        # Один повтор с ожиданием: окно могло ещё не подняться, а сокет — не появиться.
        Write-Log '   отладочного канала нет — жду сокет до 30 с и пробую ещё раз' 'Gray'
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline -and -not $script:CdpReady) {
            Start-Sleep -Seconds 3
            $script:CdpReady = Sync-CdpForward
        }
    }
    if (-not $script:CdpReady) {
        # Неудачу не кэшируем: следующий шаг вправе попробовать снова (сокет появляется
        # с задержкой), а «нет канала» — не ответ, который можно запомнить.
        $script:CdpChecked = $false
    }
    return $script:CdpReady
}

# Требование канала для шагов, без которых прогон не имеет смысла. Молча идти дальше
# нельзя: без канала стенд не видит ни вход, ни проверку браузера, а «Собрать» вслепую
# может нажать кнопку поверх капчи Магнита.
function Require-Cdp {
    param([switch]$Refresh)
    $ready = if ($Refresh) { Ensure-Cdp -Refresh } else { Ensure-Cdp }
    if (-not $ready) {
        Stop-Break @'
Отладочный канал окна приложения недоступен (код 10): без него стенд не видит ни вход
в Магнит, ни проверку браузера, и продолжать вслепую нельзя.
Что проверить:
  • окно «Моей корзины» открыто на экране эмулятора (adb shell pidof ru.moyakorzina.app);
  • в приложении включена отладка WebView (MainActivity) — без неё отладочного сокета нет;
  • живой сокет и проброс:
      adb shell cat /proc/net/unix | findstr webview_devtools_remote
      adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
Если отладку включить нельзя — это решение оркестратора: запасной путь доказательств
(снимки и ответы приложения) слабее и в протоколе называется отдельно.
'@
    }
    return $true
}

# ─────────────────────────────────────────────────────────────────────────────
# Отладочный протокол окна (CDP) своими руками.
#
# ЗАЧЕМ не Playwright. `connect_over_cdp` у Playwright первым делом настраивает
# браузер целиком, а Android WebView этого не умеет: соединение падает с «Browser
# context management is not supported». Инструмент tools/emucart.py пробует Playwright
# и честно говорит, что окно его не приняло. Стенду от страницы нужны три вещи —
# адрес, нажатие кнопки и имена кук, — и для них хватает самого протокола: он открыт
# по адресу из /json и говорит на языке запросов и ответов.
# ─────────────────────────────────────────────────────────────────────────────

function Get-CdpTargets {
    $probe = Get-HttpProbe 'http://127.0.0.1:9222/json' 10
    if ($probe.Код -ne 200) { return @() }
    $targets = ConvertFrom-CdpPages $probe.Тело
    $script:CdpTargets = $targets
    Save-CdpPages
    return $targets
}

function Update-CdpTargets {
    $script:CdpTargets = Get-CdpTargets
    return $script:CdpTargets
}

function Get-OurPage {
    foreach ($t in @($script:CdpTargets)) {
        if ($t.type -eq 'page' -and [string]$t.url -like "*10.0.2.2:$Port*") { return $t }
    }
    return $null
}

function Get-MagnitPage {
    foreach ($t in @($script:CdpTargets)) {
        if ($t.type -eq 'page' -and [string]$t.url -like '*magnit.ru*') { return $t }
    }
    return $null
}

# Ждать, пока в окне появится наша страница. Адрес в списке страниц обновляется только
# после того, как окно отрисует документ, и на уходе с тяжёлого Магнита это занимает
# не секунду: без ожидания стенд объявил бы «страницы нет» при живом окне.
function Wait-OurPage {
    param([int]$TimeoutSec = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Update-CdpTargets | Out-Null
        $page = Get-OurPage
        if ($page) { return $page }
        Start-Sleep -Seconds 2
    }
    return $null
}

# Запросы и ответы протокола. Значения кук отбрасываются ЗДЕСЬ ЖЕ и по умолчанию: наружу
# из этой функции уходят только имена. Ключ -RawCookies — для редкого случая, когда сырой
# ответ нужен целиком; тогда его нельзя ни печатать, ни сохранять.
function Invoke-CdpCommands {
    param(
        [string]$WsUrl,
        [object[]]$Commands,
        [int]$TimeoutSec = 30,
        [switch]$RawCookies
    )
    $ws = New-Object System.Net.WebSockets.ClientWebSocket
    $token = [System.Threading.CancellationToken]::None
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    try {
        if (-not $ws.ConnectAsync([uri]$WsUrl, $token).Wait($TimeoutSec * 1000)) {
            throw 'окно не приняло соединение вовремя'
        }
        $id = 0
        foreach ($cmd in $Commands) {
            $id++
            $cmd['id'] = $id
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(($cmd | ConvertTo-Json -Depth 8 -Compress))
            $seg = [System.ArraySegment[byte]]::new($bytes)
            if (-not $ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $token).Wait($TimeoutSec * 1000)) {
                throw 'окно не приняло запрос вовремя'
            }
        }
        $answers = @{}
        $buffer = New-Object byte[] 262144
        while ($answers.Count -lt $Commands.Count -and (Get-Date) -lt $deadline) {
            $sb = New-Object System.Text.StringBuilder
            do {
                $seg = [System.ArraySegment[byte]]::new($buffer)
                $recv = $ws.ReceiveAsync($seg, $token)
                if (-not $recv.Wait($TimeoutSec * 1000)) { throw 'окно молчит' }
                $chunk = $recv.Result
                if ($chunk.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) { break }
                [void]$sb.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $chunk.Count))
            } while (-not $chunk.EndOfMessage)
            if ($sb.Length -eq 0) { continue }
            $obj = $sb.ToString() | ConvertFrom-Json
            if ($null -eq $obj.id) { continue }
            if (-not $RawCookies -and $obj.result -and $obj.result.cookies) {
                $obj.result = [pscustomobject]@{ names = @($obj.result.cookies | ForEach-Object { $_.name }) }
            }
            $answers[[int]$obj.id] = $obj
        }
        $out = @()
        for ($i = 1; $i -le $Commands.Count; $i++) { $out += $answers[$i] }
        return $out
    } finally {
        try { $ws.Dispose() } catch { }
    }
}

function Invoke-CdpEval {
    param([string]$WsUrl, [string]$Expression, [int]$TimeoutSec = 30)
    $res = Invoke-CdpCommands -WsUrl $WsUrl -TimeoutSec $TimeoutSec -Commands @(
        @{ method = 'Runtime.evaluate'; params = @{ expression = $Expression; returnByValue = $true; awaitPromise = $true } }
    )
    if (-not $res -or -not $res[0] -or $res[0].result.exceptionDetails) { return $null }
    return $res[0].result.result.value
}

function Invoke-CdpEvalSafe {
    param([string]$WsUrl, [string]$Expression, [int]$TimeoutSec = 30, [switch]$Quiet)
    try {
        return Invoke-CdpEval -WsUrl $WsUrl -Expression $Expression -TimeoutSec $TimeoutSec
    } catch {
        if (-not $Quiet) { Write-Warn ("Отладочный протокол не ответил: " + $_.Exception.Message) }
        return $null
    }
}

# Дождаться, пока страница дорисуется. Магнит — тяжёлый сайт: сессия в localStorage
# появляется не сразу, и проверка входа, снятая на середине загрузки, честно говорит
# «не знаю». Тогда стенд позвал бы владельца зря — ровно та ошибка, из-за которой
# шаг 11 перевели на check-login.
function Wait-PageReady {
    param([string]$WsUrl, [int]$TimeoutSec = 40)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $state = Invoke-CdpEvalSafe -WsUrl $WsUrl -Expression 'document.readyState' -Quiet
        if ($state -eq 'complete') { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

# Имена кук для адреса нашего сервера. Кука сессии закрыта от страницы (HttpOnly),
# поэтому спрашиваем протокол — и его ответ схлопывается в имена внутри функции.
function Get-OurCookieNames {
    param([string]$WsUrl)
    $res = Invoke-CdpCommands -WsUrl $WsUrl -TimeoutSec 30 -Commands @(
        @{ method = 'Network.enable'; params = @{} },
        @{ method = 'Network.getCookies'; params = @{ urls = @("http://10.0.2.2:$Port/") } }
    )
    if ($res -and $res[1] -and $res[1].result) { return @($res[1].result.names) }
    return @()
}

# Вход в «Мою корзину» без кода: кнопка «Посмотреть демо» на нашем экране входа.
# Разбор вида телефона её не видит — страница отдаёт наружу только заголовок, — поэтому
# нажимаем её тем же окном приложения. Магазина это не касается: вход в Магнит человек
# проходит сам (шаг 11).
#
# Признак входа берём не по адресу страницы (у сайта есть и открытые страницы), а по
# имени куки сессии нашего сервера: она закрыта от страницы, и её видит только протокол.
function Enter-Demo {
    Update-CdpTargets | Out-Null
    $page = Get-OurPage
    if (-not $page) {
        # Наша страница не открыта. Куда смотрит окно — и можно ли его трогать:
        #   • своя страница — трогать нечего, работаем с ней;
        #   • пустая (about:blank) — можно: там ничего не происходит;
        #   • страница сети — ТОЛЬКО если вход уже подтверждён (check-login=yes).
        #     Увести окно посреди ввода номера, СМС или капчи — значит сбить работу
        #     владельца; а скрытые окна фоновой сборки стенд не трогает вообще.
        $magnit = Get-MagnitPage
        $target = $null
        if ($magnit) {
            $v = Get-LoginVerdict
            if ($v.Код -eq $script:EXIT_OK -and $v.Login -eq 'yes') {
                $target = $magnit
            } else {
                $loginWord = if ($v.Login) { $v.Login } else { '?' }
                return [pscustomobject]@{
                    Итог    = ("окно стоит на странице сети, вход не подтверждён (login={0}) — окно не трогаю" -f $loginWord)
                    Адрес   = Get-SafeUrl ([string]$magnit.url)
                    Нажатие = ''
                }
            }
        } else {
            $target = @($script:CdpTargets) |
                Where-Object { $_.type -eq 'page' -and (-not $_.url -or $_.url -eq 'about:blank') } |
                Select-Object -First 1
        }
        if ($target) {
            Write-Log ("   окно стояло на {0} — возвращаю его на наш экран" -f (Get-SafeUrl ([string]$target.url))) 'Gray'
            $null = Invoke-CdpEvalSafe -WsUrl $target.webSocketDebuggerUrl -Expression "location.href='http://10.0.2.2:$Port/'"
            $page = Wait-OurPage -TimeoutSec 30
        }
    }
    if (-not $page) {
        return [pscustomobject]@{ Итог = 'страница нашего сайта в окне не найдена'; Адрес = ''; Нажатие = '' }
    }
    $names = @(Get-OurCookieNames -WsUrl $page.webSocketDebuggerUrl)
    $loggedIn = $names -contains 'session'
    if ($loggedIn) {
        return [pscustomobject]@{ Итог = 'вход уже есть'; Адрес = Get-SafeUrl ([string]$page.url); Нажатие = '' }
    }
    # Открытая страница сайта тоже бывает без входа: тогда сначала на экран входа.
    if ([string]$page.url -notmatch '/login') {
        $null = Invoke-CdpEvalSafe -WsUrl $page.webSocketDebuggerUrl -Expression "location.href='/login'"
        # Перехода тоже ждём: адрес в списке страниц меняется не мгновенно, а кнопку
        # «Посмотреть демо» надо нажимать уже на экране входа.
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            Update-CdpTargets | Out-Null
            $page = Get-OurPage
            if ($page -and ([string]$page.url -match '/login')) { break }
        }
    }
    $clicked = ''
    if ($page -and ([string]$page.url -match '/login')) {
        $clicked = Invoke-CdpEvalSafe -WsUrl $page.webSocketDebuggerUrl -Expression @'
(function(){
  var f = document.querySelector('form[action$="/demo"]');
  if (!f) return 'нет формы демо';
  var b = f.querySelector('button');
  if (!b) return 'нет кнопки демо';
  b.click();
  return 'нажал';
})()
'@
    }
    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        Update-CdpTargets | Out-Null
        $page = Get-OurPage
        if (-not $page) { continue }
        if (@(Get-OurCookieNames -WsUrl $page.webSocketDebuggerUrl) -contains 'session') {
            $loggedIn = $true
            break
        }
    }
    # Адрес в списке страниц обновляется только после того, как окно дорисует новый
    # документ: без этой паузы в журнал попал бы прежний адрес и сбил бы с толку.
    Start-Sleep -Seconds 2
    Update-CdpTargets | Out-Null
    $page = Get-OurPage
    $addr = if ($page) { Get-SafeUrl ([string]$page.url) } else { '' }
    return [pscustomobject]@{
        Итог    = if ($loggedIn) { 'вошли' } else { 'остались на входе' }
        Адрес   = $addr
        Нажатие = [string]$clicked
    }
}

# Открыть Магнит в окне приложения тем же мостом, которым пользуется наш сайт
# (кнопка «Войти» на «Кабинетах» зовёт ровно это). Входа это не заменяет.
# Ждём долго: главная Магнита весит много, и её адрес в списке страниц появляется
# только после того, как окно отрисует документ.
function Open-MagnitInApp {
    Update-CdpTargets | Out-Null
    $page = Get-OurPage
    if (-not $page) { return 'наша страница не найдена — Магнит открывает человек' }
    $null = Invoke-CdpEvalSafe -WsUrl $page.webSocketDebuggerUrl -Expression "window.Korzina && window.Korzina.openStore('magnit')"
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        Update-CdpTargets | Out-Null
        if (Get-MagnitPage) { return 'Магнит открыт' }
    }
    return 'Магнит не открылся'
}

# Решение о входе в Магнит. Оно должно быть ОДНО, и его принимает инструмент
# `emucart check-login`: он видит и слова страницы, и ключи сессии в localStorage, и
# имена кук. Своё суждение стенда по имени куки mg_at ушло в прошлое: замер
# 18.10.2026 показал, что вход Магнита живёт в localStorage (au_atid, au_expd, au_phnu),
# а куки mg_at нет вовсе — по ней стенд звал владельца зря.
#   код 0  — login=yes: шаг пройден, человека не зовём;
#   код 20 — login=no или unknown: нужен человек (у стенда это тот же код 20);
#   код 10 — канал или окно не ответили: это сбой стенда, а не «не вошёл».
function Get-LoginVerdict {
    $out = Join-Path (Ensure-RunDir) 'check-login.txt'
    $r = Invoke-Emucart @('check-login', '--require', '--cdp', 'http://127.0.0.1:9222') $out 300
    $login = ''
    $mgAt = ''
    foreach ($line in ($r.Вывод -split "`r?`n")) {
        if ($line -match '^\s*login=(\w+)') { $login = $Matches[1] }
        if ($line -match 'mg_at=([\w-]+)') { $mgAt = $Matches[1] }
    }
    # Наружу отдаём только «есть / нет / не знаю». Сырое значение инструмента в журнал
    # не идёт: строку про куку печатает инструмент, а стенд повторяет лишь признак.
    $mgAtWord = switch ($mgAt) {
        'visible' { 'present' }
        'not-visible' { 'absent' }
        default { '?' }
    }
    return [pscustomobject]@{ Код = $r.Код; Login = $login; MgAt = $mgAtWord; Файл = $out }
}

function Step-HumanLogin {
    Ensure-Device | Out-Null
    # Проброс порта обновляем прямо перед проверкой: за прогон сокет окна меняется,
    # и старый проброс ответил бы тишиной при здоровом эмуляторе.
    Require-Cdp -Refresh | Out-Null
    $opened = Ensure-MagnitPage
    $v = Get-LoginVerdict
    # «Не знаю» — не ответ. Пока Магнит дорисовывается, признаков сессии ещё не видно,
    # и звать человека в эту минуту значило бы гонять владельца зря (та же ошибка, что
    # была с именем куки mg_at). Даём странице устояться и спрашиваем ещё два раза.
    for ($attempt = 1; $attempt -le 2 -and $v.Код -eq $script:EXIT_HUMAN -and $v.Login -eq 'unknown'; $attempt++) {
        Write-Log ("   проверка входа пока «не знаю» (login=unknown) — повторяю через 8 с") 'Gray'
        Start-Sleep -Seconds 8
        $v = Get-LoginVerdict
    }
    if ($v.Код -eq $script:EXIT_OK -and $v.Login -eq 'yes') {
        Write-Ok ("вход в Магнит есть (login=yes) — человека не зову; для справки: mg_at={0} (признаком входа не считается)" -f $v.MgAt)
        return
    }
    if ($v.Код -eq $script:EXIT_OK) {
        # Ноль без «да» — не подтверждение: инструмент мог ответить не про то окно.
        $loginWord = if ($v.Login) { $v.Login } else { '(пусто)' }
        Stop-Break ("check-login вернул код 0, но login={0}, а не yes — считать вход подтверждённым нельзя. Разбор: {1}" -f $loginWord, $v.Файл)
    }
    if ($v.Код -ne $script:EXIT_HUMAN) {
        Stop-Break ("check-login ответил кодом {0} — это сбой канала или инструмента, а не «человек не вошёл». Разбор: {1}" -f $v.Код, $v.Файл)
    }
    # Код 20: login=no или login=unknown — зовём человека.
    $shot = Save-Screen '11-login'
    Write-Log ''
    Write-Log 'НУЖЕН ЧЕЛОВЕК: откройте magnit.ru в окне приложения на эмуляторе и войдите' 'Yellow'
    Write-Log '  (номер, код из СМС, капча, выбор адреса — родной клавиатурой).' 'Yellow'
    if (-not $opened) {
        Write-Log '  Магнит стенд открыть сам не смог: откройте раздел «Кабинеты» и нажмите «Войти» у Магнита.' 'Yellow'
    }
    Write-Log ("  Проверка входа сказала: login={0} (mg_at={1}; имя куки признаком входа не считается — сессия Магнита лежит в localStorage)." -f $v.Login, $v.MgAt) 'Yellow'
    Write-Log ("  Снимок экрана: {0}" -f $shot) 'Yellow'
    Write-Log '  Значения кук и localStorage стенд не читает и не сохраняет — только имена и слова страницы.' 'Yellow'
    if (-not $WaitHuman) {
        $hint = Get-ResumeHint 11
        Stop-Human @"
Стенд дошёл до человеческого шага и без -WaitHuman дальше не идёт (по плану это код 20).
Когда вход будет выполнен, продолжить так:
    $hint -WaitHuman
"@
    }
    for ($try = 1; $try -le 3; $try++) {
        Read-Host 'Нажмите Enter, когда вход в Магнит выполнен (или Ctrl+C, чтобы прервать)' | Out-Null
        $v = Get-LoginVerdict
        if ($v.Код -eq $script:EXIT_OK -and $v.Login -eq 'yes') {
            Write-Ok ("вход подтверждён: login=yes (mg_at={0} — для справки; значения не читались)" -f $v.MgAt)
            return
        }
        # Всё, что не «код 20», — сбой стенда или инструмента: за человеческий шаг его
        # выдавать нельзя, иначе владельца позовут из-за упавшего питона.
        if ($v.Код -ne $script:EXIT_HUMAN) {
            Stop-Break ("check-login ответил кодом {0} (login={1}) — это сбой канала или инструмента, а не «не вошёл». Разбор: {2}" -f $v.Код, $v.Login, $v.Файл)
        }
        Write-Warn ("Вход всё ещё не подтверждён: login={0} (mg_at={1}). Разбор — {2}" -f $v.Login, $v.MgAt, $v.Файл)
        if ($try -lt 3) { Write-Warn 'Проверьте, что вход действительно завершён и открыт magnit.ru, и нажмите Enter ещё раз.' }
    }
    Save-Screen '11-login-failed' | Out-Null
    Stop-Human ("Вход в Магнит не подтверждён после трёх проверок (login={0}). Смотри {1} и снимок screens\11-login-failed.png." -f $v.Login, $v.Файл)
}

# ─────────────────────────────────────────────────────────────────────────────
# Шаги плана. Каждый шаг проверяет, сделан ли он, и только потом делает.
# ─────────────────────────────────────────────────────────────────────────────

function Step-Jdk {
    $jdk = Ensure-Jdk
    Write-Ok ("JDK {0} — {1}" -f $script:JdkMajor, $jdk)
}

function Step-Sdk {
    Ensure-Sdk
    Write-Ok 'четыре опоры SDK на месте (что откуда взято — строки выше)'
}

function Step-Accel {
    Ensure-Sdk
    $r = Invoke-Tool $script:EmulatorCheck @('accel') (Join-Path (Ensure-RunDir) 'accel-check.txt') 120
    $txt = $r.Вывод
    if ($r.Код -eq 0 -and $txt -match 'WHPX|AEHD|HAXM|usable') {
        Write-Ok ("ускорение есть: " + (($txt -split "`r?`n" | Where-Object { $_.Trim() }) -join ' ').Trim())
        return
    }
    # Проверка не прошла. Если эмулятор уже работает и загрузился — ускорение доказано
    # самим фактом: риск из плана снят (эмулятор поднят владельцем). Иначе это стоп.
    $serial = $null
    foreach ($s in Get-EmulatorSerials) { if ((Get-AvdOfSerial $s) -eq $Avd) { $serial = $s; break } }
    if ($serial -and (Get-BootCompleted $serial)) {
        Write-Warn ("Проверка ускорения не прошла, но эмулятор {0} уже работает — продолжаю: ускорение доказано фактом." -f $serial)
        return
    }
    Stop-Break ("Нет аппаратного ускорения эмулятора:`n{0}" -f $txt.Trim())
}

function Step-Emulator {
    $serial = Ensure-Device
    $avdName = Get-AvdOfSerial $serial
    Save-Screen '04-emulator' | Out-Null
    Write-Ok ("эмулятор {0} (AVD {1}) работает, загрузка завершена" -f $serial, $avdName)
}

function Step-Server {
    Ensure-Server
    Write-Ok ("сервер отвечает на http://127.0.0.1:{0}/login" -f $Port)
}

function Step-Plan {
    Ensure-Server
    $run = Ensure-RunDir
    $jsonOut = Join-Path $run 'plan.json'
    $txtOut = Join-Path $run 'plan.txt'
    # Порог считаем по JSON (ключи real_rows, min_rows), а не по человеческому тексту:
    # текст печатается в кодировке консоли, и разбор его регулярками уже один раз
    # соврал «наряд пуст» там, где строк было четыре из четырёх.
    $r = Invoke-Emucart @('plan', '--base', "http://127.0.0.1:$Port", '--out', $jsonOut) $txtOut 600
    $planData = Get-JsonFile $jsonOut
    if (-not $planData) {
        Stop-Break ("emucart plan не записал plan.json (код {0}) — порог приёмки проверить нечем. Вывод: {1}" -f $r.Код, $txtOut)
    }
    $real = [int]$planData.real_rows
    $min = [int]$planData.min_rows
    Write-Log ("   настоящих ссылок Магнита: {0}, порог {1}; корзина {2} (отпечаток {3})" -f $real, $min, $planData.basket, $planData.stamp)
    foreach ($row in @($planData.rows | Where-Object { $_ -and ("$($_.url)" -match 'magnit\.ru') } | Select-Object -First 6)) {
        Write-Log ("      " + [string]$row.sku + "  нажатий " + [string]$row.clicks + "  " + [string]$row.name) 'DarkGray'
        Write-Log ("        " + (Get-SafeUrl ([string]$row.url))) 'DarkGray'
    }
    if ($planData.note) { Write-Log ("   заметка наряда: " + [string]$planData.note) 'Gray' }
    # Порог — 4 строки (поправка оркестратора): у рабочего места demo ровно 4 связи
    # Магнита с настоящими ссылками, остальные синтетические.
    if ($real -lt $min) {
        Save-Screen '06-plan' | Out-Null
        Stop-Human (@'
Пробный наряд беднее порога: настоящих ссылок Магнита {0}, а нужно {1} (поправка
оркестратора). Строки наряда — в plan.json и plan.txt файла прогона. Подменять проверку
кнопкой «Положить этот» нельзя: один товар с открытой страницы годится как дымовая
проверка, но приёмку не закрывает. Нужно решение оркестратора.
'@ -f $real, $min)
    }
    Write-Ok ("наряд готов: {0} строк с настоящими ссылками Магнита (порог {1})" -f $real, $min)
}

function Step-Apk {
    $apk = Ensure-Apk
    $facts = Get-ApkFacts $apk
    Write-Ok ("APK: {0}; версия {1} (код {2}); адрес {3} зашит" -f $apk, $facts.Версия, $facts.Код, $Server)
}

function Step-Install {
    Ensure-InstalledApp
    $inst = Get-InstalledApp
    Write-Ok ("приложение на эмуляторе: версия {0} (код {1})" -f $inst.Версия, $inst.Код)
}

function Step-OpenApp {
    Ensure-Device | Out-Null
    Ensure-InstalledApp
    Ensure-AppRunning | Out-Null
    Save-Screen '09-app' | Out-Null
    # Вход в «Мою корзину» без кода — кнопка «Посмотреть демо» на экране входа нашего
    # сайта (поправка 4 плана). Она живёт ВНУТРИ страницы WebView, а разбор вида
    # телефона видит от страницы только заголовок («Вход — Моя корзина»), поэтому
    # нажимаем её через отладочный канал: то же окно приложения, та же кнопка.
    # Входа в магазин это не касается — он остаётся человеку (шаг 11).
    # Запасного пути «по разбору вида» здесь нет нарочно: он всё равно не видит
    # содержимого страницы, а его молчание выдавало бы незнание за успех.
    Require-Cdp | Out-Null
    $j = Enter-Demo
    if ($j.Адрес) { Write-Log ("   окно приложения на адресе: " + [string]$j.Адрес) 'Gray' }
    Save-Screen '09-demo' | Out-Null
    if ($j.Итог -eq 'вошли' -or $j.Итог -eq 'вход уже есть') {
        Write-Ok ("приложение открыто, демо-рабочее место: {0}" -f $j.Итог)
        return
    }
    Stop-Human ("Войти в демо через окно приложения не вышло: {0} (адрес {1}, нажатие: {2}). Смотри снимок screens\09-demo.png." -f $j.Итог, $j.Адрес, $j.Нажатие)
}

function Step-WebViewDebug {
    Ensure-Device | Out-Null
    # Проброс порта обновляем на живой сокет именно здесь: дальше шаги 11–14 ходят
    # через него, и мёртвый проброс выглядел бы как «WebView не отвечает».
    Require-Cdp -Refresh | Out-Null
    $targets = Update-CdpTargets
    Write-Ok ("отладочный протокол окна отвечает, страниц: {0}" -f @($targets).Count)
    foreach ($t in @($targets)) { Write-Log ("      " + (Get-SafeUrl ([string]$t.url))) 'DarkGray' }

    # ОКНО МОЖЕТ ОТВЕЧАТЬ И НЕ ГРУЗИТЬ. Замер 19.10.2026: отладочный протокол живой, а
    # окно приложения на любой адрес показывает «Не удалось открыть веб-страницу»
    # (chrome-error) — заклинило WebView, при том что сеть эмулятора в порядке (ping до
    # 10.0.2.2 и 8.8.8.8 идёт, наш сервер отвечает). Бывает и вторая беда того же рода:
    # приложение показывает «Приложение не отвечает» и на нажатия не реагирует. Дальше
    # шаги 11–14 молча не работают, и разбираться приходится вручную. Говорим сразу.
    $broken = @($targets | Where-Object { ([string]$_.url) -like 'chrome-error*' })
    $hung = ''
    try {
        $dumpPath = '/sdcard/korzina-stand-ui.xml'
        Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'uiautomator', 'dump', $dumpPath) | Out-Null
        $dump = (Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'cat', $dumpPath)).Вывод
        if ($dump -match 'Закрыть приложение|Приложение не отвечает') { $hung = 'app-anr' }
    } catch { }
    if ($broken.Count -gt 0 -or $hung -or @($targets).Count -eq 0) {
        $proxy = (Invoke-Tool $script:Adb @('-s', $script:Serial, 'shell', 'settings', 'get',
                                           'global', 'global_http_proxy_host')).Вывод.Trim()
        $hint = ''
        if ($proxy -and $proxy -ne 'null' -and $proxy -ne '""') {
            $hint = " Похоже на прокси эмулятора ($proxy) — проверьте global_http_proxy_host/port."
        }
        $what = if ($hung) { 'приложение показывает «Приложение не отвечает» и не реагирует' }
                else { 'в нём «Не удалось открыть веб-страницу» (chrome-error)' }
        Stop-Human ('Окно приложения не годится для прогона: ' + $what + '.' + $hint +
            [Environment]::NewLine +
            '  Заклинило окно приложения, а не сеть: шаги 11–14 без перезапуска не пройдут.' +
            [Environment]::NewLine +
            '  Нужен человек: перезапустите эмулятор (AVD korzina) или само окно приложения, ' +
            'потом повторите с шага 8.')
    }
}

function Ensure-MagnitPage {
    if (-not (Ensure-Cdp)) { return $false }
    Update-CdpTargets | Out-Null
    $page = Get-MagnitPage
    if (-not $page) {
        # Мост сайта openStore — то же, что кнопка «Войти» у Магнита на «Кабинетах»:
        # он открывает страницу сети в том же окне. Входа это не заменяет.
        $итог = Open-MagnitInApp
        Write-Log ("   открываю Магнит в окне: " + $итог) 'Gray'
        if ($итог -ne 'Магнит открыт') { return $false }
        Update-CdpTargets | Out-Null
        $page = Get-MagnitPage
    }
    if ($page) {
        # Дорисовку ждём здесь, а не в проверке: на середине загрузки сессия Магнита
        # ещё не видна, и проверка входа сказала бы «не знаю».
        $ready = Wait-PageReady -WsUrl $page.webSocketDebuggerUrl -TimeoutSec 40
        if (-not $ready) { Write-Warn 'Страница Магнита не дорисовалась за 40 с — проверяю вход как есть.' }
        Start-Sleep -Seconds 3
    }
    return $true
}

function Ensure-Preview {
    param([string]$Name)
    # Перед снимком корзины проброс обновляем: сокет окна за прогон меняется.
    Require-Cdp -Refresh | Out-Null
    Ensure-Emucart | Out-Null
    $out = Join-Path (Ensure-RunDir) ("preview-{0}.json" -f $Name)
    # Сырой ответ Магнита (в нём адрес, телефон, куки) кладём рядом, но помечаем как
    # сырой: в репозиторий его не пускают, вычищенный — только этот файл.
    $raw = Join-Path (Ensure-RunDir) ("preview-{0}-raw.json" -f $Name)
    $r = Invoke-Emucart @('snap-preview', '--out', $out, '--raw-out', $raw) `
        (Join-Path (Ensure-RunDir) ("snap-{0}.txt" -f $Name)) 300
    if ($r.Код -ne 0 -or -not (Test-Path $out)) {
        Stop-Break ("Снимок корзины Магнита ({0}) не снялся (код {1}). Смотри snap-{0}.txt и cdp.log." -f $Name, $r.Код)
    }
    # Число позиций берём из ответа Магнита. Инструмент уже вычистил личное; если ключа
    # нет — так и скажем, а не покажем пустоту за ноль.
    $count = '(число позиций не названо)'
    $j = Get-JsonFile $out
    if ($j) {
        foreach ($key in @('itemsCount', 'items_count', 'позиций')) {
            if ($null -ne $j.$key) { $count = $j.$key; break }
        }
    }
    return [pscustomobject]@{ Путь = $out; Позиций = $count }
}

function Step-PreviewBefore {
    Require-Cdp -Refresh | Out-Null
    $p = Ensure-Preview 'before'
    Write-Ok ("корзина Магнита «до»: {0} позиций (снимок: {1})" -f $p.Позиций, $p.Путь)
    if ($p.Позиций -and $p.Позиций -ne 0) {
        Write-Warn 'Корзина Магнита не пуста. Это не остановка: сверять будем разницу, но повторный прогон кладёт всё заново — перед повтором корзину очищает владелец.'
    }
}

# Общая проверка: не встретилось ли на странице чужое (капча, проверка сети, вход).
# Признак берём у самой страницы, словами, и не пытаемся её обойти.
function Assert-NoGuard {
    param([string]$Where)
    # Незнание — не «чисто». Если канала нет, стенд не видит, что на странице, и жать
    # «Собрать» поверх капчи Магнита нельзя: это лишняя нагрузка и повод для блокировки.
    if (-not $script:CdpReady) {
        Stop-Break ("Состояние страниц Магнита неизвестно ({0}): нет отладочного канала, а нажимать «Собрать» вслепую нельзя — там может стоять проверка браузера." -f $Where)
    }
    Update-CdpTargets | Out-Null
    if (-not (Get-MagnitPage)) {
        Stop-Break ("Проверку браузера не с чем сверить ({0}): страницы Магнита в окне нет." -f $Where)
    }
    foreach ($t in @($script:CdpTargets)) {
        $u = [string]$t.url
        if ($u -match '(?i)captcha|challenge|blocked') {
            Save-Screen ('12-guard-' + $Where) | Out-Null
            Stop-Human ("Магнит встретил проверкой: {0}. Стенд её не обходит. Смотри снимок. Повтор — не раньше 20 минут." -f (Get-SafeUrl $u))
        }
    }
}

function Step-Collect {
    Ensure-AppRunning | Out-Null
    # Перед сборкой проброс обновляем: во время демо и открытия Магнита сокет окна
    # успевает смениться, а панель «Собрать» и проверка страниц идут через него.
    Require-Cdp -Refresh | Out-Null
    if (-not (Ensure-MagnitPage)) {
        Stop-Human 'Страница Магнита в окне не открылась — начинать сборку негде. Откройте magnit.ru в окне приложения и продолжите с шага 13.'
    }
    Assert-NoGuard 'before-collect'
    $since = (Get-Date).ToString('o')
    # «Собрать» — кнопка на панели приложения над страницей сети. Её нажатие и есть
    # начало сборки: приложение само ходит по карточкам руками телефона.
    if (-not (Invoke-TapText 'Собрать' '13-collect')) {
        Stop-Human @'
Кнопки «Собрать» на панели приложения не видно. Панель появляется только на странице
сети: убедитесь, что в окне открыт magnit.ru, и нажмите «Собрать» сами, затем продолжите
с шага 13: -From 13.
'@
    }
    Write-Log '   сборка пошла, жду отчёт на сервере (до 15 минут)' 'Yellow'
    $out = Join-Path (Ensure-RunDir) 'report.json'
    $r = Invoke-Emucart @('wait-report', '--since', $since, '--timeout', '900', '--out', $out) `
        (Join-Path (Ensure-RunDir) 'wait-report.txt') 1000
    if ($r.Код -eq $script:EXIT_HUMAN) {
        # Отчёт сам сказал, что позиция не легла из-за входа или проверки сети. Это
        # человеческий шаг, а не сбой: снимаем экран и зовём человека (раздел 2а плана).
        Save-Screen '13-needs-human' | Out-Null
        Stop-Human "Отчёт говорит про вход или проверку сети — нужен человек. Смотри report.json, wait-report.txt и снимок screens\13-needs-human.png."
    }
    if ($r.Код -eq $script:EXIT_NOT_FILLED) {
        # У wait-report код 30 значит «отчёт не пришёл за отведённое время» — это сбой
        # ожидания (10), а не «корзина не наполнилась» (30). Разводим их по словам
        # самого инструмента, а не по одному коду: коды у стенда и инструмента общие,
        # и слепое «30 → 30» выдало бы таймаут за вердикт.
        if ($r.Вывод -match 'НЕ ПРИШЁЛ|не пришёл') {
            Stop-Break "Отчёт о сборке не пришёл за 15 минут — это сбой ожидания, а не «корзина не наполнилась». Смотри report.json, wait-report.txt и logcat-korzina.txt."
        }
        Stop-Stand -Code $script:EXIT_NOT_FILLED -Why "Отчёт говорит, что корзина не наполнилась. Разбор: wait-report.txt и report.json."
    }
    if ($r.Код -ne 0) {
        Stop-Break ("Ожидание отчёта сорвалось (код {0}). Смотри wait-report.txt." -f $r.Код)
    }
    Write-Ok ("отчёт пришёл: {0}" -f $out)
}

function Step-PreviewAfter {
    Require-Cdp -Refresh | Out-Null
    Assert-NoGuard 'after-collect'
    Save-Screen '14-after' | Out-Null
    Get-UiTree '14-after' | Out-Null
    $p = Ensure-Preview 'after'
    Write-Ok ("корзина Магнита «после»: {0} позиций (снимок: {1})" -f $p.Позиций, $p.Путь)
    $report = Join-Path (Ensure-RunDir) 'report.json'
    $before = Join-Path (Ensure-RunDir) 'preview-before.json'
    $after = Join-Path (Ensure-RunDir) 'preview-after.json'
    if (-not (Test-Path $before) -or -not (Test-Path $after) -or -not (Test-Path $report)) {
        Stop-Break 'Для вердикта не хватает файлов: нужны preview-before.json, preview-after.json и report.json в каталоге прогона.'
    }
    $out = Join-Path (Ensure-RunDir) 'verdict.json'
    $r = Invoke-Emucart @('verdict', '--before', $before, '--after', $after, '--report', $report, '--out', $out) `
        (Join-Path (Ensure-RunDir) 'verdict.txt') 300
    $j = Get-JsonFile $out
    if (-not $j) {
        Stop-Break ("Вердикт не записался (код {0}). Смотри verdict.txt." -f $r.Код)
    }
    if ($j.ok -eq $true) {
        Write-Ok ("вердикт: {0} (легло {1} из {2}), разбор — {3}" -f $j.verdict, $j.landed, $j.planned, $out)
        return
    }
    if ($j.ok -eq $false) {
        Save-Screen '14-verdict-fail' | Out-Null
        Stop-Stand -Code $script:EXIT_NOT_FILLED -Why (
            "Корзина Магнита не наполнилась так, как обещал отчёт: {0} (легло {1} из {2}). Разбор и дефекты — {3}" -f $j.verdict, $j.landed, $j.planned, $out)
    }
    Stop-Break ("В вердикте нет ключа ok — по нему решать нельзя. Смотри {0}" -f $out)
}

function Step-BackgroundCheck {
    # Шаг 15 — необязательный: проверка фоновой сборки 1.13 на экране «Корзина».
    # Не сложилось — говорим вслух и идём дальше: приёмку решает вердикт шага 14.
    Ensure-Cdp -Refresh | Out-Null
    if (-not $script:CdpReady) { Write-Warn 'Шаг 15 пропущен: нет отладочного протокола.'; return }
    Update-CdpTargets | Out-Null
    $page = Get-OurPage
    if (-not $page) { Write-Warn 'Шаг 15: нашей страницы в окне нет — пропускаю.'; return }
    # Открываем наш экран корзины мостом сайта (openApp) — тем же, чем ходит нижнее меню.
    $null = Invoke-CdpEvalSafe -WsUrl $page.webSocketDebuggerUrl -Expression "window.Korzina && window.Korzina.openApp('/phone/basket')"
    Start-Sleep -Seconds 8
    Update-CdpTargets | Out-Null
    $page = Get-OurPage
    if (-not $page) { Write-Warn 'Шаг 15: экран корзины не открылся.'; return }
    # Из страницы достаём только строки про сборку: остальной её текст (адрес, суммы)
    # стенду не нужен, и в файл прогона он не попадает.
    $probe = Invoke-CdpEvalSafe -WsUrl $page.webSocketDebuggerUrl -Expression @'
(function(){
  var t = document.body ? document.body.innerText : '';
  var lines = t.split('\n').filter(function(s){return /лежит|собираю|Корзина в магазинах/.test(s)});
  return {"есть": t.indexOf('Корзина в магазинах') >= 0,
          "строки": lines.slice(0,8).map(function(s){return s.replace(/\s+/g,' ').trim().slice(0,120)})};
})()
'@
    if (-not $probe) { Write-Warn 'Шаг 15: экран корзины не прочитался.'; return }
    if ($probe.есть -eq $true) {
        Write-Ok 'на экране «Корзина в магазинах» есть строки о сборке — корзина не удвоилась'
        foreach ($ln in @($probe.строки)) { Write-Log ("      " + [string]$ln) 'DarkGray' }
    } else {
        Write-Warn 'Шаг 15: раздела «Корзина в магазинах» на экране не видно. Это не остановка прогона, но отметить надо.'
    }
}

function Step-Gates {
    # Полный прогон pytest — ворота оркестратора, а не стенда: стенд не должен
    # подменять их своим запуском и не должен мешать прогону живой проверки.
    Write-Skip 'шаг 16 не делаю: полный прогон тестов держат ворота оркестратора (распоряжение Лида)'
}

# ─────────────────────────────────────────────────────────────────────────────
# Прогон шагов
# ─────────────────────────────────────────────────────────────────────────────

$plan = @(
    [pscustomobject]@{ N = 1;  Имя = 'JDK 17+';                                             Дело = { Step-Jdk } },
    [pscustomobject]@{ N = 2;  Имя = 'SDK: cmdline-tools, platform-tools, emulator, образ'; Дело = { Step-Sdk } },
    [pscustomobject]@{ N = 3;  Имя = 'Аппаратное ускорение эмулятора';                      Дело = { Step-Accel } },
    [pscustomobject]@{ N = 4;  Имя = "Эмулятор $Avd уже поднят (только проверка)";          Дело = { Step-Emulator } },
    [pscustomobject]@{ N = 5;  Имя = "Местный сервер на 127.0.0.1:$Port";                   Дело = { Step-Server } },
    [pscustomobject]@{ N = 6;  Имя = 'Пробный наряд: строки со ссылками Магнита';           Дело = { Step-Plan } },
    [pscustomobject]@{ N = 7;  Имя = 'APK с адресом сервера (сборка в Docker)';             Дело = { Step-Apk } },
    [pscustomobject]@{ N = 8;  Имя = 'Установка APK на эмулятор';                           Дело = { Step-Install } },
    [pscustomobject]@{ N = 9;  Имя = 'Запуск приложения и вход в демо';                     Дело = { Step-OpenApp } },
    [pscustomobject]@{ N = 10; Имя = 'Отладочный протокол окна приложения';                 Дело = { Step-WebViewDebug } },
    [pscustomobject]@{ N = 11; Имя = 'ЧЕЛОВЕК: вход в magnit.ru';                            Дело = { Step-HumanLogin } },
    [pscustomobject]@{ N = 12; Имя = 'Корзина Магнита «до»';                                Дело = { Step-PreviewBefore } },
    [pscustomobject]@{ N = 13; Имя = '«Собрать» и ожидание отчёта';                          Дело = { Step-Collect } },
    [pscustomobject]@{ N = 14; Имя = 'Корзина «после» и вердикт';                            Дело = { Step-PreviewAfter } },
    [pscustomobject]@{ N = 15; Имя = 'Необязательно: фоновая сборка 1.13';                   Дело = { Step-BackgroundCheck } },
    [pscustomobject]@{ N = 16; Имя = 'Ворота после прогона (держит оркестратор)';            Дело = { Step-Gates } }
)

function Invoke-Stand {
    if ($From -lt 1 -or $Until -gt 16 -or $From -gt $Until) {
        Stop-Break ("Шаги заданы неверно: -From {0} -Until {1}. Всего шагов 16." -f $From, $Until)
    }
    $run = Ensure-RunDir
    Write-Log ''
    Write-Log '══════════════════════════════════════════════════════════════════' 'Cyan'
    Write-Log '  Стенд эмулятора «Моей корзины»' 'Cyan'
    Write-Log ("  шаги {0}–{1} из 16; AVD {2}; сервер {3}; адрес в APK {4}" -f $From, $Until, $Avd, "127.0.0.1:$Port", $Server) 'Cyan'
    Write-Log ("  файлы прогона: {0}" -f $run) 'Cyan'
    Write-Log '══════════════════════════════════════════════════════════════════' 'Cyan'
    Write-Log ''
    Write-Log 'Поправки оркестратора соблюдены: эмулятор владельца не перезапускаю, снимок AVD не трогаю,'
    Write-Log 'второй экземпляр не поднимаю; Магнит наполняется только через приложение; сырое — вне репозитория.'

    foreach ($item in $plan) {
        if ($item.N -lt $From -or $item.N -gt $Until) {
            $script:StepsSkipped.Add(("{0} {1}" -f $item.N, $item.Имя))
            continue
        }
        Write-Step $item.N $item.Имя
        $null = & $item.Дело
        $script:StepsDone.Add(("{0} {1}" -f $item.N, $item.Имя))
    }
    Save-Logcat | Out-Null
    return $run
}

# ─────────────────────────────────────────────────────────────────────────────
# Вход в стенд: разбираем остановку на код выхода и говорим, что делать дальше.
# ─────────────────────────────────────────────────────────────────────────────

$exitCode = $EXIT_OK
$exitWhy = ''
try {
    Enter-StandLock
    $runDir = Invoke-Stand
} catch {
    $e = $_.Exception
    # Свой код выхода лежит в данных исключения; на случай обёртки смотрим и вложенное.
    $code = $e.Data['stand_exit']
    if (-not $code -and $e.InnerException) { $code = $e.InnerException.Data['stand_exit'] }
    if ($code) {
        $exitCode = [int]$code
    } else {
        $exitCode = $EXIT_BREAK
    }
    $exitWhy = $e.Message
    if (-not $exitWhy -and $e.InnerException) { $exitWhy = $e.InnerException.Message }
    Write-Log ''
    switch ($exitCode) {
        $EXIT_HUMAN { Write-Log 'ОСТАНОВКА: нужен человек (код 20).' 'Yellow' }
        $EXIT_NOT_FILLED { Write-Log 'ОСТАНОВКА: корзина не наполнилась (код 30).' 'Red' }
        default { Write-Log 'ОСТАНОВКА: сбой стенда (код 10).' 'Red' }
    }
    Write-Log $exitWhy 'Red'
    if (-not $code -and $env:DSH_STAND_TRACE) { Write-Log $_.ScriptStackTrace 'DarkGray' }
} finally {
    # Замок снимаем всегда: и после удачного прогона, и после остановки.
    Exit-StandLock
}

Write-Log ''
if ($exitCode -eq $EXIT_OK) {
    Write-Log ('ГОТОВО (код 0). Шагов пройдено: {0}.' -f $script:StepsDone.Count) 'Green'
} else {
    $lastDone = if ($script:StepsDone.Count) { $script:StepsDone[$script:StepsDone.Count - 1] } else { 'ни одного' }
    Write-Log ("Шагов пройдено: {0}. Последний: {1}" -f $script:StepsDone.Count, $lastDone) 'Yellow'
    if ($exitCode -eq $EXIT_HUMAN) {
        Write-Log ("Продолжить после человеческого шага: {0} -WaitHuman" -f (Get-ResumeHint 11)) 'Yellow'
    }
}
if ($script:StepsSkipped.Count) {
    Write-Log ("Вне запрошенного отрезка шагов ({0}–{1}) осталось: {2} — их можно добрать ключами -From/-Until." -f $From, $Until, $script:StepsSkipped.Count) 'Gray'
}
if ($script:RunDir) {
    Write-Log ("Файлы прогона: {0}" -f $script:RunDir) 'Gray'
    Write-Log ("Журнал прогона: {0}" -f (Join-Path $script:RunDir 'stand.log')) 'Gray'
}
Write-Log 'Коды выхода: 0 — готово, 10 — сбой стенда, 20 — нужен человек, 30 — корзина не наполнилась.' 'Gray'
exit $exitCode
