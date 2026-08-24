from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from procesamientos.services.generacion_fruver import (
    reconstruir_resultado_para_escritura_fruver,
)
from procesamientos.services.cuadre_fruver import (
    enriquecer_resumen_cuadre,
    ventas_calc_desde_lineas,
)
from services.fruver.extractor import (
    extraer_liquidacion_fruver,
    parsear_numero,
    parsear_texto_liquidacion_fruver,
)
from services.fruver.matcher import (
    CLIENTE_FRUVER,
    LineaDespachoFruver,
    ResultadoMatcherFruver,
    buscar_lineas_despachos_fruver,
)
from services.fruver.validator import validar_liquidaciones_fruver
from services.fruver.writer import construir_valores_fila_fruver


TEXTO_MUESTRA = """
LIQUIDACIÓN
261022698
EMISOR DE LA FACTURA
TROPICALES DEL VALLE S.A.
Cia. Fru&Ver Madrid S.L.
FECHA Nº PROV. Nº ALBARÁN PROV. Nº FACTURA MONEDA Cambio
29/05/2026 4610 130855 00400001090000005503 Euro 1,0000
OBSERVACIONES
SZLU9841813 - ETA 04/06 ALG
BLT. MERCANCÍA T.P. Kgs. brutos Tara Kgs. netos Precio Importe
1.200 PIÑA COLOR EXTRA Cat1 5 B 14.400,00 12,00000 14.400,00
375 PIÑA COLOR EXTRA Cat1 6 B 4.500,00 12,00000 4.500,00
T. Bultos: 1575 T. Neto: 18.900,00 Total en Divisa: 18.900,00
Comisión: 8% 1.512,00 G. Merc.: 0,00
Mercancía: 18.900,00
Dtos.: 1.512,00
T. Factura: 15.543,06
Flete: 0,00 Gastos Puerto: 425,00
Total gastos: 1.844,94
Demora Puerto: 0,00 Aduanas: 506,36
Portes: 811,36 Otros: 102,22 TOTAL A PAGAR € 15.543,06
"""

TEXTO_CON_FLETE = TEXTO_MUESTRA.replace(
    "Flete: 0,00",
    "Flete: 250,00",
)

TEXTO_FACTURA_CORTA = TEXTO_MUESTRA.replace(
    "00400001090000005503",
    "5503",
)

TEXTO_COMISION_10 = """
LIQUIDACIÓN
261022700
FECHA Nº PROV. Nº ALBARÁN PROV. Nº FACTURA MONEDA Cambio
29/05/2026 4610 130860 00400001090000005510 Euro 1,0000
SZLU9841999 - ETA 04/06 ALG
1.575 PIÑA COLOR EXTRA Cat1 5 B 18.900,00 12,00000 18.900,00
T. Neto: 18.900,00
COMISIÓN: 10% 1.890,00
Demora Puerto: 10,00 Aduanas: 50,00
Portes: 100,00 Otros: 0,00
Gastos Puerto: 200,00
Total gastos: 360,00
"""


def _linea_despacho(**kwargs) -> LineaDespachoFruver:
    base = dict(
        fila_excel=2,
        semana=22,
        anio=2026,
        semana_texto="22-2026",
        contenedor="SZLU9841813",
        cliente="FRU&VER",
        barco="NAVE X",
        puerto_destino="ALGECIRAS",
        tipo_empaque="ESPECIAL",
        carton="ESPECIAL",
        calibre=5,
        total_cajas=1200,
        factura="5503",
        factura_corta="5503",
    )
    base.update(kwargs)
    return LineaDespachoFruver(**base)


def _despachos(
    lineas: tuple[LineaDespachoFruver, ...],
) -> ResultadoMatcherFruver:
    return ResultadoMatcherFruver(
        archivo="despachos.xlsx",
        hoja="Base Datos",
        cliente_buscado=CLIENTE_FRUVER,
        semana=22,
        anio=2026,
        destino_buscado="ALGECIRAS",
        factura_corta_buscada="5503",
        semana_texto="22-2026",
        lineas=lineas,
        total_cajas=sum(ln.total_cajas for ln in lineas),
        contenedores=tuple(
            dict.fromkeys(ln.contenedor for ln in lineas)
        ),
        destinos=("ALGECIRAS",),
        naves=("NAVE X",),
    )


class ParseoFruverTests(unittest.TestCase):
    def test_parsear_numero_eu(self):
        self.assertEqual(parsear_numero("1.200"), Decimal("1200"))
        self.assertEqual(parsear_numero("12,00000"), Decimal("12.00000"))
        self.assertEqual(parsear_numero("1.844,94"), Decimal("1844.94"))

    def test_parsea_texto_muestra(self):
        liq = parsear_texto_liquidacion_fruver(TEXTO_MUESTRA)
        self.assertEqual(liq.contenedor, "SZLU9841813")
        self.assertEqual(liq.factura_corta, "5503")
        self.assertEqual(liq.total_cajas, Decimal("1575"))
        self.assertEqual(len(liq.productos), 2)
        self.assertEqual(liq.productos[0].calibre, 5)
        self.assertEqual(liq.productos[0].cajas, Decimal("1200"))
        self.assertEqual(liq.productos[0].precio_eur, Decimal("12"))
        self.assertEqual(liq.productos[1].calibre, 6)
        self.assertEqual(liq.productos[1].cajas, Decimal("375"))
        self.assertEqual(liq.comision, Decimal("1512.00"))
        self.assertEqual(liq.flete_eur, Decimal("0"))
        self.assertEqual(liq.gastos["Demora Eur"], Decimal("0"))
        self.assertEqual(liq.gastos["Portes Eur"], Decimal("811.36"))
        self.assertEqual(
            liq.gastos["Gasto Puerto eur"],
            Decimal("425.00"),
        )
        self.assertEqual(liq.gastos["Aduanas Eur"], Decimal("506.36"))
        self.assertEqual(liq.gastos["Otros3"], Decimal("102.22"))
        self.assertEqual(liq.total_venta_eur, Decimal("18900.00"))
        self.assertEqual(liq.total_gastos_eur, Decimal("1844.94"))

    def test_factura_corta_de_cuatro_digitos(self):
        liq = parsear_texto_liquidacion_fruver(TEXTO_FACTURA_CORTA)
        self.assertEqual(liq.factura, "5503")
        self.assertEqual(liq.factura_corta, "5503")

    def test_flete_positivo_no_va_a_otros3(self):
        liq = parsear_texto_liquidacion_fruver(TEXTO_CON_FLETE)
        self.assertEqual(liq.flete_eur, Decimal("250.00"))
        self.assertEqual(liq.gastos["Otros3"], Decimal("102.22"))

    def test_comision_diez_por_ciento(self):
        liq = parsear_texto_liquidacion_fruver(TEXTO_COMISION_10)
        self.assertEqual(liq.comision, Decimal("1890.00"))
        self.assertEqual(liq.contenedor, "SZLU9841999")
        self.assertEqual(liq.gastos["Demora Eur"], Decimal("10.00"))


PDF_SEMANA_21 = Path(
    r"c:\Users\aobando\Fruta Internacional\GONZÁLES, César (TDV) "
    r"- Liquidaciones TDV\Clientes Liquidaciones PDF\FRU&VER"
    r"\2026\Semana 21"
)


class ExtraccionPdfRealFruverTests(unittest.TestCase):
    def test_extrae_pdfs_semana_21(self):
        pdfs = sorted(PDF_SEMANA_21.glob("*.pdf")) if PDF_SEMANA_21.is_dir() else []
        if not pdfs:
            self.skipTest("PDFs Semana 21 no disponibles")
        for ruta in pdfs:
            with self.subTest(archivo=ruta.name):
                liq = extraer_liquidacion_fruver(ruta)
                self.assertEqual(liq.factura_corta, "5503")
                self.assertEqual(liq.total_cajas, Decimal("1575"))
                self.assertEqual(liq.comision, Decimal("1512.00"))
                self.assertEqual(liq.gastos["Portes Eur"], Decimal("811.36"))
                self.assertEqual(
                    liq.gastos["Gasto Puerto eur"],
                    Decimal("425.00"),
                )
                self.assertEqual(liq.gastos["Aduanas Eur"], Decimal("506.36"))
                self.assertEqual(liq.gastos["Otros3"], Decimal("102.22"))
                self.assertEqual(liq.flete_eur, Decimal("0"))


class ValidacionFruverTests(unittest.TestCase):
    def setUp(self):
        self.liq = parsear_texto_liquidacion_fruver(TEXTO_MUESTRA)
        self.despachos = _despachos(
            (
                _linea_despacho(calibre=5, total_cajas=1200),
                _linea_despacho(
                    fila_excel=3,
                    calibre=6,
                    total_cajas=375,
                ),
            )
        )

    def test_gastos_y_comision_en_todas_las_lineas(self):
        resultado = validar_liquidaciones_fruver(
            (self.liq,),
            self.despachos,
            "ALG",
            "5503",
        )
        self.assertTrue(resultado.es_valido)
        self.assertEqual(len(resultado.lineas_preparadas), 2)
        for linea in resultado.lineas_preparadas:
            self.assertEqual(linea.tipo_fruta, "Especial")
            self.assertEqual(linea.comision, Decimal("1512.00"))
            self.assertEqual(linea.portes_eur, Decimal("811.36"))
            self.assertEqual(
                linea.gasto_puerto_eur,
                Decimal("425.00"),
            )
            self.assertEqual(linea.aduanas_eur, Decimal("506.36"))
            self.assertEqual(linea.otros3, Decimal("102.22"))
            self.assertEqual(linea.precio_venta_eur, Decimal("12"))
            self.assertEqual(linea.destino, "Algeciras")
        resumen = resultado.resumen_gastos_contenedores[0]
        self.assertTrue(resumen.venta_cuadra)
        self.assertTrue(resumen.gastos_cuadran)
        self.assertEqual(resumen.total_venta_calc, Decimal("18900.00"))
        self.assertEqual(resumen.total_venta_pdf, Decimal("18900.00"))
        self.assertEqual(resumen.total_gastos_calc, Decimal("1844.94"))
        self.assertEqual(resumen.total_gastos_pdf, Decimal("1844.94"))

    def test_lineas_salen_de_despachos(self):
        resultado = validar_liquidaciones_fruver(
            (self.liq,),
            self.despachos,
            "ALGECIRAS",
            "5503",
        )
        calibres = [ln.calibre for ln in resultado.lineas_preparadas]
        self.assertEqual(calibres, [5, 6])
        cajas = [ln.total_cajas for ln in resultado.lineas_preparadas]
        self.assertEqual(cajas, [Decimal("1200"), Decimal("375")])

    def test_cajas_distintas_es_error(self):
        despachos = _despachos(
            (
                _linea_despacho(calibre=5, total_cajas=1200),
                _linea_despacho(
                    fila_excel=3,
                    calibre=6,
                    total_cajas=400,
                ),
            )
        )
        resultado = validar_liquidaciones_fruver(
            (self.liq,),
            despachos,
            "ALGECIRAS",
            "5503",
        )
        self.assertFalse(resultado.es_valido)
        codigos = {e.codigo for e in resultado.errores}
        self.assertIn("CAJAS_NO_COINCIDEN", codigos)
        self.assertIn("CAJAS_CALIBRE_NO_COINCIDEN", codigos)

    def test_flete_mayor_a_cero_advertencia(self):
        liq = parsear_texto_liquidacion_fruver(TEXTO_CON_FLETE)
        resultado = validar_liquidaciones_fruver(
            (liq,),
            self.despachos,
            "ALGECIRAS",
            "5503",
        )
        avisos = {a.codigo for a in resultado.advertencias}
        self.assertIn("FLETE_CON_MONTO", avisos)
        for linea in resultado.lineas_preparadas:
            self.assertEqual(linea.otros3, Decimal("102.22"))

    def test_tipo_no_especial_advertencia_pero_se_digita_especial(self):
        despachos = _despachos(
            (
                _linea_despacho(
                    tipo_empaque="VERDE",
                    carton="VERDE",
                    calibre=5,
                    total_cajas=1200,
                ),
                _linea_despacho(
                    fila_excel=3,
                    tipo_empaque="CROWNLESS",
                    carton="CROWNLESS",
                    calibre=6,
                    total_cajas=375,
                ),
            )
        )
        resultado = validar_liquidaciones_fruver(
            (self.liq,),
            despachos,
            "ALGECIRAS",
            "5503",
        )
        avisos = [
            a for a in resultado.advertencias
            if a.codigo == "TIPO_NO_ESPECIAL"
        ]
        self.assertEqual(len(avisos), 2)
        self.assertTrue(
            all(
                ln.tipo_fruta == "Especial"
                for ln in resultado.lineas_preparadas
            )
        )

    def test_factura_pdf_distinta_es_error(self):
        resultado = validar_liquidaciones_fruver(
            (self.liq,),
            self.despachos,
            "ALGECIRAS",
            "9999",
        )
        self.assertFalse(resultado.es_valido)
        self.assertIn(
            "FACTURA_PDF_NO_COINCIDE",
            {e.codigo for e in resultado.errores},
        )


class MatcherFruverTests(unittest.TestCase):
    def test_busca_lineas_fruver(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "despachos.xlsx"
            libro = Workbook()
            hoja = libro.active
            hoja.title = "Base Datos"
            hoja.append(
                [
                    "SEMANA",
                    "AÑO",
                    "CONTENEDOR",
                    "CLIENTE",
                    "BARCO",
                    "PUERTO DESTINO",
                    "TIPO EMPAQUE",
                    "CARTON",
                    "CALIBRE",
                    "TOTAL CAJAS",
                    "FACTURA",
                ]
            )
            hoja.append(
                [
                    22,
                    2026,
                    "SZLU9841813",
                    "FRU&VER",
                    "NAVE X",
                    "ALG",
                    "ESPECIAL",
                    "ESPECIAL",
                    5,
                    1200,
                    "5503",
                ]
            )
            hoja.append(
                [
                    22,
                    2026,
                    "SZLU9841999",
                    "FRU&VER",
                    "NAVE X",
                    "ALG",
                    "ESPECIAL",
                    "ESPECIAL",
                    5,
                    80,
                    "9999",
                ]
            )
            hoja.append(
                [
                    22,
                    2026,
                    "OTRO1234567",
                    "OTRO CLIENTE",
                    "NAVE Y",
                    "ALG",
                    "ESPECIAL",
                    "ESPECIAL",
                    5,
                    80,
                    "1111",
                ]
            )
            libro.save(ruta)
            resultado = buscar_lineas_despachos_fruver(
                ruta,
                semana=22,
                anio=2026,
                destino="ALGECIRAS",
                factura_corta="5503",
            )
            self.assertEqual(len(resultado.lineas), 1)
            self.assertEqual(
                resultado.lineas[0].contenedor,
                "SZLU9841813",
            )
            self.assertEqual(resultado.total_cajas, 1200)


class ReconstruccionFruverTests(unittest.TestCase):
    def test_reconstruye_y_arma_fila(self):
        resultado = reconstruir_resultado_para_escritura_fruver(
            anio=2026,
            semana=22,
            destino_ui="Algeciras",
            lineas_preparadas=[
                {
                    "semana": 22,
                    "anio": 2026,
                    "semana_texto": "22-2026",
                    "cliente": "FRU&VER",
                    "nave": "NAVE X",
                    "contenedor": "SZLU9841813",
                    "destino": "Algeciras",
                    "tipo_fruta": "Especial",
                    "calibre": 5,
                    "total_cajas": "1200",
                    "carton": "ESPECIAL",
                    "demora_eur": "0",
                    "portes_eur": "811.36",
                    "gasto_puerto_eur": "425",
                    "aduanas_eur": "506.36",
                    "otros3": "102.22",
                    "comision": "1512",
                    "precio_venta_eur": "12",
                }
            ],
        )
        self.assertTrue(resultado.puede_escribir)
        valores = construir_valores_fila_fruver(resultado, 0)
        self.assertEqual(valores["Tipo de fruta"], "Especial")
        self.assertEqual(valores["Cliente"], "FRU&VER")
        self.assertEqual(valores["Destino"], "Algeciras")
        self.assertEqual(valores["Comisión"], 1512.0)
        self.assertEqual(valores["Portes Eur"], 811.36)


class CuadreFruverTests(unittest.TestCase):
    def test_venta_calc_desde_lineas(self):
        lineas = [
            {
                "contenedor": "SZLU9841813",
                "total_cajas": "1200",
                "precio_venta_eur": "12",
            },
            {
                "contenedor": "SZLU9841813",
                "total_cajas": "375",
                "precio_venta_eur": "12",
            },
        ]
        ventas = ventas_calc_desde_lineas(lineas)
        self.assertEqual(
            ventas["SZLU9841813"],
            Decimal("18900"),
        )

    def test_enriquecer_completa_gastos_pdf(self):
        resumen = [
            {
                "contenedor": "SZLU9841813",
                "gastos": {
                    "Demora Eur": "0",
                    "Portes Eur": "811.36",
                    "Gasto Puerto eur": "425",
                    "Aduanas Eur": "506.36",
                    "Otros3": "102.22",
                },
                "total_venta_calc": "18900",
                "total_venta_pdf": "18900",
                "total_gastos_pdf": "1844.94",
                "flete_eur": "0",
            }
        ]
        salida = enriquecer_resumen_cuadre(resumen)
        self.assertTrue(salida[0]["venta_cuadra"])
        self.assertTrue(salida[0]["gastos_cuadran"])
        self.assertEqual(salida[0]["total_gastos_calc"], "1844.94")
        self.assertTrue(salida[0]["tiene_gastos_pdf"])
