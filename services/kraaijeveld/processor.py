from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.kraaijeveld.extractor import (
    LiquidacionKraaijeveld,
    extraer_liquidaciones_kraaijeveld,
)
from services.kraaijeveld.matcher import (
    CLIENTE_KRAAIJEVELD,
    ResultadoMatcherKraaijeveld,
    buscar_lineas_despachos_kraaijeveld,
)
from services.kraaijeveld.validator import (
    ResultadoValidacionKraaijeveld,
    validar_liquidaciones_kraaijeveld,
)


EstadoProcesamientoKraaijeveld = Literal["invalido", "listo"]


@dataclass(frozen=True)
class ResultadoPreparacionKraaijeveld:
    estado: EstadoProcesamientoKraaijeveld
    puede_escribir: bool
    liquidaciones: tuple[LiquidacionKraaijeveld, ...]
    despachos: ResultadoMatcherKraaijeveld
    validacion: ResultadoValidacionKraaijeveld


class ErrorProcesamientoKraaijeveld(Exception):
    """Error general del procesamiento Kraaijeveld."""


def preparar_procesamiento_kraaijeveld(
    rutas_pdf: list[str | Path],
    ruta_despachos: str | Path,
    *,
    semana: int,
    anio: int,
    destino: str,
    incluye_precio_fijo: bool = False,
    factura_corta_fijo: str | None = None,
    precio_fijo: Decimal | None = None,
    moneda_fijo: str | None = None,
    cliente: str = CLIENTE_KRAAIJEVELD,
) -> ResultadoPreparacionKraaijeveld:
    liquidaciones = extraer_liquidaciones_kraaijeveld(rutas_pdf)

    facturas: set[str] = {
        liq.factura_corta for liq in liquidaciones
    }
    if incluye_precio_fijo and factura_corta_fijo:
        facturas.add(str(factura_corta_fijo).strip())

    despachos = buscar_lineas_despachos_kraaijeveld(
        ruta_archivo=ruta_despachos,
        semana=semana,
        anio=anio,
        destino=destino,
        cliente=cliente,
        facturas_cortas=facturas or None,
    )

    validacion = validar_liquidaciones_kraaijeveld(
        liquidaciones=liquidaciones,
        despachos=despachos,
        destino_ui=destino,
        incluye_precio_fijo=incluye_precio_fijo,
        factura_corta_fijo=factura_corta_fijo,
        precio_fijo=precio_fijo,
        moneda_fijo=moneda_fijo,
    )

    if not validacion.es_valido:
        return ResultadoPreparacionKraaijeveld(
            estado="invalido",
            puede_escribir=False,
            liquidaciones=liquidaciones,
            despachos=despachos,
            validacion=validacion,
        )

    return ResultadoPreparacionKraaijeveld(
        estado="listo",
        puede_escribir=True,
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
    parser = argparse.ArgumentParser(
        description="Prepara liquidaciones Kraaijeveld.",
    )
    parser.add_argument("--pdf", action="append", required=True)
    parser.add_argument("--despachos", required=True)
    parser.add_argument("--semana", type=int, required=True)
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--destino", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                preparar_procesamiento_kraaijeveld(
                    rutas_pdf=args.pdf,
                    ruta_despachos=args.despachos,
                    semana=args.semana,
                    anio=args.anio,
                    destino=args.destino,
                )
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
