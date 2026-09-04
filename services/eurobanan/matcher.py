from __future__ import annotations

from services.glamour.matcher import (
    CLIENTE_GLAMOUR,
    ErrorMatcherGlamour,
    FormatoDespachosGlamourError,
    LineaDespachoGlamour,
    ResultadoMatcherGlamour,
    SinCoincidenciasGlamourError,
    buscar_lineas_despachos_glamour,
)

CLIENTE_EUROBANAN = "EUROBANAN"

ErrorMatcherEurobanan = ErrorMatcherGlamour
FormatoDespachosEurobananError = FormatoDespachosGlamourError
SinCoincidenciasEurobananError = SinCoincidenciasGlamourError
LineaDespachoEurobanan = LineaDespachoGlamour
ResultadoMatcherEurobanan = ResultadoMatcherGlamour


def buscar_lineas_despachos_eurobanan(
    ruta_archivo: str,
    *,
    semana: int,
    anio: int,
    destino: str,
    factura_corta: str,
    cliente: str = CLIENTE_EUROBANAN,
) -> ResultadoMatcherEurobanan:
    return buscar_lineas_despachos_glamour(
        ruta_archivo=ruta_archivo,
        semana=semana,
        anio=anio,
        destino=destino,
        factura_corta=factura_corta,
        cliente=cliente,
    )
