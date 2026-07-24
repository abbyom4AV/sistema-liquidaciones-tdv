from pathlib import Path
import unittest

from services.dimanno.processor import (
    preparar_procesamiento_dimanno,
)


BASE_DIR = Path(__file__).resolve().parents[1]

RUTA_LIQUIDACION = (
    BASE_DIR
    / "PruebaSemana15 - Dimanno"
    / "Copia de Esquema gastos TROPICALES 2026 - Hasta semana 22.xlsx"
)

RUTA_DESPACHOS = (
    BASE_DIR
    / "PruebaSemana15 - Dimanno"
    / "Despachos TDV 2025 V18.xlsx"
)

ARCHIVOS_DISPONIBLES = (
    RUTA_LIQUIDACION.is_file()
    and RUTA_DESPACHOS.is_file()
)


@unittest.skipUnless(
    ARCHIVOS_DISPONIBLES,
    "Los archivos reales de prueba no están disponibles.",
)
class PruebasProcesadorDimanno(unittest.TestCase):
    def test_semana_15_esta_lista(self) -> None:
        resultado = preparar_procesamiento_dimanno(
            ruta_liquidacion=RUTA_LIQUIDACION,
            nombre_hoja="FT 5292 W15",
            ruta_despachos=RUTA_DESPACHOS,
            anio=2026,
        )

        self.assertEqual(resultado.estado, "listo")
        self.assertTrue(resultado.puede_escribir)

        self.assertEqual(
            resultado.destino_final,
            "VADO LIGURE",
        )

        self.assertEqual(
            resultado.origen_destino,
            "coincidente",
        )

        self.assertEqual(
            len(resultado.validacion.lineas_preparadas),
            5,
        )

    def test_semana_22_se_detiene_sin_destino(self) -> None:
        resultado = preparar_procesamiento_dimanno(
            ruta_liquidacion=RUTA_LIQUIDACION,
            nombre_hoja="FT 5532 W22",
            ruta_despachos=RUTA_DESPACHOS,
            anio=2026,
        )

        self.assertEqual(
            resultado.estado,
            "requiere_destino",
        )

        self.assertFalse(resultado.puede_escribir)
        self.assertIsNone(resultado.destino_final)
        self.assertIsNone(resultado.origen_destino)

    def test_semana_22_acepta_destino_despachos(
        self,
    ) -> None:
        resultado = preparar_procesamiento_dimanno(
            ruta_liquidacion=RUTA_LIQUIDACION,
            nombre_hoja="FT 5532 W22",
            ruta_despachos=RUTA_DESPACHOS,
            anio=2026,
            destino_confirmado="LIVORNO",
        )

        self.assertEqual(resultado.estado, "listo")
        self.assertTrue(resultado.puede_escribir)

        self.assertEqual(
            resultado.destino_final,
            "LIVORNO",
        )

        self.assertEqual(
            resultado.origen_destino,
            "despachos",
        )

    def test_semana_22_acepta_destino_liquidacion(
        self,
    ) -> None:
        resultado = preparar_procesamiento_dimanno(
            ruta_liquidacion=RUTA_LIQUIDACION,
            nombre_hoja="FT 5532 W22",
            ruta_despachos=RUTA_DESPACHOS,
            anio=2026,
            destino_confirmado="GENOVA",
        )

        self.assertEqual(resultado.estado, "listo")
        self.assertTrue(resultado.puede_escribir)

        self.assertEqual(
            resultado.destino_final,
            "GENOVA",
        )

        self.assertEqual(
            resultado.origen_destino,
            "liquidacion",
        )

    def test_semana_22_acepta_destino_manual(
        self,
    ) -> None:
        resultado = preparar_procesamiento_dimanno(
            ruta_liquidacion=RUTA_LIQUIDACION,
            nombre_hoja="FT 5532 W22",
            ruta_despachos=RUTA_DESPACHOS,
            anio=2026,
            destino_confirmado="LA SPEZIA",
        )

        self.assertEqual(resultado.estado, "listo")

        self.assertEqual(
            resultado.destino_final,
            "LA SPEZIA",
        )

        self.assertEqual(
            resultado.origen_destino,
            "manual",
        )


if __name__ == "__main__":
    unittest.main()