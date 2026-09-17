from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponseForbidden
from django.shortcuts import redirect


ROL_ADMIN = "admin"
ROL_BASICO = "basico"
ROLES = (
    (ROL_ADMIN, "Administrador"),
    (ROL_BASICO, "Usuario básico"),
)


def obtener_rol(usuario) -> str:
    if not getattr(usuario, "is_authenticated", False):
        return ROL_BASICO
    if getattr(usuario, "is_superuser", False):
        return ROL_ADMIN
    perfil = getattr(usuario, "perfil", None)
    if perfil is None:
        # Usuarios viejos sin perfil: staff/superuser = admin.
        if getattr(usuario, "is_staff", False):
            return ROL_ADMIN
        return ROL_BASICO
    return perfil.rol or ROL_BASICO


def es_admin(usuario) -> bool:
    return obtener_rol(usuario) == ROL_ADMIN


def requiere_admin(view_func):
    """Solo administradores. El resto recibe 403 o va al login."""

    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        usuario = request.user
        if not usuario.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not es_admin(usuario):
            return HttpResponseForbidden(
                "No tiene permiso para esta sección."
            )
        return view_func(request, *args, **kwargs)

    return _wrapped
