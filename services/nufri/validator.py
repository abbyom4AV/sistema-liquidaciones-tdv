from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.dimanno.matcher import normalizar_texto
from services.nufri.extractor import (
    COLUMNAS_GASTO,
    LiquidacionNufri,
    es_caja_vertical_despacho,
)
from services.nufri.matcher import (
    LineaDespachoNufri,
    ResultadoMatcherNufri,
)
from services.mensajes_gastos import (
    etiquetas_rubros,
    mensaje_gastos_no_mapeados,
)

NivelIncidencia = Literal["error", "advertencia"]
TIPO_FRUTA_NUFRI = "Especial"


@dataclass(frozen=True)
class IncidenciaValidacionNufri:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaNufri:
    despacho: LineaDespachoNufri
    tipo_fruta: str
    calibre: int
    precio_venta_eur: Decimal
    gastos: dict[str, Decimal]
    comision_eur: Decimal


@dataclass(frozen=True)
class ResultadoValidacionNufri:
    es_valido: bool
    destino_final: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    total_venta_eur: Decimal
    total_gastos_eur: Decimal
    comision_eur: Decimal
    comision_pct: Decimal | None
    errores: tuple[IncidenciaValidacionNufri, ...]
    advertencias: tuple[IncidenciaValidacionNufri, ...]
    lineas_preparadas: tuple[LineaPreparadaNufri, ...]
    resumen_gastos: dict[str, Decimal]


def _normalizar_contenedor(valor: str) -> str:
    return normalizar_texto(valor).replace(" ", "")


def _destino_coincide(a: str, b: str) -> bool:
    na = normalizar_texto(a)
    nb = normalizar_texto(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _clave_grupo(calibre: int, es_vertical: bool) -> tuple[int, bool]:
    return (calibre, es_vertical)


def _etiqueta_grupo(calibre: int, es_vertical: bool) -> str:
    tipo = "vertical (CRV)" if es_vertical else "no vertical"
    return f"calibre {calibre} {tipo}"


def validar_liquidacion_nufri(
    liquidacion: LiquidacionNufri,
    despachos: ResultadoMatcherNufri,
    *,
    destino_ui: str = "",
    factura_ui: str = "",
    semana_ui: int | None = None,
    anio_ui: int | None = None,
) -> ResultadoValidacionNufri:
    errores: list[IncidenciaValidacionNufri] = []
    advertencias: list[IncidenciaValidacionNufri] = []

    if semana_ui is not None and despachos.semana != int(semana_ui):
        errores.append(
            IncidenciaValidacionNufri(
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
            IncidenciaValidacionNufri(
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
            IncidenciaValidacionNufri(
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

    # Agrupar PDF por (calibre, vertical/CRV).
    # Precio/caja = importe_total_grupo / cajas_total_grupo;
    # ese precio se escribe en cada fila Despachos del grupo.
    cajas_por_grupo_pdf: dict[tuple[int, bool], int] = defaultdict(int)
    importe_por_grupo_pdf: dict[tuple[int, bool], Decimal] = defaultdict(
        Decimal
    )
    for producto in liquidacion.productos:
        clave = _clave_grupo(producto.calibre, producto.es_vertical)
        cajas_por_grupo_pdf[clave] += producto.bultos
        importe_por_grupo_pdf[clave] += producto.importe_eur

    precio_por_grupo_pdf: dict[tuple[int, bool], Decimal] = {}
    for clave, cajas_grupo in cajas_por_grupo_pdf.items():
        if cajas_grupo <= 0:
            continue
        precio_por_grupo_pdf[clave] = (
            importe_por_grupo_pdf[clave] / Decimal(cajas_grupo)
        )

    resumen_gastos: dict[str, Decimal] = {
        col: liquidacion.gastos.get(col, Decimal("0"))
        for col in COLUMNAS_GASTO
    }
    total_gastos = sum(
        resumen_gastos.values(),
        Decimal("0"),
    )

    if liquidacion.total_cajas != despachos.total_cajas:
        advertencias.append(
            IncidenciaValidacionNufri(
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
    if contenedores_pdf and contenedores_desp:
        if not contenedores_pdf.intersection(contenedores_desp):
            advertencias.append(
                IncidenciaValidacionNufri(
                    codigo="CONTENEDORES_DIFERENTES",
                    nivel="advertencia",
                    mensaje=(
                        "Los contenedores del PDF no coinciden "
                        "con Despachos."
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
            IncidenciaValidacionNufri(
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
            IncidenciaValidacionNufri(
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
            IncidenciaValidacionNufri(
                codigo="DESTINO_UI_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El destino de la UI no coincide con "
                    "los puertos de Despachos."
                ),
                detalles={
                    "destino_ui": destino_form,
                    "destinos_despachos": list(despachos.destinos),
                },
            )
        )

    if liquidacion.destino_pdf and destino_final:
        if not _destino_coincide(
            liquidacion.destino_pdf,
            destino_final,
        ):
            advertencias.append(
                IncidenciaValidacionNufri(
                    codigo="DESTINO_PDF_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        "El destino del PDF difiere del "
                        "indicado en Despachos/UI."
                    ),
                    detalles={
                        "destino_pdf": liquidacion.destino_pdf,
                        "destino_final": destino_final,
                    },
                )
            )

    cajas_por_grupo_desp: dict[tuple[int, bool], int] = defaultdict(
        int
    )
    for linea in despachos.lineas:
        clave = _clave_grupo(
            linea.calibre,
            es_caja_vertical_despacho(linea.carton),
        )
        cajas_por_grupo_desp[clave] += linea.total_cajas

    lineas_preparadas: list[LineaPreparadaNufri] = []
    for linea in despachos.lineas:
        es_vertical = es_caja_vertical_despacho(linea.carton)
        clave = _clave_grupo(linea.calibre, es_vertical)
        precio = precio_por_grupo_pdf.get(clave)
        if precio is None:
            errores.append(
                IncidenciaValidacionNufri(
                    codigo="IMPORTE_NO_ENCONTRADO",
                    nivel="error",
                    mensaje=(
                        "No hay importe en el PDF para "
                        f"{_etiqueta_grupo(*clave)}."
                    ),
                    detalles={
                        "calibre": linea.calibre,
                        "es_vertical": es_vertical,
                        "carton": linea.carton,
                        "cajas": linea.total_cajas,
                    },
                )
            )
            precio = Decimal("0")

        lineas_preparadas.append(
            LineaPreparadaNufri(
                despacho=linea,
                tipo_fruta=TIPO_FRUTA_NUFRI,
                calibre=linea.calibre,
                precio_venta_eur=precio,
                gastos=dict(resumen_gastos),
                comision_eur=liquidacion.comision_eur,
            )
        )

    for clave in sorted(
        set(cajas_por_grupo_pdf) | set(cajas_por_grupo_desp)
    ):
        pdf_c = cajas_por_grupo_pdf.get(clave, 0)
        desp_c = cajas_por_grupo_desp.get(clave, 0)
        if pdf_c != desp_c:
            calibre, es_vertical = clave
            advertencias.append(
                IncidenciaValidacionNufri(
                    codigo="CAJAS_CALIBRE_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        "Cajas PDF vs Despachos difieren para "
                        f"{_etiqueta_grupo(*clave)}: "
                        f"PDF={pdf_c}, Despachos={desp_c}."
                    ),
                    detalles={
                        "calibre": calibre,
                        "es_vertical": es_vertical,
                        "cajas_pdf": pdf_c,
                        "cajas_despachos": desp_c,
                    },
                )
            )

    if not lineas_preparadas and not errores:
        errores.append(
            IncidenciaValidacionNufri(
                codigo="SIN_LINEAS_PREPARADAS",
                nivel="error",
                mensaje="No quedó ninguna línea para escribir.",
            )
        )

    return ResultadoValidacionNufri(
        es_valido=not errores,
        destino_final=destino_final,
        destinos_despachos=despachos.destinos,
        total_cajas_liquidacion=liquidacion.total_cajas,
        total_cajas_despachos=despachos.total_cajas,
        total_venta_eur=liquidacion.total_venta_eur,
        total_gastos_eur=total_gastos,
        comision_eur=liquidacion.comision_eur,
        comision_pct=liquidacion.comision_pct,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_preparadas),
        resumen_gastos=resumen_gastos,
    )
