# Detiene runserver y workers del Sistema TDV iniciados por start_sistema_tdv.ps1

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot

Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -like "*$Root*" -and
    (
        $_.CommandLine -like "*manage.py*runserver*" -or
        $_.CommandLine -like "*procesar_generaciones_*"
    )
} | ForEach-Object {
    Write-Host "Deteniendo PID $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force
}

Get-ChildItem -LiteralPath (Join-Path $Root "runtime") -Filter "*_worker.lock" |
    Remove-Item -Force

Write-Host "Sistema TDV detenido."
