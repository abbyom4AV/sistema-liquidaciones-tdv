from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.tdv_europa.extractor import (
    LiquidacionTdvEuropa,
    aplicar_contenedores_especiales,
    extraer_liquidacion_tdv_europa,
)
from services.tdv_europa.matcher import (
    CLIENTE_TDV_EUROPA_PREFIXES,
    ResultadoMatcherTdvEuropa,
    buscar_lineas_despachos_tdv_europa,
)
from services.tdv_europa.validator import (
    ResultadoValidacionTdvEuropa,
    validar_liquidacion_tdv_europa,
)


EstadoProcesamientoTdvEuropa = Literal[
    "invalido",
    "listo",
]


@dataclass(frozen=True)
class ResultadoPreparacionTdvEuropa:
    estado: EstadoProcesamientoTdvEuropa
    puede_escribir: bool
    destino_final: str | None
    origen_destino: str | None
    liquidacion: LiquidacionTdvEuropa
    despachos: ResultadoMatcherTdvEuropa
    validacion: ResultadoValidacionTdvEuropa


class ErrorProcesamientoTdvEuropa(Exception):
    """Error general del procesamiento TDV Europa."""


def preparar_procesamiento_tdv_europa(
    ruta_liquidacion: str | Path,
    ruta_despachos: str | Path,
    *,
    semana: int,
    anio: int,
    destino: str,
    factura_corta: str,
    contenedores_especiales: tuple[str, ...] | list[str] | str = (),
    cliente_prefix: str | tuple[str, ...] = CLIENTE_TDV_EUROPA_PREFIXES,
) -> ResultadoPreparacionTdvEuropa:
    liquidacion = extraer_liquidacion_tdv_europa(ruta_liquidacion)
    liquidacion = aplicar_contenedores_especiales(
        liquidacion,
        contenedores_especiales,
    )
    factura_ui = str(factura_corta).strip()

    despachos = buscar_lineas_despachos_tdv_europa(
        ruta_archivo=ruta_despachos,
        semana=semana,
        anio=anio,
        destino=destino,
        factura_corta=factura_ui,
        cliente_prefix=cliente_prefix,
    )

    validacion = validar_liquidacion_tdv_europa(
        liquidacion=liquidacion,
        despachos=despachos,
        destino_ui=destino,
        factura_ui=factura_ui,
        semana_ui=int(semana),
        anio_ui=int(anio),
    )

    if not validacion.es_valido:
        return ResultadoPreparacionTdvEuropa(
            estado="invalido",
            puede_escribir=False,
            destino_final=None,
            origen_destino=None,
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    return ResultadoPreparacionTdvEuropa(
        estado="listo",
        puede_escribir=True,
        destino_final=validacion.destino_final,
        origen_destino="ui",
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
        return [convertir_a_json(elemento) for elemento in valor]
    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepara una liquidación TDV Europa.",
    )
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--despachos", required=True)
    parser.add_argument("--semana", type=int, required=True)
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--destino", required=True)
    parser.add_argument("--factura", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                preparar_procesamiento_tdv_europa(
                    ruta_liquidacion=args.pdf,
                    ruta_despachos=args.despachos,
                    semana=args.semana,
                    anio=args.anio,
                    destino=args.destino,
                    factura_corta=args.factura,
                )
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
