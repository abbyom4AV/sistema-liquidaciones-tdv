from pathlib import Path
import unittest

from services.dimanno.matcher import (
    FormatoDespachosError,
    obtener_factura_corta,
    interpretar_semana,
    normalizar_factura,
    normalizar_texto,
    buscar_lineas_despachos,
)


BASE_DIR = Path(__file__).resolve().parents[1]

RUTA_DESPACHOS = (
    BASE_DIR
    / "PruebaSemana15 - Dimanno"
    / "Despachos TDV 2025 V18.xlsx"
)


class PruebasUtilidadesMatcherDimanno(unittest.TestCase):
    def test_normalizar_texto_elimina_acentos(self) -> None:
        resultado = normalizar_texto("  Año  ")

        self.assertEqual(resultado, "ANO")

    def test_interpretar_semana_con_anio(self) -> None:
        semana, anio = interpretar_semana("15-2026")

        self.assertEqual(semana, 15)
        self.assertEqual(anio, 2026)

    def test_interpretar_semana_sin_anio(self) -> None:
        semana, anio = interpretar_semana("W 15")

        self.assertEqual(semana, 15)
        self.assertIsNone(anio)

    def test_normalizar_factura_conserva_ceros_iniciales(self) -> None:
        resultado = normalizar_factura(
            "00400001090000005292"
        )

        self.assertEqual(
            resultado,
            "00400001090000005292",
        )

    def test_obtener_ultimos_cuatro_digitos(self) -> None:
        resultado = obtener_factura_corta(
            "00400001090000005292"
        )

        self.assertEqual(resultado, "5292")

    def test_rechaza_factura_float_extensa(self) -> None:
        with self.assertRaises(FormatoDespachosError):
            normalizar_factura(400001090000005292.0)


@unittest.skipUnless(
    RUTA_DESPACHOS.is_file(),
    "El archivo real de Despachos no está disponible localmente.",
)
class PruebaIntegracionMatcherDimanno(unittest.TestCase):
    def test_buscar_semana_15_factura_5292(self) -> None:
        resultado = buscar_lineas_despachos(
            ruta_archivo=RUTA_DESPACHOS,
            cliente="DI MANNO",
            anio=2026,
            semana=15,
            factura_corta="5292",
        )

        self.assertEqual(resultado.total_cajas, 3255)

        self.assertEqual(
            resultado.contenedores,
            ("CXRU1615350", "SEGU9548592"),
        )

        self.assertEqual(len(resultado.lineas), 5)

        lineas_obtenidas = [
            (
                linea.contenedor,
                linea.tipo_empaque,
                linea.calibre,
                linea.total_cajas,
            )
            for linea in resultado.lineas
        ]

        lineas_esperadas = [
            ("CXRU1615350", "Especial", 6, 1050),
            ("CXRU1615350", "Especial", 7, 525),
            ("SEGU9548592", "Intermedio", 6, 480),
            ("SEGU9548592", "Intermedio", 7, 1040),
            ("SEGU9548592", "Intermedio", 8, 160),
        ]

        self.assertEqual(
            lineas_obtenidas,
            lineas_esperadas,
        )

        for linea in resultado.lineas:
            self.assertEqual(linea.factura_corta, "5292")
            self.assertEqual(linea.anio, 2026)
            self.assertEqual(linea.semana, 15)
            self.assertEqual(linea.cliente, "DI MANNO")
            self.assertEqual(
                linea.puerto_destino,
                "VADO LIGURE",
            )


if __name__ == "__main__":
    unittest.main()