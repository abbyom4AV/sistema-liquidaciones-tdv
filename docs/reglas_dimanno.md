# Reglas de procesamiento de liquidaciones Di Manno

## Alcance

Este documento define las reglas para procesar las liquidaciones enviadas por Di Manno y agregar la información correspondiente en la hoja `Raw Data` del archivo `DIMANNO Liquidaciones`.

La primera versión procesa únicamente archivos Excel.

## Archivos de entrada

El procesamiento utiliza tres archivos:

1. Archivo de liquidación enviado por Di Manno.
2. Archivo de Despachos TDV.
3. Archivo acumulativo `DIMANNO Liquidaciones`.

## Hojas utilizadas

### Liquidación Di Manno

El archivo contiene:

- Una hoja `RESUME`.
- Una hoja de detalle por liquidación.

El nombre de la hoja de detalle sigue este formato:

```text
FT {últimos cuatro dígitos de factura} W{semana}