# Выполнить JavaScript в окне приложения через отладочный протокол (CDP).
# Использование: pwsh -File tools/cdp-eval.ps1 -Expression "window.Korzina.openStore('magnit')"
param(
    [Parameter(Mandatory = $true)][string]$Expression,
    [int]$Port = 9222
)

$ErrorActionPreference = 'Stop'
$adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
$appPid = (& $adb shell pidof ru.moyakorzina.app).Trim()
if (-not $appPid) { throw 'приложение не запущено' }
& $adb forward --remove-all | Out-Null
& $adb forward "tcp:$Port" "localabstract:webview_devtools_remote_$appPid" | Out-Null
$pages = Invoke-RestMethod "http://127.0.0.1:$Port/json" -TimeoutSec 8
$page = $pages | Where-Object { $_.type -eq 'page' -and $_.url -like 'http*' } | Select-Object -First 1
if (-not $page) { throw 'страница в окне приложения не найдена' }
Write-Host "окно: $($page.url)"

$ws = [System.Net.WebSockets.ClientWebSocket]::new()
$ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).Wait()
$payload = [Text.Encoding]::UTF8.GetBytes((@{ id = 1; method = 'Runtime.evaluate'
    params = @{ expression = $Expression; returnByValue = $true; awaitPromise = $true } } |
    ConvertTo-Json -Compress -Depth 6))
$ws.SendAsync([ArraySegment[byte]]::new($payload), 'Text', $true, [Threading.CancellationToken]::None).Wait()
$buffer = New-Object byte[] 65536
$result = $ws.ReceiveAsync([ArraySegment[byte]]::new($buffer), [Threading.CancellationToken]::None).Result
$text = [Text.Encoding]::UTF8.GetString($buffer, 0, $result.Count)
$ws.Dispose()
$answer = $text | ConvertFrom-Json
if ($answer.result.result.value -ne $null) { $answer.result.result.value } else { $text }
