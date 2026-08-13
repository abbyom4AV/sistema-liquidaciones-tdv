from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.tdv_europa.extractor import (
    COLUMNAS_GASTO,
    LiquidacionTdvEuropa,
)
from services.tdv_europa.matcher import (
    CLIENTE_TDV_EUROPA_PREFIX,
    ResultadoMatcherTdvEuropa,
)
from services.tdv_europa.processor import ResultadoPreparacionTdvEuropa
from services.tdv_europa.validator import (
    AtribucionMermaTdvEuropa,
    LineaPreparadaTdvEuropa,
    ResultadoValidacionTdvEuropa,
)
from services.tdv_europa.writer import NOMBRE_DESCARGA


class ErrorConfirmacionGeneracionTdvEuropa(Exception):
    """Error al reconstruir el procesamiento TDV Europa para escritura."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA


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
        raise ErrorConfirmacionGeneracionTdvEuropa(
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
        raise ErrorConfirmacionGeneracionTdvEuropa(
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


def serializar_atribucion_merma(
    atribucion: AtribucionMermaTdvEuropa,
) -> dict[str, str]:
    return {
        "contenedor": atribucion.contenedor,
        "calibre_raw": atribucion.calibre_raw,
        "carton": atribucion.carton,
        "cliente": atribucion.cliente,
        "cajas_merma": _decimal_a_texto(atribucion.cajas_merma),
        "cajas_netas": _decimal_a_texto(atribucion.cajas_netas),
        "cajas_brutas": _decimal_a_texto(atribucion.cajas_brutas),
    }


def serializar_atribuciones_merma(atribuciones) -> list[dict[str, str]]:
    return [
        serializar_atribucion_merma(item)
        for item in atribuciones or ()
    ]


def serializar_linea_preparada_tdv_europa(
    linea: LineaPreparadaTdvEuropa,
) -> dict[str, Any]:
    return {
        "contenedor": linea.contenedor,
        "cliente_liq": linea.cliente_liq,
        "nave": linea.nave,
        "destino": linea.destino,
        "destino_log": linea.destino_log,
        "tipo_fruta": linea.tipo_fruta,
        "carton": linea.carton,
        "calibre": linea.calibre,
        "calibre_raw": linea.calibre_raw,
        "total_cajas": _decimal_a_texto(linea.total_cajas),
        "merma": _decimal_a_texto(linea.merma),
        "cajas_netas": _decimal_a_texto(linea.cajas_netas),
        "precio_venta_eur": format(linea.precio_venta_eur, "f"),
        "gasto_puerto": _decimal_a_texto(linea.gasto_puerto),
        "gasto_trans": _decimal_a_texto(linea.gasto_trans),
        "gasto_handl": _decimal_a_texto(linea.gasto_handl),
        "gasto_inspeccion": _decimal_a_texto(
            linea.gasto_inspeccion
        ),
        "gasto_customs": _decimal_a_texto(linea.gasto_customs),
        "reclamos_irmadona": _decimal_a_texto(
            linea.reclamos_irmadona
        ),
        "reclamos_mercado": _decimal_a_texto(
            linea.reclamos_mercado
        ),
        "comision_euros": _decimal_a_texto(linea.comision_euros),
    }


def serializar_lineas_preparadas_tdv_europa(lineas) -> list[dict[str, Any]]:
    return [
        serializar_linea_preparada_tdv_europa(linea)
        for linea in lineas
    ]


def serializar_resumen_gastos_tdv_europa(
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


def reconstruir_resultado_para_escritura_tdv_europa(
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
    total_merma: int | Decimal = 0,
    reclamos_irmadona: int | Decimal = 0,
    reclamos_mercado: int | Decimal = 0,
    comision_eur: int | Decimal = 0,
    destinos_despachos: list[str]
    | tuple[str, ...]
    | None = None,
) -> ResultadoPreparacionTdvEuropa:
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionTdvEuropa(
            "No hay líneas preparadas guardadas."
        )

    gastos_globales = _gastos_desde_dict(resumen_gastos)
    destino = (destino_final or "").strip().upper()
    if not destino:
        raise ErrorConfirmacionGeneracionTdvEuropa(
            "No hay destino final."
        )

    factura = (factura_corta or "").strip()
    if not factura:
        raise ErrorConfirmacionGeneracionTdvEuropa(
            "No hay factura corta guardada."
        )

    semana_txt = (semana_texto or "").strip() or (
        f"{int(semana):02d}-{int(anio)}"
    )

    lineas: list[LineaPreparadaTdvEuropa] = []
    contenedores: list[str] = []
    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionTdvEuropa(
                f"Línea preparada inválida en posición {indice}."
            )

        contenedor = str(cruda.get("contenedor") or "").strip()
        if not contenedor:
            raise ErrorConfirmacionGeneracionTdvEuropa(
                f"Línea {indice} incompleta (contenedor)."
            )

        lineas.append(
            LineaPreparadaTdvEuropa(
                semana=int(semana),
                anio=int(anio),
                semana_texto=semana_txt,
                cliente_liq=str(
                    cruda.get("cliente_liq") or ""
                ).strip(),
                nave=str(cruda.get("nave") or "").strip(),
                contenedor=contenedor,
                destino=str(
                    cruda.get("destino") or destino
                ).strip(),
                destino_log=str(
                    cruda.get("destino_log")
                    or cruda.get("destino")
                    or destino
                ).strip(),
                tipo_fruta=str(
                    cruda.get("tipo_fruta") or "Especial"
                ).strip(),
                carton=str(cruda.get("carton") or "").strip(),
                producto_liq="",
                calibre=_a_entero(cruda.get("calibre"), "calibre"),
                calibre_raw=str(
                    cruda.get("calibre_raw")
                    or cruda.get("calibre")
                    or ""
                ).strip(),
                total_cajas=_a_decimal(
                    cruda.get("total_cajas"),
                    "total_cajas",
                ),
                merma=_a_decimal(
                    cruda.get("merma"),
                    "merma",
                ),
                cajas_netas=_a_decimal(
                    cruda.get("cajas_netas"),
                    "cajas_netas",
                ),
                precio_venta_eur=_a_decimal(
                    cruda.get("precio_venta_eur"),
                    "precio_venta_eur",
                ),
                gasto_puerto=_a_decimal(
                    cruda.get("gasto_puerto")
                    or gastos_globales["Gasto Puerto"],
                    "gasto_puerto",
                ),
                gasto_trans=_a_decimal(
                    cruda.get("gasto_trans")
                    or gastos_globales["Gasto Trans"],
                    "gasto_trans",
                ),
                gasto_handl=_a_decimal(
                    cruda.get("gasto_handl")
                    or gastos_globales["Gasto Handl"],
                    "gasto_handl",
                ),
                gasto_inspeccion=_a_decimal(
                    cruda.get("gasto_inspeccion")
                    or gastos_globales["G.Inspección"],
                    "gasto_inspeccion",
                ),
                gasto_customs=_a_decimal(
                    cruda.get("gasto_customs")
                    or gastos_globales["G.Customs Duties"],
                    "gasto_customs",
                ),
                reclamos_irmadona=_a_decimal(
                    cruda.get("reclamos_irmadona")
                    or reclamos_irmadona,
                    "reclamos_irmadona",
                ),
                reclamos_mercado=_a_decimal(
                    cruda.get("reclamos_mercado")
                    or reclamos_mercado,
                    "reclamos_mercado",
                ),
                comision_euros=_a_decimal(
                    cruda.get("comision_euros") or comision_eur,
                    "comision_euros",
                ),
                factura_corta=factura,
                venta_bruta_eur=Decimal("0"),
            )
        )
        if contenedor not in contenedores:
            contenedores.append(contenedor)

    destinos = tuple(
        str(item).strip().upper()
        for item in (destinos_despachos or [])
        if str(item).strip()
    ) or (destino,)

    total_liq = _a_decimal(
        total_cajas_liquidacion or 0,
        "total_cajas_liquidacion",
    )
    total_desp = _a_entero(
        total_cajas_despachos or 0,
        "total_cajas_despachos",
    )
    if total_desp <= 0:
        total_desp = int(
            sum(linea.total_cajas for linea in lineas)
        )

    liquidacion = LiquidacionTdvEuropa(
        archivo="",
        semana=int(semana),
        anio=int(anio),
        destino_pdf=destino,
        nave=lineas[0].nave if lineas else "",
        factura_completa=factura,
        factura_corta=factura,
        lineas=(),
        mermas=(),
        gastos=dict(gastos_globales),
        comision_eur=_a_decimal(comision_eur or 0, "comision_eur"),
        total_cajas_netas=Decimal("0"),
        total_venta_eur=_a_decimal(
            total_venta_eur or 0,
            "total_venta_eur",
        ),
        reclamos=(),
        rubros_no_mapeados=(),
    )
    despachos = ResultadoMatcherTdvEuropa(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_TDV_EUROPA_PREFIX,
        factura_corta_buscada=factura,
        semana=int(semana),
        anio=int(anio),
        destino_buscado=destino,
        semana_texto=semana_txt,
        lineas=(),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
        destinos=destinos,
        naves=(),
    )
    validacion = ResultadoValidacionTdvEuropa(
        es_valido=True,
        destino_final=destino,
        destinos_despachos=destinos,
        total_cajas_brutas_liquidacion=total_liq or Decimal(total_desp),
        total_cajas_netas_liquidacion=Decimal("0"),
        total_cajas_despachos=total_desp,
        total_venta_eur=_a_decimal(
            total_venta_eur or 0,
            "total_venta_eur",
        ),
        total_gastos_eur=_a_decimal(
            total_gastos_eur or 0,
            "total_gastos_eur",
        ),
        total_merma=_a_decimal(total_merma or 0, "total_merma"),
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
        atribuciones_merma=(),
        resumen_gastos=dict(gastos_globales),
        reclamos_irmadona=_a_decimal(
            reclamos_irmadona or 0,
            "reclamos_irmadona",
        ),
        reclamos_mercado=_a_decimal(
            reclamos_mercado or 0,
            "reclamos_mercado",
        ),
        comision_eur=_a_decimal(comision_eur or 0, "comision_eur"),
    )
    return ResultadoPreparacionTdvEuropa(
        estado="listo",
        puede_escribir=True,
        destino_final=destino,
        origen_destino="ui",
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )
