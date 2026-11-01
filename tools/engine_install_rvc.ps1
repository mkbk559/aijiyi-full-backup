<#
.SYNOPSIS
  一条命令装好 RVC（Retrieval-based-Voice-Conversion-WebUI）。默认 dry-run，只打印要做什么。

.DESCRIPTION
  抄的是上游 README 的官方步骤，只改了两处「本机实测不可用」的地方：
    1. 依赖用 uv 建 venv（本机没有 conda，有 uv；Python 3.10/3.12 都在本地缓存在位）；
    2. torch 走 mirrors.nju.edu.cn 镜像（download.pytorch.org 本机实测 0.06 MB/s，2.5 GB 要 11 小时）。
  权重走 huggingface.co 直连（实测 ~1 MB/s）。

  上游出处：README「环境配置」+「下载模型」两段。

.PARAMETER Device
  CU128 / CU118 / CPU。默认 auto：有 nvidia-smi 且驱动 >= 12.8 走 CU128，否则 CU118，再否则 CPU。

.PARAMETER Apply
  真的执行。不加就是 dry-run。

.EXAMPLE
  pwsh -File tools\engine_install_rvc.ps1                 # 看要做什么
  pwsh -File tools\engine_install_rvc.ps1 -Apply          # 真装
#>
[CmdletBinding()]
param(
    [ValidateSet("auto", "CU128", "CU118", "CPU")][string]$Device = "auto",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root "voice-engine\rvc"
$Repo = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git"
$WeightsRepo = "lj1995/VoiceConversionWebUI"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Step($msg) { Write-Host "[STEP] $msg" -ForegroundColor Cyan }
function Plan($msg) { Write-Host "  · $msg" }

# ---------- 1. 设备判定 ----------
if ($Device -eq "auto") {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        $drv = (& nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>$null | Select-Object -First 1)
        $major = 0
        if ($drv -match '^(\d+)\.') { $major = [int]$Matches[1] }
        $Device = if ($major -ge 570) { "CU128" } else { "CU118" }
        Say "检测到 NVIDIA 驱动 $drv → 选 $Device" Green
    } else {
        $Device = "CPU"
        Say "没有 nvidia-smi → 选 CPU" Yellow
    }
}

$req = switch ($Device) {
    "CU128" { "requirments_cu128_py312.txt" }
    "CU118" { "requirments_cu118_py312.txt" }
    default { "requirments_cpu_py312.txt" }
}
$torchIndex = switch ($Device) {
    "CU128" { "https://mirrors.nju.edu.cn/pytorch/whl/cu128" }
    "CU118" { "https://mirrors.nju.edu.cn/pytorch/whl/cu118" }
    default { "https://mirrors.nju.edu.cn/pytorch/whl/cpu" }
}
$torchTag = switch ($Device) {
    "CU128" { "2.7.1+cu128" }
    "CU118" { "2.7.1+cu118" }
    default { "2.7.1+cpu" }
}

Say "`n=== 计划（$Device）===" White
Plan "clone $Repo → $Dest"
Plan "uv venv --python 3.12 $Dest\.venv"
Plan "uv pip install torch==$torchTag torchaudio=$torchTag --index-url $torchIndex"
Plan "uv pip install -r $req（上游文件，内置 pku/nju 镜像）"
Plan "hf download $WeightsRepo：hubert_base/*, rmvpe.pt, pretrained_v2/*, pymss_weights/*"
Plan "自检：python -c 'import torch; torch.cuda.is_available()'"
Plan "启动：$Dest\webui.py（:7865）"

if (-not $Apply) {
    Say "`n[dry-run] 没做任何改动。加 -Apply 才会执行。" Yellow
    return
}

# ---------- 2. 执行 ----------
$env:UV_CACHE_DIR = Join-Path $Root ".tmp\uvcache"

if (-not (Test-Path $Dest)) {
    Step "克隆上游（本机 schannel 会报 SEC_E_NO_CREDENTIALS，必须用 openssl 后端）"
    git -c http.sslBackend=openssl clone --depth 1 $Repo $Dest
} else {
    Say "已存在 $Dest，跳过克隆" Gray
}

Step "建 venv（Python 3.12）"
uv venv --python 3.12 (Join-Path $Dest ".venv")
$py = Join-Path $Dest ".venv\Scripts\python.exe"
$env:VIRTUAL_ENV = Join-Path $Dest ".venv"

Step "装 torch（$torchTag，走 nju 镜像；依赖包回落 pypi）"
uv pip install "torch==$torchTag" "torchaudio==$torchTag" `
    --index-url $torchIndex --extra-index-url https://pypi.org/simple `
    --index-strategy unsafe-best-match

Step "装上游依赖（$req）"
uv pip install -r (Join-Path $Dest $req)

Step "下权重到 $Dest\assets"
Push-Location $Dest
try {
    uv pip install --upgrade huggingface_hub
    & $py -m huggingface_hub.commands.huggingface_cli download $WeightsRepo --revision main `
        --include "hubert_base/*" --local-dir assets
    & $py -m huggingface_hub.commands.huggingface_cli download $WeightsRepo rmvpe.pt --revision main `
        --local-dir assets/rmvpe
    & $py -m huggingface_hub.commands.huggingface_cli download $WeightsRepo --revision main `
        --include "pretrained_v2/*" --local-dir assets
    & $py -m huggingface_hub.commands.huggingface_cli download $WeightsRepo --revision main `
        --include "pymss_weights/*" --local-dir assets
} finally {
    Pop-Location
}

Step "自检"
& $py -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
Say "`n装完。启动：& '$py' '$Dest\webui.py'   （默认 http://127.0.0.1:7865）" Green
