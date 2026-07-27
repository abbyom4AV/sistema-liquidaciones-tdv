from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

from procesamientos.models import ProcesamientoDimanno


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
