# Arranca el sistema TDV (runserver + workers) para demo/prueba en red local.
# Uso: powershell -ExecutionPolicy Bypass -File .\scripts\start_sistema_tdv.ps1

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "No se encontro el venv: $Python"
}

$EnvFile = Join-Path $Root ".env"
if (Test-Path -LiteralPath $EnvFile) {
    Get-Content -LiteralPath $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $parts = $line -split "=", 2
        if ($parts.Count -lt 2) { return }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        Set-Item -Path "Env:$name" -Value $value
    }
}

$Runtime = Join-Path $Root "runtime"
$Logs = Join-Path $Runtime "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

Get-ChildItem -LiteralPath $Runtime -Filter "*_worker.lock" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

function Start-TdvProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    Start-Process -FilePath $Python `
        -WorkingDirectory $Root `
        -ArgumentList $ArgumentList `
        -WindowStyle Minimized | Out-Null
    Write-Host "Iniciado: $Name"
}

# Evitar duplicados si ya esta corriendo
$ya = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*manage.py*runserver*0.0.0.0:8000*" }
if ($ya) {
    Write-Host "runserver ya estaba en ejecucion (PID $($ya.ProcessId -join ', '))"
} else {
    Start-TdvProcess -Name "runserver" -ArgumentList @(
        "manage.py", "runserver", "0.0.0.0:8000"
    )
}

$Workers = @(
    "procesar_generaciones_dimanno",
    "procesar_generaciones_master",
    "procesar_generaciones_orsero",
    "procesar_generaciones_kraaijeveld",
    "procesar_generaciones_sifa",
    "procesar_generaciones_glamour",
    "procesar_generaciones_nufri",
    "procesar_generaciones_eurobanan",
    "procesar_generaciones_tdv_europa",
    "procesar_generaciones_fruver"
)

foreach ($cmd in $Workers) {
    $existe = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*manage.py*$cmd*" }
    if ($existe) {
        Write-Host "Ya corria: $cmd"
        continue
    }
    Start-TdvProcess -Name $cmd -ArgumentList @("manage.py", $cmd)
}

Write-Host ""
Write-Host "Sistema TDV en marcha."
Write-Host "Login local:     http://127.0.0.1:8000/"
$nombrePc = $env:COMPUTERNAME
if ($nombrePc) {
    Write-Host "Login fijo (PC): http://${nombrePc}:8000/"
}
$ipLan = @(
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.*" } |
        Select-Object -ExpandProperty IPAddress -First 1
)
if (-not $ipLan) {
    $ipLan = (
        ipconfig |
            Select-String "IPv4" |
            ForEach-Object { ($_ -split ":")[-1].Trim() } |
            Where-Object { $_ -like "192.168.*" } |
            Select-Object -First 1
    )
}
if ($ipLan) {
    Write-Host "Login por IP:    http://${ipLan}:8000/"
}
Write-Host "Para detener: scripts\stop_sistema_tdv.ps1"
