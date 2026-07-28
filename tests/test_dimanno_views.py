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

from django.contrib.auth import get_user_model
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
    ResolucionDestinoDimanno,
)
from procesamientos.views import obtener_nombre_usuario
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

User = get_user_model()
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
        self.usuario = User.objects.create_user(
            username="operador",
            password="clave-segura-123",
            first_name="Ana",
            last_name="Pérez",
        )
        self.cliente.force_login(self.usuario)

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
        self.assertEqual(
            procesamiento.creado_por,
            self.usuario,
        )
        self.assertEqual(
            procesamiento.creado_por_nombre,
            "Ana Pérez",
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
        self.assertContains(respuesta, "Gastos")
        self.assertContains(
            respuesta,
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
        )
        self.assertContains(respuesta, "Modificar")
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
            "Ana Pérez",
        )
        self.assertEqual(correccion.usuario, self.usuario)
        self.assertIsInstance(
            transporte.valor_aplicado,
            Decimal,
        )

        detalle = self.cliente.get(
            f"/procesamientos/dimanno/{procesamiento.id}/"
        )
        self.assertContains(detalle, "Mod.")
        self.assertContains(
            detalle,
            'class="etiqueta-modificado"',
        )
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
    def test_sin_cambios_ni_motivo(
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


def _crear_procesamiento_destino(
    *,
    media_dir: str,
    estado: str = "requiere_destino",
    puede_escribir: bool = False,
    requiere_resolver_destino: bool = True,
    destino_liquidacion: str = "GENOVA",
    destinos_despachos: list[str] | None = None,
    destino_final: str = "",
    origen_destino_final: str = "",
    errores: list | None = None,
) -> ProcesamientoDimanno:
    if destinos_despachos is None:
        destinos_despachos = ["LIVORNO"]
    if errores is None:
        errores = []

    procesamiento = ProcesamientoDimanno(
        anio=2026,
        nombre_hoja="FT 5532 W22",
        factura_corta="5532",
        semana=22,
        estado=estado,
        destino_liquidacion=destino_liquidacion,
        destinos_despachos=destinos_despachos,
        destino_final=destino_final,
        origen_destino_final=origen_destino_final,
        puede_escribir=puede_escribir,
        requiere_resolver_destino=requiere_resolver_destino,
        errores=errores,
        advertencias=[],
        lineas_preparadas=[],
    )
    procesamiento.archivo_despachos.save(
        "despachos.xlsx",
        _xlsx_falso("despachos.xlsx"),
        save=False,
    )
    procesamiento.archivo_liquidacion.save(
        "liquidacion.xlsx",
        _xlsx_falso("liquidacion.xlsx"),
        save=False,
    )
    procesamiento.archivo_cliente.save(
        "cliente.xlsx",
        _xlsx_falso("cliente.xlsx"),
        save=False,
    )
    procesamiento.save()
    return procesamiento


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class PruebasResolucionDestinoDimanno(TestCase):
    def setUp(self) -> None:
        self.media_dir = tempfile.mkdtemp(prefix="media_dest_")
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
        self.usuario = User.objects.create_user(
            username="operador_destino",
            password="clave-segura-123",
            first_name="Carlos",
            last_name="Ruiz",
        )
        self.cliente.force_login(self.usuario)

    def _url(self, procesamiento: ProcesamientoDimanno) -> str:
        return (
            f"/procesamientos/dimanno/"
            f"{procesamiento.id}/destino/resolver/"
        )

    def _detalle(
        self,
        procesamiento: ProcesamientoDimanno,
    ) -> str:
        return f"/procesamientos/dimanno/{procesamiento.id}/"

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_get_resolucion_200_y_opciones(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        respuesta = self.cliente.get(
            self._url(procesamiento)
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(
            respuesta,
            "Usar destino de la liquidación: GENOVA",
        )
        self.assertContains(
            respuesta,
            "Usar destino de Despachos: LIVORNO",
        )
        self.assertContains(
            respuesta,
            "Ingresar otro destino",
        )
        self.assertContains(respuesta, "Definir destino")
        mock_preparar.assert_not_called()
        mock_writer.assert_not_called()

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_seleccionar_liquidacion_guarda_genova(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        respuesta = self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "liquidacion",
                "destino_manual": "",
                "motivo": "Se confirma GENOVA",
            },
        )
        self.assertEqual(respuesta.status_code, 302)
        procesamiento.refresh_from_db()
        self.assertEqual(
            procesamiento.destino_final,
            "GENOVA",
        )
        self.assertEqual(
            procesamiento.origen_destino_final,
            "liquidacion",
        )
        self.assertFalse(
            procesamiento.requiere_resolver_destino
        )
        self.assertTrue(procesamiento.puede_escribir)
        self.assertEqual(procesamiento.estado, "listo")
        self.assertEqual(
            ResolucionDestinoDimanno.objects.count(),
            1,
        )
        resolucion = ResolucionDestinoDimanno.objects.get()
        self.assertEqual(resolucion.destino_anterior, "")
        self.assertEqual(resolucion.destino_nuevo, "GENOVA")
        self.assertEqual(
            resolucion.origen_seleccionado,
            "liquidacion",
        )
        self.assertEqual(
            resolucion.destino_liquidacion,
            "GENOVA",
        )
        self.assertEqual(
            resolucion.destinos_despachos,
            ["LIVORNO"],
        )
        self.assertEqual(resolucion.usuario, self.usuario)
        self.assertEqual(
            resolucion.usuario_nombre,
            "Carlos Ruiz",
        )
        mock_preparar.assert_not_called()
        mock_writer.assert_not_called()

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_seleccionar_despachos_guarda_livorno(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        respuesta = self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "despachos|LIVORNO",
                "motivo": "Se confirma LIVORNO",
            },
        )
        self.assertEqual(respuesta.status_code, 302)
        procesamiento.refresh_from_db()
        self.assertEqual(
            procesamiento.destino_final,
            "LIVORNO",
        )
        self.assertEqual(
            procesamiento.origen_destino_final,
            "despachos",
        )
        mock_preparar.assert_not_called()
        mock_writer.assert_not_called()

    def test_manual_normaliza_y_vacio_rechaza(self) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        respuesta = self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "manual",
                "destino_manual": "  porto   spezia  ",
                "motivo": "Destino corregido",
            },
        )
        self.assertEqual(respuesta.status_code, 302)
        procesamiento.refresh_from_db()
        self.assertEqual(
            procesamiento.destino_final,
            "PORTO SPEZIA",
        )
        self.assertEqual(
            procesamiento.origen_destino_final,
            "manual",
        )

        procesamiento2 = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        procesamiento2.factura_corta = "5533"
        procesamiento2.save(update_fields=["factura_corta"])

        respuesta = self.cliente.post(
            self._url(procesamiento2),
            {
                "opcion_destino": "manual",
                "destino_manual": "   ",
                "motivo": "Intento vacío",
            },
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "Indique el destino manual.",
            status_code=400,
        )

    def test_rechaza_destino_despachos_no_persistido(
        self,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        respuesta = self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "despachos|ROMA",
                "motivo": "Intento inválido",
            },
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "La opción de destino no es válida.",
            status_code=400,
        )
        self.assertEqual(
            ResolucionDestinoDimanno.objects.count(),
            0,
        )

    def test_motivo_obligatorio(
        self,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        respuesta = self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "liquidacion",
                "motivo": "",
            },
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(
            respuesta,
            "Indique el motivo de la elección o corrección.",
            status_code=400,
        )
        self.assertNotContains(
            respuesta,
            "Responsable",
            status_code=400,
        )

    def test_segunda_modificacion_conserva_historial(
        self,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "despachos|LIVORNO",
                "motivo": "Primera decisión",
            },
        )
        self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "liquidacion",
                "motivo": "Segunda decisión",
            },
        )
        historial = list(
            ResolucionDestinoDimanno.objects.filter(
                procesamiento=procesamiento
            ).order_by("creado_en")
        )
        self.assertEqual(len(historial), 2)
        self.assertEqual(historial[0].destino_anterior, "")
        self.assertEqual(
            historial[0].destino_nuevo,
            "LIVORNO",
        )
        self.assertEqual(
            historial[0].origen_seleccionado,
            "despachos",
        )
        self.assertEqual(
            historial[1].destino_anterior,
            "LIVORNO",
        )
        self.assertEqual(
            historial[1].destino_nuevo,
            "GENOVA",
        )
        self.assertEqual(
            historial[1].origen_seleccionado,
            "liquidacion",
        )

    def test_post_sin_cambios_no_crea_bitacora(
        self,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
            estado="listo",
            puede_escribir=True,
            requiere_resolver_destino=False,
            destino_final="LIVORNO",
            origen_destino_final="despachos",
        )
        respuesta = self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "despachos|LIVORNO",
                "motivo": "Sin cambio real",
            },
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            ResolucionDestinoDimanno.objects.count(),
            0,
        )
        detalle = self.cliente.get(
            self._detalle(procesamiento)
        )
        self.assertContains(
            detalle,
            "No se realizó ningún cambio en el destino.",
        )

    def test_con_errores_no_queda_listo(self) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
            estado="invalido",
            errores=[
                {
                    "codigo": "x",
                    "nivel": "error",
                    "mensaje": "Error de prueba",
                }
            ],
        )
        self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "liquidacion",
                "motivo": "Con errores previos",
            },
        )
        procesamiento.refresh_from_db()
        self.assertFalse(
            procesamiento.requiere_resolver_destino
        )
        self.assertFalse(procesamiento.puede_escribir)
        self.assertEqual(procesamiento.estado, "invalido")
        self.assertEqual(
            procesamiento.destino_final,
            "GENOVA",
        )

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_detalle_definir_y_modificar_destino(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        detalle = self.cliente.get(
            self._detalle(procesamiento)
        )
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(
            detalle,
            "Requiere definir destino",
        )
        self.assertContains(
            detalle,
            "Debe definir el destino antes de generar el archivo.",
        )
        self.assertContains(detalle, "Definir destino")
        self.assertNotContains(
            detalle,
            ">requiere_destino<",
        )
        mock_preparar.assert_not_called()
        mock_writer.assert_not_called()

        self.cliente.post(
            self._url(procesamiento),
            {
                "opcion_destino": "despachos|LIVORNO",
                "motivo": "Definición inicial",
            },
        )
        mock_preparar.reset_mock()
        mock_writer.reset_mock()
        detalle = self.cliente.get(
            self._detalle(procesamiento)
        )
        self.assertContains(detalle, "Modificar destino")
        self.assertContains(detalle, "Despachos")
        self.assertContains(detalle, "LIVORNO")
        self.assertContains(detalle, "Listo")
        self.assertNotContains(
            detalle,
            "Debe definir el destino antes de generar el archivo.",
        )
        mock_preparar.assert_not_called()
        mock_writer.assert_not_called()

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_sin_diferencia_destino_sigue_ok(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        respuesta = self.cliente.post(
            "/procesamientos/dimanno/",
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
        self.assertEqual(respuesta.status_code, 302)
        procesamiento = ProcesamientoDimanno.objects.get()
        self.assertEqual(
            procesamiento.origen_destino_final,
            "coincidente",
        )
        self.assertFalse(
            procesamiento.requiere_resolver_destino
        )
        self.assertTrue(procesamiento.puede_escribir)
        detalle = self.cliente.get(
            self._detalle(procesamiento)
        )
        self.assertContains(
            detalle,
            "Coincidencia automática",
        )
        self.assertContains(detalle, "Modificar destino")
        self.assertNotContains(
            detalle,
            "Debe definir el destino antes de generar el archivo.",
        )


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class PruebasAutenticacionDimanno(TestCase):
    def setUp(self) -> None:
        self.media_dir = tempfile.mkdtemp(prefix="media_auth_")
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
        self.usuario = User.objects.create_user(
            username="auth_user",
            password="clave-segura-123",
            first_name="Laura",
            last_name="Mora",
        )
        self.usuario_sin_nombre = User.objects.create_user(
            username="solo_username",
            password="clave-segura-123",
        )

    def test_get_login_200(self) -> None:
        respuesta = self.cliente.get(
            "/cuentas/iniciar-sesion/"
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Iniciar sesión")

    def test_login_correcto_e_incorrecto(self) -> None:
        ok = self.cliente.post(
            "/cuentas/iniciar-sesion/",
            {
                "username": "auth_user",
                "password": "clave-segura-123",
            },
        )
        self.assertEqual(ok.status_code, 302)
        # Tras login debe ir al panel (/procesamientos/), no a Di Manno.
        location = ok["Location"]
        self.assertTrue(
            location.rstrip("/").endswith("/procesamientos")
            or location.endswith("/procesamientos/")
        )
        self.assertNotIn("/dimanno/", location)

        self.cliente.logout()
        malo = self.cliente.post(
            "/cuentas/iniciar-sesion/",
            {
                "username": "auth_user",
                "password": "incorrecta",
            },
        )
        self.assertEqual(malo.status_code, 200)
        self.assertTrue(malo.context["form"].errors)

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_anonimo_redirige_con_next(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        rutas = [
            "/procesamientos/",
            "/procesamientos/dimanno/",
            f"/procesamientos/dimanno/{procesamiento.id}/",
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/destino/resolver/"
            ),
        ]
        for ruta in rutas:
            respuesta = self.cliente.get(ruta)
            self.assertEqual(respuesta.status_code, 302)
            self.assertIn(
                "/cuentas/iniciar-sesion/",
                respuesta["Location"],
            )
            self.assertIn("next=", respuesta["Location"])
        mock_preparar.assert_not_called()
        mock_writer.assert_not_called()

    def test_autenticado_abre_vistas(self) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        panel = self.cliente.get("/procesamientos/")
        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, "Panel de control")
        self.assertContains(panel, "Di Manno")
        self.assertContains(panel, "Abrir módulo")
        self.assertEqual(
            self.cliente.get(
                "/procesamientos/dimanno/"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.cliente.get(
                f"/procesamientos/dimanno/{procesamiento.id}/"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.cliente.get(
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.cliente.get(
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/destino/resolver/"
            ).status_code,
            200,
        )

    def test_formularios_sin_responsable(self) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        gastos = self.cliente.get(
            f"/procesamientos/dimanno/"
            f"{procesamiento.id}/gastos/editar/"
        )
        destino = self.cliente.get(
            f"/procesamientos/dimanno/"
            f"{procesamiento.id}/destino/resolver/"
        )
        self.assertNotContains(gastos, "Responsable")
        self.assertNotContains(destino, "Responsable")
        self.assertNotContains(gastos, 'name="responsable"')
        self.assertNotContains(destino, 'name="responsable"')
        self.assertContains(
            gastos,
            "La corrección quedará registrada a nombre de:",
        )
        self.assertContains(
            destino,
            "La decisión quedará registrada a nombre de:",
        )

    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_post_manipulado_ignora_responsable_y_usuario(
        self,
        mock_preparar,
    ) -> None:
        mock_preparar.return_value = _procesamiento_listo()
        self.cliente.force_login(self.usuario)
        self.cliente.post(
            "/procesamientos/dimanno/",
            {
                "anio": "2026",
                "nombre_hoja": "FT 5292 W15",
                "archivo_despachos": _xlsx_falso("d.xlsx"),
                "archivo_liquidacion": _xlsx_falso("l.xlsx"),
                "archivo_cliente": _xlsx_falso("c.xlsx"),
                "creado_por": str(self.usuario_sin_nombre.id),
                "creado_por_nombre": "Falsificado",
            },
        )
        procesamiento = ProcesamientoDimanno.objects.get()
        self.assertEqual(procesamiento.creado_por, self.usuario)
        self.assertEqual(
            procesamiento.creado_por_nombre,
            "Laura Mora",
        )

        gastos = list(procesamiento.gastos.order_by("orden"))
        datos = {
            "motivo": "Ajuste autenticado",
            "responsable": "OtroUsuario",
            "usuario": str(self.usuario_sin_nombre.id),
            "form-TOTAL_FORMS": str(len(gastos)),
            "form-INITIAL_FORMS": str(len(gastos)),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for indice, gasto in enumerate(gastos):
            datos[f"form-{indice}-id"] = str(gasto.id)
            if gasto.codigo == "transporte":
                datos[f"form-{indice}-valor_aplicado"] = "4500"
            else:
                datos[f"form-{indice}-valor_aplicado"] = format(
                    gasto.valor_aplicado, "f"
                )
        self.cliente.post(
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/gastos/editar/"
            ),
            datos,
        )
        correccion = CorreccionGastoDimanno.objects.get()
        self.assertEqual(correccion.usuario, self.usuario)
        self.assertEqual(
            correccion.usuario_nombre,
            "Laura Mora",
        )
        self.assertNotEqual(
            correccion.usuario_nombre,
            "OtroUsuario",
        )

        destino = _crear_procesamiento_destino(
            media_dir=self.media_dir,
        )
        self.cliente.post(
            (
                f"/procesamientos/dimanno/"
                f"{destino.id}/destino/resolver/"
            ),
            {
                "opcion_destino": "despachos|LIVORNO",
                "motivo": "Decisión autenticada",
                "responsable": "OtroUsuario",
                "usuario": str(self.usuario_sin_nombre.id),
            },
        )
        resolucion = ResolucionDestinoDimanno.objects.get(
            procesamiento=destino
        )
        self.assertEqual(resolucion.usuario, self.usuario)
        self.assertEqual(
            resolucion.usuario_nombre,
            "Laura Mora",
        )

    def test_usuario_nombre_usa_username_sin_nombre(
        self,
    ) -> None:
        self.assertEqual(
            obtener_nombre_usuario(self.usuario_sin_nombre),
            "solo_username",
        )
        self.assertEqual(
            obtener_nombre_usuario(self.usuario),
            "Laura Mora",
        )

    def test_logout_post_cierra_get_no(self) -> None:
        self.cliente.force_login(self.usuario)
        get_logout = self.cliente.get(
            "/cuentas/cerrar-sesion/"
        )
        self.assertIn(get_logout.status_code, (405, 302))
        if get_logout.status_code == 405:
            self.assertTrue(
                self.cliente.session.get("_auth_user_id")
            )

        post_logout = self.cliente.post(
            "/cuentas/cerrar-sesion/"
        )
        self.assertEqual(post_logout.status_code, 302)
        self.assertIsNone(
            self.cliente.session.get("_auth_user_id")
        )

    def test_historial_usuario_none_consultable(
        self,
    ) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_destino(
            media_dir=self.media_dir,
            estado="listo",
            puede_escribir=True,
            requiere_resolver_destino=False,
            destino_final="LIVORNO",
            origen_destino_final="despachos",
        )
        ResolucionDestinoDimanno.objects.create(
            procesamiento=procesamiento,
            destino_anterior="",
            destino_nuevo="LIVORNO",
            origen_seleccionado="despachos",
            destino_liquidacion="GENOVA",
            destinos_despachos=["LIVORNO"],
            motivo="Histórico anónimo",
            usuario=None,
            usuario_nombre="Operador Antiguo",
        )
        self.assertEqual(
            ResolucionDestinoDimanno.objects.filter(
                usuario__isnull=True
            ).count(),
            1,
        )
        detalle = self.cliente.get(
            f"/procesamientos/dimanno/{procesamiento.id}/"
        )
        self.assertEqual(detalle.status_code, 200)
