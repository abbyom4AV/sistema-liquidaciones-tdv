from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.sifa.extractor import (
    COLUMNAS_GASTO,
    LiquidacionSifa,
)
from services.sifa.matcher import (
    CLIENTE_SIFA_DESPACHOS,
    CLIENTE_SIFA_RAW,
    LineaDespachoSifa,
    ResultadoMatcherSifa,
)
from services.sifa.processor import ResultadoPreparacionSifa
from services.sifa.validator import (
    LineaPreparadaSifa,
    ResultadoValidacionSifa,
)
from services.sifa.writer import NOMBRE_DESCARGA_SIFA


class ErrorConfirmacionGeneracionSifa(Exception):
    """Error al reconstruir el procesamiento SIFA para escritura."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA_SIFA


def _decimal_a_texto(valor: Decimal) -> str:
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto if texto else "0"


def _a_decimal(valor: Any, campo: str) -> Decimal:
    try:
        if isinstance(valor, Decimal):
            return valor
        if isinstance(valor, bool):
            raise InvalidOperation
        if isinstance(valor, (int, float, str)):
            return Decimal(str(valor))
        raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ErrorConfirmacionGeneracionSifa(
            f"El campo {campo!r} no es numérico."
        ) from error


def _a_entero(valor: Any, campo: str) -> int:
    try:
        if isinstance(valor, bool):
            raise TypeError
        if isinstance(valor, int):
            return valor
        if isinstance(valor, Decimal):
            return int(valor)
        return int(str(valor).strip())
    except (TypeError, ValueError) as error:
        raise ErrorConfirmacionGeneracionSifa(
            f"El campo {campo!r} no es entero."
        ) from error


def _a_bool(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() in {"1", "true", "si", "sí"}
    return bool(valor)


def _gastos_desde_dict(
    crudo: Mapping[str, object] | None,
) -> dict[str, Decimal]:
    gastos: dict[str, Decimal] = {
        columna: Decimal("0") for columna in COLUMNAS_GASTO
    }
    if not crudo:
        return gastos
    for columna in COLUMNAS_GASTO:
        if columna in crudo:
            gastos[columna] = _a_decimal(
                crudo[columna],
                f"gastos.{columna}",
            )
    return gastos


def serializar_linea_preparada_sifa(
    linea: LineaPreparadaSifa,
) -> dict[str, Any]:
    """Convierte una línea preparada a JSON para guardar en la BD."""
    return {
        "contenedor": linea.contenedor,
        "nave": linea.nave,
        "destino": linea.destino,
        "tipo_fruta": linea.tipo_fruta,
        "carton": linea.carton,
        "calibre": linea.calibre,
        "total_cajas": linea.total_cajas,
        "semana": linea.semana,
        "anio": linea.anio,
        "semana_texto": linea.semana_texto,
        "cliente_raw": linea.cliente_raw,
        "precio_venta_eur": _decimal_a_texto(
            linea.precio_venta_eur
        ),
        "comision": _decimal_a_texto(linea.comision),
        "sin_comision_linea": linea.sin_comision_linea,
        "gastos": {
            columna: _decimal_a_texto(
                linea.gastos.get(columna, Decimal("0"))
            )
            for columna in COLUMNAS_GASTO
        },
        "factura_corta": linea.factura_corta,
        "fila_origen": linea.fila_origen,
    }


def serializar_lineas_preparadas_sifa(lineas) -> list[dict[str, Any]]:
    return [
        serializar_linea_preparada_sifa(linea) for linea in lineas
    ]


def serializar_resumen_gastos_sifa(
    resumen: Mapping[str, Decimal] | None,
) -> dict[str, str]:
    if not resumen:
        return {
            columna: "0" for columna in COLUMNAS_GASTO
        }
    return {
        columna: _decimal_a_texto(
            resumen.get(columna, Decimal("0"))
        )
        for columna in COLUMNAS_GASTO
    }


def serializar_resumen_contenedores_sifa(resumen) -> list[dict]:
    resultado = []
    for item in resumen or ():
        resultado.append(
            {
                "contenedor": item.contenedor,
                "total_cajas": item.total_cajas,
                "total_venta_eur": _decimal_a_texto(
                    item.total_venta_eur
                ),
                "comision_eur": _decimal_a_texto(
                    item.comision_eur
                ),
            }
        )
    return resultado


def reconstruir_resultado_para_escritura_sifa(
    *,
    anio: int,
    semana: int,
    destino_ui: str,
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...],
    total_cajas_liquidacion: int | Decimal = 0,
    total_cajas_despachos: int | Decimal = 0,
    destinos_despachos: list[str]
    | tuple[str, ...]
    | None = None,
    comision_total: int | Decimal = 0,
    resumen_gastos: Mapping[str, object] | None = None,
    lineas_con_comision: int = 0,
    lineas_sin_comision: int = 0,
) -> ResultadoPreparacionSifa:
    """
    Arma el resultado listo para escribir Excel sin re-leer el
    Excel de liquidación ni Despachos: usa las líneas y gastos ya
    persistidos en la BD (lineas_preparadas del ProcesamientoSifa).
    """
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionSifa(
            "No hay líneas preparadas guardadas."
        )

    semana_int = int(semana)
    anio_int = int(anio)
    semana_texto_defecto = f"{semana_int:02d}-{anio_int}"
    gastos_globales = _gastos_desde_dict(resumen_gastos)

    lineas: list[LineaPreparadaSifa] = []
    contenedores: list[str] = []
    destinos_vistos: list[str] = []
    naves: list[str] = []
    despachos_lineas: list[LineaDespachoSifa] = []

    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionSifa(
                f"Línea preparada inválida en posición {indice}."
            )

        contenedor = (
            str(cruda.get("contenedor") or "").strip().upper()
        )
        nave = str(cruda.get("nave") or "").strip()
        carton = str(cruda.get("carton") or "").strip()
        tipo_fruta = str(cruda.get("tipo_fruta") or "").strip()
        destino = (
            str(cruda.get("destino") or destino_ui or "")
            .strip()
            .upper()
        )
        calibre = _a_entero(cruda.get("calibre"), "calibre")
        total_cajas = _a_entero(
            cruda.get("total_cajas"),
            "total_cajas",
        )
        factura_corta = str(
            cruda.get("factura_corta") or ""
        ).strip()
        anio_linea = _a_entero(
            cruda.get("anio") or anio_int,
            "anio",
        )
        semana_linea = _a_entero(
            cruda.get("semana") or semana_int,
            "semana",
        )
        semana_texto = (
            str(cruda.get("semana_texto") or "").strip()
            or semana_texto_defecto
        )
        cliente_raw = (
            str(cruda.get("cliente_raw") or "").strip()
            or CLIENTE_SIFA_RAW
        )

        if not contenedor or not destino:
            raise ErrorConfirmacionGeneracionSifa(
                f"Línea {indice} incompleta (contenedor/destino)."
            )

        comision = _a_decimal(
            cruda.get("comision") or 0,
            "comision",
        )
        precio_venta_eur = _a_decimal(
            cruda.get("precio_venta_eur") or 0,
            "precio_venta_eur",
        )
        gastos = _gastos_desde_dict(
            cruda.get("gastos") or gastos_globales
        )
        sin_comision_linea = _a_bool(
            cruda.get("sin_comision_linea")
        )
        fila_origen = _a_entero(
            cruda.get("fila_origen") or indice,
            "fila_origen",
        )

        lineas.append(
            LineaPreparadaSifa(
                contenedor=contenedor,
                nave=nave,
                destino=destino,
                tipo_fruta=tipo_fruta,
                carton=carton,
                calibre=calibre,
                total_cajas=total_cajas,
                semana=semana_linea,
                anio=anio_linea,
                semana_texto=semana_texto,
                cliente_raw=cliente_raw,
                precio_venta_eur=precio_venta_eur,
                comision=comision,
                sin_comision_linea=sin_comision_linea,
                gastos=gastos,
                factura_corta=factura_corta,
                fila_origen=fila_origen,
            )
        )
        despachos_lineas.append(
            LineaDespachoSifa(
                fila_excel=indice,
                semana=semana_linea,
                anio=anio_linea,
                semana_texto=semana_texto,
                contenedor=contenedor,
                cliente=CLIENTE_SIFA_DESPACHOS,
                barco=nave,
                puerto_destino=destino,
                tipo_empaque=tipo_fruta,
                carton=carton,
                calibre=calibre,
                total_cajas=total_cajas,
                factura=factura_corta,
                factura_corta=factura_corta,
            )
        )
        if contenedor not in contenedores:
            contenedores.append(contenedor)
        if destino not in destinos_vistos:
            destinos_vistos.append(destino)
        if nave and nave not in naves:
            naves.append(nave)

    destinos = tuple(
        str(item).strip().upper()
        for item in (destinos_despachos or [])
        if str(item).strip()
    ) or tuple(destinos_vistos)

    total_liq = _a_entero(
        total_cajas_liquidacion or 0,
        "total_cajas_liquidacion",
    )
    total_desp = _a_entero(
        total_cajas_despachos or 0,
        "total_cajas_despachos",
    )
    if total_desp <= 0:
        total_desp = sum(linea.total_cajas for linea in lineas)

    comision_val = _a_decimal(
        comision_total or 0,
        "comision_total",
    )
    if comision_val == 0 and lineas:
        comision_val = lineas[0].comision

    despachos = ResultadoMatcherSifa(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_SIFA_DESPACHOS,
        semana=semana_int,
        anio=anio_int,
        destino_buscado=destino_ui,
        semana_texto=semana_texto_defecto,
        lineas=tuple(despachos_lineas),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
        destinos=destinos,
        facturas_cortas=tuple(
            dict.fromkeys(
                linea.factura_corta
                for linea in lineas
                if linea.factura_corta
            )
        ),
        naves=tuple(naves),
    )
    liquidacion = LiquidacionSifa(
        archivo="",
        hoja="",
        vessel="",
        destino_header=destino_ui,
        factura="",
        factura_corta="",
        orden="",
        contenedores_header=tuple(contenedores),
        lineas=(),
        gastos=dict(gastos_globales),
        rubros_mapeados=(),
        rubros_no_mapeados=(),
        comisiones_contenedor=(),
        totales_contenedor=(),
        total_costos_excel=None,
        total_cajas=total_liq or total_desp,
        total_venta_eur=Decimal("0"),
    )
    validacion = ResultadoValidacionSifa(
        es_valido=True,
        destino_ui=destino_ui,
        destinos_despachos=destinos,
        total_cajas_liquidacion=total_liq or total_desp,
        total_cajas_despachos=total_desp,
        comision_total=comision_val,
        total_venta_eur=Decimal("0"),
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
        resumen_gastos=dict(gastos_globales),
        resumen_contenedores=(),
        lineas_con_comision=int(lineas_con_comision or 0),
        lineas_sin_comision=int(lineas_sin_comision or 0),
    )
    return ResultadoPreparacionSifa(
        estado="listo",
        puede_escribir=True,
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )
