from __future__ import annotations

import logging
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from procesamientos.forms import FormularioCargaSifa
from procesamientos.models import GeneracionSifa, ProcesamientoSifa
from procesamientos.services.generacion_sifa import (
    serializar_lineas_preparadas_sifa,
    serializar_resumen_contenedores_sifa,
    serializar_resumen_gastos_sifa,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.sifa.extractor import ErrorExtraccionSifa
from services.sifa.matcher import ErrorMatcherSifa
from services.sifa.processor import (
    ErrorProcesamientoSifa,
    ResultadoPreparacionSifa,
    preparar_procesamiento_sifa,
)
from services.sifa.writer import NOMBRE_DESCARGA_SIFA

logger = logging.getLogger(__name__)


def _serializar_incidencias(incidencias) -> list[dict]:
    return [
        {
            "codigo": item.codigo,
            "nivel": item.nivel,
            "mensaje": item.mensaje,
            "detalles": item.detalles,
        }
        for item in incidencias
    ]


def _aplicar_resultado_sifa(
    procesamiento: ProcesamientoSifa,
    resultado: ResultadoPreparacionSifa,
) -> None:
    despachos = resultado.despachos
    validacion = resultado.validacion

    procesamiento.semana = despachos.semana
    procesamiento.anio = despachos.anio
    procesamiento.semana_texto = despachos.semana_texto
    procesamiento.estado = resultado.estado
    procesamiento.destinos_despachos = list(
        validacion.destinos_despachos
    )
    procesamiento.cantidad_contenedores = len(
        despachos.contenedores
    )
    procesamiento.total_cajas_liquidacion = Decimal(
        validacion.total_cajas_liquidacion
    )
    procesamiento.total_cajas_despachos = Decimal(
        validacion.total_cajas_despachos
    )
    procesamiento.comision_total = validacion.comision_total
    procesamiento.lineas_con_comision = (
        validacion.lineas_con_comision
    )
    procesamiento.lineas_sin_comision = (
        validacion.lineas_sin_comision
    )
    procesamiento.puede_escribir = resultado.puede_escribir
    procesamiento.errores = _serializar_incidencias(
        validacion.errores
    )
    procesamiento.advertencias = _serializar_incidencias(
        validacion.advertencias
    )
    procesamiento.lineas_preparadas = (
        serializar_lineas_preparadas_sifa(
            validacion.lineas_preparadas
        )
    )
    procesamiento.resumen_gastos = (
        serializar_resumen_gastos_sifa(
            validacion.resumen_gastos
        )
    )
    procesamiento.resumen_contenedores = (
        serializar_resumen_contenedores_sifa(
            validacion.resumen_contenedores
        )
    )
    procesamiento.total_venta_eur = validacion.total_venta_eur


def _eliminar_procesamiento_sifa(
    procesamiento: ProcesamientoSifa | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoSifa.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento SIFA %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (media_root / "procesamientos" / "sifa").resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


@login_required
def cargar_sifa(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/sifa_cargar.html",
            {**ctx, "formulario": FormularioCargaSifa()},
        )

    formulario = FormularioCargaSifa(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/sifa_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoSifa | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            factura_corta = (
                datos.get("factura_corta") or ""
            ).strip()
            procesamiento = ProcesamientoSifa(
                id=uuid.uuid4(),
                anio=datos["anio"],
                semana=datos["semana"],
                destino_ui=datos["destino"],
                factura_corta=factura_corta,
                estado="procesando",
                creado_por=request.user,
                creado_por_nombre=nombre,
            )
            procesamiento.archivo_despachos = datos[
                "archivo_despachos"
            ]
            procesamiento.archivo_liquidacion = datos[
                "archivo_liquidacion"
            ]
            procesamiento.archivo_cliente = datos[
                "archivo_cliente"
            ]
            procesamiento.save()

            resultado = preparar_procesamiento_sifa(
                ruta_liquidacion=(
                    procesamiento.archivo_liquidacion.path
                ),
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
                semana=datos["semana"],
                anio=datos["anio"],
                destino=datos["destino"],
                factura_corta=factura_corta or None,
            )
            _aplicar_resultado_sifa(procesamiento, resultado)
            procesamiento.save()

        return redirect(
            "procesamientos:sifa_detalle",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionSifa,
        ErrorMatcherSifa,
        ErrorProcesamientoSifa,
    ) as error:
        logger.exception("Error conocido SIFA")
        _eliminar_procesamiento_sifa(procesamiento)
        return render(
            request,
            "procesamientos/sifa_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado SIFA")
        _eliminar_procesamiento_sifa(procesamiento)
        return render(
            request,
            "procesamientos/sifa_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": (
                    "Ocurrió un error inesperado al validar "
                    "los archivos."
                ),
            },
            status=500,
        )


@login_required
def detalle_sifa(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoSifa,
        pk=procesamiento_id,
    )
    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionSifa.Estado.PENDIENTE,
                GeneracionSifa.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionSifa.Estado.COMPLETADO
        )
        .order_by("-solicitado_en")
        .first()
    )
    puede_solicitar = (
        procesamiento.puede_escribir
        and procesamiento.estado == "listo"
        and generacion_activa is None
    )
    resumen_gastos = procesamiento.resumen_gastos or {}
    total_gastos = Decimal("0")
    if isinstance(resumen_gastos, dict):
        gastos_items = []
        for nombre, valor in resumen_gastos.items():
            gastos_items.append(
                {"nombre": nombre, "valor": valor}
            )
            try:
                total_gastos += Decimal(str(valor))
            except Exception:
                pass
    else:
        gastos_items = []

    total_costos_estimado = total_gastos + Decimal(
        str(procesamiento.comision_total or 0)
    )

    return render(
        request,
        "procesamientos/sifa_validacion.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamiento": procesamiento,
            "generacion_activa": generacion_activa,
            "ultima_completada": ultima_completada,
            "puede_solicitar_generacion": puede_solicitar,
            "lineas": procesamiento.lineas_preparadas or [],
            "resumen_gastos": gastos_items,
            "total_gastos": total_gastos,
            "total_costos_estimado": total_costos_estimado,
            "resumen_contenedores": (
                procesamiento.resumen_contenedores or []
            ),
        },
    )


@login_required
@require_POST
def solicitar_generacion_sifa(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoSifa,
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
    ):
        return redirect(
            "procesamientos:sifa_detalle",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionSifa.Estado.PENDIENTE,
            GeneracionSifa.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:sifa_detalle",
            procesamiento_id=procesamiento.id,
        )

    try:
        generacion = GeneracionSifa.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionSifa.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
        )
    except IntegrityError:
        return redirect(
            "procesamientos:sifa_detalle",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:sifa_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def detalle_generacion_sifa(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionSifa.objects.select_related("procesamiento"),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/sifa_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def descargar_generacion_sifa(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionSifa,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404("La generación no está lista.")
    if not generacion.archivo_resultado:
        raise Http404("No hay archivo de resultado.")

    ruta = Path(generacion.archivo_resultado.path)
    if not ruta.is_file():
        raise Http404("El archivo ya no existe.")

    nombre = generacion.nombre_descarga or NOMBRE_DESCARGA_SIFA
    if nombre != NOMBRE_DESCARGA_SIFA:
        nombre = NOMBRE_DESCARGA_SIFA

    try:
        handle = ruta.open("rb")
    except OSError as error:
        raise Http404("No se pudo abrir el archivo.") from error

    return FileResponse(
        handle,
        as_attachment=True,
        filename=nombre,
    )
