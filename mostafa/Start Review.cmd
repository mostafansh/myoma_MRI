@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\Start-MyomaReviewLite.ps1" -ReaderDir "%~dp0."
if errorlevel 1 pause
