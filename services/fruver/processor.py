from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.fruver.extractor import (
    LiquidacionFruver,
    extraer_liquidaciones_fruver,
)
from services.fruver.matcher import (
    CLIENTE_FRUVER,
    ResultadoMatcherFruver,
    buscar_lineas_despachos_fruver,
)
from services.fruver.validator import (
    ResultadoValidacionFruver,
    validar_liquidaciones_fruver,
)


EstadoProcesamientoFruver = Literal["invalido", "listo"]


@dataclass(frozen=True)
class ResultadoPreparacionFruver:
    estado: EstadoProcesamientoFruver
    puede_escribir: bool
    liquidaciones: tuple[LiquidacionFruver, ...]
    despachos: ResultadoMatcherFruver
    validacion: ResultadoValidacionFruver


class ErrorProcesamientoFruver(Exception):
    """Error general del procesamiento FRU&VER."""


def preparar_procesamiento_fruver(
    rutas_pdf: list[str | Path],
    ruta_despachos: str | Path,
    *,
    semana: int,
    anio: int,
    destino: str,
    factura_corta: str,
    cliente: str = CLIENTE_FRUVER,
) -> ResultadoPreparacionFruver:
    liquidaciones = extraer_liquidaciones_fruver(rutas_pdf)
    despachos = buscar_lineas_despachos_fruver(
        ruta_archivo=ruta_despachos,
        semana=semana,
        anio=anio,
        destino=destino,
        factura_corta=factura_corta,
        cliente=cliente,
    )
    validacion = validar_liquidaciones_fruver(
        liquidaciones=liquidaciones,
        despachos=despachos,
        destino_ui=destino,
        factura_ui=factura_corta,
    )
    estado: EstadoProcesamientoFruver = (
        "listo" if validacion.es_valido else "invalido"
    )
    return ResultadoPreparacionFruver(
        estado=estado,
        puede_escribir=validacion.es_valido
        and bool(validacion.lineas_preparadas),
        liquidaciones=liquidaciones,
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--despachos", required=True)
    parser.add_argument("--semana", type=int, required=True)
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--destino", required=True)
    parser.add_argument("--factura", required=True)
    parser.add_argument("pdfs", nargs="+")
    args = parser.parse_args()
    resultado = preparar_procesamiento_fruver(
        args.pdfs,
        args.despachos,
        semana=args.semana,
        anio=args.anio,
        destino=args.destino,
        factura_corta=args.factura,
    )
    print(
        json.dumps(
            convertir_a_json(resultado),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
