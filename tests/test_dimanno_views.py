from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, override_settings

from services.dimanno.extractor import (
    FormatoLiquidacionError,
    LiquidacionDimanno,
)
from services.dimanno.matcher import LineaDespacho, ResultadoMatcher
from services.dimanno.processor import ResultadoPreparacionDimanno
from services.dimanno.validator import (
    LineaPreparada,
    ResultadoValidacion,
)


def _xlsx_falso(nombre: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        nombre,
        b"contenido-falso-xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


def _datos_post() -> dict:
    return {
        "anio": "2026",
        "nombre_hoja": "FT 5292 W15",
        "archivo_despachos": _xlsx_falso("despachos.xlsx"),
        "archivo_liquidacion": _xlsx_falso(
            "liquidacion.xlsx"
        ),
        "archivo_cliente": _xlsx_falso("cliente.xlsx"),
    }


def _procesamiento_listo(
    *,
    gastos: dict[str, Decimal] | None = None,
) -> ResultadoPreparacionDimanno:
    if gastos is None:
        gastos = {
            "Comisión": Decimal("4258.9832"),
            "Flete Eu": Decimal("0"),
            "Control calidad Eu": Decimal("146.36"),
            "THC": Decimal("830"),
            "Transporte": Decimal("4600"),
            "Aduanas": Decimal("272.63"),
        }

    despacho = LineaDespacho(
        fila_excel=10,
        semana=15,
        anio=2026,
        contenedor="CXRU1615350",
        cliente="DI MANNO",
        barco="NAVE",
        puerto_destino="VADO LIGURE",
        tipo_empaque="Especial",
        carton="CARTON",
        calibre=6,
        total_cajas=1050,
        factura="5292",
        factura_corta="5292",
    )

    liquidacion = LiquidacionDimanno(
        archivo="liquidacion.xlsx",
        hoja="FT 5292 W15",
        factura_corta="5292",
        semana=15,
        contenedores=("CXRU1615350",),
        naviera="NAV",
        destino="VADO LIGURE",
        total_cajas=1050,
        total_venta_eur=Decimal("100.00"),
        productos=(),
        gastos=gastos,
        rubros_no_mapeados=(),
    )

    despachos = ResultadoMatcher(
        archivo="despachos.xlsx",
        hoja="Despachos",
        cliente_buscado="DI MANNO",
        anio_buscado=2026,
        semana_buscada=15,
        factura_corta_buscada="5292",
        lineas=(despacho,),
        total_cajas=1050,
        contenedores=("CXRU1615350",),
    )

    validacion = ResultadoValidacion(
        es_valido=True,
        requiere_resolver_destino=False,
        destino_liquidacion="VADO LIGURE",
        destinos_despachos=("VADO LIGURE",),
        total_cajas_liquidacion=1050,
        total_cajas_despachos=1050,
        total_venta_informado_eur=Decimal("100.00"),
        total_venta_calculado_eur=Decimal("100.00"),
        errores=(),
        advertencias=(),
        lineas_preparadas=(
            LineaPreparada(
                despacho=despacho,
                tipo_fruta="Especial",
                calibre=6,
                precio_venta_eur=Decimal("18.82"),
            ),
        ),
    )

    return ResultadoPreparacionDimanno(
        estado="listo",
        puede_escribir=True,
        destino_final="VADO LIGURE",
        origen_destino="coincidente",
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class PruebasVistaCargaDimanno(SimpleTestCase):
    def setUp(self) -> None:
        self.cliente = Client()
        self.url = "/procesamientos/dimanno/"

    def test_get_devuelve_200(self) -> None:
        respuesta = self.cliente.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Validar archivos")

    def test_post_sin_archivos_devuelve_errores(
        self,
    ) -> None:
        respuesta = self.cliente.post(
            self.url,
            {
                "anio": "2026",
                "nombre_hoja": "FT 5292 W15",
            },
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "Seleccione el archivo de despachos.",
            status_code=400,
        )
        self.assertContains(
            respuesta,
            "Seleccione el archivo de liquidación.",
            status_code=400,
        )
        self.assertContains(
            respuesta,
            "Seleccione el archivo acumulativo del cliente.",
            status_code=400,
        )

    def test_rechaza_extension_distinta_de_xlsx(
        self,
    ) -> None:
        respuesta = self.cliente.post(
            self.url,
            {
                "anio": "2026",
                "nombre_hoja": "FT 5292 W15",
                "archivo_despachos": SimpleUploadedFile(
                    "despachos.csv",
                    b"a,b",
                    content_type="text/csv",
                ),
                "archivo_liquidacion": _xlsx_falso(
                    "liquidacion.xlsx"
                ),
                "archivo_cliente": _xlsx_falso(
                    "cliente.xlsx"
                ),
            },
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "debe tener extensión .xlsx",
            status_code=400,
        )

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_no_llama_al_writer(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()

        respuesta = self.cliente.post(
            self.url,
            _datos_post(),
        )

        self.assertEqual(respuesta.status_code, 200)
        mock_preparar.assert_called_once()
        mock_writer.assert_not_called()
        self.assertContains(respuesta, "listo")
        self.assertContains(respuesta, "5292")

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno",
        side_effect=FormatoLiquidacionError(
            "La hoja indicada no existe."
        ),
    )
    def test_muestra_errores_del_processor(
        self,
        _mock_preparar,
    ) -> None:
        respuesta = self.cliente.post(
            self.url,
            _datos_post(),
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "La hoja indicada no existe.",
            status_code=400,
        )

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_muestra_gastos_extraidos(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()

        respuesta = self.cliente.post(
            self.url,
            _datos_post(),
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Gastos extraídos")
        for rubro in (
            "Comisión",
            "Flete Eu",
            "Control calidad Eu",
            "THC",
            "Transporte",
            "Aduanas",
        ):
            self.assertContains(respuesta, rubro)

        self.assertContains(respuesta, "4258,9832")
        self.assertContains(respuesta, ">0<")
        self.assertContains(respuesta, "4600")

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_gastos_vacios_muestra_mensaje(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo(
            gastos={},
        )

        respuesta = self.cliente.post(
            self.url,
            _datos_post(),
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Gastos extraídos")
        self.assertContains(
            respuesta,
            "No se extrajeron gastos de la liquidación.",
        )
        mock_writer.assert_not_called()
