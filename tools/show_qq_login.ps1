# Make QQ's hidden login window visible so the human can scan the QR code.
#
# Why this exists (measured 2026-11-05): QQ is launched by tools\ensure_console.ps1 with
# -WindowStyle Hidden, so its login window (class=Chrome_WidgetWin_0) stays visible=false.
# Inside the tool sandbox ShowWindow returned False and IsWindowVisible stayed False, and
# gui_act refuses to focus an invisible window. A scheduled task runs outside that sandbox,
# which is why this script is meant to be launched by schtasks.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 parses a BOM-less .ps1 as ANSI/GBK,
# and non-ASCII comments then break string terminators (hit on 2026-11-05).
$ErrorActionPreference = 'Stop'
Add-Type -Namespace AijiW -Name U -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
[DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int n);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
[DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder sb, int max);
public delegate bool EnumProc(IntPtr h, IntPtr l);
'@

function Get-QqWindows {
    $pids = @(Get-Process -Name QQ -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id })
    if ($pids.Count -eq 0) { return @() }
    $found = New-Object System.Collections.ArrayList
    $cb = [AijiW.U+EnumProc]{
        param([IntPtr]$h, [IntPtr]$l)
        $owner = [uint32]0
        [void][AijiW.U]::GetWindowThreadProcessId($h, [ref]$owner)
        if ($pids -contains $owner) {
            $sb = New-Object System.Text.StringBuilder 256
            [void][AijiW.U]::GetClassName($h, $sb, 256)
            [void]$found.Add([pscustomobject]@{
                hwnd = $h; pid = $owner; cls = $sb.ToString()
                visible = [AijiW.U]::IsWindowVisible($h)
            })
        }
        return $true
    }
    [void][AijiW.U]::EnumWindows($cb, [IntPtr]::Zero)
    return $found
}

$deadline = (Get-Date).AddSeconds(90)
$done = $false
while ((Get-Date) -lt $deadline) {
    $wins = Get-QqWindows
    foreach ($w in $wins) {
        [void][AijiW.U]::ShowWindow($w.hwnd, 9)     # SW_RESTORE
        [void][AijiW.U]::ShowWindow($w.hwnd, 5)     # SW_SHOW
        [void][AijiW.U]::ShowWindowAsync($w.hwnd, 9)
        [void][AijiW.U]::SetForegroundWindow($w.hwnd)
    }
    Start-Sleep -Milliseconds 700
    $vis = @(Get-QqWindows | Where-Object { [AijiW.U]::IsWindowVisible($_.hwnd) })
    if ($vis.Count -gt 0) {
        foreach ($v in $vis) {
            "visible=True hwnd=$($v.hwnd) pid=$($v.pid) class=$($v.cls) at $(Get-Date -Format 'HH:mm:ss')"
        }
        $done = $true
        break
    }
}
if (-not $done) {
    $wins = Get-QqWindows
    "visible=False (could not show QQ window in 90s); candidates=$($wins.Count)"
    foreach ($w in $wins) { "  hwnd=$($w.hwnd) pid=$($w.pid) class=$($w.cls)" }
}
