# RVC 闲置自退：10 分钟没有 /convert 调用就关掉，别让 torch 白占内存。
$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:18601'
$statePath = 'D:\AIJiYi\data\temp\rvc_idle.json'
$idleSeconds = 600
try {
  $op = [System.Net.WebRequest]::Create("$base/health"); $op.Proxy = $null; $op.Timeout = 5000
  $resp = $op.GetResponse(); $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
  $health = $reader.ReadToEnd() | ConvertFrom-Json; $reader.Close(); $resp.Close()
} catch { exit 0 }   # 服务没起，什么都不用做
$calls = [int]$health.calls
$now = [int][double]::Parse((Get-Date -UFormat %s))
$prev = if (Test-Path $statePath) { Get-Content $statePath -Raw | ConvertFrom-Json } else { $null }
if ($null -eq $prev) {
  @{ calls = $calls; ts = $now } | ConvertTo-Json | Set-Content $statePath -Encoding utf8
  exit 0
}
if ($calls -ne [int]$prev.calls) { @{ calls = $calls; ts = $now } | ConvertTo-Json | Set-Content $statePath -Encoding utf8; exit 0 }
if (($now - [int]$prev.ts) -lt $idleSeconds) { exit 0 }
try {
  $req = [System.Net.WebRequest]::Create("$base/shutdown"); $req.Proxy = $null; $req.Method = 'POST'
  $req.ContentType = 'application/json'; $body = [Text.Encoding]::UTF8.GetBytes('{}')
  $req.ContentLength = $body.Length; $s = $req.GetRequestStream(); $s.Write($body,0,$body.Length); $s.Close()
  $req.GetResponse().Close()
  "idle $($now - [int]$prev.ts)s with calls=$calls → shutdown"
} catch { "shutdown failed: $_" }
