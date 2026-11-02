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

$pidFile = Join-Path $logDir 'rvc-server.pid'

if ($Stop) {
    $h = Get-Health
    $killed = $false
    if ($h) {
        try {
            Invoke-Local -Uri "$base/shutdown" -Method 'POST' -Body '{}' | Out-Null
            Write-Host "已请求关闭：$base"
        } catch {
            Write-Host "关闭请求失败：$_"
        }
        # /shutdown 只是让服务自己退出；等它真退出，别留一个还占着显存/端口的幽灵
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Milliseconds 500
            if (-not (Get-Health)) { break }
        }
    }
    if (Get-Health) {
        # 还活着就按 pid 硬停（pid 文件由启动分支写）
        $victim = $null
        if (Test-Path $pidFile) {
            $victim = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        }
        if (-not $victim) {
            $victim = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                Where-Object { $_.CommandLine -like "*rvc_server.py*--port $Port*" } |
                Select-Object -First 1 -ExpandProperty ProcessId)
        }
        if ($victim) {
            try {
                Stop-Process -Id ([int]$victim) -Force -ErrorAction Stop
                $killed = $true
                Write-Host "已强制结束 pid=$victim（/shutdown 没停住）"
            } catch {
                Write-Host "强制结束失败：$_"
            }
        }
    }
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
    if (-not $h -and -not $killed) { Write-Host "RVC 服务没在跑（$base）" }
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

# 起服务：必须**完全脱离**调用方的句柄，否则任何"捕获输出"的调用方（python subprocess、
# 管道里的 shell）都会一直挂到超时——Start-Process 起出来的孙进程仍握着父进程的 stdout 管道。
# Win32_Process.Create 由 WMI 代起进程，不继承任何句柄，这是 Windows 上最干净的脱离方式。
$cmdLine = '"{0}" "{1}" --port {2} --log "{3}" --device {4}' -f `
    $python, $server, $Port, $logFile, $device
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmdLine; CurrentDirectory = $root
}
if ($created.ReturnValue -ne 0) {
    Write-Error "起服务失败（Win32_Process.Create 返回 $($created.ReturnValue)）：$cmdLine"
    exit 1
}
Write-Host "已拉起进程 pid=$($created.ProcessId)"
# 记下 pid：/shutdown 有时停不住（比如推理线程卡在 CUDA 上），-Stop 要靠它兜底
Set-Content -Path $pidFile -Value $created.ProcessId -Encoding ascii

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
