from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook

from procesamientos.services.generacion_glamour import (
    reconstruir_resultado_para_escritura_glamour,
    serializar_lineas_preparadas_glamour,
)
from services.glamour.extractor import (
    COLUMNAS_GASTO,
    clave_gasto,
    extraer_liquidacion_glamour,
    mapear_columna_gasto,
    parsear_numero,
)
from services.glamour.matcher import (
    CLIENTE_GLAMOUR,
    LineaDespachoGlamour,
    ResultadoMatcherGlamour,
)
from services.glamour.validator import (
    LineaPreparadaGlamour,
    validar_liquidacion_glamour,
)
from services.glamour.writer import (
    NOMBRE_DESCARGA_GLAMOUR,
    construir_valores_fila_glamour,
    escribir_archivo_glamour,
    _resolver_rutas_raw_data,
)


PDF_MUESTRA = Path(
    r"c:\Users\aobando\Fruta Internacional\GONZÁLES, César (TDV) "
    r"- Liquidaciones TDV\Clientes Liquidaciones PDF\GLAMOUR"
    r"\2026\Semana 9\B5073 - Algeciras.pdf"
)


def _linea_despacho(**kwargs) -> LineaDespachoGlamour:
    base = dict(
        fila_excel=2,
        semana=9,
        anio=2026,
        semana_texto="09-2026",
        contenedor="CGMU5152457",
        cliente=CLIENTE_GLAMOUR,
        barco="NAVE X",
        puerto_destino="ALGECIRAS",
        tipo_empaque="ESPECIAL",
        carton="CARTON STD",
        calibre=5,
        total_cajas=525,
        factura="5073",
        factura_corta="5073",
    )
    base.update(kwargs)
    return LineaDespachoGlamour(**base)


class ParseoGlamourTests(unittest.TestCase):
    def test_parsear_numero_eu(self):
        self.assertEqual(parsear_numero("1.800,00"), Decimal("1800.00"))
        self.assertEqual(parsear_numero("45.483,62"), Decimal("45483.62"))
        self.assertEqual(parsear_numero("11,96"), Decimal("11.96"))

    def test_mapeo_base_gastos(self):
        self.assertEqual(
            mapear_columna_gasto("COMISION 8%"),
            "Comision Eur",
        )
        self.assertEqual(
            mapear_columna_gasto("TRANSPORTE ALGECIRAS"),
            "T Euros",
        )
        self.assertEqual(
            mapear_columna_gasto("TRANSITARIO UCC SPAIN"),
            "TUCC Euros",
        )
        self.assertIsNone(
            mapear_columna_gasto("GASTO RARO XYZ")
        )
        self.assertEqual(
            mapear_columna_gasto(
                "GASTO RARO XYZ",
                mapeos_extra={
                    clave_gasto("GASTO RARO XYZ"): "TFC Euros",
                },
            ),
            "TFC Euros",
        )


class ExtraccionGlamourTests(unittest.TestCase):
    def test_extrae_pdf_2026_si_existe(self):
        if not PDF_MUESTRA.is_file():
            self.skipTest("PDF B5073 no disponible")
        liq = extraer_liquidacion_glamour(PDF_MUESTRA)
        self.assertEqual(liq.factura_corta, "5073")
        self.assertEqual(liq.destino_pdf, "ALGECIRAS")
        self.assertEqual(liq.total_cajas, 6300)
        self.assertEqual(liq.total_venta_eur, Decimal("88407.75"))
        self.assertEqual(
            liq.total_importe_neto_eur,
            Decimal("74235.88"),
        )
        self.assertEqual(liq.gastos["Comision Eur"], Decimal("7072.62"))
        self.assertEqual(liq.gastos["T Euros"], Decimal("2400.00"))
        self.assertEqual(liq.gastos["TUCC Euros"], Decimal("4699.25"))
        self.assertEqual(liq.rubros_no_mapeados, ())
        self.assertEqual(len(liq.contenedores), 4)


class ValidacionGlamourTests(unittest.TestCase):
    def test_bloquea_rubros_no_mapeados(self):
        from services.glamour.extractor import (
            LiquidacionGlamour,
            LineaProductoGlamour,
        )

        liq = LiquidacionGlamour(
            archivo="x.pdf",
            factura_corta="5073",
            referencia="",
            destino_pdf="ALGECIRAS",
            contenedores=("CGMU5152457",),
            productos=(
                LineaProductoGlamour(
                    calibre=5,
                    bultos=525,
                    precio_eur=Decimal("15.34"),
                    importe_eur=Decimal("8053.50"),
                    descripcion="",
                ),
            ),
            gastos={col: Decimal("0") for col in COLUMNAS_GASTO},
            rubros_mapeados=(),
            rubros_no_mapeados=(
                ("GASTO NUEVO", Decimal("100")),
            ),
            total_cajas=525,
            total_venta_eur=Decimal("8053.50"),
            total_importe_neto_eur=None,
            comision_pct=None,
        )
        desp = ResultadoMatcherGlamour(
            archivo="d.xlsx",
            hoja="Base Datos",
            cliente_buscado=CLIENTE_GLAMOUR,
            factura_corta_buscada="5073",
            semana=9,
            anio=2026,
            destino_buscado="ALGECIRAS",
            semana_texto="09-2026",
            lineas=(_linea_despacho(),),
            total_cajas=525,
            contenedores=("CGMU5152457",),
            destinos=("ALGECIRAS",),
        )
        val = validar_liquidacion_glamour(liq, desp)
        self.assertFalse(val.es_valido)
        codigos = {e.codigo for e in val.errores}
        self.assertIn("RUBROS_NO_MAPEADOS", codigos)


class WriterGlamourTests(unittest.TestCase):
    def _plantilla_minima(self, ruta: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "Raw Data"
        headers = [
            "Semana",
            "Año",
            "Cliente",
            "Nave",
            "Contenedor ",
            "Destino",
            "Tipo de fruta",
            "Cartón",
            "# Calibre",
            "Total Cajas",
            *COLUMNAS_GASTO,
            "Precio de Venta €",
        ]
        ws.append(headers)
        # Tabla1 vía openpyxl Table
        from openpyxl.worksheet.table import Table, TableStyleInfo

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

    def test_escribir_digitados(self):
        with tempfile.TemporaryDirectory() as tmp:
            origen = Path(tmp) / "origen.xlsx"
            salida = Path(tmp) / "salida.xlsx"
            self._plantilla_minima(origen)

            gastos = {col: Decimal("0") for col in COLUMNAS_GASTO}
            gastos["Comision Eur"] = Decimal("10")
            gastos["T Euros"] = Decimal("20")
            despacho = _linea_despacho()
            linea = LineaPreparadaGlamour(
                despacho=despacho,
                tipo_fruta="ESPECIAL",
                calibre=5,
                precio_venta_eur=Decimal("15.34"),
                gastos=gastos,
            )
            resultado = reconstruir_resultado_para_escritura_glamour(
                factura_corta="5073",
                semana=9,
                anio=2026,
                semana_texto="09-2026",
                destino_final="ALGECIRAS",
                lineas_preparadas=serializar_lineas_preparadas_glamour(
                    (linea,)
                ),
                resumen_gastos=gastos,
                total_cajas_liquidacion=525,
                total_cajas_despachos=525,
                total_venta_eur=Decimal("8053.50"),
                total_gastos_eur=Decimal("30"),
            )
            valores = construir_valores_fila_glamour(resultado, 0)
            self.assertEqual(valores["Tipo de fruta"], "ESPECIAL")
            self.assertEqual(valores["# Calibre"], "5")
            self.assertEqual(valores["Año"], "2026")
            self.assertEqual(
                valores["Comision Eur"],
                Decimal("10"),
            )

            escritura = escribir_archivo_glamour(
                procesamiento=resultado,
                ruta_archivo_cliente=origen,
                ruta_salida=salida,
            )
            self.assertEqual(escritura.filas_agregadas, 1)
            self.assertTrue(salida.is_file())
            with ZipFile(salida) as zf:
                hoja, tabla = _resolver_rutas_raw_data(zf)
                self.assertTrue(hoja.endswith(".xml"))
                self.assertIn("table", tabla.lower())
            self.assertEqual(
                NOMBRE_DESCARGA_GLAMOUR,
                "Glamour Liquidaciones V1.xlsx",
            )


if __name__ == "__main__":
    unittest.main()
