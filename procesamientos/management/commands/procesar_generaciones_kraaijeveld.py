from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from procesamientos.services.trabajador_generacion_kraaijeveld import (
    WorkerLockKraaijeveld,
    WorkerLockKraaijeveldError,
    procesar_generacion_kraaijeveld,
    reclamar_siguiente_generacion_kraaijeveld,
)


class Command(BaseCommand):
    help = "Procesa generaciones pendientes de Kraaijeveld."

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
            with WorkerLockKraaijeveld():
                self.stdout.write(
                    self.style.SUCCESS(
                        "Trabajador Kraaijeveld iniciado."
                    )
                )
                while True:
                    generacion = (
                        reclamar_siguiente_generacion_kraaijeveld()
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
                    procesar_generacion_kraaijeveld(generacion)
                    generacion.refresh_from_db()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Finalizó {generacion.id}: "
                            f"{generacion.estado}"
                        )
                    )
                    if once:
                        return
        except WorkerLockKraaijeveldError as error:
            self.stderr.write(self.style.ERROR(str(error)))
            raise SystemExit(1) from error
