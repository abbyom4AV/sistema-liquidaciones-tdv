from __future__ import annotations

import json
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

from procesamientos.forms import FormularioCargaTdvEuropa
from procesamientos.models import (
    GeneracionTdvEuropa,
    ProcesamientoTdvEuropa,
)
from procesamientos.services.generacion_tdv_europa import (
    serializar_atribuciones_merma,
    serializar_lineas_preparadas_tdv_europa,
    serializar_resumen_gastos_tdv_europa,
)
from procesamientos.views import (
    contexto_sesion,
    obtener_nombre_usuario,
)
from services.tdv_europa.extractor import ErrorExtraccionTdvEuropa
from services.tdv_europa.matcher import ErrorMatcherTdvEuropa
from services.tdv_europa.processor import (
    ErrorProcesamientoTdvEuropa,
    ResultadoPreparacionTdvEuropa,
    preparar_procesamiento_tdv_europa,
)
from services.tdv_europa.writer import NOMBRE_DESCARGA

logger = logging.getLogger(__name__)


def _decimal_a_str(valor) -> str:
    if isinstance(valor, Decimal):
        texto = format(valor, "f")
    else:
        texto = str(valor)
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


def _decimal_2(valor) -> str:
    """Siempre 2 decimales (no elimina .00)."""
    try:
        return format(Decimal(str(valor)), ".2f")
    except Exception:
        return "0.00"


def _decimal_completo(valor) -> str:
    """Conserva todos los decimales del precio."""
    try:
        return format(Decimal(str(valor)), "f")
    except Exception:
        return str(valor or "0")


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


def _aplicar_resultado_tdv_europa(
    procesamiento: ProcesamientoTdvEuropa,
    resultado: ResultadoPreparacionTdvEuropa,
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
        validacion.total_cajas_brutas_liquidacion
    )
    procesamiento.total_cajas_despachos = Decimal(
        validacion.total_cajas_despachos
    )
    procesamiento.total_venta_eur = validacion.total_venta_eur
    procesamiento.total_gastos_eur = validacion.total_gastos_eur
    procesamiento.total_merma = validacion.total_merma
    procesamiento.reclamos_irmadona = validacion.reclamos_irmadona
    procesamiento.reclamos_mercado = validacion.reclamos_mercado
    procesamiento.comision_eur = validacion.comision_eur
    procesamiento.puede_escribir = resultado.puede_escribir
    procesamiento.errores = _serializar_incidencias(
        validacion.errores
    )
    procesamiento.advertencias = _serializar_incidencias(
        validacion.advertencias
    )
    procesamiento.lineas_preparadas = (
        serializar_lineas_preparadas_tdv_europa(
            validacion.lineas_preparadas
        )
    )
    procesamiento.atribuciones_merma = serializar_atribuciones_merma(
        validacion.atribuciones_merma
    )
    procesamiento.resumen_gastos = serializar_resumen_gastos_tdv_europa(
        validacion.resumen_gastos
    )


def _eliminar_procesamiento_tdv_europa(
    procesamiento: ProcesamientoTdvEuropa | None,
) -> None:
    if procesamiento is None:
        return
    pid = procesamiento.id
    try:
        ProcesamientoTdvEuropa.objects.filter(pk=pid).delete()
    except Exception:
        logger.exception(
            "No se pudo eliminar procesamiento TDV Europa %s",
            pid,
        )
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base = (media_root / "procesamientos" / "tdv_europa").resolve()
    carpeta = (base / str(pid)).resolve()
    try:
        carpeta.relative_to(base)
    except ValueError:
        return
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)


def _contenedores_especiales_json(
    formulario: FormularioCargaTdvEuropa,
) -> str:
    contenedores: list[str] = []
    if formulario.is_bound and formulario.is_valid():
        raw = formulario.cleaned_data.get("contenedor_especial") or ""
        if raw:
            contenedores = [
                c.strip()
                for c in raw.split(",")
                if c.strip()
            ]
    if not contenedores and formulario.is_bound:
        contenedores = [
            (c or "").strip().upper()
            for c in formulario.data.getlist("contenedor_especial")
            if (c or "").strip()
        ]
    return json.dumps(contenedores)


def _contexto_carga_tdv_europa(
    formulario: FormularioCargaTdvEuropa,
    **extra,
):
    return {
        "formulario": formulario,
        "contenedores_especiales_json": (
            _contenedores_especiales_json(formulario)
        ),
        **extra,
    }


def _repreparar_procesamiento(
    procesamiento: ProcesamientoTdvEuropa,
) -> ResultadoPreparacionTdvEuropa:
    especiales = ()
    if procesamiento.incluye_contenedor_especial:
        especiales = (
            procesamiento.contenedor_especial or ""
        ).strip()
    resultado = preparar_procesamiento_tdv_europa(
        ruta_liquidacion=procesamiento.archivo_liquidacion.path,
        ruta_despachos=procesamiento.archivo_despachos.path,
        semana=procesamiento.semana,
        anio=procesamiento.anio,
        destino=(
            procesamiento.destino_ui
            or procesamiento.destino_final
        ),
        factura_corta=procesamiento.factura_corta,
        contenedores_especiales=especiales,
    )
    _aplicar_resultado_tdv_europa(procesamiento, resultado)
    procesamiento.save()
    return resultado


@login_required
def tdv_europa_cargar(request):
    ctx = contexto_sesion(request, nav_activo="panel")
    if request.method != "POST":
        return render(
            request,
            "procesamientos/tdv_europa_cargar.html",
            {
                **ctx,
                **_contexto_carga_tdv_europa(
                    FormularioCargaTdvEuropa()
                ),
            },
        )

    formulario = FormularioCargaTdvEuropa(
        request.POST,
        request.FILES,
    )
    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/tdv_europa_cargar.html",
            {
                **ctx,
                **_contexto_carga_tdv_europa(formulario),
            },
            status=400,
        )

    datos = formulario.cleaned_data
    procesamiento: ProcesamientoTdvEuropa | None = None
    nombre = obtener_nombre_usuario(request.user)

    try:
        with transaction.atomic():
            procesamiento = ProcesamientoTdvEuropa(
                id=uuid.uuid4(),
                factura_corta=datos["factura_corta"],
                semana=datos["semana"],
                anio=datos["anio"],
                destino_ui=datos["destino"],
                incluye_contenedor_especial=bool(
                    datos.get("incluye_contenedor_especial")
                ),
                contenedor_especial=(
                    datos.get("contenedor_especial") or ""
                    if datos.get("incluye_contenedor_especial")
                    else ""
                ),
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

            _repreparar_procesamiento(procesamiento)

        return redirect(
            "procesamientos:tdv_europa_validacion",
            procesamiento_id=procesamiento.id,
        )
    except (
        ErrorExtraccionTdvEuropa,
        ErrorMatcherTdvEuropa,
        ErrorProcesamientoTdvEuropa,
    ) as error:
        logger.exception("Error conocido TDV Europa")
        _eliminar_procesamiento_tdv_europa(procesamiento)
        return render(
            request,
            "procesamientos/tdv_europa_cargar.html",
            {
                **ctx,
                **_contexto_carga_tdv_europa(
                    formulario,
                    error_proceso=str(error),
                ),
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado TDV Europa")
        _eliminar_procesamiento_tdv_europa(procesamiento)
        return render(
            request,
            "procesamientos/tdv_europa_cargar.html",
            {
                **ctx,
                **_contexto_carga_tdv_europa(
                    formulario,
                    error_proceso=(
                        "Ocurrió un error inesperado al validar "
                        "los archivos."
                    ),
                ),
            },
            status=500,
        )


@login_required
def tdv_europa_validacion(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoTdvEuropa,
        pk=procesamiento_id,
    )

    generacion_activa = (
        procesamiento.generaciones.filter(
            estado__in=[
                GeneracionTdvEuropa.Estado.PENDIENTE,
                GeneracionTdvEuropa.Estado.PROCESANDO,
            ]
        )
        .order_by("-solicitado_en")
        .first()
    )
    ultima_completada = (
        procesamiento.generaciones.filter(
            estado=GeneracionTdvEuropa.Estado.COMPLETADO
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
    comision = procesamiento.comision_eur or Decimal("0")
    if comision != 0:
        gastos_items.append(
            {
                "nombre": "Comision Euros",
                "valor": _decimal_a_str(comision),
            }
        )

    atribuciones = []
    for fila in procesamiento.atribuciones_merma or []:
        if not isinstance(fila, dict):
            continue
        fila_fmt = dict(fila)
        for campo in ("cajas_merma", "cajas_netas", "cajas_brutas"):
            fila_fmt[campo] = _decimal_2(fila.get(campo) or "0")
        atribuciones.append(fila_fmt)

    total_merma_tabla = Decimal("0")
    lineas_unicas: dict[
        tuple[str, str, str, str],
        tuple[Decimal, Decimal],
    ] = {}
    for fila in procesamiento.atribuciones_merma or []:
        if not isinstance(fila, dict):
            continue
        try:
            merma_fila = Decimal(str(fila.get("cajas_merma") or "0"))
            netas_fila = Decimal(str(fila.get("cajas_netas") or "0"))
            brutas_fila = Decimal(str(fila.get("cajas_brutas") or "0"))
        except Exception:
            continue
        total_merma_tabla += merma_fila
        clave = (
            str(fila.get("contenedor") or ""),
            str(fila.get("calibre_raw") or ""),
            str(fila.get("carton") or ""),
            str(fila.get("cliente") or ""),
        )
        lineas_unicas[clave] = (netas_fila, brutas_fila)
    total_netas_merma = sum(
        (n for n, _b in lineas_unicas.values()),
        Decimal("0"),
    )
    total_brutas_merma = sum(
        (b for _n, b in lineas_unicas.values()),
        Decimal("0"),
    )

    lineas_fmt = []
    for linea in procesamiento.lineas_preparadas or []:
        if not isinstance(linea, dict):
            continue
        item = dict(linea)
        for campo in ("total_cajas", "merma"):
            item[campo] = _decimal_2(linea.get(campo) or "0")
        item["precio_venta_eur"] = _decimal_completo(
            linea.get("precio_venta_eur") or "0"
        )
        lineas_fmt.append(item)

    gastos_fmt = []
    for item in gastos_items:
        gastos_fmt.append(
            {
                "nombre": item["nombre"],
                "valor": _decimal_2(item["valor"]),
            }
        )

    errores = list(procesamiento.errores or [])
    merma_desempates = []
    otras_advertencias = []
    for adv in procesamiento.advertencias or []:
        if not isinstance(adv, dict):
            continue
        codigo = str(adv.get("codigo") or "")
        if codigo.startswith("MERMA_DESEMPATE"):
            detalles = adv.get("detalles") or {}
            if not isinstance(detalles, dict):
                detalles = {}
            candidatos = detalles.get("candidatos") or []
            if isinstance(candidatos, (list, tuple)):
                candidatos_txt = ", ".join(str(c) for c in candidatos)
            else:
                candidatos_txt = str(candidatos)
            merma_desempates.append(
                {
                    "contenedor": detalles.get("contenedor", ""),
                    "calibre_raw": detalles.get("calibre_raw", ""),
                    "carton": detalles.get("carton", ""),
                    "cajas": _decimal_2(detalles.get("cajas") or "0"),
                    "cliente_elegido": detalles.get(
                        "cliente_elegido",
                        "",
                    ),
                    "candidatos": candidatos_txt,
                }
            )
        else:
            otras_advertencias.append(adv)

    return render(
        request,
        "procesamientos/tdv_europa_validacion.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "procesamiento": procesamiento,
            "generacion_activa": generacion_activa,
            "ultima_completada": ultima_completada,
            "puede_solicitar_generacion": puede_solicitar,
            "lineas": lineas_fmt,
            "atribuciones_merma": atribuciones,
            "gastos_items": gastos_fmt,
            "total_merma_tabla": _decimal_2(total_merma_tabla),
            "total_netas_merma": _decimal_2(total_netas_merma),
            "total_brutas_merma": _decimal_2(total_brutas_merma),
            "comision_eur": _decimal_2(comision),
            "errores": errores,
            "merma_desempates": merma_desempates,
            "otras_advertencias": otras_advertencias,
            "cantidad_errores": len(errores),
            "cantidad_advertencias": (
                len(merma_desempates) + len(otras_advertencias)
            ),
        },
    )


@login_required
@require_POST
def tdv_europa_generar(request, procesamiento_id):
    procesamiento = get_object_or_404(
        ProcesamientoTdvEuropa,
        pk=procesamiento_id,
    )
    if (
        not procesamiento.puede_escribir
        or procesamiento.estado != "listo"
    ):
        return redirect(
            "procesamientos:tdv_europa_validacion",
            procesamiento_id=procesamiento.id,
        )

    if procesamiento.generaciones.filter(
        estado__in=[
            GeneracionTdvEuropa.Estado.PENDIENTE,
            GeneracionTdvEuropa.Estado.PROCESANDO,
        ]
    ).exists():
        return redirect(
            "procesamientos:tdv_europa_validacion",
            procesamiento_id=procesamiento.id,
        )

    try:
        generacion = GeneracionTdvEuropa.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionTdvEuropa.Estado.PENDIENTE,
            solicitado_por=request.user,
            solicitado_por_nombre=obtener_nombre_usuario(
                request.user
            ),
            destino_aplicado=procesamiento.destino_final,
            origen_destino_aplicado=(
                procesamiento.origen_destino or "ui"
            ),
        )
    except IntegrityError:
        return redirect(
            "procesamientos:tdv_europa_validacion",
            procesamiento_id=procesamiento.id,
        )

    return redirect(
        "procesamientos:tdv_europa_generacion_detalle",
        generacion_id=generacion.id,
    )


@login_required
@require_GET
def tdv_europa_generacion_detalle(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionTdvEuropa.objects.select_related("procesamiento"),
        pk=generacion_id,
    )
    return render(
        request,
        "procesamientos/tdv_europa_generacion_detalle.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "generacion": generacion,
            "procesamiento": generacion.procesamiento,
            "recargar_automaticamente": generacion.esta_activa,
        },
    )


@login_required
@require_GET
def tdv_europa_generacion_descargar(request, generacion_id):
    generacion = get_object_or_404(
        GeneracionTdvEuropa,
        pk=generacion_id,
    )
    if not generacion.esta_completada:
        raise Http404("La generación no está lista.")
    if not generacion.archivo_resultado:
        raise Http404("No hay archivo de resultado.")

    ruta = Path(generacion.archivo_resultado.path)
    if not ruta.is_file():
        raise Http404("El archivo ya no existe.")

    nombre = generacion.nombre_descarga or NOMBRE_DESCARGA
    if nombre != NOMBRE_DESCARGA:
        nombre = NOMBRE_DESCARGA

    try:
        handle = ruta.open("rb")
    except OSError as error:
        raise Http404("No se pudo abrir el archivo.") from error

    return FileResponse(
        handle,
        as_attachment=True,
        filename=nombre,
    )
