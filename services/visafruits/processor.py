from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.visafruits.extractor import (
    LiquidacionVisafruits,
    extraer_liquidacion_visafruits,
)
from services.visafruits.matcher import (
    CLIENTE_VISAFRUITS_DESPACHOS,
    ResultadoMatcherVisafruits,
    buscar_lineas_despachos_visafruits,
)
from services.visafruits.validator import (
    ResultadoValidacionVisafruits,
    validar_liquidacion_visafruits,
)


EstadoProcesamientoVisafruits = Literal["invalido", "listo"]


@dataclass(frozen=True)
class ResultadoPreparacionVisafruits:
    estado: EstadoProcesamientoVisafruits
    puede_escribir: bool
    liquidacion: LiquidacionVisafruits
    despachos: ResultadoMatcherVisafruits
    validacion: ResultadoValidacionVisafruits


class ErrorProcesamientoVisafruits(Exception):
    """Error general del procesamiento VISAFRUITS."""


def preparar_procesamiento_visafruits(
    ruta_liquidacion: str | Path,
    ruta_despachos: str | Path,
    *,
    semana: int,
    anio: int,
    destino: str,
    factura_corta: str,
    cliente: str = CLIENTE_VISAFRUITS_DESPACHOS,
) -> ResultadoPreparacionVisafruits:
    """Semana, año, destino y factura los digita el usuario; el PDF
    solo aporta precios, gastos y comisión."""
    liquidacion = extraer_liquidacion_visafruits(ruta_liquidacion)

    despachos = buscar_lineas_despachos_visafruits(
        ruta_archivo=ruta_despachos,
        semana=semana,
        anio=anio,
        destino=destino,
        factura_corta=factura_corta,
        cliente=cliente,
    )

    validacion = validar_liquidacion_visafruits(
        liquidacion=liquidacion,
        despachos=despachos,
        destino_ui=destino,
    )

    estado: EstadoProcesamientoVisafruits = (
        "listo" if validacion.es_valido else "invalido"
    )
    return ResultadoPreparacionVisafruits(
        estado=estado,
        puede_escribir=validacion.es_valido,
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
        description="Prepara una liquidación VISAFRUITS.",
    )
    parser.add_argument("--liquidacion", required=True)
    parser.add_argument("--despachos", required=True)
    parser.add_argument("--semana", type=int, required=True)
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--destino", required=True)
    parser.add_argument("--factura", required=True)
    args = parser.parse_args()

    resultado = preparar_procesamiento_visafruits(
        ruta_liquidacion=args.liquidacion,
        ruta_despachos=args.despachos,
        semana=args.semana,
        anio=args.anio,
        destino=args.destino,
        factura_corta=args.factura,
    )
    datos = convertir_a_json(resultado)
    datos.get("liquidacion", {}).pop("texto", None)
    print(json.dumps(datos, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
