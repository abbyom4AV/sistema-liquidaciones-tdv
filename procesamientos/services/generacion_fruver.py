from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from services.fruver.extractor import LiquidacionFruver
from services.fruver.matcher import (
    CLIENTE_FRUVER,
    ResultadoMatcherFruver,
)
from services.fruver.processor import ResultadoPreparacionFruver
from services.fruver.validator import (
    LineaPreparadaFruver,
    ResultadoValidacionFruver,
)
from services.fruver.writer import NOMBRE_DESCARGA_FRUVER


class ErrorConfirmacionGeneracionFruver(Exception):
    """Error al reconstruir el procesamiento FRU&VER para escritura."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA_FRUVER


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
        raise ErrorConfirmacionGeneracionFruver(
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
        raise ErrorConfirmacionGeneracionFruver(
            f"El campo {campo!r} no es entero."
        ) from error


def serializar_linea_preparada_fruver(
    linea: LineaPreparadaFruver,
) -> dict[str, Any]:
    return {
        "semana": linea.semana,
        "anio": linea.anio,
        "semana_texto": linea.semana_texto,
        "cliente": linea.cliente,
        "nave": linea.nave,
        "contenedor": linea.contenedor,
        "destino": linea.destino,
        "tipo_fruta": linea.tipo_fruta,
        "calibre": linea.calibre,
        "total_cajas": _decimal_a_texto(linea.total_cajas),
        "carton": linea.carton,
        "demora_eur": _decimal_a_texto(linea.demora_eur),
        "portes_eur": _decimal_a_texto(linea.portes_eur),
        "gasto_puerto_eur": _decimal_a_texto(
            linea.gasto_puerto_eur
        ),
        "aduanas_eur": _decimal_a_texto(linea.aduanas_eur),
        "otros3": _decimal_a_texto(linea.otros3),
        "comision": _decimal_a_texto(linea.comision),
        "precio_venta_eur": format(linea.precio_venta_eur, "f"),
    }


def serializar_lineas_preparadas_fruver(lineas) -> list[dict[str, Any]]:
    return [
        serializar_linea_preparada_fruver(linea) for linea in lineas
    ]


def reconstruir_resultado_para_escritura_fruver(
    *,
    anio: int,
    semana: int,
    destino_ui: str,
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...],
    total_cajas_liquidacion: int | Decimal = 0,
    total_cajas_despachos: int | Decimal = 0,
    destinos_despachos: list[str] | tuple[str, ...] | None = None,
) -> ResultadoPreparacionFruver:
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionFruver(
            "No hay líneas preparadas guardadas."
        )

    semana_int = int(semana)
    anio_int = int(anio)
    semana_txt = f"{semana_int:02d}-{anio_int}"
    lineas: list[LineaPreparadaFruver] = []
    contenedores: list[str] = []

    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionFruver(
                f"Línea preparada inválida en posición {indice}."
            )
        contenedor = str(cruda.get("contenedor") or "").strip()
        if not contenedor:
            raise ErrorConfirmacionGeneracionFruver(
                f"Línea {indice} incompleta (contenedor)."
            )
        contenedores.append(contenedor)
        lineas.append(
            LineaPreparadaFruver(
                semana=_a_entero(
                    cruda.get("semana") or semana_int,
                    "semana",
                ),
                anio=_a_entero(cruda.get("anio") or anio_int, "anio"),
                semana_texto=str(
                    cruda.get("semana_texto") or semana_txt
                ).strip(),
                cliente=str(cruda.get("cliente") or "").strip(),
                nave=str(cruda.get("nave") or "").strip(),
                contenedor=contenedor,
                destino=str(
                    cruda.get("destino") or destino_ui or ""
                ).strip(),
                tipo_fruta=str(
                    cruda.get("tipo_fruta") or "Especial"
                ).strip(),
                calibre=_a_entero(cruda.get("calibre"), "calibre"),
                total_cajas=_a_decimal(
                    cruda.get("total_cajas"),
                    "total_cajas",
                ),
                carton=str(cruda.get("carton") or "").strip(),
                demora_eur=_a_decimal(
                    cruda.get("demora_eur") or 0, "demora_eur"
                ),
                portes_eur=_a_decimal(
                    cruda.get("portes_eur") or 0, "portes_eur"
                ),
                gasto_puerto_eur=_a_decimal(
                    cruda.get("gasto_puerto_eur") or 0,
                    "gasto_puerto_eur",
                ),
                aduanas_eur=_a_decimal(
                    cruda.get("aduanas_eur") or 0, "aduanas_eur"
                ),
                otros3=_a_decimal(cruda.get("otros3") or 0, "otros3"),
                comision=_a_decimal(
                    cruda.get("comision") or 0, "comision"
                ),
                precio_venta_eur=_a_decimal(
                    cruda.get("precio_venta_eur") or 0,
                    "precio_venta_eur",
                ),
            )
        )

    destinos = tuple(destinos_despachos or ())
    despachos = ResultadoMatcherFruver(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_FRUVER,
        semana=semana_int,
        anio=anio_int,
        destino_buscado=destino_ui,
        factura_corta_buscada="",
        semana_texto=semana_txt,
        lineas=(),
        total_cajas=int(total_cajas_despachos or 0),
        contenedores=tuple(dict.fromkeys(contenedores)),
        destinos=destinos,
        naves=(),
    )
    validacion = ResultadoValidacionFruver(
        es_valido=True,
        destino_ui=destino_ui,
        factura_corta="",
        destinos_despachos=destinos,
        total_cajas_liquidacion=Decimal(
            str(total_cajas_liquidacion or 0)
        ),
        total_cajas_despachos=Decimal(
            str(total_cajas_despachos or 0)
        ),
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
        resumen_gastos_contenedores=(),
    )
    dummy_liq = LiquidacionFruver(
        archivo="",
        contenedor=contenedores[0],
        factura="",
        factura_corta="",
        productos=(),
        comision=Decimal("0"),
        gastos={},
        flete_eur=Decimal("0"),
        total_cajas=Decimal("0"),
        total_venta_eur=Decimal("0"),
        total_gastos_eur=Decimal("0"),
        rubros_no_mapeados=(),
    )
    return ResultadoPreparacionFruver(
        estado="listo",
        puede_escribir=True,
        liquidaciones=(dummy_liq,),
        despachos=despachos,
        validacion=validacion,
    )
