from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from services.kraaijeveld.extractor import COLUMNAS_GASTO
from services.kraaijeveld.matcher import (
    CLIENTE_KRAAIJEVELD,
    LineaDespachoKraaijeveld,
    ResultadoMatcherKraaijeveld,
)
from services.kraaijeveld.processor import (
    ResultadoPreparacionKraaijeveld,
)
from services.kraaijeveld.validator import (
    LineaPreparadaKraaijeveld,
    ResultadoValidacionKraaijeveld,
)
from services.kraaijeveld.writer import NOMBRE_DESCARGA_KRAAIJEVELD


class ErrorConfirmacionGeneracionKraaijeveld(Exception):
    """Error al reconstruir el procesamiento Kraaijeveld para escritura."""


def construir_nombre_descarga() -> str:
    return NOMBRE_DESCARGA_KRAAIJEVELD


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
        raise ErrorConfirmacionGeneracionKraaijeveld(
            f"El campo {campo!r} no es numérico."
        ) from error


def _a_decimal_opcional(valor: Any, campo: str) -> Decimal | None:
    if valor is None or valor == "":
        return None
    return _a_decimal(valor, campo)


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
        raise ErrorConfirmacionGeneracionKraaijeveld(
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


def serializar_linea_preparada_kraaijeveld(
    linea: LineaPreparadaKraaijeveld,
) -> dict[str, Any]:
    """Convierte una línea preparada a JSON para guardar en la BD."""
    despacho = linea.despacho
    return {
        "contenedor": despacho.contenedor,
        "nave": despacho.barco,
        "destino": despacho.puerto_destino,
        "tipo_fruta": despacho.tipo_fruta,
        "carton": despacho.carton,
        "calibre": despacho.calibre,
        "total_cajas": despacho.total_cajas,
        "es_precio_fijo": linea.es_precio_fijo,
        "precio_venta_eur": (
            _decimal_a_texto(linea.precio_venta_eur)
            if linea.precio_venta_eur is not None
            else None
        ),
        "precio_venta_usd": (
            _decimal_a_texto(linea.precio_venta_usd)
            if linea.precio_venta_usd is not None
            else None
        ),
        "comision": _decimal_a_texto(linea.comision),
        "gastos": {
            columna: _decimal_a_texto(
                linea.gastos.get(columna, Decimal("0"))
            )
            for columna in COLUMNAS_GASTO
        },
        "precio_encontrado": linea.precio_encontrado,
        "factura_corta": despacho.factura_corta,
        "semana_texto": despacho.semana_texto,
        "anio": despacho.anio,
    }


def serializar_lineas_preparadas_kraaijeveld(
    lineas,
) -> list[dict[str, Any]]:
    return [
        serializar_linea_preparada_kraaijeveld(linea)
        for linea in lineas
    ]


def reconstruir_resultado_para_escritura_kraaijeveld(
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
) -> ResultadoPreparacionKraaijeveld:
    """
    Arma el resultado listo para escribir Excel sin re-leer los
    PDFs ni Despachos: usa las líneas y gastos ya persistidos en
    la BD (lineas_preparadas del ProcesamientoKraaijeveld).
    """
    if not lineas_preparadas:
        raise ErrorConfirmacionGeneracionKraaijeveld(
            "No hay líneas preparadas guardadas."
        )

    semana_int = int(semana)
    anio_int = int(anio)
    semana_texto_defecto = f"{semana_int:02d}-{anio_int}"

    lineas: list[LineaPreparadaKraaijeveld] = []
    contenedores: list[str] = []
    destinos_vistos: list[str] = []

    for indice, cruda in enumerate(lineas_preparadas, start=1):
        if not isinstance(cruda, dict):
            raise ErrorConfirmacionGeneracionKraaijeveld(
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
        semana_texto = (
            str(cruda.get("semana_texto") or "").strip()
            or semana_texto_defecto
        )

        if not contenedor or not destino:
            raise ErrorConfirmacionGeneracionKraaijeveld(
                f"Línea {indice} incompleta (contenedor/destino)."
            )

        es_precio_fijo = _a_bool(cruda.get("es_precio_fijo"))
        precio_venta_eur = _a_decimal_opcional(
            cruda.get("precio_venta_eur"),
            "precio_venta_eur",
        )
        precio_venta_usd = _a_decimal_opcional(
            cruda.get("precio_venta_usd"),
            "precio_venta_usd",
        )
        comision = _a_decimal(
            cruda.get("comision") or 0,
            "comision",
        )
        gastos = _gastos_desde_dict(cruda.get("gastos"))
        precio_encontrado = bool(
            cruda.get("precio_encontrado", True)
        )

        despacho = LineaDespachoKraaijeveld(
            fila_excel=indice,
            semana=semana_int,
            anio=anio_linea,
            semana_texto=semana_texto,
            contenedor=contenedor,
            cliente=CLIENTE_KRAAIJEVELD,
            barco=nave,
            puerto_destino=destino,
            tipo_empaque=tipo_fruta,
            carton=carton,
            calibre=calibre,
            total_cajas=total_cajas,
            factura=factura_corta,
            factura_corta=factura_corta,
        )
        lineas.append(
            LineaPreparadaKraaijeveld(
                despacho=despacho,
                es_precio_fijo=es_precio_fijo,
                precio_venta_eur=precio_venta_eur,
                precio_venta_usd=precio_venta_usd,
                comision=comision,
                gastos=gastos,
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

    despachos = ResultadoMatcherKraaijeveld(
        archivo="",
        hoja="",
        cliente_buscado=CLIENTE_KRAAIJEVELD,
        semana=semana_int,
        anio=anio_int,
        destino_buscado=destino_ui,
        semana_texto=semana_texto_defecto,
        lineas=tuple(linea.despacho for linea in lineas),
        total_cajas=total_desp,
        contenedores=tuple(contenedores),
        destinos=destinos,
        facturas_cortas=tuple(
            dict.fromkeys(
                linea.despacho.factura_corta
                for linea in lineas
                if linea.despacho.factura_corta
            )
        ),
    )
    validacion = ResultadoValidacionKraaijeveld(
        es_valido=True,
        destino_ui=destino_ui,
        destinos_despachos=destinos,
        total_cajas_liquidacion=total_liq or total_desp,
        total_cajas_despachos=total_desp,
        errores=(),
        advertencias=(),
        lineas_preparadas=tuple(lineas),
        resumen_gastos_contenedores=(),
    )
    return ResultadoPreparacionKraaijeveld(
        estado="listo",
        puede_escribir=True,
        liquidaciones=(),
        despachos=despachos,
        validacion=validacion,
    )
