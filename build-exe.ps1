# build-exe.ps1 — build a portable .exe folder (runs on machines without Python)
# ------------------------------------------------------------------------------
# This bundles a REAL Python + deps next to a tiny launcher .exe.
# (We do NOT PyInstaller-freeze the whole app: PyInstaller imports the native
#  libs — TensorFlow/scipy/numba — during analysis and crashes on this machine.)
#
# Needs: Python 3.12 (py -3.12) + ffmpeg on PATH  (see README)
# Run:   powershell -ExecutionPolicy Bypass -File build-exe.ps1
# Output: dist\BirdNET-eBird\  (zip the whole folder to share; run BirdNET-eBird.exe)
$ErrorActionPreference = "Stop"
$here   = $PSScriptRoot
$bundle = Join-Path $here "dist\BirdNET-eBird"
Write-Host "=== build BirdNET-eBird (portable .exe bundle) ===" -ForegroundColor Cyan

# 1) find a copyable Python 3.12 base
function Find-Py312Base {
    foreach ($c in @("py -3.12", "python3.12", "python")) {
        try {
            $v = & cmd /c "$c --version 2>&1"
            if ($v -match "3\.12\.") {
                return (& cmd /c "$c -c ""import sys;print(sys.base_prefix)""").Trim()
            }
        } catch {}
    }
    return $null
}
$pyBase = Find-Py312Base
if (-not $pyBase -or -not (Test-Path (Join-Path $pyBase "python.exe"))) {
    Write-Host "Python 3.12 (copyable base) not found — install: winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}
Write-Host "Python 3.12 base: $pyBase" -ForegroundColor Green

# 2) prepare output folder + copy Python into it
if (Test-Path $bundle) { Remove-Item -Recurse -Force $bundle }
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
$pyDir = Join-Path $bundle "python"
Write-Host "copy Python -> $pyDir ..." -ForegroundColor Cyan
Copy-Item -Recurse -Force $pyBase $pyDir
$vpy = Join-Path $pyDir "python.exe"

# 2b) bundle the VC++ 2015-2022 x64 runtime so clean machines need no redistributable
#     (numpy / LiteRT etc. link msvcp140.dll / vcomp140.dll)
foreach ($d in @("msvcp140.dll","msvcp140_1.dll","msvcp140_2.dll","vcomp140.dll","concrt140.dll")) {
    $sysdll = Join-Path $env:WINDIR "System32\$d"
    if ((Test-Path $sysdll) -and -not (Test-Path (Join-Path $pyDir $d))) {
        Copy-Item -Force $sysdll $pyDir
    }
}

# 3) install deps (no TensorFlow — uses ai-edge-litert)
& $vpy -m ensurepip --default-pip 2>&1 | Out-Null
& $vpy -m pip install --upgrade pip | Out-Null
Write-Host "installing deps into the bundle python ... (first run is slow)" -ForegroundColor Cyan
& $vpy -m pip install --no-warn-script-location -r (Join-Path $here "requirements-exe.txt")

# 4) freeze the tiny launcher (stdlib-only -> always freezes) with the bundle python
Write-Host "freezing launcher ..." -ForegroundColor Cyan
& $vpy -m pip install --quiet pyinstaller
$distL = Join-Path $here "dist_launcher"
$workL = Join-Path $here "build_launcher"
& $vpy -m PyInstaller --noconfirm --onefile --console --name BirdNET-eBird `
    --distpath $distL --workpath $workL (Join-Path $here "launcher.py")
& $vpy -m pip uninstall -y pyinstaller 2>&1 | Out-Null   # remove from bundle to keep it small
Copy-Item -Force (Join-Path $distL "BirdNET-eBird.exe") (Join-Path $bundle "BirdNET-eBird.exe")

# 5) copy the program code + shim
$app = Join-Path $bundle "app"
New-Item -ItemType Directory -Force -Path (Join-Path $app "tflite_runtime") | Out-Null
Copy-Item -Force (Join-Path $here "birdnet_app.py")           $app
Copy-Item -Force (Join-Path $here "field_audio_to_ebird.py")  $app
Copy-Item -Force (Join-Path $here "birdnet_gui.py")           $app
Copy-Item -Force (Join-Path $here "tflite_runtime\*.py")      (Join-Path $app "tflite_runtime")

# 6) copy ffmpeg + ffprobe (resolve the winget shim to the real file)
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff) {
    Write-Host "ffmpeg not found on PATH — install: winget install Gyan.FFmpeg, then re-run" -ForegroundColor Red
    exit 1
}
$ffReal   = (Resolve-Path $ff.Source).Path
$ffBin    = Split-Path $ffReal -Parent
Copy-Item -Force (Join-Path $ffBin "ffmpeg.exe")  $bundle
Copy-Item -Force (Join-Path $ffBin "ffprobe.exe") $bundle

# 6b) READ-ME-FIRST (Thai/English) for the people you share it with
$readme = Join-Path $here "อ่านก่อนใช้.txt"
if (Test-Path $readme) { Copy-Item -Force $readme $bundle }

# 7) clean up temporary build dirs
Remove-Item -Recurse -Force $distL, $workL -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $here "BirdNET-eBird.spec") -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host "Folder: dist\BirdNET-eBird  (run BirdNET-eBird.exe)" -ForegroundColor Cyan
Write-Host "To share: zip the whole dist\BirdNET-eBird folder (don't separate the .exe)" -ForegroundColor Cyan
