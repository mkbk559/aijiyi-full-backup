# Run by Task Scheduler every five minutes while the user is signed in.
$mutex = New-Object System.Threading.Mutex($false, 'Local\AIJiYiServiceWatchdog')
if (-not $mutex.WaitOne(0)) { exit 0 }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExe = 'D:\Python313\python.exe'
$dataDir = Join-Path $projectRoot 'data'
$astrbotRoot = 'D:\AstrBot'
$astrbotPython = Join-Path $astrbotRoot '.venv\Scripts\python.exe'
$napcatRoot = 'D:\NapCat\shell'
$napcatExe = Join-Path $napcatRoot 'NapCatWinBootMain.exe'

$napcat = Get-CimInstance Win32_Process -Filter "Name='NapCatWinBootMain.exe'" -ErrorAction SilentlyContinue
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

$astrbot = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $astrbotPython }
if (-not $astrbot -and (Test-Path -LiteralPath $astrbotPython)) {
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

Start-Process -FilePath $pythonExe `
    -ArgumentList '-m', 'aiji.cli', 'serve' `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $dataDir 'console.out.log') `
    -RedirectStandardError (Join-Path $dataDir 'console.err.log')
exit 0
