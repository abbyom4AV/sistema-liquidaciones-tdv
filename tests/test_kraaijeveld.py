from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from services.kraaijeveld.extractor import (
    extraer_liquidacion_kraaijeveld,
    parsear_numero,
    parsear_texto_liquidacion_kraaijeveld,
)
from procesamientos.services.generacion_kraaijeveld import (
    reconstruir_resultado_para_escritura_kraaijeveld,
)


PDF_DIR = Path(
    r"c:\Users\aobando\Fruta Internacional\GONZÁLES, César (TDV) "
    r"- Liquidaciones TDV\Clientes Liquidaciones PDF\Kraaijeveld"
    r"\2026\Semana 24\Vlissingen"
)


TEXTO_MUESTRA = """
Settlement of sales
Container SILU7048489 Currency EUR
External reference 1090000005598
Commission order C0006678
Line no. Description Packing Net HE PriceQuantity HECom % Revenue
10000 Pineapple Super Sweet 5, CR, CAT I, Carton 11,00 9,00 1275 3,0 11.475,00
20000 Pineapple Super Sweet 6, CR, CAT I, Carton 11,00 11,00 80 6,0 880,00
14.755,00
Description Costs/Pallet Costs
Commission -541,05
Logistic handling charges -25,00 -525,00
Import transport from harbour to Kraaijeveld -500,00
Other import costs -84,00
Subtotal 13.104,95
"""


class ExtraccionKraaijeveldTests(TestCase):
    def test_parsea_comision_en_miles_formato_eu(self):
        self.assertEqual(
            parsear_numero("-1.008,00"),
            Decimal("-1008.00"),
        )
        self.assertEqual(
            parsear_numero("1.008,00"),
            Decimal("1008.00"),
        )
        texto = """
Settlement of sales
Container SILU7048489 Currency EUR
External reference 1090000005598
10000 Pineapple Super Sweet 5, CR, CAT I, Carton 11,00 9,00 1275 3,0 11.475,00
Description Costs/Pallet Costs
Commission -1.008,00
Logistic handling charges -25,00 -525,00
Import transport from harbour to Kraaijeveld -500,00
Other import costs -84,00
Subtotal 1,00
"""
        liq = parsear_texto_liquidacion_kraaijeveld(texto)
        self.assertEqual(liq.comision, Decimal("1008.00"))

    def test_mapea_gastos_harbour_y_sea_air(self):
        texto = """
Settlement of sales
Container SILU7048489 Currency EUR
External reference 1090000005598
10000 Pineapple Super Sweet 5, CR, CAT I, Carton 11,00 9,00 1275 3,0 11.475,00
Description Costs/Pallet Costs
Commission -880,80
Logistic handling charges -25,00 -525,00
Import transport from harbour to Kraaijeveld -670,00
Import transport by sea / by air -60,40
Other import costs -84,00
Subtotal 1,00
"""
        liq = parsear_texto_liquidacion_kraaijeveld(texto)
        self.assertEqual(liq.comision, Decimal("880.80"))
        self.assertEqual(liq.gastos["Logistics.C"], Decimal("525.00"))
        self.assertEqual(liq.gastos["Import.T"], Decimal("670.00"))
        self.assertEqual(liq.gastos["Import.T S.A"], Decimal("60.40"))
        self.assertEqual(
            liq.gastos["Other Import C."],
            Decimal("84.00"),
        )

    def test_parsea_texto_muestra(self):
        liq = parsear_texto_liquidacion_kraaijeveld(TEXTO_MUESTRA)
        self.assertEqual(liq.contenedor, "SILU7048489")
        self.assertEqual(liq.factura_corta, "5598")
        self.assertEqual(liq.comision, Decimal("541.05"))
        self.assertEqual(
            liq.gastos["Logistics.C"],
            Decimal("525.00"),
        )
        self.assertEqual(len(liq.productos), 2)
        self.assertEqual(liq.productos[0].calibre, 5)
        self.assertEqual(
            liq.productos[0].precio_eur,
            Decimal("9.00"),
        )

    def test_parsea_crownless_con_precio(self):
        texto = """
Settlement of sales
Container MNBU0053787 Currency EUR
External reference 1090000005615
Commission order C0006689
10000 Pineapples 6, CR, CAT I, Carton 11,00 8,00 80 0,0 640,00
40000 Pineapple Crownless 12, CR, CAT I, 4063651332018, RAF Carton 14,00 11,77 80 0,0 941,46
Description Costs/Pallet Costs
Commission -100,00
Logistic handling charges -25,00 -525,00
Import transport from harbour to Kraaijeveld -500,00
Other import costs -84,00
Subtotal 1,00
"""
        liq = parsear_texto_liquidacion_kraaijeveld(texto)
        crownless = [
            p for p in liq.productos if p.variante == "CROWNLESS"
        ]
        self.assertEqual(len(crownless), 1)
        self.assertEqual(crownless[0].precio_eur, Decimal("11.77"))
        self.assertTrue(liq.tiene_crownless)

    def test_crownless_sin_numero_tambien_extrae_precio(self):
        texto = """
Container MNBU0053787 Currency EUR
External reference 1090000005615
40000 Pineapple Crownless, CR, CAT I, RAF Carton 14,00 11,77 80 0,0 941,46
Description Costs/Pallet Costs
Commission -100,00
Logistic handling charges -525,00
Import transport from harbour to Kraaijeveld -500,00
Other import costs -84,00
Subtotal 1,00
"""
        liq = parsear_texto_liquidacion_kraaijeveld(texto)
        crownless = [
            p for p in liq.productos if p.variante == "CROWNLESS"
        ]
        self.assertEqual(len(crownless), 1)
        self.assertEqual(crownless[0].precio_eur, Decimal("11.77"))



class ReconstruccionKraaijeveldTests(TestCase):
    def test_reconstruye_sin_pdf(self):
        resultado = reconstruir_resultado_para_escritura_kraaijeveld(
            anio=2026,
            semana=24,
            destino_ui="Vlissingen",
            lineas_preparadas=[
                {
                    "contenedor": "SILU7048489",
                    "nave": "NAVE",
                    "destino": "VLISSINGEN",
                    "tipo_fruta": "ESPECIAL",
                    "carton": "ESPECIAL",
                    "calibre": 5,
                    "total_cajas": 100,
                    "es_precio_fijo": False,
                    "precio_venta_eur": "9",
                    "precio_venta_usd": None,
                    "comision": "541.05",
                    "gastos": {
                        "Logistics.C": "525",
                        "Import.T": "500",
                        "Import.T S.A": "0",
                        "Scanning Cost": "0",
                        "Other Import C.": "84",
                        "Demourge Cost": "0",
                        "Export C.S": "0",
                        "Repack Costs": "0",
                    },
                    "precio_encontrado": True,
                    "factura_corta": "5598",
                    "semana_texto": "24-2026",
                    "anio": 2026,
                }
            ],
        )
        self.assertTrue(resultado.puede_escribir)
        linea = resultado.validacion.lineas_preparadas[0]
        self.assertEqual(linea.comision, Decimal("541.05"))
        self.assertEqual(
            linea.gastos["Logistics.C"],
            Decimal("525"),
        )
