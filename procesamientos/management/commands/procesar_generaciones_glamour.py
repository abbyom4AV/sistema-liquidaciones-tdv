from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from procesamientos.services.trabajador_generacion_glamour import (
    WorkerLockGlamour,
    WorkerLockGlamourError,
    procesar_generacion_glamour,
    reclamar_siguiente_generacion_glamour,
)


class Command(BaseCommand):
    help = "Procesa generaciones pendientes de Glamour Fresh."

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
            help="Segundos entre consultas si no hay trabajos.",
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
            with WorkerLockGlamour():
                self.stdout.write(
                    self.style.SUCCESS(
                        "Trabajador Glamour iniciado."
                    )
                )
                while True:
                    generacion = (
                        reclamar_siguiente_generacion_glamour()
                    )
                    if generacion is None:
                        if once:
                            self.stdout.write(
                                "No hay generaciones pendientes."
                            )
                            return
                        time.sleep(intervalo)
                        continue
                    self.stdout.write(
                        f"Procesando {generacion.id}…"
                    )
                    procesar_generacion_glamour(generacion)
                    generacion.refresh_from_db()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Finalizó {generacion.id}: "
                            f"{generacion.estado}"
                        )
                    )
                    if once:
                        return
        except WorkerLockGlamourError as error:
            self.stderr.write(self.style.ERROR(str(error)))
            raise SystemExit(1) from error
