# Capture a screen region to a PNG. ASCII-only (see show_qq_login.ps1 for why).
# Usage: powershell -File snapshot_window.ps1 -X 96 -Y 96 -W 1440 -H 740 -Out D:\path\shot.png
param(
    [int]$X = 0,
    [int]$Y = 0,
    [int]$W = 1280,
    [int]$H = 800,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing | Out-Null
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$bmp = New-Object System.Drawing.Bitmap($W, $H)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($X, $Y, 0, 0, (New-Object System.Drawing.Size($W, $H)))
$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
"saved $Out ($W x $H from $X,$Y)"
