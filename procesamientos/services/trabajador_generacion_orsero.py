from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from procesamientos.models import GeneracionOrsero
from procesamientos.services.generacion_orsero import (
    ErrorConfirmacionGeneracionOrsero,
    construir_nombre_descarga,
    reconstruir_resultado_para_escritura_orsero,
)
from services.dimanno.writer import ErrorEscrituraDimanno
from services.orsero.extractor import ErrorExtraccionOrsero
from services.orsero.matcher import ErrorMatcherOrsero
from services.orsero.processor import ErrorProcesamientoOrsero
from services.orsero.writer import (
    ErrorEscrituraOrsero,
    establecer_contexto_generacion,
    escribir_archivo_orsero,
    limpiar_contexto_generacion,
)

logger = logging.getLogger(__name__)


class WorkerLockOrseroError(RuntimeError):
    """No se pudo adquirir el bloqueo del trabajador Orsero."""


class WorkerLockOrsero:
    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta or (
            Path(settings.BASE_DIR)
            / "runtime"
            / "orsero_worker.lock"
        )
        self._fd: int | None = None

    def __enter__(self) -> "WorkerLockOrsero":
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                str(self.ruta),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise WorkerLockOrseroError(
                "Ya hay un trabajador Orsero en ejecución. Si "
                "terminó abruptamente, elimine "
                f"{self.ruta}"
            ) from error
        os.write(
            self._fd,
            (
                f"pid={os.getpid()}\n"
                f"inicio="
                f"{datetime.now(dt_timezone.utc).isoformat()}\n"
            ).encode("utf-8"),
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            if self.ruta.exists():
                self.ruta.unlink()
        except OSError:
            logger.exception(
                "No se pudo eliminar el lock %s",
                self.ruta,
            )


def reclamar_siguiente_generacion_orsero() -> (
    GeneracionOrsero | None
):
    with transaction.atomic():
        generacion = (
            GeneracionOrsero.objects.select_for_update()
            .filter(estado=GeneracionOrsero.Estado.PENDIENTE)
            .order_by("solicitado_en")
            .first()
        )
        if generacion is None:
            return None
        generacion.estado = GeneracionOrsero.Estado.PROCESANDO
        generacion.iniciado_en = timezone.now()
        generacion.intentos = (generacion.intentos or 0) + 1
        generacion.mensaje_error = ""
        generacion.save(
            update_fields=[
                "estado",
                "iniciado_en",
                "intentos",
                "mensaje_error",
            ]
        )
        return generacion


def _marcar_error(generacion: GeneracionOrsero, mensaje: str) -> None:
    generacion.estado = GeneracionOrsero.Estado.ERROR
    generacion.mensaje_error = mensaje
    generacion.finalizado_en = timezone.now()
    generacion.save(
        update_fields=[
            "estado",
            "mensaje_error",
            "finalizado_en",
        ]
    )


def procesar_generacion_orsero(generacion: GeneracionOrsero) -> None:
    generacion.refresh_from_db()
    procesamiento = generacion.procesamiento
    ruta_salida = (
        Path(settings.MEDIA_ROOT)
        / "procesamientos"
        / "orsero"
        / str(procesamiento.id)
        / "resultados"
        / str(generacion.id)
        / "resultado.xlsx"
    )
    token = establecer_contexto_generacion(str(generacion.id))

    try:
        ruta_cliente = Path(procesamiento.archivo_cliente.path)
        if not ruta_cliente.is_file():
            _marcar_error(
                generacion,
                "El acumulativo del cliente ya no está disponible.",
            )
            return

        if not generacion.gastos_aplicados:
            _marcar_error(
                generacion,
                "No hay gastos confirmados.",
            )
            return

        if not procesamiento.lineas_preparadas:
            _marcar_error(
                generacion,
                "No hay líneas preparadas guardadas.",
            )
            return

        inicio = time.perf_counter()
        try:
            resultado = reconstruir_resultado_para_escritura_orsero(
                anio=procesamiento.anio,
                semana=procesamiento.semana,
                nave_texto=procesamiento.nave_texto,
                tipo_cambio=procesamiento.tipo_cambio,
                lineas_preparadas=procesamiento.lineas_preparadas,
                gastos_aplicados=generacion.gastos_aplicados,
                total_cajas_liquidacion=(
                    procesamiento.total_cajas_liquidacion
                ),
                total_cajas_despachos=(
                    procesamiento.total_cajas_despachos
                ),
                destinos_despachos=procesamiento.destinos_despachos,
            )
        except ErrorConfirmacionGeneracionOrsero as error:
            _marcar_error(generacion, str(error))
            return
        logger.info(
            "generacion_orsero=%s fase=reconstruir_desde_bd "
            "segundos=%.3f",
            generacion.id,
            time.perf_counter() - inicio,
        )

        if not resultado.puede_escribir:
            _marcar_error(
                generacion,
                "El procesamiento dejó de ser válido.",
            )
            return

        if ruta_salida.exists():
            ruta_salida.unlink()
        ruta_salida.parent.mkdir(parents=True, exist_ok=True)

        escritura = escribir_archivo_orsero(
            procesamiento=resultado,
            ruta_archivo_cliente=str(ruta_cliente),
            ruta_salida=ruta_salida,
            recalcular_al_final=bool(
                getattr(
                    settings,
                    "ORSERO_RECALCULAR_AL_FINAL",
                    False,
                )
            ),
        )

        if not ruta_salida.is_file():
            _marcar_error(
                generacion,
                "El archivo de salida no fue creado.",
            )
            return

        relativo = (
            Path("procesamientos")
            / "orsero"
            / str(procesamiento.id)
            / "resultados"
            / str(generacion.id)
            / "resultado.xlsx"
        ).as_posix()
        generacion.archivo_resultado.name = relativo
        generacion.nombre_descarga = construir_nombre_descarga()
        generacion.filas_agregadas = escritura.filas_agregadas
        generacion.fila_inicial = escritura.fila_inicial
        generacion.fila_final = escritura.fila_final
        generacion.rango_tabla = escritura.rango_tabla
        generacion.estado = GeneracionOrsero.Estado.COMPLETADO
        generacion.mensaje_error = ""
        generacion.finalizado_en = timezone.now()
        generacion.save()

    except (
        ErrorEscrituraOrsero,
        ErrorEscrituraDimanno,
        ErrorExtraccionOrsero,
        ErrorMatcherOrsero,
        ErrorProcesamientoOrsero,
    ) as error:
        logger.exception(
            "Error en generación Orsero %s",
            generacion.id,
        )
        if ruta_salida.exists():
            ruta_salida.unlink(missing_ok=True)
        _marcar_error(generacion, str(error))
    except Exception:
        logger.exception(
            "Error inesperado en generación Orsero %s",
            generacion.id,
        )
        if ruta_salida.exists():
            ruta_salida.unlink(missing_ok=True)
        _marcar_error(
            generacion,
            "Error inesperado al generar el archivo.",
        )
    finally:
        limpiar_contexto_generacion(token)
