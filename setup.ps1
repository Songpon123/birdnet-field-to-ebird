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
    Write-Host "ไม่พบ Python 3.12 — TensorFlow ต้องใช้ 3.12 เท่านั้น" -ForegroundColor Red
    Write-Host "ลงด้วย:  winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
Write-Host "Python 3.12: $py" -ForegroundColor Green

# 2) สร้าง venv
$venv = Join-Path $here ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "สร้าง virtual env ที่ .venv ..."
    & cmd /c "$py -m venv `"$venv`""
}
$vpy = Join-Path $venv "Scripts\python.exe"

# 3) ติดตั้ง dependencies (TensorFlow ใหญ่ ~1-2 GB ใช้เวลาสักครู่)
Write-Host "ติดตั้ง dependencies ... (ครั้งแรกนาน)" -ForegroundColor Cyan
& $vpy -m pip install --upgrade pip
& $vpy -m pip install -r (Join-Path $here "requirements.txt")

# 4) เช็ค ffmpeg
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "ffmpeg: พบใน PATH" -ForegroundColor Green
} else {
    Write-Host "เตือน: ไม่พบ ffmpeg ใน PATH" -ForegroundColor Yellow
    Write-Host "  ลงด้วย:  winget install Gyan.FFmpeg" -ForegroundColor Yellow
    Write-Host "  (สคริปต์พยายามหา ffmpeg ใน WinGet\Links ให้เองด้วย)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== เสร็จ! ===" -ForegroundColor Green
Write-Host "เปิดแอป:" -ForegroundColor Cyan
Write-Host "  .\run-gui.ps1   (หน้าต่างโปรแกรม Tkinter — เลือกไฟล์ผ่าน dialog)"
Write-Host "  .\run-web.ps1   (เปิดในเบราว์เซอร์ Streamlit)"
