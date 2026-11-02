param(
    [ValidateSet('start', 'stop', 'restart')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$logPath = Join-Path $projectRoot 'data\lifecycle.log'
$desiredState = Join-Path $projectRoot 'data\service_state.txt'

function Write-LifecycleLog([string]$message) {
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" -Encoding utf8
}

function Stop-AijiStack {
    param([switch]$IncludeNapCat)
    # Stop only listeners verified as this project's services.
    # QQ + NapCat survive a plain restart: NapCat is injected into the QQ process, its
    # OneBot socket reconnects to AstrBot by itself, and killing the launcher takes QQ
    # down with it (2026-11-02: a "restart" left the bot offline for minutes and needed a
    # fresh QQ login). Only 'stop' is allowed to touch them.
    $astr = Get-NetTCPConnection -LocalPort 6199 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($astr) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($astr.OwningProcess)" -ErrorAction SilentlyContinue
        if ($p -and $p.CommandLine -match 'main\.py' -and $p.ExecutablePath -match '\\python(?:\.exe)?$') {
            Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
            Write-LifecycleLog "AstrBot stopped pid=$($p.ProcessId)$(if (-not $IncludeNapCat) { ' (QQ/NapCat kept running)' })"
        }
    }
    if ($IncludeNapCat) {
        $napcat = Get-CimInstance Win32_Process -Filter "Name='NapCatWinBootMain.exe'" -ErrorAction SilentlyContinue
        foreach ($p in $napcat) {
            if ($p.ExecutablePath -like 'D:\NapCat\shell\*') {
                Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
                Write-LifecycleLog "NapCat launcher stopped pid=$($p.ProcessId)"
            }
        }
    }
    $core = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($core) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($core.OwningProcess)" -ErrorAction SilentlyContinue
        if ($p -and $p.CommandLine -match 'aiji\.cli' -and $p.CommandLine -match 'serve') {
            Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
            Write-LifecycleLog "AIJiYi stopped pid=$($p.ProcessId)"
        }
    }
}

try {
    # The HTTP response must be sent before the current service is stopped.
    Start-Sleep -Seconds 2
    if ($Action -eq 'stop') {
        Set-Content -LiteralPath $desiredState -Value 'stopped' -Encoding ascii
    } else {
        Set-Content -LiteralPath $desiredState -Value 'running' -Encoding ascii
    }
    if ($Action -eq 'stop') { Stop-AijiStack -IncludeNapCat }
    elseif ($Action -eq 'restart') { Stop-AijiStack }
    if ($Action -in @('start', 'restart')) {
        if ($Action -eq 'restart') { Start-Sleep -Seconds 2 }
        Write-LifecycleLog "Stack start requested"
        & (Join-Path $PSScriptRoot 'ensure_console.ps1')
    }
} catch {
    Write-LifecycleLog "ERROR $($_.Exception.Message)"
}
