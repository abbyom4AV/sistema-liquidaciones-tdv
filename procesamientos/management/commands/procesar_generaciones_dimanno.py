from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from procesamientos.services.trabajador_generacion import (
    WorkerLock,
    WorkerLockError,
    procesar_generacion,
    reclamar_siguiente_generacion,
)


class Command(BaseCommand):
    help = (
        "Procesa generaciones pendientes de Di Manno "
        "de una en una."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Procesa como máximo un trabajo y termina.",
        )
        parser.add_argument(
            "--intervalo",
            type=float,
            default=5.0,
            help=(
                "Segundos de espera entre consultas "
                "cuando no hay trabajos."
            ),
        )

    def handle(self, *args, **options):
        once = options["once"]
        intervalo = max(float(options["intervalo"]), 0.5)

        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            force=True,
        )

        try:
            with WorkerLock():
                recalcular = bool(
                    getattr(
                        settings,
                        "DIMANNO_RECALCULAR_AL_FINAL",
                        False,
                    )
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        "Trabajador Di Manno iniciado."
                    )
                )
                self.stdout.write(
                    "Recálculo al final: "
                    f"{'sí' if recalcular else 'no'}"
                )
                while True:
                    generacion = reclamar_siguiente_generacion()
                    if generacion is None:
                        if once:
                            self.stdout.write(
                                "No hay generaciones pendientes."
                            )
                            return
                        time.sleep(intervalo)
                        continue

                    self.stdout.write(
                        f"Procesando generación {generacion.id}…"
                    )
                    procesar_generacion(generacion)
                    generacion.refresh_from_db()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Generación {generacion.id}: "
                            f"{generacion.estado}"
                        )
                    )
                    if once:
                        return
        except WorkerLockError as error:
            self.stderr.write(self.style.ERROR(str(error)))
            raise SystemExit(1) from error
