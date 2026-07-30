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

from procesamientos.forms import (
    FormularioCargaMaster,
    FormularioMotivoCorreccion,
    FormsetGastosMaster,
)
from procesamientos.models import (
    RUBROS_GASTOS_MASTER_DEFINICION,
    CorreccionGastoMaster,
    GastoProcesamientoMaster,
    GeneracionMaster,
    ProcesamientoMaster,
)
from procesamientos.services.generacion_master import (
    serializar_gastos_aplicados_master,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.master.extractor import ErrorExtraccionMaster
from services.master.matcher import ErrorMatcherMaster
from services.master.processor import (
    ErrorProcesamientoMaster,
    ResultadoPreparacionMaster,
    preparar_procesamiento_master,
)
from services.master.writer import NOMBRE_DESCARGA_MASTER

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


def _serializar_lineas_master(lineas) -> list[dict]:
    resultado = []
    for linea in lineas:
        despacho = linea.despacho
        resultado.append(
            {
                "contenedor": despacho.contenedor,
                "nave": despacho.barco,
                "destino": despacho.puerto_destino,
                "tipo_fruta": linea.tipo_fruta,
                "variante": linea.variante,
                "carton": despacho.carton,
                "calibre": linea.calibre,
                "total_cajas": despacho.total_cajas,
                "merma": linea.merma,
                "precio_venta_eur": _decimal_a_str(
                    linea.precio_venta_eur
                ),
            }
        )
    return resultado


def _aplicar_resultado_master(
    procesamiento: ProcesamientoMaster,
    resultado: ResultadoPreparacionMaster,
) -> None:
    liquidacion = resultado.liquidacion
    despachos = resultado.despachos
    validacion = resultado.validacion

    procesamiento.factura_corta = liquidacion.factura_corta
    procesamiento.semana = despachos.semana
    procesamiento.anio = despachos.anio
    procesamiento.semana_texto = despachos.semana_texto
    procesamiento.estado = resultado.estado
    procesamiento.destino_final = resultado.destino_final or ""
    procesamiento.origen_destino_final = (
        resultado.origen_destino or ""
    )
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
    procesamiento.lineas_preparadas = _serializar_lineas_master(
        validacion.lineas_preparadas
    )


def _crear_gastos_master(
    procesamiento: ProcesamientoMaster,
    resultado: ResultadoPreparacionMaster,
) -> None:
    gastos = resultado.liquidacion.gastos
    registros = []
    for codigo, nombre, orden in RUBROS_GASTOS_MASTER_DEFINICION:
        if nombre not in gastos:
            continue
        valor = gastos[nombre]
        if not isinstance(valor, Decimal):
            valor = Decimal(_decimal_a_str(valor))
        registros.append(
            GastoProcesamientoMaster(
                procesamiento=procesamiento,
                codigo=codigo,
                nombre=nombre,
                orden=orden,
                valor_original=valor,
                valor_aplicado=valor,
            )
        )
    GastoProcesamientoMaster.objects.bulk_create(registros)


def _eliminar_procesamiento_master(
    procesamiento: ProcesamientoMaster | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoMaster.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento Master %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (media_root / "procesamientos" / "master").resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


@login_required
def cargar_master(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/master_cargar.html",
            {**ctx, "formulario": FormularioCargaMaster()},
        )

    formulario = FormularioCargaMaster(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/master_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoMaster | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoMaster(
                id=uuid.uuid4(),
                factura_corta="",
                semana=0,
                anio=0,
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

            resultado = preparar_procesamiento_master(
                ruta_liquidacion=(
                    procesamiento.archivo_liquidacion.path
                ),
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
            )
            _aplicar_resultado_master(
                procesamiento,
                resultado,
            )
            procesamiento.save()
            _crear_gastos_master(procesamiento, resultado)

        return redirect(
            "procesamientos:master_detalle",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionMaster,
        ErrorMatcherMaster,
        ErrorProcesamientoMaster,
    ) as error:
        logger.exception("Error conocido Master Fruits")
        _eliminar_procesamiento_master(procesamiento)
        return render(
            request,
            "procesamientos/master_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado Master Fruits")
        _eliminar_procesamiento_master(procesamiento)
        return render(
            request,
            "procesamientos/master_cargar.html",
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
def detalle_master(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoMaster.objects.prefetch_related("gastos"),
        pk=procesamiento_id,
    )
    gastos = list(procesamiento.gastos.order_by("orden"))
    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionMaster.Estado.PENDIENTE,
                GeneracionMaster.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionMaster.Estado.COMPLETADO
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
        "procesamientos/master_validacion.html",
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
def editar_gastos_master(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoMaster,
        pk=procesamiento_id,
    )
    queryset = procesamiento.gastos.order_by("orden")
    ctx = contexto_sesion(request, nav_activo="panel")

    if request.method != "POST":
        return render(
            request,
            "procesamientos/master_gastos_editar.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formset": FormsetGastosMaster(queryset=queryset),
                "formulario_motivo": FormularioMotivoCorreccion(),
            },
        )

    formset = FormsetGastosMaster(
        request.POST,
        queryset=queryset,
    )
    formulario_motivo = FormularioMotivoCorreccion(request.POST)
    if not formset.is_valid() or not formulario_motivo.is_valid():
        return render(
            request,
            "procesamientos/master_gastos_editar.html",
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
            CorreccionGastoMaster.objects.create(
                gasto=gasto,
                valor_anterior=anterior,
                valor_nuevo=nuevo,
                motivo=motivo,
                usuario=request.user,
                usuario_nombre=nombre,
            )

    return redirect(
        "procesamientos:master_detalle",
        procesamiento_id=procesamiento.id,
    )


@login_required
@require_POST
def solicitar_generacion_master(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoMaster.objects.prefetch_related("gastos"),
        pk=procesamiento_id,
    )
    if not procesamiento.puede_escribir or procesamiento.estado != "listo":
        return redirect(
            "procesamientos:master_detalle",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionMaster.Estado.PENDIENTE,
            GeneracionMaster.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:master_detalle",
            procesamiento_id=procesamiento.id,
        )

    gastos = serializar_gastos_aplicados_master(
        procesamiento.obtener_gastos_aplicados()
    )
    try:
        generacion = GeneracionMaster.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionMaster.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
            destino_aplicado=procesamiento.destino_final,
            origen_destino_aplicado=(
                procesamiento.origen_destino_final or "despachos"
            ),
            gastos_aplicados=gastos,
        )
    except IntegrityError:
        return redirect(
            "procesamientos:master_detalle",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:master_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def detalle_generacion_master(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionMaster.objects.select_related("procesamiento"),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/master_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def descargar_generacion_master(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionMaster,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404("La generación no está lista.")
    if not generacion.archivo_resultado:
        raise Http404("No hay archivo de resultado.")

    ruta = Path(generacion.archivo_resultado.path)
    if not ruta.is_file():
        raise Http404("El archivo ya no existe.")

    nombre = generacion.nombre_descarga or NOMBRE_DESCARGA_MASTER
    if nombre != NOMBRE_DESCARGA_MASTER:
        nombre = NOMBRE_DESCARGA_MASTER

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
