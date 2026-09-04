from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from services.nufri.extractor import (
    es_caja_vertical_despacho,
    es_caja_vertical_pdf,
    extraer_liquidacion_nufri,
    parsear_numero,
)
from services.nufri.matcher import CLIENTE_NUFRI


class NufriExtractorTests(unittest.TestCase):
    def test_cliente_constante(self):
        self.assertEqual(CLIENTE_NUFRI, "NUFRI")

    def test_parsear_numero_eu(self):
        self.assertEqual(parsear_numero("13.773,52"), Decimal("13773.52"))

    def test_extrae_pagina_pdf_ejemplo(self):
        pdf = Path(
            r"c:\Users\aobando\Fruta Internacional\GONZÁLES, César "
            r"(TDV) - Liquidaciones TDV\Clientes Liquidaciones PDF"
            r"\NUFRI\2026\Liquidaciones Tropicales "
            r"478689-482206 modificadas.pdf"
        )
        if not pdf.is_file():
            self.skipTest("PDF de prueba no disponible")
        liquidacion = extraer_liquidacion_nufri(pdf, pagina_pdf=1)
        self.assertEqual(liquidacion.pagina_pdf, 1)
        self.assertGreater(liquidacion.total_cajas, 0)
        self.assertGreater(liquidacion.total_venta_eur, Decimal("0"))
        self.assertEqual(len(liquidacion.productos), 5)
        verticales = [p for p in liquidacion.productos if p.es_vertical]
        no_vert = [p for p in liquidacion.productos if not p.es_vertical]
        self.assertEqual(len(verticales), 2)
        self.assertEqual(
            {(p.calibre, p.bultos) for p in verticales},
            {(6, 300), (5, 300)},
        )
        self.assertEqual(
            {(p.calibre, p.bultos) for p in no_vert},
            {(6, 2000), (7, 320), (5, 1800)},
        )
        cal5_no_vert = next(
            p
            for p in no_vert
            if p.calibre == 5 and p.bultos == 1800
        )
        self.assertEqual(cal5_no_vert.importe_eur, Decimal("12340.03"))
        self.assertGreater(
            liquidacion.gastos.get("Inland", Decimal("0")),
            Decimal("0"),
        )

    def test_detecta_caja_vertical(self):
        self.assertTrue(es_caja_vertical_pdf("Env:CRV 5PIEZAS"))
        self.assertTrue(es_caja_vertical_pdf("Env.CRV"))
        self.assertFalse(es_caja_vertical_pdf("Env:036 5PIEZAS"))
        self.assertTrue(
            es_caja_vertical_despacho("VERTICAL SUPER SWEET")
        )
        self.assertFalse(
            es_caja_vertical_despacho("SUPER SWEET ALTA")
        )

    def test_extrae_comision_pagina_2(self):
        pdf = Path(
            r"c:\Users\aobando\Fruta Internacional\GONZÁLES, César "
            r"(TDV) - Liquidaciones TDV\Clientes Liquidaciones PDF"
            r"\NUFRI\2026\Liquidaciones Tropicales "
            r"478689-482206 modificadas.pdf"
        )
        if not pdf.is_file():
            self.skipTest("PDF de prueba no disponible")
        liquidacion = extraer_liquidacion_nufri(pdf, pagina_pdf=2)
        self.assertEqual(liquidacion.comision_pct, Decimal("8"))
        self.assertEqual(
            liquidacion.comision_eur,
            Decimal("2633.36"),
        )

    def test_gastos_ocr_separados_pagina_8(self):
        """Etiquetas y montos en líneas distintas (OCR)."""
        pdf = Path(
            r"c:\Users\aobando\Fruta Internacional\GONZÁLES, César "
            r"(TDV) - Liquidaciones TDV\Clientes Liquidaciones PDF"
            r"\NUFRI\2026\Liquidaciones Tropicales "
            r"478689-482206 modificadas.pdf"
        )
        if not pdf.is_file():
            self.skipTest("PDF de prueba no disponible")
        liquidacion = extraer_liquidacion_nufri(pdf, pagina_pdf=8)
        self.assertEqual(
            liquidacion.gastos["Inland"],
            Decimal("3124.55"),
        )
        self.assertEqual(
            liquidacion.gastos["shipping line"],
            Decimal("3105.01"),
        )
        self.assertEqual(
            liquidacion.gastos["Demurage"],
            Decimal("4444.00"),
        )
        self.assertEqual(
            liquidacion.gastos["Costoms & clear"],
            Decimal("877.83"),
        )


def _despacho(
    *,
    calibre: int,
    cajas: int,
    carton: str,
    fila: int = 1,
):
    from services.nufri.matcher import LineaDespachoNufri

    return LineaDespachoNufri(
        fila_excel=fila,
        semana=24,
        anio=2026,
        semana_texto="24-2026",
        contenedor="SEKU9089420",
        cliente="NUFRI",
        barco="HARMONY",
        puerto_destino="ALGECIRAS",
        tipo_empaque="ESPECIAL",
        carton=carton,
        calibre=calibre,
        total_cajas=cajas,
        factura="5593",
        factura_corta="5593",
    )


class NufriValidatorTests(unittest.TestCase):
    def test_precio_unitario_por_grupo_calibre_y_vertical(self):
        from services.nufri.extractor import (
            COLUMNAS_GASTO,
            LineaProductoNufri,
            LiquidacionNufri,
        )
        from services.nufri.matcher import ResultadoMatcherNufri
        from services.nufri.validator import validar_liquidacion_nufri

        liq = LiquidacionNufri(
            archivo="x.pdf",
            pagina_pdf=1,
            nave="HARMONY",
            destino_pdf="ALGECIRAS",
            contenedores=("SEKU9089420",),
            productos=(
                LineaProductoNufri(
                    calibre=5,
                    bultos=1800,
                    importe_eur=Decimal("12340.03"),
                    descripcion="Env:036 5PIEZAS",
                    es_vertical=False,
                ),
                LineaProductoNufri(
                    calibre=5,
                    bultos=300,
                    importe_eur=Decimal("3311.48"),
                    descripcion="Env:CRV 5PIEZAS",
                    es_vertical=True,
                ),
                LineaProductoNufri(
                    calibre=6,
                    bultos=2000,
                    importe_eur=Decimal("13773.52"),
                    descripcion="Env:036 6PIEZAS",
                    es_vertical=False,
                ),
            ),
            gastos={col: Decimal("0") for col in COLUMNAS_GASTO},
            comision_pct=None,
            comision_eur=Decimal("0"),
            rubros_mapeados=(),
            rubros_no_mapeados=(),
            total_cajas=4100,
            total_venta_eur=Decimal("29425.03"),
            total_neto_eur=None,
        )
        lineas = (
            _despacho(
                calibre=5,
                cajas=825,
                carton="SUPER SWEET ALTA",
                fila=1,
            ),
            _despacho(
                calibre=5,
                cajas=375,
                carton="SUPER SWEET ALTA",
                fila=2,
            ),
            _despacho(
                calibre=5,
                cajas=600,
                carton="SUPER SWEET",
                fila=3,
            ),
            _despacho(
                calibre=5,
                cajas=240,
                carton="VERTICAL SUPER SWEET",
                fila=4,
            ),
            _despacho(
                calibre=5,
                cajas=60,
                carton="VERTICAL SUPER SWEET",
                fila=5,
            ),
            _despacho(
                calibre=6,
                cajas=2000,
                carton="SUPER SWEET",
                fila=6,
            ),
        )
        desp = ResultadoMatcherNufri(
            archivo="d.xlsx",
            hoja="Base Datos",
            cliente_buscado="NUFRI",
            factura_corta_buscada="5593",
            semana=24,
            anio=2026,
            destino_buscado="ALGECIRAS",
            semana_texto="24-2026",
            lineas=lineas,
            total_cajas=4100,
            contenedores=("SEKU9089420",),
            destinos=("ALGECIRAS",),
        )
        resultado = validar_liquidacion_nufri(
            liq,
            desp,
            destino_ui="ALGECIRAS",
            semana_ui=24,
            anio_ui=2026,
        )
        self.assertTrue(resultado.es_valido)
        # 12340.03/1800 ; 3311.48/300 ; 13773.52/2000 (sin redondear)
        esperado_5 = Decimal("12340.03") / Decimal("1800")
        esperado_5v = Decimal("3311.48") / Decimal("300")
        esperado_6 = Decimal("13773.52") / Decimal("2000")
        no_vert_5 = [
            ln.precio_venta_eur
            for ln in resultado.lineas_preparadas
            if ln.calibre == 5
            and "VERTICAL" not in ln.despacho.carton
        ]
        vert_5 = [
            ln.precio_venta_eur
            for ln in resultado.lineas_preparadas
            if ln.calibre == 5 and "VERTICAL" in ln.despacho.carton
        ]
        no_vert_6 = [
            ln.precio_venta_eur
            for ln in resultado.lineas_preparadas
            if ln.calibre == 6
        ]
        self.assertEqual(no_vert_5, [esperado_5] * 3)
        self.assertEqual(vert_5, [esperado_5v] * 2)
        self.assertEqual(no_vert_6, [esperado_6])


if __name__ == "__main__":
    unittest.main()
