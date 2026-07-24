from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from services.dimanno.extractor import (
    LiquidacionDimanno,
    extraer_liquidacion,
)
from services.dimanno.matcher import (
    LineaDespacho,
    ResultadoMatcher,
    buscar_lineas_despachos,
    normalizar_texto,
)


NivelIncidencia = Literal["error", "advertencia"]


@dataclass(frozen=True)
class IncidenciaValidacion:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparada:
    despacho: LineaDespacho
    tipo_fruta: str
    calibre: int
    precio_venta_eur: Decimal


@dataclass(frozen=True)
class ResultadoValidacion:
    es_valido: bool
    requiere_resolver_destino: bool
    destino_liquidacion: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    total_venta_informado_eur: Decimal
    total_venta_calculado_eur: Decimal
    errores: tuple[IncidenciaValidacion, ...]
    advertencias: tuple[IncidenciaValidacion, ...]
    lineas_preparadas: tuple[LineaPreparada, ...]


def crear_clave_producto(
    tipo_fruta: str,
    calibre: int,
) -> tuple[str, int]:
    return normalizar_texto(tipo_fruta), calibre


def describir_clave_producto(
    clave: tuple[str, int],
) -> str:
    tipo_fruta, calibre = clave
    return f"{tipo_fruta.title()}, calibre {calibre}"


def obtener_destinos_despachos(
    resultado: ResultadoMatcher,
) -> tuple[str, ...]:
    destinos = [
        linea.puerto_destino.strip().upper()
        for linea in resultado.lineas
        if linea.puerto_destino.strip()
    ]

    return tuple(dict.fromkeys(destinos))


def validar_liquidacion(
    liquidacion: LiquidacionDimanno,
    despachos: ResultadoMatcher,
) -> ResultadoValidacion:
    errores: list[IncidenciaValidacion] = []
    advertencias: list[IncidenciaValidacion] = []

    precios_liquidacion: dict[
        tuple[str, int],
        Decimal,
    ] = {}

    cajas_liquidacion_por_producto: dict[
        tuple[str, int],
        int,
    ] = defaultdict(int)

    cajas_despachos_por_producto: dict[
        tuple[str, int],
        int,
    ] = defaultdict(int)

    # ---------------------------------------------------------
    # Validación de rubros
    # ---------------------------------------------------------

    if liquidacion.rubros_no_mapeados:
        errores.append(
            IncidenciaValidacion(
                codigo="RUBROS_NO_MAPEADOS",
                nivel="error",
                mensaje=(
                    "La liquidación contiene rubros que todavía "
                    "no tienen una columna asignada en Raw Data."
                ),
                detalles={
                    "rubros": list(
                        liquidacion.rubros_no_mapeados
                    )
                },
            )
        )

    # ---------------------------------------------------------
    # Productos y precios de la liquidación
    # ---------------------------------------------------------

    total_venta_calculado = Decimal("0")

    for producto in liquidacion.productos:
        total_venta_calculado += (
            Decimal(producto.cajas)
            * producto.precio_eur
        )

        if (
            producto.tipo_fruta is None
            or producto.calibre is None
        ):
            errores.append(
                IncidenciaValidacion(
                    codigo="PRODUCTO_NO_RECONOCIDO",
                    nivel="error",
                    mensaje=(
                        "No fue posible identificar el tipo de "
                        "fruta o calibre de una línea."
                    ),
                    detalles={
                        "descripcion": (
                            producto.descripcion_original
                        ),
                        "cajas": producto.cajas,
                        "precio_eur": str(
                            producto.precio_eur
                        ),
                    },
                )
            )
            continue

        clave = crear_clave_producto(
            producto.tipo_fruta,
            producto.calibre,
        )

        if producto.precio_eur <= 0:
            errores.append(
                IncidenciaValidacion(
                    codigo="PRECIO_INVALIDO",
                    nivel="error",
                    mensaje=(
                        "Una línea con cajas liquidadas tiene "
                        "un precio menor o igual a cero."
                    ),
                    detalles={
                        "producto": (
                            describir_clave_producto(clave)
                        ),
                        "precio_eur": str(
                            producto.precio_eur
                        ),
                    },
                )
            )

        if clave in precios_liquidacion:
            precio_anterior = precios_liquidacion[clave]

            if precio_anterior != producto.precio_eur:
                errores.append(
                    IncidenciaValidacion(
                        codigo="PRECIOS_CONFLICTIVOS",
                        nivel="error",
                        mensaje=(
                            "La misma combinación de tipo de "
                            "fruta y calibre tiene precios "
                            "diferentes en la liquidación."
                        ),
                        detalles={
                            "producto": (
                                describir_clave_producto(clave)
                            ),
                            "precio_1": str(precio_anterior),
                            "precio_2": str(
                                producto.precio_eur
                            ),
                        },
                    )
                )
        else:
            precios_liquidacion[clave] = (
                producto.precio_eur
            )

        cajas_liquidacion_por_producto[clave] += (
            producto.cajas
        )

    # ---------------------------------------------------------
    # Total de venta
    # ---------------------------------------------------------

    if (
        total_venta_calculado
        != liquidacion.total_venta_eur
    ):
        errores.append(
            IncidenciaValidacion(
                codigo="TOTAL_VENTA_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "El total de venta informado no coincide "
                    "con la suma de cajas por precio."
                ),
                detalles={
                    "total_informado_eur": str(
                        liquidacion.total_venta_eur
                    ),
                    "total_calculado_eur": str(
                        total_venta_calculado
                    ),
                },
            )
        )

    # ---------------------------------------------------------
    # Productos encontrados en Despachos
    # ---------------------------------------------------------

    for linea in despachos.lineas:
        clave = crear_clave_producto(
            linea.tipo_empaque,
            linea.calibre,
        )

        cajas_despachos_por_producto[clave] += (
            linea.total_cajas
        )

    claves_liquidacion = set(
        cajas_liquidacion_por_producto
    )

    claves_despachos = set(
        cajas_despachos_por_producto
    )

    sin_precio = claves_despachos - claves_liquidacion

    for clave in sorted(sin_precio):
        errores.append(
            IncidenciaValidacion(
                codigo="PRODUCTO_SIN_PRECIO",
                nivel="error",
                mensaje=(
                    "Despachos contiene una combinación de "
                    "tipo de fruta y calibre sin precio en la "
                    "liquidación."
                ),
                detalles={
                    "producto": (
                        describir_clave_producto(clave)
                    ),
                    "cajas_despachos": (
                        cajas_despachos_por_producto[clave]
                    ),
                },
            )
        )

    sin_despacho = claves_liquidacion - claves_despachos

    for clave in sorted(sin_despacho):
        errores.append(
            IncidenciaValidacion(
                codigo="PRODUCTO_SIN_DESPACHO",
                nivel="error",
                mensaje=(
                    "La liquidación contiene una combinación "
                    "de tipo de fruta y calibre que no aparece "
                    "en Despachos."
                ),
                detalles={
                    "producto": (
                        describir_clave_producto(clave)
                    ),
                    "cajas_liquidacion": (
                        cajas_liquidacion_por_producto[clave]
                    ),
                },
            )
        )

    for clave in sorted(
        claves_liquidacion & claves_despachos
    ):
        cajas_liquidacion = (
            cajas_liquidacion_por_producto[clave]
        )

        cajas_despachos = (
            cajas_despachos_por_producto[clave]
        )

        if cajas_liquidacion != cajas_despachos:
            errores.append(
                IncidenciaValidacion(
                    codigo=(
                        "CAJAS_PRODUCTO_NO_COINCIDEN"
                    ),
                    nivel="error",
                    mensaje=(
                        "Las cajas no coinciden para una "
                        "combinación de tipo de fruta y calibre."
                    ),
                    detalles={
                        "producto": (
                            describir_clave_producto(clave)
                        ),
                        "cajas_liquidacion": (
                            cajas_liquidacion
                        ),
                        "cajas_despachos": cajas_despachos,
                    },
                )
            )

    # ---------------------------------------------------------
    # Total general de cajas
    # ---------------------------------------------------------

    if (
        liquidacion.total_cajas
        != despachos.total_cajas
    ):
        errores.append(
            IncidenciaValidacion(
                codigo="TOTAL_CAJAS_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "El total de cajas de la liquidación no "
                    "coincide con el total encontrado en "
                    "Despachos."
                ),
                detalles={
                    "cajas_liquidacion": (
                        liquidacion.total_cajas
                    ),
                    "cajas_despachos": (
                        despachos.total_cajas
                    ),
                },
            )
        )

    # ---------------------------------------------------------
    # Contenedores
    # ---------------------------------------------------------

    contenedores_liquidacion = {
        contenedor.strip().upper()
        for contenedor in liquidacion.contenedores
    }

    contenedores_despachos = {
        contenedor.strip().upper()
        for contenedor in despachos.contenedores
    }

    faltantes_en_despachos = sorted(
        contenedores_liquidacion
        - contenedores_despachos
    )

    adicionales_en_despachos = sorted(
        contenedores_despachos
        - contenedores_liquidacion
    )

    if (
        faltantes_en_despachos
        or adicionales_en_despachos
    ):
        errores.append(
            IncidenciaValidacion(
                codigo="CONTENEDORES_NO_COINCIDEN",
                nivel="error",
                mensaje=(
                    "Los contenedores de la liquidación no "
                    "coinciden con los encontrados en "
                    "Despachos."
                ),
                detalles={
                    "faltantes_en_despachos": (
                        faltantes_en_despachos
                    ),
                    "adicionales_en_despachos": (
                        adicionales_en_despachos
                    ),
                },
            )
        )

    # ---------------------------------------------------------
    # Destino
    # ---------------------------------------------------------

    destino_liquidacion = (
        liquidacion.destino.strip().upper()
    )

    destinos_despachos = obtener_destinos_despachos(
        despachos
    )

    requiere_resolver_destino = False

    if not destino_liquidacion:
        errores.append(
            IncidenciaValidacion(
                codigo="DESTINO_LIQUIDACION_VACIO",
                nivel="error",
                mensaje=(
                    "La liquidación no contiene un destino."
                ),
            )
        )

    if not destinos_despachos:
        errores.append(
            IncidenciaValidacion(
                codigo="DESTINO_DESPACHOS_VACIO",
                nivel="error",
                mensaje=(
                    "Las líneas encontradas en Despachos no "
                    "contienen destino."
                ),
            )
        )

    elif len(destinos_despachos) > 1:
        errores.append(
            IncidenciaValidacion(
                codigo=(
                    "MULTIPLES_DESTINOS_EN_DESPACHOS"
                ),
                nivel="error",
                mensaje=(
                    "La liquidación encontró más de un destino "
                    "en Despachos. El caso debe revisarse antes "
                    "de continuar."
                ),
                detalles={
                    "destinos": list(destinos_despachos)
                },
            )
        )

    elif (
        destino_liquidacion
        and normalizar_texto(destino_liquidacion)
        != normalizar_texto(destinos_despachos[0])
    ):
        requiere_resolver_destino = True

        advertencias.append(
            IncidenciaValidacion(
                codigo="DESTINO_NO_COINCIDE",
                nivel="advertencia",
                mensaje=(
                    "El destino de la liquidación es diferente "
                    "al destino registrado en Despachos."
                ),
                detalles={
                    "destino_liquidacion": (
                        destino_liquidacion
                    ),
                    "destino_despachos": (
                        destinos_despachos[0]
                    ),
                },
            )
        )

    # ---------------------------------------------------------
    # Preparación de líneas con precio
    # ---------------------------------------------------------

    lineas_preparadas: list[LineaPreparada] = []

    for linea in despachos.lineas:
        clave = crear_clave_producto(
            linea.tipo_empaque,
            linea.calibre,
        )

        precio = precios_liquidacion.get(clave)

        if precio is None:
            continue

        lineas_preparadas.append(
            LineaPreparada(
                despacho=linea,
                tipo_fruta=linea.tipo_empaque,
                calibre=linea.calibre,
                precio_venta_eur=precio,
            )
        )

    return ResultadoValidacion(
        es_valido=not errores,
        requiere_resolver_destino=(
            requiere_resolver_destino
        ),
        destino_liquidacion=destino_liquidacion,
        destinos_despachos=destinos_despachos,
        total_cajas_liquidacion=(
            liquidacion.total_cajas
        ),
        total_cajas_despachos=(
            despachos.total_cajas
        ),
        total_venta_informado_eur=(
            liquidacion.total_venta_eur
        ),
        total_venta_calculado_eur=(
            total_venta_calculado
        ),
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_preparadas),
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
            "Valida una liquidación Di Manno contra "
            "el archivo de Despachos."
        )
    )

    parser.add_argument(
        "--liquidacion",
        required=True,
        help="Ruta del archivo de liquidación Di Manno.",
    )

    parser.add_argument(
        "--hoja",
        required=True,
        help="Hoja de liquidación. Ejemplo: FT 5292 W15.",
    )

    parser.add_argument(
        "--despachos",
        required=True,
        help="Ruta del archivo de Despachos.",
    )

    parser.add_argument(
        "--anio",
        required=True,
        type=int,
        help="Año de la liquidación.",
    )

    parser.add_argument(
        "--cliente",
        default="DI MANNO",
        help="Nombre del cliente en Despachos.",
    )

    argumentos = parser.parse_args()

    liquidacion = extraer_liquidacion(
        ruta_archivo=Path(argumentos.liquidacion),
        nombre_hoja=argumentos.hoja,
    )

    despachos = buscar_lineas_despachos(
        ruta_archivo=Path(argumentos.despachos),
        cliente=argumentos.cliente,
        anio=argumentos.anio,
        semana=liquidacion.semana,
        factura_corta=liquidacion.factura_corta,
    )

    resultado = validar_liquidacion(
        liquidacion=liquidacion,
        despachos=despachos,
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