from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.dimanno.extractor import (
    LiquidacionDimanno,
    extraer_liquidacion,
)
from services.dimanno.matcher import (
    ResultadoMatcher,
    buscar_lineas_despachos,
    normalizar_texto,
)
from services.dimanno.validator import (
    ResultadoValidacion,
    validar_liquidacion,
)


EstadoProcesamiento = Literal[
    "invalido",
    "requiere_destino",
    "listo",
]

OrigenDestino = Literal[
    "coincidente",
    "despachos",
    "liquidacion",
    "manual",
]


@dataclass(frozen=True)
class ResultadoPreparacionDimanno:
    estado: EstadoProcesamiento
    puede_escribir: bool
    destino_final: str | None
    origen_destino: OrigenDestino | None
    liquidacion: LiquidacionDimanno
    despachos: ResultadoMatcher
    validacion: ResultadoValidacion


class ErrorProcesamientoDimanno(Exception):
    """Error general del procesamiento de Di Manno."""


class DestinoConfirmadoInvalidoError(
    ErrorProcesamientoDimanno
):
    """El destino confirmado por el usuario no es válido."""


def limpiar_destino_confirmado(
    destino: str | None,
) -> str | None:
    if destino is None:
        return None

    destino_limpio = destino.strip().upper()

    if not destino_limpio:
        return None

    return destino_limpio


def determinar_origen_destino(
    destino_confirmado: str,
    liquidacion: LiquidacionDimanno,
    validacion: ResultadoValidacion,
) -> OrigenDestino:
    destino_normalizado = normalizar_texto(
        destino_confirmado
    )

    if destino_normalizado == normalizar_texto(
        liquidacion.destino
    ):
        return "liquidacion"

    if (
        len(validacion.destinos_despachos) == 1
        and destino_normalizado
        == normalizar_texto(
            validacion.destinos_despachos[0]
        )
    ):
        return "despachos"

    return "manual"


def preparar_procesamiento_dimanno(
    ruta_liquidacion: str | Path,
    nombre_hoja: str,
    ruta_despachos: str | Path,
    anio: int,
    cliente: str = "DI MANNO",
    destino_confirmado: str | None = None,
) -> ResultadoPreparacionDimanno:
    liquidacion = extraer_liquidacion(
        ruta_archivo=ruta_liquidacion,
        nombre_hoja=nombre_hoja,
    )

    despachos = buscar_lineas_despachos(
        ruta_archivo=ruta_despachos,
        cliente=cliente,
        anio=anio,
        semana=liquidacion.semana,
        factura_corta=liquidacion.factura_corta,
    )

    validacion = validar_liquidacion(
        liquidacion=liquidacion,
        despachos=despachos,
    )

    if not validacion.es_valido:
        return ResultadoPreparacionDimanno(
            estado="invalido",
            puede_escribir=False,
            destino_final=None,
            origen_destino=None,
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    destino_limpio = limpiar_destino_confirmado(
        destino_confirmado
    )

    if validacion.requiere_resolver_destino:
        if destino_limpio is None:
            return ResultadoPreparacionDimanno(
                estado="requiere_destino",
                puede_escribir=False,
                destino_final=None,
                origen_destino=None,
                liquidacion=liquidacion,
                despachos=despachos,
                validacion=validacion,
            )

        origen_destino = determinar_origen_destino(
            destino_confirmado=destino_limpio,
            liquidacion=liquidacion,
            validacion=validacion,
        )

        return ResultadoPreparacionDimanno(
            estado="listo",
            puede_escribir=True,
            destino_final=destino_limpio,
            origen_destino=origen_destino,
            liquidacion=liquidacion,
            despachos=despachos,
            validacion=validacion,
        )

    if len(validacion.destinos_despachos) != 1:
        raise DestinoConfirmadoInvalidoError(
            "No existe un único destino válido en Despachos."
        )

    destino_final = validacion.destinos_despachos[0]

    return ResultadoPreparacionDimanno(
        estado="listo",
        puede_escribir=True,
        destino_final=destino_final,
        origen_destino="coincidente",
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )


def convertir_a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return format(valor, "f")

    if is_dataclass(valor):
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
            "Prepara una liquidación Di Manno para su "
            "posterior escritura en Raw Data."
        )
    )

    parser.add_argument(
        "--liquidacion",
        required=True,
    )

    parser.add_argument(
        "--hoja",
        required=True,
    )

    parser.add_argument(
        "--despachos",
        required=True,
    )

    parser.add_argument(
        "--anio",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--cliente",
        default="DI MANNO",
    )

    parser.add_argument(
        "--destino-confirmado",
        default=None,
    )

    argumentos = parser.parse_args()

    resultado = preparar_procesamiento_dimanno(
        ruta_liquidacion=argumentos.liquidacion,
        nombre_hoja=argumentos.hoja,
        ruta_despachos=argumentos.despachos,
        anio=argumentos.anio,
        cliente=argumentos.cliente,
        destino_confirmado=(
            argumentos.destino_confirmado
        ),
    )

    print(
        json.dumps(
            convertir_a_json(resultado),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()