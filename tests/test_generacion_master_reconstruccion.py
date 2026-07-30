from __future__ import annotations

import os
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import SimpleTestCase

from procesamientos.services.generacion_master import (
    ErrorConfirmacionGeneracionMaster,
    reconstruir_resultado_para_escritura_master,
)


class ReconstruccionEscrituraMasterTests(SimpleTestCase):
    def _gastos(self) -> dict[str, str]:
        return {
            "LC Euros": "10",
            "Cust.C Euros": "20",
            "Import.D Euros": "30",
            "Ener&Demur. Euros": "40",
            "Inspection Euros": "50",
            "Transport.P-W Euros": "60",
            "Transport C. Euros": "70",
            "Relabelling Euros": "0",
            "Comision Euros": "100.5",
        }

    def test_reconstruye_lineas_y_gastos_sin_pdf(self):
        resultado = reconstruir_resultado_para_escritura_master(
            factura_corta="5526",
            semana=21,
            anio=2026,
            semana_texto="21-2026",
            destino_final="SETUBAL",
            lineas_preparadas=[
                {
                    "contenedor": "TEMU1",
                    "nave": "NAVE",
                    "destino": "SETUBAL",
                    "tipo_fruta": "VERDE",
                    "variante": "VERDE",
                    "carton": "CARTON",
                    "calibre": 5,
                    "total_cajas": 100,
                    "merma": 2,
                    "precio_venta_eur": (
                        "10.43219642857142857142857143"
                    ),
                },
                {
                    "contenedor": "TEMU2",
                    "nave": "NAVE",
                    "destino": "SETUBAL",
                    "tipo_fruta": "ESPECIAL",
                    "variante": "VERTICAL",
                    "carton": "VERTICAL",
                    "calibre": 6,
                    "total_cajas": 50,
                    "merma": 0,
                    "precio_venta_eur": "13.17",
                },
            ],
            gastos_aplicados=self._gastos(),
            total_cajas_liquidacion=150,
            total_cajas_despachos=150,
            destinos_despachos=["SETUBAL"],
        )

        self.assertTrue(resultado.puede_escribir)
        self.assertEqual(resultado.destino_final, "SETUBAL")
        self.assertEqual(
            resultado.liquidacion.factura_corta,
            "5526",
        )
        self.assertEqual(resultado.despachos.semana, 21)
        self.assertEqual(
            len(resultado.validacion.lineas_preparadas),
            2,
        )
        primera = resultado.validacion.lineas_preparadas[0]
        self.assertEqual(
            primera.precio_venta_eur,
            Decimal("10.43219642857142857142857143"),
        )
        self.assertEqual(
            primera.gastos["Comision Euros"],
            Decimal("100.5"),
        )
        self.assertEqual(primera.despacho.contenedor, "TEMU1")
        self.assertEqual(primera.merma, 2)

    def test_exige_destino_y_lineas(self):
        with self.assertRaises(ErrorConfirmacionGeneracionMaster):
            reconstruir_resultado_para_escritura_master(
                factura_corta="5526",
                semana=21,
                anio=2026,
                semana_texto="21-2026",
                destino_final="",
                lineas_preparadas=[
                    {
                        "contenedor": "TEMU1",
                        "nave": "NAVE",
                        "destino": "SETUBAL",
                        "tipo_fruta": "VERDE",
                        "variante": "VERDE",
                        "carton": "CARTON",
                        "calibre": 5,
                        "total_cajas": 100,
                        "merma": 0,
                        "precio_venta_eur": "10",
                    }
                ],
                gastos_aplicados=self._gastos(),
            )

        with self.assertRaises(ErrorConfirmacionGeneracionMaster):
            reconstruir_resultado_para_escritura_master(
                factura_corta="5526",
                semana=21,
                anio=2026,
                semana_texto="21-2026",
                destino_final="SETUBAL",
                lineas_preparadas=[],
                gastos_aplicados=self._gastos(),
            )
