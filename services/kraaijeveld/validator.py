from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.dimanno.matcher import normalizar_texto
from services.kraaijeveld.extractor import (
    COLUMNAS_GASTO,
    LiquidacionKraaijeveld,
)
from services.kraaijeveld.matcher import (
    LineaDespachoKraaijeveld,
    ResultadoMatcherKraaijeveld,
)
from services.mensajes_gastos import mensaje_gastos_no_mapeados


NivelIncidencia = Literal["error", "advertencia"]


@dataclass(frozen=True)
class IncidenciaValidacionKraaijeveld:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaKraaijeveld:
    despacho: LineaDespachoKraaijeveld
    es_precio_fijo: bool
    precio_venta_eur: Decimal | None
    precio_venta_usd: Decimal | None
    comision: Decimal
    gastos: dict[str, Decimal]
    precio_encontrado: bool


@dataclass(frozen=True)
class ResumenGastosContenedor:
    contenedor: str
    commission_order: str
    factura_corta: str
    comision: Decimal
    gastos: dict[str, Decimal]
    rubros_pdf: tuple[str, ...]


@dataclass(frozen=True)
class ResultadoValidacionKraaijeveld:
    es_valido: bool
    destino_ui: str
    destinos_despachos: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    errores: tuple[IncidenciaValidacionKraaijeveld, ...]
    advertencias: tuple[IncidenciaValidacionKraaijeveld, ...]
    lineas_preparadas: tuple[LineaPreparadaKraaijeveld, ...]
    resumen_gastos_contenedores: tuple[ResumenGastosContenedor, ...]


def _variante_despacho(tipo_empaque: str, carton: str = "") -> str:
    t = normalizar_texto(tipo_empaque)
    c = normalizar_texto(carton)
    if "CROWNLESS" in t or "CROWNLESS" in c:
        return "CROWNLESS"
    if t == "ESPECIAL" or "ESPECIAL" in t:
        return "ESPECIAL"
    return "VERDE"


def _precios_por_calibre(
    liquidacion: LiquidacionKraaijeveld,
) -> dict[tuple[str, int], Decimal]:
    precios: dict[tuple[str, int], Decimal] = {}
    for producto in liquidacion.productos:
        clave = (producto.variante, producto.calibre)
        precios[clave] = producto.precio_eur
    return precios


def _precio_para_linea(
    precios: dict[tuple[str, int], Decimal],
    variante: str,
    calibre: int,
) -> Decimal | None:
    # Crownless: ignorar calibre; usar el precio Crownless del PDF.
    if variante == "CROWNLESS":
        crownless = [
            p
            for (var, _cal), p in precios.items()
            if var == "CROWNLESS"
        ]
        if crownless:
            return crownless[0]
        return None

    precio = precios.get((variante, calibre))
    if precio is not None:
        return precio

    for (_var, cal), p in precios.items():
        if cal == calibre:
            return p
    return None


@dataclass(frozen=True)
class MapeoPrecioFijoContenedor:
    tipo_fruta: str
    calibre: int
    precio: Decimal


def _clave_contenedor(valor: str) -> str:
    return normalizar_texto(valor).replace(" ", "")


def _normalizar_variante_ui(valor: str) -> str:
    t = normalizar_texto(valor)
    if "CROWNLESS" in t:
        return "CROWNLESS"
    if "ESPECIAL" in t or t == "COL":
        return "ESPECIAL"
    if "VERDE" in t or t == "VER":
        return "VERDE"
    if "INTERMEDIO" in t or t == "INT":
        return "ESPECIAL"
    return t


def validar_liquidaciones_kraaijeveld(
    liquidaciones: tuple[LiquidacionKraaijeveld, ...],
    despachos: ResultadoMatcherKraaijeveld,
    destino_ui: str,
    *,
    incluye_precio_fijo: bool = False,
    modo_precio_fijo: str = "factura",
    factura_corta_fijo: str | None = None,
    precio_fijo: Decimal | None = None,
    moneda_fijo: str | None = None,
    contenedor_fijo: str | None = None,
    mapeos_precio_fijo: tuple[MapeoPrecioFijoContenedor, ...] = (),
) -> ResultadoValidacionKraaijeveld:
    errores: list[IncidenciaValidacionKraaijeveld] = []
    advertencias: list[IncidenciaValidacionKraaijeveld] = []

    destinos = despachos.destinos
    destino_n = normalizar_texto(destino_ui)
    if destinos and all(
        normalizar_texto(d) != destino_n
        and destino_n not in normalizar_texto(d)
        and normalizar_texto(d) not in destino_n
        for d in destinos
    ):
        advertencias.append(
            IncidenciaValidacionKraaijeveld(
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

    if not liquidaciones and not incluye_precio_fijo:
        errores.append(
            IncidenciaValidacionKraaijeveld(
                codigo="SIN_PDFS",
                nivel="error",
                mensaje="No se cargaron PDFs de consignación.",
            )
        )

    por_contenedor: dict[str, LiquidacionKraaijeveld] = {}
    for liq in liquidaciones:
        clave = normalizar_texto(liq.contenedor).replace(" ", "")
        if clave in por_contenedor:
            errores.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="CONTENEDOR_PDF_DUPLICADO",
                    nivel="error",
                    mensaje=(
                        f"Hay más de un PDF para el contenedor "
                        f"{liq.contenedor}."
                    ),
                )
            )
        por_contenedor[clave] = liq
        if liq.tiene_crownless:
            advertencias.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="CROWNLESS_EN_PDF",
                    nivel="advertencia",
                    mensaje=(
                        f"El PDF del contenedor {liq.contenedor} "
                        "incluye Crownless; se aplicará su precio "
                        "a las líneas Crownless de Despachos."
                    ),
                    detalles={"archivo": liq.archivo},
                )
            )
        for rubro in liq.rubros_no_mapeados:
            errores.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="GASTO_NO_MAPEADO",
                    nivel="error",
                    mensaje=mensaje_gastos_no_mapeados(
                        [f"{liq.contenedor}: {rubro}"]
                    ),
                    detalles={
                        "contenedor": liq.contenedor,
                        "rubro": rubro,
                    },
                )
            )

    factura_fijo = (
        str(factura_corta_fijo).strip()
        if incluye_precio_fijo
        and (modo_precio_fijo or "factura") == "factura"
        and factura_corta_fijo
        else ""
    )
    cont_fijo = (
        _clave_contenedor(contenedor_fijo or "")
        if incluye_precio_fijo
        and (modo_precio_fijo or "") == "contenedor"
        else ""
    )
    mapeos_fijos: dict[tuple[str, int], Decimal] = {}
    if incluye_precio_fijo and cont_fijo:
        for mapeo in mapeos_precio_fijo:
            clave = (
                _normalizar_variante_ui(mapeo.tipo_fruta),
                int(mapeo.calibre),
            )
            mapeos_fijos[clave] = mapeo.precio

    if incluye_precio_fijo:
        modo = (modo_precio_fijo or "factura").strip().lower()
        if modo not in {"factura", "contenedor"}:
            errores.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="MODO_PRECIO_FIJO_INVALIDO",
                    nivel="error",
                    mensaje=(
                        "Seleccione precio fijo por factura "
                        "o por contenedor."
                    ),
                )
            )
        if modo == "factura":
            if len(factura_fijo) != 4 or not factura_fijo.isdigit():
                errores.append(
                    IncidenciaValidacionKraaijeveld(
                        codigo="FACTURA_FIJO_INVALIDA",
                        nivel="error",
                        mensaje=(
                            "Indique los 4 dígitos de la factura "
                            "a precio fijo."
                        ),
                    )
                )
            if precio_fijo is None or precio_fijo <= 0:
                errores.append(
                    IncidenciaValidacionKraaijeveld(
                        codigo="PRECIO_FIJO_INVALIDO",
                        nivel="error",
                        mensaje="Indique el precio fijo (> 0).",
                    )
                )
        elif modo == "contenedor":
            if not cont_fijo:
                errores.append(
                    IncidenciaValidacionKraaijeveld(
                        codigo="CONTENEDOR_FIJO_INVALIDO",
                        nivel="error",
                        mensaje=(
                            "Indique el contenedor a precio fijo."
                        ),
                    )
                )
            if not mapeos_fijos:
                errores.append(
                    IncidenciaValidacionKraaijeveld(
                        codigo="MAPEOS_PRECIO_FIJO_VACIOS",
                        nivel="error",
                        mensaje=(
                            "Agregue al menos un mapeo de tipo "
                            "de fruta, calibre y precio."
                        ),
                    )
                )
            for (tipo, calibre), precio in mapeos_fijos.items():
                if tipo not in {"ESPECIAL", "VERDE", "CROWNLESS"}:
                    errores.append(
                        IncidenciaValidacionKraaijeveld(
                            codigo="TIPO_FIJO_INVALIDO",
                            nivel="error",
                            mensaje=(
                                "Tipo de fruta a precio fijo "
                                "inválido: use Especial, Verde "
                                "o Crownless."
                            ),
                            detalles={"tipo_fruta": tipo},
                        )
                    )
                if calibre <= 0:
                    errores.append(
                        IncidenciaValidacionKraaijeveld(
                            codigo="CALIBRE_FIJO_INVALIDO",
                            nivel="error",
                            mensaje=(
                                "Indique un calibre válido "
                                "en el mapeo a precio fijo."
                            ),
                        )
                    )
                if precio is None or precio <= 0:
                    errores.append(
                        IncidenciaValidacionKraaijeveld(
                            codigo="PRECIO_FIJO_INVALIDO",
                            nivel="error",
                            mensaje=(
                                "Indique precio fijo (> 0) "
                                "en cada mapeo."
                            ),
                        )
                    )
        mon = (moneda_fijo or "").strip().upper()
        if mon not in {"EUR", "USD", "€", "$"}:
            errores.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="MONEDA_FIJO_INVALIDA",
                    nivel="error",
                    mensaje="Seleccione moneda EUR o USD.",
                )
            )

    moneda = (moneda_fijo or "EUR").strip().upper()
    es_usd = moneda in {"USD", "$"}

    lineas_prep: list[LineaPreparadaKraaijeveld] = []
    cajas_liq = 0
    for liq in liquidaciones:
        cajas_liq += sum(p.cantidad for p in liq.productos)

    resumen: list[ResumenGastosContenedor] = []
    for liq in liquidaciones:
        rubros = tuple(
            f"{col}={liq.gastos.get(col, 0)}"
            for col in COLUMNAS_GASTO
            if liq.gastos.get(col, 0)
        ) + ((f"Comision={liq.comision}",) if liq.comision else ())
        resumen.append(
            ResumenGastosContenedor(
                contenedor=liq.contenedor,
                commission_order=liq.commission_order,
                factura_corta=liq.factura_corta,
                comision=liq.comision,
                gastos=dict(liq.gastos),
                rubros_pdf=rubros,
            )
        )

    for linea in despachos.lineas:
        cont_n = normalizar_texto(linea.contenedor).replace(" ", "")
        variante = _variante_despacho(
            linea.tipo_empaque,
            linea.carton,
        )
        es_fijo_factura = bool(
            incluye_precio_fijo
            and factura_fijo
            and linea.factura_corta == factura_fijo
        )
        precio_mapeo = mapeos_fijos.get((variante, linea.calibre))
        es_fijo_contenedor = bool(
            incluye_precio_fijo
            and cont_fijo
            and cont_n == cont_fijo
            and precio_mapeo is not None
        )

        if es_fijo_factura or es_fijo_contenedor:
            precio_usar = (
                precio_mapeo if es_fijo_contenedor else precio_fijo
            )
            precio_eur = None if es_usd else precio_usar
            precio_usd = precio_usar if es_usd else None
            gastos_cero = {col: Decimal("0") for col in COLUMNAS_GASTO}
            lineas_prep.append(
                LineaPreparadaKraaijeveld(
                    despacho=linea,
                    es_precio_fijo=True,
                    precio_venta_eur=precio_eur,
                    precio_venta_usd=precio_usd,
                    comision=Decimal("0"),
                    gastos=gastos_cero,
                    precio_encontrado=True,
                )
            )
            continue

        liq = por_contenedor.get(cont_n)
        if liq is None:
            # Puede ser línea de otra factura consignación sin PDF
            advertencias.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="SIN_PDF_CONTENEDOR",
                    nivel="advertencia",
                    mensaje=(
                        f"No hay PDF para el contenedor "
                        f"{linea.contenedor}; se omite la línea."
                    ),
                    detalles={
                        "contenedor": linea.contenedor,
                        "factura_corta": linea.factura_corta,
                    },
                )
            )
            continue

        precios = _precios_por_calibre(liq)
        precio = _precio_para_linea(
            precios,
            variante,
            linea.calibre,
        )
        encontrado = precio is not None
        if not encontrado:
            advertencias.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="PRECIO_NO_ENCONTRADO",
                    nivel="advertencia",
                    mensaje=(
                        f"Sin precio en PDF para "
                        f"{linea.contenedor} "
                        f"{variante} calibre "
                        f"{linea.calibre}."
                    ),
                )
            )
            precio = Decimal("0")

        lineas_prep.append(
            LineaPreparadaKraaijeveld(
                despacho=linea,
                es_precio_fijo=False,
                precio_venta_eur=precio,
                precio_venta_usd=None,
                comision=liq.comision,
                gastos=dict(liq.gastos),
                precio_encontrado=encontrado,
            )
        )

    # PDFs sin líneas Despachos
    conts_desp = {
        normalizar_texto(c).replace(" ", "")
        for c in despachos.contenedores
    }
    for clave, liq in por_contenedor.items():
        if clave not in conts_desp:
            advertencias.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="PDF_SIN_DESPACHO",
                    nivel="advertencia",
                    mensaje=(
                        f"El PDF del contenedor {liq.contenedor} "
                        "no tiene líneas en Despachos filtradas."
                    ),
                )
            )

    if incluye_precio_fijo and factura_fijo:
        fijas = [
            ln
            for ln in despachos.lineas
            if ln.factura_corta == factura_fijo
        ]
        if not fijas:
            advertencias.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="SIN_LINEAS_PRECIO_FIJO",
                    nivel="advertencia",
                    mensaje=(
                        f"No hay líneas Despachos con factura "
                        f"{factura_fijo} en semana/destino."
                    ),
                )
            )

    if incluye_precio_fijo and cont_fijo:
        lineas_cont = [
            ln
            for ln in despachos.lineas
            if _clave_contenedor(ln.contenedor) == cont_fijo
        ]
        if not lineas_cont:
            advertencias.append(
                IncidenciaValidacionKraaijeveld(
                    codigo="SIN_LINEAS_CONTENEDOR_FIJO",
                    nivel="advertencia",
                    mensaje=(
                        f"No hay líneas Despachos para el "
                        f"contenedor {contenedor_fijo} en "
                        f"semana/destino."
                    ),
                )
            )
        else:
            aplicadas = [
                ln
                for ln in lineas_cont
                if (
                    _variante_despacho(
                        ln.tipo_empaque,
                        ln.carton,
                    ),
                    ln.calibre,
                )
                in mapeos_fijos
            ]
            if not aplicadas:
                advertencias.append(
                    IncidenciaValidacionKraaijeveld(
                        codigo="SIN_MAPEO_CONTENEDOR_FIJO",
                        nivel="advertencia",
                        mensaje=(
                            f"Ningún mapeo tipo/calibre coincidió "
                            f"con líneas del contenedor "
                            f"{contenedor_fijo}."
                        ),
                    )
                )

    if not lineas_prep and not errores:
        errores.append(
            IncidenciaValidacionKraaijeveld(
                codigo="SIN_LINEAS_PREPARADAS",
                nivel="error",
                mensaje="No quedó ninguna línea para escribir.",
            )
        )

    return ResultadoValidacionKraaijeveld(
        es_valido=not errores,
        destino_ui=destino_ui,
        destinos_despachos=destinos,
        total_cajas_liquidacion=cajas_liq,
        total_cajas_despachos=despachos.total_cajas,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_prep),
        resumen_gastos_contenedores=tuple(resumen),
    )
