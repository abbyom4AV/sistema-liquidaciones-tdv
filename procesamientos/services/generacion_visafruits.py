from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.visafruits.extractor import (
    COLUMNAS_GASTO,
    LiquidacionVisafruits,
)
from services.visafruits.matcher import (
    CLIENTE_VISAFRUITS_DESPACHOS,
    CLIENTE_VISAFRUITS_RAW,
    LineaDespachoVisafruits,
    ResultadoMatcherVisafruits,
)
from services.visafruits.processor import (
    ResultadoPreparacionVisafruits,
)
from services.visafruits.validator import (
    LineaPreparadaVisafruits,
    ResultadoValidacionVisafruits,
)
from services.visafruits.writer import (
    NOMBRE_DESCARGA_VISAFRUITS,
)

# La comisión también se puede corregir a mano, así que viaja junto
# a los gastos en el snapshot de la generación.
RUBROS_GASTOS_VISAFRUITS: tuple[str, ...] = (
    *COLUMNAS_GASTO,
    "Comision Euros",
)


class ErrorConfirmacionGeneracionVisafruits(Exception):
    """Error al reconstruir el procesamiento para escritura."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA_VISAFRUITS


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
        raise ErrorConfirmacionGeneracionVisafruits(
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
        raise ErrorConfirmacionGeneracionVisafruits(
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


def serializar_linea_preparada_visafruits(
    linea: LineaPreparadaVisafruits,
) -> dict[str, Any]:
    return {
        "fila_excel": linea.fila_excel,
        "semana": linea.semana,
        "anio": linea.anio,
        "semana_texto": linea.semana_texto,
        "cliente": linea.cliente,
        "nave": linea.nave,
        "contenedor": linea.contenedor,
        "destino": linea.destino,
        "tipo_fruta": linea.tipo_fruta,
        "calibre": linea.calibre,
        "total_cajas": linea.total_cajas,
        "carton": linea.carton,
        "es_vertical": linea.es_vertical,
        "precio_venta_eur": _decimal_a_texto(
            linea.precio_venta_eur
        ),
        "precio_encontrado": linea.precio_encontrado,
        "comision_eur": _decimal_a_texto(linea.comision_eur),
        "gastos": {
            columna: _decimal_a_texto(
                linea.gastos.get(columna, Decimal("0"))
            )
            for columna in COLUMNAS_GASTO
        },
    }


def serializar_lineas_preparadas_visafruits(
    lineas,
) -> list[dict[str, Any]]:
    return [
        serializar_linea_preparada_visafruits(linea)
        for linea in lineas
    ]


def serializar_resumen_gastos_visafruits(
    resumen: Mapping[str, Decimal] | None,
) -> dict[str, str]:
    if not resumen:
        return {columna: "0" for columna in COLUMNAS_GASTO}
    return {
        columna: _decimal_a_texto(
            resumen.get(columna, Decimal("0"))
        )
        for columna in COLUMNAS_GASTO
    }


def serializar_gastos_aplicados_visafruits(
    gastos: Mapping[str, Decimal],
) -> dict[str, str]:
    serializados: dict[str, str] = {}
    for rubro in RUBROS_GASTOS_VISAFRUITS:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionVisafruits(
                f"Falta el rubro de gasto {rubro!r}."
            )
        serializados[rubro] = _decimal_a_texto(
            _a_decimal(gastos[rubro], rubro)
        )
    return serializados


def deserializar_gastos_aplicados_visafruits(
    gastos: Mapping[str, object],
) -> dict[str, Decimal]:
    resultado: dict[str, Decimal] = {}
    for rubro in RUBROS_GASTOS_VISAFRUITS:
        if rubro not in gastos:
            raise ErrorConfirmacionGeneracionVisafruits(
                f"Falta el rubro de gasto {rubro!r}."
            )
        resultado[rubro] = _a_decimal(gastos[rubro], rubro)
    return resultado


def reconstruir_resultado_para_escritura_visafruits(
    *,
    anio: int,
    semana: int,
    destino_ui: str,
    factura_corta: str,
    lineas_preparadas: list[dict[str, Any]]
    | tuple[dict[str, Any], ...],
    total_cajas_liquidacion: int | Decimal = 0,
    total_cajas_despachos: int | Decimal = 0,
    total_venta_liquidacion_eur: int | Decimal = 0,
    gastos_aplicados: Mapping[str, object] | None = None,
) -> ResultadoPreparacionVisafruits:
    """Arma el resultado listo para escribir el Excel sin volver a
    leer el PDF ni Despachos: usa lo que ya quedó en la base.

    Los montos de `gastos_aplicados` mandan sobre los que trae cada
    línea, porque son los que el usuario confirmó o corrigió.
    """
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionVisafruits(
            "No hay líneas preparadas guardadas."
        )

    semana_int = int(semana)
    anio_int = int(anio)
    semana_texto_defecto = f"{semana_int:02d}-{anio_int}"

    aplicados = dict(gastos_aplicados or {})
    gastos_globales = _gastos_desde_dict(aplicados)
    comision_global: Decimal | None = None
    if "Comision Euros" in aplicados:
        comision_global = _a_decimal(
            aplicados["Comision Euros"],
            "Comision Euros",
        )

    lineas: list[LineaPreparadaVisafruits] = []
    despachos_lineas: list[LineaDespachoVisafruits] = []
    contenedores: list[str] = []
    destinos_vistos: list[str] = []
    naves: list[str] = []

    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionVisafruits(
                f"Línea preparada inválida en posición {indice}."
            )

        contenedor = (
            str(cruda.get("contenedor") or "").strip().upper()
        )
        destino = str(
            cruda.get("destino") or destino_ui or ""
        ).strip()
        if not contenedor or not destino:
            raise ErrorConfirmacionGeneracionVisafruits(
                f"Línea {indice} incompleta (contenedor/destino)."
            )

        nave = str(cruda.get("nave") or "").strip()
        carton = str(cruda.get("carton") or "").strip()
        tipo_fruta = str(cruda.get("tipo_fruta") or "").strip()
        calibre = _a_entero(cruda.get("calibre") or 0, "calibre")
        total_cajas = _a_entero(
            cruda.get("total_cajas"),
            "total_cajas",
        )
        semana_linea = _a_entero(
            cruda.get("semana") or semana_int,
            "semana",
        )
        anio_linea = _a_entero(
            cruda.get("anio") or anio_int,
            "anio",
        )
        semana_texto = (
            str(cruda.get("semana_texto") or "").strip()
            or semana_texto_defecto
        )
        cliente = (
            str(cruda.get("cliente") or "").strip()
            or CLIENTE_VISAFRUITS_RAW
        )
        precio = _a_decimal(
            cruda.get("precio_venta_eur") or 0,
            "precio_venta_eur",
        )
        comision = (
            comision_global
            if comision_global is not None
            else _a_decimal(
                cruda.get("comision_eur") or 0,
                "comision_eur",
            )
        )
        gastos = (
            dict(gastos_globales)
            if aplicados
            else _gastos_desde_dict(cruda.get("gastos"))
        )
        fila_excel = _a_entero(
            cruda.get("fila_excel") or indice,
            "fila_excel",
        )

        lineas.append(
            LineaPreparadaVisafruits(
                fila_excel=fila_excel,
                semana=semana_linea,
                anio=anio_linea,
                semana_texto=semana_texto,
                cliente=cliente,
                nave=nave,
                contenedor=contenedor,
                destino=destino,
                tipo_fruta=tipo_fruta,
                calibre=calibre,
                total_cajas=total_cajas,
                carton=carton,
                es_vertical=_a_bool(cruda.get("es_vertical")),
                precio_venta_eur=precio,
                precio_encontrado=_a_bool(
                    cruda.get("precio_encontrado")
                ),
                gastos=gastos,
                comision_eur=comision,
            )
        )
        despachos_lineas.append(
            LineaDespachoVisafruits(
                fila_excel=fila_excel,
                semana=semana_linea,
                anio=anio_linea,
                semana_texto=semana_texto,
                contenedor=contenedor,
                cliente=CLIENTE_VISAFRUITS_DESPACHOS,
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

    total_desp = _a_entero(
        total_cajas_despachos or 0,
        "total_cajas_despachos",
    )
    if total_desp <= 0:
        total_desp = sum(linea.total_cajas for linea in lineas)
    total_liq = (
        _a_entero(
            total_cajas_liquidacion or 0,
            "total_cajas_liquidacion",
        )
        or total_desp
    )
    total_venta_liq = _a_decimal(
        total_venta_liquidacion_eur or 0,
        "total_venta_liquidacion_eur",
    )
    total_venta_calc = sum(
        (
            linea.precio_venta_eur * Decimal(linea.total_cajas)
            for linea in lineas
        ),
        Decimal("0"),
    )
    comision_final = (
        comision_global
        if comision_global is not None
        else (lineas[0].comision_eur if lineas else Decimal("0"))
    )

    despachos = ResultadoMatcherVisafruits(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_VISAFRUITS_DESPACHOS,
        semana=semana_int,
        anio=anio_int,
        destino_buscado=destino_ui,
        factura_buscada=factura_corta,
        semana_texto=semana_texto_defecto,
        lineas=tuple(despachos_lineas),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
        destinos=tuple(destinos_vistos),
        naves=tuple(naves),
    )
    liquidacion = LiquidacionVisafruits(
        archivo="",
        variedad="",
        orden_numero="",
        factura=factura_corta,
        factura_corta=factura_corta,
        etd_texto="",
        eta_texto="",
        semana_etd=semana_int,
        contenedores=tuple(contenedores),
        contenedores_declarados=len(contenedores),
        precios=(),
        total_cajas=total_liq,
        importe_total_eur=total_venta_liq,
        comision_eur=comision_final,
        gastos=dict(gastos_globales),
        total_a_pagar_eur=Decimal("0"),
        rubros_no_mapeados=(),
        texto="",
    )
    validacion = ResultadoValidacionVisafruits(
        es_valido=True,
        semana=semana_int,
        anio=anio_int,
        destino_aplicado=destinos_vistos[0]
        if destinos_vistos
        else destino_ui,
        factura_corta=factura_corta,
        nave=naves[0] if naves else "",
        contenedores=tuple(contenedores),
        total_cajas_liquidacion=total_liq,
        total_cajas_despachos=total_desp,
        total_venta_liquidacion_eur=total_venta_liq,
        total_venta_calculado_eur=total_venta_calc,
        comision_eur=comision_final,
        gastos=dict(gastos_globales),
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
    )
    return ResultadoPreparacionVisafruits(
        estado="listo",
        puede_escribir=True,
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )
