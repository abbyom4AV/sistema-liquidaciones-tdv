from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.glamour.extractor import (
    COLUMNAS_GASTO,
    LiquidacionGlamour,
)
from services.glamour.matcher import (
    CLIENTE_GLAMOUR,
    LineaDespachoGlamour,
    ResultadoMatcherGlamour,
)
from services.glamour.processor import ResultadoPreparacionGlamour
from services.glamour.validator import (
    LineaPreparadaGlamour,
    ResultadoValidacionGlamour,
)
from services.glamour.writer import NOMBRE_DESCARGA_GLAMOUR


class ErrorConfirmacionGeneracionGlamour(Exception):
    """Error al reconstruir el procesamiento Glamour para escritura."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA_GLAMOUR


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
        raise ErrorConfirmacionGeneracionGlamour(
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
        raise ErrorConfirmacionGeneracionGlamour(
            f"El campo {campo!r} no es entero."
        ) from error


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


def serializar_rubros_no_mapeados(
    rubros,
) -> list[dict[str, str]]:
    resultado: list[dict[str, str]] = []
    for item in rubros or ():
        if isinstance(item, dict):
            etiqueta = str(item.get("etiqueta") or "").strip()
            monto = item.get("monto", "0")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            etiqueta = str(item[0]).strip()
            monto = item[1]
        else:
            continue
        if not etiqueta:
            continue
        valor = monto if isinstance(monto, Decimal) else _a_decimal(
            monto,
            "monto",
        )
        resultado.append(
            {
                "etiqueta": etiqueta,
                "monto": _decimal_a_texto(valor),
            }
        )
    return resultado


def serializar_linea_preparada_glamour(
    linea: LineaPreparadaGlamour,
) -> dict[str, Any]:
    despacho = linea.despacho
    return {
        "contenedor": despacho.contenedor,
        "nave": despacho.barco,
        "destino": despacho.puerto_destino,
        "tipo_fruta": linea.tipo_fruta,
        "carton": despacho.carton,
        "calibre": linea.calibre,
        "total_cajas": despacho.total_cajas,
        "precio_venta_eur": _decimal_a_texto(
            linea.precio_venta_eur
        ),
        "gastos": {
            columna: _decimal_a_texto(
                linea.gastos.get(columna, Decimal("0"))
            )
            for columna in COLUMNAS_GASTO
        },
    }


def serializar_lineas_preparadas_glamour(lineas) -> list[dict[str, Any]]:
    return [
        serializar_linea_preparada_glamour(linea)
        for linea in lineas
    ]


def serializar_resumen_gastos_glamour(
    resumen: Mapping[str, Decimal] | None,
) -> dict[str, str]:
    if not resumen:
        return {columna: "0" for columna in COLUMNAS_GASTO}
    return {
        columna: _decimal_a_texto(
            resumen.get(columna, Decimal("0"))
            if isinstance(
                resumen.get(columna, Decimal("0")),
                Decimal,
            )
            else _a_decimal(
                resumen.get(columna, "0"),
                columna,
            )
        )
        for columna in COLUMNAS_GASTO
    }


def reconstruir_resultado_para_escritura_glamour(
    *,
    factura_corta: str,
    semana: int,
    anio: int,
    semana_texto: str,
    destino_final: str | None,
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...],
    resumen_gastos: Mapping[str, object] | None = None,
    total_cajas_liquidacion: int | Decimal = 0,
    total_cajas_despachos: int | Decimal = 0,
    total_venta_eur: int | Decimal = 0,
    total_gastos_eur: int | Decimal = 0,
    destinos_despachos: list[str]
    | tuple[str, ...]
    | None = None,
) -> ResultadoPreparacionGlamour:
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionGlamour(
            "No hay líneas preparadas guardadas."
        )

    gastos_globales = _gastos_desde_dict(resumen_gastos)
    destino = (destino_final or "").strip().upper()
    if not destino:
        raise ErrorConfirmacionGeneracionGlamour(
            "No hay destino final."
        )

    factura = (factura_corta or "").strip()
    if not factura:
        raise ErrorConfirmacionGeneracionGlamour(
            "No hay factura corta guardada."
        )

    semana_txt = (semana_texto or "").strip() or (
        f"{int(semana):02d}-{int(anio)}"
    )

    lineas: list[LineaPreparadaGlamour] = []
    contenedores: list[str] = []
    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionGlamour(
                f"Línea preparada inválida en posición {indice}."
            )

        contenedor = str(cruda.get("contenedor") or "").strip()
        nave = str(cruda.get("nave") or "").strip()
        carton = str(cruda.get("carton") or "").strip()
        tipo_fruta = str(cruda.get("tipo_fruta") or "ESPECIAL").strip()
        destino_linea = str(cruda.get("destino") or "").strip()
        calibre = _a_entero(cruda.get("calibre"), "calibre")
        total_cajas = _a_entero(
            cruda.get("total_cajas"),
            "total_cajas",
        )
        precio = _a_decimal(
            cruda.get("precio_venta_eur"),
            "precio_venta_eur",
        )
        gastos = _gastos_desde_dict(
            cruda.get("gastos") or gastos_globales
        )

        if not contenedor:
            raise ErrorConfirmacionGeneracionGlamour(
                f"Línea {indice} incompleta (contenedor)."
            )

        despacho = LineaDespachoGlamour(
            fila_excel=indice,
            semana=int(semana),
            anio=int(anio),
            semana_texto=semana_txt,
            contenedor=contenedor,
            cliente=CLIENTE_GLAMOUR,
            barco=nave,
            puerto_destino=destino_linea or destino,
            tipo_empaque=tipo_fruta,
            carton=carton,
            calibre=calibre,
            total_cajas=total_cajas,
            factura=factura,
            factura_corta=factura,
        )
        lineas.append(
            LineaPreparadaGlamour(
                despacho=despacho,
                tipo_fruta=tipo_fruta,
                calibre=calibre,
                precio_venta_eur=precio,
                gastos=dict(gastos),
            )
        )
        if contenedor not in contenedores:
            contenedores.append(contenedor)

    destinos = tuple(
        str(item).strip().upper()
        for item in (destinos_despachos or [])
        if str(item).strip()
    ) or (destino,)

    total_liq = _a_entero(
        total_cajas_liquidacion or 0,
        "total_cajas_liquidacion",
    )
    total_desp = _a_entero(
        total_cajas_despachos or 0,
        "total_cajas_despachos",
    )
    if total_desp <= 0:
        total_desp = sum(
            linea.despacho.total_cajas for linea in lineas
        )

    liquidacion = LiquidacionGlamour(
        archivo="",
        factura_corta=factura,
        referencia="",
        destino_pdf=destino,
        contenedores=tuple(contenedores),
        productos=(),
        gastos=dict(gastos_globales),
        rubros_mapeados=(),
        rubros_no_mapeados=(),
        total_cajas=total_liq or total_desp,
        total_venta_eur=_a_decimal(
            total_venta_eur or 0,
            "total_venta_eur",
        ),
        total_importe_neto_eur=None,
        comision_pct=None,
    )
    despachos = ResultadoMatcherGlamour(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_GLAMOUR,
        factura_corta_buscada=factura,
        semana=int(semana),
        anio=int(anio),
        destino_buscado=destino,
        semana_texto=semana_txt,
        lineas=tuple(linea.despacho for linea in lineas),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
        destinos=destinos,
    )
    validacion = ResultadoValidacionGlamour(
        es_valido=True,
        destino_final=destino,
        destinos_despachos=destinos,
        total_cajas_liquidacion=total_liq or total_desp,
        total_cajas_despachos=total_desp,
        total_venta_eur=_a_decimal(
            total_venta_eur or 0,
            "total_venta_eur",
        ),
        total_gastos_eur=_a_decimal(
            total_gastos_eur or 0,
            "total_gastos_eur",
        ),
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
        resumen_gastos=dict(gastos_globales),
    )
    return ResultadoPreparacionGlamour(
        estado="listo",
        puede_escribir=True,
        destino_final=destino,
        origen_destino="despachos",
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )
