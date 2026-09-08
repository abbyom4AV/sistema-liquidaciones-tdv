from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass
from decimal import Decimal
from typing import Any, Literal

from services.mensajes_gastos import (
    etiquetas_rubros,
    mensaje_gastos_no_mapeados,
)
from services.visafruits.extractor import (
    COLUMNAS_GASTO,
    LineaPrecioVisafruits,
    LiquidacionVisafruits,
    normalizar_texto,
)
from services.visafruits.matcher import (
    CLIENTE_VISAFRUITS_RAW,
    LineaDespachoVisafruits,
    ResultadoMatcherVisafruits,
)


NivelIncidencia = Literal["error", "advertencia"]

# Diferencia máxima tolerada al comparar importes en euros.
TOLERANCIA_EUR = Decimal("0.05")


@dataclass(frozen=True)
class IncidenciaValidacionVisafruits:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineaPreparadaVisafruits:
    """Una fila lista para escribir en Tabla1 del acumulativo."""

    fila_excel: int
    semana: int
    anio: int
    semana_texto: str
    cliente: str
    nave: str
    contenedor: str
    destino: str
    tipo_fruta: str
    calibre: int
    total_cajas: int
    carton: str
    es_vertical: bool
    precio_venta_eur: Decimal
    precio_encontrado: bool
    gastos: dict[str, Decimal]
    comision_eur: Decimal


@dataclass(frozen=True)
class ResultadoValidacionVisafruits:
    es_valido: bool
    semana: int
    anio: int
    destino_aplicado: str
    factura_corta: str
    nave: str
    contenedores: tuple[str, ...]
    total_cajas_liquidacion: int
    total_cajas_despachos: int
    total_venta_liquidacion_eur: Decimal
    total_venta_calculado_eur: Decimal
    comision_eur: Decimal
    gastos: dict[str, Decimal]
    errores: tuple[IncidenciaValidacionVisafruits, ...]
    advertencias: tuple[IncidenciaValidacionVisafruits, ...]
    lineas_preparadas: tuple[LineaPreparadaVisafruits, ...]


def titulo(texto: str) -> str:
    """'GOLDEN DIAMOND' → 'Golden Diamond'."""
    limpio = str(texto or "").strip()
    if not limpio:
        return ""
    return " ".join(
        palabra.capitalize() for palabra in limpio.split()
    )


def _clave_precio(
    tipo_fruta: str,
    calibre: int,
    es_vertical: bool,
) -> tuple[str, int, bool]:
    return normalizar_texto(tipo_fruta), int(calibre), bool(es_vertical)


def _indexar_precios(
    precios: tuple[LineaPrecioVisafruits, ...],
) -> dict[tuple[str, int, bool], LineaPrecioVisafruits]:
    indice: dict[tuple[str, int, bool], LineaPrecioVisafruits] = {}
    for linea in precios:
        clave = _clave_precio(
            linea.tipo_fruta,
            linea.calibre,
            linea.es_vertical,
        )
        indice.setdefault(clave, linea)
    return indice


def buscar_precio(
    indice: dict[tuple[str, int, bool], LineaPrecioVisafruits],
    tipo_fruta: str,
    calibre: int,
    es_vertical: bool,
) -> LineaPrecioVisafruits | None:
    """Precio del PDF para una línea de Despachos.

    El cartón vertical se cobra con la fila 'PIÑA VERTICAL', que en
    el PDF no trae calibre ni tipo, así que se cae hacia ella.
    """
    tipo = normalizar_texto(tipo_fruta)

    candidatos: list[tuple[str, int, bool]] = [
        (tipo, int(calibre), bool(es_vertical)),
    ]
    if es_vertical:
        # La fila genérica 'PIÑA VERTICAL' aplica a cualquier
        # calibre y tipo de fruta.
        candidatos.append((tipo, 0, True))
        for clave in indice:
            if clave[2] and clave[1] == 0:
                candidatos.append(clave)

    for clave in candidatos:
        encontrado = indice.get(clave)
        if encontrado is not None:
            return encontrado
    return None


def _cajas_por_clave(
    lineas: tuple[LineaDespachoVisafruits, ...],
) -> dict[tuple[str, int, bool], int]:
    conteo: dict[tuple[str, int, bool], int] = defaultdict(int)
    for linea in lineas:
        clave = _clave_precio(
            linea.tipo_empaque,
            linea.calibre,
            linea.es_vertical,
        )
        conteo[clave] += linea.total_cajas
    return dict(conteo)


def _describir_clave(clave: tuple[str, int, bool]) -> str:
    tipo, calibre, vertical = clave
    partes = [titulo(tipo)]
    if vertical:
        partes.append("vertical")
    if calibre:
        partes.append(f"calibre {calibre}")
    return " ".join(partes)


def validar_liquidacion_visafruits(
    liquidacion: LiquidacionVisafruits,
    despachos: ResultadoMatcherVisafruits,
    destino_ui: str = "",
) -> ResultadoValidacionVisafruits:
    errores: list[IncidenciaValidacionVisafruits] = []
    advertencias: list[IncidenciaValidacionVisafruits] = []

    destino_aplicado = (
        destino_ui.strip() or despachos.destino_buscado.strip()
    )

    # Único bloqueo: dinero del PDF que no tiene columna destino.
    if liquidacion.rubros_no_mapeados:
        rubros = etiquetas_rubros(liquidacion.rubros_no_mapeados)
        errores.append(
            IncidenciaValidacionVisafruits(
                codigo="RUBROS_NO_MAPEADOS",
                nivel="error",
                mensaje=mensaje_gastos_no_mapeados(rubros),
                detalles={"rubros": rubros},
            )
        )

    if len(despachos.naves) > 1:
        errores.append(
            IncidenciaValidacionVisafruits(
                codigo="VARIAS_NAVES",
                nivel="error",
                mensaje=(
                    "Despachos devolvió más de una nave para estos "
                    "criterios: "
                    + ", ".join(despachos.naves)
                    + ". Revise la semana, el destino y la factura."
                ),
                detalles={"naves": list(despachos.naves)},
            )
        )

    nave = despachos.naves[0] if despachos.naves else ""

    # El destino del PDF (ORDEN NUMERO) suele ser el puerto de la
    # orden y no el destino real; se avisa pero manda el digitado.
    orden = normalizar_texto(liquidacion.orden_numero)
    destinos_despachos = {normalizar_texto(d) for d in despachos.destinos}
    if orden and destinos_despachos and orden not in destinos_despachos:
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="DESTINO_PDF_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    f"El PDF indica '{liquidacion.orden_numero}' y en "
                    "Despachos el destino es "
                    + ", ".join(despachos.destinos)
                    + f". Se usa el destino indicado: {destino_aplicado}."
                ),
                detalles={
                    "orden_numero": liquidacion.orden_numero,
                    "destinos_despachos": list(despachos.destinos),
                    "destino_aplicado": destino_aplicado,
                },
            )
        )

    if liquidacion.semana_etd and liquidacion.semana_etd != despachos.semana:
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="SEMANA_PDF_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    f"El ETD del PDF es semana {liquidacion.semana_etd} "
                    f"y se solicitó la semana {despachos.semana}."
                ),
                detalles={
                    "semana_pdf": liquidacion.semana_etd,
                    "semana_solicitada": despachos.semana,
                },
            )
        )

    if (
        liquidacion.factura_corta
        and liquidacion.factura_corta != despachos.factura_buscada
    ):
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="FACTURA_PDF_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    f"El PDF trae la factura {liquidacion.factura_corta} "
                    f"y se buscó la {despachos.factura_buscada}."
                ),
                detalles={
                    "factura_pdf": liquidacion.factura_corta,
                    "factura_buscada": despachos.factura_buscada,
                },
            )
        )

    if liquidacion.total_cajas != despachos.total_cajas:
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="TOTAL_CAJAS_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    f"El PDF declara {liquidacion.total_cajas} cajas y "
                    f"Despachos suma {despachos.total_cajas}."
                ),
                detalles={
                    "cajas_liquidacion": liquidacion.total_cajas,
                    "cajas_despachos": despachos.total_cajas,
                },
            )
        )

    contenedores_pdf = {
        normalizar_texto(c) for c in liquidacion.contenedores
    }
    contenedores_desp = {
        normalizar_texto(c) for c in despachos.contenedores
    }
    if contenedores_pdf and contenedores_pdf != contenedores_desp:
        faltan = sorted(contenedores_pdf - contenedores_desp)
        sobran = sorted(contenedores_desp - contenedores_pdf)
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="CONTENEDORES_DIFERENTES",
                nivel="advertencia",
                mensaje=(
                    "Los contenedores del PDF no coinciden con "
                    "Despachos. Solo en el PDF: "
                    + (", ".join(faltan) or "ninguno")
                    + ". Solo en Despachos: "
                    + (", ".join(sobran) or "ninguno")
                    + "."
                ),
                detalles={
                    "solo_pdf": faltan,
                    "solo_despachos": sobran,
                },
            )
        )

    if (
        liquidacion.contenedores_declarados
        and liquidacion.contenedores_declarados
        != len(despachos.contenedores)
    ):
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="CANTIDAD_CONTENEDORES_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El PDF declara "
                    f"{liquidacion.contenedores_declarados} contenedores "
                    f"y Despachos trae {len(despachos.contenedores)}."
                ),
                detalles={
                    "declarados_pdf": liquidacion.contenedores_declarados,
                    "en_despachos": len(despachos.contenedores),
                },
            )
        )

    # Cajas por tipo de fruta, calibre y cartón vertical.
    indice_precios = _indexar_precios(liquidacion.precios)
    cajas_despachos = _cajas_por_clave(despachos.lineas)
    cajas_pdf: dict[tuple[str, int, bool], int] = {}
    for precio in liquidacion.precios:
        clave = _clave_precio(
            precio.tipo_fruta,
            precio.calibre,
            precio.es_vertical,
        )
        cajas_pdf[clave] = cajas_pdf.get(clave, 0) + precio.total_cajas

    for clave, cajas in sorted(cajas_despachos.items()):
        precio_linea = buscar_precio(
            indice_precios,
            clave[0],
            clave[1],
            clave[2],
        )
        clave_pdf = (
            _clave_precio(
                precio_linea.tipo_fruta,
                precio_linea.calibre,
                precio_linea.es_vertical,
            )
            if precio_linea is not None
            else clave
        )
        cajas_en_pdf = cajas_pdf.get(clave_pdf, 0)
        if precio_linea is not None and cajas_en_pdf != cajas:
            advertencias.append(
                IncidenciaValidacionVisafruits(
                    codigo="CAJAS_CALIBRE_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        f"En {_describir_clave(clave)} el PDF declara "
                        f"{cajas_en_pdf} cajas y Despachos suma {cajas}."
                    ),
                    detalles={
                        "clave": _describir_clave(clave),
                        "cajas_pdf": cajas_en_pdf,
                        "cajas_despachos": cajas,
                    },
                )
            )

    # Filas cobradas en el PDF que no aparecen en Despachos.
    for clave, cajas in sorted(cajas_pdf.items()):
        if cajas <= 0:
            continue
        if clave in cajas_despachos:
            continue
        if clave[2] and any(k[2] for k in cajas_despachos):
            continue
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="LINEA_PDF_SIN_DESPACHO",
                nivel="advertencia",
                mensaje=(
                    f"El PDF cobra {cajas} cajas de "
                    f"{_describir_clave(clave)} y Despachos no trae "
                    "esa combinación."
                ),
                detalles={
                    "clave": _describir_clave(clave),
                    "cajas_pdf": cajas,
                },
            )
        )

    gastos = {
        columna: liquidacion.gastos.get(columna, Decimal("0"))
        for columna in COLUMNAS_GASTO
    }

    lineas_prep: list[LineaPreparadaVisafruits] = []
    total_venta_calculado = Decimal("0")
    sin_precio: list[str] = []
    precio_cero: list[str] = []

    for despacho in despachos.lineas:
        precio_linea = buscar_precio(
            indice_precios,
            despacho.tipo_empaque,
            despacho.calibre,
            despacho.es_vertical,
        )
        encontrado = precio_linea is not None
        precio = (
            precio_linea.precio_eur if encontrado else Decimal("0")
        )
        descripcion = (
            f"{titulo(despacho.tipo_empaque)} calibre "
            f"{despacho.calibre} ({despacho.carton})"
        )
        if not encontrado:
            sin_precio.append(descripcion)
        elif precio == 0:
            precio_cero.append(descripcion)

        total_venta_calculado += precio * Decimal(despacho.total_cajas)

        lineas_prep.append(
            LineaPreparadaVisafruits(
                fila_excel=despacho.fila_excel,
                semana=despacho.semana,
                anio=despacho.anio,
                semana_texto=despacho.semana_texto,
                cliente=CLIENTE_VISAFRUITS_RAW,
                nave=despacho.barco,
                contenedor=despacho.contenedor,
                destino=titulo(destino_aplicado),
                tipo_fruta=titulo(despacho.tipo_empaque),
                calibre=despacho.calibre,
                total_cajas=despacho.total_cajas,
                carton=despacho.carton,
                es_vertical=despacho.es_vertical,
                precio_venta_eur=precio,
                precio_encontrado=encontrado,
                gastos=dict(gastos),
                comision_eur=liquidacion.comision_eur,
            )
        )

    if sin_precio:
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="PRECIO_NO_ENCONTRADO",
                nivel="advertencia",
                mensaje=(
                    "El PDF no trae precio para: "
                    + "; ".join(dict.fromkeys(sin_precio))
                    + ". Se escribe 0."
                ),
                detalles={"lineas": list(dict.fromkeys(sin_precio))},
            )
        )

    if precio_cero:
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="PRECIO_EN_CERO",
                nivel="advertencia",
                mensaje=(
                    "El PDF trae precio 0 para: "
                    + "; ".join(dict.fromkeys(precio_cero))
                    + "."
                ),
                detalles={"lineas": list(dict.fromkeys(precio_cero))},
            )
        )

    diferencia = abs(
        liquidacion.importe_total_eur - total_venta_calculado
    )
    if diferencia > TOLERANCIA_EUR:
        advertencias.append(
            IncidenciaValidacionVisafruits(
                codigo="TOTAL_VENTA_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El importe total del PDF es "
                    f"{liquidacion.importe_total_eur} € y el cálculo "
                    f"del sistema da {total_venta_calculado} € "
                    f"(diferencia de {diferencia} €)."
                ),
                detalles={
                    "importe_pdf": float(liquidacion.importe_total_eur),
                    "importe_calculado": float(total_venta_calculado),
                    "diferencia": float(diferencia),
                },
            )
        )

    es_valido = not errores and bool(lineas_prep)

    return ResultadoValidacionVisafruits(
        es_valido=es_valido,
        semana=despachos.semana,
        anio=despachos.anio,
        destino_aplicado=titulo(destino_aplicado),
        factura_corta=despachos.factura_buscada,
        nave=nave,
        contenedores=despachos.contenedores,
        total_cajas_liquidacion=liquidacion.total_cajas,
        total_cajas_despachos=despachos.total_cajas,
        total_venta_liquidacion_eur=liquidacion.importe_total_eur,
        total_venta_calculado_eur=total_venta_calculado,
        comision_eur=liquidacion.comision_eur,
        gastos=gastos,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_prep),
    )


def convertir_a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return float(valor)
    if is_dataclass(valor) and not isinstance(valor, type):
        return convertir_a_json(asdict(valor))
    if isinstance(valor, dict):
        return {k: convertir_a_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [convertir_a_json(v) for v in valor]
    return valor


def main() -> None:
    from services.visafruits.extractor import (
        extraer_liquidacion_visafruits,
    )
    from services.visafruits.matcher import (
        buscar_lineas_despachos_visafruits,
    )

    parser = argparse.ArgumentParser(
        description="Valida una liquidación VISAFRUITS.",
    )
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--despachos", required=True)
    parser.add_argument("--semana", type=int, required=True)
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--destino", required=True)
    parser.add_argument("--factura", required=True)
    args = parser.parse_args()

    liquidacion = extraer_liquidacion_visafruits(args.pdf)
    despachos = buscar_lineas_despachos_visafruits(
        args.despachos,
        args.semana,
        args.anio,
        args.destino,
        args.factura,
    )
    resultado = validar_liquidacion_visafruits(
        liquidacion,
        despachos,
        args.destino,
    )
    print(
        json.dumps(
            convertir_a_json(resultado),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
