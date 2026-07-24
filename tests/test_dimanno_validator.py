from dataclasses import replace
from pathlib import Path
import unittest

from services.dimanno.extractor import extraer_liquidacion
from services.dimanno.matcher import buscar_lineas_despachos
from services.dimanno.validator import validar_liquidacion


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
class PruebasValidadorDimanno(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.liquidacion_semana_15 = extraer_liquidacion(
            ruta_archivo=RUTA_LIQUIDACION,
            nombre_hoja="FT 5292 W15",
        )

        cls.despachos_semana_15 = buscar_lineas_despachos(
            ruta_archivo=RUTA_DESPACHOS,
            cliente="DI MANNO",
            anio=2026,
            semana=15,
            factura_corta="5292",
        )

    def test_semana_15_es_valida(self) -> None:
        resultado = validar_liquidacion(
            liquidacion=self.liquidacion_semana_15,
            despachos=self.despachos_semana_15,
        )

        self.assertTrue(resultado.es_valido)
        self.assertFalse(
            resultado.requiere_resolver_destino
        )

        self.assertEqual(resultado.errores, ())
        self.assertEqual(resultado.advertencias, ())
        self.assertEqual(len(resultado.lineas_preparadas), 5)

        precios = {
            (
                linea.tipo_fruta,
                linea.calibre,
            ): linea.precio_venta_eur
            for linea in resultado.lineas_preparadas
        }

        self.assertEqual(
            str(precios[("Especial", 6)]),
            "18.82",
        )

        self.assertEqual(
            str(precios[("Intermedio", 7)]),
            "14.096",
        )

    def test_detecta_total_de_cajas_diferente(self) -> None:
        liquidacion_modificada = replace(
            self.liquidacion_semana_15,
            total_cajas=3254,
        )

        resultado = validar_liquidacion(
            liquidacion=liquidacion_modificada,
            despachos=self.despachos_semana_15,
        )

        codigos = {
            error.codigo
            for error in resultado.errores
        }

        self.assertFalse(resultado.es_valido)

        self.assertIn(
            "TOTAL_CAJAS_NO_COINCIDE",
            codigos,
        )

    def test_detecta_contenedor_faltante(self) -> None:
        liquidacion_modificada = replace(
            self.liquidacion_semana_15,
            contenedores=("CXRU1615350",),
        )

        resultado = validar_liquidacion(
            liquidacion=liquidacion_modificada,
            despachos=self.despachos_semana_15,
        )

        codigos = {
            error.codigo
            for error in resultado.errores
        }

        self.assertFalse(resultado.es_valido)

        self.assertIn(
            "CONTENEDORES_NO_COINCIDEN",
            codigos,
        )

    def test_detecta_rubro_no_mapeado(self) -> None:
        liquidacion_modificada = replace(
            self.liquidacion_semana_15,
            rubros_no_mapeados=("Seguro adicional",),
        )

        resultado = validar_liquidacion(
            liquidacion=liquidacion_modificada,
            despachos=self.despachos_semana_15,
        )

        codigos = {
            error.codigo
            for error in resultado.errores
        }

        self.assertFalse(resultado.es_valido)

        self.assertIn(
            "RUBROS_NO_MAPEADOS",
            codigos,
        )

    def test_semana_22_requiere_resolver_destino(
        self,
    ) -> None:
        liquidacion = extraer_liquidacion(
            ruta_archivo=RUTA_LIQUIDACION,
            nombre_hoja="FT 5532 W22",
        )

        despachos = buscar_lineas_despachos(
            ruta_archivo=RUTA_DESPACHOS,
            cliente="DI MANNO",
            anio=2026,
            semana=22,
            factura_corta="5532",
        )

        resultado = validar_liquidacion(
            liquidacion=liquidacion,
            despachos=despachos,
        )

        codigos_advertencias = {
            advertencia.codigo
            for advertencia in resultado.advertencias
        }

        self.assertTrue(resultado.es_valido)

        self.assertTrue(
            resultado.requiere_resolver_destino
        )

        self.assertEqual(
            resultado.destino_liquidacion,
            "GENOVA",
        )

        self.assertEqual(
            resultado.destinos_despachos,
            ("LIVORNO",),
        )

        self.assertIn(
            "DESTINO_NO_COINCIDE",
            codigos_advertencias,
        )


if __name__ == "__main__":
    unittest.main()