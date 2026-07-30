from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from procesamientos.models import GeneracionDimanno
from procesamientos.services.generacion_dimanno import (
    ErrorConfirmacionGeneracionDimanno,
    aplicar_confirmaciones_a_procesamiento,
    construir_nombre_descarga,
    lineas_completas_para_escritura,
    reconstruir_resultado_para_escritura_dimanno,
)
from services.dimanno.processor import (
    ErrorProcesamientoDimanno,
    preparar_procesamiento_dimanno,
)
from services.dimanno.writer import (
    ErrorEscrituraDimanno,
    establecer_contexto_generacion,
    escribir_archivo_dimanno,
    limpiar_contexto_generacion,
)

logger = logging.getLogger(__name__)


class WorkerLockError(RuntimeError):
    """No se pudo adquirir el bloqueo del trabajador."""


class WorkerLock:
    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta or (
            Path(settings.BASE_DIR)
            / "runtime"
            / "dimanno_worker.lock"
        )
        self._fd: int | None = None

    def __enter__(self) -> "WorkerLock":
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                str(self.ruta),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise WorkerLockError(
                "Ya hay un trabajador de generaciones "
                "Di Manno en ejecución. Si el proceso anterior "
                "terminó abruptamente, elimine manualmente el "
                f"archivo de bloqueo: {self.ruta}"
            ) from error

        contenido = (
            f"pid={os.getpid()}\n"
            f"inicio={datetime.now(dt_timezone.utc).isoformat()}\n"
        ).encode("utf-8")
        os.write(self._fd, contenido)
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
                "No se pudo eliminar el archivo de bloqueo %s",
                self.ruta,
            )


def reclamar_siguiente_generacion() -> GeneracionDimanno | None:
    with transaction.atomic():
        generacion = (
            GeneracionDimanno.objects.select_for_update()
            .filter(estado=GeneracionDimanno.Estado.PENDIENTE)
            .order_by("solicitado_en")
            .first()
        )
        if generacion is None:
            return None

        generacion.estado = GeneracionDimanno.Estado.PROCESANDO
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


def _archivos_entrada_existen(
    generacion: GeneracionDimanno,
) -> bool:
    procesamiento = generacion.procesamiento
    try:
        rutas = [
            Path(procesamiento.archivo_despachos.path),
            Path(procesamiento.archivo_liquidacion.path),
            Path(procesamiento.archivo_cliente.path),
        ]
    except (ValueError, FileNotFoundError):
        return False
    return all(ruta.is_file() for ruta in rutas)


def _ruta_salida_controlada(
    generacion: GeneracionDimanno,
) -> Path:
    return (
        Path(settings.MEDIA_ROOT)
        / "procesamientos"
        / "dimanno"
        / str(generacion.procesamiento_id)
        / "resultados"
        / str(generacion.id)
        / "resultado.xlsx"
    )


def _marcar_error(
    generacion: GeneracionDimanno,
    mensaje: str,
) -> None:
    generacion.estado = GeneracionDimanno.Estado.ERROR
    generacion.mensaje_error = mensaje
    generacion.finalizado_en = timezone.now()
    generacion.save(
        update_fields=[
            "estado",
            "mensaje_error",
            "finalizado_en",
        ]
    )


def procesar_generacion(
    generacion: GeneracionDimanno,
) -> None:
    generacion.refresh_from_db()
    procesamiento = generacion.procesamiento
    ruta_salida = _ruta_salida_controlada(generacion)
    id_gen = str(generacion.id)
    token_contexto = establecer_contexto_generacion(id_gen)
    inicio_total = time.perf_counter()

    try:
        if not (generacion.destino_aplicado or "").strip():
            _marcar_error(
                generacion,
                "No hay un destino final confirmado.",
            )
            return

        if not generacion.gastos_aplicados:
            _marcar_error(
                generacion,
                "No hay gastos confirmados para la generación.",
            )
            return

        usar_reconstruccion = lineas_completas_para_escritura(
            procesamiento.lineas_preparadas
        )
        if usar_reconstruccion:
            try:
                ruta_cliente = Path(
                    procesamiento.archivo_cliente.path
                )
            except (ValueError, FileNotFoundError):
                ruta_cliente = Path()
            if not ruta_cliente.is_file():
                _marcar_error(
                    generacion,
                    "El acumulativo del cliente ya no está "
                    "disponible.",
                )
                return
        elif not _archivos_entrada_existen(generacion):
            _marcar_error(
                generacion,
                "Los archivos de entrada ya no están disponibles.",
            )
            return

        inicio = time.perf_counter()
        if usar_reconstruccion:
            try:
                resultado = (
                    reconstruir_resultado_para_escritura_dimanno(
                        factura_corta=procesamiento.factura_corta,
                        semana=procesamiento.semana,
                        anio=procesamiento.anio,
                        nombre_hoja=procesamiento.nombre_hoja,
                        destino_final=generacion.destino_aplicado,
                        origen_destino=(
                            generacion.origen_destino_aplicado
                            or None
                        ),
                        lineas_preparadas=(
                            procesamiento.lineas_preparadas
                        ),
                        gastos_aplicados=(
                            generacion.gastos_aplicados
                        ),
                        total_cajas_liquidacion=(
                            procesamiento.total_cajas_liquidacion
                        ),
                        total_cajas_despachos=(
                            procesamiento.total_cajas_despachos
                        ),
                        destino_liquidacion=(
                            procesamiento.destino_liquidacion
                        ),
                        destinos_despachos=(
                            procesamiento.destinos_despachos
                        ),
                    )
                )
            except ErrorConfirmacionGeneracionDimanno as error:
                _marcar_error(generacion, str(error))
                return
            logger.info(
                "generacion=%s fase=reconstruir_desde_bd "
                "segundos=%.3f",
                id_gen,
                time.perf_counter() - inicio,
            )
        else:
            resultado = preparar_procesamiento_dimanno(
                ruta_liquidacion=(
                    procesamiento.archivo_liquidacion.path
                ),
                nombre_hoja=procesamiento.nombre_hoja,
                ruta_despachos=(
                    procesamiento.archivo_despachos.path
                ),
                anio=procesamiento.anio,
                destino_confirmado=generacion.destino_aplicado,
            )
            logger.info(
                "generacion=%s fase=preparar_procesamiento "
                "segundos=%.3f",
                id_gen,
                time.perf_counter() - inicio,
            )

            inicio = time.perf_counter()
            try:
                resultado = aplicar_confirmaciones_a_procesamiento(
                    resultado,
                    destino_final=generacion.destino_aplicado,
                    gastos_aplicados=generacion.gastos_aplicados,
                    origen_destino=(
                        generacion.origen_destino_aplicado or None
                    ),
                )
            except ErrorConfirmacionGeneracionDimanno as error:
                _marcar_error(generacion, str(error))
                return
            finally:
                logger.info(
                    "generacion=%s fase=aplicar_confirmaciones "
                    "segundos=%.3f",
                    id_gen,
                    time.perf_counter() - inicio,
                )

        if not resultado.puede_escribir:
            _marcar_error(
                generacion,
                (
                    "El procesamiento dejó de cumplir las "
                    "validaciones requeridas."
                ),
            )
            return

        if ruta_salida.exists():
            ruta_salida.unlink()

        ruta_salida.parent.mkdir(parents=True, exist_ok=True)

        inicio = time.perf_counter()
        escritura = escribir_archivo_dimanno(
            procesamiento=resultado,
            ruta_archivo_cliente=(
                procesamiento.archivo_cliente.path
            ),
            ruta_salida=ruta_salida,
            recalcular_al_final=bool(
                getattr(
                    settings,
                    "DIMANNO_RECALCULAR_AL_FINAL",
                    False,
                )
            ),
        )
        logger.info(
            "generacion=%s fase=escribir_archivo "
            "segundos=%.3f",
            id_gen,
            time.perf_counter() - inicio,
        )

        if not ruta_salida.is_file():
            _marcar_error(
                generacion,
                "El archivo de salida no fue creado.",
            )
            return

        nombre = construir_nombre_descarga()

        relativo = (
            Path("procesamientos")
            / "dimanno"
            / str(procesamiento.id)
            / "resultados"
            / str(generacion.id)
            / "resultado.xlsx"
        ).as_posix()
        generacion.archivo_resultado.name = relativo
        generacion.nombre_descarga = nombre
        generacion.filas_agregadas = escritura.filas_agregadas
        generacion.fila_inicial = escritura.fila_inicial
        generacion.fila_final = escritura.fila_final
        generacion.rango_tabla = escritura.rango_tabla
        generacion.estado = GeneracionDimanno.Estado.COMPLETADO
        generacion.mensaje_error = ""
        generacion.finalizado_en = timezone.now()
        generacion.save()

    except ErrorEscrituraDimanno:
        logger.exception(
            "Excel no permitió completar la generación %s",
            generacion.id,
        )
        if ruta_salida.exists():
            ruta_salida.unlink(missing_ok=True)
        _marcar_error(
            generacion,
            "Excel no permitió completar la generación.",
        )
    except ErrorProcesamientoDimanno:
        logger.exception(
            "Error de procesamiento en generación %s",
            generacion.id,
        )
        if ruta_salida.exists():
            ruta_salida.unlink(missing_ok=True)
        _marcar_error(
            generacion,
            (
                "El procesamiento dejó de cumplir las "
                "validaciones requeridas."
            ),
        )
    except Exception:
        logger.exception(
            "Error inesperado en generación %s",
            generacion.id,
        )
        if ruta_salida.exists():
            ruta_salida.unlink(missing_ok=True)
        _marcar_error(
            generacion,
            (
                "Ocurrió un error inesperado durante "
                "la generación."
            ),
        )
    finally:
        logger.info(
            "generacion=%s fase=total_trabajador "
            "segundos=%.3f",
            id_gen,
            time.perf_counter() - inicio_total,
        )
        limpiar_contexto_generacion(token_contexto)
