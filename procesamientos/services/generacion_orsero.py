from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.orsero.extractor import LiquidacionOrsero
from services.orsero.matcher import (
    CLIENTE_ORSERO,
    LineaDespachoOrsero,
    ResultadoMatcherOrsero,
)
from services.orsero.processor import ResultadoPreparacionOrsero
from services.orsero.validator import (
    LineaPreparadaOrsero,
    ResultadoValidacionOrsero,
)
from services.orsero.writer import NOMBRE_DESCARGA_ORSERO

RUBROS_GASTOS_ORSERO: tuple[str, ...] = (
    "Costo en Origen Form.",
    "Inland Form.",
    "THC Origen Form.",
    "Flete Form.",
    "Insurance Form.",
    "THC Destino Form.",
    "Forwarding Form.",
    "Transport In Form.",
    "Comision Form",
)


class ErrorConfirmacionGeneracionOrsero(Exception):
    """Error al aplicar gastos confirmados Orsero."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA_ORSERO


def _decimal_a_texto(valor: Decimal) -> str:
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto if texto else "0"


def _a_decimal(valor: Any, campo: str) -> Decimal:
    try:
        if isinstance(valor, Decimal):
            return valor
        if isinstance(valor, (int, str)):
            return Decimal(str(valor))
        raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ErrorConfirmacionGeneracionOrsero(
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
        raise ErrorConfirmacionGeneracionOrsero(
            f"El campo {campo!r} no es entero."
        ) from error


def serializar_gastos_aplicados_orsero(
    gastos: Mapping[str, Decimal],
) -> dict[str, str]:
    serializados: dict[str, str] = {}
    for rubro in RUBROS_GASTOS_ORSERO:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionOrsero(
                f"Falta el rubro de gasto {rubro!r}."
            )
        valor = gastos[rubro]
        if not isinstance(valor, Decimal):
            raise ErrorConfirmacionGeneracionOrsero(
                f"El gasto {rubro!r} debe ser Decimal."
            )
        serializados[rubro] = _decimal_a_texto(valor)
    return serializados


def deserializar_gastos_aplicados_orsero(
    gastos: Mapping[str, object],
) -> dict[str, Decimal]:
    resultado: dict[str, Decimal] = {}
    for rubro in RUBROS_GASTOS_ORSERO:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionOrsero(
                f"Falta el rubro de gasto {rubro!r}."
            )
        resultado[rubro] = _a_decimal(gastos[rubro], rubro)
    return resultado


def reconstruir_resultado_para_escritura_orsero(
    *,
    anio: int,
    semana: int,
    nave_texto: str,
    tipo_cambio: Decimal,
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...],
    gastos_aplicados: Mapping[str, object],
    total_cajas_liquidacion: int | Decimal = 0,
    total_cajas_despachos: int | Decimal = 0,
    destinos_despachos: list[str]
    | tuple[str, ...]
    | None = None,
) -> ResultadoPreparacionOrsero:
    """
    Arma el resultado listo para escribir Excel sin re-leer el
    screenshot ni Despachos: usa líneas y gastos ya persistidos.
    """
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionOrsero(
            "No hay líneas preparadas guardadas."
        )

    gastos = deserializar_gastos_aplicados_orsero(
        gastos_aplicados
    )
    tipo_cambio_decimal = _a_decimal(
        tipo_cambio,
        "tipo_cambio",
    )
    semana_int = int(semana)
    anio_int = int(anio)
    semana_texto = f"{semana_int:02d}-{anio_int}"

    lineas: list[LineaPreparadaOrsero] = []
    contenedores: list[str] = []
    destinos_vistos: list[str] = []
    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionOrsero(
                f"Línea preparada inválida en posición {indice}."
            )

        contenedor = str(cruda.get("contenedor") or "").strip()
        nave = str(cruda.get("nave") or nave_texto or "").strip()
        carton = str(cruda.get("carton") or "").strip()
        tipo_fruta = (
            str(cruda.get("tipo_fruta") or "").strip()
            or "ESPECIAL"
        )
        destino = str(cruda.get("destino") or "").strip().upper()
        calibre = _a_entero(cruda.get("calibre"), "calibre")
        total_cajas = _a_entero(
            cruda.get("total_cajas"),
            "total_cajas",
        )
        precio = _a_decimal(
            cruda.get("precio_venta_eur") or 0,
            "precio_venta_eur",
        )
        precio_encontrado = bool(
            cruda.get("precio_encontrado", True)
        )

        if not contenedor or not destino:
            raise ErrorConfirmacionGeneracionOrsero(
                f"Línea {indice} incompleta "
                "(contenedor/destino)."
            )

        despacho = LineaDespachoOrsero(
            fila_excel=indice,
            semana=semana_int,
            anio=anio_int,
            semana_texto=semana_texto,
            contenedor=contenedor,
            cliente=CLIENTE_ORSERO,
            barco=nave,
            puerto_destino=destino,
            tipo_empaque=tipo_fruta,
            carton=carton,
            calibre=calibre,
            total_cajas=total_cajas,
        )
        lineas.append(
            LineaPreparadaOrsero(
                despacho=despacho,
                tipo_fruta=tipo_fruta,
                calibre=calibre,
                destino=destino,
                precio_venta_eur=precio,
                tipo_cambio_usd_eur=tipo_cambio_decimal,
                gastos=dict(gastos),
                precio_encontrado=precio_encontrado,
            )
        )
        if contenedor not in contenedores:
            contenedores.append(contenedor)
        if destino not in destinos_vistos:
            destinos_vistos.append(destino)

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
        total_desp = sum(
            linea.despacho.total_cajas for linea in lineas
        )

    liquidacion = LiquidacionOrsero(
        archivo="",
        nave_texto=nave_texto,
        semana=semana_int,
        tipo_cambio_usd_eur=tipo_cambio_decimal,
        total_cajas=total_liq or total_desp,
        precios=(),
        gastos=dict(gastos),
        texto_ocr="",
    )
    despachos = ResultadoMatcherOrsero(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_ORSERO,
        semana=semana_int,
        anio=anio_int,
        semana_texto=semana_texto,
        lineas=tuple(linea.despacho for linea in lineas),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
        destinos=destinos,
    )
    validacion = ResultadoValidacionOrsero(
        es_valido=True,
        destinos_despachos=destinos,
        total_cajas_liquidacion=total_liq or total_desp,
        total_cajas_despachos=total_desp,
        tipo_cambio_usd_eur=tipo_cambio_decimal,
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
    )
    return ResultadoPreparacionOrsero(
        estado="listo",
        puede_escribir=True,
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )
