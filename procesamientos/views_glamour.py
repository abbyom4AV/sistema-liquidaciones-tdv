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
    FormularioCargaGlamour,
    FormularioMapeoGastoGlamour,
)
from procesamientos.models import (
    GeneracionGlamour,
    MapeoGastoGlamour,
    ProcesamientoGlamour,
)
from procesamientos.services.generacion_glamour import (
    serializar_lineas_preparadas_glamour,
    serializar_resumen_gastos_glamour,
    serializar_rubros_no_mapeados,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.glamour.extractor import (
    ErrorExtraccionGlamour,
    clave_gasto,
)
from services.glamour.matcher import ErrorMatcherGlamour
from services.glamour.processor import (
    ErrorProcesamientoGlamour,
    ResultadoPreparacionGlamour,
    preparar_procesamiento_glamour,
)
from services.glamour.writer import NOMBRE_DESCARGA_GLAMOUR

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


def _aplicar_resultado_glamour(
    procesamiento: ProcesamientoGlamour,
    resultado: ResultadoPreparacionGlamour,
) -> None:
    liquidacion = resultado.liquidacion
    despachos = resultado.despachos
    validacion = resultado.validacion

    procesamiento.factura_corta = (
        procesamiento.factura_corta
        or liquidacion.factura_corta
    )
    procesamiento.semana = despachos.semana or procesamiento.semana
    procesamiento.anio = despachos.anio or procesamiento.anio
    procesamiento.semana_texto = despachos.semana_texto
    procesamiento.estado = resultado.estado
    procesamiento.destino_final = resultado.destino_final or ""
    if not procesamiento.destino_ui and resultado.destino_final:
        procesamiento.destino_ui = resultado.destino_final
    procesamiento.origen_destino = resultado.origen_destino or ""
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
    procesamiento.total_venta_eur = validacion.total_venta_eur
    procesamiento.total_gastos_eur = getattr(
        validacion,
        "total_gastos_eur",
        Decimal("0"),
    )
    procesamiento.puede_escribir = resultado.puede_escribir
    procesamiento.errores = _serializar_incidencias(
        validacion.errores
    )
    procesamiento.advertencias = _serializar_incidencias(
        validacion.advertencias
    )
    procesamiento.lineas_preparadas = (
        serializar_lineas_preparadas_glamour(
            validacion.lineas_preparadas
        )
    )
    procesamiento.rubros_no_mapeados = (
        serializar_rubros_no_mapeados(
            liquidacion.rubros_no_mapeados
        )
    )
    procesamiento.resumen_gastos = serializar_resumen_gastos_glamour(
        getattr(validacion, "resumen_gastos", None)
        or liquidacion.gastos
    )


def _eliminar_procesamiento_glamour(
    procesamiento: ProcesamientoGlamour | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoGlamour.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento Glamour %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (media_root / "procesamientos" / "glamour").resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


def _repreparar_procesamiento(
    procesamiento: ProcesamientoGlamour,
    mapeos_extra: dict[str, str] | None = None,
) -> ResultadoPreparacionGlamour:
    mapeos = mapeos_extra if mapeos_extra is not None else (
        MapeoGastoGlamour.obtener_mapeos_dict()
    )
    resultado = preparar_procesamiento_glamour(
        ruta_liquidacion=procesamiento.archivo_liquidacion.path,
        ruta_despachos=procesamiento.archivo_despachos.path,
        semana=procesamiento.semana,
        anio=procesamiento.anio,
        destino=(
            procesamiento.destino_ui
            or procesamiento.destino_final
        ),
        factura_corta=procesamiento.factura_corta,
        mapeos_extra=mapeos,
    )
    _aplicar_resultado_glamour(procesamiento, resultado)
    procesamiento.save()
    return resultado


def _guardar_mapeos_gastos(
    rubros: list[dict],
    nombre_usuario: str,
) -> None:
    for item in rubros:
        etiqueta = str(item.get("etiqueta") or "").strip()
        columna = str(item.get("columna_destino") or "").strip()
        if not etiqueta or not columna:
            continue
        normalizada = clave_gasto(etiqueta)
        MapeoGastoGlamour.objects.update_or_create(
            etiqueta_normalizada=normalizada,
            defaults={
                "etiqueta_original": etiqueta,
                "columna_destino": columna,
                "creado_por_nombre": nombre_usuario,
            },
        )


@login_required
def glamour_cargar(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/glamour_cargar.html",
            {**ctx, "formulario": FormularioCargaGlamour()},
        )

    formulario = FormularioCargaGlamour(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/glamour_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoGlamour | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoGlamour(
                id=uuid.uuid4(),
                factura_corta=datos["factura_corta"],
                semana=datos["semana"],
                anio=datos["anio"],
                destino_ui=datos["destino"],
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

            resultado = _repreparar_procesamiento(procesamiento)

        if resultado.estado == "pendiente_mapeo_gastos":
            return redirect(
                "procesamientos:glamour_mapear_gastos",
                procesamiento_id=procesamiento.id,
            )
        return redirect(
            "procesamientos:glamour_validacion",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionGlamour,
        ErrorMatcherGlamour,
        ErrorProcesamientoGlamour,
    ) as error:
        logger.exception("Error conocido Glamour")
        _eliminar_procesamiento_glamour(procesamiento)
        return render(
            request,
            "procesamientos/glamour_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception as error:
        logger.exception("Error inesperado Glamour")
        _eliminar_procesamiento_glamour(procesamiento)
        return render(
            request,
            "procesamientos/glamour_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": (
                    "Ocurrió un error inesperado al validar "
                    f"los archivos: {error}"
                ),
            },
            status=500,
        )


@login_required
def glamour_validacion(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoGlamour,
        pk=procesamiento_id,
    )
    if procesamiento.estado == "pendiente_mapeo_gastos":
        return redirect(
            "procesamientos:glamour_mapear_gastos",
            procesamiento_id=procesamiento.id,
        )

    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionGlamour.Estado.PENDIENTE,
                GeneracionGlamour.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionGlamour.Estado.COMPLETADO
        )
        .order_by("-solicitado_en")
        .first()
    )
    puede_solicitar = (
        procesamiento.puede_escribir
        and procesamiento.estado == "listo"
        and not procesamiento.tiene_rubros_pendientes
        and generacion_activa is None
    )

    resumen_gastos = procesamiento.resumen_gastos or {}
    gastos_items = []
    if isinstance(resumen_gastos, dict):
        for nombre, valor in resumen_gastos.items():
            try:
                decimal_valor = Decimal(str(valor))
            except Exception:
                decimal_valor = Decimal("0")
            if decimal_valor == 0:
                continue
            gastos_items.append(
                {"nombre": nombre, "valor": valor}
            )

    return render(
        request,
        "procesamientos/glamour_validacion.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamiento": procesamiento,
            "generacion_activa": generacion_activa,
            "ultima_completada": ultima_completada,
            "puede_solicitar_generacion": puede_solicitar,
            "lineas": procesamiento.lineas_preparadas or [],
            "gastos_items": gastos_items,
        },
    )


@login_required
def glamour_mapear_gastos(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoGlamour,
        pk=procesamiento_id,
    )
    rubros = list(procesamiento.rubros_no_mapeados or [])
    ctx = contexto_sesion(request, nav_activo="panel")

    if request.method != "POST":
        if not rubros and procesamiento.estado != (
            "pendiente_mapeo_gastos"
        ):
            return redirect(
                "procesamientos:glamour_validacion",
                procesamiento_id=procesamiento.id,
            )
        return render(
            request,
            "procesamientos/glamour_mapear_gastos.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formulario": FormularioMapeoGastoGlamour(
                    rubros=rubros
                ),
                "rubros": rubros,
            },
        )

    formulario = FormularioMapeoGastoGlamour(
        rubros=rubros,
        data=request.POST,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/glamour_mapear_gastos.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formulario": formulario,
                "rubros": rubros,
            },
            status=400,
        )

    nombre = obtener_nombre_usuario(request.user)
    try:
        with transaction.atomic():
            _guardar_mapeos_gastos(
                formulario.rubros_resueltos(),
                nombre,
            )
            mapeos = MapeoGastoGlamour.obtener_mapeos_dict()
            resultado = _repreparar_procesamiento(
                procesamiento,
                mapeos_extra=mapeos,
            )
    except (
        ErrorExtraccionGlamour,
        ErrorMatcherGlamour,
        ErrorProcesamientoGlamour,
    ) as error:
        return render(
            request,
            "procesamientos/glamour_mapear_gastos.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formulario": formulario,
                "rubros": rubros,
                "error_proceso": str(error),
            },
            status=400,
        )

    if resultado.estado == "pendiente_mapeo_gastos":
        return redirect(
            "procesamientos:glamour_mapear_gastos",
            procesamiento_id=procesamiento.id,
        )
    return redirect(
        "procesamientos:glamour_validacion",
        procesamiento_id=procesamiento.id,
    )


@login_required
@require_POST
def glamour_generar(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoGlamour,
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
        or procesamiento.tiene_rubros_pendientes
    ):
        return redirect(
            "procesamientos:glamour_validacion",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionGlamour.Estado.PENDIENTE,
            GeneracionGlamour.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:glamour_validacion",
            procesamiento_id=procesamiento.id,
        )

    try:
        generacion = GeneracionGlamour.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionGlamour.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
            destino_aplicado=procesamiento.destino_final,
            origen_destino_aplicado=(
                procesamiento.origen_destino or "despachos"
            ),
        )
    except IntegrityError:
        return redirect(
            "procesamientos:glamour_validacion",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:glamour_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def glamour_generacion_detalle(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionGlamour.objects.select_related("procesamiento"),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/glamour_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def glamour_generacion_descargar(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionGlamour,
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
        generacion.nombre_descarga or NOMBRE_DESCARGA_GLAMOUR
    )
    if nombre != NOMBRE_DESCARGA_GLAMOUR:
        nombre = NOMBRE_DESCARGA_GLAMOUR

    try:
        handle = ruta.open("rb")
    except OSError as error:
        raise Http404("No se pudo abrir el archivo.") from error

    return FileResponse(
        handle,
        as_attachment=True,
        filename=nombre,
    )
