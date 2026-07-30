from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.master.extractor import (
    LiquidacionMaster,
    extraer_liquidacion_master,
)
from services.master.matcher import (
    CLIENTE_MASTER,
    ResultadoMatcherMaster,
    buscar_lineas_despachos_master,
)
from services.master.validator import (
    ResultadoValidacionMaster,
    validar_liquidacion_master,
)


EstadoProcesamientoMaster = Literal["invalido", "listo"]


@dataclass(frozen=True)
class ResultadoPreparacionMaster:
    estado: EstadoProcesamientoMaster
    puede_escribir: bool
    destino_final: str | None
    origen_destino: str | None
    liquidacion: LiquidacionMaster
    despachos: ResultadoMatcherMaster
    validacion: ResultadoValidacionMaster


class ErrorProcesamientoMaster(Exception):
    """Error general del procesamiento Master Fruits."""


def preparar_procesamiento_master(
    ruta_liquidacion: str | Path,
    ruta_despachos: str | Path,
    cliente: str = CLIENTE_MASTER,
) -> ResultadoPreparacionMaster:
    liquidacion = extraer_liquidacion_master(ruta_liquidacion)

    despachos = buscar_lineas_despachos_master(
        ruta_archivo=ruta_despachos,
        factura_corta=liquidacion.factura_corta,
        cliente=cliente,
    )

    validacion = validar_liquidacion_master(
        liquidacion=liquidacion,
        despachos=despachos,
    )

    if not validacion.es_valido:
        return ResultadoPreparacionMaster(
            estado="invalido",
            puede_escribir=False,
            destino_final=None,
            origen_destino=None,
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    return ResultadoPreparacionMaster(
        estado="listo",
        puede_escribir=True,
        destino_final=validacion.destino_final,
        origen_destino="despachos",
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
            clave: convertir_a_json(contenido)
            for clave, contenido in valor.items()
        }

    if isinstance(valor, (list, tuple)):
        return [
            convertir_a_json(elemento)
            for elemento in valor
        ]

    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepara una liquidación Master Fruits."
        ),
    )
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--despachos", required=True)
    parser.add_argument(
        "--cliente",
        default=CLIENTE_MASTER,
    )
    argumentos = parser.parse_args()
    resultado = preparar_procesamiento_master(
        ruta_liquidacion=argumentos.pdf,
        ruta_despachos=argumentos.despachos,
        cliente=argumentos.cliente,
    )
    print(json.dumps(convertir_a_json(resultado), indent=2))


if __name__ == "__main__":
    main()
