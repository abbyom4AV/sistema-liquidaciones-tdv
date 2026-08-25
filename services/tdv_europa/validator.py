from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from services.tdv_europa.extractor import (
    COLUMNAS_GASTO,
    LiquidacionTdvEuropa,
    LineaProductoTdvEuropa,
    formatear_destino_excel,
    normalizar_destino,
    normalizar_texto,
)
from services.tdv_europa.matcher import (
    ResultadoMatcherTdvEuropa,
)


NivelIncidencia = Literal["error", "advertencia"]


@dataclass(frozen=True)
class IncidenciaValidacionTdvEuropa:
    codigo: str
    nivel: NivelIncidencia
    mensaje: str
    detalles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AtribucionMermaTdvEuropa:
    contenedor: str
    calibre_raw: str
    carton: str
    cliente: str
    cajas_merma: Decimal
    cajas_netas: Decimal
    cajas_brutas: Decimal


@dataclass(frozen=True)
class LineaPreparadaTdvEuropa:
    semana: int
    anio: int
    semana_texto: str
    cliente_liq: str
    nave: str
    contenedor: str
    destino: str
    destino_log: str
    tipo_fruta: str
    carton: str
    producto_liq: str
    calibre: int
    calibre_raw: str
    total_cajas: Decimal
    merma: Decimal
    cajas_netas: Decimal
    precio_venta_eur: Decimal
    gasto_puerto: Decimal
    gasto_trans: Decimal
    gasto_handl: Decimal
    gasto_inspeccion: Decimal
    gasto_customs: Decimal
    reclamos_irmadona: Decimal
    reclamos_mercado: Decimal
    comision_euros: Decimal
    factura_corta: str
    venta_bruta_eur: Decimal


@dataclass(frozen=True)
class ResultadoValidacionTdvEuropa:
    es_valido: bool
    destino_final: str
    destinos_despachos: tuple[str, ...]
    total_cajas_brutas_liquidacion: Decimal
    total_cajas_netas_liquidacion: Decimal
    total_cajas_despachos: int
    total_venta_eur: Decimal
    total_gastos_eur: Decimal
    total_merma: Decimal
    errores: tuple[IncidenciaValidacionTdvEuropa, ...]
    advertencias: tuple[IncidenciaValidacionTdvEuropa, ...]
    lineas_preparadas: tuple[LineaPreparadaTdvEuropa, ...]
    atribuciones_merma: tuple[AtribucionMermaTdvEuropa, ...]
    resumen_gastos: dict[str, Decimal]
    reclamos_irmadona: Decimal
    reclamos_mercado: Decimal
    comision_eur: Decimal


def _clave_merma(linea: LineaProductoTdvEuropa) -> tuple[str, str, str]:
    # calibre_raw evita mezclar CAL6 con CAL6CL8.
    return (
        normalizar_texto(linea.contenedor).replace(" ", ""),
        normalizar_texto(linea.calibre_raw),
        linea.carton_clave,
    )


def _destino_coincide(a: str, b: str) -> bool:
    na = normalizar_destino(a)
    nb = normalizar_destino(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _nave_coincide(a: str, b: str) -> bool:
    na = normalizar_texto(a)
    nb = normalizar_texto(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _es_cliente_merma_calidad(cliente: str) -> bool:
    nombre = normalizar_texto(cliente)
    return "MERMA" in nombre and nombre != "MERMA"


def _filtrar_candidatos_merma(
    candidatos: list[LineaProductoTdvEuropa],
) -> list[LineaProductoTdvEuropa]:
    venta = [
        c
        for c in candidatos
        if not _es_cliente_merma_calidad(c.cliente)
    ]
    return venta or candidatos


def _resolver_cliente_merma(
    candidatos: list[LineaProductoTdvEuropa],
    cajas_merma: Decimal,
) -> LineaProductoTdvEuropa | None:
    """
    Desempate con mismo contenedor+calibre+cartón:
    - 1 coincidencia → ese cliente
    - varias y hay una MERCADONA única → MERCADONA
    - varias sin MERCADONA y hay una IRMADONA única → IRMADONA
    - cajas de merma = netas de un solo candidato → ese cliente
    - varias con mismas cajas que la merma → el primero por nombre
    - resto → None (bloqueo)
    """
    candidatos = _filtrar_candidatos_merma(candidatos)
    if len(candidatos) == 1:
        return candidatos[0]
    mercadonas = [
        c
        for c in candidatos
        if "MERCADONA" in normalizar_texto(c.cliente)
    ]
    if len(mercadonas) == 1:
        return mercadonas[0]
    irmadonas = [
        c
        for c in candidatos
        if "IRMADONA" in normalizar_texto(c.cliente)
    ]
    if len(irmadonas) == 1:
        return irmadonas[0]

    por_cajas = [
        c
        for c in candidatos
        if c.cajas_netas == cajas_merma
    ]
    if len(por_cajas) == 1:
        return por_cajas[0]
    if len(por_cajas) > 1:
        return sorted(por_cajas, key=lambda c: c.cliente)[0]

    return None


def atribuir_mermas(
    liquidacion: LiquidacionTdvEuropa,
) -> tuple[
    dict[int, Decimal],
    list[AtribucionMermaTdvEuropa],
    list[IncidenciaValidacionTdvEuropa],
    list[IncidenciaValidacionTdvEuropa],
]:
    """
    Atribuye cada merma a una línea de cliente con mismo
    contenedor + calibre_raw + cartón.
    Devuelve merma indexada por id(línea cliente).
    """
    errores: list[IncidenciaValidacionTdvEuropa] = []
    advertencias: list[IncidenciaValidacionTdvEuropa] = []
    por_clave: dict[tuple[str, str, str], list[LineaProductoTdvEuropa]] = (
        defaultdict(list)
    )
    for linea in liquidacion.lineas:
        por_clave[_clave_merma(linea)].append(linea)

    # Merma acumulada por identidad de línea de cliente.
    merma_por_linea: dict[int, Decimal] = defaultdict(
        lambda: Decimal("0")
    )
    atribuciones: list[AtribucionMermaTdvEuropa] = []

    for merma in liquidacion.mermas:
        clave = _clave_merma(merma)
        candidatos = por_clave.get(clave, [])
        if len(candidatos) == 0:
            errores.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="MERMA_SIN_MATCH",
                    nivel="error",
                    mensaje=(
                        "Hay merma sin línea de cliente "
                        "coincidente (contenedor + calibre + "
                        "cartón)."
                    ),
                    detalles={
                        "contenedor": merma.contenedor,
                        "calibre_raw": merma.calibre_raw,
                        "carton": merma.carton,
                        "cajas": str(merma.cajas_netas),
                    },
                )
            )
            continue

        calidad = [
            c
            for c in candidatos
            if _es_cliente_merma_calidad(c.cliente)
            and c.cajas_netas == merma.cajas_netas
        ]
        if len(calidad) == 1:
            advertencias.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="MERMA_YA_EN_CALIDAD",
                    nivel="advertencia",
                    mensaje=(
                        "La merma ya está reflejada en la "
                        f"línea {calidad[0].cliente}."
                    ),
                    detalles={
                        "contenedor": merma.contenedor,
                        "calibre_raw": merma.calibre_raw,
                        "carton": merma.carton,
                        "cajas": str(merma.cajas_netas),
                        "cliente_calidad": calidad[0].cliente,
                    },
                )
            )
            continue

        candidatos_venta = _filtrar_candidatos_merma(candidatos)
        cliente = _resolver_cliente_merma(
            candidatos_venta,
            merma.cajas_netas,
        )
        if cliente is None:
            errores.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="MERMA_AMBIGUA",
                    nivel="error",
                    mensaje=(
                        "La merma coincide con más de una "
                        "línea de cliente y no se pudo "
                        "desempatar (sin MERCADONA/IRMADONA "
                        "única)."
                    ),
                    detalles={
                        "contenedor": merma.contenedor,
                        "calibre_raw": merma.calibre_raw,
                        "carton": merma.carton,
                        "cajas": str(merma.cajas_netas),
                        "clientes": [
                            c.cliente for c in candidatos_venta
                        ],
                    },
                )
            )
            continue

        if len(candidatos_venta) > 1:
            elegido_n = normalizar_texto(cliente.cliente)
            if "MERCADONA" in elegido_n:
                motivo = "MERCADONA"
            elif "IRMADONA" in elegido_n:
                motivo = "IRMADONA"
            else:
                motivo = cliente.cliente
            advertencias.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="MERMA_DESEMPATE",
                    nivel="advertencia",
                    mensaje=(
                        f"Merma ambigua atribuida a {motivo} "
                        "(había varias líneas con el mismo "
                        "contenedor/calibre/cartón)."
                    ),
                    detalles={
                        "contenedor": merma.contenedor,
                        "calibre_raw": merma.calibre_raw,
                        "carton": merma.carton,
                        "cajas": str(merma.cajas_netas),
                        "cliente_elegido": cliente.cliente,
                        "candidatos": [
                            c.cliente for c in candidatos_venta
                        ],
                    },
                )
            )

        clave_linea = id(cliente)
        merma_por_linea[clave_linea] += merma.cajas_netas
        total_merma = merma_por_linea[clave_linea]
        atribuciones.append(
            AtribucionMermaTdvEuropa(
                contenedor=merma.contenedor,
                calibre_raw=merma.calibre_raw,
                carton=merma.carton,
                cliente=cliente.cliente,
                cajas_merma=merma.cajas_netas,
                cajas_netas=cliente.cajas_netas,
                cajas_brutas=cliente.cajas_netas + total_merma,
            )
        )

    return merma_por_linea, atribuciones, errores, advertencias


def validar_liquidacion_tdv_europa(
    liquidacion: LiquidacionTdvEuropa,
    despachos: ResultadoMatcherTdvEuropa,
    *,
    destino_ui: str = "",
    factura_ui: str = "",
    semana_ui: int | None = None,
    anio_ui: int | None = None,
) -> ResultadoValidacionTdvEuropa:
    errores: list[IncidenciaValidacionTdvEuropa] = []
    advertencias: list[IncidenciaValidacionTdvEuropa] = []

    for mensaje in liquidacion.advertencias:
        advertencias.append(
            IncidenciaValidacionTdvEuropa(
                codigo="ADVERTENCIA_EXTRACCION",
                nivel="advertencia",
                mensaje=mensaje,
            )
        )

    if liquidacion.rubros_no_mapeados:
        for etiqueta, monto in liquidacion.rubros_no_mapeados:
            advertencias.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="RUBRO_NO_MAPEADO",
                    nivel="advertencia",
                    mensaje=(
                        f"Rubro de gasto no mapeado: {etiqueta}."
                    ),
                    detalles={
                        "etiqueta": etiqueta,
                        "monto": str(monto),
                    },
                )
            )

    factura = (liquidacion.factura_corta or "").strip()
    factura_form = (factura_ui or "").strip()
    if not (factura.isdigit() and len(factura) == 4):
        errores.append(
            IncidenciaValidacionTdvEuropa(
                codigo="FACTURA_CORTA_INVALIDA",
                nivel="error",
                mensaje=(
                    "La factura del PDF no tiene exactamente "
                    "4 dígitos finales."
                ),
                detalles={"factura_corta": factura},
            )
        )
    elif factura_form and factura != factura_form:
        errores.append(
            IncidenciaValidacionTdvEuropa(
                codigo="FACTURA_UI_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "La factura indicada no coincide con "
                    "la del PDF."
                ),
                detalles={
                    "factura_ui": factura_form,
                    "factura_pdf": factura,
                },
            )
        )

    if semana_ui is not None and liquidacion.semana != int(semana_ui):
        errores.append(
            IncidenciaValidacionTdvEuropa(
                codigo="SEMANA_UI_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "La semana indicada no coincide con la del PDF."
                ),
                detalles={
                    "semana_ui": semana_ui,
                    "semana_pdf": liquidacion.semana,
                },
            )
        )
    if anio_ui is not None and liquidacion.anio != int(anio_ui):
        errores.append(
            IncidenciaValidacionTdvEuropa(
                codigo="ANIO_UI_NO_COINCIDE",
                nivel="error",
                mensaje=(
                    "El año indicado no coincide con el del PDF."
                ),
                detalles={
                    "anio_ui": anio_ui,
                    "anio_pdf": liquidacion.anio,
                },
            )
        )

    merma_por_linea, atribuciones, errores_merma, adv_merma = (
        atribuir_mermas(liquidacion)
    )
    errores.extend(errores_merma)
    advertencias.extend(adv_merma)

    reclamos_irmadona = Decimal("0")
    reclamos_mercado = Decimal("0")
    for reclamo in liquidacion.reclamos:
        if not reclamo.columna_reclamo:
            advertencias.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="RECLAMO_CLIENTE_DESCONOCIDO",
                    nivel="advertencia",
                    mensaje=(
                        "Hay un reclamo en NOTA que no es "
                        "Mercadona ni Irmadona."
                    ),
                    detalles={"texto": reclamo.texto},
                )
            )
            continue
        if reclamo.columna_reclamo == "Reclamos Irmadoña":
            reclamos_irmadona += reclamo.monto_eur
        else:
            reclamos_mercado += reclamo.monto_eur
        if not reclamo.gasto_columna:
            advertencias.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="RECLAMO_SIN_RUBRO",
                    nivel="advertencia",
                    mensaje=(
                        "Reclamo sin rubro de gasto identificable."
                    ),
                    detalles={"texto": reclamo.texto},
                )
            )

    resumen_gastos: dict[str, Decimal] = {
        col: liquidacion.gastos.get(col, Decimal("0"))
        for col in COLUMNAS_GASTO
    }
    total_gastos = sum(resumen_gastos.values(), Decimal("0"))

    destino_form = normalizar_destino(destino_ui)
    if not despachos.destinos:
        errores.append(
            IncidenciaValidacionTdvEuropa(
                codigo="SIN_DESTINO_DESPACHOS",
                nivel="error",
                mensaje=(
                    "Las líneas de Despachos no tienen "
                    "puerto destino."
                ),
            )
        )
        destino_final = destino_form or liquidacion.destino_pdf
    elif len(despachos.destinos) > 1:
        advertencias.append(
            IncidenciaValidacionTdvEuropa(
                codigo="DESTINOS_MULTIPLES",
                nivel="advertencia",
                mensaje=(
                    "Hay más de un destino en Despachos; "
                    "se usará el de la UI."
                ),
                detalles={"destinos": list(despachos.destinos)},
            )
        )
        destino_final = destino_form or despachos.destinos[0]
    else:
        destino_final = destino_form or despachos.destinos[0]

    if destino_form and liquidacion.destino_pdf and not _destino_coincide(
        liquidacion.destino_pdf,
        destino_form,
    ):
        errores.append(
            IncidenciaValidacionTdvEuropa(
                codigo="DESTINO_UI_PDF",
                nivel="error",
                mensaje=(
                    "El destino de la UI no coincide con el del PDF."
                ),
                detalles={
                    "destino_ui": destino_form,
                    "destino_pdf": liquidacion.destino_pdf,
                },
            )
        )

    if (
        liquidacion.destino_pdf
        and destino_final
        and not _destino_coincide(
            liquidacion.destino_pdf,
            destino_final,
        )
    ):
        advertencias.append(
            IncidenciaValidacionTdvEuropa(
                codigo="DESTINO_PDF_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El destino del PDF no coincide con "
                    "Despachos."
                ),
                detalles={
                    "destino_pdf": liquidacion.destino_pdf,
                    "destino_final": destino_final,
                },
            )
        )

    if liquidacion.nave and despachos.naves:
        if all(
            not _nave_coincide(liquidacion.nave, nave)
            for nave in despachos.naves
        ):
            advertencias.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="NAVE_DIFERENTE",
                    nivel="advertencia",
                    mensaje=(
                        "La nave del PDF no coincide con "
                        "Despachos."
                    ),
                    detalles={
                        "nave_pdf": liquidacion.nave,
                        "naves_despachos": list(despachos.naves),
                    },
                )
            )

    total_brutas = Decimal("0")
    total_merma = Decimal("0")
    lineas_preparadas: list[LineaPreparadaTdvEuropa] = []
    semana_texto = (
        despachos.semana_texto
        or f"{liquidacion.semana:02d}-{liquidacion.anio}"
    )

    for linea in liquidacion.lineas:
        merma = merma_por_linea.get(id(linea), Decimal("0"))
        brutas = linea.cajas_netas + merma
        total_brutas += brutas
        total_merma += merma
        precio = (
            (linea.venta_bruta_eur / linea.cajas_netas)
            if linea.cajas_netas != 0
            else Decimal("0")
        )
        if precio <= 0 and not _es_cliente_merma_calidad(
            linea.cliente
        ):
            errores.append(
                IncidenciaValidacionTdvEuropa(
                    codigo="PRECIO_INVALIDO",
                    nivel="error",
                    mensaje=(
                        f"Precio inválido para {linea.cliente} "
                        f"{linea.contenedor} {linea.calibre_raw}."
                    ),
                    detalles={
                        "cliente": linea.cliente,
                        "contenedor": linea.contenedor,
                        "calibre_raw": linea.calibre_raw,
                    },
                )
            )

        lineas_preparadas.append(
            LineaPreparadaTdvEuropa(
                semana=liquidacion.semana,
                anio=liquidacion.anio,
                semana_texto=semana_texto,
                cliente_liq=linea.cliente,
                nave=liquidacion.nave,
                contenedor=linea.contenedor,
                destino=formatear_destino_excel(destino_final),
                destino_log=formatear_destino_excel(destino_final),
                tipo_fruta=linea.tipo_fruta,
                carton=linea.carton,
                producto_liq="",
                calibre=linea.calibre,
                calibre_raw=linea.calibre_raw,
                total_cajas=brutas,
                merma=merma,
                cajas_netas=linea.cajas_netas,
                precio_venta_eur=precio,
                gasto_puerto=resumen_gastos["Gasto Puerto"],
                gasto_trans=resumen_gastos["Gasto Trans"],
                gasto_handl=resumen_gastos["Gasto Handl"],
                gasto_inspeccion=resumen_gastos["G.Inspección"],
                gasto_customs=resumen_gastos["G.Customs Duties"],
                reclamos_irmadona=reclamos_irmadona,
                reclamos_mercado=reclamos_mercado,
                comision_euros=liquidacion.comision_eur,
                factura_corta=liquidacion.factura_corta,
                venta_bruta_eur=linea.venta_bruta_eur,
            )
        )

    # Comparar cajas brutas PDF vs Despachos.
    if int(total_brutas) != despachos.total_cajas and abs(
        total_brutas - Decimal(despachos.total_cajas)
    ) > Decimal("0.05"):
        advertencias.append(
            IncidenciaValidacionTdvEuropa(
                codigo="TOTAL_CAJAS_DIFERENTE",
                nivel="advertencia",
                mensaje=(
                    "El total de cajas brutas del PDF no "
                    "coincide con Despachos."
                ),
                detalles={
                    "cajas_brutas_pdf": str(total_brutas),
                    "cajas_despachos": despachos.total_cajas,
                },
            )
        )

    contenedores_pdf = {
        normalizar_texto(c).replace(" ", "")
        for c in {linea.contenedor for linea in liquidacion.lineas}
        if c
    }
    contenedores_desp = {
        normalizar_texto(c).replace(" ", "")
        for c in despachos.contenedores
        if c
    }
    if contenedores_pdf and contenedores_pdf != contenedores_desp:
        advertencias.append(
            IncidenciaValidacionTdvEuropa(
                codigo="CONTENEDORES_DIFERENTES",
                nivel="advertencia",
                mensaje=(
                    "Los contenedores del PDF no coinciden "
                    "exactamente con Despachos."
                ),
                detalles={
                    "pdf": sorted(contenedores_pdf),
                    "despachos": sorted(contenedores_desp),
                },
            )
        )

    if not lineas_preparadas and not errores:
        errores.append(
            IncidenciaValidacionTdvEuropa(
                codigo="SIN_LINEAS_PREPARADAS",
                nivel="error",
                mensaje="No quedó ninguna línea para escribir.",
            )
        )

    return ResultadoValidacionTdvEuropa(
        es_valido=not errores,
        destino_final=destino_final,
        destinos_despachos=despachos.destinos,
        total_cajas_brutas_liquidacion=total_brutas,
        total_cajas_netas_liquidacion=liquidacion.total_cajas_netas,
        total_cajas_despachos=despachos.total_cajas,
        total_venta_eur=liquidacion.total_venta_eur,
        total_gastos_eur=total_gastos,
        total_merma=total_merma,
        errores=tuple(errores),
        advertencias=tuple(advertencias),
        lineas_preparadas=tuple(lineas_preparadas),
        atribuciones_merma=tuple(atribuciones),
        resumen_gastos=resumen_gastos,
        reclamos_irmadona=reclamos_irmadona,
        reclamos_mercado=reclamos_mercado,
        comision_eur=liquidacion.comision_eur,
    )
