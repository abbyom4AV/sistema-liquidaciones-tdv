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
    FormularioCargaEurobanan,
    FormularioMapeoGastoEurobanan,
)
from procesamientos.models import (
    GeneracionEurobanan,
    MapeoGastoEurobanan,
    ProcesamientoEurobanan,
)
from procesamientos.services.generacion_eurobanan import (
    serializar_lineas_preparadas_eurobanan,
    serializar_resumen_gastos_eurobanan,
    serializar_rubros_no_mapeados,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.eurobanan.extractor import (
    ErrorExtraccionEurobanan,
    clave_gasto,
)
from services.eurobanan.matcher import ErrorMatcherEurobanan
from services.eurobanan.processor import (
    ErrorProcesamientoEurobanan,
    ResultadoPreparacionEurobanan,
    preparar_procesamiento_eurobanan,
)
from services.eurobanan.writer import NOMBRE_DESCARGA_EUROBANAN

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


def _aplicar_resultado_eurobanan(
    procesamiento: ProcesamientoEurobanan,
    resultado: ResultadoPreparacionEurobanan,
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
        serializar_lineas_preparadas_eurobanan(
            validacion.lineas_preparadas
        )
    )
    procesamiento.rubros_no_mapeados = (
        serializar_rubros_no_mapeados(
            liquidacion.rubros_no_mapeados
        )
    )
    procesamiento.resumen_gastos = serializar_resumen_gastos_eurobanan(
        getattr(validacion, "resumen_gastos", None)
        or liquidacion.gastos
    )


def _eliminar_procesamiento_eurobanan(
    procesamiento: ProcesamientoEurobanan | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoEurobanan.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento Eurobanan %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (media_root / "procesamientos" / "eurobanan").resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


def _repreparar_procesamiento(
    procesamiento: ProcesamientoEurobanan,
    mapeos_extra: dict[str, str] | None = None,
) -> ResultadoPreparacionEurobanan:
    mapeos = mapeos_extra if mapeos_extra is not None else (
        MapeoGastoEurobanan.obtener_mapeos_dict()
    )
    resultado = preparar_procesamiento_eurobanan(
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
    _aplicar_resultado_eurobanan(procesamiento, resultado)
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
        MapeoGastoEurobanan.objects.update_or_create(
            etiqueta_normalizada=normalizada,
            defaults={
                "etiqueta_original": etiqueta,
                "columna_destino": columna,
                "creado_por_nombre": nombre_usuario,
            },
        )


@login_required
def eurobanan_cargar(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/eurobanan_cargar.html",
            {**ctx, "formulario": FormularioCargaEurobanan()},
        )

    formulario = FormularioCargaEurobanan(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/eurobanan_cargar.html",
            {**ctx, "formulario": formulario},
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoEurobanan | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoEurobanan(
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
                "procesamientos:eurobanan_mapear_gastos",
                procesamiento_id=procesamiento.id,
            )
        return redirect(
            "procesamientos:eurobanan_validacion",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionEurobanan,
        ErrorMatcherEurobanan,
        ErrorProcesamientoEurobanan,
    ) as error:
        logger.exception("Error conocido Eurobanan")
        _eliminar_procesamiento_eurobanan(procesamiento)
        return render(
            request,
            "procesamientos/eurobanan_cargar.html",
            {
                **ctx,
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )
    except Exception as error:
        logger.exception("Error inesperado Eurobanan")
        _eliminar_procesamiento_eurobanan(procesamiento)
        return render(
            request,
            "procesamientos/eurobanan_cargar.html",
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
def eurobanan_validacion(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoEurobanan,
        pk=procesamiento_id,
    )
    if procesamiento.estado == "pendiente_mapeo_gastos":
        return redirect(
            "procesamientos:eurobanan_mapear_gastos",
            procesamiento_id=procesamiento.id,
        )

    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionEurobanan.Estado.PENDIENTE,
                GeneracionEurobanan.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionEurobanan.Estado.COMPLETADO
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
    lineas = procesamiento.lineas_preparadas or []
    if lineas and isinstance(lineas[0], dict):
        try:
            comision = Decimal(str(lineas[0].get("comision_eur") or "0"))
        except Exception:
            comision = Decimal("0")
        if comision != 0 and not any(
            item.get("nombre") == "Comision €" for item in gastos_items
        ):
            gastos_items.append(
                {
                    "nombre": "Comision €",
                    "valor": _decimal_a_str(comision),
                }
            )

    return render(
        request,
        "procesamientos/eurobanan_validacion.html",
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
def eurobanan_mapear_gastos(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoEurobanan,
        pk=procesamiento_id,
    )
    rubros = list(procesamiento.rubros_no_mapeados or [])
    ctx = contexto_sesion(request, nav_activo="panel")

    if request.method != "POST":
        if not rubros and procesamiento.estado != (
            "pendiente_mapeo_gastos"
        ):
            return redirect(
                "procesamientos:eurobanan_validacion",
                procesamiento_id=procesamiento.id,
            )
        return render(
            request,
            "procesamientos/eurobanan_mapear_gastos.html",
            {
                **ctx,
                "procesamiento": procesamiento,
                "formulario": FormularioMapeoGastoEurobanan(
                    rubros=rubros
                ),
                "rubros": rubros,
            },
        )

    formulario = FormularioMapeoGastoEurobanan(
        rubros=rubros,
        data=request.POST,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/eurobanan_mapear_gastos.html",
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
            mapeos = MapeoGastoEurobanan.obtener_mapeos_dict()
            resultado = _repreparar_procesamiento(
                procesamiento,
                mapeos_extra=mapeos,
            )
    except (
        ErrorExtraccionEurobanan,
        ErrorMatcherEurobanan,
        ErrorProcesamientoEurobanan,
    ) as error:
        return render(
            request,
            "procesamientos/eurobanan_mapear_gastos.html",
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
            "procesamientos:eurobanan_mapear_gastos",
            procesamiento_id=procesamiento.id,
        )
    return redirect(
        "procesamientos:eurobanan_validacion",
        procesamiento_id=procesamiento.id,
    )


@login_required
@require_POST
def eurobanan_generar(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoEurobanan,
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
        or procesamiento.tiene_rubros_pendientes
    ):
        return redirect(
            "procesamientos:eurobanan_validacion",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionEurobanan.Estado.PENDIENTE,
            GeneracionEurobanan.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:eurobanan_validacion",
            procesamiento_id=procesamiento.id,
        )

    try:
        generacion = GeneracionEurobanan.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionEurobanan.Estado.PENDIENTE,
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
            "procesamientos:eurobanan_validacion",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:eurobanan_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def eurobanan_generacion_detalle(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionEurobanan.objects.select_related("procesamiento"),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/eurobanan_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def eurobanan_generacion_descargar(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionEurobanan,
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
        generacion.nombre_descarga or NOMBRE_DESCARGA_EUROBANAN
    )
    if nombre != NOMBRE_DESCARGA_EUROBANAN:
        nombre = NOMBRE_DESCARGA_EUROBANAN

    try:
        handle = ruta.open("rb")
    except OSError as error:
        raise Http404("No se pudo abrir el archivo.") from error

    return FileResponse(
        handle,
        as_attachment=True,
        filename=nombre,
    )
