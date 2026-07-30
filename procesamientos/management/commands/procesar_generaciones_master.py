from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from procesamientos.services.trabajador_generacion_master import (
    WorkerLockMaster,
    WorkerLockMasterError,
    procesar_generacion_master,
    reclamar_siguiente_generacion_master,
)


class Command(BaseCommand):
    help = "Procesa generaciones pendientes de Master Fruits."

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
            with WorkerLockMaster():
                self.stdout.write(
                    self.style.SUCCESS(
                        "Trabajador Master Fruits iniciado."
                    )
                )
                while True:
                    generacion = (
                        reclamar_siguiente_generacion_master()
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
                    procesar_generacion_master(generacion)
                    generacion.refresh_from_db()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Finalizó {generacion.id}: "
                            f"{generacion.estado}"
                        )
                    )
                    if once:
                        return
        except WorkerLockMasterError as error:
            self.stderr.write(self.style.ERROR(str(error)))
            raise SystemExit(1) from error
