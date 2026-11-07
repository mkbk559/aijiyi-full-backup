# Run by Task Scheduler every five minutes while the user is signed in.
# Does ONE round of "douyin group proactive chat" (read group -> aiji thinks -> send -> read back).
# ASCII-only on purpose: PowerShell 5.1 parses a no-BOM .ps1 as ANSI/GBK and a CJK literal
# inside would break the whole script (see excluded#196). The group name lives in
# config/douyin_chat.json (UTF-8 JSON) and is read as data, never as source.
$mutex = New-Object System.Threading.Mutex($false, 'Local\AIJiYiDouyinChat')
if (-not $mutex.WaitOne(0)) { exit 0 }

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataDir = Join-Path $projectRoot 'data'
$logDir = Join-Path $dataDir 'logs'
$cfgPath = Join-Path $projectRoot 'config\douyin_chat.json'
$logPath = Join-Path $logDir 'douyin_chat.log'

if (-not (Test-Path -LiteralPath $pythonExe)) { exit 0 }
if (-not (Test-Path -LiteralPath $cfgPath)) { exit 0 }

# "stopped" switch, same convention as ensure_console.ps1
$offSwitch = Join-Path $dataDir 'douyin_chat_state.txt'
if ((Test-Path -LiteralPath $offSwitch) -and
    ((Get-Content -LiteralPath $offSwitch -Raw).Trim() -eq 'stopped')) { exit 0 }

$cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($cfg.enabled -eq $false) { exit 0 }
$group = [string]$cfg.group
if ([string]::IsNullOrWhiteSpace($group)) { exit 0 }

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Start-Process -RedirectStandardOutput truncates instead of appending, so rotate first
# and keep the newest few runs (same fix as ensure_console.ps1).
function Rotate-Log {
    param([string]$Path, [int]$Keep = 8)
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
Rotate-Log -Path $logPath

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
# 抖音 /chat 在 headless 会崩，所以用有头窗口 + 挪到屏幕外：人看不见，但渲染正常。
# 2026-11-05 真机验过：offscreen 窗口里成功发消息并回读 verified=true。
# headless 已默认（/chat 在 headless 下能跑），不再需要对屏幕外窗口
# $env:AIJI_DOUYIN_OFFSCREEN = '1'
# Without this the child's UTF-8 stdout is decoded with the console codepage (GBK) and the
# JSON log turns into mojibake: {"group": "鐜栫帠澶х帇涓囧瞾"}. Evidence must stay readable.
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch { }
# The machine has HTTP_PROXY=127.0.0.1:7897 (Clash). Playwright is launched with
# --no-proxy-server, but keep the env clean for the local aiji HTTP call too.
$env:NO_PROXY = '127.0.0.1,localhost'
$env:no_proxy = $env:NO_PROXY
Set-Location -LiteralPath $projectRoot

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"[$stamp] douyin watch once (all conversations)" | Out-File -LiteralPath $logPath -Append -Encoding utf8
& $pythonExe -m aiji.senses.cli social douyin watch --all --once --yes 2>&1 |
    Out-File -LiteralPath $logPath -Append -Encoding utf8
exit $LASTEXITCODE
