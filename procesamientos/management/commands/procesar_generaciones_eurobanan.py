from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from procesamientos.services.trabajador_generacion_eurobanan import (
    WorkerLockEurobanan,
    WorkerLockEurobananError,
    procesar_generacion_eurobanan,
    reclamar_siguiente_generacion_eurobanan,
)


class Command(BaseCommand):
    help = "Procesa generaciones pendientes de Eurobanan Fresh."

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
            with WorkerLockEurobanan():
                self.stdout.write(
                    self.style.SUCCESS(
                        "Trabajador Eurobanan iniciado."
                    )
                )
                while True:
                    generacion = (
                        reclamar_siguiente_generacion_eurobanan()
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
                    procesar_generacion_eurobanan(generacion)
                    generacion.refresh_from_db()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Finalizó {generacion.id}: "
                            f"{generacion.estado}"
                        )
                    )
                    if once:
                        return
        except WorkerLockEurobananError as error:
            self.stderr.write(self.style.ERROR(str(error)))
            raise SystemExit(1) from error
