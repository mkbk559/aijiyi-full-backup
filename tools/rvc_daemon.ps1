<#
.SYNOPSIS
    保证 RVC 常驻换声服务在跑（没有就拉起来，有就什么都不做）。

.DESCRIPTION
    2026-11-03：RVC 换成常驻服务后，热调用从 14 064 ms 降到 2 892 ms（×4.86），
    但服务本身不会自己活过来。这个脚本干三件事：探活 → 没活就起 → 等它真能应答。

    为什么不直接注册计划任务：改系统状态应该由你点头。要挂开机自启，自己跑一次
        schtasks /Create /SC ONLOGON /TN AIJiYi-RVC /TR "powershell -NoProfile -File D:\AIJiYi\tools\rvc_daemon.ps1"

.PARAMETER Stop
    停掉服务（POST /shutdown）。

.PARAMETER Port
    端口，默认 18601（和 tools/rvc_convert.py 的默认一致）。

.EXAMPLE
    powershell -NoProfile -File tools\rvc_daemon.ps1
    powershell -NoProfile -File tools\rvc_daemon.ps1 -Stop
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [int]$Port = 18601,
    [int]$WaitSeconds = 180,
    [ValidateSet('auto', 'cuda', 'cpu')]
    [string]$Device = 'auto'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
# 优先 GPU 环境（voice-engine/rvc/.venv：Python 3.12 + torch cu128，实测 6 s 素材 503 ms）；
# 没有才退回 CPU 环境（同样的活要 9 519 ms，差 19 倍）。可用 -Device 强制覆盖。
$gpuPy = Join-Path $root 'voice-engine\rvc\.venv\Scripts\python.exe'
$cpuPy = Join-Path $root 'voice-engine\.venv\Scripts\python.exe'
if (Test-Path $gpuPy) { $python = $gpuPy; $device = 'cuda' }
else { $python = $cpuPy; $device = 'cpu' }
$server = Join-Path $root 'tools\rvc_server.py'
$logDir = Join-Path $root 'logs'
$logFile = Join-Path $logDir 'rvc-server.log'
$base = "http://127.0.0.1:$Port"

# 本机有全局代理（HTTP_PROXY → Clash）。Windows PowerShell 5.1 的 Invoke-WebRequest
# 没有可用的 -Proxy $null 写法（会直接卡住），所以这里清空四个代理变量再探活。
$env:HTTP_PROXY = ''
$env:HTTPS_PROXY = ''
$env:http_proxy = ''
$env:https_proxy = ''

function Invoke-Local {
    param([string]$Uri, [string]$Method = 'GET', [string]$Body = '')
    $args = @{
        Uri = $Uri; Method = $Method; TimeoutSec = 10; UseBasicParsing = $true
    }
    if ($Body) {
        $args['Body'] = $Body
        $args['ContentType'] = 'application/json'
    }
    return Invoke-WebRequest @args
}

if ($Device -ne 'auto') { $device = $Device }

function Get-Health {
    try {
        $r = Invoke-Local -Uri "$base/health"
        return ($r.Content | ConvertFrom-Json)
    } catch {
        return $null
    }
}

if ($Stop) {
    $h = Get-Health
    if (-not $h) { Write-Host "RVC 服务没在跑（$base）"; exit 0 }
    try {
        Invoke-Local -Uri "$base/shutdown" -Method 'POST' -Body '{}' | Out-Null
        Write-Host "已请求关闭：$base"
    } catch {
        Write-Host "关闭请求失败：$_"
        exit 1
    }
    exit 0
}

$h = Get-Health
if ($h) {
    Write-Host ("已在运行：{0}  model={1}  device={2}  loaded_ms={3}  calls={4}" -f `
        $base, $h.model, $h.device, $h.loaded_ms, $h.calls)
    exit 0
}

if (-not (Test-Path $python)) { Write-Error "缺 RVC venv：$python"; exit 1 }
if (-not (Test-Path $server)) { Write-Error "缺服务脚本：$server"; exit 1 }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 日志轮转：Start-Process 的重定向是"截断"而非追加，先把旧的挪走
if (Test-Path $logFile) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Move-Item -Force $logFile "$logDir\rvc-server-$stamp.log"
}

# 起服务：**不要**用 -RedirectStandardOutput。Windows 上那会把子进程绑到父进程的句柄，
# 被管道捕获的调用方会一直挂着（实测十几分钟不返回）。
# 改成让服务自己写日志（tools/rvc_server.py --log），父进程只管 launch 完就走。
Start-Process -FilePath $python `
    -ArgumentList @($server, '--port', "$Port", '--log', $logFile, '--device', $device) `
    -WorkingDirectory $root -WindowStyle Hidden | Out-Null

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $h = Get-Health
    if ($h) {
        Write-Host ("已启动：{0}  model={1}  loaded_ms={2}" -f $base, $h.model, $h.loaded_ms)
        exit 0
    }
}

Write-Error "等了 $WaitSeconds 秒仍没有 /health，看日志：$logFile / $logFile.err"
exit 1
