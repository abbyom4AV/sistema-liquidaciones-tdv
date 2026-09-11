# Respaldo liviano del Sistema TDV.
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\backup_sistema_tdv.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\backup_sistema_tdv.ps1 -IncluirMedia
#
# Por defecto solo copia db.sqlite3 (chico, critico).
# media\ es opcional porque pesa mucho; se recomienda semanal.

param(
    [switch]$IncluirMedia,
    [int]$ConservarDias = 14
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$DestinoRaiz = Join-Path $Root "backups"
$Hoy = Get-Date -Format "yyyy-MM-dd"
$DestinoDia = Join-Path $DestinoRaiz $Hoy
New-Item -ItemType Directory -Force -Path $DestinoDia | Out-Null

$db = Join-Path $Root "db.sqlite3"
if (-not (Test-Path -LiteralPath $db)) {
    throw "No se encontro db.sqlite3 en $Root"
}

# Copia consistente: SQLite puede estar en uso por Django/workers.
# Copy-Item suele funcionar en Windows; si falla, reintentamos.
$destinoDb = Join-Path $DestinoDia "db.sqlite3"
$ok = $false
for ($i = 1; $i -le 3; $i++) {
    try {
        Copy-Item -LiteralPath $db -Destination $destinoDb -Force
        $ok = $true
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $ok) {
    throw "No se pudo copiar db.sqlite3 (archivo en uso)."
}

$tamanoDb = [math]::Round((Get-Item -LiteralPath $destinoDb).Length / 1KB, 1)
Write-Host "OK base de datos -> backups\$Hoy\db.sqlite3 ($tamanoDb KB)"

if ($IncluirMedia) {
    $media = Join-Path $Root "media"
    if (Test-Path -LiteralPath $media) {
        $destinoMedia = Join-Path $DestinoDia "media"
        # /MIR copia solo cambios; /R:1 /W:1 evita cuelgues largos
        robocopy $media $destinoMedia /E /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
        # robocopy: codigos < 8 son exito
        if ($LASTEXITCODE -ge 8) {
            throw "robocopy fallo al copiar media (codigo $LASTEXITCODE)."
        }
        Write-Host "OK media -> backups\$Hoy\media\"
    } else {
        Write-Host "AVISO: no existe carpeta media\; se omite."
    }
} else {
    Write-Host "media\ omitida (usar -IncluirMedia una vez por semana)."
}

Write-Host "Los Excel acumulativos no se respaldan aqui: viven en OneDrive/carpeta compartida."

# Rotacion: borrar carpetas de backup mas viejas que ConservarDias
Get-ChildItem -LiteralPath $DestinoRaiz -Directory -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match '^\d{4}-\d{2}-\d{2}$' -and
        $_.LastWriteTime -lt (Get-Date).AddDays(-$ConservarDias)
    } |
    ForEach-Object {
        Write-Host "Borrando backup viejo: $($_.Name)"
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }

Write-Host "Backup terminado."
