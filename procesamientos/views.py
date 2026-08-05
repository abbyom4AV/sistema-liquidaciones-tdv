from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
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
    FormularioCargaDimanno,
    FormularioMotivoCorreccion,
    FormularioResolucionDestinoDimanno,
    FormsetGastosDimanno,
)
from procesamientos.models import (
    RUBROS_GASTOS_DEFINICION,
    CorreccionGastoDimanno,
    CorreccionGastoMaster,
    CorreccionGastoOrsero,
    GastoProcesamientoDimanno,
    GeneracionDimanno,
    GeneracionKraaijeveld,
    GeneracionMaster,
    GeneracionOrsero,
    GeneracionSifa,
    ProcesamientoDimanno,
    ProcesamientoKraaijeveld,
    ProcesamientoMaster,
    ProcesamientoOrsero,
    ProcesamientoSifa,
    ResolucionDestinoDimanno,
)
from procesamientos.services.generacion_dimanno import (
    NOMBRE_DESCARGA_DIMANNO,
    ErrorConfirmacionGeneracionDimanno,
    serializar_gastos_aplicados,
)
from services.dimanno.extractor import ErrorExtraccionDimanno
from services.dimanno.matcher import ErrorMatcherDimanno
from services.dimanno.processor import (
    ErrorProcesamientoDimanno,
    ResultadoPreparacionDimanno,
    preparar_procesamiento_dimanno,
)

logger = logging.getLogger(__name__)


def obtener_nombre_usuario(usuario) -> str:
    nombre_completo = usuario.get_full_name().strip()
    return nombre_completo or usuario.get_username()


def obtener_iniciales_usuario(usuario) -> str:
    nombre_completo = usuario.get_full_name().strip()
    if nombre_completo:
        partes = [p for p in nombre_completo.split() if p]
        if len(partes) >= 2:
            return (partes[0][0] + partes[-1][0]).upper()
        return partes[0][:2].upper()
    username = usuario.get_username().strip()
    if not username:
        return "?"
    return username[:2].upper()


def contexto_sesion(request, *, nav_activo: str | None = None) -> dict:
    contexto = {
        "nombre_usuario_sesion": obtener_nombre_usuario(
            request.user
        ),
        "iniciales_usuario": obtener_iniciales_usuario(
            request.user
        ),
    }
    if nav_activo is not None:
        contexto["nav_activo"] = nav_activo
    return contexto


CLIENTES_PANEL = (
    {
        "codigo": "dimanno",
        "nombre": "Di Manno",
        "descripcion": (
            "Validar liquidaciones, corregir gastos "
            "y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:dimanno_cargar",
    },
    {
        "codigo": "eurobanan",
        "nombre": "EUROBANAN",
        "descripcion": "Módulo de liquidaciones EUROBANAN.",
        "disponible": False,
        "url_name": None,
    },
    {
        "codigo": "fruver",
        "nombre": "FRU&VER",
        "descripcion": "Módulo de liquidaciones FRU&VER.",
        "disponible": False,
        "url_name": None,
    },
    {
        "codigo": "glamour",
        "nombre": "Glamour",
        "descripcion": (
            "Validar liquidaciones PDF, mapear gastos "
            "y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:glamour_cargar",
    },
    {
        "codigo": "kraaijeveld",
        "nombre": "Kraaijeveld",
        "descripcion": (
            "Validar liquidaciones PDF por contenedor, cruzar "
            "Despachos y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:kraaijeveld_cargar",
    },
    {
        "codigo": "master",
        "nombre": "Master Fruits",
        "descripcion": (
            "Validar liquidaciones PDF, cruzar Despachos "
            "y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:master_cargar",
    },
    {
        "codigo": "nufri",
        "nombre": "NUFRI",
        "descripcion": "Módulo de liquidaciones NUFRI.",
        "disponible": False,
        "url_name": None,
    },
    {
        "codigo": "orsero",
        "nombre": "ORSERO",
        "descripcion": (
            "Validar screenshots de liquidación, cruzar "
            "Despachos y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:orsero_cargar",
    },
    {
        "codigo": "sifa",
        "nombre": "SIFA",
        "descripcion": (
            "Validar liquidación Excel, cruzar Despachos "
            "y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:sifa_cargar",
    },
    {
        "codigo": "tdv_europa",
        "nombre": "TDV Europa",
        "descripcion": "Módulo de liquidaciones TDV Europa.",
        "disponible": False,
        "url_name": None,
    },
    {
        "codigo": "tdv_usa",
        "nombre": "TDV USA",
        "descripcion": "Módulo de liquidaciones TDV USA.",
        "disponible": False,
        "url_name": None,
    },
    {
        "codigo": "visafruits",
        "nombre": "VISAFRUITS",
        "descripcion": "Módulo de liquidaciones VISAFRUITS.",
        "disponible": False,
        "url_name": None,
    },
)


@login_required
def panel_control(request):
    recientes_dimanno = [
        {
            "cliente": "Di Manno",
            "factura_corta": item.factura_corta,
            "semana": item.semana,
            "anio": item.anio,
            "estado_legible": item.estado_legible,
            "creado_en": item.creado_en,
            "url_name": "procesamientos:dimanno_detalle",
            "id": item.id,
        }
        for item in ProcesamientoDimanno.objects.order_by(
            "-creado_en"
        )[:10]
    ]
    recientes_master = [
        {
            "cliente": "Master Fruits",
            "factura_corta": item.factura_corta,
            "semana": item.semana,
            "anio": item.anio,
            "estado_legible": item.estado_legible,
            "creado_en": item.creado_en,
            "url_name": "procesamientos:master_detalle",
            "id": item.id,
        }
        for item in ProcesamientoMaster.objects.order_by(
            "-creado_en"
        )[:10]
    ]
    recientes_orsero = [
        {
            "cliente": "ORSERO",
            "factura_corta": item.nave_texto,
            "semana": item.semana,
            "anio": item.anio,
            "estado_legible": item.estado_legible,
            "creado_en": item.creado_en,
            "url_name": "procesamientos:orsero_detalle",
            "id": item.id,
        }
        for item in ProcesamientoOrsero.objects.order_by(
            "-creado_en"
        )[:10]
    ]
    recientes_kraaijeveld = [
        {
            "cliente": "KRAAIJEVELD",
            "factura_corta": item.destino_ui,
            "semana": item.semana,
            "anio": item.anio,
            "estado_legible": item.estado_legible,
            "creado_en": item.creado_en,
            "url_name": "procesamientos:kraaijeveld_detalle",
            "id": item.id,
        }
        for item in ProcesamientoKraaijeveld.objects.order_by(
            "-creado_en"
        )[:10]
    ]
    recientes_sifa = [
        {
            "cliente": "SIFA",
            "factura_corta": (
                item.factura_corta or item.destino_ui
            ),
            "semana": item.semana,
            "anio": item.anio,
            "estado_legible": item.estado_legible,
            "creado_en": item.creado_en,
            "url_name": "procesamientos:sifa_detalle",
            "id": item.id,
        }
        for item in ProcesamientoSifa.objects.order_by(
            "-creado_en"
        )[:10]
    ]
    recientes = sorted(
        recientes_dimanno
        + recientes_master
        + recientes_orsero
        + recientes_kraaijeveld
        + recientes_sifa,
        key=lambda item: item["creado_en"],
        reverse=True,
    )[:5]
    clientes_disponibles = sum(
        1 for cliente in CLIENTES_PANEL if cliente["disponible"]
    )
    return render(
        request,
        "procesamientos/panel.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamientos_recientes": recientes,
            "total_procesamientos": (
                ProcesamientoDimanno.objects.count()
                + ProcesamientoMaster.objects.count()
                + ProcesamientoOrsero.objects.count()
                + ProcesamientoKraaijeveld.objects.count()
                + ProcesamientoSifa.objects.count()
            ),
            "clientes_panel": CLIENTES_PANEL,
            "total_clientes": len(CLIENTES_PANEL),
            "clientes_disponibles": clientes_disponibles,
        },
    )


@login_required
def bitacoras(request):
    eventos: list[dict] = []

    correcciones = CorreccionGastoDimanno.objects.select_related(
        "gasto",
        "gasto__procesamiento",
    ).order_by("-creado_en")[:120]
    for item in correcciones:
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

    resoluciones = ResolucionDestinoDimanno.objects.select_related(
        "procesamiento",
    ).order_by("-creado_en")[:120]
    for item in resoluciones:
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

    generaciones = GeneracionDimanno.objects.select_related(
        "procesamiento",
    ).order_by("-solicitado_en")[:120]
    estados_generacion = {
        "completado": ("Transacción completada", "completada"),
        "error": ("Error en generación", "error"),
        "procesando": ("En proceso", "proceso"),
        "pendiente": ("Pendiente", "proceso"),
    }
    for item in generaciones:
        estado_texto, estado_clase = estados_generacion.get(
            item.estado,
            (item.estado_legible, "proceso"),
        )
        eventos.append(
            {
                "tipo": "Generación de archivo",
                "estado": estado_texto,
                "estado_clase": estado_clase,
                "cliente": "Di Manno",
                "detalle": estado_texto,
                "usuario": item.solicitado_por_nombre or "—",
                "fecha": item.solicitado_en,
                "factura": item.procesamiento.factura_corta or "—",
                "url_detalle": (
                    "procesamientos:dimanno_generacion_detalle",
                    item.id,
                ),
            }
        )

    correcciones_master = CorreccionGastoMaster.objects.select_related(
        "gasto",
        "gasto__procesamiento",
    ).order_by("-creado_en")[:120]
    for item in correcciones_master:
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

    generaciones_master = GeneracionMaster.objects.select_related(
        "procesamiento",
    ).order_by("-solicitado_en")[:120]
    for item in generaciones_master:
        estado_texto, estado_clase = estados_generacion.get(
            item.estado,
            (item.estado_legible, "proceso"),
        )
        eventos.append(
            {
                "tipo": "Generación de archivo",
                "estado": estado_texto,
                "estado_clase": estado_clase,
                "cliente": "Master Fruits",
                "detalle": estado_texto,
                "usuario": item.solicitado_por_nombre or "—",
                "fecha": item.solicitado_en,
                "factura": item.procesamiento.factura_corta or "—",
                "url_detalle": (
                    "procesamientos:master_generacion_detalle",
                    item.id,
                ),
            }
        )

    correcciones_orsero = CorreccionGastoOrsero.objects.select_related(
        "gasto",
        "gasto__procesamiento",
    ).order_by("-creado_en")[:120]
    for item in correcciones_orsero:
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

    generaciones_orsero = GeneracionOrsero.objects.select_related(
        "procesamiento",
    ).order_by("-solicitado_en")[:120]
    for item in generaciones_orsero:
        estado_texto, estado_clase = estados_generacion.get(
            item.estado,
            (item.estado_legible, "proceso"),
        )
        eventos.append(
            {
                "tipo": "Generación de archivo",
                "estado": estado_texto,
                "estado_clase": estado_clase,
                "cliente": "ORSERO",
                "detalle": estado_texto,
                "usuario": item.solicitado_por_nombre or "—",
                "fecha": item.solicitado_en,
                "factura": item.procesamiento.nave_texto or "—",
                "url_detalle": (
                    "procesamientos:orsero_generacion_detalle",
                    item.id,
                ),
            }
        )

    generaciones_kraaijeveld = (
        GeneracionKraaijeveld.objects.select_related(
            "procesamiento",
        ).order_by("-solicitado_en")[:120]
    )
    for item in generaciones_kraaijeveld:
        estado_texto, estado_clase = estados_generacion.get(
            item.estado,
            (item.estado_legible, "proceso"),
        )
        eventos.append(
            {
                "tipo": "Generación de archivo",
                "estado": estado_texto,
                "estado_clase": estado_clase,
                "cliente": "KRAAIJEVELD",
                "detalle": estado_texto,
                "usuario": item.solicitado_por_nombre or "—",
                "fecha": item.solicitado_en,
                "factura": (
                    item.procesamiento.destino_ui or "—"
                ),
                "url_detalle": (
                    "procesamientos:kraaijeveld_generacion_detalle",
                    item.id,
                ),
            }
        )

    generaciones_sifa = (
        GeneracionSifa.objects.select_related(
            "procesamiento",
        ).order_by("-solicitado_en")[:120]
    )
    for item in generaciones_sifa:
        estado_texto, estado_clase = estados_generacion.get(
            item.estado,
            (item.estado_legible, "proceso"),
        )
        eventos.append(
            {
                "tipo": "Generación de archivo",
                "estado": estado_texto,
                "estado_clase": estado_clase,
                "cliente": "SIFA",
                "detalle": estado_texto,
                "usuario": item.solicitado_por_nombre or "—",
                "fecha": item.solicitado_en,
                "factura": (
                    item.procesamiento.factura_corta
                    or item.procesamiento.destino_ui
                    or "—"
                ),
                "url_detalle": (
                    "procesamientos:sifa_generacion_detalle",
                    item.id,
                ),
            }
        )

    q = (request.GET.get("q") or "").strip().lower()
    cliente = (request.GET.get("cliente") or "").strip().lower()
    factura = (request.GET.get("factura") or "").strip().lower()
    usuario = (request.GET.get("usuario") or "").strip().lower()
    fecha_desde = (request.GET.get("fecha_desde") or "").strip()
    fecha_hasta = (request.GET.get("fecha_hasta") or "").strip()

    filtrados: list[dict] = []
    for evento in eventos:
        fecha = evento["fecha"]
        if fecha_desde:
            try:
                desde = datetime.strptime(fecha_desde, "%Y-%m-%d").date()
                if fecha.date() < desde:
                    continue
            except ValueError:
                pass
        if fecha_hasta:
            try:
                hasta = datetime.strptime(fecha_hasta, "%Y-%m-%d").date()
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

    filtrados.sort(key=lambda evento: evento["fecha"], reverse=True)
    return render(
        request,
        "procesamientos/bitacoras.html",
        {
            **contexto_sesion(request, nav_activo="bitacoras"),
            "eventos": filtrados[:80],
            "filtros": {
                "q": request.GET.get("q", ""),
                "cliente": request.GET.get("cliente", ""),
                "factura": request.GET.get("factura", ""),
                "usuario": request.GET.get("usuario", ""),
                "fecha_desde": fecha_desde,
                "fecha_hasta": fecha_hasta,
            },
        },
    )


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
            "nave": linea.despacho.barco,
            "cliente": linea.despacho.cliente,
            "destino": linea.despacho.puerto_destino,
            "tipo_fruta": linea.tipo_fruta,
            "carton": linea.despacho.carton,
            "calibre": linea.calibre,
            "total_cajas": linea.despacho.total_cajas,
            "semana": linea.despacho.semana,
            "anio": linea.despacho.anio,
            "factura": linea.despacho.factura,
            "factura_corta": linea.despacho.factura_corta,
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
    procesamiento.origen_destino_final = (
        getattr(resultado, "origen_destino", None) or ""
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


@login_required
def cargar_dimanno(request):
    contexto_base = {
        "nombre_usuario_sesion": obtener_nombre_usuario(
            request.user
        ),
    }
    if request.method != "POST":
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                **contexto_base,
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
                **contexto_base,
                "formulario": formulario,
            },
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoDimanno | None = None
    nombre_visible = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoDimanno(
                id=uuid.uuid4(),
                anio=datos["anio"],
                nombre_hoja=datos["nombre_hoja"],
                factura_corta="",
                semana=0,
                estado="procesando",
                creado_por=request.user,
                creado_por_nombre=nombre_visible,
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
                **contexto_base,
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
                **contexto_base,
                "formulario": formulario,
                "error_proceso": (
                    "Ocurrió un error inesperado al validar "
                    "los archivos. Revise el registro del "
                    "servidor para más detalle."
                ),
            },
            status=500,
        )


@login_required
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
    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionDimanno.Estado.PENDIENTE,
                GeneracionDimanno.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionDimanno.Estado.COMPLETADO
        )
        .order_by("-finalizado_en", "-solicitado_en")
        .first()
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
            "nombre_usuario_sesion": obtener_nombre_usuario(
                request.user
            ),
            "generacion_activa": generacion_activa,
            "ultima_completada": ultima_completada,
            "puede_solicitar_generacion": (
                procesamiento.puede_escribir
                and not procesamiento.requiere_resolver_destino
                and not procesamiento.errores
                and generacion_activa is None
            ),
        },
    )


@login_required
def editar_gastos_dimanno(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoDimanno,
        pk=procesamiento_id,
    )
    queryset = GastoProcesamientoDimanno.objects.filter(
        procesamiento=procesamiento
    ).order_by("orden")
    nombre_visible = obtener_nombre_usuario(request.user)

    if request.method == "POST":
        formset = FormsetGastosDimanno(
            request.POST,
            queryset=queryset,
        )
        formulario_motivo = FormularioMotivoCorreccion(
            request.POST,
        )

        if formset.is_valid() and formulario_motivo.is_valid():
            motivo = formulario_motivo.cleaned_data["motivo"]
            usuario = request.user
            usuario_nombre = nombre_visible

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
                "nombre_usuario_sesion": nombre_visible,
            },
            status=400,
        )

    formset = FormsetGastosDimanno(queryset=queryset)
    formulario_motivo = FormularioMotivoCorreccion()

    return render(
        request,
        "procesamientos/dimanno_gastos_editar.html",
        {
            "procesamiento": procesamiento,
            "formset": formset,
            "formulario_motivo": formulario_motivo,
            "nombre_usuario_sesion": nombre_visible,
        },
    )


@login_required
def resolver_destino_dimanno(request, procesamiento_id):
    nombre_visible = obtener_nombre_usuario(request.user)

    if request.method == "POST":
        formulario_invalido = None
        procesamiento_vista = None

        with transaction.atomic():
            procesamiento = get_object_or_404(
                ProcesamientoDimanno.objects.select_for_update(),
                pk=procesamiento_id,
            )
            formulario = FormularioResolucionDestinoDimanno(
                request.POST,
                procesamiento=procesamiento,
            )

            if not formulario.is_valid():
                formulario_invalido = formulario
                procesamiento_vista = procesamiento
            else:
                destino_nuevo = formulario.cleaned_data[
                    "destino_nuevo"
                ]
                origen_seleccionado = formulario.cleaned_data[
                    "origen_seleccionado"
                ]
                destino_anterior = (
                    procesamiento.destino_final or ""
                )

                if (
                    destino_nuevo
                    == (procesamiento.destino_final or "")
                    and origen_seleccionado
                    == (
                        procesamiento.origen_destino_final
                        or ""
                    )
                ):
                    messages.info(
                        request,
                        (
                            "No se realizó ningún cambio "
                            "en el destino."
                        ),
                    )
                    return redirect(
                        "procesamientos:dimanno_detalle",
                        procesamiento_id=procesamiento.id,
                    )

                ResolucionDestinoDimanno.objects.create(
                    procesamiento=procesamiento,
                    destino_anterior=destino_anterior,
                    destino_nuevo=destino_nuevo,
                    origen_seleccionado=origen_seleccionado,
                    destino_liquidacion=(
                        procesamiento.destino_liquidacion
                        or ""
                    ),
                    destinos_despachos=list(
                        procesamiento.destinos_despachos
                        or []
                    ),
                    motivo=formulario.cleaned_data["motivo"],
                    usuario=request.user,
                    usuario_nombre=nombre_visible,
                )

                procesamiento.destino_final = destino_nuevo
                procesamiento.origen_destino_final = (
                    origen_seleccionado
                )
                procesamiento.requiere_resolver_destino = False

                if not procesamiento.errores:
                    procesamiento.estado = "listo"
                    procesamiento.puede_escribir = True
                else:
                    procesamiento.puede_escribir = False

                procesamiento.save(
                    update_fields=[
                        "destino_final",
                        "origen_destino_final",
                        "requiere_resolver_destino",
                        "estado",
                        "puede_escribir",
                        "actualizado_en",
                    ]
                )

                messages.success(
                    request,
                    "El destino se definió correctamente.",
                )
                return redirect(
                    "procesamientos:dimanno_detalle",
                    procesamiento_id=procesamiento.id,
                )

        return render(
            request,
            "procesamientos/dimanno_destino_resolver.html",
            {
                "procesamiento": procesamiento_vista,
                "formulario": formulario_invalido,
                "nombre_usuario_sesion": nombre_visible,
            },
            status=400,
        )

    procesamiento = get_object_or_404(
        ProcesamientoDimanno,
        pk=procesamiento_id,
    )
    formulario = FormularioResolucionDestinoDimanno(
        procesamiento=procesamiento,
    )
    return render(
        request,
        "procesamientos/dimanno_destino_resolver.html",
        {
            "procesamiento": procesamiento,
            "formulario": formulario,
            "nombre_usuario_sesion": nombre_visible,
        },
    )


def _validar_procesamiento_para_generacion(
    procesamiento: ProcesamientoDimanno,
) -> str | None:
    if not procesamiento.puede_escribir:
        return (
            "El procesamiento no está listo para generar "
            "el archivo."
        )
    if procesamiento.errores:
        return (
            "El procesamiento tiene errores y no puede "
            "generar el archivo."
        )
    if procesamiento.requiere_resolver_destino:
        return (
            "Debe definir el destino antes de generar "
            "el archivo."
        )
    if not (procesamiento.destino_final or "").strip():
        return "No hay un destino final confirmado."

    for campo in (
        procesamiento.archivo_despachos,
        procesamiento.archivo_liquidacion,
        procesamiento.archivo_cliente,
    ):
        try:
            if not Path(campo.path).is_file():
                return (
                    "Los archivos de entrada ya no están "
                    "disponibles."
                )
        except (ValueError, FileNotFoundError):
            return (
                "Los archivos de entrada ya no están "
                "disponibles."
            )

    gastos = list(procesamiento.gastos.order_by("orden"))
    if len(gastos) != 6:
        return (
            "Deben existir exactamente los seis gastos "
            "esperados."
        )
    try:
        serializar_gastos_aplicados(
            procesamiento.obtener_gastos_aplicados()
        )
    except ErrorConfirmacionGeneracionDimanno as error:
        return str(error)
    return None


@login_required
@require_POST
def solicitar_generacion_dimanno(request, procesamiento_id):
    with transaction.atomic():
        procesamiento = get_object_or_404(
            ProcesamientoDimanno.objects.select_for_update(),
            pk=procesamiento_id,
        )

        activa = (
            GeneracionDimanno.objects.select_for_update()
            .filter(
                procesamiento=procesamiento,
                estado__in=[
                    GeneracionDimanno.Estado.PENDIENTE,
                    GeneracionDimanno.Estado.PROCESANDO,
                ],
            )
            .order_by("-solicitado_en")
            .first()
        )
        if activa is not None:
            messages.info(
                request,
                (
                    "Ya existe una generación en curso "
                    "para este procesamiento."
                ),
            )
            return redirect(
                "procesamientos:dimanno_generacion_detalle",
                generacion_id=activa.id,
            )

        error = _validar_procesamiento_para_generacion(
            procesamiento
        )
        if error:
            messages.error(request, error)
            return redirect(
                "procesamientos:dimanno_detalle",
                procesamiento_id=procesamiento.id,
            )

        try:
            gastos_snapshot = serializar_gastos_aplicados(
                procesamiento.obtener_gastos_aplicados()
            )
        except ErrorConfirmacionGeneracionDimanno as error:
            messages.error(request, str(error))
            return redirect(
                "procesamientos:dimanno_detalle",
                procesamiento_id=procesamiento.id,
            )

        try:
            generacion = GeneracionDimanno.objects.create(
                procesamiento=procesamiento,
                estado=GeneracionDimanno.Estado.PENDIENTE,
                solicitado_por=request.user,
                solicitado_por_nombre=obtener_nombre_usuario(
                    request.user
                ),
                destino_aplicado=procesamiento.destino_final,
                origen_destino_aplicado=(
                    procesamiento.origen_destino_final or ""
                ),
                gastos_aplicados=gastos_snapshot,
            )
        except IntegrityError:
            activa = (
                GeneracionDimanno.objects.filter(
                    procesamiento=procesamiento,
                    estado__in=[
                        GeneracionDimanno.Estado.PENDIENTE,
                        GeneracionDimanno.Estado.PROCESANDO,
                    ],
                )
                .order_by("-solicitado_en")
                .first()
            )
            messages.info(
                request,
                (
                    "Ya existe una generación en curso "
                    "para este procesamiento."
                ),
            )
            if activa is None:
                return redirect(
                    "procesamientos:dimanno_detalle",
                    procesamiento_id=procesamiento.id,
                )
            return redirect(
                "procesamientos:dimanno_generacion_detalle",
                generacion_id=activa.id,
            )

    messages.success(
        request,
        "La generación del archivo fue solicitada.",
    )
    return redirect(
        "procesamientos:dimanno_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
def detalle_generacion_dimanno(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionDimanno.objects.select_related(
            "procesamiento"
        ),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/dimanno_generacion_detalle.html",
        {
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "nombre_usuario_sesion": obtener_nombre_usuario(
                request.user
            ),
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def descargar_generacion_dimanno(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionDimanno,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404(
            "La generación no está disponible para descarga."
        )
    if not generacion.archivo_resultado:
        raise Http404("El archivo de resultado no existe.")

    try:
        ruta = Path(generacion.archivo_resultado.path)
    except (ValueError, FileNotFoundError) as error:
        raise Http404(
            "El archivo de resultado no existe."
        ) from error

    if not ruta.is_file():
        raise Http404("El archivo de resultado no existe.")

    nombre = generacion.nombre_descarga
    if nombre != NOMBRE_DESCARGA_DIMANNO:
        nombre = NOMBRE_DESCARGA_DIMANNO

    archivo = ruta.open("rb")
    try:
        return FileResponse(
            archivo,
            as_attachment=True,
            filename=nombre,
            content_type=(
                "application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet"
            ),
        )
    except Exception:
        archivo.close()
        raise

