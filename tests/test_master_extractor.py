from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import SimpleTestCase

from services.master.extractor import (
    clasificar_producto,
    extraer_liquidacion_master,
)
from services.master.processor import preparar_procesamiento_master


class ExtraccionMasterTests(SimpleTestCase):
    def test_clasificar_producto(self):
        self.assertEqual(
            clasificar_producto("Pina Exportacion"),
            ("VERDE", "VERDE"),
        )
        self.assertEqual(
            clasificar_producto("Pina Exportacion Especial"),
            ("ESPECIAL", "ESPECIAL"),
        )
        self.assertEqual(
            clasificar_producto("Pina Exportacion Vertical"),
            ("VERTICAL", "ESPECIAL"),
        )

    def test_extraer_pdf_semana_20_si_existe(self):
        root = next(
            p
            for p in Path(
                r"c:\Users\aobando\Fruta Internacional"
            ).iterdir()
            if p.is_dir()
        )
        carpeta = (
            root
            / "Clientes Liquidaciones PDF"
            / "Master Fruits"
            / "2026"
            / "Semana 20"
        )
        pdfs = list(carpeta.glob("*.pdf")) if carpeta.exists() else []
        if not pdfs:
            self.skipTest("PDF semana 20 no disponible")

        liquidacion = extraer_liquidacion_master(pdfs[0])
        self.assertEqual(liquidacion.factura_corta, "5491")
        self.assertEqual(liquidacion.total_boxes, 9880)
        self.assertEqual(len(liquidacion.productos), 6)
        self.assertEqual(
            liquidacion.comision_eur,
            Decimal("11378.34"),
        )
        for producto in liquidacion.productos:
            esperado = (
                producto.sale_value_eur
                / Decimal(producto.sold_boxes)
            )
            self.assertEqual(
                producto.precio_eur,
                esperado,
            )
        precios = {
            (p.variante, p.calibre): p.precio_eur
            for p in liquidacion.productos
        }
        self.assertEqual(
            precios[("VERDE", 5)],
            Decimal("23004.80") / Decimal(2240),
        )
        self.assertEqual(
            precios[("VERTICAL", 6)],
            Decimal("5926.20") / Decimal(420),
        )


class ProcessorMasterTests(SimpleTestCase):
    def test_preparar_con_despachos_si_existen(self):
        root = next(
            p
            for p in Path(
                r"c:\Users\aobando\Fruta Internacional"
            ).iterdir()
            if p.is_dir()
        )
        pdf_dir = (
            root
            / "Clientes Liquidaciones PDF"
            / "Master Fruits"
            / "2026"
            / "Semana 20"
        )
        pdfs = list(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []
        desp = Path(
            r"C:\Users\aobando\AppData\Local\Temp\despachos_copy.xlsx"
        )
        if not pdfs or not desp.exists():
            self.skipTest("Datos reales no disponibles")

        resultado = preparar_procesamiento_master(
            pdfs[0],
            desp,
        )
        self.assertEqual(resultado.estado, "listo")
        self.assertTrue(resultado.puede_escribir)
        self.assertEqual(
            len(resultado.validacion.lineas_preparadas),
            23,
        )
        self.assertEqual(
            resultado.validacion.total_cajas_liquidacion,
            9880,
        )
        self.assertEqual(
            resultado.validacion.total_cajas_despachos,
            9880,
        )
        self.assertEqual(resultado.destino_final, "SETUBAL")
