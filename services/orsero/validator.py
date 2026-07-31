from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.orsero.extractor import LiquidacionOrsero
from services.orsero.matcher import (
    LineaDespachoOrsero,
    ResultadoMatcherOrsero,
    normalizar_texto,
)


NivelIncidencia = Literal["error", "advertencia"]


@dataclass(frozen=True)
class IncidenciaValidacionOrsero:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaOrsero:
    despacho: LineaDespachoOrsero
    tipo_fruta: str
    calibre: int
    destino: str
    precio_venta_eur: Decimal
    tipo_cambio_usd_eur: Decimal
    gastos: dict[str, Decimal]
    precio_encontrado: bool


@dataclass(frozen=True)
class ResultadoValidacionOrsero:
    es_valido: bool
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    tipo_cambio_usd_eur: Decimal
    errores: tuple[IncidenciaValidacionOrsero, ...]
    advertencias: tuple[IncidenciaValidacionOrsero, ...]
    lineas_preparadas: tuple[LineaPreparadaOrsero, ...]


def clave_destino_calibre(
    destino: str,
    calibre: int,
) -> tuple[str, int]:
    return normalizar_texto(destino), calibre


def describir_clave(clave: tuple[str, int]) -> str:
    destino, calibre = clave
    return f"{destino.title()}, calibre {calibre}"


def _mejor_precio_para_destino(
    precios: dict[tuple[str, int], Decimal],
    destino: str,
    calibre: int,
) -> Decimal | None:
    clave = clave_destino_calibre(destino, calibre)
    if clave in precios:
        return precios[clave]

    destino_n = clave[0]
    # Tolerancia: destino Despachos puede ser más largo
    # (ej. SETUBAL vs SETUBAL, PORTUGAL).
    for (dest_liq, cal), precio in precios.items():
        if cal != calibre:
            continue
        if (
            dest_liq in destino_n
            or destino_n in dest_liq
        ):
            return precio
    return None


def validar_liquidacion_orsero(
    liquidacion: LiquidacionOrsero,
    despachos: ResultadoMatcherOrsero,
) -> ResultadoValidacionOrsero:
    errores: list[IncidenciaValidacionOrsero] = []
    advertencias: list[IncidenciaValidacionOrsero] = []

    if liquidacion.semana != despachos.semana:
        errores.append(
            IncidenciaValidacionOrsero(
                codigo="SEMANA_DIFERENTE",
                nivel="error",
                mensaje=(
                    "La semana del screenshot no coincide "
                    "con Despachos."
                ),
                detalles={
                    "semana_liquidacion": liquidacion.semana,
                    "semana_despachos": despachos.semana,
                },
            )
        )

    precios: dict[tuple[str, int], Decimal] = {}
    cajas_liq: dict[tuple[str, int], int] = defaultdict(int)

    for producto in liquidacion.precios:
        clave = clave_destino_calibre(
            producto.destino,
            producto.calibre,
        )
        if producto.precio_eur <= 0:
            advertencias.append(
                IncidenciaValidacionOrsero(
                    codigo="PRECIO_INVALIDO",
                    nivel="advertencia",
                    mensaje=(
                        "Hay un precio inválido en el "
                        "screenshot."
                    ),
                    detalles={
                        "producto": describir_clave(clave),
                        "precio_eur": str(producto.precio_eur),
                    },
                )
            )
        if (
            clave in precios
            and precios[clave] != producto.precio_eur
        ):
            advertencias.append(
                IncidenciaValidacionOrsero(
                    codigo="PRECIOS_CONFLICTIVOS",
                    nivel="advertencia",
                    mensaje=(
                        "El mismo destino/calibre tiene "
                        "precios distintos en el screenshot."
                    ),
                    detalles={
                        "producto": describir_clave(clave),
                    },
                )
            )
        else:
            precios[clave] = producto.precio_eur
        cajas_liq[clave] += producto.total_cajas

    if liquidacion.total_cajas != despachos.total_cajas:
        advertencias.append(
            IncidenciaValidacionOrsero(
                codigo="TOTAL_CAJAS_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El total de cajas del screenshot no "
                    "coincide con las líneas Especial de "
                    "Despachos."
                ),
                detalles={
                    "cajas_liquidacion": liquidacion.total_cajas,
                    "cajas_despachos": despachos.total_cajas,
                },
            )
        )

    if not despachos.destinos:
        errores.append(
            IncidenciaValidacionOrsero(
                codigo="SIN_DESTINO_DESPACHOS",
                nivel="error",
                mensaje=(
                    "Las líneas Especial de Despachos no "
                    "tienen puerto destino."
                ),
            )
        )

    lineas_preparadas: list[LineaPreparadaOrsero] = []
    for linea in despachos.lineas:
        destino = linea.puerto_destino.strip().upper()
        precio = _mejor_precio_para_destino(
            precios,
            destino,
            linea.calibre,
        )
        encontrado = precio is not None
        if not encontrado:
            advertencias.append(
                IncidenciaValidacionOrsero(
                    codigo="PRECIO_NO_ENCONTRADO",
                    nivel="advertencia",
                    mensaje=(
                        "No hay precio en el screenshot para "
                        f"{describir_clave((normalizar_texto(destino), linea.calibre))}."
                    ),
                    detalles={
                        "contenedor": linea.contenedor,
                        "destino": destino,
                        "calibre": linea.calibre,
                    },
                )
            )
            precio = Decimal("0")

        lineas_preparadas.append(
            LineaPreparadaOrsero(
                despacho=linea,
                tipo_fruta=linea.tipo_fruta,
                calibre=linea.calibre,
                destino=destino,
                precio_venta_eur=precio,
                tipo_cambio_usd_eur=(
                    liquidacion.tipo_cambio_usd_eur
                ),
                gastos=dict(liquidacion.gastos),
                precio_encontrado=encontrado,
            )
        )

    es_valido = not errores
    return ResultadoValidacionOrsero(
        es_valido=es_valido,
        destinos_despachos=despachos.destinos,
        total_cajas_liquidacion=liquidacion.total_cajas,
        total_cajas_despachos=despachos.total_cajas,
        tipo_cambio_usd_eur=liquidacion.tipo_cambio_usd_eur,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_preparadas),
    )
