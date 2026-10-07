@echo off
rem Opens BirdNET eBird without a console window.
rem If nothing appears, see data\logs\BirdNET-eBird.log
cd /d "%~dp0"
start "" "%~dp0python\pythonw.exe" "%~dp0app\birdnet_app.py"
