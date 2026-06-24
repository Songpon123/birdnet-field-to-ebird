# run-web.ps1 — เปิด Streamlit (เบราว์เซอร์)
$here = $PSScriptRoot
$vpy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "ยังไม่ได้ติดตั้ง — รัน .\setup.ps1 ก่อน" -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
& $vpy -m streamlit run (Join-Path $here "birdnet_ui.py") --server.maxUploadSize 2048 --browser.gatherUsageStats false
