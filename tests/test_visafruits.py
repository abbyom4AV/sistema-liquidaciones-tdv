from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from procesamientos.services.generacion_visafruits import (
    reconstruir_resultado_para_escritura_visafruits,
    serializar_gastos_aplicados_visafruits,
    serializar_lineas_preparadas_visafruits,
)
from services.visafruits.extractor import (
    parsear_texto_liquidacion_visafruits,
)
from services.visafruits.matcher import (
    LineaDespachoVisafruits,
    ResultadoMatcherVisafruits,
)
from services.visafruits.validator import (
    validar_liquidacion_visafruits,
)
from services.visafruits.writer import (
    construir_valores_fila_visafruits,
)


# Texto tal como lo devuelve pdfplumber para la semana 28 de 2026.
TEXTO_PDF = """
VARIEDAD: PIÑA MD2
ORDEN NUMERO ALGECIRAS
FACTURA 5712
ETD WEEK28
ETA WEEK30
CONTENEDORES BMOU9833210//SZLU6006098//TEMU9668233
NUMERO DE CONTENEDORES 3
DISTRIBUCION
NUMERO DE CAJAS PRECIO CAJA IMPORTE TOTAL
PIÑA CALIBRE 5 VERDE 0 0,00 € 0,00 €
PIÑA CALIBRE 6 VERDE 0 0,00 € 0,00 €
PIÑA CALIBRE 7 VERDE 0 0,00 € 0,00 €
PIÑA CALIBRE 8 VERDE 0 0,00 € 0,00 €
PIÑA CALIBRE 9 VERDE 0 0,00 € 0,00 €
PIÑA CALIBRE 10 VERDE 0 0,00 € 0,00 €
PIÑA CALIBRE 5 1050 12,00 € 12.600,00 €
PIÑA CALIBRE 6 1120 13,50 € 15.120,00 €
PIÑA CALIBRE 7 960 13,00 € 12.480,00 €
PIÑA CALIBRE 8 800 12,50 € 10.000,00 €
PIÑA CALIBRE 9 480 11,00 € 5.280,00 €
PIÑA CALIBRE 10 240 11,00 € 2.640,00 €
PIÑA VERTICAL 240 17,00 € 4.080,00 €
Total de cajas 4890
IMPORTE TOTAL 62.200,00 €
COMISION VISAFRUIT 4.976,00 €
GASTOS LOGISTICOS 3.923,39
TRANSPORTE 1.202,64 €
DESPACHO +ADUANAS 2.720,75 €
TOTAL A PAGAR 53.300,61 €
53.300,61 €
"""


def _despacho(
    calibre,
    cajas,
    carton="GOLDEN DIAMOND",
    tipo="ESPECIAL",
    contenedor="TEMU9668233",
    fila=100,
    destino="TARRAGONA",
):
    return LineaDespachoVisafruits(
        fila_excel=fila,
        semana=28,
        anio=2026,
        semana_texto="28-2026",
        contenedor=contenedor,
        cliente="VISA FRUITS",
        barco="STAR COURAGE V.2628",
        puerto_destino=destino,
        tipo_empaque=tipo,
        carton=carton,
        calibre=calibre,
        total_cajas=cajas,
        factura="00400001090000005712",
        factura_corta="5712",
    )


def _matcher(lineas, destino="TARRAGONA"):
    return ResultadoMatcherVisafruits(
        archivo="Despachos.xlsx",
        hoja="Base Datos",
        cliente_buscado="VISA FRUITS",
        semana=28,
        anio=2026,
        destino_buscado=destino,
        factura_buscada="5712",
        semana_texto="28-2026",
        lineas=tuple(lineas),
        total_cajas=sum(ln.total_cajas for ln in lineas),
        contenedores=tuple(
            dict.fromkeys(ln.contenedor for ln in lineas)
        ),
        destinos=tuple(
            dict.fromkeys(ln.puerto_destino for ln in lineas)
        ),
        naves=tuple(dict.fromkeys(ln.barco for ln in lineas)),
    )


class ExtraccionVisafruitsTests(unittest.TestCase):
    def test_lee_cabecera_precios_gastos_y_comision(self):
        liq = parsear_texto_liquidacion_visafruits(
            TEXTO_PDF,
            "5712.pdf",
        )
        self.assertEqual(liq.factura_corta, "5712")
        self.assertEqual(liq.orden_numero, "ALGECIRAS")
        self.assertEqual(liq.semana_etd, 28)
        self.assertEqual(liq.contenedores_declarados, 3)
        self.assertEqual(
            liq.contenedores,
            ("BMOU9833210", "SZLU6006098", "TEMU9668233"),
        )
        self.assertEqual(liq.total_cajas, 4890)
        self.assertEqual(
            liq.importe_total_eur,
            Decimal("62200.00"),
        )
        self.assertEqual(liq.comision_eur, Decimal("4976.00"))
        self.assertEqual(
            liq.gastos["Transporte"],
            Decimal("1202.64"),
        )
        self.assertEqual(
            liq.gastos["Despachos + Aduanas"],
            Decimal("2720.75"),
        )
        self.assertEqual(
            liq.total_a_pagar_eur,
            Decimal("53300.61"),
        )

    def test_clasifica_verde_especial_y_vertical(self):
        liq = parsear_texto_liquidacion_visafruits(TEXTO_PDF)
        self.assertEqual(len(liq.precios), 13)

        verdes = [p for p in liq.precios if p.tipo_fruta == "VERDE"]
        self.assertEqual(len(verdes), 6)
        self.assertTrue(all(p.total_cajas == 0 for p in verdes))

        vertical = [p for p in liq.precios if p.es_vertical]
        self.assertEqual(len(vertical), 1)
        self.assertEqual(vertical[0].precio_eur, Decimal("17.00"))
        self.assertEqual(vertical[0].calibre, 0)

        calibre_6 = next(
            p
            for p in liq.precios
            if p.calibre == 6
            and p.tipo_fruta == "ESPECIAL"
            and not p.es_vertical
        )
        self.assertEqual(calibre_6.precio_eur, Decimal("13.50"))
        self.assertEqual(calibre_6.total_cajas, 1120)

    def test_gastos_logisticos_es_subtotal_y_no_se_reporta(self):
        liq = parsear_texto_liquidacion_visafruits(TEXTO_PDF)
        self.assertEqual(liq.rubros_no_mapeados, ())

    def test_reporta_rubro_de_gasto_sin_columna(self):
        texto = TEXTO_PDF.replace(
            "TOTAL A PAGAR 53.300,61 €",
            "ALMACENAJE 415,20 €\nTOTAL A PAGAR 53.300,61 €",
        )
        liq = parsear_texto_liquidacion_visafruits(texto)
        self.assertEqual(liq.rubros_no_mapeados, ("ALMACENAJE",))


class ValidacionVisafruitsTests(unittest.TestCase):
    def _liquidacion(self):
        return parsear_texto_liquidacion_visafruits(
            TEXTO_PDF,
            "5712.pdf",
        )

    def test_semana_28_completa_es_valida(self):
        lineas = [
            _despacho(10, 80),
            _despacho(5, 900, carton="GOLDEN DIAMOND ALTA"),
            _despacho(6, 240),
            _despacho(7, 160),
            _despacho(8, 160),
            _despacho(9, 80),
            _despacho(
                5,
                75,
                carton="GOLDEN DIAMOND ALTA",
                contenedor="BMOU9833210",
            ),
            _despacho(6, 880, contenedor="BMOU9833210"),
            _despacho(
                6,
                240,
                carton="VERTICAL GOLDEN DIAMOND",
                contenedor="BMOU9833210",
            ),
            _despacho(7, 400, contenedor="BMOU9833210"),
            _despacho(10, 160, contenedor="SZLU6006098"),
            _despacho(
                5,
                75,
                carton="GOLDEN DIAMOND ALTA",
                contenedor="SZLU6006098",
            ),
            _despacho(7, 400, contenedor="SZLU6006098"),
            _despacho(8, 640, contenedor="SZLU6006098"),
            _despacho(9, 400, contenedor="SZLU6006098"),
        ]
        resultado = validar_liquidacion_visafruits(
            self._liquidacion(),
            _matcher(lineas),
            "TARRAGONA",
        )

        self.assertTrue(resultado.es_valido)
        self.assertEqual(resultado.errores, ())
        self.assertEqual(len(resultado.lineas_preparadas), 15)
        self.assertEqual(resultado.total_cajas_despachos, 4890)
        self.assertEqual(
            resultado.total_venta_calculado_eur,
            Decimal("62200.00"),
        )
        self.assertTrue(
            all(
                linea.precio_encontrado
                for linea in resultado.lineas_preparadas
            )
        )

    def test_carton_vertical_usa_su_propio_precio(self):
        lineas = [
            _despacho(6, 880),
            _despacho(6, 240, carton="VERTICAL GOLDEN DIAMOND"),
        ]
        resultado = validar_liquidacion_visafruits(
            self._liquidacion(),
            _matcher(lineas),
            "TARRAGONA",
        )
        normal, vertical = resultado.lineas_preparadas
        self.assertEqual(normal.precio_venta_eur, Decimal("13.50"))
        self.assertTrue(vertical.es_vertical)
        self.assertEqual(vertical.precio_venta_eur, Decimal("17.00"))

    def test_destino_del_pdf_distinto_solo_advierte(self):
        resultado = validar_liquidacion_visafruits(
            self._liquidacion(),
            _matcher([_despacho(6, 1120)]),
            "TARRAGONA",
        )
        codigos = {a.codigo for a in resultado.advertencias}
        self.assertIn("DESTINO_PDF_DIFERENTE", codigos)
        self.assertTrue(resultado.es_valido)
        self.assertEqual(
            resultado.lineas_preparadas[0].destino,
            "Tarragona",
        )

    def test_tipo_sin_precio_en_el_pdf_solo_advierte(self):
        resultado = validar_liquidacion_visafruits(
            self._liquidacion(),
            _matcher(
                [_despacho(6, 240, tipo="INTERMEDIO")],
            ),
            "TARRAGONA",
        )
        codigos = {a.codigo for a in resultado.advertencias}
        self.assertIn("PRECIO_NO_ENCONTRADO", codigos)
        self.assertTrue(resultado.es_valido)
        linea = resultado.lineas_preparadas[0]
        self.assertFalse(linea.precio_encontrado)
        self.assertEqual(linea.precio_venta_eur, Decimal("0"))

    def test_cajas_y_venta_que_no_calzan_advierten(self):
        resultado = validar_liquidacion_visafruits(
            self._liquidacion(),
            _matcher([_despacho(6, 500)]),
            "TARRAGONA",
        )
        codigos = {a.codigo for a in resultado.advertencias}
        self.assertIn("TOTAL_CAJAS_DIFERENTE", codigos)
        self.assertIn("CAJAS_CALIBRE_DIFERENTE", codigos)
        self.assertIn("TOTAL_VENTA_DIFERENTE", codigos)
        self.assertIn("CONTENEDORES_DIFERENTES", codigos)

    def test_gasto_no_mapeado_bloquea(self):
        texto = TEXTO_PDF.replace(
            "TOTAL A PAGAR 53.300,61 €",
            "ALMACENAJE 415,20 €\nTOTAL A PAGAR 53.300,61 €",
        )
        liquidacion = parsear_texto_liquidacion_visafruits(texto)
        resultado = validar_liquidacion_visafruits(
            liquidacion,
            _matcher([_despacho(6, 1120)]),
            "TARRAGONA",
        )
        self.assertFalse(resultado.es_valido)
        codigos = {e.codigo for e in resultado.errores}
        self.assertIn("RUBROS_NO_MAPEADOS", codigos)

    def test_varias_naves_bloquea(self):
        primera = _despacho(6, 500)
        segunda = _despacho(
            7,
            620,
            contenedor="SZLU6006098",
        )
        segunda = replace(segunda, barco="OTRA NAVE V.2629")
        resultado = validar_liquidacion_visafruits(
            self._liquidacion(),
            _matcher([primera, segunda]),
            "TARRAGONA",
        )
        self.assertFalse(resultado.es_valido)
        codigos = {e.codigo for e in resultado.errores}
        self.assertIn("VARIAS_NAVES", codigos)


class ReconstruccionVisafruitsTests(unittest.TestCase):
    def _resultado_validado(self):
        liquidacion = parsear_texto_liquidacion_visafruits(
            TEXTO_PDF,
            "5712.pdf",
        )
        lineas = [
            _despacho(6, 880),
            _despacho(
                6,
                240,
                carton="VERTICAL GOLDEN DIAMOND",
                contenedor="BMOU9833210",
            ),
        ]
        return validar_liquidacion_visafruits(
            liquidacion,
            _matcher(lineas),
            "TARRAGONA",
        )

    def test_serializa_reconstruye_y_arma_la_fila(self):
        validacion = self._resultado_validado()
        serializadas = serializar_lineas_preparadas_visafruits(
            validacion.lineas_preparadas
        )
        gastos = serializar_gastos_aplicados_visafruits(
            {
                "Transporte": Decimal("1202.64"),
                "Despachos + Aduanas": Decimal("2720.75"),
                "Comision Euros": Decimal("4976.00"),
            }
        )
        resultado = reconstruir_resultado_para_escritura_visafruits(
            anio=2026,
            semana=28,
            destino_ui="TARRAGONA",
            factura_corta="5712",
            lineas_preparadas=serializadas,
            total_cajas_liquidacion=4890,
            total_cajas_despachos=1120,
            total_venta_liquidacion_eur=Decimal("62200.00"),
            gastos_aplicados=gastos,
        )
        self.assertTrue(resultado.puede_escribir)
        self.assertEqual(
            len(resultado.validacion.lineas_preparadas),
            2,
        )

        fila = construir_valores_fila_visafruits(resultado, 0)
        self.assertEqual(fila["Semana"], "28-2026")
        self.assertEqual(fila["Año"], 2026)
        self.assertEqual(fila["Cliente"], "VISA FRUITS")
        self.assertEqual(fila["Nave"], "STAR COURAGE V.2628")
        self.assertEqual(fila["Destino"], "Tarragona")
        self.assertEqual(fila["Tipo de fruta"], "Especial")
        self.assertEqual(fila["# Calibre"], 6)
        self.assertEqual(fila["Total Cajas"], 880)
        self.assertEqual(fila["Cartón"], "GOLDEN DIAMOND")
        self.assertEqual(fila["Transporte"], 1202.64)
        self.assertEqual(fila["Despachos + Aduanas"], 2720.75)
        self.assertEqual(fila["Comision Euros"], 4976.0)
        self.assertEqual(fila["Precio de Venta €"], 13.5)

        fila_vertical = construir_valores_fila_visafruits(
            resultado,
            1,
        )
        self.assertEqual(
            fila_vertical["Cartón"],
            "VERTICAL GOLDEN DIAMOND",
        )
        self.assertEqual(fila_vertical["Precio de Venta €"], 17.0)

    def test_gasto_corregido_manda_sobre_el_del_pdf(self):
        validacion = self._resultado_validado()
        serializadas = serializar_lineas_preparadas_visafruits(
            validacion.lineas_preparadas
        )
        resultado = reconstruir_resultado_para_escritura_visafruits(
            anio=2026,
            semana=28,
            destino_ui="TARRAGONA",
            factura_corta="5712",
            lineas_preparadas=serializadas,
            gastos_aplicados={
                "Transporte": "1500",
                "Despachos + Aduanas": "2720.75",
                "Comision Euros": "5000",
            },
        )
        fila = construir_valores_fila_visafruits(resultado, 0)
        self.assertEqual(fila["Transporte"], 1500.0)
        self.assertEqual(fila["Comision Euros"], 5000.0)


if __name__ == "__main__":
    unittest.main()
