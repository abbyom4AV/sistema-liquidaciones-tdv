from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from openpyxl import Workbook
from openpyxl.worksheet.table import Table, TableStyleInfo

from procesamientos.services.generacion_master import (
    reconstruir_resultado_para_escritura_master,
)
from services.master.writer import (
    COLUMNAS_ENTRADA,
    NOMBRE_DESCARGA_MASTER,
    _resolver_rutas_raw_data,
    construir_valores_fila_master,
    escribir_archivo_master,
)


class WriterMasterTests(unittest.TestCase):
    def _plantilla_minima(self, ruta: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "Raw Data"
        headers = [
            *sorted(COLUMNAS_ENTRADA),
            "Fact. 4 Digitos",
        ]
        ws.append(headers)
        tab = Table(
            displayName="Tabla1",
            ref=f"A1:{chr(64 + len(headers))}1",
        )
        tab.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        ws.add_table(tab)
        wb.save(ruta)

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

    def test_construir_valores_y_escribir_digitados(self):
        with tempfile.TemporaryDirectory() as tmp:
            origen = Path(tmp) / "origen.xlsx"
            salida = Path(tmp) / "salida.xlsx"
            self._plantilla_minima(origen)

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
                        "precio_venta_eur": "10.43",
                    },
                ],
                gastos_aplicados=self._gastos(),
                total_cajas_liquidacion=100,
                total_cajas_despachos=100,
                destinos_despachos=["SETUBAL"],
            )

            valores = construir_valores_fila_master(resultado, 0)
            self.assertEqual(valores["Año"], "2026")
            self.assertEqual(valores["# Calibre"], "5")
            self.assertEqual(valores["Contenedor "], "TEMU1")
            self.assertEqual(valores["Destino"], "SETUBAL")
            self.assertEqual(
                valores["Comision Euros"],
                Decimal("100.5"),
            )

            escritura = escribir_archivo_master(
                procesamiento=resultado,
                ruta_archivo_cliente=origen,
                ruta_salida=salida,
            )
            self.assertEqual(escritura.filas_agregadas, 1)
            self.assertEqual(escritura.factura_corta, "5526")
            self.assertTrue(salida.is_file())
            with ZipFile(salida) as zf:
                hoja, tabla = _resolver_rutas_raw_data(zf)
                self.assertTrue(hoja.endswith(".xml"))
                self.assertIn("table", tabla.lower())
            self.assertEqual(
                NOMBRE_DESCARGA_MASTER,
                "Master Liquidaciones (1).xlsx",
            )


if __name__ == "__main__":
    unittest.main()
