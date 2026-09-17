from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from procesamientos.forms import (
    FormularioCargaDimanno,
    FormularioCrearUsuario,
    FormularioEditarUsuario,
    FormularioMotivoCorreccion,
    FormularioResolucionDestinoDimanno,
    FormsetGastosDimanno,
)
from procesamientos.models import (
    RUBROS_GASTOS_DEFINICION,
    CorreccionGastoDimanno,
    GastoProcesamientoDimanno,
    GeneracionDimanno,
    PerfilUsuario,
    ProcesamientoDimanno,
    ProcesamientoEurobanan,
    ProcesamientoFruver,
    ProcesamientoGlamour,
    ProcesamientoKraaijeveld,
    ProcesamientoMaster,
    ProcesamientoNufri,
    ProcesamientoOrsero,
    ProcesamientoSifa,
    ProcesamientoTdvEuropa,
    ProcesamientoVisafruits,
    ResolucionDestinoDimanno,
)
from procesamientos.roles import obtener_rol, requiere_admin
from procesamientos.services.bitacoras import (
    filtrar_eventos_bitacora,
    recolectar_eventos_bitacora,
    resumen_ingresos_diarios,
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

# Modelos que alimentan el contador del panel.
_MODELOS_PROCESAMIENTO_PANEL = (
    ProcesamientoDimanno,
    ProcesamientoMaster,
    ProcesamientoOrsero,
    ProcesamientoKraaijeveld,
    ProcesamientoFruver,
    ProcesamientoSifa,
    ProcesamientoVisafruits,
    ProcesamientoGlamour,
    ProcesamientoNufri,
    ProcesamientoEurobanan,
    ProcesamientoTdvEuropa,
)


def total_procesamientos_del_dia() -> int:
    """Cuenta cargas creadas hoy (zona horaria local). Se reinicia a medianoche."""
    hoy = timezone.localdate()
    inicio = timezone.make_aware(
        datetime.combine(hoy, datetime.min.time())
    )
    fin = timezone.make_aware(
        datetime.combine(hoy, datetime.max.time())
    )
    return sum(
        modelo.objects.filter(
            creado_en__gte=inicio,
            creado_en__lte=fin,
        ).count()
        for modelo in _MODELOS_PROCESAMIENTO_PANEL
    )


_DIAS_SEMANA_ES = (
    "lun",
    "mar",
    "mié",
    "jue",
    "vie",
    "sáb",
    "dom",
)

_MODELOS_CON_CLIENTE = (
    ("Di Manno", ProcesamientoDimanno),
    ("Master Fruits", ProcesamientoMaster),
    ("ORSERO", ProcesamientoOrsero),
    ("KRAAIJEVELD", ProcesamientoKraaijeveld),
    ("FRU&VER", ProcesamientoFruver),
    ("SIFA", ProcesamientoSifa),
    ("VISAFRUITS", ProcesamientoVisafruits),
    ("Glamour", ProcesamientoGlamour),
    ("NUFRI", ProcesamientoNufri),
    ("EUROBANAN", ProcesamientoEurobanan),
    ("TDV Europa", ProcesamientoTdvEuropa),
)


def pulso_ultimos_7_dias() -> list[dict]:
    """Conteos diarios de procesamientos para la franja del panel."""
    hoy = timezone.localdate()
    dias: list[dict] = []
    max_total = 1
    for offset in range(6, -1, -1):
        dia = hoy - timedelta(days=offset)
        inicio = timezone.make_aware(
            datetime.combine(dia, datetime.min.time())
        )
        fin = timezone.make_aware(
            datetime.combine(dia, datetime.max.time())
        )
        total = sum(
            modelo.objects.filter(
                creado_en__gte=inicio,
                creado_en__lte=fin,
            ).count()
            for modelo in _MODELOS_PROCESAMIENTO_PANEL
        )
        max_total = max(max_total, total)
        dias.append(
            {
                "fecha": dia,
                "etiqueta": _DIAS_SEMANA_ES[dia.weekday()],
                "dia_num": dia.day,
                "total": total,
                "es_hoy": dia == hoy,
            }
        )
    for item in dias:
        item["altura_pct"] = (
            int(round(100 * item["total"] / max_total))
            if max_total
            else 0
        )
        if item["total"] > 0 and item["altura_pct"] < 12:
            item["altura_pct"] = 12
    return dias


def cliente_mas_activo_semana() -> str | None:
    """Cliente con más cargas en los últimos 7 días (local)."""
    hoy = timezone.localdate()
    inicio = timezone.make_aware(
        datetime.combine(hoy - timedelta(days=6), datetime.min.time())
    )
    fin = timezone.make_aware(
        datetime.combine(hoy, datetime.max.time())
    )
    mejor_nombre = None
    mejor_total = 0
    for nombre, modelo in _MODELOS_CON_CLIENTE:
        total = modelo.objects.filter(
            creado_en__gte=inicio,
            creado_en__lte=fin,
        ).count()
        if total > mejor_total:
            mejor_total = total
            mejor_nombre = nombre
    return mejor_nombre if mejor_total else None


def saludo_por_hora(ahora=None) -> str:
    momento = timezone.localtime(ahora) if ahora else timezone.localtime()
    hora = momento.hour
    if hora < 12:
        return "Buenos días"
    if hora < 19:
        return "Buenas tardes"
    return "Buenas noches"


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
    rol = obtener_rol(request.user)
    contexto = {
        "nombre_usuario_sesion": obtener_nombre_usuario(
            request.user
        ),
        "iniciales_usuario": obtener_iniciales_usuario(
            request.user
        ),
        "es_admin_sesion": rol == "admin",
        "rol_sesion": rol,
        "rol_sesion_legible": (
            "Administrador" if rol == "admin" else "Usuario básico"
        ),
    }
    if nav_activo is not None:
        contexto["nav_activo"] = nav_activo
    return contexto


def _item_reciente(
    *,
    cliente: str,
    item,
    factura_corta,
    url_name: str,
) -> dict:
    return {
        "cliente": cliente,
        "factura_corta": factura_corta,
        "semana": item.semana,
        "anio": item.anio,
        "estado_legible": item.estado_legible,
        "creado_en": item.creado_en,
        "url_name": url_name,
        "id": item.id,
    }


def recolectar_actividad_reciente(limite: int = 5) -> list[dict]:
    """Últimos procesamientos de todos los módulos activos."""
    fuentes = (
        (
            "Di Manno",
            ProcesamientoDimanno,
            lambda i: i.factura_corta,
            "procesamientos:dimanno_detalle",
        ),
        (
            "Master Fruits",
            ProcesamientoMaster,
            lambda i: i.factura_corta,
            "procesamientos:master_detalle",
        ),
        (
            "ORSERO",
            ProcesamientoOrsero,
            lambda i: i.nave_texto,
            "procesamientos:orsero_detalle",
        ),
        (
            "KRAAIJEVELD",
            ProcesamientoKraaijeveld,
            lambda i: i.destino_ui,
            "procesamientos:kraaijeveld_detalle",
        ),
        (
            "FRU&VER",
            ProcesamientoFruver,
            lambda i: i.factura_corta or i.destino_ui,
            "procesamientos:fruver_detalle",
        ),
        (
            "SIFA",
            ProcesamientoSifa,
            lambda i: i.factura_corta or i.destino_ui,
            "procesamientos:sifa_detalle",
        ),
        (
            "VISAFRUITS",
            ProcesamientoVisafruits,
            lambda i: i.factura_corta or i.destino_ui,
            "procesamientos:visafruits_detalle",
        ),
        (
            "Glamour",
            ProcesamientoGlamour,
            lambda i: i.factura_corta or i.destino_ui,
            "procesamientos:glamour_validacion",
        ),
        (
            "NUFRI",
            ProcesamientoNufri,
            lambda i: i.factura_corta or i.destino_ui,
            "procesamientos:nufri_validacion",
        ),
        (
            "EUROBANAN",
            ProcesamientoEurobanan,
            lambda i: i.factura_corta or i.destino_ui,
            "procesamientos:eurobanan_validacion",
        ),
        (
            "TDV Europa",
            ProcesamientoTdvEuropa,
            lambda i: i.factura_corta or i.destino_ui,
            "procesamientos:tdv_europa_validacion",
        ),
    )
    recientes: list[dict] = []
    for cliente, modelo, ref, url_name in fuentes:
        for item in modelo.objects.order_by("-creado_en")[:10]:
            recientes.append(
                _item_reciente(
                    cliente=cliente,
                    item=item,
                    factura_corta=ref(item),
                    url_name=url_name,
                )
            )
    recientes.sort(key=lambda x: x["creado_en"], reverse=True)
    return recientes[:limite]


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
        "descripcion": (
            "Validar liquidaciones PDF, cruzar Despachos "
            "y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:eurobanan_cargar",
    },
    {
        "codigo": "fruver",
        "nombre": "FRU&VER",
        "descripcion": (
            "Validar liquidaciones PDF por contenedor, cruzar "
            "Despachos y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:fruver_cargar",
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
        "descripcion": (
            "Validar liquidaciones PDF por página, cruzar "
            "Despachos y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:nufri_cargar",
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
        "descripcion": (
            "Validar liquidaciones PDF, cruzar Despachos "
            "y generar el acumulativo."
        ),
        "disponible": True,
        "url_name": "procesamientos:tdv_europa_cargar",
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
        "disponible": True,
        "url_name": "procesamientos:visafruits_cargar",
    },
)


@login_required
def panel_control(request):
    clientes_disponibles = sum(
        1 for cliente in CLIENTES_PANEL if cliente["disponible"]
    )
    pulso = pulso_ultimos_7_dias()
    return render(
        request,
        "procesamientos/panel.html",
        {
            **contexto_sesion(request, nav_activo="panel"),
            "saludo": saludo_por_hora(),
            "pulso_7_dias": pulso,
            "procesamientos_recientes": recolectar_actividad_reciente(3),
            "total_procesamientos": total_procesamientos_del_dia(),
            "clientes_disponibles": clientes_disponibles,
            "cliente_destacado": cliente_mas_activo_semana(),
            "total_semana": sum(d["total"] for d in pulso),
        },
    )


@login_required
@require_GET
def listar_clientes(request):
    clientes_disponibles = sum(
        1 for cliente in CLIENTES_PANEL if cliente["disponible"]
    )
    return render(
        request,
        "procesamientos/clientes.html",
        {
            **contexto_sesion(request, nav_activo="clientes"),
            "clientes_panel": CLIENTES_PANEL,
            "total_clientes": len(CLIENTES_PANEL),
            "clientes_disponibles": clientes_disponibles,
        },
    )


@login_required
@requiere_admin
def bitacoras(request):
    eventos = recolectar_eventos_bitacora()
    filtrados = filtrar_eventos_bitacora(
        eventos,
        q=request.GET.get("q", ""),
        cliente=request.GET.get("cliente", ""),
        factura=request.GET.get("factura", ""),
        usuario=request.GET.get("usuario", ""),
        fecha_desde=request.GET.get("fecha_desde", ""),
        fecha_hasta=request.GET.get("fecha_hasta", ""),
    )
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
                "fecha_desde": request.GET.get("fecha_desde", ""),
                "fecha_hasta": request.GET.get("fecha_hasta", ""),
            },
        },
    )


def _filas_usuarios() -> list[dict]:
    User = get_user_model()
    usuarios = (
        User.objects.select_related("perfil")
        .order_by("username")
    )
    filas = []
    for usuario in usuarios:
        perfil = getattr(usuario, "perfil", None)
        rol = (
            perfil.rol
            if perfil is not None
            else obtener_rol(usuario)
        )
        filas.append(
            {
                "id": usuario.id,
                "username": usuario.username,
                "nombre": usuario.get_full_name() or "—",
                "rol": rol,
                "rol_legible": (
                    "Administrador"
                    if rol == "admin"
                    else "Usuario básico"
                ),
                "activo": usuario.is_active,
                "ultimo_acceso": usuario.last_login,
            }
        )
    return filas


@login_required
@requiere_admin
@require_GET
def listar_usuarios(request):
    return render(
        request,
        "procesamientos/usuarios.html",
        {
            **contexto_sesion(request, nav_activo="usuarios"),
            "usuarios": _filas_usuarios(),
        },
    )


@login_required
@require_GET
def mi_perfil(request):
    usuario = request.user
    rol = obtener_rol(usuario)
    nombre = usuario.get_full_name().strip()
    return render(
        request,
        "procesamientos/mi_perfil.html",
        {
            **contexto_sesion(request, nav_activo="perfil"),
            "user_id": usuario.pk,
            "username": usuario.username,
            "nombre_completo": nombre or usuario.username,
            "nombre_mostrar": nombre or "—",
            "rol": rol,
            "rol_legible": (
                "Administrador" if rol == "admin" else "Usuario básico"
            ),
            "activo": usuario.is_active,
            "ultimo_acceso": usuario.last_login,
        },
    )


@login_required
@requiere_admin
def crear_usuario(request):
    if request.method == "POST":
        formulario = FormularioCrearUsuario(request.POST)
        if formulario.is_valid():
            User = get_user_model()
            datos = formulario.cleaned_data
            with transaction.atomic():
                usuario = User.objects.create_user(
                    username=datos["username"],
                    password=datos["password1"],
                    first_name=datos.get("first_name") or "",
                )
                # Staff solo para admins (acceso /admin/ de Django).
                usuario.is_staff = datos["rol"] == "admin"
                usuario.save(update_fields=["is_staff"])
                PerfilUsuario.objects.update_or_create(
                    usuario=usuario,
                    defaults={"rol": datos["rol"]},
                )
            messages.success(
                request,
                f"Usuario “{usuario.username}” creado.",
            )
            return redirect("procesamientos:usuarios")
    else:
        formulario = FormularioCrearUsuario()

    return render(
        request,
        "procesamientos/usuario_crear.html",
        {
            **contexto_sesion(request, nav_activo="usuarios"),
            "formulario": formulario,
            "usuarios": _filas_usuarios(),
        },
    )


@login_required
@requiere_admin
def editar_usuario(request, user_id: int):
    User = get_user_model()
    usuario = get_object_or_404(User, pk=user_id)
    perfil = getattr(usuario, "perfil", None)
    rol_actual = (
        perfil.rol if perfil is not None else obtener_rol(usuario)
    )
    es_mismo = request.user.pk == usuario.pk
    secciones = ("datos", "acceso", "clave")
    seccion = (request.POST.get("seccion") or request.GET.get("seccion") or "datos").strip()
    if seccion not in secciones:
        seccion = "datos"

    if request.method == "POST":
        formulario = FormularioEditarUsuario(
            request.POST,
            usuario_id=usuario.pk,
        )
        if formulario.is_valid():
            datos = formulario.cleaned_data
            nuevo_rol = datos["rol"]
            activo = bool(datos.get("is_active"))

            if seccion == "acceso":
                if es_mismo and nuevo_rol != "admin":
                    messages.error(
                        request,
                        "No puede quitarse el rol de administrador a sí mismo.",
                    )
                    return redirect(
                        "procesamientos:usuario_editar",
                        user_id=usuario.pk,
                    )
                if es_mismo and not activo:
                    messages.error(
                        request,
                        "No puede desactivar su propia cuenta.",
                    )
                    return redirect(
                        "procesamientos:usuario_editar",
                        user_id=usuario.pk,
                    )

            with transaction.atomic():
                if seccion == "datos":
                    usuario.username = datos["username"]
                    usuario.first_name = datos.get("first_name") or ""
                    usuario.save(
                        update_fields=["username", "first_name"]
                    )
                    mensaje = (
                        f"Datos de “{usuario.username}” actualizados."
                    )
                elif seccion == "acceso":
                    usuario.is_active = activo
                    usuario.is_staff = nuevo_rol == "admin"
                    usuario.save(
                        update_fields=["is_active", "is_staff"]
                    )
                    PerfilUsuario.objects.update_or_create(
                        usuario=usuario,
                        defaults={"rol": nuevo_rol},
                    )
                    mensaje = (
                        f"Acceso de “{usuario.username}” actualizado."
                    )
                else:
                    if not datos.get("password1"):
                        formulario.add_error(
                            "password1",
                            "Indique la nueva contraseña.",
                        )
                        return render(
                            request,
                            "procesamientos/usuario_editar.html",
                            {
                                **contexto_sesion(
                                    request, nav_activo="usuarios"
                                ),
                                "formulario": formulario,
                                "usuario_editado": usuario,
                                "usuarios": _filas_usuarios(),
                                "seccion": seccion,
                            },
                        )
                    usuario.set_password(datos["password1"])
                    usuario.save(update_fields=["password"])
                    mensaje = (
                        f"Contraseña de “{usuario.username}” actualizada."
                    )

            messages.success(request, mensaje)
            return redirect("procesamientos:usuarios")
    else:
        formulario = FormularioEditarUsuario(
            initial={
                "username": usuario.username,
                "first_name": usuario.first_name,
                "rol": rol_actual,
                "is_active": usuario.is_active,
            },
            usuario_id=usuario.pk,
        )

    return render(
        request,
        "procesamientos/usuario_editar.html",
        {
            **contexto_sesion(request, nav_activo="usuarios"),
            "formulario": formulario,
            "usuario_editado": usuario,
            "usuarios": _filas_usuarios(),
            "seccion": seccion,
        },
    )


@login_required
@require_GET
def ingresos(request):
    resumen = resumen_ingresos_diarios(
        fecha_desde=request.GET.get("fecha_desde"),
        fecha_hasta=request.GET.get("fecha_hasta"),
        cliente_filtro=request.GET.get("cliente"),
    )
    return render(
        request,
        "procesamientos/ingresos.html",
        {
            **contexto_sesion(request, nav_activo="ingresos"),
            "resumen": resumen,
            "filtros": {
                "fecha_desde": resumen["fecha_desde"],
                "fecha_hasta": resumen["fecha_hasta"],
                "cliente": request.GET.get("cliente", ""),
            },
            "chart_payload": {
                "labels": resumen["etiquetas"],
                "datasets": resumen["datasets"],
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
    contexto_base = contexto_sesion(request, nav_activo="panel")
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
            **contexto_sesion(request, nav_activo="panel"),
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
                **contexto_sesion(request, nav_activo="panel"),
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
            **contexto_sesion(request, nav_activo="panel"),
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
                **contexto_sesion(request, nav_activo="panel"),
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
            **contexto_sesion(request, nav_activo="panel"),
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
            **contexto_sesion(request, nav_activo="panel"),
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

