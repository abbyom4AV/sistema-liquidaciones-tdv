from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.dimanno.matcher import normalizar_texto
from services.sifa.extractor import (
    COLUMNAS_GASTO,
    LiquidacionSifa,
)
from services.sifa.matcher import (
    CLIENTE_SIFA_RAW,
    ResultadoMatcherSifa,
)
from services.mensajes_gastos import mensaje_gastos_no_mapeados


NivelIncidencia = Literal["error", "advertencia"]


@dataclass(frozen=True)
class IncidenciaValidacionSifa:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaSifa:
    contenedor: str
    nave: str
    destino: str
    tipo_fruta: str
    carton: str
    calibre: int
    total_cajas: int
    semana: int
    anio: int
    semana_texto: str
    cliente_raw: str
    precio_venta_eur: Decimal
    comision: Decimal
    sin_comision_linea: bool
    gastos: dict[str, Decimal]
    factura_corta: str
    fila_origen: int


@dataclass(frozen=True)
class ResumenContenedorSifa:
    contenedor: str
    total_cajas: int
    total_venta_eur: Decimal
    comision_eur: Decimal


@dataclass(frozen=True)
class ResultadoValidacionSifa:
    es_valido: bool
    destino_ui: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    comision_total: Decimal
    total_venta_eur: Decimal
    errores: tuple[IncidenciaValidacionSifa, ...]
    advertencias: tuple[IncidenciaValidacionSifa, ...]
    lineas_preparadas: tuple[LineaPreparadaSifa, ...]
    resumen_gastos: dict[str, Decimal]
    resumen_contenedores: tuple[ResumenContenedorSifa, ...]
    lineas_con_comision: int
    lineas_sin_comision: int


def validar_liquidacion_sifa(
    liquidacion: LiquidacionSifa,
    despachos: ResultadoMatcherSifa,
    destino_ui: str,
) -> ResultadoValidacionSifa:
    errores: list[IncidenciaValidacionSifa] = []
    advertencias: list[IncidenciaValidacionSifa] = []

    destinos = despachos.destinos
    destino_n = normalizar_texto(destino_ui)
    if destinos and all(
        normalizar_texto(d) != destino_n
        and destino_n not in normalizar_texto(d)
        and normalizar_texto(d) not in destino_n
        for d in destinos
    ):
        advertencias.append(
            IncidenciaValidacionSifa(
                codigo="DESTINO_NO_COINCIDE",
                nivel="advertencia",
                mensaje=(
                    "El destino de la UI no coincide 1:1 con "
                    "los puertos de Despachos."
                ),
                detalles={
                    "destino_ui": destino_ui,
                    "destinos_despachos": list(destinos),
                },
            )
        )

    if liquidacion.rubros_no_mapeados:
        rubros = list(liquidacion.rubros_no_mapeados)
        errores.append(
            IncidenciaValidacionSifa(
                codigo="GASTO_NO_MAPEADO",
                nivel="error",
                mensaje=mensaje_gastos_no_mapeados(rubros),
                detalles={"rubros": rubros},
            )
        )

    suma_gastos = sum(
        liquidacion.gastos.get(col, Decimal("0"))
        for col in COLUMNAS_GASTO
    )

    comision_por_cont: dict[str, Decimal] = {}
    for item in liquidacion.comisiones_contenedor:
        clave = normalizar_texto(item.contenedor).replace(" ", "")
        # Si hay varias filas del mismo contenedor, conservar monto > 0.
        actual = comision_por_cont.get(clave, Decimal("0"))
        if item.monto_eur > actual:
            comision_por_cont[clave] = item.monto_eur
        elif clave not in comision_por_cont:
            comision_por_cont[clave] = item.monto_eur

    comision_total = sum(
        comision_por_cont.values(),
        Decimal("0"),
    )
    if not liquidacion.comisiones_contenedor:
        if all(ln.sin_comision for ln in liquidacion.lineas):
            comision_total = Decimal("0")
        else:
            advertencias.append(
                IncidenciaValidacionSifa(
                    codigo="COMISION_SIN_RESUMEN",
                    nivel="advertencia",
                    mensaje=(
                        "Hay líneas sin 'NO COMMISSION' pero no "
                        "se encontró el resumen de comisión por "
                        "contenedor; se usará 0."
                    ),
                )
            )
            comision_total = Decimal("0")

    if (
        liquidacion.total_costos_excel is not None
        and abs(
            (suma_gastos + comision_total)
            - liquidacion.total_costos_excel
        )
        > Decimal("0.05")
    ):
        advertencias.append(
            IncidenciaValidacionSifa(
                codigo="TOTAL_COSTOS_NO_CALZA",
                nivel="advertencia",
                mensaje=(
                    "TOTAL OF COSTS no calza con "
                    "gastos + comisión total."
                ),
                detalles={
                    "suma_gastos": str(suma_gastos),
                    "comision_total": str(comision_total),
                    "esperado": str(suma_gastos + comision_total),
                    "total_excel": str(
                        liquidacion.total_costos_excel
                    ),
                },
            )
        )

    cont_liq = {
        normalizar_texto(c).replace(" ", "")
        for c in {
            ln.contenedor for ln in liquidacion.lineas
        }
    }
    cont_desp = {
        normalizar_texto(c).replace(" ", "")
        for c in despachos.contenedores
    }
    faltan_en_desp = sorted(cont_liq - cont_desp)
    sobran_en_desp = sorted(cont_desp - cont_liq)
    if faltan_en_desp:
        errores.append(
            IncidenciaValidacionSifa(
                codigo="CONTENEDOR_FALTA_DESPACHOS",
                nivel="error",
                mensaje=(
                    "Hay contenedores en la liquidación que no "
                    "aparecen en Despachos."
                ),
                detalles={"contenedores": faltan_en_desp},
            )
        )
    if sobran_en_desp:
        advertencias.append(
            IncidenciaValidacionSifa(
                codigo="CONTENEDOR_EXTRA_DESPACHOS",
                nivel="advertencia",
                mensaje=(
                    "Hay contenedores en Despachos que no están "
                    "en la liquidación."
                ),
                detalles={"contenedores": sobran_en_desp},
            )
        )

    if liquidacion.total_cajas != despachos.total_cajas:
        errores.append(
            IncidenciaValidacionSifa(
                codigo="TOTAL_CAJAS_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "El total de cajas de la liquidación no "
                    "coincide con Despachos."
                ),
                detalles={
                    "cajas_liquidacion": liquidacion.total_cajas,
                    "cajas_despachos": despachos.total_cajas,
                },
            )
        )

    # Cajas por contenedor.
    cajas_liq_cont: dict[str, int] = defaultdict(int)
    for ln in liquidacion.lineas:
        clave = normalizar_texto(ln.contenedor).replace(" ", "")
        cajas_liq_cont[clave] += ln.total_cajas
    cajas_desp_cont: dict[str, int] = defaultdict(int)
    for ln in despachos.lineas:
        clave = normalizar_texto(ln.contenedor).replace(" ", "")
        cajas_desp_cont[clave] += ln.total_cajas
    for cont, total in sorted(cajas_liq_cont.items()):
        otro = cajas_desp_cont.get(cont)
        if otro is not None and otro != total:
            errores.append(
                IncidenciaValidacionSifa(
                    codigo="CAJAS_CONTENEDOR_NO_COINCIDE",
                    nivel="error",
                    mensaje=(
                        f"Cajas del contenedor {cont} no calzan "
                        f"(liquidación {total} vs Despachos {otro})."
                    ),
                    detalles={
                        "contenedor": cont,
                        "cajas_liquidacion": total,
                        "cajas_despachos": otro,
                    },
                )
            )

    # Nave / meta por contenedor desde Despachos.
    meta_por_cont: dict[str, Any] = {}
    for ln in despachos.lineas:
        clave = normalizar_texto(ln.contenedor).replace(" ", "")
        if clave not in meta_por_cont:
            meta_por_cont[clave] = ln

    gastos = {
        col: liquidacion.gastos.get(col, Decimal("0"))
        for col in COLUMNAS_GASTO
    }

    nave_global = (
        despachos.naves[0]
        if len(despachos.naves) == 1
        else ""
    )
    if len(despachos.naves) > 1:
        advertencias.append(
            IncidenciaValidacionSifa(
                codigo="MULTIPLES_NAVES",
                nivel="advertencia",
                mensaje=(
                    "Despachos tiene más de una nave para el "
                    "filtro; se usará la nave por contenedor."
                ),
                detalles={"naves": list(despachos.naves)},
            )
        )

    lineas_prep: list[LineaPreparadaSifa] = []
    con_com = 0
    sin_com = 0
    destino_escrito = (
        despachos.lineas[0].puerto_destino
        if despachos.lineas
        else destino_ui
    )

    for ln in liquidacion.lineas:
        clave = normalizar_texto(ln.contenedor).replace(" ", "")
        meta = meta_por_cont.get(clave)
        if meta is None:
            # Ya hay error de contenedor; saltar armado.
            continue
        if ln.sin_comision:
            sin_com += 1
            comision_linea = Decimal("0")
        else:
            con_com += 1
            # Total de comisión (suma de contenedores) en cada
            # línea que sí lleva comisión.
            comision_linea = comision_total

        lineas_prep.append(
            LineaPreparadaSifa(
                contenedor=ln.contenedor,
                nave=meta.barco or nave_global,
                destino=meta.puerto_destino or destino_escrito,
                tipo_fruta=ln.tipo_fruta,
                carton=ln.carton,
                calibre=ln.calibre,
                total_cajas=ln.total_cajas,
                semana=despachos.semana,
                anio=despachos.anio,
                semana_texto=despachos.semana_texto,
                cliente_raw=CLIENTE_SIFA_RAW,
                precio_venta_eur=ln.precio_eur,
                comision=comision_linea,
                sin_comision_linea=ln.sin_comision,
                gastos=dict(gastos),
                factura_corta=meta.factura_corta,
                fila_origen=ln.fila_excel,
            )
        )

    if not lineas_prep and not errores:
        errores.append(
            IncidenciaValidacionSifa(
                codigo="SIN_LINEAS_PREPARADAS",
                nivel="error",
                mensaje="No se pudieron preparar líneas para escribir.",
            )
        )

    es_valido = not errores and bool(lineas_prep)

    resumen_contenedores: list[ResumenContenedorSifa] = []
    if liquidacion.totales_contenedor:
        for total in liquidacion.totales_contenedor:
            clave = normalizar_texto(total.contenedor).replace(
                " ", ""
            )
            resumen_contenedores.append(
                ResumenContenedorSifa(
                    contenedor=total.contenedor,
                    total_cajas=total.total_cajas,
                    total_venta_eur=total.total_venta_eur,
                    comision_eur=comision_por_cont.get(
                        clave,
                        Decimal("0"),
                    ),
                )
            )
    else:
        # Fallback por si no hubo filas TOTAL.
        cajas_tmp: dict[str, int] = defaultdict(int)
        venta_tmp: dict[str, Decimal] = defaultdict(
            lambda: Decimal("0")
        )
        for ln in liquidacion.lineas:
            cajas_tmp[ln.contenedor] += ln.total_cajas
            venta_tmp[ln.contenedor] += ln.amount_eur
        for cont, cajas in cajas_tmp.items():
            clave = normalizar_texto(cont).replace(" ", "")
            resumen_contenedores.append(
                ResumenContenedorSifa(
                    contenedor=cont,
                    total_cajas=cajas,
                    total_venta_eur=venta_tmp[cont],
                    comision_eur=comision_por_cont.get(
                        clave,
                        Decimal("0"),
                    ),
                )
            )

    return ResultadoValidacionSifa(
        es_valido=es_valido,
        destino_ui=destino_ui,
        destinos_despachos=destinos,
        total_cajas_liquidacion=liquidacion.total_cajas,
        total_cajas_despachos=despachos.total_cajas,
        comision_total=comision_total,
        total_venta_eur=liquidacion.total_venta_eur,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_prep),
        resumen_gastos=gastos,
        resumen_contenedores=tuple(resumen_contenedores),
        lineas_con_comision=con_com,
        lineas_sin_comision=sin_com,
    )
