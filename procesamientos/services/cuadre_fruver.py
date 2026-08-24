from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from services.fruver.extractor import extraer_liquidacion_fruver

if TYPE_CHECKING:
    from procesamientos.models import ArchivoPdfFruver

logger = logging.getLogger(__name__)


def decimal_a_str_fruver(valor) -> str:
    if isinstance(valor, Decimal):
        texto = format(valor, "f")
    else:
        texto = str(valor)
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


def clave_contenedor_fruver(valor: str) -> str:
    return str(valor or "").upper().replace(" ", "")


def ventas_calc_desde_lineas(lineas: list[dict]) -> dict[str, Decimal]:
    ventas: dict[str, Decimal] = {}
    for linea in lineas:
        contenedor = str(linea.get("contenedor") or "")
        if not contenedor:
            continue
        cajas = Decimal(str(linea.get("total_cajas") or 0))
        precio = Decimal(str(linea.get("precio_venta_eur") or 0))
        ventas[contenedor] = (
            ventas.get(contenedor, Decimal("0")) + cajas * precio
        )
    return ventas


def completar_totales_pdf_resumen(
    resumen: list[dict],
    pdfs: list[ArchivoPdfFruver],
) -> tuple[list[dict], bool]:
    if not resumen or not pdfs:
        return resumen, False

    liquidaciones = {}
    for pdf in pdfs:
        try:
            liq = extraer_liquidacion_fruver(pdf.archivo.path)
        except Exception:
            logger.warning(
                "No se pudo re-leer PDF Fruver %s",
                pdf.archivo.name,
                exc_info=True,
            )
            continue
        liquidaciones[clave_contenedor_fruver(liq.contenedor)] = liq

    actualizado = False
    salida: list[dict] = []
    for crudo in resumen:
        item = dict(crudo)
        liq = liquidaciones.get(
            clave_contenedor_fruver(str(item.get("contenedor") or ""))
        )
        if liq is not None:
            if item.get("total_gastos_pdf") in (None, ""):
                item["total_gastos_pdf"] = decimal_a_str_fruver(
                    liq.total_gastos_eur
                )
                actualizado = True
            if item.get("total_venta_pdf") in (None, ""):
                item["total_venta_pdf"] = decimal_a_str_fruver(
                    liq.total_venta_eur
                )
                actualizado = True
        salida.append(item)
    return salida, actualizado


def enriquecer_resumen_cuadre(
    resumen: list[dict],
    lineas: list[dict] | None = None,
) -> list[dict]:
    ventas_lineas = ventas_calc_desde_lineas(lineas or [])
    salida: list[dict] = []
    for crudo in resumen:
        item = dict(crudo)
        contenedor = str(item.get("contenedor") or "")
        if contenedor in ventas_lineas:
            item["total_venta_calc"] = decimal_a_str_fruver(
                ventas_lineas[contenedor]
            )
        gastos = item.get("gastos") or {}
        try:
            suma = sum(
                Decimal(str(valor or 0))
                for valor in gastos.values()
            )
        except Exception:
            suma = Decimal("0")
        if item.get("total_gastos_calc") in (None, ""):
            item["total_gastos_calc"] = decimal_a_str_fruver(suma)
        venta_calc = Decimal(str(item.get("total_venta_calc") or 0))
        venta_pdf = Decimal(str(item.get("total_venta_pdf") or 0))
        gastos_calc = Decimal(
            str(item.get("total_gastos_calc") or 0)
        )
        gastos_pdf = Decimal(str(item.get("total_gastos_pdf") or 0))
        flete = Decimal(str(item.get("flete_eur") or 0))
        tiene_gastos_pdf = item.get("total_gastos_pdf") not in (
            None,
            "",
        )
        item["tiene_gastos_pdf"] = tiene_gastos_pdf
        if "venta_cuadra" not in item:
            item["venta_cuadra"] = (
                abs(venta_calc - venta_pdf) <= Decimal("0.05")
            )
        if "gastos_cuadran" not in item and tiene_gastos_pdf:
            item["gastos_cuadran"] = (
                abs(gastos_calc - gastos_pdf) <= Decimal("0.05")
                or abs(gastos_calc + flete - gastos_pdf)
                <= Decimal("0.05")
            )
        salida.append(item)
    return salida


def totales_cuadre(resumen: list[dict]) -> dict[str, Any]:
    venta_calc = Decimal("0")
    venta_pdf = Decimal("0")
    gastos_calc = Decimal("0")
    gastos_pdf = Decimal("0")
    tiene_gastos_pdf = True
    venta_ok = True
    gastos_ok = True
    if not resumen:
        tiene_gastos_pdf = False
        venta_ok = False
        gastos_ok = False
    for item in resumen:
        venta_calc += Decimal(str(item.get("total_venta_calc") or 0))
        venta_pdf += Decimal(str(item.get("total_venta_pdf") or 0))
        gastos_calc += Decimal(str(item.get("total_gastos_calc") or 0))
        if not item.get("tiene_gastos_pdf"):
            tiene_gastos_pdf = False
            gastos_ok = False
        else:
            gastos_pdf += Decimal(str(item.get("total_gastos_pdf") or 0))
        if not item.get("venta_cuadra"):
            venta_ok = False
        if item.get("gastos_cuadran") is False:
            gastos_ok = False
    return {
        "total_venta_calc": decimal_a_str_fruver(venta_calc),
        "total_venta_pdf": decimal_a_str_fruver(venta_pdf),
        "total_gastos_calc": decimal_a_str_fruver(gastos_calc),
        "total_gastos_pdf": decimal_a_str_fruver(gastos_pdf),
        "tiene_gastos_pdf": tiene_gastos_pdf,
        "venta_cuadra": venta_ok,
        "gastos_cuadran": gastos_ok,
    }
