from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Mapping

from services.eurobanan.extractor import (
    extraer_liquidacion_eurobanan,
)
from services.eurobanan.matcher import (
    CLIENTE_EUROBANAN,
    ResultadoMatcherEurobanan,
    buscar_lineas_despachos_eurobanan,
)
from services.eurobanan.validator import (
    ResultadoValidacionEurobanan,
    validar_liquidacion_eurobanan,
)
from services.eurobanan.extractor import LiquidacionEurobanan


EstadoProcesamientoEurobanan = Literal[
    "invalido",
    "pendiente_mapeo_gastos",
    "listo",
]


@dataclass(frozen=True)
class ResultadoPreparacionEurobanan:
    estado: EstadoProcesamientoEurobanan
    puede_escribir: bool
    destino_final: str | None
    origen_destino: str | None
    liquidacion: LiquidacionEurobanan
    despachos: ResultadoMatcherEurobanan
    validacion: ResultadoValidacionEurobanan


class ErrorProcesamientoEurobanan(Exception):
    """Error general del procesamiento EUROBANAN."""


def preparar_procesamiento_eurobanan(
    ruta_liquidacion: str | Path,
    ruta_despachos: str | Path,
    *,
    semana: int,
    anio: int,
    destino: str,
    factura_corta: str,
    mapeos_extra: Mapping[str, str] | None = None,
    cliente: str = CLIENTE_EUROBANAN,
) -> ResultadoPreparacionEurobanan:
    liquidacion = extraer_liquidacion_eurobanan(
        ruta_liquidacion,
        mapeos_extra=mapeos_extra,
    )

    factura_ui = str(factura_corta).strip()

    despachos = buscar_lineas_despachos_eurobanan(
        ruta_archivo=ruta_despachos,
        semana=semana,
        anio=anio,
        destino=destino,
        factura_corta=factura_ui,
        cliente=cliente,
    )

    validacion = validar_liquidacion_eurobanan(
        liquidacion=liquidacion,
        despachos=despachos,
        destino_ui=destino,
        factura_ui=factura_ui,
        semana_ui=int(semana),
        anio_ui=int(anio),
    )

    solo_mapeo = bool(liquidacion.rubros_no_mapeados) and all(
        err.codigo == "RUBROS_NO_MAPEADOS"
        for err in validacion.errores
    )

    if solo_mapeo:
        return ResultadoPreparacionEurobanan(
            estado="pendiente_mapeo_gastos",
            puede_escribir=False,
            destino_final=validacion.destino_final or destino,
            origen_destino="ui",
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    if not validacion.es_valido:
        return ResultadoPreparacionEurobanan(
            estado="invalido",
            puede_escribir=False,
            destino_final=None,
            origen_destino=None,
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    if liquidacion.rubros_no_mapeados:
        return ResultadoPreparacionEurobanan(
            estado="pendiente_mapeo_gastos",
            puede_escribir=False,
            destino_final=validacion.destino_final or destino,
            origen_destino="ui",
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    return ResultadoPreparacionEurobanan(
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
        description="Prepara una liquidación EUROBANAN.",
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
                preparar_procesamiento_eurobanan(
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
