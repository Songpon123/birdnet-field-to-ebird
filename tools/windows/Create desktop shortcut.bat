@echo off
rem Puts a "BirdNET eBird" shortcut (with the app icon) on your Desktop. Run once.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create_shortcut.ps1"
pause
