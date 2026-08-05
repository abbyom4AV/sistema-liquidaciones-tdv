from __future__ import annotations

import contextvars
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pywintypes import com_error
import xlwings as xw

from services.dimanno.matcher import (
    interpretar_semana,
    normalizar_texto,
)
from services.dimanno.writer import (
    cerrar_aplicacion_excel,
    copiar_rango_con_reintentos,
    decimal_a_excel,
    esperar_excel_listo,
    escribir_valores_bloque,
    ErrorEscrituraDimanno,
    guardar_libro_con_reintentos,
    normalizar_factura_corta,
    recalcular_dirigido_con_fallback,
    rellenar_hacia_abajo_con_reintentos,
)
from services.master.processor import (
    ResultadoPreparacionMaster,
)

logger = logging.getLogger(__name__)

_contexto_generacion: contextvars.ContextVar[str] = (
    contextvars.ContextVar(
        "master_generacion_id",
        default="-",
    )
)

HOJA_RAW_DATA = "Raw Data"
TABLA_RAW_DATA = "Tabla1"
NOMBRE_DESCARGA_MASTER = "Master Liquidaciones (1).xlsx"

COLUMNAS_ENTRADA = {
    "Semana",
    "Año",
    "Cliente",
    "Nave",
    "Contenedor ",
    "Destino",
    "Tipo de fruta",
    "Cartón",
    "# Calibre",
    "Total Cajas",
    "Merma",
    "LC Euros",
    "Cust.C Euros",
    "Import.D Euros",
    "Ener&Demur. Euros",
    "Inspection Euros",
    "Transport.P-W Euros",
    "Transport C. Euros",
    "Relabelling Euros",
    "Comision Euros",
    "Precio de Venta €",
}

COLUMNAS_FORMULA_CRITICAS: tuple[str, ...] = (
    "Contar Fcls",
    "Fact. 4 Digitos",
    "Total Cajas Netas",
    "Total Venta €",
    "Comision %",
    "Comision €",
    "Comision Formula",
    "GED Total  €",
    "Retorno  EXW €",
    "Precio de Venta $",
    "Local Charges",
)


class ErrorEscrituraMaster(Exception):
    """Error general al escribir el acumulativo Master."""


class EstructuraRawDataMasterError(ErrorEscrituraMaster):
    """Tabla1 no tiene la estructura esperada."""


class LiquidacionDuplicadaMasterError(ErrorEscrituraMaster):
    """La liquidación ya está en Raw Data."""


class ProcesamientoMasterNoListoError(ErrorEscrituraMaster):
    """El procesamiento no está listo para escribir."""


@dataclass(frozen=True)
class ResultadoEscrituraMaster:
    archivo_origen: str
    archivo_salida: str
    filas_agregadas: int
    fila_inicial: int
    fila_final: int
    destino_final: str
    factura_corta: str
    semana: int
    anio: int
    rango_tabla: str


def establecer_contexto_generacion(generacion_id: str):
    return _contexto_generacion.set(str(generacion_id))


def limpiar_contexto_generacion(token) -> None:
    _contexto_generacion.reset(token)


def _log_fase(fase: str, segundos: float) -> None:
    logger.info(
        "generacion_master=%s fase=%s segundos=%.3f",
        _contexto_generacion.get(),
        fase,
        segundos,
    )


def obtener_encabezados_tabla_master(
    tabla: Any,
) -> tuple[list[str], dict[str, int], set[str]]:
    encabezados: list[str] = []
    posiciones: dict[str, int] = {}

    for indice in range(1, tabla.ListColumns.Count + 1):
        nombre = str(tabla.ListColumns(indice).Name)
        if nombre in posiciones:
            raise EstructuraRawDataMasterError(
                f"El encabezado {nombre!r} está repetido."
            )
        encabezados.append(nombre)
        posiciones[nombre] = indice

    encontrados = set(encabezados)
    faltantes = COLUMNAS_ENTRADA - encontrados
    if faltantes:
        raise EstructuraRawDataMasterError(
            "Faltan columnas digitadas en Tabla1: "
            + ", ".join(repr(n) for n in sorted(faltantes))
        )

    formula_cols = encontrados - COLUMNAS_ENTRADA
    return encabezados, posiciones, formula_cols


def validar_formulas_ultima_fila_master(
    tabla: Any,
    posiciones: dict[str, int],
    columnas_formula: set[str],
) -> None:
    cuerpo = tabla.DataBodyRange
    if cuerpo is None or cuerpo.Rows.Count < 1:
        raise EstructuraRawDataMasterError(
            "Tabla1 no tiene fila plantilla de fórmulas."
        )

    ultima = cuerpo.Rows(cuerpo.Rows.Count)
    sin_formula: list[str] = []
    for nombre in sorted(columnas_formula):
        if nombre not in posiciones:
            continue
        celda = ultima.Cells(1, posiciones[nombre])
        if not bool(celda.HasFormula):
            # Algunas columnas auxiliares pueden quedar vacías.
            if nombre in COLUMNAS_FORMULA_CRITICAS:
                sin_formula.append(nombre)

    if sin_formula:
        raise EstructuraRawDataMasterError(
            "La última fila no tiene fórmula en: "
            + ", ".join(repr(n) for n in sin_formula)
        )


def existe_liquidacion_duplicada_master(
    tabla: Any,
    posiciones: dict[str, int],
    anio: int,
    semana: int,
    factura_corta: str,
) -> bool:
    cuerpo = tabla.DataBodyRange
    if cuerpo is None:
        return False

    filas = int(cuerpo.Rows.Count)
    if filas < 1:
        return False

    factura_buscada = normalizar_factura_corta(factura_corta)

    def _col(nombre: str) -> list[Any]:
        crudo = cuerpo.Columns(posiciones[nombre]).Value2
        if filas <= 1:
            return [crudo]
        return [
            fila[0] if isinstance(fila, (list, tuple)) else fila
            for fila in crudo
        ]

    valores_anio = _col("Año")
    valores_semana = _col("Semana")
    valores_factura = _col("Fact. 4 Digitos")

    for i in range(filas):
        try:
            anio_existente = int(float(valores_anio[i]))
        except (TypeError, ValueError):
            continue

        try:
            semana_existente, anio_en_semana = (
                interpretar_semana(valores_semana[i])
            )
        except Exception:
            continue

        if anio_en_semana is not None and anio_en_semana != anio:
            continue

        if (
            anio_existente == anio
            and semana_existente == semana
            and normalizar_factura_corta(valores_factura[i])
            == factura_buscada
        ):
            return True

    return False


def construir_valores_fila_master(
    procesamiento: ResultadoPreparacionMaster,
    indice_linea: int,
) -> dict[str, Any]:
    linea = procesamiento.validacion.lineas_preparadas[
        indice_linea
    ]
    despacho = linea.despacho
    gastos = linea.gastos

    if procesamiento.destino_final is None:
        raise ProcesamientoMasterNoListoError(
            "No hay destino final de Despachos."
        )

    semana_texto = despacho.semana_texto or (
        f"{despacho.semana:02d}-{despacho.anio}"
    )

    return {
        "Semana": semana_texto,
        "Año": despacho.anio,
        "Cliente": despacho.cliente,
        "Nave": despacho.barco,
        "Contenedor ": despacho.contenedor,
        "Destino": procesamiento.destino_final,
        "Tipo de fruta": linea.tipo_fruta,
        "Cartón": despacho.carton,
        "# Calibre": despacho.calibre,
        "Total Cajas": despacho.total_cajas,
        "Merma": linea.merma if linea.merma else None,
        "LC Euros": decimal_a_excel(gastos["LC Euros"]),
        "Cust.C Euros": decimal_a_excel(
            gastos["Cust.C Euros"]
        ),
        "Import.D Euros": decimal_a_excel(
            gastos["Import.D Euros"]
        ),
        "Ener&Demur. Euros": decimal_a_excel(
            gastos["Ener&Demur. Euros"]
        ),
        "Inspection Euros": decimal_a_excel(
            gastos["Inspection Euros"]
        ),
        "Transport.P-W Euros": decimal_a_excel(
            gastos["Transport.P-W Euros"]
        ),
        "Transport C. Euros": decimal_a_excel(
            gastos["Transport C. Euros"]
        ),
        "Relabelling Euros": decimal_a_excel(
            gastos["Relabelling Euros"]
        ),
        "Comision Euros": decimal_a_excel(
            gastos["Comision Euros"]
        ),
        # Texto decimal completo: Excel lo convierte a número
        # sin el redondeo a 2 decimales del pipeline antiguo.
        "Precio de Venta €": format(
            linea.precio_venta_eur,
            "f",
        ),
    }


def redimensionar_tabla_master(
    tabla: Any,
    nuevo_rango: Any,
    aplicacion: xw.App,
    maximo_intentos: int = 8,
) -> None:
    """Expande Tabla1 en un solo Resize (más estable que Add)."""
    ultimo_error: Exception | None = None

    for intento in range(1, maximo_intentos + 1):
        try:
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=90,
            )
            tabla.Resize(nuevo_rango)
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=90,
            )
            return
        except (com_error, ErrorEscrituraDimanno) as error:
            ultimo_error = error
            time.sleep(1.0 * intento)

    raise ErrorEscrituraMaster(
        "Excel no permitió redimensionar Tabla1 "
        f"después de {maximo_intentos} intentos. "
        "Cierre Excel/Master Liquidaciones si está abierto "
        "e intente de nuevo."
    ) from ultimo_error


def _desactivar_refresco_pivots(libro: xw.Book) -> None:
    try:
        caches = libro.api.PivotCaches()
        total = int(caches.Count)
    except (com_error, AttributeError, TypeError, ValueError):
        return

    for indice in range(1, total + 1):
        try:
            caches(indice).EnableRefresh = False
        except com_error:
            continue


def _propagar_formulas_columnas(
    fila_plantilla: Any,
    rango_nuevas: Any,
    posiciones: dict[str, int],
    columnas_formula: set[str],
    aplicacion: xw.App,
) -> None:
    """
    Propaga fórmulas a las filas nuevas.

    1) Copy + FillDown (como Di Manno), con AutoFill de listas
       desactivado para no sincronizar toda Tabla1.
    2) Repara columnas de fórmula que queden vacías (PasteSpecial
       / FillDown en tablas a menudo omite Comision %, Local
       Charges, etc.).
    """
    filas = int(rango_nuevas.Rows.Count)
    autofill_previo: bool | None = None
    try:
        autofill_previo = bool(
            aplicacion.api.AutoCorrect.AutoFillFormulasInLists
        )
        aplicacion.api.AutoCorrect.AutoFillFormulasInLists = False
    except (com_error, AttributeError):
        autofill_previo = None

    try:
        esperar_excel_listo(
            aplicacion=aplicacion,
            timeout_segundos=60,
        )

        primera = (
            rango_nuevas
            if filas == 1
            else rango_nuevas.Rows(1)
        )
        try:
            copiar_rango_con_reintentos(
                origen=fila_plantilla,
                destino=primera,
                aplicacion=aplicacion,
            )
            if filas > 1:
                rellenar_hacia_abajo_con_reintentos(
                    rango=rango_nuevas,
                    aplicacion=aplicacion,
                )
            try:
                aplicacion.api.CutCopyMode = False
            except com_error:
                pass
        except ErrorEscrituraDimanno:
            logger.warning(
                "generacion_master=%s Copy+FillDown falló; "
                "se reparará columna a columna",
                _contexto_generacion.get(),
            )
            try:
                aplicacion.api.CutCopyMode = False
            except com_error:
                pass

        reparadas = 0
        ultima = (
            rango_nuevas
            if filas == 1
            else rango_nuevas.Rows(filas)
        )
        for nombre in sorted(columnas_formula):
            if nombre not in posiciones:
                continue
            indice = posiciones[nombre]
            try:
                formula = fila_plantilla.Cells(
                    1, indice
                ).FormulaR1C1
            except com_error:
                continue
            if not formula:
                continue

            try:
                ya_tiene = bool(
                    ultima.Cells(1, indice).HasFormula
                )
            except com_error:
                ya_tiene = False
            if ya_tiene:
                continue

            destino = rango_nuevas.Cells(1, indice).Resize(
                filas, 1
            )
            try:
                destino.FormulaR1C1 = formula
                reparadas += 1
                continue
            except com_error:
                pass

            for fila_i in range(1, filas + 1):
                try:
                    rango_nuevas.Cells(
                        fila_i, indice
                    ).FormulaR1C1 = formula
                except com_error:
                    continue
            reparadas += 1

        if reparadas:
            logger.info(
                "generacion_master=%s formulas_reparadas=%s",
                _contexto_generacion.get(),
                reparadas,
            )

        esperar_excel_listo(
            aplicacion=aplicacion,
            timeout_segundos=90,
        )
    finally:
        if autofill_previo is not None:
            try:
                aplicacion.api.AutoCorrect.AutoFillFormulasInLists = (
                    autofill_previo
                )
            except (com_error, AttributeError):
                pass


def escribir_archivo_master(
    procesamiento: ResultadoPreparacionMaster,
    ruta_archivo_cliente: str | Path,
    ruta_salida: str | Path,
    recalcular_al_final: bool = False,
) -> ResultadoEscrituraMaster:
    if not procesamiento.puede_escribir:
        raise ProcesamientoMasterNoListoError(
            "El procesamiento no está listo para escribir. "
            f"Estado: {procesamiento.estado}."
        )

    if procesamiento.destino_final is None:
        raise ProcesamientoMasterNoListoError(
            "No hay destino final."
        )

    origen = Path(ruta_archivo_cliente).resolve()
    salida = Path(ruta_salida).resolve()
    inicio_total = time.perf_counter()

    if not origen.is_file():
        raise FileNotFoundError(
            f"No existe el acumulativo: {origen}"
        )
    if origen == salida:
        raise ErrorEscrituraMaster(
            "La salida no puede ser el mismo archivo de origen."
        )
    if salida.exists():
        raise FileExistsError(
            f"El archivo de salida ya existe: {salida}"
        )
    if not procesamiento.validacion.lineas_preparadas:
        raise ProcesamientoMasterNoListoError(
            "No hay líneas preparadas."
        )

    salida.parent.mkdir(parents=True, exist_ok=True)
    carpeta_trabajo = Path(
        tempfile.mkdtemp(prefix="master_write_")
    )
    salida_trabajo = carpeta_trabajo / "trabajo.xlsx"

    aplicacion: xw.App | None = None
    libro: xw.Book | None = None
    escritura_completada = False
    resultado: ResultadoEscrituraMaster | None = None

    try:
        inicio = time.perf_counter()
        shutil.copy2(origen, salida_trabajo)
        aplicacion = xw.App(visible=False, add_book=False)
        aplicacion.display_alerts = False
        aplicacion.screen_updating = False
        libro = aplicacion.books.open(
            str(salida_trabajo),
            update_links=False,
            read_only=False,
            add_to_mru=False,
        )
        aplicacion.calculation = "manual"
        aplicacion.enable_events = False
        try:
            aplicacion.api.CalculateBeforeSave = False
        except com_error:
            pass
        try:
            # Evita que Tabla1 sincronice fórmulas en miles de
            # filas al hacer Resize / pegar fórmulas.
            aplicacion.api.AutoCorrect.AutoFillFormulasInLists = (
                False
            )
        except (com_error, AttributeError):
            pass
        _desactivar_refresco_pivots(libro)
        esperar_excel_listo(
            aplicacion=aplicacion,
            timeout_segundos=120,
        )
        _log_fase(
            "copiar_abrir_acumulativo",
            time.perf_counter() - inicio,
        )

        nombres = [hoja.name for hoja in libro.sheets]
        if HOJA_RAW_DATA not in nombres:
            raise EstructuraRawDataMasterError(
                f"No existe la hoja '{HOJA_RAW_DATA}'."
            )
        hoja = libro.sheets[HOJA_RAW_DATA]

        try:
            tabla = hoja.api.ListObjects(TABLA_RAW_DATA)
        except Exception as error:
            raise EstructuraRawDataMasterError(
                f"No existe la tabla '{TABLA_RAW_DATA}'."
            ) from error

        inicio = time.perf_counter()
        _, posiciones, _formula_cols = (
            obtener_encabezados_tabla_master(tabla)
        )
        # No se validan ni propagan fórmulas: el usuario las
        # baja manualmente al abrir el acumulativo (igual que
        # SIFA / Kraaijeveld).

        if existe_liquidacion_duplicada_master(
            tabla=tabla,
            posiciones=posiciones,
            anio=procesamiento.despachos.anio,
            semana=procesamiento.despachos.semana,
            factura_corta=(
                procesamiento.liquidacion.factura_corta
            ),
        ):
            raise LiquidacionDuplicadaMasterError(
                "La liquidación ya existe en Raw Data "
                "para el mismo año, semana y factura."
            )
        _log_fase(
            "validar_estructura_duplicado",
            time.perf_counter() - inicio,
        )

        cuerpo_inicial = tabla.DataBodyRange
        filas_antes = int(cuerpo_inicial.Rows.Count)
        cantidad = len(
            procesamiento.validacion.lineas_preparadas
        )

        col_ini = int(tabla.Range.Column)
        col_fin = (
            col_ini + int(tabla.Range.Columns.Count) - 1
        )
        fila_encabezado = int(tabla.HeaderRowRange.Row)
        fila_plantilla_excel = fila_encabezado + filas_antes
        fila_inicial = fila_plantilla_excel + 1
        fila_final = fila_plantilla_excel + cantidad

        nuevo_rango = hoja.api.Range(
            hoja.api.Cells(fila_encabezado, col_ini),
            hoja.api.Cells(fila_final, col_fin),
        )
        inicio = time.perf_counter()
        redimensionar_tabla_master(
            tabla=tabla,
            nuevo_rango=nuevo_rango,
            aplicacion=aplicacion,
        )
        _log_fase(
            "resize_tabla",
            time.perf_counter() - inicio,
        )

        rango_nuevas = hoja.api.Range(
            hoja.api.Cells(fila_inicial, col_ini),
            hoja.api.Cells(fila_final, col_fin),
        )

        logger.info(
            "generacion_master=%s digitados_only=1 "
            "(sin formulas; fill-down manual)",
            _contexto_generacion.get(),
        )

        filas_valores = [
            construir_valores_fila_master(
                procesamiento=procesamiento,
                indice_linea=i,
            )
            for i in range(cantidad)
        ]
        for fila in filas_valores:
            if fila.get("Merma") is None:
                fila["Merma"] = ""

        inicio = time.perf_counter()
        escribir_valores_bloque(
            hoja=hoja.api,
            fila_inicial=fila_inicial,
            fila_final=fila_final,
            posiciones=posiciones,
            filas_valores=filas_valores,
        )
        _log_fase(
            "escribir_valores",
            time.perf_counter() - inicio,
        )

        if recalcular_al_final:
            inicio = time.perf_counter()
            recalcular_dirigido_con_fallback(
                aplicacion=aplicacion,
                hoja=hoja,
                rango_filas_nuevas=rango_nuevas,
                fila_inicial=fila_inicial,
                fila_final=fila_final,
                posiciones=posiciones,
            )
            _log_fase(
                "recalcular",
                time.perf_counter() - inicio,
            )
        else:
            _log_fase("recalcular_omitido", 0.0)

        rango_tabla = str(tabla.Range.Address)
        inicio = time.perf_counter()
        guardar_libro_con_reintentos(
            libro=libro,
            aplicacion=aplicacion,
        )
        _log_fase(
            "guardar",
            time.perf_counter() - inicio,
        )
        escritura_completada = True
        resultado = ResultadoEscrituraMaster(
            archivo_origen=origen.name,
            archivo_salida=salida.name,
            filas_agregadas=cantidad,
            fila_inicial=fila_inicial,
            fila_final=fila_final,
            destino_final=procesamiento.destino_final,
            factura_corta=(
                procesamiento.liquidacion.factura_corta
            ),
            semana=procesamiento.despachos.semana,
            anio=procesamiento.despachos.anio,
            rango_tabla=rango_tabla,
        )
    finally:
        cerrar_aplicacion_excel(
            libro=libro,
            aplicacion=aplicacion,
            guardar=False,
        )
        if escritura_completada and salida_trabajo.is_file():
            try:
                shutil.copy2(salida_trabajo, salida)
            except OSError:
                escritura_completada = False
                resultado = None
                if salida.exists():
                    try:
                        salida.unlink()
                    except OSError:
                        pass
        elif salida.exists():
            try:
                salida.unlink()
            except OSError:
                pass

        shutil.rmtree(carpeta_trabajo, ignore_errors=True)
        _log_fase(
            "total_writer",
            time.perf_counter() - inicio_total,
        )

    if resultado is None:
        raise ErrorEscrituraMaster(
            "El archivo de salida no fue creado."
        )
    return resultado
