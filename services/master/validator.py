from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.master.extractor import LiquidacionMaster
from services.master.matcher import (
    LineaDespachoMaster,
    ResultadoMatcherMaster,
    normalizar_texto,
)
from services.mensajes_gastos import (
    etiquetas_rubros,
    mensaje_gastos_no_mapeados,
)


NivelIncidencia = Literal["error", "advertencia"]


@dataclass(frozen=True)
class IncidenciaValidacionMaster:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaMaster:
    despacho: LineaDespachoMaster
    tipo_fruta: str
    variante: str
    calibre: int
    precio_venta_eur: Decimal
    merma: int
    gastos: dict[str, Decimal]


@dataclass(frozen=True)
class ResultadoValidacionMaster:
    es_valido: bool
    destino_final: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    total_venta_eur: Decimal
    errores: tuple[IncidenciaValidacionMaster, ...]
    advertencias: tuple[IncidenciaValidacionMaster, ...]
    lineas_preparadas: tuple[LineaPreparadaMaster, ...]


def clave_variante_calibre(
    variante: str,
    calibre: int,
) -> tuple[str, int]:
    return normalizar_texto(variante), calibre


def describir_clave(clave: tuple[str, int]) -> str:
    variante, calibre = clave
    return f"{variante.title()}, calibre {calibre}"


def validar_liquidacion_master(
    liquidacion: LiquidacionMaster,
    despachos: ResultadoMatcherMaster,
) -> ResultadoValidacionMaster:
    errores: list[IncidenciaValidacionMaster] = []
    advertencias: list[IncidenciaValidacionMaster] = []

    if liquidacion.rubros_no_mapeados:
        rubros = etiquetas_rubros(liquidacion.rubros_no_mapeados)
        errores.append(
            IncidenciaValidacionMaster(
                codigo="RUBROS_NO_MAPEADOS",
                nivel="error",
                mensaje=mensaje_gastos_no_mapeados(rubros),
                detalles={"rubros": rubros},
            )
        )

    precios: dict[tuple[str, int], Decimal] = {}
    cajas_pdf: dict[tuple[str, int], int] = defaultdict(int)
    mermas_pdf: dict[tuple[str, int], int] = defaultdict(int)

    for producto in liquidacion.productos:
        clave = clave_variante_calibre(
            producto.variante,
            producto.calibre,
        )

        if producto.precio_eur <= 0:
            errores.append(
                IncidenciaValidacionMaster(
                    codigo="PRECIO_INVALIDO",
                    nivel="error",
                    mensaje=(
                        "Hay una línea con precio inválido "
                        "en la liquidación."
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
            errores.append(
                IncidenciaValidacionMaster(
                    codigo="PRECIOS_CONFLICTIVOS",
                    nivel="error",
                    mensaje=(
                        "La misma variante/calibre tiene "
                        "precios distintos en el PDF."
                    ),
                    detalles={
                        "producto": describir_clave(clave),
                    },
                )
            )
        else:
            precios[clave] = producto.precio_eur

        cajas_pdf[clave] += producto.boxes_in
        mermas_pdf[clave] += producto.merma

        if producto.merma > 0:
            advertencias.append(
                IncidenciaValidacionMaster(
                    codigo="MERMA_REPORTADA",
                    nivel="advertencia",
                    mensaje=(
                        f"Se reportó merma de {producto.merma} "
                        f"caja(s) en {describir_clave(clave)}."
                    ),
                    detalles={
                        "variante": producto.variante,
                        "tipo_fruta": producto.tipo_fruta,
                        "calibre": producto.calibre,
                        "merma": producto.merma,
                        "boxes_in": producto.boxes_in,
                        "sold_boxes": producto.sold_boxes,
                        "waste": producto.waste,
                    },
                )
            )

    cajas_desp: dict[tuple[str, int], int] = defaultdict(int)
    for linea in despachos.lineas:
        clave = clave_variante_calibre(
            linea.variante,
            linea.calibre,
        )
        cajas_desp[clave] += linea.total_cajas

    if liquidacion.total_boxes != despachos.total_cajas:
        advertencias.append(
            IncidenciaValidacionMaster(
                codigo="TOTAL_CAJAS_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El total de cajas del PDF no coincide "
                    "con Despachos."
                ),
                detalles={
                    "cajas_pdf": liquidacion.total_boxes,
                    "cajas_despachos": despachos.total_cajas,
                },
            )
        )

    todas_claves = set(cajas_pdf) | set(cajas_desp)
    for clave in sorted(todas_claves, key=lambda item: item[0]):
        if cajas_pdf.get(clave, 0) != cajas_desp.get(clave, 0):
            advertencias.append(
                IncidenciaValidacionMaster(
                    codigo="CAJAS_POR_PRODUCTO_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        "Hay diferencia de cajas entre PDF y "
                        f"Despachos en {describir_clave(clave)}."
                    ),
                    detalles={
                        "producto": describir_clave(clave),
                        "cajas_pdf": cajas_pdf.get(clave, 0),
                        "cajas_despachos": cajas_desp.get(
                            clave,
                            0,
                        ),
                    },
                )
            )

    contenedores_pdf = {
        normalizar_texto(item)
        for item in liquidacion.contenedores
    }
    contenedores_desp = {
        normalizar_texto(item)
        for item in despachos.contenedores
    }
    if contenedores_pdf and contenedores_pdf != contenedores_desp:
        advertencias.append(
            IncidenciaValidacionMaster(
                codigo="CONTENEDORES_DIFERENTES",
                nivel="advertencia",
                mensaje=(
                    "Los contenedores del PDF no coinciden "
                    "exactamente con Despachos."
                ),
                detalles={
                    "pdf": sorted(contenedores_pdf),
                    "despachos": sorted(contenedores_desp),
                },
            )
        )

    if not despachos.destinos:
        errores.append(
            IncidenciaValidacionMaster(
                codigo="SIN_DESTINO_DESPACHOS",
                nivel="error",
                mensaje=(
                    "Las líneas de Despachos no tienen "
                    "puerto destino."
                ),
            )
        )
        destino_final = ""
    elif len(despachos.destinos) > 1:
        advertencias.append(
            IncidenciaValidacionMaster(
                codigo="DESTINOS_MULTIPLES",
                nivel="advertencia",
                mensaje=(
                    "Hay más de un destino en Despachos; "
                    "se usará el primero."
                ),
                detalles={
                    "destinos": list(despachos.destinos),
                },
            )
        )
        destino_final = despachos.destinos[0]
    else:
        destino_final = despachos.destinos[0]

    mermas_pendientes = dict(mermas_pdf)
    lineas_preparadas: list[LineaPreparadaMaster] = []

    for linea in despachos.lineas:
        clave = clave_variante_calibre(
            linea.variante,
            linea.calibre,
        )
        precio = precios.get(clave)
        if precio is None:
            errores.append(
                IncidenciaValidacionMaster(
                    codigo="PRECIO_NO_ENCONTRADO",
                    nivel="error",
                    mensaje=(
                        "No hay precio en el PDF para "
                        f"{describir_clave(clave)}."
                    ),
                    detalles={
                        "contenedor": linea.contenedor,
                        "carton": linea.carton,
                    },
                )
            )
            precio = Decimal("0")

        merma = 0
        pendiente = mermas_pendientes.get(clave, 0)
        if pendiente > 0:
            merma = pendiente
            mermas_pendientes[clave] = 0

        lineas_preparadas.append(
            LineaPreparadaMaster(
                despacho=linea,
                tipo_fruta=linea.tipo_fruta,
                variante=linea.variante,
                calibre=linea.calibre,
                precio_venta_eur=precio,
                merma=merma,
                gastos=dict(liquidacion.gastos),
            )
        )

    for clave, pendiente in mermas_pendientes.items():
        if pendiente > 0:
            errores.append(
                IncidenciaValidacionMaster(
                    codigo="MERMA_SIN_LINEA",
                    nivel="error",
                    mensaje=(
                        "Hay merma en el PDF sin línea de "
                        "Despachos donde asignarla "
                        f"({describir_clave(clave)})."
                    ),
                    detalles={"merma": pendiente},
                )
            )

    es_valido = not errores

    return ResultadoValidacionMaster(
        es_valido=es_valido,
        destino_final=destino_final,
        destinos_despachos=despachos.destinos,
        total_cajas_liquidacion=liquidacion.total_boxes,
        total_cajas_despachos=despachos.total_cajas,
        total_venta_eur=liquidacion.total_venta_eur,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_preparadas),
    )
