@echo off
REM Lanza el arranque del Sistema TDV (usado por Task Scheduler)
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_sistema_tdv.ps1"
