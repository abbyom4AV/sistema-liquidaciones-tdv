@echo off
REM Respaldo diario liviano (solo db.sqlite3).
REM Para incluir media (recomendado 1 vez por semana):
REM   powershell -ExecutionPolicy Bypass -File "%~dp0backup_sistema_tdv.ps1" -IncluirMedia
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0backup_sistema_tdv.ps1"
