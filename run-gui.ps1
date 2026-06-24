# run-gui.ps1 — เปิด Tkinter GUI
$here = $PSScriptRoot
$vpy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "ยังไม่ได้ติดตั้ง — รัน .\setup.ps1 ก่อน" -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
& $vpy (Join-Path $here "birdnet_gui.py")
