from __future__ import annotations

import unittest
from decimal import Decimal

from services.eurobanan.extractor import (
    clasificar_familia_producto,
    familia_desde_despacho,
    parsear_numero,
    tipo_fruta_digitada,
)
from services.eurobanan.matcher import (
    CLIENTE_EUROBANAN,
    LineaDespachoEurobanan,
)
from services.eurobanan.validator import validar_liquidacion_eurobanan
from services.eurobanan.extractor import (
    COLUMNAS_GASTO,
    LiquidacionEurobanan,
    LineaProductoEurobanan,
)
from services.eurobanan.matcher import ResultadoMatcherEurobanan


def _linea_despacho(**kwargs) -> LineaDespachoEurobanan:
    base = dict(
        fila_excel=2,
        semana=26,
        anio=2026,
        semana_texto="26-2026",
        contenedor="TCLU1297340",
        cliente=CLIENTE_EUROBANAN,
        barco="NAVE X",
        puerto_destino="ALGECIRAS",
        tipo_empaque="ESPECIAL",
        carton="SUMMUM SELECT",
        calibre=6,
        total_cajas=720,
        factura="5648",
        factura_corta="5648",
    )
    base.update(kwargs)
    return LineaDespachoEurobanan(**base)


class EurobananClasificacionTests(unittest.TestCase):
    def test_familia_vertical_es_alta(self):
        self.assertEqual(
            familia_desde_despacho(
                "ESPECIAL",
                "VERTICAL SUMMUM SELECT",
            ),
            "SUMMUM_ALTA",
        )
        self.assertEqual(
            clasificar_familia_producto(
                "PIÑA IBO T.RIPE SUMMUM ALTA CAL.5 CR"
            ),
            "SUMMUM_ALTA",
        )

    def test_carton_summum_alta_es_summum_regular(self):
        self.assertEqual(
            familia_desde_despacho("ESPECIAL", "SUMMUM ALTA"),
            "SUMMUM",
        )
        self.assertEqual(
            familia_desde_despacho("ESPECIAL", "SUMMUM SELECT"),
            "SUMMUM",
        )

    def test_familia_intermedio(self):
        self.assertEqual(
            familia_desde_despacho(
                "INTERMEDIO",
                "ISLA BONITA MARITIMA",
            ),
            "TREE_RIPE",
        )
        self.assertEqual(
            clasificar_familia_producto(
                "PIÑA IBO TREE RIPE CAL.5 CR GG"
            ),
            "TREE_RIPE",
        )

    def test_tipo_fruta_digitada(self):
        self.assertEqual(
            tipo_fruta_digitada("INTERMEDIO"),
            "Intermedio",
        )
        self.assertEqual(
            tipo_fruta_digitada("ESPECIAL"),
            "Especial",
        )

    def test_parsear_numero_eu(self):
        self.assertEqual(parsear_numero("9,70"), Decimal("9.70"))
        self.assertEqual(
            parsear_numero("11.640,00"),
            Decimal("11640.00"),
        )


class EurobananValidacionTests(unittest.TestCase):
    def test_match_precio_por_familia_y_calibre(self):
        liquidacion = LiquidacionEurobanan(
            archivo="test.pdf",
            factura_corta="5648",
            referencia="",
            destino_pdf="ALGECIRAS",
            contenedores=("TCLU1297340",),
            productos=(
                LineaProductoEurobanan(
                    familia="SUMMUM",
                    calibre=6,
                    bultos=1440,
                    precio_eur=Decimal("9.70"),
                    importe_eur=Decimal("13968.00"),
                    descripcion="SUMMUM CAL.6",
                ),
                LineaProductoEurobanan(
                    familia="SUMMUM_ALTA",
                    calibre=6,
                    bultos=300,
                    precio_eur=Decimal("15.15"),
                    importe_eur=Decimal("4545.00"),
                    descripcion="SUMMUM ALTA CAL.6",
                ),
            ),
            gastos={col: Decimal("0") for col in COLUMNAS_GASTO},
            rubros_mapeados=(),
            rubros_no_mapeados=(),
            total_cajas=1740,
            total_venta_eur=Decimal("18513.00"),
            total_suma_pdf=Decimal("18513.00"),
            total_importe_neto_eur=None,
            comision_pct=Decimal("8"),
            comision_eur=Decimal("1481.04"),
        )
        despachos = ResultadoMatcherEurobanan(
            archivo="desp.xlsx",
            hoja="Base Datos",
            cliente_buscado=CLIENTE_EUROBANAN,
            factura_corta_buscada="5648",
            semana=26,
            anio=2026,
            destino_buscado="ALGECIRAS",
            semana_texto="26-2026",
            lineas=(
                _linea_despacho(
                    total_cajas=560,
                    carton="SUMMUM SELECT",
                    calibre=6,
                ),
                _linea_despacho(
                    total_cajas=180,
                    carton="VERTICAL SUMMUM SELECT",
                    calibre=6,
                    contenedor="SEGU9501018",
                ),
            ),
            total_cajas=740,
            contenedores=("TCLU1297340", "SEGU9501018"),
            destinos=("ALGECIRAS",),
        )
        resultado = validar_liquidacion_eurobanan(
            liquidacion,
            despachos,
            destino_ui="ALGECIRAS",
            factura_ui="5648",
            semana_ui=26,
            anio_ui=2026,
        )
        self.assertTrue(resultado.es_valido)
        self.assertEqual(
            resultado.lineas_preparadas[0].precio_venta_eur,
            Decimal("9.70"),
        )
        self.assertEqual(
            resultado.lineas_preparadas[1].precio_venta_eur,
            Decimal("15.15"),
        )
        self.assertEqual(
            resultado.comision_eur,
            Decimal("1481.04"),
        )
        self.assertEqual(
            resultado.lineas_preparadas[0].comision_eur,
            Decimal("1481.04"),
        )


class EurobananExtraccionComisionTests(unittest.TestCase):
    def test_extrae_comision_desde_nota_pdf(self):
        from services.eurobanan.extractor import extraer_liquidacion_eurobanan
        from pathlib import Path

        pdf = Path(
            "media/procesamientos/eurobanan/"
            "4b72d8ff-7a27-423d-8d2f-05b58268a50c/liquidacion.pdf"
        )
        if not pdf.is_file():
            self.skipTest("PDF de prueba no disponible")
        liquidacion = extraer_liquidacion_eurobanan(pdf)
        self.assertEqual(liquidacion.comision_pct, Decimal("8"))
        self.assertEqual(
            liquidacion.comision_eur,
            Decimal("3141.24"),
        )


if __name__ == "__main__":
    unittest.main()
