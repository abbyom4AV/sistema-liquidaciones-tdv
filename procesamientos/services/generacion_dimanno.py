from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Mapping

from services.dimanno.processor import (
    OrigenDestino,
    ResultadoPreparacionDimanno,
    limpiar_destino_confirmado,
)

RUBROS_GASTOS_ESPERADOS: tuple[str, ...] = (
    "Comisión",
    "Flete Eu",
    "Control calidad Eu",
    "THC",
    "Transporte",
    "Aduanas",
)


class ErrorConfirmacionGeneracionDimanno(Exception):
    """Error al aplicar destino o gastos confirmados."""


def _decimal_a_texto(valor: Decimal) -> str:
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto if texto else "0"


def serializar_gastos_aplicados(
    gastos: Mapping[str, Decimal],
) -> dict[str, str]:
    serializados: dict[str, str] = {}
    for rubro in RUBROS_GASTOS_ESPERADOS:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionDimanno(
                f"Falta el rubro de gasto {rubro!r}."
            )
        valor = gastos[rubro]
        if not isinstance(valor, Decimal):
            raise ErrorConfirmacionGeneracionDimanno(
                f"El gasto {rubro!r} debe ser Decimal."
            )
        serializados[rubro] = _decimal_a_texto(valor)
    return serializados


def deserializar_gastos_aplicados(
    gastos: Mapping[str, object],
) -> dict[str, Decimal]:
    resultado: dict[str, Decimal] = {}
    for rubro in RUBROS_GASTOS_ESPERADOS:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionDimanno(
                f"Falta el rubro de gasto {rubro!r}."
            )
        crudo = gastos[rubro]
        try:
            if isinstance(crudo, Decimal):
                valor = crudo
            elif isinstance(crudo, (int, str)):
                valor = Decimal(str(crudo))
            else:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ErrorConfirmacionGeneracionDimanno(
                f"El gasto {rubro!r} no es numérico."
            ) from error
        resultado[rubro] = valor
    return resultado


def aplicar_confirmaciones_a_procesamiento(
    procesamiento_motor: ResultadoPreparacionDimanno,
    destino_final: str,
    gastos_aplicados: Mapping[str, object],
    origen_destino: str | None = None,
) -> ResultadoPreparacionDimanno:
    if not procesamiento_motor.validacion.es_valido:
        raise ErrorConfirmacionGeneracionDimanno(
            "El procesamiento dejó de cumplir las "
            "validaciones requeridas."
        )

    destino = limpiar_destino_confirmado(destino_final)
    if destino is None:
        raise ErrorConfirmacionGeneracionDimanno(
            "No hay un destino final confirmado."
        )

    gastos = deserializar_gastos_aplicados(gastos_aplicados)
    liquidacion = replace(
        procesamiento_motor.liquidacion,
        gastos=gastos,
    )

    origen_valido: OrigenDestino | None = None
    if origen_destino in {
        "coincidente",
        "despachos",
        "liquidacion",
        "manual",
    }:
        origen_valido = origen_destino  # type: ignore[assignment]
    elif procesamiento_motor.origen_destino is not None:
        origen_valido = procesamiento_motor.origen_destino
    else:
        origen_valido = "manual"

    return replace(
        procesamiento_motor,
        estado="listo",
        puede_escribir=True,
        destino_final=destino,
        origen_destino=origen_valido,
        liquidacion=liquidacion,
    )


NOMBRE_DESCARGA_DIMANNO = "DIMANNO Liquidaciones v2.1.xlsx"


def construir_nombre_descarga(
    *,
    anio: int | None = None,
    semana: int | None = None,
    factura_corta: str | None = None,
) -> str:
    """Nombre fijo de descarga visible al usuario."""
    del anio, semana, factura_corta
    return NOMBRE_DESCARGA_DIMANNO
