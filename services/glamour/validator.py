from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.dimanno.matcher import normalizar_texto
from services.glamour.extractor import (
    COLUMNAS_GASTO,
    LiquidacionGlamour,
)
from services.glamour.matcher import (
    LineaDespachoGlamour,
    ResultadoMatcherGlamour,
)
from services.mensajes_gastos import (
    etiquetas_rubros,
    mensaje_gastos_no_mapeados,
)


NivelIncidencia = Literal["error", "advertencia"]
TIPO_FRUTA_GLAMOUR = "ESPECIAL"


@dataclass(frozen=True)
class IncidenciaValidacionGlamour:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaGlamour:
    despacho: LineaDespachoGlamour
    tipo_fruta: str
    calibre: int
    precio_venta_eur: Decimal
    gastos: dict[str, Decimal]


@dataclass(frozen=True)
class ResultadoValidacionGlamour:
    es_valido: bool
    destino_final: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    total_venta_eur: Decimal
    total_gastos_eur: Decimal
    errores: tuple[IncidenciaValidacionGlamour, ...]
    advertencias: tuple[IncidenciaValidacionGlamour, ...]
    lineas_preparadas: tuple[LineaPreparadaGlamour, ...]
    resumen_gastos: dict[str, Decimal]


def _normalizar_contenedor(valor: str) -> str:
    return normalizar_texto(valor).replace(" ", "")


def _destino_coincide(a: str, b: str) -> bool:
    na = normalizar_texto(a)
    nb = normalizar_texto(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def validar_liquidacion_glamour(
    liquidacion: LiquidacionGlamour,
    despachos: ResultadoMatcherGlamour,
    *,
    destino_ui: str = "",
    factura_ui: str = "",
    semana_ui: int | None = None,
    anio_ui: int | None = None,
) -> ResultadoValidacionGlamour:
    errores: list[IncidenciaValidacionGlamour] = []
    advertencias: list[IncidenciaValidacionGlamour] = []

    factura = (liquidacion.factura_corta or "").strip()
    factura_form = (factura_ui or "").strip()
    if not (factura.isdigit() and len(factura) == 4):
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="FACTURA_CORTA_INVALIDA",
                nivel="error",
                mensaje=(
                    "La factura del PDF no tiene exactamente "
                    "4 dígitos finales."
                ),
                detalles={"factura_corta": factura},
            )
        )
    elif factura_form and factura != factura_form:
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="FACTURA_UI_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "La factura indicada no coincide con "
                    "la del PDF."
                ),
                detalles={
                    "factura_ui": factura_form,
                    "factura_pdf": factura,
                },
            )
        )

    if semana_ui is not None and despachos.semana != int(semana_ui):
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="SEMANA_UI_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "La semana indicada no coincide con "
                    "Despachos filtrados."
                ),
                detalles={
                    "semana_ui": semana_ui,
                    "semana_despachos": despachos.semana,
                },
            )
        )
    if anio_ui is not None and despachos.anio != int(anio_ui):
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="ANIO_UI_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "El año indicado no coincide con "
                    "Despachos filtrados."
                ),
                detalles={
                    "anio_ui": anio_ui,
                    "anio_despachos": despachos.anio,
                },
            )
        )

    if liquidacion.rubros_no_mapeados:
        rubros = etiquetas_rubros(liquidacion.rubros_no_mapeados)
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="RUBROS_NO_MAPEADOS",
                nivel="error",
                mensaje=mensaje_gastos_no_mapeados(rubros),
                detalles={
                    "rubros": [
                        {
                            "etiqueta": etiqueta,
                            "monto": str(monto),
                        }
                        for etiqueta, monto in (
                            liquidacion.rubros_no_mapeados
                        )
                    ]
                },
            )
        )

    precios: dict[int, Decimal] = {}
    cajas_pdf: dict[int, int] = defaultdict(int)
    venta_calculada = Decimal("0")

    for producto in liquidacion.productos:
        venta_calculada += producto.importe_eur
        cajas_pdf[producto.calibre] += producto.bultos
        if producto.precio_eur <= 0:
            errores.append(
                IncidenciaValidacionGlamour(
                    codigo="PRECIO_INVALIDO",
                    nivel="error",
                    mensaje=(
                        f"Precio inválido en calibre "
                        f"{producto.calibre}."
                    ),
                    detalles={
                        "calibre": producto.calibre,
                        "precio_eur": str(producto.precio_eur),
                    },
                )
            )
        previo = precios.get(producto.calibre)
        if previo is not None and previo != producto.precio_eur:
            errores.append(
                IncidenciaValidacionGlamour(
                    codigo="PRECIOS_CONFLICTIVOS",
                    nivel="error",
                    mensaje=(
                        f"El calibre {producto.calibre} tiene "
                        "precios distintos en el PDF."
                    ),
                    detalles={"calibre": producto.calibre},
                )
            )
        else:
            precios[producto.calibre] = producto.precio_eur

    if venta_calculada != liquidacion.total_venta_eur:
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="TOTAL_VENTA_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "El total de venta del PDF no coincide "
                    "con la suma de importes por línea."
                ),
                detalles={
                    "total_informado_eur": str(
                        liquidacion.total_venta_eur
                    ),
                    "total_calculado_eur": str(venta_calculada),
                },
            )
        )

    resumen_gastos: dict[str, Decimal] = {
        col: liquidacion.gastos.get(col, Decimal("0"))
        for col in COLUMNAS_GASTO
    }
    total_gastos = sum(
        resumen_gastos.values(),
        Decimal("0"),
    )

    if liquidacion.total_importe_neto_eur is not None:
        esperado = liquidacion.total_venta_eur - total_gastos
        if esperado != liquidacion.total_importe_neto_eur:
            advertencias.append(
                IncidenciaValidacionGlamour(
                    codigo="TOTAL_NETO_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        "TOTAL IMPORTE (EUROS) no cuadra con "
                        "venta menos gastos mapeados."
                    ),
                    detalles={
                        "total_neto_pdf": str(
                            liquidacion.total_importe_neto_eur
                        ),
                        "venta_menos_gastos": str(esperado),
                        "total_gastos_eur": str(total_gastos),
                    },
                )
            )

    cajas_desp: dict[int, int] = defaultdict(int)
    for linea in despachos.lineas:
        cajas_desp[linea.calibre] += linea.total_cajas

    if liquidacion.total_cajas != despachos.total_cajas:
        advertencias.append(
            IncidenciaValidacionGlamour(
                codigo="TOTAL_CAJAS_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El total de cajas del PDF no coincide "
                    "con Despachos."
                ),
                detalles={
                    "cajas_pdf": liquidacion.total_cajas,
                    "cajas_despachos": despachos.total_cajas,
                },
            )
        )

    for calibre in sorted(set(cajas_pdf) | set(cajas_desp)):
        if cajas_pdf.get(calibre, 0) != cajas_desp.get(
            calibre, 0
        ):
            advertencias.append(
                IncidenciaValidacionGlamour(
                    codigo="CAJAS_POR_CALIBRE_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        f"Diferencia de cajas en calibre "
                        f"{calibre} entre PDF y Despachos."
                    ),
                    detalles={
                        "calibre": calibre,
                        "cajas_pdf": cajas_pdf.get(calibre, 0),
                        "cajas_despachos": cajas_desp.get(
                            calibre, 0
                        ),
                    },
                )
            )

    contenedores_pdf = {
        _normalizar_contenedor(c)
        for c in liquidacion.contenedores
        if c
    }
    contenedores_desp = {
        _normalizar_contenedor(c)
        for c in despachos.contenedores
        if c
    }
    if contenedores_pdf and contenedores_pdf != contenedores_desp:
        advertencias.append(
            IncidenciaValidacionGlamour(
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

    destino_form = (destino_ui or "").strip().upper()
    if not despachos.destinos:
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="SIN_DESTINO_DESPACHOS",
                nivel="error",
                mensaje=(
                    "Las líneas de Despachos no tienen "
                    "puerto destino."
                ),
            )
        )
        destino_final = destino_form
    elif len(despachos.destinos) > 1:
        advertencias.append(
            IncidenciaValidacionGlamour(
                codigo="DESTINOS_MULTIPLES",
                nivel="advertencia",
                mensaje=(
                    "Hay más de un destino en Despachos; "
                    "se usará el de la UI."
                ),
                detalles={"destinos": list(despachos.destinos)},
            )
        )
        destino_final = destino_form or despachos.destinos[0]
    else:
        destino_final = destino_form or despachos.destinos[0]

    if destino_form and despachos.destinos and all(
        not _destino_coincide(d, destino_form)
        for d in despachos.destinos
    ):
        advertencias.append(
            IncidenciaValidacionGlamour(
                codigo="DESTINO_UI_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El destino de la UI no coincide 1:1 con "
                    "los puertos de Despachos."
                ),
                detalles={
                    "destino_ui": destino_form,
                    "destinos_despachos": list(despachos.destinos),
                },
            )
        )

    if (
        liquidacion.destino_pdf
        and destino_final
        and not _destino_coincide(
            liquidacion.destino_pdf,
            destino_final,
        )
    ):
        advertencias.append(
            IncidenciaValidacionGlamour(
                codigo="DESTINO_PDF_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El destino del PDF no coincide con "
                    "el indicado / Despachos."
                ),
                detalles={
                    "destino_pdf": liquidacion.destino_pdf,
                    "destino_final": destino_final,
                },
            )
        )

    lineas_preparadas: list[LineaPreparadaGlamour] = []
    for linea in despachos.lineas:
        precio = precios.get(linea.calibre)
        if precio is None:
            errores.append(
                IncidenciaValidacionGlamour(
                    codigo="PRECIO_NO_ENCONTRADO",
                    nivel="error",
                    mensaje=(
                        f"No hay precio en el PDF para "
                        f"calibre {linea.calibre}."
                    ),
                    detalles={
                        "contenedor": linea.contenedor,
                        "calibre": linea.calibre,
                        "carton": linea.carton,
                    },
                )
            )
            precio = Decimal("0")

        lineas_preparadas.append(
            LineaPreparadaGlamour(
                despacho=linea,
                tipo_fruta=TIPO_FRUTA_GLAMOUR,
                calibre=linea.calibre,
                precio_venta_eur=precio,
                gastos=dict(resumen_gastos),
            )
        )

    if not lineas_preparadas and not errores:
        errores.append(
            IncidenciaValidacionGlamour(
                codigo="SIN_LINEAS_PREPARADAS",
                nivel="error",
                mensaje="No quedó ninguna línea para escribir.",
            )
        )

    return ResultadoValidacionGlamour(
        es_valido=not errores,
        destino_final=destino_final,
        destinos_despachos=despachos.destinos,
        total_cajas_liquidacion=liquidacion.total_cajas,
        total_cajas_despachos=despachos.total_cajas,
        total_venta_eur=liquidacion.total_venta_eur,
        total_gastos_eur=total_gastos,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_preparadas),
        resumen_gastos=resumen_gastos,
    )
