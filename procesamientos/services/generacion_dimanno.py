from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.dimanno.extractor import LiquidacionDimanno
from services.dimanno.matcher import LineaDespacho, ResultadoMatcher
from services.dimanno.processor import (
    OrigenDestino,
    ResultadoPreparacionDimanno,
    limpiar_destino_confirmado,
)
from services.dimanno.validator import (
    LineaPreparada,
    ResultadoValidacion,
)

RUBROS_GASTOS_ESPERADOS: tuple[str, ...] = (
    "Comisión",
    "Flete Eu",
    "Control calidad Eu",
    "THC",
    "Transporte",
    "Aduanas",
)

CAMPOS_LINEA_ESCRITURA: tuple[str, ...] = (
    "contenedor",
    "nave",
    "cliente",
    "tipo_fruta",
    "carton",
    "calibre",
    "total_cajas",
    "precio_venta_eur",
)


class ErrorConfirmacionGeneracionDimanno(Exception):
    """Error al aplicar destino o gastos confirmados."""


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
        raise ErrorConfirmacionGeneracionDimanno(
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
        raise ErrorConfirmacionGeneracionDimanno(
            f"El campo {campo!r} no es entero."
        ) from error


def serializar_gastos_aplicados(
    gastos: Mapping[str, Decimal],
) -> dict[str, str]:
    serializados: dict[str, str] = {}
    for rubro in RUBROS_GASTOS_ESPERADOS:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionDimanno(
                f"Falta el rubro de gasto {rubro!r}."
            )
        valor = gastos[rubro]
        if not isinstance(valor, Decimal):
            raise ErrorConfirmacionGeneracionDimanno(
                f"El gasto {rubro!r} debe ser Decimal."
            )
        serializados[rubro] = _decimal_a_texto(valor)
    return serializados


def deserializar_gastos_aplicados(
    gastos: Mapping[str, object],
) -> dict[str, Decimal]:
    resultado: dict[str, Decimal] = {}
    for rubro in RUBROS_GASTOS_ESPERADOS:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionDimanno(
                f"Falta el rubro de gasto {rubro!r}."
            )
        resultado[rubro] = _a_decimal(gastos[rubro], rubro)
    return resultado


def lineas_completas_para_escritura(
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...]
    | None,
) -> bool:
    """True si las líneas guardadas traen lo necesario para escribir."""
    if not lineas_preparadas:
        return False
    for linea in lineas_preparadas:
        if not isinstance(linea, dict):
            return False
        for campo in CAMPOS_LINEA_ESCRITURA:
            if campo not in linea or linea.get(campo) in (
                None,
                "",
            ):
                return False
    return True


def aplicar_confirmaciones_a_procesamiento(
    procesamiento_motor: ResultadoPreparacionDimanno,
    destino_final: str,
    gastos_aplicados: Mapping[str, object],
    origen_destino: str | None = None,
) -> ResultadoPreparacionDimanno:
    if not procesamiento_motor.validacion.es_valido:
        raise ErrorConfirmacionGeneracionDimanno(
            "El procesamiento dejó de cumplir las "
            "validaciones requeridas."
        )

    destino = limpiar_destino_confirmado(destino_final)
    if destino is None:
        raise ErrorConfirmacionGeneracionDimanno(
            "No hay un destino final confirmado."
        )

    gastos = deserializar_gastos_aplicados(gastos_aplicados)
    liquidacion = replace(
        procesamiento_motor.liquidacion,
        gastos=gastos,
    )

    origen_valido: OrigenDestino | None = None
    if origen_destino in {
        "coincidente",
        "despachos",
        "liquidacion",
        "manual",
    }:
        origen_valido = origen_destino  # type: ignore[assignment]
    elif procesamiento_motor.origen_destino is not None:
        origen_valido = procesamiento_motor.origen_destino
    else:
        origen_valido = "manual"

    return replace(
        procesamiento_motor,
        estado="listo",
        puede_escribir=True,
        destino_final=destino,
        origen_destino=origen_valido,
        liquidacion=liquidacion,
    )


def reconstruir_resultado_para_escritura_dimanno(
    *,
    factura_corta: str,
    semana: int,
    anio: int,
    nombre_hoja: str,
    destino_final: str | None,
    origen_destino: str | None,
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...],
    gastos_aplicados: Mapping[str, object],
    total_cajas_liquidacion: int | Decimal = 0,
    total_cajas_despachos: int | Decimal = 0,
    destino_liquidacion: str = "",
    destinos_despachos: list[str]
    | tuple[str, ...]
    | None = None,
) -> ResultadoPreparacionDimanno:
    """
    Arma el resultado listo para escribir Excel sin re-leer
    liquidación ni Despachos.
    """
    if not lineas_completas_para_escritura(lineas_preparadas):
        raise ErrorConfirmacionGeneracionDimanno(
            "Las líneas preparadas guardadas están incompletas "
            "para escribir sin re-extraer."
        )

    gastos = deserializar_gastos_aplicados(gastos_aplicados)
    destino = limpiar_destino_confirmado(destino_final)
    if destino is None:
        raise ErrorConfirmacionGeneracionDimanno(
            "No hay un destino final confirmado."
        )

    factura = (factura_corta or "").strip()
    if not factura:
        raise ErrorConfirmacionGeneracionDimanno(
            "No hay factura corta guardada."
        )

    origen_valido: OrigenDestino = "manual"
    if origen_destino in {
        "coincidente",
        "despachos",
        "liquidacion",
        "manual",
    }:
        origen_valido = origen_destino  # type: ignore[assignment]

    lineas: list[LineaPreparada] = []
    contenedores: list[str] = []
    for indice, cruda in enumerate(lineas_preparadas, start=1):
        contenedor = str(cruda.get("contenedor") or "").strip()
        nave = str(cruda.get("nave") or "").strip()
        cliente = str(cruda.get("cliente") or "DI MANNO").strip()
        tipo_fruta = str(cruda.get("tipo_fruta") or "").strip()
        carton = str(cruda.get("carton") or "").strip()
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
        semana_linea = _a_entero(
            cruda.get("semana", semana),
            "semana",
        )
        anio_linea = _a_entero(
            cruda.get("anio", anio),
            "anio",
        )
        factura_linea = str(
            cruda.get("factura") or factura
        ).strip()
        factura_corta_linea = str(
            cruda.get("factura_corta") or factura
        ).strip()

        despacho = LineaDespacho(
            fila_excel=indice,
            semana=semana_linea,
            anio=anio_linea,
            contenedor=contenedor,
            cliente=cliente,
            barco=nave,
            puerto_destino=destino_linea or destino,
            tipo_empaque=tipo_fruta,
            carton=carton,
            calibre=calibre,
            total_cajas=total_cajas,
            factura=factura_linea,
            factura_corta=factura_corta_linea,
        )
        lineas.append(
            LineaPreparada(
                despacho=despacho,
                tipo_fruta=tipo_fruta,
                calibre=calibre,
                precio_venta_eur=precio,
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

    liquidacion = LiquidacionDimanno(
        archivo="",
        hoja=nombre_hoja or "",
        factura_corta=factura,
        semana=int(semana),
        contenedores=tuple(contenedores),
        naviera="",
        destino=destino_liquidacion or destino,
        total_cajas=total_liq or total_desp,
        total_venta_eur=Decimal("0"),
        productos=(),
        gastos=dict(gastos),
        rubros_no_mapeados=(),
    )
    despachos = ResultadoMatcher(
        archivo="",
        hoja="",
        cliente_buscado="DI MANNO",
        anio_buscado=int(anio),
        semana_buscada=int(semana),
        factura_corta_buscada=factura,
        lineas=tuple(linea.despacho for linea in lineas),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
    )
    validacion = ResultadoValidacion(
        es_valido=True,
        requiere_resolver_destino=False,
        destino_liquidacion=destino_liquidacion or destino,
        destinos_despachos=destinos,
        total_cajas_liquidacion=total_liq or total_desp,
        total_cajas_despachos=total_desp,
        total_venta_informado_eur=Decimal("0"),
        total_venta_calculado_eur=Decimal("0"),
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
    )
    return ResultadoPreparacionDimanno(
        estado="listo",
        puede_escribir=True,
        destino_final=destino,
        origen_destino=origen_valido,
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )


NOMBRE_DESCARGA_DIMANNO = "DIMANNO Liquidaciones v2.1.xlsx"


def construir_nombre_descarga(
    *,
    anio: int | None = None,
    semana: int | None = None,
    factura_corta: str | None = None,
) -> str:
    """Nombre fijo de descarga visible al usuario."""
    del anio, semana, factura_corta
    return NOMBRE_DESCARGA_DIMANNO
