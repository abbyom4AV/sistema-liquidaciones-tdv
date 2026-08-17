from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.fruver.extractor import (
    LiquidacionFruver,
    formatear_destino_excel,
    normalizar_texto,
)
from services.fruver.matcher import (
    LineaDespachoFruver,
    ResultadoMatcherFruver,
)


NivelIncidencia = Literal["error", "advertencia"]
TOLERANCIA_VENTA = Decimal("0.05")
TOLERANCIA_GASTOS = Decimal("0.05")


@dataclass(frozen=True)
class IncidenciaValidacionFruver:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaFruver:
    semana: int
    anio: int
    semana_texto: str
    cliente: str
    nave: str
    contenedor: str
    destino: str
    tipo_fruta: str
    calibre: int
    total_cajas: Decimal
    carton: str
    demora_eur: Decimal
    portes_eur: Decimal
    gasto_puerto_eur: Decimal
    aduanas_eur: Decimal
    otros3: Decimal
    comision: Decimal
    precio_venta_eur: Decimal


@dataclass(frozen=True)
class ResumenGastosContenedor:
    contenedor: str
    factura_corta: str
    comision: Decimal
    gastos: dict[str, Decimal]
    total_cajas_pdf: Decimal
    total_cajas_despachos: Decimal
    total_venta_pdf: Decimal
    total_venta_calc: Decimal
    total_gastos_pdf: Decimal
    total_gastos_calc: Decimal
    venta_cuadra: bool
    gastos_cuadran: bool
    flete_eur: Decimal


@dataclass(frozen=True)
class ResultadoValidacionFruver:
    es_valido: bool
    destino_ui: str
    factura_corta: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: Decimal
    total_cajas_despachos: Decimal
    errores: tuple[IncidenciaValidacionFruver, ...]
    advertencias: tuple[IncidenciaValidacionFruver, ...]
    lineas_preparadas: tuple[LineaPreparadaFruver, ...]
    resumen_gastos_contenedores: tuple[ResumenGastosContenedor, ...]


def _clave_cont(valor: str) -> str:
    return normalizar_texto(valor).replace(" ", "")


def _es_especial(tipo_empaque: str, carton: str) -> bool:
    t = normalizar_texto(tipo_empaque)
    if t:
        return "ESPECIAL" in t
    c = normalizar_texto(carton)
    return (not c) or ("ESPECIAL" in c)


def validar_liquidaciones_fruver(
    liquidaciones: tuple[LiquidacionFruver, ...],
    despachos: ResultadoMatcherFruver,
    destino_ui: str,
    factura_ui: str,
) -> ResultadoValidacionFruver:
    errores: list[IncidenciaValidacionFruver] = []
    advertencias: list[IncidenciaValidacionFruver] = []
    factura_objetivo = str(factura_ui or "").strip()

    if not liquidaciones:
        errores.append(
            IncidenciaValidacionFruver(
                codigo="SIN_PDFS",
                nivel="error",
                mensaje="No se cargaron PDFs de liquidación.",
            )
        )

    por_contenedor: dict[str, LiquidacionFruver] = {}
    for liq in liquidaciones:
        clave = _clave_cont(liq.contenedor)
        if clave in por_contenedor:
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="CONTENEDOR_PDF_DUPLICADO",
                    nivel="error",
                    mensaje=(
                        f"Hay más de un PDF para el contenedor "
                        f"{liq.contenedor}."
                    ),
                )
            )
            continue
        por_contenedor[clave] = liq
        if (
            factura_objetivo
            and liq.factura_corta != factura_objetivo
        ):
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="FACTURA_PDF_NO_COINCIDE",
                    nivel="error",
                    mensaje=(
                        f"El PDF de {liq.contenedor} tiene factura "
                        f"{liq.factura_corta}, se indicó "
                        f"{factura_objetivo}."
                    ),
                    detalles={
                        "contenedor": liq.contenedor,
                        "factura_pdf": liq.factura_corta,
                        "factura_ui": factura_objetivo,
                    },
                )
            )
        if liq.flete_eur > 0:
            advertencias.append(
                IncidenciaValidacionFruver(
                    codigo="FLETE_CON_MONTO",
                    nivel="advertencia",
                    mensaje=(
                        f"El PDF de {liq.contenedor} trae Flete "
                        f"{liq.flete_eur} € (no se digita)."
                    ),
                    detalles={
                        "contenedor": liq.contenedor,
                        "flete_eur": str(liq.flete_eur),
                    },
                )
            )
        for rubro in liq.rubros_no_mapeados:
            advertencias.append(
                IncidenciaValidacionFruver(
                    codigo="GASTO_NO_MAPEADO",
                    nivel="advertencia",
                    mensaje=(
                        f"Rubro de gasto sin columna digitada en "
                        f"{liq.contenedor}: {rubro}"
                    ),
                )
            )

    destinos = despachos.destinos
    lineas_prep: list[LineaPreparadaFruver] = []
    resumen: list[ResumenGastosContenedor] = []
    destino_excel = formatear_destino_excel(
        destino_ui or (destinos[0] if destinos else "")
    )

    cajas_desp_cont: dict[str, int] = defaultdict(int)
    for linea in despachos.lineas:
        cajas_desp_cont[_clave_cont(linea.contenedor)] += (
            linea.total_cajas
        )

    conts_desp = {
        _clave_cont(c) for c in despachos.contenedores
    }
    for clave, liq in por_contenedor.items():
        if clave not in conts_desp:
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="PDF_SIN_DESPACHO",
                    nivel="error",
                    mensaje=(
                        f"El contenedor {liq.contenedor} del PDF "
                        "no está en Despachos para semana/puerto."
                    ),
                )
            )

    for clave, cajas_d in cajas_desp_cont.items():
        if clave not in por_contenedor:
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="DESPACHO_SIN_PDF",
                    nivel="error",
                    mensaje=(
                        "Hay líneas en Despachos sin PDF para el "
                        f"contenedor {clave}."
                    ),
                )
            )
            continue
        liq = por_contenedor[clave]
        if liq.total_cajas != Decimal(cajas_d):
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="CAJAS_NO_COINCIDEN",
                    nivel="error",
                    mensaje=(
                        f"Cajas de {liq.contenedor}: PDF "
                        f"{liq.total_cajas} vs Despachos {cajas_d}."
                    ),
                    detalles={
                        "contenedor": liq.contenedor,
                        "cajas_pdf": str(liq.total_cajas),
                        "cajas_despachos": cajas_d,
                    },
                )
            )
        cajas_pdf_cal: dict[int, Decimal] = defaultdict(
            lambda: Decimal("0")
        )
        for prod in liq.productos:
            cajas_pdf_cal[prod.calibre] += prod.cajas
        cajas_desp_cal: dict[int, int] = defaultdict(int)
        for ln in despachos.lineas:
            if _clave_cont(ln.contenedor) != clave:
                continue
            cajas_desp_cal[ln.calibre] += ln.total_cajas
        for calibre in sorted(
            set(cajas_pdf_cal) | set(cajas_desp_cal)
        ):
            c_pdf = cajas_pdf_cal.get(calibre, Decimal("0"))
            c_desp = Decimal(cajas_desp_cal.get(calibre, 0))
            if c_pdf != c_desp:
                errores.append(
                    IncidenciaValidacionFruver(
                        codigo="CAJAS_CALIBRE_NO_COINCIDEN",
                        nivel="error",
                        mensaje=(
                            f"Cajas {liq.contenedor} calibre "
                            f"{calibre}: PDF {c_pdf} vs "
                            f"Despachos {c_desp}."
                        ),
                        detalles={
                            "contenedor": liq.contenedor,
                            "calibre": calibre,
                            "cajas_pdf": str(c_pdf),
                            "cajas_despachos": str(c_desp),
                        },
                    )
                )
        venta_calc = sum(
            (p.venta_eur for p in liq.productos),
            Decimal("0"),
        )
        venta_cuadra = (
            abs(venta_calc - liq.total_venta_eur) <= TOLERANCIA_VENTA
        )
        if not venta_cuadra:
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="VENTA_NO_CUADRA",
                    nivel="error",
                    mensaje=(
                        f"Venta de {liq.contenedor}: cálculo "
                        f"{venta_calc} vs PDF {liq.total_venta_eur}."
                    ),
                )
            )
        suma_gastos = sum(liq.gastos.values(), Decimal("0"))
        diff_gastos = abs(suma_gastos - liq.total_gastos_eur)
        diff_con_flete = abs(
            suma_gastos + liq.flete_eur - liq.total_gastos_eur
        )
        gastos_cuadran = (
            diff_gastos <= TOLERANCIA_GASTOS
            or diff_con_flete <= TOLERANCIA_GASTOS
        )
        if not gastos_cuadran:
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="GASTOS_NO_CUADRAN",
                    nivel="error",
                    mensaje=(
                        f"Gastos de {liq.contenedor}: suma "
                        f"{suma_gastos} vs PDF {liq.total_gastos_eur}."
                    ),
                )
            )
        resumen.append(
            ResumenGastosContenedor(
                contenedor=liq.contenedor,
                factura_corta=liq.factura_corta,
                comision=liq.comision,
                gastos=dict(liq.gastos),
                total_cajas_pdf=liq.total_cajas,
                total_cajas_despachos=Decimal(cajas_d),
                total_venta_pdf=liq.total_venta_eur,
                total_venta_calc=venta_calc,
                total_gastos_pdf=liq.total_gastos_eur,
                total_gastos_calc=suma_gastos,
                venta_cuadra=venta_cuadra,
                gastos_cuadran=gastos_cuadran,
                flete_eur=liq.flete_eur,
            )
        )

    precios_por_cont: dict[str, dict[int, Decimal]] = {}
    for clave, liq in por_contenedor.items():
        precios_por_cont[clave] = {
            p.calibre: p.precio_eur for p in liq.productos
        }

    avisos_tipo: set[tuple[str, int, str]] = set()
    for linea in despachos.lineas:
        clave = _clave_cont(linea.contenedor)
        liq = por_contenedor.get(clave)
        if liq is None:
            continue
        if not _es_especial(linea.tipo_empaque, linea.carton):
            firma = (
                linea.contenedor,
                linea.calibre,
                linea.tipo_empaque,
            )
            if firma not in avisos_tipo:
                avisos_tipo.add(firma)
                advertencias.append(
                    IncidenciaValidacionFruver(
                        codigo="TIPO_NO_ESPECIAL",
                        nivel="advertencia",
                        mensaje=(
                            f"Despachos {linea.contenedor} calibre "
                            f"{linea.calibre} no es Especial "
                            f"({linea.tipo_empaque or linea.carton}); "
                            "se digita Especial."
                        ),
                    )
                )
        precio = precios_por_cont[clave].get(linea.calibre)
        if precio is None:
            errores.append(
                IncidenciaValidacionFruver(
                    codigo="PRECIO_NO_ENCONTRADO",
                    nivel="error",
                    mensaje=(
                        f"Sin precio en PDF para "
                        f"{linea.contenedor} calibre {linea.calibre}."
                    ),
                )
            )
            precio = Decimal("0")
        lineas_prep.append(
            LineaPreparadaFruver(
                semana=linea.semana,
                anio=linea.anio,
                semana_texto=linea.semana_texto
                or despachos.semana_texto,
                cliente=linea.cliente,
                nave=linea.barco,
                contenedor=linea.contenedor,
                destino=destino_excel,
                tipo_fruta="Especial",
                calibre=linea.calibre,
                total_cajas=Decimal(linea.total_cajas),
                carton=linea.carton,
                demora_eur=liq.gastos.get("Demora Eur", Decimal("0")),
                portes_eur=liq.gastos.get("Portes Eur", Decimal("0")),
                gasto_puerto_eur=liq.gastos.get(
                    "Gasto Puerto eur", Decimal("0")
                ),
                aduanas_eur=liq.gastos.get("Aduanas Eur", Decimal("0")),
                otros3=liq.gastos.get("Otros3", Decimal("0")),
                comision=liq.comision,
                precio_venta_eur=precio,
            )
        )

    total_liq = sum(
        (liq.total_cajas for liq in liquidaciones),
        Decimal("0"),
    )
    if not lineas_prep and not errores:
        errores.append(
            IncidenciaValidacionFruver(
                codigo="SIN_LINEAS_PREPARADAS",
                nivel="error",
                mensaje="No quedó ninguna línea para escribir.",
            )
        )

    return ResultadoValidacionFruver(
        es_valido=not errores,
        destino_ui=destino_ui,
        factura_corta=factura_objetivo,
        destinos_despachos=destinos,
        total_cajas_liquidacion=total_liq,
        total_cajas_despachos=Decimal(despachos.total_cajas),
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_prep),
        resumen_gastos_contenedores=tuple(resumen),
    )
