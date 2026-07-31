from __future__ import annotations

from decimal import Decimal
from unittest import TestCase

from services.orsero.extractor import (
    FormatoLiquidacionOrseroError,
    parsear_texto_liquidacion_orsero,
)
from services.orsero.validator import validar_liquidacion_orsero
from services.orsero.matcher import (
    LineaDespachoOrsero,
    ResultadoMatcherOrsero,
)
from procesamientos.services.generacion_orsero import (
    reconstruir_resultado_para_escritura_orsero,
)


TEXTO_OCR_MUESTRA = """
Cala Pino v. 24

Setubal pit caja/plt total cajas | ventas €/ caja | facturacion
cal 5 12 70 840 12,50 10.500,00 €
cal 6 28 70 1.960 14,00 27.440,00 €
cal 7 2 70 140 14,00 1.960,00 €
total 42 2.940 13,57 39.900,00
Vado pit caja/plt total cajas | ventas €/ caja | facturacion
cal 6 21 65 1.365 16,25 22.181,25 €
cal 7 21 65 1.365 16,25 22.181,25 €
total 42 2.730 16,25 44.362,50
grand total 84 5.670 84.262,50 €
cambio $/€: 1,1594

Descripción del costo Valor € Valor $
Venta 84.262,50€ $ 97.693,94
Costos de Origen $ $ 567,00
Inland $ $ 2.300,00
THC Origen $ $ 1.922,33
Flete + BAF + ETS $ $ 17.916,30
Insurance 226,80€ $ 262,95
THC destino 1.559,92€ $ 1.808,57
Forwarding 538,78€ $ 624,66
Transport IN+OUT 6.520,50€ $ 7.559,87
Comisión 8% $ 7.815,52
Costos totales $ 40.777,20
"""


class ExtraccionOrseroTests(TestCase):
    def test_parsea_semana_cambio_precios_y_gastos(self):
        liq = parsear_texto_liquidacion_orsero(TEXTO_OCR_MUESTRA)
        self.assertEqual(liq.semana, 24)
        self.assertEqual(liq.nave_texto.upper(), "CALA PINO")
        self.assertEqual(
            liq.tipo_cambio_usd_eur,
            Decimal("1.1594"),
        )
        self.assertEqual(len(liq.precios), 5)
        self.assertEqual(liq.precios[0].destino, "SETUBAL")
        self.assertEqual(liq.precios[0].calibre, 5)
        self.assertEqual(
            liq.precios[0].precio_eur,
            Decimal("12.50"),
        )
        self.assertEqual(liq.precios[0].total_cajas, 840)
        self.assertEqual(
            liq.gastos["Costo en Origen Form."],
            Decimal("567.00"),
        )
        self.assertEqual(
            liq.gastos["Comision Form"],
            Decimal("7815.52"),
        )
        self.assertEqual(liq.total_cajas, 5670)

    def test_falla_sin_semana(self):
        with self.assertRaises(FormatoLiquidacionOrseroError):
            parsear_texto_liquidacion_orsero(
                "cambio $/€: 1.08\nSetubal\ncal 6 100 14"
            )


class ValidacionOrseroTests(TestCase):
    def _despacho(self, destino, calibre, cajas, fila=1):
        return LineaDespachoOrsero(
            fila_excel=fila,
            semana=24,
            anio=2026,
            semana_texto="24-2026",
            contenedor=f"CONT{fila}",
            cliente="ORSERO",
            barco="CALA PINO",
            puerto_destino=destino,
            tipo_empaque="ESPECIAL",
            carton="ESPECIAL",
            calibre=calibre,
            total_cajas=cajas,
        )

    def test_precio_faltante_es_advertencia(self):
        liq = parsear_texto_liquidacion_orsero(TEXTO_OCR_MUESTRA)
        desp = ResultadoMatcherOrsero(
            archivo="d.xlsx",
            hoja="Base Datos",
            cliente_buscado="ORSERO",
            semana=24,
            anio=2026,
            semana_texto="24-2026",
            lineas=(
                self._despacho("SETUBAL", 5, 840, 1),
                self._despacho("GENOVA", 9, 100, 2),
            ),
            total_cajas=940,
            contenedores=("CONT1", "CONT2"),
            destinos=("SETUBAL", "GENOVA"),
        )
        resultado = validar_liquidacion_orsero(liq, desp)
        self.assertTrue(resultado.es_valido)
        codigos = {
            a.codigo for a in resultado.advertencias
        }
        self.assertIn("PRECIO_NO_ENCONTRADO", codigos)
        self.assertIn("TOTAL_CAJAS_DIFERENTE", codigos)
        self.assertFalse(
            resultado.lineas_preparadas[1].precio_encontrado
        )


class ReconstruccionOrseroTests(TestCase):
    def test_reconstruye_sin_ocr(self):
        gastos = {
            "Costo en Origen Form.": "2300",
            "Inland Form.": "450",
            "THC Origen Form.": "320",
            "Flete Form.": "5100",
            "Insurance Form.": "210",
            "THC Destino Form.": "180",
            "Forwarding Form.": "95",
            "Transport In Form.": "640",
            "Comision Form": "7815.52",
        }
        resultado = reconstruir_resultado_para_escritura_orsero(
            anio=2026,
            semana=24,
            nave_texto="Cala Pino",
            tipo_cambio=Decimal("1.085"),
            lineas_preparadas=[
                {
                    "contenedor": "MSCU123",
                    "nave": "CALA PINO",
                    "destino": "SETUBAL",
                    "tipo_fruta": "ESPECIAL",
                    "carton": "ESPECIAL",
                    "calibre": 6,
                    "total_cajas": 100,
                    "precio_venta_eur": "14",
                    "precio_encontrado": True,
                }
            ],
            gastos_aplicados=gastos,
        )
        self.assertTrue(resultado.puede_escribir)
        linea = resultado.validacion.lineas_preparadas[0]
        self.assertEqual(linea.destino, "SETUBAL")
        self.assertEqual(
            linea.gastos["Comision Form"],
            Decimal("7815.52"),
        )
