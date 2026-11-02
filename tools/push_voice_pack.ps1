<#
.SYNOPSIS
  把 data/voices/dafeiyu 这个"大肥鱼语音资产包"推到一个新的 GitHub 私有仓库（走 git-lfs）。

.DESCRIPTION
  本机**没有任何 GitHub 凭据**（无 token 环境变量、无 gh CLI、无 .git-credentials，
  ~/.ssh/id_ed25519 也未被 GitHub 接受），所以最后一步需要你的 PAT。
  拿到 PAT 后这个脚本一次做完：建私有仓库 → 配 remote → 推 LFS 对象 → 把 token 从 remote 里抹掉。

  全新细粒度 token 只需要 **Repository permissions → Contents: Read and write**
  （建仓库需要 Administration: Read and write，或者你先在网页上手动建好空仓库）。

.PARAMETER Token
  GitHub Personal Access Token。

.PARAMETER Repo
  仓库名，默认 aiji-voice-dafeiyu。

.EXAMPLE
  pwsh -File tools\push_voice_pack.ps1 -Token ghp_xxxx
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Repo = "aiji-voice-dafeiyu",
    [string]$PackDir = (Join-Path (Split-Path -Parent $PSScriptRoot) "data\voices\dafeiyu")
)

$ErrorActionPreference = "Stop"
Set-Location $PackDir
if (-not (Test-Path ".git")) { throw "$PackDir 不是 git 仓库（先跑 git init + commit）" }

# 1. 当前登录用户
$me = Invoke-RestMethod -Uri "https://api.github.com/user" -Headers @{
    Authorization          = "Bearer $Token"
    "User-Agent"           = "aiji-voice-pack"
}
Write-Host "账号: $($me.login)" -ForegroundColor Cyan

# 2. 建私有仓库（已存在就跳过）
try {
    Invoke-RestMethod -Method Post -Uri "https://api.github.com/user/repos" -Headers @{
        Authorization          = "Bearer $Token"
        "User-Agent"           = "aiji-voice-pack"
    } -Body (@{
        name        = $Repo
        private     = $true
        description = "Dafeiyu RVC voice pack: model + faiss index + training clips + source vocal"
    } | ConvertTo-Json) | Out-Null
    Write-Host "已创建私有仓库 $($me.login)/$Repo" -ForegroundColor Green
} catch {
    Write-Host "建仓库跳过（可能已存在或无权限）: $($_.Exception.Message)" -ForegroundColor Yellow
}

# 3. 配 remote（token 只放在这次 push 的命令行里，不写进 .git/config）
$url = "https://x-access-token:$Token@github.com/$($me.login)/$Repo.git"
git remote remove origin 2>$null
git remote add origin "https://github.com/$($me.login)/$Repo.git"

Write-Host "推送中（LFS 约 470 MB，视网速 5–20 分钟）..." -ForegroundColor Cyan
$env:GIT_LFS_SKIP_SMUDGE = "0"
git -c http.sslBackend=openssl -c http.extraHeader="Authorization: Bearer $Token" `
    push -u $url "HEAD:refs/heads/main" --force 2>&1 | Select-Object -Last 12

# 4. 确认
git -c http.sslBackend=openssl ls-remote origin 2>&1 | Select-Object -First 3
Write-Host "`n完成。仓库: https://github.com/$($me.login)/$Repo" -ForegroundColor Green
Write-Host "remote 已设为无 token 的干净地址；token 只出现在刚才那条 push 命令里。" -ForegroundColor Gray
