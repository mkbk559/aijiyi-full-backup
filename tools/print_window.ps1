# Render a single window to PNG via PrintWindow (works for windows that are covered,
# and unlike CopyFromScreen it does not depend on the desktop being painted).
# ASCII-only. Usage: powershell -File print_window.ps1 -Hwnd 197750 -Out D:\path\shot.png
param(
    [Parameter(Mandatory = $true)][int]$Hwnd,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing | Out-Null
Add-Type -Namespace AijiP -Name W -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
public struct RECT { public int Left, Top, Right, Bottom; }
'@
$r = New-Object 'AijiP.W+RECT'
[void][AijiP.W]::GetWindowRect([IntPtr]$Hwnd, [ref]$r)
$w = $r.Right - $r.Left
$h = $r.Bottom - $r.Top
if ($w -le 0 -or $h -le 0) { throw "window $Hwnd has empty rect ($w x $h)" }
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [AijiP.W]::PrintWindow([IntPtr]$Hwnd, $hdc, 2)   # PW_RENDERFULLCONTENT
$g.ReleaseHdc($hdc)
$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
"printwindow=$ok saved $Out ($w x $h)"
