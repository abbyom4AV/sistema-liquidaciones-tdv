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
    FormularioCargaVisafruits,
    FormularioMotivoCorreccion,
    FormsetGastosVisafruits,
)
from procesamientos.models import (
    RUBROS_GASTOS_VISAFRUITS_DEFINICION,
    CorreccionGastoVisafruits,
    GastoProcesamientoVisafruits,
    GeneracionVisafruits,
    ProcesamientoVisafruits,
)
from procesamientos.services.generacion_visafruits import (
    serializar_gastos_aplicados_visafruits,
    serializar_lineas_preparadas_visafruits,
    serializar_resumen_gastos_visafruits,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.visafruits.extractor import (
    ErrorExtraccionVisafruits,
)
from services.visafruits.matcher import (
    CLIENTE_VISAFRUITS_DESPACHOS,
    ErrorMatcherVisafruits,
)
from services.visafruits.processor import (
    ErrorProcesamientoVisafruits,
    ResultadoPreparacionVisafruits,
    preparar_procesamiento_visafruits,
)
from services.visafruits.writer import (
    NOMBRE_DESCARGA_VISAFRUITS,
)

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


def _aplicar_resultado_visafruits(
    procesamiento: ProcesamientoVisafruits,
    resultado: ResultadoPreparacionVisafruits,
) -> None:
    despachos = resultado.despachos
    validacion = resultado.validacion

    procesamiento.semana = despachos.semana
    procesamiento.semana_texto = despachos.semana_texto
    procesamiento.factura_corta = validacion.factura_corta
    procesamiento.nave = validacion.nave
    procesamiento.estado = resultado.estado
    procesamiento.contenedores = list(validacion.contenedores)
    procesamiento.destinos_despachos = list(despachos.destinos)
    procesamiento.total_cajas_liquidacion = (
        validacion.total_cajas_liquidacion
    )
    procesamiento.total_cajas_despachos = (
        validacion.total_cajas_despachos
    )
    procesamiento.total_venta_liquidacion_eur = (
        validacion.total_venta_liquidacion_eur
    )
    procesamiento.total_venta_calculado_eur = (
        validacion.total_venta_calculado_eur
    )
    procesamiento.lineas_sin_precio = sum(
        1
        for linea in validacion.lineas_preparadas
        if not linea.precio_encontrado
    )
    procesamiento.puede_escribir = resultado.puede_escribir
    procesamiento.errores = _serializar_incidencias(
        validacion.errores
    )
    procesamiento.advertencias = _serializar_incidencias(
        validacion.advertencias
    )
    procesamiento.lineas_preparadas = (
        serializar_lineas_preparadas_visafruits(
            validacion.lineas_preparadas
        )
    )
    procesamiento.resumen_gastos = (
        serializar_resumen_gastos_visafruits(validacion.gastos)
    )


def _crear_gastos_visafruits(
    procesamiento: ProcesamientoVisafruits,
    resultado: ResultadoPreparacionVisafruits,
) -> None:
    """La comisión se registra como un rubro más para que se pueda
    corregir en la misma pantalla que los gastos."""
    montos = dict(resultado.validacion.gastos)
    montos["Comision Euros"] = resultado.validacion.comision_eur

    registros = []
    for codigo, nombre, orden in RUBROS_GASTOS_VISAFRUITS_DEFINICION:
        valor = montos.get(nombre, Decimal("0"))
        if not isinstance(valor, Decimal):
            valor = Decimal(_decimal_a_str(valor))
        registros.append(
            GastoProcesamientoVisafruits(
                procesamiento=procesamiento,
                codigo=codigo,
                nombre=nombre,
                orden=orden,
                valor_original=valor,
                valor_aplicado=valor,
            )
        )
    GastoProcesamientoVisafruits.objects.bulk_create(registros)


def _eliminar_procesamiento_visafruits(
    procesamiento: ProcesamientoVisafruits | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoVisafruits.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento Visafruits %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (
        media_root / "procesamientos" / "visafruits"
    ).resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


@login_required
def cargar_visafruits(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/visafruits_cargar.html",
            {**ctx, "formulario": FormularioCargaVisafruits()},
        )

    formulario = FormularioCargaVisafruits(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/visafruits_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoVisafruits | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoVisafruits(
                id=uuid.uuid4(),
                anio=datos["anio"],
                semana=datos["semana"],
                destino_ui=datos["destino"],
                factura_corta=datos["factura"],
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

            resultado = preparar_procesamiento_visafruits(
                ruta_liquidacion=(
                    procesamiento.archivo_liquidacion.path
                ),
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
                semana=procesamiento.semana,
                anio=procesamiento.anio,
                destino=procesamiento.destino_ui,
                factura_corta=procesamiento.factura_corta,
                cliente=CLIENTE_VISAFRUITS_DESPACHOS,
            )
            _aplicar_resultado_visafruits(
                procesamiento,
                resultado,
            )
            procesamiento.save()
            _crear_gastos_visafruits(procesamiento, resultado)

        return redirect(
            "procesamientos:visafruits_detalle",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionVisafruits,
        ErrorMatcherVisafruits,
        ErrorProcesamientoVisafruits,
    ) as error:
        logger.exception("Error conocido Visafruits")
        _eliminar_procesamiento_visafruits(procesamiento)
        return render(
            request,
            "procesamientos/visafruits_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado Visafruits")
        _eliminar_procesamiento_visafruits(procesamiento)
        return render(
            request,
            "procesamientos/visafruits_cargar.html",
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
def detalle_visafruits(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoVisafruits.objects.prefetch_related("gastos"),
        pk=procesamiento_id,
    )
    gastos = list(procesamiento.gastos.order_by("orden"))
    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionVisafruits.Estado.PENDIENTE,
                GeneracionVisafruits.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_generacion = (
        procesamiento.generaciones.filter(
            estado=GeneracionVisafruits.Estado.COMPLETADO
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
        "procesamientos/visafruits_validacion.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamiento": procesamiento,
            "gastos": gastos,
            "generacion_activa": generacion_activa,
            "ultima_generacion": ultima_generacion,
            "puede_solicitar": puede_solicitar,
        },
    )


@login_required
def editar_gastos_visafruits(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoVisafruits,
        pk=procesamiento_id,
    )
    queryset = procesamiento.gastos.order_by("orden")
    ctx = contexto_sesion(request, nav_activo="panel")

    if request.method != "POST":
        return render(
            request,
            "procesamientos/visafruits_gastos_editar.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formset": FormsetGastosVisafruits(
                    queryset=queryset
                ),
                "formulario_motivo": FormularioMotivoCorreccion(),
            },
        )

    formset = FormsetGastosVisafruits(
        request.POST,
        queryset=queryset,
    )
    formulario_motivo = FormularioMotivoCorreccion(request.POST)
    if not formset.is_valid() or not formulario_motivo.is_valid():
        return render(
            request,
            "procesamientos/visafruits_gastos_editar.html",
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
            CorreccionGastoVisafruits.objects.create(
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
        "procesamientos:visafruits_detalle",
        procesamiento_id=procesamiento.id,
    )


@login_required
@require_POST
def solicitar_generacion_visafruits(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoVisafruits.objects.prefetch_related("gastos"),
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
    ):
        return redirect(
            "procesamientos:visafruits_detalle",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionVisafruits.Estado.PENDIENTE,
            GeneracionVisafruits.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:visafruits_detalle",
            procesamiento_id=procesamiento.id,
        )

    gastos = serializar_gastos_aplicados_visafruits(
        procesamiento.obtener_gastos_aplicados()
    )
    try:
        generacion = GeneracionVisafruits.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionVisafruits.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
            gastos_aplicados=gastos,
        )
    except IntegrityError:
        return redirect(
            "procesamientos:visafruits_detalle",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:visafruits_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def detalle_generacion_visafruits(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionVisafruits.objects.select_related(
            "procesamiento"
        ),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/visafruits_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def descargar_generacion_visafruits(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionVisafruits,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404("La generación no está lista.")
    if not generacion.archivo_resultado:
        raise Http404("No hay archivo de resultado.")

    ruta = Path(generacion.archivo_resultado.path)
    if not ruta.is_file():
        raise Http404("El archivo ya no existe.")

    try:
        handle = ruta.open("rb")
    except OSError as error:
        raise Http404("No se pudo abrir el archivo.") from error

    return FileResponse(
        handle,
        as_attachment=True,
        filename=NOMBRE_DESCARGA_VISAFRUITS,
    )
