from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from procesamientos.models import GeneracionSifa
from procesamientos.services.generacion_sifa import (
    ErrorConfirmacionGeneracionSifa,
    construir_nombre_descarga,
    reconstruir_resultado_para_escritura_sifa,
)
from services.sifa.processor import ErrorProcesamientoSifa
from services.sifa.writer import (
    ErrorEscrituraSifa,
    escribir_archivo_sifa,
    establecer_contexto_generacion,
    limpiar_contexto_generacion,
)

logger = logging.getLogger(__name__)


class WorkerLockSifaError(RuntimeError):
    """No se pudo adquirir el bloqueo del trabajador SIFA."""


class WorkerLockSifa:
    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta or (
            Path(settings.BASE_DIR)
            / "runtime"
            / "sifa_worker.lock"
        )
        self._fd: int | None = None

    def __enter__(self) -> "WorkerLockSifa":
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                str(self.ruta),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise WorkerLockSifaError(
                "Ya hay un trabajador SIFA en ejecución. Si "
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


def reclamar_siguiente_generacion_sifa() -> GeneracionSifa | None:
    with transaction.atomic():
        generacion = (
            GeneracionSifa.objects.select_for_update()
            .filter(estado=GeneracionSifa.Estado.PENDIENTE)
            .order_by("solicitado_en")
            .first()
        )
        if generacion is None:
            return None
        generacion.estado = GeneracionSifa.Estado.PROCESANDO
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


def _marcar_error(
    generacion: GeneracionSifa,
    mensaje: str,
) -> None:
    generacion.estado = GeneracionSifa.Estado.ERROR
    generacion.mensaje_error = mensaje
    generacion.finalizado_en = timezone.now()
    generacion.save(
        update_fields=[
            "estado",
            "mensaje_error",
            "finalizado_en",
        ]
    )


def procesar_generacion_sifa(generacion: GeneracionSifa) -> None:
    generacion.refresh_from_db()
    procesamiento = generacion.procesamiento
    ruta_salida = (
        Path(settings.MEDIA_ROOT)
        / "procesamientos"
        / "sifa"
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

        if not procesamiento.lineas_preparadas:
            _marcar_error(
                generacion,
                "No hay líneas preparadas guardadas.",
            )
            return

        inicio = time.perf_counter()
        try:
            resultado = reconstruir_resultado_para_escritura_sifa(
                anio=procesamiento.anio,
                semana=procesamiento.semana,
                destino_ui=procesamiento.destino_ui,
                lineas_preparadas=(
                    procesamiento.lineas_preparadas
                ),
                total_cajas_liquidacion=(
                    procesamiento.total_cajas_liquidacion
                ),
                total_cajas_despachos=(
                    procesamiento.total_cajas_despachos
                ),
                destinos_despachos=(
                    procesamiento.destinos_despachos
                ),
                comision_total=procesamiento.comision_total,
                resumen_gastos=procesamiento.resumen_gastos,
                lineas_con_comision=(
                    procesamiento.lineas_con_comision
                ),
                lineas_sin_comision=(
                    procesamiento.lineas_sin_comision
                ),
            )
        except ErrorConfirmacionGeneracionSifa as error:
            _marcar_error(generacion, str(error))
            return
        logger.info(
            "generacion_sifa=%s fase=reconstruir_desde_bd "
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

        escritura = escribir_archivo_sifa(
            procesamiento=resultado,
            ruta_archivo_cliente=str(ruta_cliente),
            ruta_salida=ruta_salida,
            recalcular_al_final=bool(
                getattr(
                    settings,
                    "SIFA_RECALCULAR_AL_FINAL",
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
            / "sifa"
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
        generacion.estado = GeneracionSifa.Estado.COMPLETADO
        generacion.mensaje_error = ""
        generacion.finalizado_en = timezone.now()
        generacion.save()

    except (
        ErrorEscrituraSifa,
        ErrorProcesamientoSifa,
    ) as error:
        logger.exception(
            "Error en generación SIFA %s",
            generacion.id,
        )
        if ruta_salida.exists():
            ruta_salida.unlink(missing_ok=True)
        _marcar_error(generacion, str(error))
    except Exception:
        logger.exception(
            "Error inesperado en generación SIFA %s",
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
