from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

from django.db.models import QuerySet
from django.utils import timezone

from procesamientos.models import (
    CorreccionGastoDimanno,
    CorreccionGastoMaster,
    CorreccionGastoOrsero,
    GeneracionDimanno,
    GeneracionEurobanan,
    GeneracionFruver,
    GeneracionGlamour,
    GeneracionKraaijeveld,
    GeneracionMaster,
    GeneracionNufri,
    GeneracionOrsero,
    GeneracionSifa,
    GeneracionTdvEuropa,
    ResolucionDestinoDimanno,
)

ESTADOS_GENERACION = {
    "completado": ("Transacción completada", "completada"),
    "error": ("Error en generación", "error"),
    "procesando": ("En proceso", "proceso"),
    "pendiente": ("Pendiente", "proceso"),
}

# (cliente, modelo, url_name, extractor_ref)
FUENTES_GENERACION: tuple[
    tuple[str, type, str, Callable[[Any], str]],
    ...,
] = (
    (
        "Di Manno",
        GeneracionDimanno,
        "procesamientos:dimanno_generacion_detalle",
        lambda g: g.procesamiento.factura_corta or "—",
    ),
    (
        "Master Fruits",
        GeneracionMaster,
        "procesamientos:master_generacion_detalle",
        lambda g: g.procesamiento.factura_corta or "—",
    ),
    (
        "ORSERO",
        GeneracionOrsero,
        "procesamientos:orsero_generacion_detalle",
        lambda g: g.procesamiento.nave_texto or "—",
    ),
    (
        "KRAAIJEVELD",
        GeneracionKraaijeveld,
        "procesamientos:kraaijeveld_generacion_detalle",
        lambda g: (
            g.procesamiento.factura_corta_fijo
            or g.procesamiento.destino_ui
            or "—"
        ),
    ),
    (
        "SIFA",
        GeneracionSifa,
        "procesamientos:sifa_generacion_detalle",
        lambda g: (
            g.procesamiento.factura_corta
            or g.procesamiento.destino_ui
            or "—"
        ),
    ),
    (
        "Glamour",
        GeneracionGlamour,
        "procesamientos:glamour_generacion_detalle",
        lambda g: (
            g.procesamiento.factura_corta
            or g.procesamiento.destino_ui
            or "—"
        ),
    ),
    (
        "TDV Europa",
        GeneracionTdvEuropa,
        "procesamientos:tdv_europa_generacion_detalle",
        lambda g: (
            g.procesamiento.factura_corta
            or g.procesamiento.destino_ui
            or "—"
        ),
    ),
    (
        "FRU&VER",
        GeneracionFruver,
        "procesamientos:fruver_generacion_detalle",
        lambda g: (
            g.procesamiento.factura_corta
            or g.procesamiento.destino_ui
            or "—"
        ),
    ),
    (
        "NUFRI",
        GeneracionNufri,
        "procesamientos:nufri_generacion_detalle",
        lambda g: (
            g.procesamiento.factura_corta
            or g.procesamiento.destino_ui
            or "—"
        ),
    ),
    (
        "EUROBANAN",
        GeneracionEurobanan,
        "procesamientos:eurobanan_generacion_detalle",
        lambda g: (
            g.procesamiento.factura_corta
            or g.procesamiento.destino_ui
            or "—"
        ),
    ),
)

CLIENTES_INGRESOS = tuple(
    cliente for cliente, *_ in FUENTES_GENERACION
)

COLORES_CLIENTE = {
    "Di Manno": "#FF5A3D",
    "Master Fruits": "#3B82F6",
    "ORSERO": "#A855F7",
    "KRAAIJEVELD": "#14B8A6",
    "SIFA": "#F59E0B",
    "Glamour": "#EC4899",
    "TDV Europa": "#22C55E",
    "FRU&VER": "#8B5CF6",
    "NUFRI": "#06B6D4",
    "EUROBANAN": "#EAB308",
}


def _evento_generacion(
    *,
    item: Any,
    cliente: str,
    url_name: str,
    referencia: str,
) -> dict[str, Any]:
    estado_texto, estado_clase = ESTADOS_GENERACION.get(
        item.estado,
        (getattr(item, "estado_legible", item.estado), "proceso"),
    )
    return {
        "tipo": "Generación de archivo",
        "estado": estado_texto,
        "estado_clase": estado_clase,
        "cliente": cliente,
        "detalle": estado_texto,
        "usuario": item.solicitado_por_nombre or "—",
        "fecha": item.solicitado_en,
        "factura": referencia,
        "url_detalle": (url_name, item.id),
    }


def recolectar_eventos_bitacora(
    *,
    limite_por_fuente: int = 120,
) -> list[dict[str, Any]]:
    eventos: list[dict[str, Any]] = []

    for item in CorreccionGastoDimanno.objects.select_related(
        "gasto",
        "gasto__procesamiento",
    ).order_by("-creado_en")[:limite_por_fuente]:
        procesamiento = item.gasto.procesamiento
        eventos.append(
            {
                "tipo": "Corrección de gasto",
                "estado": "Cambiada",
                "estado_clase": "cambiada",
                "cliente": "Di Manno",
                "detalle": (
                    f"{item.gasto.nombre}: "
                    f"{item.valor_anterior} → "
                    f"{item.valor_nuevo}"
                ),
                "usuario": item.usuario_nombre or "—",
                "fecha": item.creado_en,
                "factura": procesamiento.factura_corta or "—",
                "url_detalle": (
                    "procesamientos:dimanno_detalle",
                    procesamiento.id,
                ),
            }
        )

    for item in ResolucionDestinoDimanno.objects.select_related(
        "procesamiento",
    ).order_by("-creado_en")[:limite_por_fuente]:
        eventos.append(
            {
                "tipo": "Resolución de destino",
                "estado": "Cambiada",
                "estado_clase": "cambiada",
                "cliente": "Di Manno",
                "detalle": (
                    f"Destino actualizado: "
                    f"{item.destino_anterior or 'sin definir'} → "
                    f"{item.destino_nuevo}"
                ),
                "usuario": item.usuario_nombre or "—",
                "fecha": item.creado_en,
                "factura": item.procesamiento.factura_corta or "—",
                "url_detalle": (
                    "procesamientos:dimanno_detalle",
                    item.procesamiento_id,
                ),
            }
        )

    for item in CorreccionGastoMaster.objects.select_related(
        "gasto",
        "gasto__procesamiento",
    ).order_by("-creado_en")[:limite_por_fuente]:
        procesamiento = item.gasto.procesamiento
        eventos.append(
            {
                "tipo": "Corrección de gasto",
                "estado": "Cambiada",
                "estado_clase": "cambiada",
                "cliente": "Master Fruits",
                "detalle": (
                    f"{item.gasto.nombre}: "
                    f"{item.valor_anterior} → "
                    f"{item.valor_nuevo}"
                ),
                "usuario": item.usuario_nombre or "—",
                "fecha": item.creado_en,
                "factura": procesamiento.factura_corta or "—",
                "url_detalle": (
                    "procesamientos:master_detalle",
                    procesamiento.id,
                ),
            }
        )

    for item in CorreccionGastoOrsero.objects.select_related(
        "gasto",
        "gasto__procesamiento",
    ).order_by("-creado_en")[:limite_por_fuente]:
        procesamiento = item.gasto.procesamiento
        eventos.append(
            {
                "tipo": "Corrección de gasto",
                "estado": "Cambiada",
                "estado_clase": "cambiada",
                "cliente": "ORSERO",
                "detalle": (
                    f"{item.gasto.nombre}: "
                    f"{item.valor_anterior} → "
                    f"{item.valor_nuevo}"
                ),
                "usuario": item.usuario_nombre or "—",
                "fecha": item.creado_en,
                "factura": procesamiento.nave_texto or "—",
                "url_detalle": (
                    "procesamientos:orsero_detalle",
                    procesamiento.id,
                ),
            }
        )

    for cliente, modelo, url_name, ref_fn in FUENTES_GENERACION:
        qs: QuerySet = modelo.objects.select_related(
            "procesamiento",
        ).order_by("-solicitado_en")[:limite_por_fuente]
        for item in qs:
            eventos.append(
                _evento_generacion(
                    item=item,
                    cliente=cliente,
                    url_name=url_name,
                    referencia=ref_fn(item),
                )
            )

    return eventos


def filtrar_eventos_bitacora(
    eventos: list[dict[str, Any]],
    *,
    q: str = "",
    cliente: str = "",
    factura: str = "",
    usuario: str = "",
    fecha_desde: str = "",
    fecha_hasta: str = "",
) -> list[dict[str, Any]]:
    q = (q or "").strip().lower()
    cliente = (cliente or "").strip().lower()
    factura = (factura or "").strip().lower()
    usuario = (usuario or "").strip().lower()
    fecha_desde = (fecha_desde or "").strip()
    fecha_hasta = (fecha_hasta or "").strip()

    filtrados: list[dict[str, Any]] = []
    for evento in eventos:
        fecha = evento["fecha"]
        if fecha_desde:
            try:
                desde = datetime.strptime(
                    fecha_desde,
                    "%Y-%m-%d",
                ).date()
                if fecha.date() < desde:
                    continue
            except ValueError:
                pass
        if fecha_hasta:
            try:
                hasta = datetime.strptime(
                    fecha_hasta,
                    "%Y-%m-%d",
                ).date()
                if fecha.date() > hasta:
                    continue
            except ValueError:
                pass
        if cliente and cliente not in evento["cliente"].lower():
            continue
        if factura and factura not in str(evento["factura"]).lower():
            continue
        if usuario and usuario not in evento["usuario"].lower():
            continue
        if q:
            haystack = " ".join(
                [
                    evento["tipo"],
                    evento["estado"],
                    evento["cliente"],
                    str(evento["factura"]),
                    evento["detalle"],
                    evento["usuario"],
                ]
            ).lower()
            if q not in haystack:
                continue
        filtrados.append(evento)

    filtrados.sort(key=lambda e: e["fecha"], reverse=True)
    return filtrados


def _parse_fecha(valor: str | None, default: date) -> date:
    texto = (valor or "").strip()
    if not texto:
        return default
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return default


def resumen_ingresos_diarios(
    *,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    cliente_filtro: str | None = None,
) -> dict[str, Any]:
    hoy = timezone.localdate()
    hasta = _parse_fecha(fecha_hasta, hoy)
    desde = _parse_fecha(fecha_desde, hasta - timedelta(days=13))
    if desde > hasta:
        desde, hasta = hasta, desde

    inicio = timezone.make_aware(
        datetime.combine(desde, datetime.min.time())
    )
    fin = timezone.make_aware(
        datetime.combine(hasta, datetime.max.time())
    )

    cliente_n = (cliente_filtro or "").strip().lower()
    contadores: dict[str, dict[date, int]] = {
        c: defaultdict(int) for c in CLIENTES_INGRESOS
    }
    totales_cliente: dict[str, int] = {
        c: 0 for c in CLIENTES_INGRESOS
    }

    for cliente, modelo, _url, _ref in FUENTES_GENERACION:
        if cliente_n and cliente_n not in cliente.lower():
            continue
        qs = modelo.objects.filter(
            estado="completado",
            solicitado_en__gte=inicio,
            solicitado_en__lte=fin,
        ).only("solicitado_en")
        for item in qs:
            dia = timezone.localtime(item.solicitado_en).date()
            contadores[cliente][dia] += 1
            totales_cliente[cliente] += 1

    dias: list[date] = []
    actual = desde
    while actual <= hasta:
        dias.append(actual)
        actual += timedelta(days=1)

    etiquetas = [d.strftime("%d/%m") for d in dias]
    datasets = []
    for cliente in CLIENTES_INGRESOS:
        if cliente_n and cliente_n not in cliente.lower():
            continue
        if totales_cliente[cliente] == 0 and cliente_n:
            # si filtró un cliente, igual mostrar serie
            pass
        datasets.append(
            {
                "label": cliente,
                "data": [
                    contadores[cliente].get(d, 0) for d in dias
                ],
                "backgroundColor": COLORES_CLIENTE.get(
                    cliente,
                    "#06B6D4",
                ),
                "borderRadius": 4,
                "stack": "ingresos",
            }
        )

    # Si no hay filtro, ocultar clientes en cero para no saturar
    if not cliente_n:
        datasets = [
            ds
            for ds in datasets
            if any(v > 0 for v in ds["data"])
            or totales_cliente[ds["label"]] > 0
        ]

    ranking = sorted(
        (
            {
                "cliente": c,
                "total": totales_cliente[c],
                "color": COLORES_CLIENTE.get(c, "#06B6D4"),
            }
            for c in CLIENTES_INGRESOS
            if not cliente_n or cliente_n in c.lower()
        ),
        key=lambda x: x["total"],
        reverse=True,
    )
    ranking_activos = [item for item in ranking if item["total"] > 0]
    ranking_cero = [item for item in ranking if item["total"] == 0]
    clientes_con_ingresos = len(ranking_activos)

    return {
        "fecha_desde": desde.isoformat(),
        "fecha_hasta": hasta.isoformat(),
        "etiquetas": etiquetas,
        "datasets": datasets,
        "total_periodo": sum(totales_cliente.values()),
        "ranking": ranking,
        "ranking_activos": ranking_activos,
        "ranking_cero": ranking_cero,
        "clientes": list(CLIENTES_INGRESOS),
        "dias_count": len(dias),
        "clientes_con_ingresos": clientes_con_ingresos,
    }
