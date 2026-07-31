from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.orsero.extractor import (
    LiquidacionOrsero,
    extraer_liquidacion_orsero,
    parsear_texto_liquidacion_orsero,
)
from services.orsero.matcher import (
    CLIENTE_ORSERO,
    ResultadoMatcherOrsero,
    buscar_lineas_despachos_orsero,
)
from services.orsero.validator import (
    ResultadoValidacionOrsero,
    validar_liquidacion_orsero,
)


EstadoProcesamientoOrsero = Literal["invalido", "listo"]


@dataclass(frozen=True)
class ResultadoPreparacionOrsero:
    estado: EstadoProcesamientoOrsero
    puede_escribir: bool
    liquidacion: LiquidacionOrsero
    despachos: ResultadoMatcherOrsero
    validacion: ResultadoValidacionOrsero


class ErrorProcesamientoOrsero(Exception):
    """Error general del procesamiento Orsero."""


def preparar_procesamiento_orsero(
    ruta_liquidacion: str | Path,
    ruta_despachos: str | Path,
    anio: int,
    cliente: str = CLIENTE_ORSERO,
    texto_ocr: str | None = None,
) -> ResultadoPreparacionOrsero:
    if texto_ocr is not None:
        liquidacion = parsear_texto_liquidacion_orsero(
            texto_ocr,
            archivo=str(ruta_liquidacion),
        )
    else:
        liquidacion = extraer_liquidacion_orsero(
            ruta_liquidacion
        )

    despachos = buscar_lineas_despachos_orsero(
        ruta_archivo=ruta_despachos,
        semana=liquidacion.semana,
        anio=int(anio),
        cliente=cliente,
    )

    validacion = validar_liquidacion_orsero(
        liquidacion=liquidacion,
        despachos=despachos,
    )

    if not validacion.es_valido:
        return ResultadoPreparacionOrsero(
            estado="invalido",
            puede_escribir=False,
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    return ResultadoPreparacionOrsero(
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
        return {
            k: convertir_a_json(v) for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return [convertir_a_json(v) for v in valor]
    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepara una liquidación Orsero.",
    )
    parser.add_argument("--imagen", required=True)
    parser.add_argument("--despachos", required=True)
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--cliente", default=CLIENTE_ORSERO)
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                preparar_procesamiento_orsero(
                    ruta_liquidacion=args.imagen,
                    ruta_despachos=args.despachos,
                    anio=args.anio,
                    cliente=args.cliente,
                )
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
