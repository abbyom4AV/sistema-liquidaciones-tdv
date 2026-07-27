from __future__ import annotations

import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.test.utils import (
    setup_databases,
    setup_test_environment,
    teardown_databases,
    teardown_test_environment,
)

from procesamientos.models import (
    CorreccionGastoDimanno,
    GastoProcesamientoDimanno,
    ProcesamientoDimanno,
)
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

_DJANGO_DB_CONFIG = None


def setUpModule() -> None:
    global _DJANGO_DB_CONFIG
    setup_test_environment()
    _DJANGO_DB_CONFIG = setup_databases(
        verbosity=0,
        interactive=False,
        keepdb=False,
    )


def tearDownModule() -> None:
    global _DJANGO_DB_CONFIG
    if _DJANGO_DB_CONFIG is not None:
        teardown_databases(_DJANGO_DB_CONFIG, verbosity=0)
        _DJANGO_DB_CONFIG = None
    teardown_test_environment()


def _xlsx_falso(nombre: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        nombre,
        b"contenido-falso-xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


def _gastos_semana_15() -> dict[str, Decimal]:
    return {
        "Comisión": Decimal("4258.9832"),
        "Flete Eu": Decimal("0"),
        "Control calidad Eu": Decimal("146.36"),
        "THC": Decimal("830"),
        "Transporte": Decimal("4600"),
        "Aduanas": Decimal("272.63"),
    }


def _procesamiento_listo(
    *,
    gastos: dict[str, Decimal] | None = None,
    estado: str = "listo",
    puede_escribir: bool = True,
    requiere_destino: bool = False,
) -> ResultadoPreparacionDimanno:
    if gastos is None:
        gastos = _gastos_semana_15()

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
        es_valido=estado != "invalido",
        requiere_resolver_destino=requiere_destino,
        destino_liquidacion="VADO LIGURE",
        destinos_despachos=(
            ("GENOVA", "LIVORNO")
            if requiere_destino
            else ("VADO LIGURE",)
        ),
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
        estado=estado,
        puede_escribir=puede_escribir,
        destino_final=(
            None if requiere_destino else "VADO LIGURE"
        ),
        origen_destino=(
            None if requiere_destino else "coincidente"
        ),
        liquidacion=liquidacion,
        despachos=despachos,
        validacion=validacion,
    )


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class PruebasVistaCargaDimanno(TestCase):
    def setUp(self) -> None:
        self.media_dir = tempfile.mkdtemp(prefix="media_test_")
        self.override = override_settings(
            MEDIA_ROOT=self.media_dir,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(
            lambda: shutil.rmtree(
                self.media_dir,
                ignore_errors=True,
            )
        )
        self.cliente = Client()
        self.url = "/procesamientos/dimanno/"

    def _post_carga(self):
        return self.cliente.post(
            self.url,
            {
                "anio": "2026",
                "nombre_hoja": "FT 5292 W15",
                "archivo_despachos": _xlsx_falso(
                    "Despachos Original.xlsx"
                ),
                "archivo_liquidacion": _xlsx_falso(
                    "Liquidacion Original.xlsx"
                ),
                "archivo_cliente": _xlsx_falso(
                    "Cliente Original.xlsx"
                ),
            },
        )

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
    def test_post_valido_crea_procesamiento_y_redirige(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()

        respuesta = self._post_carga()

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(ProcesamientoDimanno.objects.count(), 1)
        procesamiento = ProcesamientoDimanno.objects.get()
        self.assertIn(
            str(procesamiento.id),
            respuesta["Location"],
        )
        mock_writer.assert_not_called()
        mock_preparar.assert_called_once()

        self.assertTrue(
            procesamiento.archivo_despachos.name.endswith(
                f"procesamientos/dimanno/{procesamiento.id}/despachos.xlsx"
            )
        )
        self.assertTrue(
            procesamiento.archivo_liquidacion.name.endswith(
                "liquidacion.xlsx"
            )
        )
        self.assertTrue(
            procesamiento.archivo_cliente.name.endswith(
                "cliente.xlsx"
            )
        )

        gastos = list(
            procesamiento.gastos.order_by("orden")
        )
        self.assertEqual(len(gastos), 6)
        for gasto in gastos:
            self.assertEqual(
                gasto.valor_original,
                gasto.valor_aplicado,
            )
            self.assertIsInstance(
                gasto.valor_original,
                Decimal,
            )

        self.assertEqual(
            procesamiento.total_gastos_originales,
            Decimal("10107.9732"),
        )
        self.assertEqual(
            procesamiento.total_gastos_aplicados,
            Decimal("10107.9732"),
        )

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_detalle_muestra_gastos_y_totales(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self._post_carga()
        procesamiento = ProcesamientoDimanno.objects.get()

        mock_preparar.reset_mock()
        respuesta = self.cliente.get(
            f"/procesamientos/dimanno/{procesamiento.id}/"
        )

        self.assertEqual(respuesta.status_code, 200)
        mock_preparar.assert_not_called()
        self.assertContains(respuesta, "Modificar gastos")
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
        self.assertContains(respuesta, "10107,9732")
        self.assertContains(respuesta, ">0<")
        self.assertContains(respuesta, "Flete Eu")

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno",
        side_effect=FormatoLiquidacionError(
            "La hoja indicada no existe."
        ),
    )
    def test_error_processor_no_deja_huerfanos(
        self,
        _mock_preparar,
    ) -> None:
        respuesta = self._post_carga()
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "La hoja indicada no existe.",
            status_code=400,
        )
        self.assertEqual(
            ProcesamientoDimanno.objects.count(),
            0,
        )
        base = (
            Path(self.media_dir)
            / "procesamientos"
            / "dimanno"
        )
        if base.exists():
            self.assertEqual(list(base.iterdir()), [])

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_editar_un_rubro_crea_correccion(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self._post_carga()
        procesamiento = ProcesamientoDimanno.objects.get()
        gastos = list(
            procesamiento.gastos.order_by("orden")
        )
        transporte = next(
            g for g in gastos if g.codigo == "transporte"
        )

        datos = {
            "motivo": "Ajuste de flete interno",
            "responsable": "Operador Prueba",
            "form-TOTAL_FORMS": str(len(gastos)),
            "form-INITIAL_FORMS": str(len(gastos)),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for indice, gasto in enumerate(gastos):
            datos[f"form-{indice}-id"] = str(gasto.id)
            if gasto.id == transporte.id:
                datos[f"form-{indice}-valor_aplicado"] = "4500"
            else:
                datos[f"form-{indice}-valor_aplicado"] = (
                    format(gasto.valor_aplicado, "f")
                )

        respuesta = self.cliente.post(
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
            datos,
        )
        self.assertEqual(respuesta.status_code, 302)

        transporte.refresh_from_db()
        self.assertEqual(
            transporte.valor_original,
            Decimal("4600"),
        )
        self.assertEqual(
            transporte.valor_aplicado,
            Decimal("4500"),
        )
        self.assertEqual(
            CorreccionGastoDimanno.objects.count(),
            1,
        )
        correccion = CorreccionGastoDimanno.objects.get()
        self.assertEqual(
            correccion.valor_anterior,
            Decimal("4600"),
        )
        self.assertEqual(
            correccion.valor_nuevo,
            Decimal("4500"),
        )
        self.assertEqual(
            correccion.usuario_nombre,
            "Operador Prueba",
        )
        self.assertIsInstance(
            transporte.valor_aplicado,
            Decimal,
        )

        detalle = self.cliente.get(
            f"/procesamientos/dimanno/{procesamiento.id}/"
        )
        self.assertContains(detalle, "Modificado")
        self.assertContains(
            detalle,
            "Los gastos se actualizaron correctamente.",
        )

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_editar_dos_rubros_crea_dos_correcciones(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self._post_carga()
        procesamiento = ProcesamientoDimanno.objects.get()
        gastos = list(
            procesamiento.gastos.order_by("orden")
        )

        datos = {
            "motivo": "Ajuste doble",
            "responsable": "Operador Prueba",
            "form-TOTAL_FORMS": str(len(gastos)),
            "form-INITIAL_FORMS": str(len(gastos)),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for indice, gasto in enumerate(gastos):
            datos[f"form-{indice}-id"] = str(gasto.id)
            if gasto.codigo == "transporte":
                datos[f"form-{indice}-valor_aplicado"] = "4500"
            elif gasto.codigo == "aduanas":
                datos[f"form-{indice}-valor_aplicado"] = "200"
            else:
                datos[f"form-{indice}-valor_aplicado"] = (
                    format(gasto.valor_aplicado, "f")
                )

        respuesta = self.cliente.post(
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
            datos,
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            CorreccionGastoDimanno.objects.count(),
            2,
        )

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_sin_cambios_ni_motivo_ni_responsable(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self._post_carga()
        procesamiento = ProcesamientoDimanno.objects.get()
        gastos = list(
            procesamiento.gastos.order_by("orden")
        )

        datos_sin_cambio = {
            "motivo": "Sin cambios reales",
            "responsable": "Operador",
            "form-TOTAL_FORMS": str(len(gastos)),
            "form-INITIAL_FORMS": str(len(gastos)),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for indice, gasto in enumerate(gastos):
            datos_sin_cambio[f"form-{indice}-id"] = str(
                gasto.id
            )
            datos_sin_cambio[
                f"form-{indice}-valor_aplicado"
            ] = format(gasto.valor_aplicado, "f")

        respuesta = self.cliente.post(
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
            datos_sin_cambio,
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "No se realizó ningún cambio en los gastos.",
            status_code=400,
        )
        self.assertEqual(
            CorreccionGastoDimanno.objects.count(),
            0,
        )

        datos_sin_motivo = dict(datos_sin_cambio)
        datos_sin_motivo["motivo"] = ""
        datos_sin_motivo["form-0-valor_aplicado"] = "1"
        respuesta = self.cliente.post(
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
            datos_sin_motivo,
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "Indique el motivo de la corrección.",
            status_code=400,
        )

        datos_sin_responsable = dict(datos_sin_cambio)
        datos_sin_responsable["responsable"] = ""
        datos_sin_responsable[
            "form-0-valor_aplicado"
        ] = "1"
        respuesta = self.cliente.post(
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
            datos_sin_responsable,
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "Indique el nombre del responsable.",
            status_code=400,
        )

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_segunda_modificacion_conserva_historial(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self._post_carga()
        procesamiento = ProcesamientoDimanno.objects.get()
        gastos = list(
            procesamiento.gastos.order_by("orden")
        )
        flete = next(
            g for g in gastos if g.codigo == "flete_eu"
        )

        def _post_valor(valor: str) -> None:
            datos = {
                "motivo": f"Cambio a {valor}",
                "responsable": "Operador",
                "form-TOTAL_FORMS": str(len(gastos)),
                "form-INITIAL_FORMS": str(len(gastos)),
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
            }
            actuales = list(
                procesamiento.gastos.order_by("orden")
            )
            for indice, gasto in enumerate(actuales):
                datos[f"form-{indice}-id"] = str(gasto.id)
                if gasto.codigo == "flete_eu":
                    datos[
                        f"form-{indice}-valor_aplicado"
                    ] = valor
                else:
                    datos[
                        f"form-{indice}-valor_aplicado"
                    ] = format(gasto.valor_aplicado, "f")
            self.cliente.post(
                (
                    f"/procesamientos/dimanno/"
                    f"{procesamiento.id}/gastos/editar/"
                ),
                datos,
            )

        _post_valor("10")
        _post_valor("0")

        flete.refresh_from_db()
        self.assertEqual(flete.valor_original, Decimal("0"))
        self.assertEqual(flete.valor_aplicado, Decimal("0"))
        self.assertEqual(
            CorreccionGastoDimanno.objects.filter(
                gasto=flete
            ).count(),
            2,
        )
        historial = list(
            CorreccionGastoDimanno.objects.filter(
                gasto=flete
            ).order_by("creado_en")
        )
        self.assertEqual(
            historial[0].valor_anterior,
            Decimal("0"),
        )
        self.assertEqual(
            historial[0].valor_nuevo,
            Decimal("10"),
        )
        self.assertEqual(
            historial[1].valor_anterior,
            Decimal("10"),
        )
        self.assertEqual(
            historial[1].valor_nuevo,
            Decimal("0"),
        )

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_eliminar_procesamiento_borra_solo_su_carpeta(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self._post_carga()
        procesamiento = ProcesamientoDimanno.objects.get()
        carpeta = Path(procesamiento.carpeta_media)
        self.assertTrue(carpeta.exists())

        ajena = (
            Path(self.media_dir)
            / "procesamientos"
            / "otra"
        )
        ajena.mkdir(parents=True)
        (ajena / "no-borrar.txt").write_text("x", encoding="utf-8")

        procesamiento.delete()
        self.assertFalse(carpeta.exists())
        self.assertTrue((ajena / "no-borrar.txt").exists())

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_obtener_gastos_aplicados(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self._post_carga()
        procesamiento = ProcesamientoDimanno.objects.get()
        aplicados = procesamiento.obtener_gastos_aplicados()
        self.assertEqual(
            aplicados["Transporte"],
            Decimal("4600"),
        )
        self.assertEqual(
            aplicados["Flete Eu"],
            Decimal("0"),
        )
