from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.sifa.extractor import (
    LiquidacionSifa,
    extraer_liquidacion_sifa,
)
from services.sifa.matcher import (
    CLIENTE_SIFA_DESPACHOS,
    ResultadoMatcherSifa,
    buscar_lineas_despachos_sifa,
)
from services.sifa.validator import (
    ResultadoValidacionSifa,
    validar_liquidacion_sifa,
)


EstadoProcesamientoSifa = Literal["invalido", "listo"]


@dataclass(frozen=True)
class ResultadoPreparacionSifa:
    estado: EstadoProcesamientoSifa
    puede_escribir: bool
    liquidacion: LiquidacionSifa
    despachos: ResultadoMatcherSifa
    validacion: ResultadoValidacionSifa


class ErrorProcesamientoSifa(Exception):
    """Error general del procesamiento SIFA."""


def preparar_procesamiento_sifa(
    ruta_liquidacion: str | Path,
    ruta_despachos: str | Path,
    *,
    semana: int,
    anio: int,
    destino: str,
    factura_corta: str | None = None,
    cliente: str = CLIENTE_SIFA_DESPACHOS,
) -> ResultadoPreparacionSifa:
    liquidacion = extraer_liquidacion_sifa(ruta_liquidacion)

    facturas: set[str] = set()
    if factura_corta and str(factura_corta).strip():
        facturas.add(str(factura_corta).strip())
    elif liquidacion.factura_corta:
        facturas.add(liquidacion.factura_corta)

    despachos = buscar_lineas_despachos_sifa(
        ruta_archivo=ruta_despachos,
        semana=semana,
        anio=anio,
        destino=destino,
        cliente=cliente,
        facturas_cortas=facturas or None,
    )

    validacion = validar_liquidacion_sifa(
        liquidacion=liquidacion,
        despachos=despachos,
        destino_ui=destino,
    )

    if not validacion.es_valido:
        return ResultadoPreparacionSifa(
            estado="invalido",
            puede_escribir=False,
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    return ResultadoPreparacionSifa(
        estado="listo",
        puede_escribir=True,
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )


def convertir_a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if is_dataclass(valor) and not isinstance(valor, type):
        return convertir_a_json(asdict(valor))
    if isinstance(valor, dict):
        return {k: convertir_a_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [convertir_a_json(v) for v in valor]
    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepara liquidación SIFA.",
    )
    parser.add_argument("--liquidacion", required=True)
    parser.add_argument("--despachos", required=True)
    parser.add_argument("--semana", type=int, required=True)
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--destino", required=True)
    parser.add_argument("--factura", default="")
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                preparar_procesamiento_sifa(
                    ruta_liquidacion=args.liquidacion,
                    ruta_despachos=args.despachos,
                    semana=args.semana,
                    anio=args.anio,
                    destino=args.destino,
                    factura_corta=args.factura or None,
                )
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
