from __future__ import annotations

import logging
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from procesamientos.forms import (
    FormularioCargaOrsero,
    FormularioMotivoCorreccion,
    FormsetGastosOrsero,
)
from procesamientos.models import (
    RUBROS_GASTOS_ORSERO_DEFINICION,
    CorreccionGastoOrsero,
    GastoProcesamientoOrsero,
    GeneracionOrsero,
    ProcesamientoOrsero,
)
from procesamientos.services.generacion_orsero import (
    serializar_gastos_aplicados_orsero,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.orsero.extractor import ErrorExtraccionOrsero
from services.orsero.matcher import CLIENTE_ORSERO, ErrorMatcherOrsero
from services.orsero.processor import (
    ErrorProcesamientoOrsero,
    ResultadoPreparacionOrsero,
    preparar_procesamiento_orsero,
)
from services.orsero.writer import NOMBRE_DESCARGA_ORSERO

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


def _serializar_lineas_orsero(lineas) -> list[dict]:
    resultado = []
    for linea in lineas:
        despacho = linea.despacho
        resultado.append(
            {
                "contenedor": despacho.contenedor,
                "nave": despacho.barco,
                "destino": linea.destino,
                "tipo_fruta": linea.tipo_fruta,
                "carton": despacho.carton,
                "calibre": linea.calibre,
                "total_cajas": despacho.total_cajas,
                "precio_venta_eur": _decimal_a_str(
                    linea.precio_venta_eur
                ),
                "precio_encontrado": linea.precio_encontrado,
            }
        )
    return resultado


def _aplicar_resultado_orsero(
    procesamiento: ProcesamientoOrsero,
    resultado: ResultadoPreparacionOrsero,
) -> None:
    liquidacion = resultado.liquidacion
    despachos = resultado.despachos
    validacion = resultado.validacion

    procesamiento.semana = despachos.semana
    procesamiento.nave_texto = liquidacion.nave_texto
    procesamiento.tipo_cambio = liquidacion.tipo_cambio_usd_eur
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
    procesamiento.lineas_preparadas = _serializar_lineas_orsero(
        validacion.lineas_preparadas
    )


def _crear_gastos_orsero(
    procesamiento: ProcesamientoOrsero,
    resultado: ResultadoPreparacionOrsero,
) -> None:
    gastos = resultado.liquidacion.gastos
    registros = []
    for codigo, nombre, orden in RUBROS_GASTOS_ORSERO_DEFINICION:
        if nombre not in gastos:
            continue
        valor = gastos[nombre]
        if not isinstance(valor, Decimal):
            valor = Decimal(_decimal_a_str(valor))
        registros.append(
            GastoProcesamientoOrsero(
                procesamiento=procesamiento,
                codigo=codigo,
                nombre=nombre,
                orden=orden,
                valor_original=valor,
                valor_aplicado=valor,
            )
        )
    GastoProcesamientoOrsero.objects.bulk_create(registros)


def _eliminar_procesamiento_orsero(
    procesamiento: ProcesamientoOrsero | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoOrsero.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento Orsero %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (media_root / "procesamientos" / "orsero").resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


@login_required
def cargar_orsero(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/orsero_cargar.html",
            {**ctx, "formulario": FormularioCargaOrsero()},
        )

    formulario = FormularioCargaOrsero(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/orsero_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoOrsero | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoOrsero(
                id=uuid.uuid4(),
                anio=datos["anio"],
                semana=0,
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

            resultado = preparar_procesamiento_orsero(
                ruta_liquidacion=(
                    procesamiento.archivo_liquidacion.path
                ),
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
                anio=procesamiento.anio,
                cliente=CLIENTE_ORSERO,
            )
            _aplicar_resultado_orsero(
                procesamiento,
                resultado,
            )
            procesamiento.save()
            _crear_gastos_orsero(procesamiento, resultado)

        return redirect(
            "procesamientos:orsero_detalle",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionOrsero,
        ErrorMatcherOrsero,
        ErrorProcesamientoOrsero,
    ) as error:
        logger.exception("Error conocido Orsero")
        _eliminar_procesamiento_orsero(procesamiento)
        return render(
            request,
            "procesamientos/orsero_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado Orsero")
        _eliminar_procesamiento_orsero(procesamiento)
        return render(
            request,
            "procesamientos/orsero_cargar.html",
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
def detalle_orsero(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoOrsero.objects.prefetch_related("gastos"),
        pk=procesamiento_id,
    )
    gastos = list(procesamiento.gastos.order_by("orden"))
    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionOrsero.Estado.PENDIENTE,
                GeneracionOrsero.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionOrsero.Estado.COMPLETADO
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
        "procesamientos/orsero_validacion.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamiento": procesamiento,
            "gastos": gastos,
            "generacion_activa": generacion_activa,
            "ultima_completada": ultima_completada,
            "puede_solicitar_generacion": puede_solicitar,
            "lineas": procesamiento.lineas_preparadas or [],
            "total_gastos_originales": sum(
                (g.valor_original for g in gastos),
                Decimal("0"),
            ),
            "total_gastos_aplicados": sum(
                (g.valor_aplicado for g in gastos),
                Decimal("0"),
            ),
        },
    )


@login_required
def editar_gastos_orsero(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoOrsero,
        pk=procesamiento_id,
    )
    queryset = procesamiento.gastos.order_by("orden")
    ctx = contexto_sesion(request, nav_activo="panel")

    if request.method != "POST":
        return render(
            request,
            "procesamientos/orsero_gastos_editar.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formset": FormsetGastosOrsero(queryset=queryset),
                "formulario_motivo": FormularioMotivoCorreccion(),
            },
        )

    formset = FormsetGastosOrsero(
        request.POST,
        queryset=queryset,
    )
    formulario_motivo = FormularioMotivoCorreccion(request.POST)
    if not formset.is_valid() or not formulario_motivo.is_valid():
        return render(
            request,
            "procesamientos/orsero_gastos_editar.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formset": formset,
                "formulario_motivo": formulario_motivo,
            },
            status=400,
        )

    motivo = formulario_motivo.cleaned_data["motivo"]
    nombre = obtener_nombre_usuario(request.user)
    with transaction.atomic():
        for form in formset:
            gasto = form.instance
            anterior = gasto.valor_aplicado
            nuevo = form.cleaned_data["valor_aplicado"]
            if nuevo == anterior:
                continue
            gasto.valor_aplicado = nuevo
            gasto.save(update_fields=["valor_aplicado"])
            CorreccionGastoOrsero.objects.create(
                gasto=gasto,
                valor_anterior=anterior,
                valor_nuevo=nuevo,
                motivo=motivo,
                usuario=request.user,
                usuario_nombre=nombre,
            )

    messages.success(
        request,
        "Los gastos se actualizaron correctamente.",
    )
    return redirect(
        "procesamientos:orsero_detalle",
        procesamiento_id=procesamiento.id,
    )


@login_required
@require_POST
def solicitar_generacion_orsero(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoOrsero.objects.prefetch_related("gastos"),
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
    ):
        return redirect(
            "procesamientos:orsero_detalle",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionOrsero.Estado.PENDIENTE,
            GeneracionOrsero.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:orsero_detalle",
            procesamiento_id=procesamiento.id,
        )

    gastos = serializar_gastos_aplicados_orsero(
        procesamiento.obtener_gastos_aplicados()
    )
    try:
        generacion = GeneracionOrsero.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionOrsero.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
            gastos_aplicados=gastos,
        )
    except IntegrityError:
        return redirect(
            "procesamientos:orsero_detalle",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:orsero_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def detalle_generacion_orsero(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionOrsero.objects.select_related("procesamiento"),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/orsero_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def descargar_generacion_orsero(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionOrsero,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404("La generación no está lista.")
    if not generacion.archivo_resultado:
        raise Http404("No hay archivo de resultado.")

    ruta = Path(generacion.archivo_resultado.path)
    if not ruta.is_file():
        raise Http404("El archivo ya no existe.")

    nombre = generacion.nombre_descarga or NOMBRE_DESCARGA_ORSERO
    if nombre != NOMBRE_DESCARGA_ORSERO:
        nombre = NOMBRE_DESCARGA_ORSERO

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
