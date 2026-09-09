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
    _detectar_ultima_fila_datos,
    _preparar_sheet_antes_escritura,
    _resolver_fila_fin_datos,
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

    def test_preparar_sheet_quita_huerfanas_y_duplicados(self):
        """
        Reproduce el fallo de la semana 28: filas huérfanas vacías
        con el mismo r="N" que las nuevas. Excel se queda con la
        vacía y descarta los datos.
        """
        fila_completa = (
            '<row r="1067" spans="1:101">'
            + ('<c r="A1067"><v>1</v></c>' * 50)
            + "</row>"
        )
        # Huérfanas como las de 1071-1075 del resultado real.
        huerfanas = "".join(
            (
                f'<row r="{n}" spans="74:74">'
                f'<c r="BV{n}"><f>+1</f><v>0</v></c></row>'
            )
            for n in range(1071, 1076)
        )
        digitada = (
            '<row r="1068" spans="1:101">'
            + (
                '<c r="A1068" t="inlineStr">'
                "<is><t>28-2026</t></is></c>"
                * 21
            )
            + "</row>"
        )
        # Duplicado: primero vacía, luego con datos (como en el XML roto).
        duplicada_vacia = (
            '<row r="1071" spans="74:74">'
            '<c r="BV1071"><f>+1</f><v>0</v></c></row>'
        )
        duplicada_datos = (
            '<row r="1071" spans="1:101">'
            + (
                '<c r="A1071" t="inlineStr">'
                "<is><t>MASTERFRUITS</t></is></c>"
                * 21
            )
            + "</row>"
        )
        sheet = (
            '<?xml version="1.0"?><worksheet>'
            "<sheetData>"
            '<row r="1"><c r="A1"><v>Semana</v></c></row>'
            f"{fila_completa}{digitada}{huerfanas}"
            f"{duplicada_vacia}{duplicada_datos}"
            "</sheetData></worksheet>"
        )

        self.assertEqual(_detectar_ultima_fila_datos(sheet), 1071)
        self.assertEqual(
            _resolver_fila_fin_datos(sheet, 1067),
            1071,
        )

        # Con límite en la última fila real previa a las huérfanas
        # sueltas (1067), se eliminan 1068+ y las huérfanas.
        limpio = _preparar_sheet_antes_escritura(sheet, 1067)
        self.assertIn('r="1067"', limpio)
        self.assertNotIn('r="1068"', limpio)
        self.assertNotIn('r="1071"', limpio)
        self.assertNotIn('r="1075"', limpio)

        # Con límite en 1071 (tabla atrasada o digitada previa),
        # conserva la fila con más celdas y descarta la vacía.
        limpio_dup = _preparar_sheet_antes_escritura(sheet, 1071)
        self.assertEqual(limpio_dup.count('<row r="1071"'), 1)
        self.assertIn("MASTERFRUITS", limpio_dup)
        self.assertNotIn('r="1075"', limpio_dup)


if __name__ == "__main__":
    unittest.main()
