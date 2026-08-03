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

from procesamientos.forms import FormularioCargaKraaijeveld
from procesamientos.models import (
    ArchivoPdfKraaijeveld,
    GeneracionKraaijeveld,
    ProcesamientoKraaijeveld,
)
from procesamientos.services.generacion_kraaijeveld import (
    serializar_lineas_preparadas_kraaijeveld,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.kraaijeveld.extractor import ErrorExtraccionKraaijeveld
from services.kraaijeveld.matcher import ErrorMatcherKraaijeveld
from services.kraaijeveld.processor import (
    ErrorProcesamientoKraaijeveld,
    ResultadoPreparacionKraaijeveld,
    preparar_procesamiento_kraaijeveld,
)
from services.kraaijeveld.writer import NOMBRE_DESCARGA_KRAAIJEVELD

logger = logging.getLogger(__name__)


def _decimal_a_str(valor) -> str:
    if isinstance(valor, Decimal):
        texto = format(valor, "f")
    else:
        texto = str(valor)
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


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
                "commission_order": item.commission_order,
                "factura_corta": item.factura_corta,
                "comision": _decimal_a_str(item.comision),
                "gastos": {
                    nombre: _decimal_a_str(valor)
                    for nombre, valor in item.gastos.items()
                },
                "rubros_pdf": list(item.rubros_pdf),
            }
        )
    return resultado


def _aplicar_resultado_kraaijeveld(
    procesamiento: ProcesamientoKraaijeveld,
    resultado: ResultadoPreparacionKraaijeveld,
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
        serializar_lineas_preparadas_kraaijeveld(
            validacion.lineas_preparadas
        )
    )
    procesamiento.resumen_gastos_contenedores = (
        _serializar_resumen_gastos(
            validacion.resumen_gastos_contenedores
        )
    )


def _eliminar_procesamiento_kraaijeveld(
    procesamiento: ProcesamientoKraaijeveld | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoKraaijeveld.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento Kraaijeveld %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (
        media_root / "procesamientos" / "kraaijeveld"
    ).resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


@login_required
def cargar_kraaijeveld(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/kraaijeveld_cargar.html",
            {**ctx, "formulario": FormularioCargaKraaijeveld()},
        )

    formulario = FormularioCargaKraaijeveld(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/kraaijeveld_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoKraaijeveld | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            moneda_fijo = (
                (datos.get("moneda_fijo") or "").strip().upper()
                if datos.get("incluye_precio_fijo")
                else ""
            )
            factura_fijo = (
                datos.get("factura_corta_fijo") or ""
                if datos.get("incluye_precio_fijo")
                else ""
            )
            precio_fijo = (
                datos.get("precio_fijo")
                if datos.get("incluye_precio_fijo")
                else None
            )

            procesamiento = ProcesamientoKraaijeveld(
                id=uuid.uuid4(),
                anio=datos["anio"],
                semana=0,
                destino_ui=datos["destino"],
                incluye_precio_fijo=bool(
                    datos.get("incluye_precio_fijo")
                ),
                factura_corta_fijo=factura_fijo,
                precio_fijo=precio_fijo,
                moneda_fijo=moneda_fijo,
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
                registro = ArchivoPdfKraaijeveld(
                    procesamiento=procesamiento,
                    nombre_original=(
                        getattr(archivo, "name", "") or ""
                    ),
                    orden=indice,
                )
                registro.archivo = archivo
                registro.save()
                rutas_pdf.append(registro.archivo.path)

            resultado = preparar_procesamiento_kraaijeveld(
                rutas_pdf=rutas_pdf,
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
                semana=datos["semana"],
                anio=datos["anio"],
                destino=datos["destino"],
                incluye_precio_fijo=bool(
                    datos.get("incluye_precio_fijo")
                ),
                factura_corta_fijo=factura_fijo or None,
                precio_fijo=precio_fijo,
                moneda_fijo=moneda_fijo or None,
            )
            _aplicar_resultado_kraaijeveld(
                procesamiento,
                resultado,
            )
            procesamiento.save()

        return redirect(
            "procesamientos:kraaijeveld_detalle",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionKraaijeveld,
        ErrorMatcherKraaijeveld,
        ErrorProcesamientoKraaijeveld,
    ) as error:
        logger.exception("Error conocido Kraaijeveld")
        _eliminar_procesamiento_kraaijeveld(procesamiento)
        return render(
            request,
            "procesamientos/kraaijeveld_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado Kraaijeveld")
        _eliminar_procesamiento_kraaijeveld(procesamiento)
        return render(
            request,
            "procesamientos/kraaijeveld_cargar.html",
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
def detalle_kraaijeveld(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoKraaijeveld.objects.prefetch_related("pdfs"),
        pk=procesamiento_id,
    )
    pdfs = list(procesamiento.pdfs.order_by("orden"))
    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionKraaijeveld.Estado.PENDIENTE,
                GeneracionKraaijeveld.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionKraaijeveld.Estado.COMPLETADO
        )
        .order_by("-solicitado_en")
        .first()
    )
    puede_solicitar = (
        procesamiento.puede_escribir
        and procesamiento.estado == "listo"
        and generacion_activa is None
    )
    return render(
        request,
        "procesamientos/kraaijeveld_validacion.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamiento": procesamiento,
            "pdfs": pdfs,
            "generacion_activa": generacion_activa,
            "ultima_completada": ultima_completada,
            "puede_solicitar_generacion": puede_solicitar,
            "lineas": procesamiento.lineas_preparadas or [],
            "resumen_gastos": (
                procesamiento.resumen_gastos_contenedores or []
            ),
        },
    )


@login_required
@require_POST
def solicitar_generacion_kraaijeveld(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoKraaijeveld,
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
    ):
        return redirect(
            "procesamientos:kraaijeveld_detalle",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionKraaijeveld.Estado.PENDIENTE,
            GeneracionKraaijeveld.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:kraaijeveld_detalle",
            procesamiento_id=procesamiento.id,
        )

    try:
        generacion = GeneracionKraaijeveld.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionKraaijeveld.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
        )
    except IntegrityError:
        return redirect(
            "procesamientos:kraaijeveld_detalle",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:kraaijeveld_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def detalle_generacion_kraaijeveld(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionKraaijeveld.objects.select_related(
            "procesamiento"
        ),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/kraaijeveld_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def descargar_generacion_kraaijeveld(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionKraaijeveld,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404("La generación no está lista.")
    if not generacion.archivo_resultado:
        raise Http404("No hay archivo de resultado.")

    ruta = Path(generacion.archivo_resultado.path)
    if not ruta.is_file():
        raise Http404("El archivo ya no existe.")

    nombre = (
        generacion.nombre_descarga or NOMBRE_DESCARGA_KRAAIJEVELD
    )
    if nombre != NOMBRE_DESCARGA_KRAAIJEVELD:
        nombre = NOMBRE_DESCARGA_KRAAIJEVELD

    try:
        handle = ruta.open("rb")
    except OSError as error:
        raise Http404("No se pudo abrir el archivo.") from error

    respuesta = FileResponse(
        handle,
        as_attachment=True,
        filename=nombre,
    )
    return respuesta
