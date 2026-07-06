# run-gui.ps1 — open the Tkinter GUI
$here = $PSScriptRoot
$vpy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "Not installed yet — run .\setup.ps1 first" -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
& $vpy (Join-Path $here "birdnet_gui.py")
