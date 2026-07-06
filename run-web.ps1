# run-web.ps1 — open Streamlit (browser)
$here = $PSScriptRoot
$vpy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "Not installed yet — run .\setup.ps1 first" -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
& $vpy -m streamlit run (Join-Path $here "birdnet_ui.py") --server.maxUploadSize 2048 --browser.gatherUsageStats false
