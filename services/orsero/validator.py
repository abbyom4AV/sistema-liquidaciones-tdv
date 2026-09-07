from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal, Sequence

from services.orsero.extractor import (
    LineaPrecioOrsero,
    LiquidacionOrsero,
)
from services.orsero.matcher import (
    LineaDespachoOrsero,
    ResultadoMatcherOrsero,
    normalizar_texto,
)
from services.mensajes_gastos import (
    etiquetas_rubros,
    mensaje_gastos_no_mapeados,
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
    destinos_aplicados: tuple[str, ...]
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


def _bloques_de_precios(
    precios: Sequence[LineaPrecioOrsero],
) -> tuple[str, ...]:
    """Destinos del screenshot, en el orden en que aparecen."""
    orden: list[str] = []
    for precio in precios:
        if precio.destino not in orden:
            orden.append(precio.destino)
    return tuple(orden)


def aplicar_destinos_manuales(
    precios: Sequence[LineaPrecioOrsero],
    destinos_manuales: Sequence[str],
) -> tuple[tuple[LineaPrecioOrsero, ...], tuple[str, ...]]:
    """Renombra los bloques del screenshot con los destinos dados.

    El destino indicado manda sobre el que leyó el OCR y se asigna
    por orden: el primero que escribe el usuario corresponde al
    primer bloque del screenshot.
    """
    limpios = tuple(
        texto.strip().upper()
        for texto in destinos_manuales
        if texto and texto.strip()
    )
    if not limpios:
        return tuple(precios), ()

    bloques = _bloques_de_precios(precios)
    equivalencias = {
        original: limpios[indice]
        for indice, original in enumerate(bloques)
        if indice < len(limpios)
    }
    renombrados = tuple(
        replace(
            precio,
            destino=equivalencias.get(precio.destino, precio.destino),
        )
        for precio in precios
    )
    return renombrados, limpios


def _mejor_precio_para_destino(
    precios: dict[tuple[str, int], Decimal],
    destino: str,
    calibre: int,
) -> tuple[Decimal | None, bool]:
    """Precio del screenshot y si el destino coincidió."""
    clave = clave_destino_calibre(destino, calibre)
    if clave in precios:
        return precios[clave], True

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
            return precio, True

    return None, False


def validar_liquidacion_orsero(
    liquidacion: LiquidacionOrsero,
    despachos: ResultadoMatcherOrsero,
    destinos_manuales: Sequence[str] = (),
) -> ResultadoValidacionOrsero:
    errores: list[IncidenciaValidacionOrsero] = []
    advertencias: list[IncidenciaValidacionOrsero] = []

    precios_liquidacion, destinos_aplicados = aplicar_destinos_manuales(
        liquidacion.precios,
        destinos_manuales,
    )
    bloques = _bloques_de_precios(liquidacion.precios)
    if destinos_aplicados and len(destinos_aplicados) < len(bloques):
        advertencias.append(
            IncidenciaValidacionOrsero(
                codigo="DESTINOS_INSUFICIENTES",
                nivel="advertencia",
                mensaje=(
                    f"Indicó {len(destinos_aplicados)} destino(s) "
                    f"y el screenshot trae {len(bloques)}. Los "
                    "bloques restantes conservan el nombre leído "
                    "por OCR."
                ),
                detalles={
                    "destinos_indicados": list(destinos_aplicados),
                    "bloques_screenshot": list(bloques),
                },
            )
        )
    elif destinos_aplicados and len(destinos_aplicados) > len(bloques):
        advertencias.append(
            IncidenciaValidacionOrsero(
                codigo="DESTINOS_SOBRANTES",
                nivel="advertencia",
                mensaje=(
                    f"Indicó {len(destinos_aplicados)} destino(s) "
                    f"y el screenshot solo trae {len(bloques)} "
                    "bloque(s) de precios."
                ),
                detalles={
                    "destinos_indicados": list(destinos_aplicados),
                    "bloques_screenshot": list(bloques),
                },
            )
        )

    if liquidacion.rubros_no_mapeados:
        rubros = etiquetas_rubros(liquidacion.rubros_no_mapeados)
        errores.append(
            IncidenciaValidacionOrsero(
                codigo="RUBROS_NO_MAPEADOS",
                nivel="error",
                mensaje=mensaje_gastos_no_mapeados(rubros),
                detalles={"rubros": rubros},
            )
        )

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

    for producto in precios_liquidacion:
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
        precio, encontrado = _mejor_precio_para_destino(
            precios,
            destino,
            linea.calibre,
        )
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
        destinos_aplicados=destinos_aplicados,
        destinos_despachos=despachos.destinos,
        total_cajas_liquidacion=liquidacion.total_cajas,
        total_cajas_despachos=despachos.total_cajas,
        tipo_cambio_usd_eur=liquidacion.tipo_cambio_usd_eur,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_preparadas),
    )
