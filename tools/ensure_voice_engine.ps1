# 确保"定制语音"引擎（Seed-VC 9880）在启动控制台/开机时可用。
# - 幂等：端口已在监听就直接返回
# - 只有当前声线需要本地转换器（profile.json 里有 postprocess）时才启动
# - 最长等 120 秒；失败时指向 Seed-VC 日志
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Seed = Join-Path $Root "voice-engine\seed-vc"
$Voices = Join-Path $Root "data\voices"
$Port = 9880

function Test-VoicePort {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $Port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

if (Test-VoicePort) {
    Write-Output "voice-engine: already listening on 127.0.0.1:$Port"
    exit 0
}

$ActiveId = "edge-default"
$ActiveFile = Join-Path $Voices "active_voice.txt"
if (Test-Path -LiteralPath $ActiveFile) {
    $value = (Get-Content -LiteralPath $ActiveFile -Raw).Trim()
    if ($value) { $ActiveId = $value }
}

$ProfileFile = Join-Path $Voices (Join-Path $ActiveId "profile.json")
$NeedsEngine = $false
if (Test-Path -LiteralPath $ProfileFile) {
    $NeedsEngine = (Get-Content -LiteralPath $ProfileFile -Raw) -match '"postprocess"'
}
if (-not $NeedsEngine) {
    Write-Output "voice-engine: active profile '$ActiveId' does not need a local engine"
    exit 0
}

$Starter = Join-Path $Seed "start-aiji.ps1"
if (-not (Test-Path -LiteralPath $Starter)) {
    Write-Output "voice-engine: starter missing: $Starter"
    exit 1
}

Push-Location $Seed
try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Starter
} finally {
    Pop-Location
}

for ($i = 1; $i -le 120; $i++) {
    Start-Sleep -Seconds 1
    if (Test-VoicePort) {
        Write-Output "voice-engine: ready for '$ActiveId' after ${i}s"
        exit 0
    }
}
Write-Output "voice-engine: not ready after 120s; see $Seed\aiji-seed-vc.err.log"
exit 1
