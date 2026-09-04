from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from procesamientos.models import GeneracionEurobanan
from procesamientos.services.generacion_eurobanan import (
    ErrorConfirmacionGeneracionEurobanan,
    construir_nombre_descarga,
    reconstruir_resultado_para_escritura_eurobanan,
)
from services.eurobanan.extractor import ErrorExtraccionEurobanan
from services.eurobanan.matcher import ErrorMatcherEurobanan
from services.eurobanan.processor import ErrorProcesamientoEurobanan
from services.eurobanan.writer import (
    ErrorEscrituraEurobanan,
    escribir_archivo_eurobanan,
    establecer_contexto_generacion,
    limpiar_contexto_generacion,
)

logger = logging.getLogger(__name__)


class WorkerLockEurobananError(RuntimeError):
    """No se pudo adquirir el bloqueo del trabajador Eurobanan."""


class WorkerLockEurobanan:
    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta or (
            Path(settings.BASE_DIR)
            / "runtime"
            / "eurobanan_worker.lock"
        )
        self._fd: int | None = None

    def __enter__(self) -> "WorkerLockEurobanan":
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                str(self.ruta),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise WorkerLockEurobananError(
                "Ya hay un trabajador Eurobanan en ejecución. Si "
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


def reclamar_siguiente_generacion_eurobanan() -> (
    GeneracionEurobanan | None
):
    with transaction.atomic():
        generacion = (
            GeneracionEurobanan.objects.select_for_update()
            .filter(estado=GeneracionEurobanan.Estado.PENDIENTE)
            .order_by("solicitado_en")
            .first()
        )
        if generacion is None:
            return None
        generacion.estado = GeneracionEurobanan.Estado.PROCESANDO
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
    generacion: GeneracionEurobanan,
    mensaje: str,
) -> None:
    generacion.estado = GeneracionEurobanan.Estado.ERROR
    generacion.mensaje_error = mensaje
    generacion.finalizado_en = timezone.now()
    generacion.save(
        update_fields=[
            "estado",
            "mensaje_error",
            "finalizado_en",
        ]
    )


def procesar_generacion_eurobanan(
    generacion: GeneracionEurobanan,
) -> None:
    generacion.refresh_from_db()
    procesamiento = generacion.procesamiento
    ruta_salida = (
        Path(settings.MEDIA_ROOT)
        / "procesamientos"
        / "Eurobanan"
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

        if (
            not procesamiento.puede_escribir
            or procesamiento.estado != "listo"
            or procesamiento.tiene_rubros_pendientes
        ):
            _marcar_error(
                generacion,
                "El procesamiento no está listo para generar.",
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
            resultado = reconstruir_resultado_para_escritura_eurobanan(
                factura_corta=procesamiento.factura_corta,
                semana=procesamiento.semana,
                anio=procesamiento.anio,
                semana_texto=procesamiento.semana_texto,
                destino_final=(
                    generacion.destino_aplicado
                    or procesamiento.destino_final
                ),
                lineas_preparadas=procesamiento.lineas_preparadas,
                resumen_gastos=procesamiento.resumen_gastos,
                total_cajas_liquidacion=(
                    procesamiento.total_cajas_liquidacion
                ),
                total_cajas_despachos=(
                    procesamiento.total_cajas_despachos
                ),
                total_venta_eur=procesamiento.total_venta_eur,
                total_gastos_eur=procesamiento.total_gastos_eur,
                destinos_despachos=procesamiento.destinos_despachos,
            )
        except ErrorConfirmacionGeneracionEurobanan as error:
            _marcar_error(generacion, str(error))
            return
        logger.info(
            "generacion_eurobanan=%s fase=reconstruir_desde_bd "
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

        escritura = escribir_archivo_eurobanan(
            procesamiento=resultado,
            ruta_archivo_cliente=str(ruta_cliente),
            ruta_salida=ruta_salida,
        )

        if not ruta_salida.is_file():
            _marcar_error(
                generacion,
                "El archivo de salida no fue creado.",
            )
            return

        relativo = (
            Path("procesamientos")
            / "Eurobanan"
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
        generacion.estado = GeneracionEurobanan.Estado.COMPLETADO
        generacion.mensaje_error = ""
        generacion.finalizado_en = timezone.now()
        generacion.save()

    except (
        ErrorEscrituraEurobanan,
        ErrorExtraccionEurobanan,
        ErrorMatcherEurobanan,
        ErrorProcesamientoEurobanan,
    ) as error:
        logger.exception(
            "Error en generación Eurobanan %s",
            generacion.id,
        )
        if ruta_salida.exists():
            ruta_salida.unlink(missing_ok=True)
        _marcar_error(generacion, str(error))
    except Exception:
        logger.exception(
            "Error inesperado en generación Eurobanan %s",
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
