# setup.ps1 — ติดตั้ง BirdNET field-to-eBird (Windows / PowerShell)
# รัน:  คลิกขวา > Run with PowerShell  หรือ  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
Write-Host "=== BirdNET field-to-eBird : setup ===" -ForegroundColor Cyan

# 1) หา Python 3.12 (TensorFlow ไม่รองรับ 3.13+)
function Find-Py312 {
    foreach ($c in @("py -3.12", "python3.12", "python")) {
        try {
            $v = & cmd /c "$c --version 2>&1"
            if ($v -match "3\.12\.") { return $c }
        } catch {}
    }
    return $null
}
$py = Find-Py312
if (-not $py) {
    Write-Host "Python 3.12 not found — TensorFlow requires 3.12" -ForegroundColor Red
    Write-Host "Install with:  winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
Write-Host "Python 3.12: $py" -ForegroundColor Green

# 2) สร้าง venv
$venv = Join-Path $here ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Creating virtual env at .venv ..."
    & cmd /c "$py -m venv `"$venv`""
}
$vpy = Join-Path $venv "Scripts\python.exe"

# 3) ติดตั้ง dependencies (TensorFlow ใหญ่ ~1-2 GB ใช้เวลาสักครู่)
Write-Host "Installing dependencies ... (first run is slow)" -ForegroundColor Cyan
& $vpy -m pip install --upgrade pip
& $vpy -m pip install -r (Join-Path $here "requirements.txt")

# 4) เช็ค ffmpeg
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "ffmpeg: found in PATH" -ForegroundColor Green
} else {
    Write-Host "Warning: ffmpeg not found in PATH" -ForegroundColor Yellow
    Write-Host "  Install with:  winget install Gyan.FFmpeg" -ForegroundColor Yellow
    Write-Host "  (the script also tries to auto-locate ffmpeg in WinGet\Links)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Green
Write-Host "Open the app:" -ForegroundColor Cyan
Write-Host "  .\run-gui.ps1   (Tkinter window — pick files via dialog)"
Write-Host "  .\run-web.ps1   (open in browser via Streamlit)"
