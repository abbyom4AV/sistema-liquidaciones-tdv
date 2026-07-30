from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.master.extractor import LiquidacionMaster
from services.master.matcher import (
    CLIENTE_MASTER,
    LineaDespachoMaster,
    ResultadoMatcherMaster,
)
from services.master.processor import ResultadoPreparacionMaster
from services.master.validator import (
    LineaPreparadaMaster,
    ResultadoValidacionMaster,
)
from services.master.writer import NOMBRE_DESCARGA_MASTER

RUBROS_GASTOS_MASTER: tuple[str, ...] = (
    "LC Euros",
    "Cust.C Euros",
    "Import.D Euros",
    "Ener&Demur. Euros",
    "Inspection Euros",
    "Transport.P-W Euros",
    "Transport C. Euros",
    "Relabelling Euros",
    "Comision Euros",
)


class ErrorConfirmacionGeneracionMaster(Exception):
    """Error al aplicar gastos confirmados Master."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA_MASTER


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
        raise ErrorConfirmacionGeneracionMaster(
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
        raise ErrorConfirmacionGeneracionMaster(
            f"El campo {campo!r} no es entero."
        ) from error


def serializar_gastos_aplicados_master(
    gastos: Mapping[str, Decimal],
) -> dict[str, str]:
    serializados: dict[str, str] = {}
    for rubro in RUBROS_GASTOS_MASTER:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionMaster(
                f"Falta el rubro de gasto {rubro!r}."
            )
        valor = gastos[rubro]
        if not isinstance(valor, Decimal):
            raise ErrorConfirmacionGeneracionMaster(
                f"El gasto {rubro!r} debe ser Decimal."
            )
        serializados[rubro] = _decimal_a_texto(valor)
    return serializados


def deserializar_gastos_aplicados_master(
    gastos: Mapping[str, object],
) -> dict[str, Decimal]:
    resultado: dict[str, Decimal] = {}
    for rubro in RUBROS_GASTOS_MASTER:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionMaster(
                f"Falta el rubro de gasto {rubro!r}."
            )
        resultado[rubro] = _a_decimal(gastos[rubro], rubro)
    return resultado


def aplicar_confirmaciones_a_procesamiento_master(
    procesamiento_motor: ResultadoPreparacionMaster,
    gastos_aplicados: Mapping[str, object],
    destino_final: str | None = None,
) -> ResultadoPreparacionMaster:
    if not procesamiento_motor.validacion.es_valido:
        raise ErrorConfirmacionGeneracionMaster(
            "El procesamiento ya no es válido."
        )

    gastos = deserializar_gastos_aplicados_master(
        gastos_aplicados
    )
    liquidacion = replace(
        procesamiento_motor.liquidacion,
        gastos=gastos,
    )

    lineas = tuple(
        LineaPreparadaMaster(
            despacho=linea.despacho,
            tipo_fruta=linea.tipo_fruta,
            variante=linea.variante,
            calibre=linea.calibre,
            precio_venta_eur=linea.precio_venta_eur,
            merma=linea.merma,
            gastos=dict(gastos),
        )
        for linea in procesamiento_motor.validacion.lineas_preparadas
    )
    validacion = replace(
        procesamiento_motor.validacion,
        lineas_preparadas=lineas,
    )

    destino = (
        (destino_final or "").strip().upper()
        or procesamiento_motor.destino_final
    )
    if not destino:
        raise ErrorConfirmacionGeneracionMaster(
            "No hay destino final."
        )

    return replace(
        procesamiento_motor,
        estado="listo",
        puede_escribir=True,
        destino_final=destino,
        origen_destino="despachos",
        liquidacion=liquidacion,
        validacion=validacion,
    )


def reconstruir_resultado_para_escritura_master(
    *,
    factura_corta: str,
    semana: int,
    anio: int,
    semana_texto: str,
    destino_final: str | None,
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...],
    gastos_aplicados: Mapping[str, object],
    total_cajas_liquidacion: int | Decimal = 0,
    total_cajas_despachos: int | Decimal = 0,
    destinos_despachos: list[str]
    | tuple[str, ...]
    | None = None,
) -> ResultadoPreparacionMaster:
    """
    Arma el resultado listo para escribir Excel sin re-leer
    PDF ni Despachos: usa líneas y gastos ya persistidos.
    """
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionMaster(
            "No hay líneas preparadas guardadas."
        )

    gastos = deserializar_gastos_aplicados_master(
        gastos_aplicados
    )
    destino = (destino_final or "").strip().upper()
    if not destino:
        raise ErrorConfirmacionGeneracionMaster(
            "No hay destino final."
        )

    factura = (factura_corta or "").strip()
    if not factura:
        raise ErrorConfirmacionGeneracionMaster(
            "No hay factura corta guardada."
        )

    semana_txt = (semana_texto or "").strip() or (
        f"{int(semana):02d}-{int(anio)}"
    )

    lineas: list[LineaPreparadaMaster] = []
    contenedores: list[str] = []
    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionMaster(
                f"Línea preparada inválida en posición {indice}."
            )

        contenedor = str(cruda.get("contenedor") or "").strip()
        nave = str(cruda.get("nave") or "").strip()
        carton = str(cruda.get("carton") or "").strip()
        tipo_fruta = str(cruda.get("tipo_fruta") or "").strip()
        variante = str(cruda.get("variante") or "").strip()
        destino_linea = str(cruda.get("destino") or "").strip()
        calibre = _a_entero(cruda.get("calibre"), "calibre")
        total_cajas = _a_entero(
            cruda.get("total_cajas"),
            "total_cajas",
        )
        merma = _a_entero(cruda.get("merma") or 0, "merma")
        precio = _a_decimal(
            cruda.get("precio_venta_eur"),
            "precio_venta_eur",
        )

        if not contenedor or not tipo_fruta or not variante:
            raise ErrorConfirmacionGeneracionMaster(
                f"Línea {indice} incompleta "
                "(contenedor/tipo/variante)."
            )

        despacho = LineaDespachoMaster(
            fila_excel=indice,
            semana=int(semana),
            anio=int(anio),
            semana_texto=semana_txt,
            contenedor=contenedor,
            cliente=CLIENTE_MASTER,
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
            LineaPreparadaMaster(
                despacho=despacho,
                tipo_fruta=tipo_fruta,
                variante=variante,
                calibre=calibre,
                precio_venta_eur=precio,
                merma=merma,
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

    liquidacion = LiquidacionMaster(
        archivo="",
        factura_corta=factura,
        referencia="",
        nave="",
        contenedores=tuple(contenedores),
        total_boxes=total_liq or total_desp,
        total_sold_boxes=total_liq or total_desp,
        comision_eur=gastos.get(
            "Comision Euros",
            Decimal("0"),
        ),
        total_venta_eur=Decimal("0"),
        total_costos_eur=Decimal("0"),
        productos=(),
        gastos=dict(gastos),
        rubros_no_mapeados=(),
    )
    despachos = ResultadoMatcherMaster(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_MASTER,
        factura_corta_buscada=factura,
        semana=int(semana),
        anio=int(anio),
        semana_texto=semana_txt,
        lineas=tuple(linea.despacho for linea in lineas),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
        destinos=destinos,
    )
    validacion = ResultadoValidacionMaster(
        es_valido=True,
        destino_final=destino,
        destinos_despachos=destinos,
        total_cajas_liquidacion=total_liq or total_desp,
        total_cajas_despachos=total_desp,
        total_venta_eur=Decimal("0"),
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
    )
    return ResultadoPreparacionMaster(
        estado="listo",
        puede_escribir=True,
        destino_final=destino,
        origen_destino="despachos",
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )
