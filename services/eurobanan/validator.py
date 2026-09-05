from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.dimanno.matcher import normalizar_texto
from services.eurobanan.extractor import (
    COLUMNAS_GASTO,
    FamiliaProductoEurobanan,
    LiquidacionEurobanan,
    familia_desde_despacho,
    tipo_fruta_digitada,
)
from services.eurobanan.matcher import (
    LineaDespachoEurobanan,
    ResultadoMatcherEurobanan,
)
from services.mensajes_gastos import (
    etiquetas_rubros,
    mensaje_gastos_no_mapeados,
)

NivelIncidencia = Literal["error", "advertencia"]


@dataclass(frozen=True)
class IncidenciaValidacionEurobanan:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaEurobanan:
    despacho: LineaDespachoEurobanan
    tipo_fruta: str
    calibre: int
    precio_venta_eur: Decimal
    gastos: dict[str, Decimal]
    comision_eur: Decimal


@dataclass(frozen=True)
class ResultadoValidacionEurobanan:
    es_valido: bool
    destino_final: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    total_venta_eur: Decimal
    total_gastos_eur: Decimal
    comision_eur: Decimal
    comision_pct: Decimal | None
    errores: tuple[IncidenciaValidacionEurobanan, ...]
    advertencias: tuple[IncidenciaValidacionEurobanan, ...]
    lineas_preparadas: tuple[LineaPreparadaEurobanan, ...]
    resumen_gastos: dict[str, Decimal]


def _normalizar_contenedor(valor: str) -> str:
    return normalizar_texto(valor).replace(" ", "")


def _destino_coincide(a: str, b: str) -> bool:
    na = normalizar_texto(a)
    nb = normalizar_texto(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _clave_precio(
    familia: FamiliaProductoEurobanan,
    calibre: int,
) -> tuple[FamiliaProductoEurobanan, int]:
    return (familia, calibre)


def validar_liquidacion_eurobanan(
    liquidacion: LiquidacionEurobanan,
    despachos: ResultadoMatcherEurobanan,
    *,
    destino_ui: str = "",
    factura_ui: str = "",
    semana_ui: int | None = None,
    anio_ui: int | None = None,
) -> ResultadoValidacionEurobanan:
    errores: list[IncidenciaValidacionEurobanan] = []
    advertencias: list[IncidenciaValidacionEurobanan] = []

    factura = (liquidacion.factura_corta or "").strip()
    factura_form = (factura_ui or "").strip()
    if not (factura.isdigit() and len(factura) == 4):
        advertencias.append(
            IncidenciaValidacionEurobanan(
                codigo="FACTURA_PDF_NO_CUATRO_DIGITOS",
                nivel="advertencia",
                mensaje=(
                    "La factura del PDF no tiene 4 dígitos; "
                    "use la factura de Despachos en la UI."
                ),
                detalles={"factura_pdf": factura},
            )
        )
    elif (
        factura_form
        and factura != factura_form
        and factura_form != despachos.factura_corta_buscada
    ):
        advertencias.append(
            IncidenciaValidacionEurobanan(
                codigo="FACTURA_PDF_DIFERENTE_UI",
                nivel="advertencia",
                mensaje=(
                    "La factura del PDF difiere de la indicada; "
                    "se usa la factura de Despachos."
                ),
                detalles={
                    "factura_ui": factura_form,
                    "factura_pdf": factura,
                    "factura_despachos": (
                        despachos.factura_corta_buscada
                    ),
                },
            )
        )

    if semana_ui is not None and despachos.semana != int(semana_ui):
        errores.append(
            IncidenciaValidacionEurobanan(
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
            IncidenciaValidacionEurobanan(
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
            IncidenciaValidacionEurobanan(
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

    # Precio por familia + calibre (el PDF consolida cajas;
    # Despachos las parte por contenedor/cartón).
    precios: dict[
        tuple[FamiliaProductoEurobanan, int],
        Decimal,
    ] = {}
    cajas_pdf_familia: dict[
        tuple[FamiliaProductoEurobanan, int],
        int,
    ] = defaultdict(int)
    for producto in liquidacion.productos:
        clave = _clave_precio(producto.familia, producto.calibre)
        cajas_pdf_familia[clave] += producto.bultos
        previo = precios.get(clave)
        if previo is not None and previo != producto.precio_eur:
            errores.append(
                IncidenciaValidacionEurobanan(
                    codigo="PRECIOS_CONFLICTIVOS",
                    nivel="error",
                    mensaje=(
                        "Hay precios distintos en el PDF para la "
                        "misma familia y calibre."
                    ),
                    detalles={
                        "familia": producto.familia,
                        "calibre": producto.calibre,
                    },
                )
            )
        else:
            precios[clave] = producto.precio_eur

    venta_calculada = sum(
        (p.importe_eur for p in liquidacion.productos),
        Decimal("0"),
    )
    if (
        liquidacion.total_suma_pdf is not None
        and venta_calculada != liquidacion.total_suma_pdf
    ):
        errores.append(
            IncidenciaValidacionEurobanan(
                codigo="TOTAL_SUMA_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "TOTAL SUMA del PDF no coincide con la "
                    "suma de importes por línea."
                ),
                detalles={
                    "total_suma_pdf": str(
                        liquidacion.total_suma_pdf
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

    if liquidacion.total_cajas != despachos.total_cajas:
        advertencias.append(
            IncidenciaValidacionEurobanan(
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
                IncidenciaValidacionEurobanan(
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
            IncidenciaValidacionEurobanan(
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
            IncidenciaValidacionEurobanan(
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
            IncidenciaValidacionEurobanan(
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

    cajas_desp_familia: dict[
        tuple[FamiliaProductoEurobanan, int],
        int,
    ] = defaultdict(int)
    lineas_preparadas: list[LineaPreparadaEurobanan] = []
    for linea in despachos.lineas:
        familia = familia_desde_despacho(
            linea.tipo_empaque,
            linea.carton,
        )
        clave = _clave_precio(familia, linea.calibre)
        cajas_desp_familia[clave] += linea.total_cajas
        precio = precios.get(clave)
        if precio is None:
            errores.append(
                IncidenciaValidacionEurobanan(
                    codigo="PRECIO_NO_ENCONTRADO",
                    nivel="error",
                    mensaje=(
                        "No hay precio en el PDF para familia "
                        f"{familia} y calibre {linea.calibre}."
                    ),
                    detalles={
                        "contenedor": linea.contenedor,
                        "carton": linea.carton,
                        "tipo_empaque": linea.tipo_empaque,
                        "familia": familia,
                        "calibre": linea.calibre,
                        "cajas": linea.total_cajas,
                    },
                )
            )
            precio = Decimal("0")

        lineas_preparadas.append(
            LineaPreparadaEurobanan(
                despacho=linea,
                tipo_fruta=tipo_fruta_digitada(
                    linea.tipo_empaque
                ),
                calibre=linea.calibre,
                precio_venta_eur=precio,
                gastos=dict(resumen_gastos),
                comision_eur=liquidacion.comision_eur,
            )
        )

    for clave in sorted(
        set(cajas_pdf_familia) | set(cajas_desp_familia)
    ):
        pdf_cajas = cajas_pdf_familia.get(clave, 0)
        desp_cajas = cajas_desp_familia.get(clave, 0)
        if pdf_cajas != desp_cajas:
            familia, calibre = clave
            advertencias.append(
                IncidenciaValidacionEurobanan(
                    codigo="CAJAS_FAMILIA_CALIBRE_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        f"Cajas PDF vs Despachos difieren para "
                        f"{familia} calibre {calibre}."
                    ),
                    detalles={
                        "familia": familia,
                        "calibre": calibre,
                        "cajas_pdf": pdf_cajas,
                        "cajas_despachos": desp_cajas,
                    },
                )
            )

    if not lineas_preparadas and not errores:
        errores.append(
            IncidenciaValidacionEurobanan(
                codigo="SIN_LINEAS_PREPARADAS",
                nivel="error",
                mensaje="No quedó ninguna línea para escribir.",
            )
        )

    return ResultadoValidacionEurobanan(
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
