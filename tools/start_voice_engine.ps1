# 按需拉起语音引擎（RVC 变声，18601）。**不做开机自启**：torch 一挂就是几百 MB，
# 只在真要发语音时拉；闲置由 tools\rvc_idle_stop.ps1 每 10 分钟检查后自退。
$ErrorActionPreference = 'Continue'
$listening = Get-NetTCPConnection -LocalPort 18601 -State Listen -ErrorAction SilentlyContinue
if ($listening) { "already up pid=$($listening[0].OwningProcess)"; exit 0 }
& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'rvc_daemon.ps1')
exit $LASTEXITCODE
