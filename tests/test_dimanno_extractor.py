from decimal import Decimal
from pathlib import Path
import unittest

from services.dimanno.extractor import (
    convertir_decimal,
    extraer_liquidacion,
    interpretar_producto,
    normalizar_texto,
    separar_contenedores,
)


BASE_DIR = Path(__file__).resolve().parents[1]

RUTA_LIQUIDACION = (
    BASE_DIR
    / "PruebaSemana15 - Dimanno"
    / "Copia de Esquema gastos TROPICALES 2026 - Hasta semana 22.xlsx"
)


class PruebasUtilidadesExtractorDimanno(unittest.TestCase):
    def test_normalizar_texto(self) -> None:
        resultado = normalizar_texto("  Control de calidad  ")
        self.assertEqual(resultado, "CONTROL DE CALIDAD")

    def test_convertir_decimal_elimina_residuo_float(self) -> None:
        resultado = convertir_decimal(
            4258.983200000001,
            "Comisión",
        )
        self.assertEqual(resultado, Decimal("4258.9832"))

    def test_convertir_decimal_formato_europeo(self) -> None:
        resultado = convertir_decimal(
            "1.234,56 €",
            "Monto",
        )
        self.assertEqual(resultado, Decimal("1234.56"))

    def test_interpretar_merce_corona(self) -> None:
        tipo_fruta, calibre = interpretar_producto(
            "Merce Corona 7"
        )
        self.assertEqual(tipo_fruta, "Intermedio")
        self.assertEqual(calibre, 7)

    def test_interpretar_extra_con_cintillo(self) -> None:
        tipo_fruta, calibre = interpretar_producto(
            "EXTRA CON CINTILLO 6"
        )
        self.assertEqual(tipo_fruta, "Especial")
        self.assertEqual(calibre, 6)

    def test_separar_varios_contenedores(self) -> None:
        resultado = separar_contenedores(
            "CXRU1615350/SEGU9548592"
        )
        self.assertEqual(
            resultado,
            ("CXRU1615350", "SEGU9548592"),
        )


@unittest.skipUnless(
    RUTA_LIQUIDACION.is_file(),
    "El archivo real de prueba no está disponible localmente.",
)
class PruebaIntegracionExtractorDimanno(unittest.TestCase):
    def test_extraer_semana_15(self) -> None:
        liquidacion = extraer_liquidacion(
            ruta_archivo=RUTA_LIQUIDACION,
            nombre_hoja="FT 5292 W15",
        )

        self.assertEqual(liquidacion.factura_corta, "5292")
        self.assertEqual(liquidacion.semana, 15)

        self.assertEqual(
            liquidacion.contenedores,
            ("CXRU1615350", "SEGU9548592"),
        )

        self.assertEqual(liquidacion.naviera, "COSIARMA")
        self.assertEqual(liquidacion.destino, "VADO LIGURE")
        self.assertEqual(liquidacion.total_cajas, 3255)

        self.assertEqual(
            liquidacion.total_venta_eur,
            Decimal("53237.29"),
        )

        productos = {
            (producto.tipo_fruta, producto.calibre): (
                producto.cajas,
                producto.precio_eur,
            )
            for producto in liquidacion.productos
        }

        self.assertEqual(
            productos[("Intermedio", 6)],
            (480, Decimal("14.15")),
        )

        self.assertEqual(
            productos[("Intermedio", 7)],
            (1040, Decimal("14.096")),
        )

        self.assertEqual(
            productos[("Intermedio", 8)],
            (160, Decimal("13.17")),
        )

        self.assertEqual(
            productos[("Especial", 6)],
            (1050, Decimal("18.82")),
        )

        self.assertEqual(
            productos[("Especial", 7)],
            (525, Decimal("18.89")),
        )

        self.assertEqual(
            liquidacion.gastos,
            {
                "Comisión": Decimal("4258.9832"),
                "Flete Eu": Decimal("0"),
                "Control calidad Eu": Decimal("146.36"),
                "THC": Decimal("830"),
                "Transporte": Decimal("4600"),
                "Aduanas": Decimal("272.63"),
            },
        )

        self.assertEqual(
            liquidacion.rubros_no_mapeados,
            (),
        )


if __name__ == "__main__":
    unittest.main()