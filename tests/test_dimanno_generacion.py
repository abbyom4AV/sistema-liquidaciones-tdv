from __future__ import annotations

import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.test.utils import (
    setup_databases,
    setup_test_environment,
    teardown_databases,
    teardown_test_environment,
)

from procesamientos.models import (
    GastoProcesamientoDimanno,
    GeneracionDimanno,
    ProcesamientoDimanno,
    RUBROS_GASTOS_DEFINICION,
)
from procesamientos.services.generacion_dimanno import (
    NOMBRE_DESCARGA_DIMANNO,
    aplicar_confirmaciones_a_procesamiento,
    construir_nombre_descarga,
    deserializar_gastos_aplicados,
    serializar_gastos_aplicados,
)
from procesamientos.services.trabajador_generacion import (
    WorkerLock,
    WorkerLockError,
    procesar_generacion,
    reclamar_siguiente_generacion,
)
from services.dimanno.extractor import LiquidacionDimanno
from services.dimanno.matcher import LineaDespacho, ResultadoMatcher
from services.dimanno.processor import ResultadoPreparacionDimanno
from services.dimanno.validator import (
    LineaPreparada,
    ResultadoValidacion,
)
from services.dimanno.writer import ResultadoEscrituraDimanno

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


def _gastos_decimal() -> dict[str, Decimal]:
    return {
        "Comisión": Decimal("4258.9832"),
        "Flete Eu": Decimal("0"),
        "Control calidad Eu": Decimal("146.36"),
        "THC": Decimal("830"),
        "Transporte": Decimal("4600"),
        "Aduanas": Decimal("272.63"),
    }


def _resultado_motor(
    *,
    gastos: dict[str, Decimal] | None = None,
) -> ResultadoPreparacionDimanno:
    if gastos is None:
        gastos = _gastos_decimal()
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


def _crear_procesamiento_listo(media_dir: str) -> ProcesamientoDimanno:
    procesamiento = ProcesamientoDimanno(
        anio=2026,
        nombre_hoja="FT 5292 W15",
        factura_corta="5292",
        semana=15,
        estado="listo",
        destino_liquidacion="VADO LIGURE",
        destinos_despachos=["VADO LIGURE"],
        destino_final="VADO LIGURE",
        origen_destino_final="coincidente",
        puede_escribir=True,
        requiere_resolver_destino=False,
        errores=[],
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
    for codigo, nombre, orden in RUBROS_GASTOS_DEFINICION:
        valor = _gastos_decimal()[nombre]
        GastoProcesamientoDimanno.objects.create(
            procesamiento=procesamiento,
            codigo=codigo,
            nombre=nombre,
            orden=orden,
            valor_original=valor,
            valor_aplicado=valor,
        )
    return procesamiento


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class PruebasGeneracionDimanno(TestCase):
    def setUp(self) -> None:
        self.media_dir = tempfile.mkdtemp(prefix="media_gen_")
        self.lock_dir = tempfile.mkdtemp(prefix="lock_gen_")
        self.override = override_settings(
            MEDIA_ROOT=self.media_dir,
            BASE_DIR=Path(self.lock_dir),
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(
            lambda: shutil.rmtree(
                self.media_dir,
                ignore_errors=True,
            )
        )
        self.addCleanup(
            lambda: shutil.rmtree(
                self.lock_dir,
                ignore_errors=True,
            )
        )
        self.cliente = Client()
        self.usuario = User.objects.create_user(
            username="generador",
            password="clave-segura-123",
            first_name="Ana",
            last_name="López",
        )

    def test_serializa_gastos_incluyendo_cero(self) -> None:
        serializados = serializar_gastos_aplicados(
            _gastos_decimal()
        )
        self.assertEqual(serializados["Flete Eu"], "0")
        deserializados = deserializar_gastos_aplicados(
            serializados
        )
        self.assertEqual(
            deserializados["Flete Eu"],
            Decimal("0"),
        )
        self.assertIsInstance(
            deserializados["Flete Eu"],
            Decimal,
        )

    def test_aplicar_confirmaciones_usa_snapshot(
        self,
    ) -> None:
        motor = _resultado_motor()
        gastos = serializar_gastos_aplicados(
            {
                **_gastos_decimal(),
                "Transporte": Decimal("0"),
            }
        )
        adaptado = aplicar_confirmaciones_a_procesamiento(
            motor,
            destino_final="GENOVA",
            gastos_aplicados=gastos,
            origen_destino="manual",
        )
        self.assertEqual(adaptado.destino_final, "GENOVA")
        self.assertEqual(
            adaptado.liquidacion.gastos["Transporte"],
            Decimal("0"),
        )
        self.assertIsInstance(
            adaptado.liquidacion.gastos["Transporte"],
            Decimal,
        )
        self.assertTrue(adaptado.puede_escribir)

    def test_anonimo_no_solicita(self) -> None:
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        respuesta = self.cliente.post(
            f"/procesamientos/dimanno/{procesamiento.id}/generar/"
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(
            "/cuentas/iniciar-sesion/",
            respuesta["Location"],
        )
        self.assertEqual(
            GeneracionDimanno.objects.count(),
            0,
        )

    @patch("services.dimanno.writer.escribir_archivo_dimanno")
    @patch(
        "procesamientos.views.preparar_procesamiento_dimanno"
    )
    def test_solicitud_valida_crea_snapshot(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        respuesta = self.cliente.post(
            f"/procesamientos/dimanno/{procesamiento.id}/generar/"
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            GeneracionDimanno.objects.count(),
            1,
        )
        generacion = GeneracionDimanno.objects.get()
        self.assertEqual(
            generacion.solicitado_por,
            self.usuario,
        )
        self.assertEqual(
            generacion.solicitado_por_nombre,
            "Ana López",
        )
        self.assertEqual(
            generacion.destino_aplicado,
            "VADO LIGURE",
        )
        self.assertEqual(
            generacion.gastos_aplicados["Flete Eu"],
            "0",
        )
        self.assertEqual(len(generacion.gastos_aplicados), 6)
        mock_writer.assert_not_called()
        mock_preparar.assert_not_called()
        self.assertIn(
            f"/generaciones/{generacion.id}/",
            respuesta["Location"],
        )

    def test_get_no_genera(self) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        respuesta = self.cliente.get(
            f"/procesamientos/dimanno/{procesamiento.id}/generar/"
        )
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(
            GeneracionDimanno.objects.count(),
            0,
        )

    def test_invalido_y_destino_pendiente(
        self,
    ) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        procesamiento.puede_escribir = False
        procesamiento.errores = [
            {
                "codigo": "x",
                "nivel": "error",
                "mensaje": "Error",
            }
        ]
        procesamiento.save()
        self.cliente.post(
            f"/procesamientos/dimanno/{procesamiento.id}/generar/"
        )
        self.assertEqual(
            GeneracionDimanno.objects.count(),
            0,
        )

        procesamiento.errores = []
        procesamiento.puede_escribir = False
        procesamiento.requiere_resolver_destino = True
        procesamiento.destino_final = ""
        procesamiento.save()
        self.cliente.post(
            f"/procesamientos/dimanno/{procesamiento.id}/generar/"
        )
        self.assertEqual(
            GeneracionDimanno.objects.count(),
            0,
        )

    def test_no_duplica_generacion_activa(self) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        primera = self.cliente.post(
            f"/procesamientos/dimanno/{procesamiento.id}/generar/"
        )
        generacion = GeneracionDimanno.objects.get()
        segunda = self.cliente.post(
            f"/procesamientos/dimanno/{procesamiento.id}/generar/"
        )
        self.assertEqual(
            GeneracionDimanno.objects.count(),
            1,
        )
        self.assertIn(
            str(generacion.id),
            segunda["Location"],
        )
        self.assertEqual(primera.status_code, 302)

    def test_detalle_estados_y_descarga(
        self,
    ) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        generacion = GeneracionDimanno.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionDimanno.Estado.PENDIENTE,
            solicitado_por=self.usuario,
            solicitado_por_nombre="Ana López",
            destino_aplicado="VADO LIGURE",
            gastos_aplicados=serializar_gastos_aplicados(
                _gastos_decimal()
            ),
        )
        detalle = self.cliente.get(
            f"/procesamientos/dimanno/generaciones/{generacion.id}/"
        )
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, "Pendiente")

        generacion.estado = GeneracionDimanno.Estado.PROCESANDO
        generacion.save(update_fields=["estado"])
        self.assertContains(
            self.cliente.get(
                f"/procesamientos/dimanno/generaciones/{generacion.id}/"
            ),
            "Procesando",
        )

        descarga = self.cliente.get(
            (
                f"/procesamientos/dimanno/generaciones/"
                f"{generacion.id}/descargar/"
            )
        )
        self.assertEqual(descarga.status_code, 404)

        generacion.estado = GeneracionDimanno.Estado.ERROR
        generacion.mensaje_error = "Fallo controlado"
        generacion.save(
            update_fields=["estado", "mensaje_error"]
        )
        error_detalle = self.cliente.get(
            f"/procesamientos/dimanno/generaciones/{generacion.id}/"
        )
        self.assertContains(error_detalle, "Error")
        self.assertContains(
            error_detalle,
            "No fue posible generar el archivo.",
        )
        self.assertEqual(
            self.cliente.get(
                (
                    f"/procesamientos/dimanno/generaciones/"
                    f"{generacion.id}/descargar/"
                )
            ).status_code,
            404,
        )

    def test_descarga_completada(self) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        generacion = GeneracionDimanno.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionDimanno.Estado.COMPLETADO,
            solicitado_por=self.usuario,
            solicitado_por_nombre="Ana López",
            destino_aplicado="VADO LIGURE",
            gastos_aplicados=serializar_gastos_aplicados(
                _gastos_decimal()
            ),
            nombre_descarga=NOMBRE_DESCARGA_DIMANNO,
        )
        ruta = (
            Path(self.media_dir)
            / "procesamientos"
            / "dimanno"
            / str(procesamiento.id)
            / "resultados"
            / str(generacion.id)
            / "resultado.xlsx"
        )
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(b"contenido-xlsx")
        generacion.archivo_resultado.name = (
            Path("procesamientos")
            / "dimanno"
            / str(procesamiento.id)
            / "resultados"
            / str(generacion.id)
            / "resultado.xlsx"
        ).as_posix()
        generacion.save(update_fields=["archivo_resultado"])

        anonimo = Client()
        self.assertEqual(
            anonimo.get(
                (
                    f"/procesamientos/dimanno/generaciones/"
                    f"{generacion.id}/descargar/"
                )
            ).status_code,
            302,
        )

        respuesta = self.cliente.get(
            (
                f"/procesamientos/dimanno/generaciones/"
                f"{generacion.id}/descargar/"
            )
        )
        try:
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(
                generacion.nombre_descarga,
                "DIMANNO Liquidaciones v2.1.xlsx",
            )
            self.assertIn(
                "DIMANNO Liquidaciones v2.1.xlsx",
                respuesta.get("Content-Disposition", ""),
            )
            _ = b"".join(respuesta.streaming_content)
        finally:
            respuesta.close()

        self.assertTrue(ruta.name == "resultado.xlsx")
        self.assertNotContains(
            self.cliente.get(
                f"/procesamientos/dimanno/generaciones/{generacion.id}/"
            ),
            str(ruta),
        )

        otra = GeneracionDimanno.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionDimanno.Estado.COMPLETADO,
            solicitado_por=self.usuario,
            solicitado_por_nombre="Ana López",
            destino_aplicado="VADO LIGURE",
            gastos_aplicados=serializar_gastos_aplicados(
                _gastos_decimal()
            ),
            nombre_descarga=NOMBRE_DESCARGA_DIMANNO,
        )
        ruta_otra = (
            Path(self.media_dir)
            / "procesamientos"
            / "dimanno"
            / str(procesamiento.id)
            / "resultados"
            / str(otra.id)
            / "resultado.xlsx"
        )
        ruta_otra.parent.mkdir(parents=True, exist_ok=True)
        ruta_otra.write_bytes(b"otro-xlsx")
        otra.archivo_resultado.name = (
            Path("procesamientos")
            / "dimanno"
            / str(procesamiento.id)
            / "resultados"
            / str(otra.id)
            / "resultado.xlsx"
        ).as_posix()
        otra.save(update_fields=["archivo_resultado"])
        self.assertNotEqual(ruta, ruta_otra)
        self.assertEqual(
            construir_nombre_descarga(
                anio=2026,
                semana=15,
                factura_corta="5292",
            ),
            NOMBRE_DESCARGA_DIMANNO,
        )
        self.assertEqual(
            otra.nombre_descarga,
            NOMBRE_DESCARGA_DIMANNO,
        )

    def test_detalle_muestra_generar(self) -> None:
        self.cliente.force_login(self.usuario)
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        detalle = self.cliente.get(
            f"/procesamientos/dimanno/{procesamiento.id}/"
        )
        self.assertContains(detalle, "Generar archivo")
        self.assertContains(
            detalle,
            (
                f"/procesamientos/dimanno/"
                f"{procesamiento.id}/generar/"
            ),
        )

        procesamiento.puede_escribir = False
        procesamiento.estado = "invalido"
        procesamiento.errores = [
            {
                "codigo": "x",
                "nivel": "error",
                "mensaje": "Error",
            }
        ]
        procesamiento.save()
        detalle = self.cliente.get(
            f"/procesamientos/dimanno/{procesamiento.id}/"
        )
        self.assertNotContains(
            detalle,
            "Generar archivo",
        )

    @patch(
        "procesamientos.services.trabajador_generacion"
        ".escribir_archivo_dimanno"
    )
    @patch(
        "procesamientos.services.trabajador_generacion"
        ".preparar_procesamiento_dimanno"
    )
    def test_trabajador_once_completa(
        self,
        mock_preparar,
        mock_writer,
    ) -> None:
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        gastos_mod = {
            **_gastos_decimal(),
            "Transporte": Decimal("0"),
        }
        generacion = GeneracionDimanno.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionDimanno.Estado.PENDIENTE,
            solicitado_por=self.usuario,
            solicitado_por_nombre="Ana López",
            destino_aplicado="GENOVA",
            origen_destino_aplicado="manual",
            gastos_aplicados=serializar_gastos_aplicados(
                gastos_mod
            ),
        )
        mock_preparar.return_value = _resultado_motor()

        def _escribir(procesamiento, ruta_archivo_cliente, ruta_salida, recalcular_al_final=False):
            salida = Path(ruta_salida)
            self.assertNotEqual(
                Path(ruta_archivo_cliente).resolve(),
                salida.resolve(),
            )
            self.assertEqual(
                procesamiento.destino_final,
                "GENOVA",
            )
            self.assertEqual(
                procesamiento.liquidacion.gastos["Transporte"],
                Decimal("0"),
            )
            self.assertIsInstance(
                procesamiento.liquidacion.gastos["Transporte"],
                Decimal,
            )
            self.assertFalse(recalcular_al_final)
            salida.parent.mkdir(parents=True, exist_ok=True)
            salida.write_bytes(b"resultado")
            return ResultadoEscrituraDimanno(
                archivo_origen=str(ruta_archivo_cliente),
                archivo_salida=str(salida),
                filas_agregadas=5,
                fila_inicial=10,
                fila_final=14,
                destino_final="GENOVA",
                factura_corta="5292",
                semana=15,
                anio=2026,
                rango_tabla="A1:Z20",
            )

        mock_writer.side_effect = _escribir
        call_command("procesar_generaciones_dimanno", "--once")
        generacion.refresh_from_db()
        self.assertEqual(
            generacion.estado,
            GeneracionDimanno.Estado.COMPLETADO,
        )
        self.assertEqual(generacion.filas_agregadas, 5)
        self.assertEqual(generacion.rango_tabla, "A1:Z20")
        self.assertTrue(generacion.archivo_resultado)
        self.assertEqual(
            generacion.nombre_descarga,
            NOMBRE_DESCARGA_DIMANNO,
        )
        mock_writer.assert_called_once()
        self.assertTrue(
            Path(procesamiento.archivo_cliente.path).is_file()
        )
        self.assertEqual(
            Path(generacion.archivo_resultado.path).name,
            "resultado.xlsx",
        )

    @patch(
        "procesamientos.services.trabajador_generacion"
        ".escribir_archivo_dimanno",
        side_effect=Exception("boom"),
    )
    @patch(
        "procesamientos.services.trabajador_generacion"
        ".preparar_procesamiento_dimanno"
    )
    def test_trabajador_error_no_deja_procesando(
        self,
        mock_preparar,
        _mock_writer,
    ) -> None:
        mock_preparar.return_value = _resultado_motor()
        procesamiento = _crear_procesamiento_listo(
            self.media_dir
        )
        generacion = GeneracionDimanno.objects.create(
            procesamiento=procesamiento,
            estado=GeneracionDimanno.Estado.PENDIENTE,
            solicitado_por=self.usuario,
            solicitado_por_nombre="Ana López",
            destino_aplicado="VADO LIGURE",
            gastos_aplicados=serializar_gastos_aplicados(
                _gastos_decimal()
            ),
        )
        reclamada = reclamar_siguiente_generacion()
        self.assertEqual(
            reclamada.estado,
            GeneracionDimanno.Estado.PROCESANDO,
        )
        procesar_generacion(reclamada)
        generacion.refresh_from_db()
        self.assertEqual(
            generacion.estado,
            GeneracionDimanno.Estado.ERROR,
        )
        self.assertTrue(generacion.finalizado_en)
        self.assertTrue(
            Path(procesamiento.archivo_cliente.path).is_file()
        )

    def test_lock_trabajador(self) -> None:
        with WorkerLock():
            with self.assertRaises(WorkerLockError):
                with WorkerLock():
                    pass
        ruta = Path(self.lock_dir) / "runtime" / "dimanno_worker.lock"
        self.assertFalse(ruta.exists())
