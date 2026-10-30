param([switch]$LocalImages)   # .\setup.ps1 -LocalImages  → tải lại FLUX + sd.cpp (mặc định: dùng Google Flow)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$vendor = Join-Path $root "vendor"

function Get-File($url, $dest) {
    if (Test-Path $dest) { Write-Host "co san: $dest"; return }
    Write-Host "tai: $url"
    & curl.exe -L --fail --retry 5 -C - -o "$dest.part" $url
    if ($LASTEXITCODE -ne 0) { throw "tai loi: $url" }
    Move-Item "$dest.part" $dest
}

if ($LocalImages) {
    $models = Join-Path $vendor "models"
    New-Item -ItemType Directory -Force $vendor, $models | Out-Null
    # stable-diffusion.cpp (CUDA 12)
    $sdDir = Join-Path $vendor "sdcpp"
    if (-not (Test-Path (Join-Path $sdDir "sd-cli.exe")) -and -not (Test-Path (Join-Path $sdDir "sd.exe"))) {
        $rel = Invoke-RestMethod "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest" -Headers @{ 'User-Agent' = 'autovideo' }
        New-Item -ItemType Directory -Force $sdDir | Out-Null
        foreach ($pat in 'bin-win-cuda12-x64\.zip$', '^cudart-sd-bin-win-cu12-x64\.zip$') {
            $a = $rel.assets | Where-Object name -match $pat | Select-Object -First 1
            $zip = Join-Path $vendor $a.name
            Get-File $a.browser_download_url $zip
            Expand-Archive $zip -DestinationPath $sdDir -Force
            Remove-Item $zip
        }
    }
    # FLUX.1-schnell GGUF
    $hf = "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main"
    foreach ($f in 'flux1-schnell-Q4_0.gguf', 't5xxl-Q4_K.gguf', 'clip_l-Q8_0.gguf', 'ae.safetensors') {
        Get-File "$hf/$f" (Join-Path $models $f)
    }
}

# Python env cho app + OmniVoice
$venv = Join-Path $root ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    uv venv --python 3.11 $venv
}
$py = Join-Path $venv "Scripts\python.exe"
uv pip install --python $py torch==2.8.0+cu128 torchaudio==2.8.0+cu128 --extra-index-url https://download.pytorch.org/whl/cu128
uv pip install --python $py omnivoice PySide6 requests pyyaml pillow opencv-python numpy soundfile ddgs trafilatura send2trash psutil vieneu zerotts
& $py -c "import torch, omnivoice; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

# Kiểu dựng Nikola (bảng hai đảo): repo + Python riêng cho engine nét của repo
$nk = Join-Path (Split-Path $root -Parent) "nikola"
if (-not (Test-Path (Join-Path $nk "SKILL.md"))) {
    git clone --depth 1 https://github.com/hi-nikola/hand-drawn-explainer-video-nikola $nk
}
$nkpy = Join-Path $nk ".venv\Scripts\python.exe"
if (-not (Test-Path $nkpy)) { uv venv --python 3.11 (Join-Path $nk ".venv") }
uv pip install --python $nkpy -r (Join-Path $nk "requirements-media.txt")
$env:PYTHONUTF8 = "1"
& $nkpy (Join-Path $nk "scripts\stroke_story_preflight.py") --backend-python $nkpy --report (Join-Path $root "data\nikola_preflight.json")
Write-Host "SETUP XONG"
