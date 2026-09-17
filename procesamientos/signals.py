from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from procesamientos.models import (
    PerfilUsuario,
    ProcesamientoDimanno,
    ProcesamientoMaster,
)

User = get_user_model()


@receiver(post_delete, sender=ProcesamientoDimanno)
def eliminar_carpeta_media_procesamiento(
    sender,
    instance: ProcesamientoDimanno,
    **kwargs,
) -> None:
    """
    Elimina únicamente media/procesamientos/dimanno/<uuid>/.
    """
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base_permitida = (
        media_root / "procesamientos" / "dimanno"
    ).resolve()
    carpeta = (
        base_permitida / str(instance.id)
    ).resolve()

    try:
        carpeta.relative_to(base_permitida)
    except ValueError:
        return

    if carpeta.exists() and carpeta.is_dir():
        shutil.rmtree(carpeta, ignore_errors=True)


@receiver(post_delete, sender=ProcesamientoMaster)
def eliminar_carpeta_media_procesamiento_master(
    sender,
    instance: ProcesamientoMaster,
    **kwargs,
) -> None:
    """Elimina media/procesamientos/master/<uuid>/."""
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base_permitida = (
        media_root / "procesamientos" / "master"
    ).resolve()
    carpeta = (
        base_permitida / str(instance.id)
    ).resolve()

    try:
        carpeta.relative_to(base_permitida)
    except ValueError:
        return

    if carpeta.exists() and carpeta.is_dir():
        shutil.rmtree(carpeta, ignore_errors=True)


@receiver(post_save, sender=User)
def asegurar_perfil_usuario(
    sender,
    instance,
    created: bool,
    **kwargs,
) -> None:
    """Crea el perfil si el usuario aún no tiene uno."""
    if created:
        rol = (
            PerfilUsuario.Rol.ADMIN
            if instance.is_superuser or instance.is_staff
            else PerfilUsuario.Rol.BASICO
        )
        PerfilUsuario.objects.create(usuario=instance, rol=rol)
        return
    PerfilUsuario.objects.get_or_create(
        usuario=instance,
        defaults={
            "rol": (
                PerfilUsuario.Rol.ADMIN
                if instance.is_superuser or instance.is_staff
                else PerfilUsuario.Rol.BASICO
            ),
        },
    )
