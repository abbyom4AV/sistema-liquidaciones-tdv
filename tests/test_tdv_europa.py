from __future__ import annotations

import unittest
from decimal import Decimal

from services.tdv_europa.extractor import (
    LiquidacionTdvEuropa,
    LineaProductoTdvEuropa,
    _parsear_total_general,
    clave_carton,
    limpiar_carton,
    normalizar_destino,
    tipo_fruta_desde,
)
from services.tdv_europa.matcher import ResultadoMatcherTdvEuropa
from services.tdv_europa.validator import (
    LineaPreparadaTdvEuropa,
    atribuir_mermas,
    validar_liquidacion_tdv_europa,
)


def _linea_producto(**kwargs) -> LineaProductoTdvEuropa:
    base = dict(
        contenedor="CGMU5152457",
        cliente="MERCADONA",
        fecha_llegada="01/02/2026",
        tipo_raw="COL",
        tipo_fruta="Especial",
        calibre=6,
        calibre_raw="CAL6",
        carton="CARTON STD",
        carton_clave="CARTON STD",
        cajas_netas=Decimal("100"),
        venta_bruta_eur=Decimal("500"),
        precio_caja_eur=Decimal("5"),
        es_merma=False,
    )
    base.update(kwargs)
    return LineaProductoTdvEuropa(**base)


def _liquidacion_base(**kwargs) -> LiquidacionTdvEuropa:
    base = dict(
        archivo="",
        semana=9,
        anio=2026,
        destino_pdf="AMBERES",
        nave="NAVE X",
        factura_completa="1234",
        factura_corta="1234",
        lineas=(),
        mermas=(),
        gastos={
            "Gasto Puerto": Decimal("10"),
            "Gasto Trans": Decimal("20"),
            "Gasto Handl": Decimal("30"),
            "G.Inspección": Decimal("0"),
            "G.Customs Duties": Decimal("0"),
        },
        comision_eur=Decimal("5"),
        total_cajas_netas=Decimal("100"),
        total_venta_eur=Decimal("500"),
        reclamos=(),
        rubros_no_mapeados=(),
    )
    base.update(kwargs)
    return LiquidacionTdvEuropa(**base)


def _despachos(total_cajas: int = 100) -> ResultadoMatcherTdvEuropa:
    return ResultadoMatcherTdvEuropa(
        archivo="",
        hoja="",
        cliente_buscado="TROPICALES DEL VALLE EUROPA",
        factura_corta_buscada="1234",
        semana=9,
        anio=2026,
        destino_buscado="AMBERES",
        semana_texto="09-2026",
        lineas=(),
        total_cajas=total_cajas,
        contenedores=("CGMU5152457",),
        destinos=("AMBERES",),
        naves=("NAVE X",),
    )


class ParseoTdvEuropaTests(unittest.TestCase):
    def test_tipo_fruta_col_especial(self):
        self.assertEqual(tipo_fruta_desde("COL", "CAL6"), "Especial")

    def test_tipo_fruta_int_intermedio(self):
        self.assertEqual(tipo_fruta_desde("INT", "CAL6"), "Intermedio")

    def test_tipo_fruta_ver_verde(self):
        self.assertEqual(tipo_fruta_desde("VER", "CAL6"), "Verde")

    def test_tipo_fruta_cl_crownless(self):
        self.assertEqual(
            tipo_fruta_desde("COL", "CAL6CL8"),
            "Crownless Especial",
        )

    def test_destino_excel_primera_mayuscula(self):
        from services.tdv_europa.extractor import (
            formatear_destino_excel,
        )

        self.assertEqual(
            formatear_destino_excel("ALGECIRAS"),
            "Algeciras",
        )
        self.assertEqual(
            formatear_destino_excel("Antwerp"),
            "Amberes",
        )

    def test_carton_sin_colilla(self):
        self.assertEqual(
            limpiar_carton("CARTON STD SIN COLILLA 20 KG"),
            "CARTON STD 20 KG",
        )
        self.assertEqual(
            clave_carton("CARTON STD SIN COLILLA"),
            "CARTON STD",
        )

    def test_antwerp_a_ambar(self):
        self.assertEqual(normalizar_destino("Antwerp"), "AMBERES")

    def test_total_general_gastos(self):
        # Formato real PDF: cajas + montos con €
        # (kg, precio caja, venta, puerto, trans, handl, comisión).
        total_cajas, venta, gastos, comision = _parsear_total_general(
            "Total general 1.800,00 1,20 € 11,96 € 21.552,00 € "
            "1.234,56 € 567,89 € 123,45 € 89,01 €"
        )
        self.assertEqual(total_cajas, Decimal("1800.00"))
        self.assertEqual(venta, Decimal("21552.00"))
        self.assertEqual(gastos["Gasto Puerto"], Decimal("1234.56"))
        self.assertEqual(gastos["Gasto Trans"], Decimal("567.89"))
        self.assertEqual(gastos["Gasto Handl"], Decimal("123.45"))
        self.assertEqual(comision, Decimal("89.01"))


class MermaTdvEuropaTests(unittest.TestCase):
    def test_merma_unica_coincide_con_ese_cliente(self):
        cliente = _linea_producto(cliente="GOZALBO")
        merma = _linea_producto(
            cliente="MERMA",
            es_merma=True,
            cajas_netas=Decimal("4"),
        )
        liquidacion = _liquidacion_base(
            lineas=(cliente,),
            mermas=(merma,),
        )
        merma_map, atribuciones, errores, _adv = atribuir_mermas(
            liquidacion
        )
        self.assertEqual(len(errores), 0)
        self.assertEqual(atribuciones[0].cliente, "GOZALBO")
        self.assertEqual(merma_map[id(cliente)], Decimal("4"))

    def test_merma_prefer_irmadona(self):
        cliente_merc = _linea_producto(cliente="MERCADONA")
        cliente_irma = _linea_producto(cliente="IRMADONA SA")
        merma = _linea_producto(
            cliente="MERMA",
            es_merma=True,
            cajas_netas=Decimal("5"),
        )
        liquidacion = _liquidacion_base(
            lineas=(cliente_merc, cliente_irma),
            mermas=(merma,),
        )
        merma_map, atribuciones, errores, _adv = atribuir_mermas(
            liquidacion
        )
        self.assertEqual(len(errores), 0)
        self.assertEqual(len(atribuciones), 1)
        self.assertEqual(atribuciones[0].cliente, "IRMADONA SA")
        self.assertEqual(
            merma_map[id(cliente_irma)],
            Decimal("5"),
        )

    def test_merma_ambigua_sin_irmadona_bloquea(self):
        cliente_irma = _linea_producto(cliente="IRMADONA")
        cliente_otro = _linea_producto(cliente="GOZALBO")
        merma = _linea_producto(
            cliente="MERMA",
            es_merma=True,
            cajas_netas=Decimal("3"),
        )
        liquidacion = _liquidacion_base(
            lineas=(cliente_otro, cliente_irma),
            mermas=(merma,),
        )
        _map, _atr, errores, _adv = atribuir_mermas(liquidacion)
        self.assertEqual(len(errores), 1)
        self.assertEqual(errores[0].codigo, "MERMA_AMBIGUA")


class ValidacionTdvEuropaTests(unittest.TestCase):
    def test_total_general_gastos_en_validacion(self):
        linea = _linea_producto()
        liquidacion = _liquidacion_base(lineas=(linea,))
        resultado = validar_liquidacion_tdv_europa(
            liquidacion=liquidacion,
            despachos=_despachos(total_cajas=100),
            destino_ui="AMBERES",
            factura_ui="1234",
            semana_ui=9,
            anio_ui=2026,
        )
        self.assertTrue(resultado.es_valido)
        self.assertEqual(resultado.total_gastos_eur, Decimal("60"))
        self.assertEqual(
            resultado.resumen_gastos["Gasto Puerto"],
            Decimal("10"),
        )
        self.assertIsInstance(
            resultado.lineas_preparadas[0],
            LineaPreparadaTdvEuropa,
        )


class WriterTdvEuropaTests(unittest.TestCase):
    def test_preparar_sheet_quita_huerfanas_y_duplicados(self) -> None:
        from services.tdv_europa.writer import (
            _detectar_ultima_fila_datos,
            _preparar_sheet_antes_escritura,
            _resolver_fila_fin_datos,
        )

        fila_completa = (
            '<row r="11984" spans="1:108">'
            + ('<c r="A11984"><v>1</v></c>' * 50)
            + "</row>"
        )
        huerfana = (
            '<row r="11988" spans="74:74">'
            '<c r="BV11988"><f>+1</f><v>0</v></c></row>'
        )
        parcial = (
            '<row r="11985" spans="1:108">'
            '<c r="A11985" t="inlineStr"><is><t>16-2026</t></is></c>'
            '<c r="B11985" t="inlineStr"><is><t>2026</t></is></c>'
            "</row>"
        )
        sheet = (
            '<?xml version="1.0"?><worksheet>'
            "<sheetData>"
            '<row r="1"><c r="A1"><v>Semana</v></c></row>'
            f"{fila_completa}{huerfana}{parcial}"
            "</sheetData></worksheet>"
        )

        self.assertEqual(_detectar_ultima_fila_datos(sheet), 11984)
        self.assertEqual(
            _resolver_fila_fin_datos(sheet, 12014),
            11984,
        )

        limpio = _preparar_sheet_antes_escritura(sheet, 11984)
        self.assertNotIn('r="11985"', limpio)
        self.assertNotIn('r="11988"', limpio)
        self.assertIn('r="11984"', limpio)
        self.assertEqual(limpio.count('r="11984"'), 1)

    def test_conserva_filas_digitadas_previas(self) -> None:
        from services.tdv_europa.writer import (
            _detectar_ultima_fila_datos,
            _preparar_sheet_antes_escritura,
            _resolver_fila_fin_datos,
        )

        fila_completa = (
            '<row r="11984" spans="1:108">'
            + ('<c r="A11984"><v>1</v></c>' * 50)
            + "</row>"
        )
        digitada = (
            '<row r="12014" spans="1:108">'
            + (
                '<c r="A12014" t="inlineStr"><is><t>16-2026</t></is></c>'
                * 22
            )
            + "</row>"
        )
        huerfana = (
            '<row r="12020" spans="74:74">'
            '<c r="BV12020"><f>+1</f><v>0</v></c></row>'
        )
        sheet = (
            '<?xml version="1.0"?><worksheet>'
            "<sheetData>"
            '<row r="1"><c r="A1"><v>Semana</v></c></row>'
            f"{fila_completa}{digitada}{huerfana}"
            "</sheetData></worksheet>"
        )

        self.assertEqual(_detectar_ultima_fila_datos(sheet), 12014)
        self.assertEqual(
            _resolver_fila_fin_datos(sheet, 12014),
            12014,
        )
        limpio = _preparar_sheet_antes_escritura(sheet, 12014)
        self.assertIn('r="12014"', limpio)
        self.assertNotIn('r="12020"', limpio)

    def test_actualizar_auto_filter_tabla(self) -> None:
        from services.tdv_europa.writer import (
            _actualizar_auto_filter_tabla,
            _actualizar_ref_tabla,
        )

        tabla = (
            '<table ref="A1:DD11984">'
            '<autoFilter ref="A1:DD11984"/>'
            "</table>"
        )
        nueva = "A1:DD12014"
        actualizada = _actualizar_ref_tabla(tabla, nueva)
        actualizada = _actualizar_auto_filter_tabla(
            actualizada,
            nueva,
        )
        self.assertIn('ref="A1:DD12014"', actualizada)
        self.assertEqual(
            actualizada.count('ref="A1:DD12014"'),
            2,
        )
