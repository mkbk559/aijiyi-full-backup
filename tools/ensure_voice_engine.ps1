# 检查/拉起"定制语音"的本地转换器（按 profile 的 postprocess 类型自适应）。
#
# 两种接法：
#   - command：RVC 之类 CLI 转换器，**不需要常驻进程**，只确认可执行文件在
#   - url：HTTP 常驻引擎（旧 Seed-VC 那种），需要监听端口
#
# 设计原则：**永不阻塞启动、永不非零退出**。转换器缺失只打印"会回退"，
# 因为 voice_service 本身会退回 edge 音色（并记 last_fallback）。
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Voices = Join-Path $Root "data\voices"

function Test-Port([int]$port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

$ActiveId = "edge-default"
$ActiveFile = Join-Path $Voices "active_voice.txt"
if (Test-Path -LiteralPath $ActiveFile) {
    $value = (Get-Content -LiteralPath $ActiveFile -Raw).Trim()
    if ($value) { $ActiveId = $value }
}

$ProfileFile = Join-Path $Voices (Join-Path $ActiveId "profile.json")
if (-not (Test-Path -LiteralPath $ProfileFile)) {
    Write-Output "voice-engine: active profile '$ActiveId' not found; nothing to start"
    exit 0
}
$profile = Get-Content -LiteralPath $ProfileFile -Raw | ConvertFrom-Json
$pp = $profile.postprocess
if (-not $pp) {
    Write-Output "voice-engine: '$ActiveId' needs no local engine"
    exit 0
}

# ---- CLI 转换器（RVC 等）：只查文件，不起进程 ----
$names = @($pp.PSObject.Properties.Name)
if (($names -contains "command") -and $pp.command) {
    $exe = [string](@($pp.command)[0])
    if ([System.IO.Path]::IsPathRooted($exe)) {
        if (Test-Path -LiteralPath $exe) {
            Write-Output "voice-engine: CLI converter ready for '$ActiveId': $exe"
        } else {
            Write-Output "voice-engine: CLI converter missing for '$ActiveId': $exe (will fall back to edge)"
        }
        exit 0
    }
    $found = Get-Command $exe -ErrorAction SilentlyContinue
    if ($found) {
        Write-Output "voice-engine: CLI converter ready for '$ActiveId': $($found.Source)"
    } else {
        Write-Output "voice-engine: CLI converter '$exe' not on PATH for '$ActiveId' (will fall back to edge)"
    }
    exit 0
}

# ---- HTTP 常驻引擎：按端口判活，按脚本拉起 ----
$url = [string]$pp.url
if (-not $url) {
    Write-Output "voice-engine: '$ActiveId' has postprocess but no url/command; nothing to start"
    exit 0
}
$port = 9880
try {
    $parsed = [System.Uri]$url
    if ($parsed.Port -gt 0) { $port = $parsed.Port }
} catch {
    Write-Output "voice-engine: cannot parse postprocess.url '$url'; nothing to start"
    exit 0
}
if (Test-Port $port) {
    Write-Output "voice-engine: engine already listening on 127.0.0.1:$port"
    exit 0
}

# 启动脚本：环境变量优先；旧 Seed-VC 位置仅作兼容（2026-11-02 已弃用并删除）
$starter = $env:AIJI_VOICE_ENGINE_START
if (-not $starter) {
    $legacy = Join-Path $Root "voice-engine\seed-vc\start-aiji.ps1"
    if (Test-Path -LiteralPath $legacy) { $starter = $legacy }
}
if (-not $starter -or -not (Test-Path -LiteralPath $starter)) {
    Write-Output ("voice-engine: '{0}' points at {1}, but no engine is installed" -f $ActiveId, $url)
    Write-Output "              custom voice will fall back to the edge voice"
    exit 0
}

Write-Output "voice-engine: starting $starter"
& powershell -NoProfile -ExecutionPolicy Bypass -File $starter | Out-Host
for ($i = 1; $i -le 120; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Port $port) {
        Write-Output "voice-engine: ready for '$ActiveId' after ${i}s"
        exit 0
    }
}
Write-Output "voice-engine: not ready after 120s; custom voice will fall back to edge"
exit 0
