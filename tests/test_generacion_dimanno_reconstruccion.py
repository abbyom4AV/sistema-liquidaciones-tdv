from __future__ import annotations

import os
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import SimpleTestCase

from procesamientos.services.generacion_dimanno import (
    ErrorConfirmacionGeneracionDimanno,
    lineas_completas_para_escritura,
    reconstruir_resultado_para_escritura_dimanno,
)


class ReconstruccionEscrituraDimannoTests(SimpleTestCase):
    def _gastos(self) -> dict[str, str]:
        return {
            "Comisión": "100",
            "Flete Eu": "10",
            "Control calidad Eu": "20",
            "THC": "30",
            "Transporte": "40",
            "Aduanas": "50",
        }

    def _linea(self) -> dict:
        return {
            "contenedor": "CONT1",
            "nave": "NAVE",
            "cliente": "DI MANNO",
            "destino": "GENOVA",
            "tipo_fruta": "Especial",
            "carton": "CARTON",
            "calibre": 5,
            "total_cajas": 200,
            "semana": 15,
            "anio": 2026,
            "factura": "xxx5532",
            "factura_corta": "5532",
            "precio_venta_eur": "12.5",
        }

    def test_detecta_lineas_incompletas_antiguas(self):
        self.assertFalse(
            lineas_completas_para_escritura(
                [
                    {
                        "contenedor": "C1",
                        "tipo_fruta": "Especial",
                        "calibre": 5,
                        "total_cajas": 10,
                        "precio_venta_eur": "1",
                    }
                ]
            )
        )
        self.assertTrue(
            lineas_completas_para_escritura([self._linea()])
        )

    def test_reconstruye_sin_releer_archivos(self):
        resultado = reconstruir_resultado_para_escritura_dimanno(
            factura_corta="5532",
            semana=15,
            anio=2026,
            nombre_hoja="FT 5532 W15",
            destino_final="GENOVA",
            origen_destino="manual",
            lineas_preparadas=[self._linea()],
            gastos_aplicados=self._gastos(),
            total_cajas_liquidacion=200,
            total_cajas_despachos=200,
            destinos_despachos=["GENOVA"],
        )
        self.assertTrue(resultado.puede_escribir)
        self.assertEqual(resultado.destino_final, "GENOVA")
        self.assertEqual(
            resultado.liquidacion.gastos["Comisión"],
            Decimal("100"),
        )
        linea = resultado.validacion.lineas_preparadas[0]
        self.assertEqual(linea.despacho.barco, "NAVE")
        self.assertEqual(linea.despacho.carton, "CARTON")
        self.assertEqual(
            linea.precio_venta_eur,
            Decimal("12.5"),
        )

    def test_exige_destino(self):
        with self.assertRaises(ErrorConfirmacionGeneracionDimanno):
            reconstruir_resultado_para_escritura_dimanno(
                factura_corta="5532",
                semana=15,
                anio=2026,
                nombre_hoja="FT 5532 W15",
                destino_final="",
                origen_destino="manual",
                lineas_preparadas=[self._linea()],
                gastos_aplicados=self._gastos(),
            )
