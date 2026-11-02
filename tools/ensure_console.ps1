# Run by Task Scheduler every five minutes while the user is signed in.
$mutex = New-Object System.Threading.Mutex($false, 'Local\AIJiYiServiceWatchdog')
if (-not $mutex.WaitOne(0)) { exit 0 }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExe = 'D:\Python313\python.exe'
$dataDir = Join-Path $projectRoot 'data'
$desiredState = Join-Path $dataDir 'service_state.txt'
if ((Test-Path -LiteralPath $desiredState) -and
    ((Get-Content -LiteralPath $desiredState -Raw).Trim() -eq 'stopped')) { exit 0 }
$astrbotRoot = 'D:\AstrBot'
$astrbotPython = Join-Path $astrbotRoot '.venv\Scripts\python.exe'
$napcatRoot = 'D:\NapCat\shell'
$napcatExe = Join-Path $napcatRoot 'NapCatWinBootMain.exe'

# Start-Process -RedirectStandardOutput truncates its target, so a restart used to
# wipe every log. Rotate the non-empty file to <name>.<stamp>.log first and keep the
# newest few, so yesterday's evidence survives today's restart.
function Rotate-Log {
    param([string]$Path, [int]$Keep = 5)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    if ((Get-Item -LiteralPath $Path).Length -le 0) { return }
    $dir = Split-Path -Parent $Path
    $base = Split-Path -Leaf $Path
    $stem = [IO.Path]::GetFileNameWithoutExtension($base)
    $target = Join-Path $dir ("$stem." + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    try { Move-Item -LiteralPath $Path -Destination $target -ErrorAction Stop } catch { return }
    Get-ChildItem -LiteralPath $dir -File -Filter "$stem.*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
}

# AstrBot writes emoji/Chinese to stdout; under the default cp936 console it raised
# ~2000 x "UnicodeEncodeError: 'gbk'" per day and left astrbot.err.log unreadable.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
# AstrBot's download code uses aiohttp with trust_env=True, so it inherits the machine's
# HTTP_PROXY (127.0.0.1:7897). QQ rich media must not go through that tunnel: on
# 2026-11-02 every group image failed with "HTTP status code: 4xx" until AstrBot was
# restarted, and the same CDN answers 400 when the request is rewritten by a proxy.
# Keep local QQ media direct, leave the proxy for everything else.
$noProxy = @($env:NO_PROXY, $env:no_proxy, 'multimedia.nt.qq.com.cn') |
    Where-Object { $_ } | ForEach-Object { $_.Split(',') } |
    Where-Object { $_ } | Select-Object -Unique
$env:NO_PROXY = ($noProxy -join ',')
$env:no_proxy = $env:NO_PROXY

# Optional diagnostics collector (opt-in: create data\monitors.on to enable).
# field_monitor.py loops forever and writes every 15s, so its own output file is
# the liveness signal: stale for >2 minutes means the process died and is restarted
# here (this script runs under Task Scheduler, i.e. outside any tool sandbox).
# proactive_audit_1h.py is deliberately NOT restarted: DURATION_S=3600, it is a
# one-shot audit that exits by itself and deletes data\proactive_audit_1h.pid.
$monitorsFlag = Join-Path $dataDir 'monitors.on'
if (Test-Path -LiteralPath $monitorsFlag) {
    $pythonw = 'D:\Python313\pythonw.exe'
    $monitorOut = Join-Path $dataDir 'field_monitor.jsonl'
    $monitorAlive = $false
    if (Test-Path -LiteralPath $monitorOut) {
        $monitorAlive = ((Get-Date) - (Get-Item -LiteralPath $monitorOut).LastWriteTime).TotalSeconds -le 120
    }
    if (-not $monitorAlive -and (Test-Path -LiteralPath $pythonw)) {
        Start-Process -FilePath $pythonw -ArgumentList 'tools\start_field_monitor.py' `
            -WorkingDirectory $projectRoot -WindowStyle Hidden
    }
}

$napcat = Get-NetTCPConnection -LocalPort 6099 -State Listen -ErrorAction SilentlyContinue
if (-not $napcat -and (Test-Path -LiteralPath $napcatExe)) {
    $env:NAPCAT_PATCH_PACKAGE = Join-Path $napcatRoot 'qqnt.json'
    $env:NAPCAT_LOAD_PATH = Join-Path $napcatRoot 'loadNapCat.js'
    $env:NAPCAT_INJECT_PATH = Join-Path $napcatRoot 'NapCatWinBootHook.dll'
    $env:NAPCAT_LAUNCHER_PATH = $napcatExe
    $env:NAPCAT_MAIN_PATH = Join-Path $napcatRoot 'napcat.mjs'
    Start-Process -FilePath $napcatExe `
        -ArgumentList 'D:\QQNT\QQ.exe', (Join-Path $napcatRoot 'NapCatWinBootHook.dll') `
        -WorkingDirectory $napcatRoot -WindowStyle Hidden
}

$astrbot = Get-NetTCPConnection -LocalPort 6199 -State Listen -ErrorAction SilentlyContinue
if (-not $astrbot -and (Test-Path -LiteralPath $astrbotPython)) {
    $runtimeConfig = Join-Path $projectRoot 'config\runtime.json'
    if (Test-Path -LiteralPath $runtimeConfig) {
        try {
            $qqSettings = Get-Content -LiteralPath $runtimeConfig -Raw -Encoding utf8 | ConvertFrom-Json
            $qqMap = @{
                qq_mode = 'AIJI_QQ_MODE'
                qq_auto_interject_chance = 'AIJI_QQ_AUTO_INTERJECT_CHANCE'
                qq_auto_max_chance = 'AIJI_QQ_AUTO_MAX_CHANCE'
                qq_auto_cooldown = 'AIJI_QQ_AUTO_COOLDOWN'
                qq_auto_hourly_max = 'AIJI_QQ_AUTO_HOURLY_MAX'
                qq_auto_daily_max = 'AIJI_QQ_AUTO_DAILY_MAX'
                qq_auto_burst_max = 'AIJI_QQ_AUTO_BURST_MAX'
                qq_auto_min_recent = 'AIJI_QQ_AUTO_MIN_RECENT'
                qq_heart_silence = 'AIJI_HEART_SILENCE'
                qq_heart_cooldown = 'AIJI_HEART_COOLDOWN'
                qq_heart_daily = 'AIJI_HEART_DAILY'
            }
            foreach ($name in $qqMap.Keys) {
                $value = $qqSettings.$name
                if ($null -ne $value) {
                    [Environment]::SetEnvironmentVariable($qqMap[$name], [string]$value, 'Process')
                }
            }
        } catch { }
    }
    Rotate-Log (Join-Path $dataDir 'astrbot.out.log')
    Rotate-Log (Join-Path $dataDir 'astrbot.err.log')
    Start-Process -FilePath $astrbotPython -ArgumentList 'main.py' `
        -WorkingDirectory $astrbotRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir 'astrbot.out.log') `
        -RedirectStandardError (Join-Path $dataDir 'astrbot.err.log')
}

try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/health' -TimeoutSec 3 -UseBasicParsing
    if ($response.StatusCode -eq 200) { exit 0 }
} catch { }

# An occupied port must not cause repeated failed launches.
$listener = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
if ($listener) { exit 1 }
if (-not (Test-Path -LiteralPath $pythonExe)) { exit 2 }

$runtimeConfig = Join-Path $projectRoot 'config\runtime.json'
$launchArgs = @('-m', 'aiji.senses.cli')
if (Test-Path -LiteralPath $runtimeConfig) { $launchArgs += @('--config', $runtimeConfig) }
$launchArgs += 'serve'
Rotate-Log (Join-Path $dataDir 'console.out.log')
Rotate-Log (Join-Path $dataDir 'console.err.log')
Start-Process -FilePath $pythonExe `
    -ArgumentList $launchArgs `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $dataDir 'console.out.log') `
    -RedirectStandardError (Join-Path $dataDir 'console.err.log')
exit 0
