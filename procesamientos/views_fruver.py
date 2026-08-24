from __future__ import annotations

import logging
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from procesamientos.forms import FormularioCargaFruver
from procesamientos.http_descargas import respuesta_descarga_xlsx
from procesamientos.models import (
    ArchivoPdfFruver,
    GeneracionFruver,
    ProcesamientoFruver,
)
from procesamientos.services.cuadre_fruver import (
    completar_totales_pdf_resumen,
    decimal_a_str_fruver,
    enriquecer_resumen_cuadre,
    totales_cuadre,
)
from procesamientos.services.generacion_fruver import (
    serializar_lineas_preparadas_fruver,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.fruver.extractor import ErrorExtraccionFruver
from services.fruver.matcher import ErrorMatcherFruver
from services.fruver.processor import (
    ErrorProcesamientoFruver,
    ResultadoPreparacionFruver,
    preparar_procesamiento_fruver,
)
from services.fruver.writer import NOMBRE_DESCARGA_FRUVER

logger = logging.getLogger(__name__)


def _decimal_a_str(valor) -> str:
    return decimal_a_str_fruver(valor)


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


def _serializar_resumen_gastos(resumen) -> list[dict]:
    resultado = []
    for item in resumen:
        resultado.append(
            {
                "contenedor": item.contenedor,
                "factura_corta": item.factura_corta,
                "comision": _decimal_a_str(item.comision),
                "gastos": {
                    nombre: _decimal_a_str(valor)
                    for nombre, valor in item.gastos.items()
                },
                "total_cajas_pdf": _decimal_a_str(
                    item.total_cajas_pdf
                ),
                "total_cajas_despachos": _decimal_a_str(
                    item.total_cajas_despachos
                ),
                "total_venta_pdf": _decimal_a_str(
                    item.total_venta_pdf
                ),
                "total_venta_calc": _decimal_a_str(
                    item.total_venta_calc
                ),
                "total_gastos_pdf": _decimal_a_str(
                    item.total_gastos_pdf
                ),
                "total_gastos_calc": _decimal_a_str(
                    item.total_gastos_calc
                ),
                "venta_cuadra": item.venta_cuadra,
                "gastos_cuadran": item.gastos_cuadran,
                "flete_eur": _decimal_a_str(item.flete_eur),
            }
        )
    return resultado


def _aplicar_resultado_fruver(
    procesamiento: ProcesamientoFruver,
    resultado: ResultadoPreparacionFruver,
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
    procesamiento.puede_escribir = resultado.puede_escribir
    procesamiento.errores = _serializar_incidencias(
        validacion.errores
    )
    procesamiento.advertencias = _serializar_incidencias(
        validacion.advertencias
    )
    procesamiento.lineas_preparadas = (
        serializar_lineas_preparadas_fruver(
            validacion.lineas_preparadas
        )
    )
    procesamiento.resumen_gastos_contenedores = (
        _serializar_resumen_gastos(
            validacion.resumen_gastos_contenedores
        )
    )


def _eliminar_procesamiento_fruver(
    procesamiento: ProcesamientoFruver | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoFruver.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento Fruver %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (
        media_root / "procesamientos" / "fruver"
    ).resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


@login_required
def cargar_fruver(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/fruver_cargar.html",
            {**ctx, "formulario": FormularioCargaFruver()},
        )

    formulario = FormularioCargaFruver(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/fruver_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoFruver | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoFruver(
                id=uuid.uuid4(),
                anio=datos["anio"],
                semana=0,
                destino_ui=datos["destino"],
                factura_corta=datos["factura_corta"],
                estado="procesando",
                creado_por=request.user,
                creado_por_nombre=nombre,
            )
            procesamiento.archivo_despachos = datos[
                "archivo_despachos"
            ]
            procesamiento.archivo_cliente = datos[
                "archivo_cliente"
            ]
            procesamiento.save()

            rutas_pdf: list[str] = []
            for indice, archivo in enumerate(
                datos["archivos_pdf"],
                start=1,
            ):
                registro = ArchivoPdfFruver(
                    procesamiento=procesamiento,
                    nombre_original=(
                        getattr(archivo, "name", "") or ""
                    ),
                    orden=indice,
                )
                registro.archivo = archivo
                registro.save()
                rutas_pdf.append(registro.archivo.path)

            resultado = preparar_procesamiento_fruver(
                rutas_pdf=rutas_pdf,
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
                semana=datos["semana"],
                anio=datos["anio"],
                destino=datos["destino"],
                factura_corta=datos["factura_corta"],
            )
            _aplicar_resultado_fruver(
                procesamiento,
                resultado,
            )
            procesamiento.save()

        return redirect(
            "procesamientos:fruver_detalle",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionFruver,
        ErrorMatcherFruver,
        ErrorProcesamientoFruver,
    ) as error:
        logger.exception("Error conocido Fruver")
        _eliminar_procesamiento_fruver(procesamiento)
        return render(
            request,
            "procesamientos/fruver_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado Fruver")
        _eliminar_procesamiento_fruver(procesamiento)
        return render(
            request,
            "procesamientos/fruver_cargar.html",
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
def detalle_fruver(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoFruver.objects.prefetch_related("pdfs"),
        pk=procesamiento_id,
    )
    pdfs = list(procesamiento.pdfs.order_by("orden"))
    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionFruver.Estado.PENDIENTE,
                GeneracionFruver.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionFruver.Estado.COMPLETADO
        )
        .order_by("-solicitado_en")
        .first()
    )
    puede_solicitar = (
        procesamiento.puede_escribir
        and procesamiento.estado == "listo"
        and generacion_activa is None
    )
    lineas = procesamiento.lineas_preparadas or []
    resumen_crudo = list(
        procesamiento.resumen_gastos_contenedores or []
    )
    resumen_crudo, resumen_actualizado = completar_totales_pdf_resumen(
        resumen_crudo,
        pdfs,
    )
    if resumen_actualizado:
        procesamiento.resumen_gastos_contenedores = resumen_crudo
        procesamiento.save(
            update_fields=["resumen_gastos_contenedores"]
        )
    resumen_gastos = enriquecer_resumen_cuadre(
        resumen_crudo,
        lineas,
    )
    return render(
        request,
        "procesamientos/fruver_validacion.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamiento": procesamiento,
            "pdfs": pdfs,
            "generacion_activa": generacion_activa,
            "ultima_completada": ultima_completada,
            "puede_solicitar_generacion": puede_solicitar,
            "lineas": lineas,
            "resumen_gastos": resumen_gastos,
            "totales_cuadre": totales_cuadre(resumen_gastos),
        },
    )


@login_required
@require_POST
def solicitar_generacion_fruver(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoFruver,
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
    ):
        return redirect(
            "procesamientos:fruver_detalle",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionFruver.Estado.PENDIENTE,
            GeneracionFruver.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:fruver_detalle",
            procesamiento_id=procesamiento.id,
        )

    try:
        generacion = GeneracionFruver.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionFruver.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
        )
    except IntegrityError:
        return redirect(
            "procesamientos:fruver_detalle",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:fruver_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def detalle_generacion_fruver(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionFruver.objects.select_related(
            "procesamiento"
        ),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/fruver_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def descargar_generacion_fruver(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionFruver,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404("La generación no está lista.")
    if not generacion.archivo_resultado:
        raise Http404("No hay archivo de resultado.")

    ruta = Path(generacion.archivo_resultado.path)
    nombre = (
        generacion.nombre_descarga or NOMBRE_DESCARGA_FRUVER
    )
    if nombre != NOMBRE_DESCARGA_FRUVER:
        nombre = NOMBRE_DESCARGA_FRUVER
    return respuesta_descarga_xlsx(ruta, nombre)
