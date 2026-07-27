from __future__ import annotations

import logging
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from procesamientos.forms import (
    FormularioCargaDimanno,
    FormularioMotivoCorreccion,
    FormsetGastosDimanno,
)
from procesamientos.models import (
    RUBROS_GASTOS_DEFINICION,
    CorreccionGastoDimanno,
    GastoProcesamientoDimanno,
    ProcesamientoDimanno,
)
from services.dimanno.extractor import ErrorExtraccionDimanno
from services.dimanno.matcher import ErrorMatcherDimanno
from services.dimanno.processor import (
    ErrorProcesamientoDimanno,
    ResultadoPreparacionDimanno,
    preparar_procesamiento_dimanno,
)

logger = logging.getLogger(__name__)


def _decimal_a_str(valor: Decimal | int | str) -> str:
    if isinstance(valor, Decimal):
        return format(valor, "f")
    return str(valor)


def _serializar_incidencias(incidencias) -> list[dict[str, str]]:
    return [
        {
            "codigo": incidencia.codigo,
            "nivel": incidencia.nivel,
            "mensaje": incidencia.mensaje,
        }
        for incidencia in incidencias
    ]


def _serializar_lineas(lineas) -> list[dict[str, str | int]]:
    return [
        {
            "contenedor": linea.despacho.contenedor,
            "tipo_fruta": linea.tipo_fruta,
            "calibre": linea.calibre,
            "total_cajas": linea.despacho.total_cajas,
            "precio_venta_eur": _decimal_a_str(
                linea.precio_venta_eur
            ),
        }
        for linea in lineas
    ]


def _aplicar_resultado_a_procesamiento(
    procesamiento: ProcesamientoDimanno,
    resultado: ResultadoPreparacionDimanno,
) -> None:
    validacion = resultado.validacion
    liquidacion = resultado.liquidacion

    procesamiento.factura_corta = liquidacion.factura_corta
    procesamiento.semana = liquidacion.semana
    procesamiento.estado = resultado.estado
    procesamiento.destino_liquidacion = (
        validacion.destino_liquidacion or ""
    )
    procesamiento.destinos_despachos = list(
        validacion.destinos_despachos
    )
    procesamiento.destino_final = (
        resultado.destino_final or ""
    )
    procesamiento.cantidad_contenedores = len(
        liquidacion.contenedores
    )
    procesamiento.total_cajas_liquidacion = Decimal(
        validacion.total_cajas_liquidacion
    )
    procesamiento.total_cajas_despachos = Decimal(
        validacion.total_cajas_despachos
    )
    procesamiento.puede_escribir = resultado.puede_escribir
    procesamiento.requiere_resolver_destino = (
        validacion.requiere_resolver_destino
    )
    procesamiento.errores = _serializar_incidencias(
        validacion.errores
    )
    procesamiento.advertencias = _serializar_incidencias(
        validacion.advertencias
    )
    procesamiento.lineas_preparadas = _serializar_lineas(
        validacion.lineas_preparadas
    )


def _crear_gastos_desde_resultado(
    procesamiento: ProcesamientoDimanno,
    resultado: ResultadoPreparacionDimanno,
) -> None:
    gastos_extraidos = resultado.liquidacion.gastos
    registros: list[GastoProcesamientoDimanno] = []

    for codigo, nombre, orden in RUBROS_GASTOS_DEFINICION:
        if nombre not in gastos_extraidos:
            continue
        valor = gastos_extraidos[nombre]
        if not isinstance(valor, Decimal):
            valor = Decimal(_decimal_a_str(valor))
        registros.append(
            GastoProcesamientoDimanno(
                procesamiento=procesamiento,
                codigo=codigo,
                nombre=nombre,
                orden=orden,
                valor_original=valor,
                valor_aplicado=valor,
            )
        )

    GastoProcesamientoDimanno.objects.bulk_create(registros)


def _eliminar_procesamiento_y_archivos(
    procesamiento: ProcesamientoDimanno | None,
) -> None:
    if procesamiento is None:
        return

    procesamiento_id = procesamiento.id
    try:
        ProcesamientoDimanno.objects.filter(
            pk=procesamiento_id
        ).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar el registro incompleto %s.",
            procesamiento_id,
        )

    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (
        media_root / "procesamientos" / "dimanno"
    ).resolve()
    carpeta = (base / str(procesamiento_id)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists() and carpeta.is_dir():
        shutil.rmtree(carpeta, ignore_errors=True)


def cargar_dimanno(request):
    if request.method != "POST":
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": FormularioCargaDimanno(),
            },
        )

    formulario = FormularioCargaDimanno(
        request.POST,
        request.FILES,
    )

    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": formulario,
            },
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoDimanno | None = None

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoDimanno(
                id=uuid.uuid4(),
                anio=datos["anio"],
                nombre_hoja=datos["nombre_hoja"],
                factura_corta="",
                semana=0,
                estado="procesando",
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

            resultado = preparar_procesamiento_dimanno(
                ruta_liquidacion=(
                    procesamiento.archivo_liquidacion.path
                ),
                nombre_hoja=procesamiento.nombre_hoja,
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
                anio=procesamiento.anio,
            )

            _aplicar_resultado_a_procesamiento(
                procesamiento,
                resultado,
            )
            procesamiento.save()
            _crear_gastos_desde_resultado(
                procesamiento,
                resultado,
            )

        return redirect(
            "procesamientos:dimanno_detalle",
            procesamiento_id=procesamiento.id,
        )

    except (
        ErrorExtraccionDimanno,
        ErrorMatcherDimanno,
        ErrorProcesamientoDimanno,
    ) as error:
        logger.exception(
            "Error conocido al procesar Di Manno."
        )
        _eliminar_procesamiento_y_archivos(procesamiento)
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )

    except Exception:
        logger.exception(
            "Error inesperado al procesar Di Manno."
        )
        _eliminar_procesamiento_y_archivos(procesamiento)
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": formulario,
                "error_proceso": (
                    "Ocurrió un error inesperado al validar "
                    "los archivos. Revise el registro del "
                    "servidor para más detalle."
                ),
            },
            status=500,
        )


def detalle_dimanno(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoDimanno.objects.prefetch_related(
            "gastos"
        ),
        pk=procesamiento_id,
    )
    gastos = list(
        procesamiento.gastos.order_by("orden")
    )

    return render(
        request,
        "procesamientos/dimanno_validacion.html",
        {
            "procesamiento": procesamiento,
            "gastos": gastos,
            "total_gastos_originales": (
                procesamiento.total_gastos_originales
            ),
            "total_gastos_aplicados": (
                procesamiento.total_gastos_aplicados
            ),
        },
    )


def editar_gastos_dimanno(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoDimanno,
        pk=procesamiento_id,
    )
    queryset = GastoProcesamientoDimanno.objects.filter(
        procesamiento=procesamiento
    ).order_by("orden")
    autenticado = request.user.is_authenticated

    if request.method == "POST":
        formset = FormsetGastosDimanno(
            request.POST,
            queryset=queryset,
        )
        formulario_motivo = FormularioMotivoCorreccion(
            request.POST,
            usuario_autenticado=autenticado,
        )

        if formset.is_valid() and formulario_motivo.is_valid():
            motivo = formulario_motivo.cleaned_data["motivo"]
            if autenticado:
                usuario = request.user
                usuario_nombre = request.user.get_username()
            else:
                usuario = None
                usuario_nombre = formulario_motivo.cleaned_data[
                    "responsable"
                ]

            hay_cambios = False
            for formulario_gasto in formset:
                gasto_db = GastoProcesamientoDimanno.objects.get(
                    pk=formulario_gasto.instance.pk
                )
                if (
                    formulario_gasto.cleaned_data[
                        "valor_aplicado"
                    ]
                    != gasto_db.valor_aplicado
                ):
                    hay_cambios = True
                    break

            if not hay_cambios:
                formulario_motivo.add_error(
                    None,
                    "No se realizó ningún cambio en los gastos.",
                )
            else:
                with transaction.atomic():
                    gastos_bloqueados = {
                        gasto.id: gasto
                        for gasto in (
                            GastoProcesamientoDimanno.objects
                            .select_for_update()
                            .filter(
                                procesamiento=procesamiento
                            )
                        )
                    }

                    for formulario_gasto in formset:
                        gasto = gastos_bloqueados[
                            formulario_gasto.instance.pk
                        ]
                        valor_nuevo = (
                            formulario_gasto.cleaned_data[
                                "valor_aplicado"
                            ]
                        )
                        valor_anterior = gasto.valor_aplicado

                        if valor_nuevo == valor_anterior:
                            continue

                        CorreccionGastoDimanno.objects.create(
                            gasto=gasto,
                            valor_anterior=valor_anterior,
                            valor_nuevo=valor_nuevo,
                            motivo=motivo,
                            usuario=usuario,
                            usuario_nombre=usuario_nombre,
                        )
                        gasto.valor_aplicado = valor_nuevo
                        gasto.save(
                            update_fields=["valor_aplicado"]
                        )

                messages.success(
                    request,
                    "Los gastos se actualizaron correctamente.",
                )
                return redirect(
                    "procesamientos:dimanno_detalle",
                    procesamiento_id=procesamiento.id,
                )

        return render(
            request,
            "procesamientos/dimanno_gastos_editar.html",
            {
                "procesamiento": procesamiento,
                "formset": formset,
                "formulario_motivo": formulario_motivo,
                "usuario_autenticado": autenticado,
            },
            status=400,
        )

    formset = FormsetGastosDimanno(queryset=queryset)
    formulario_motivo = FormularioMotivoCorreccion(
        usuario_autenticado=autenticado,
    )

    return render(
        request,
        "procesamientos/dimanno_gastos_editar.html",
        {
            "procesamiento": procesamiento,
            "formset": formset,
            "formulario_motivo": formulario_motivo,
            "usuario_autenticado": autenticado,
        },
    )
