@echo off
rem Double-click to start Tauon and let it follow your phone.
rem Options are passed on, e.g.:  tauon-mirror.cmd -Device moto -InstallStartup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tauon-mirror.ps1" %*
if errorlevel 1 pause
