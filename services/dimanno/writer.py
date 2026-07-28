from __future__ import annotations

import argparse
import contextvars
import ctypes
import gc
import json
import logging
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
import time

from pywintypes import com_error

import xlwings as xw

from services.dimanno.matcher import (
    interpretar_semana,
    normalizar_texto,
)
from services.dimanno.processor import (
    ResultadoPreparacionDimanno,
    preparar_procesamiento_dimanno,
)

logger = logging.getLogger(__name__)

_contexto_generacion: contextvars.ContextVar[str] = (
    contextvars.ContextVar(
        "dimanno_generacion_id",
        default="-",
    )
)

HOJA_RAW_DATA = "Raw Data"
TABLA_RAW_DATA = "Tabla1"

ERRORES_FORMULA_EXCEL = (
    "#REF!",
    "#VALUE!",
    "#N/A",
    "#DIV/0!",
    "#NAME?",
    "#NULL!",
    "#NUM!",
)


COLUMNAS_ENTRADA = {
    "Año",
    "Semana",
    "Cliente",
    "Nave",
    "Contenedor ",
    "Destino",
    "Tipo de fruta",
    "Cartón",
    "# Calibre",
    "Total Cajas",
    "Flete Eu",
    "Control calidad Eu",
    "Aduanas ",
    "THC ",
    "Transporte ",
    "Comisión",
    "Precio de Venta €",
}


COLUMNAS_FORMULA = {
    "# ",
    "# de semana",
    "Contar Fcls",
    "#Contenedores",
    "Calibre",
    "Factura",
    "Fecha de Emision.Fact",
    "Fact. 4 Digitos",
    "Total Facturado $",
    "Total Facturado  EUR.",
    "NC/ND",
    "Monto EUR NC/ND",
    "Monto $ NC/ND2",
    "T.C Fact.",
    "T.C NC/ND",
    "Electricidad",
    "Electricidad Σ",
    "Aduanas",
    "Aduanas Σ",
    "Flete Interno",
    "Flete Interno Σ",
    "Flete",
    " Flete Σ",
    "BL",
    "BL Σ",
    "LAR",
    "LAR Σ",
    "Scanner APM",
    "Scanner APM Σ",
    "VGM",
    "VGM Σ",
    "DTHC",
    "DTHC Σ",
    "Otros",
    "Otros Σ",
    "Estadia ",
    "Estadia Σ",
    "GEO Total $",
    "GEO Total €",
    "C.C €",
    "Control de Calidad Σ",
    "Aduanas €",
    "Aduanas D. Σ",
    "THC €",
    "THC Destino Σ",
    "Transporte €",
    "Transporte Σ",
    "GED Total  €",
    "GED",
    "GED Total  $",
    "Comision %",
    "Comisión Eu",
    "Comision Total €",
    "Comision Total $",
    "Precio de Venta $",
    "Total Venta $",
    "Total Venta €",
    "Retorno EXW $",
    "Retorno  EXW €",
}


COLUMNAS_ESPERADAS = COLUMNAS_ENTRADA | COLUMNAS_FORMULA

# Subconjunto crítico para validar errores tras un recálculo dirigido.
# Evita leer por COM todas las columnas de fórmula.
COLUMNAS_FORMULA_CRITICAS: tuple[str, ...] = (
    "Contar Fcls",
    "Fact. 4 Digitos",
    "Total Venta €",
    "Comisión Eu",
    "GED Total  €",
    "Retorno  EXW €",
)


class ErrorEscrituraDimanno(Exception):
    """Error general al escribir el archivo Di Manno."""


class EstructuraRawDataError(ErrorEscrituraDimanno):
    """La hoja Raw Data o Tabla1 no tiene la estructura esperada."""


class LiquidacionDuplicadaError(ErrorEscrituraDimanno):
    """La liquidación ya está registrada en Raw Data."""


class ProcesamientoNoListoError(ErrorEscrituraDimanno):
    """El procesamiento todavía contiene errores pendientes."""


def establecer_contexto_generacion(generacion_id: str):
    return _contexto_generacion.set(str(generacion_id))


def limpiar_contexto_generacion(token) -> None:
    _contexto_generacion.reset(token)


def _log_fase(fase: str, segundos: float) -> None:
    logger.info(
        "generacion=%s fase=%s segundos=%.3f",
        _contexto_generacion.get(),
        fase,
        segundos,
    )


def esperar_excel_listo(
    aplicacion: xw.App,
    timeout_segundos: float = 30.0,
) -> None:
    """
    Espera hasta que Excel acepte nuevas operaciones COM.
    """
    inicio = time.monotonic()

    while time.monotonic() - inicio < timeout_segundos:
        try:
            if bool(aplicacion.api.Ready):
                return
        except com_error:
            pass

        time.sleep(0.25)

    raise ErrorEscrituraDimanno(
        "Excel permaneció ocupado durante más de "
        f"{timeout_segundos:.0f} segundos."
    )

def copiar_rango_con_reintentos(
    origen: Any,
    destino: Any,
    aplicacion: xw.App,
    maximo_intentos: int = 8,
) -> None:
    """
    Copia una fila mediante Excel COM y reintenta
    cuando Excel rechaza temporalmente la operación.
    """
    ultimo_error: com_error | None = None

    for intento in range(1, maximo_intentos + 1):
        try:
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=30,
            )

            origen.Copy(
                Destination=destino,
            )

            return

        except com_error as error:
            ultimo_error = error
            time.sleep(0.5 * intento)

    raise ErrorEscrituraDimanno(
        "Excel no permitió copiar la fórmula y el formato "
        f"después de {maximo_intentos} intentos."
    ) from ultimo_error


def rellenar_hacia_abajo_con_reintentos(
    rango: Any,
    aplicacion: xw.App,
    maximo_intentos: int = 8,
) -> None:
    """Propaga fórmulas y formatos hacia abajo en bloque."""
    ultimo_error: com_error | None = None

    for intento in range(1, maximo_intentos + 1):
        try:
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=30,
            )
            rango.FillDown()
            return
        except com_error as error:
            ultimo_error = error
            time.sleep(0.5 * intento)

    raise ErrorEscrituraDimanno(
        "Excel no permitió propagar fórmulas "
        f"después de {maximo_intentos} intentos."
    ) from ultimo_error


def agregar_listrow_con_reintentos(
    tabla: Any,
    aplicacion: xw.App,
    maximo_intentos: int = 10,
) -> Any:
    """
    Agrega una fila a Tabla1 reintentando si Excel está ocupado
    (p. ej. 0x800AC472).
    """
    ultimo_error: com_error | None = None

    for intento in range(1, maximo_intentos + 1):
        try:
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=60,
            )
            return tabla.ListRows.Add()
        except com_error as error:
            ultimo_error = error
            time.sleep(0.5 * intento)

    raise ErrorEscrituraDimanno(
        "Excel no permitió agregar filas a la tabla "
        f"después de {maximo_intentos} intentos."
    ) from ultimo_error


def guardar_libro_con_reintentos(
    libro: xw.Book,
    aplicacion: xw.App,
    maximo_intentos: int = 10,
) -> None:
    """
    Guarda el libro reintentando si Excel está ocupado.
    Usa Save de COM directamente sobre el archivo en TEMP.
    """
    ultimo_error: com_error | None = None

    for intento in range(1, maximo_intentos + 1):
        try:
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=60,
            )

            libro.api.Save()

            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=30,
            )
            return

        except com_error as error:
            ultimo_error = error
            time.sleep(1.0 * intento)

    raise ErrorEscrituraDimanno(
        "Excel no permitió guardar el libro "
        f"después de {maximo_intentos} intentos."
    ) from ultimo_error


def calcular_con_reintentos(
    aplicacion: xw.App,
    maximo_intentos: int = 10,
) -> None:
    """
    Fuerza un recálculo completo manteniendo cálculo manual.

    No activa el modo automatic: eso puede disparar trabajo extra
    antes de guardar.
    """
    ultimo_error: com_error | None = None

    for intento in range(1, maximo_intentos + 1):
        try:
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=60,
            )

            aplicacion.calculation = "manual"
            aplicacion.api.Calculate()

            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=120,
            )

            return

        except com_error as error:
            ultimo_error = error
            time.sleep(1.0 * intento)

    raise ErrorEscrituraDimanno(
        "Excel no permitió recalcular el libro "
        f"después de {maximo_intentos} intentos."
    ) from ultimo_error


def _calculo_incompleto(aplicacion: xw.App) -> bool:
    try:
        # xlDone = 0, xlCalculating = 1, xlPending = 2
        return int(aplicacion.api.CalculationState) != 0
    except (com_error, TypeError, ValueError):
        return True


def _valores_como_lista(valor: Any, filas: int) -> list[Any]:
    if filas <= 1:
        return [valor]
    resultado: list[Any] = []
    for fila in valor:
        if isinstance(fila, (list, tuple)):
            resultado.append(fila[0] if fila else None)
        else:
            resultado.append(fila)
    return resultado


def _es_error_formula(valor: Any) -> bool:
    if valor is None:
        return False
    texto = str(valor).strip().upper()
    return any(
        texto == error or texto.startswith(error)
        for error in ERRORES_FORMULA_EXCEL
    )


def hay_errores_formula_en_filas(
    hoja: Any,
    fila_inicial: int,
    fila_final: int,
    posiciones: dict[str, int],
    columnas: tuple[str, ...] | None = None,
) -> bool:
    filas = fila_final - fila_inicial + 1
    columnas_a_revisar = columnas or COLUMNAS_FORMULA_CRITICAS
    for nombre in columnas_a_revisar:
        if nombre not in posiciones:
            continue
        indice = posiciones[nombre]
        valores = hoja.Range(
            hoja.Cells(fila_inicial, indice),
            hoja.Cells(fila_final, indice),
        ).Value2
        for valor in _valores_como_lista(valores, filas):
            if _es_error_formula(valor):
                return True
    return False


def propagar_formulas_filas_nuevas(
    fila_plantilla: Any,
    rango_filas_nuevas: Any,
    posiciones: dict[str, int],
    aplicacion: xw.App,
) -> None:
    """
    Propaga fórmulas y formatos de la fila plantilla a las filas nuevas.

    Usa una sola copia + FillDown. Si falla, FormulaR1C1 por columna.
    """
    filas = int(rango_filas_nuevas.Rows.Count)
    primera = (
        rango_filas_nuevas
        if filas == 1
        else rango_filas_nuevas.Rows(1)
    )

    try:
        copiar_rango_con_reintentos(
            origen=fila_plantilla,
            destino=primera,
            aplicacion=aplicacion,
        )
        if filas > 1:
            rellenar_hacia_abajo_con_reintentos(
                rango=rango_filas_nuevas,
                aplicacion=aplicacion,
            )
        return
    except ErrorEscrituraDimanno:
        logger.warning(
            "generacion=%s Copy+FillDown falló; "
            "usando FormulaR1C1",
            _contexto_generacion.get(),
        )

    for nombre in COLUMNAS_FORMULA:
        indice = posiciones[nombre]
        formula = fila_plantilla.Cells(
            1,
            indice,
        ).FormulaR1C1
        if not formula:
            continue
        destino = rango_filas_nuevas.Cells(
            1,
            indice,
        ).Resize(filas, 1)
        destino.FormulaR1C1 = formula


def recalcular_dirigido_con_fallback(
    aplicacion: xw.App,
    hoja: xw.Sheet,
    rango_filas_nuevas: Any,
    fila_inicial: int,
    fila_final: int,
    posiciones: dict[str, int],
) -> str:
    """
    Recalcula solo el rango de filas nuevas; si no basta, recálculo completo.

    Mantiene calculation=manual en todo momento.
    Devuelve 'dirigido' o 'completo'.
    """
    try:
        esperar_excel_listo(
            aplicacion=aplicacion,
            timeout_segundos=60,
        )
        aplicacion.calculation = "manual"
        rango_filas_nuevas.Calculate()
        esperar_excel_listo(
            aplicacion=aplicacion,
            timeout_segundos=120,
        )

        if _calculo_incompleto(aplicacion):
            raise ErrorEscrituraDimanno(
                "Quedaron fórmulas pendientes tras el "
                "recálculo dirigido."
            )

        if hay_errores_formula_en_filas(
            hoja.api,
            fila_inicial,
            fila_final,
            posiciones,
            columnas=COLUMNAS_FORMULA_CRITICAS,
        ):
            raise ErrorEscrituraDimanno(
                "Se detectaron errores de fórmula en "
                "filas nuevas."
            )

        return "dirigido"

    except Exception:
        logger.warning(
            "generacion=%s recálculo dirigido insuficiente; "
            "usando recálculo completo",
            _contexto_generacion.get(),
            exc_info=True,
        )
        calcular_con_reintentos(aplicacion=aplicacion)
        return "completo"


def _proceso_sigue_activo(pid: int) -> bool:
    """True si el PID todavía existe en Windows."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def cerrar_aplicacion_excel(
    libro: xw.Book | None,
    aplicacion: xw.App | None,
    *,
    guardar: bool = False,
) -> None:
    """
    Cierra únicamente la instancia de Excel creada por el writer.

    Orden: Close del libro → Quit → esperar → kill del PID
    propio si sigue vivo → liberar referencias → gc.collect().
    """
    pid: int | None = None

    if aplicacion is not None:
        try:
            pid = int(aplicacion.pid)
        except (
            com_error,
            OSError,
            AttributeError,
            TypeError,
            ValueError,
        ):
            pid = None

    if libro is not None:
        try:
            libro.api.Close(SaveChanges=guardar)
        except (com_error, OSError, AttributeError):
            try:
                libro.close()
            except (com_error, OSError, AttributeError):
                pass

    time.sleep(0.5)

    if aplicacion is not None:
        try:
            aplicacion.api.Quit()
        except (com_error, OSError, AttributeError):
            try:
                aplicacion.quit()
            except (com_error, OSError, AttributeError):
                pass

    if pid is not None:
        limite = time.monotonic() + 15.0
        while time.monotonic() < limite:
            if not _proceso_sigue_activo(pid):
                break
            time.sleep(0.25)
        else:
            # Quit no terminó esta instancia: forzar solo ese PID.
            if aplicacion is not None:
                try:
                    aplicacion.kill()
                except (com_error, OSError, AttributeError):
                    pass

            limite_forzado = time.monotonic() + 5.0
            while time.monotonic() < limite_forzado:
                if not _proceso_sigue_activo(pid):
                    break
                time.sleep(0.25)

    libro = None
    aplicacion = None
    gc.collect()
    time.sleep(0.5)

@dataclass(frozen=True)
class ResultadoEscrituraDimanno:
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


def decimal_a_excel(valor: Decimal) -> float:
    return float(valor)


def obtener_encabezados_tabla(
    tabla: Any,
) -> tuple[list[str], dict[str, int]]:
    encabezados: list[str] = []
    posiciones: dict[str, int] = {}

    cantidad_columnas = tabla.ListColumns.Count

    for indice in range(1, cantidad_columnas + 1):
        nombre = str(
            tabla.ListColumns(indice).Name
        )

        if nombre in posiciones:
            raise EstructuraRawDataError(
                f"El encabezado {nombre!r} está repetido."
            )

        encabezados.append(nombre)
        posiciones[nombre] = indice

    encontrados = set(encabezados)

    faltantes = COLUMNAS_ESPERADAS - encontrados
    adicionales = encontrados - COLUMNAS_ESPERADAS

    if faltantes or adicionales:
        partes: list[str] = []

        if faltantes:
            partes.append(
                "Faltantes: "
                + ", ".join(
                    repr(nombre)
                    for nombre in sorted(faltantes)
                )
            )

        if adicionales:
            partes.append(
                "No reconocidos: "
                + ", ".join(
                    repr(nombre)
                    for nombre in sorted(adicionales)
                )
            )

        raise EstructuraRawDataError(
            "Los encabezados de Tabla1 no coinciden con "
            "la estructura esperada. "
            + " ".join(partes)
        )

    return encabezados, posiciones


def validar_formulas_ultima_fila(
    tabla: Any,
    posiciones: dict[str, int],
) -> None:
    cuerpo = tabla.DataBodyRange

    if cuerpo is None or cuerpo.Rows.Count < 1:
        raise EstructuraRawDataError(
            "Tabla1 no contiene una fila desde la cual "
            "copiar las fórmulas."
        )

    ultima_fila = cuerpo.Rows(cuerpo.Rows.Count)
    columnas_sin_formula: list[str] = []

    for nombre in sorted(COLUMNAS_FORMULA):
        indice = posiciones[nombre]
        celda = ultima_fila.Cells(1, indice)

        if not bool(celda.HasFormula):
            columnas_sin_formula.append(nombre)

    if columnas_sin_formula:
        raise EstructuraRawDataError(
            "La última fila de Tabla1 no contiene fórmula "
            "en estas columnas: "
            + ", ".join(
                repr(nombre)
                for nombre in columnas_sin_formula
            )
        )


def normalizar_factura_corta(valor: Any) -> str:
    digitos = re.sub(
        r"\D",
        "",
        str(valor or ""),
    )

    if not digitos:
        return ""

    return digitos.zfill(4)[-4:]


def existe_liquidacion_duplicada(
    tabla: Any,
    posiciones: dict[str, int],
    anio: int,
    semana: int,
    factura_corta: str,
    destino: str,
) -> bool:
    cuerpo = tabla.DataBodyRange

    if cuerpo is None:
        return False

    destino_buscado = normalizar_texto(destino)
    factura_buscada = normalizar_factura_corta(
        factura_corta
    )
    filas = int(cuerpo.Rows.Count)
    if filas < 1:
        return False

    def _columna(nombre: str) -> list[Any]:
        indice = posiciones[nombre]
        crudo = cuerpo.Columns(indice).Value2
        return _valores_como_lista(crudo, filas)

    valores_anio = _columna("Año")
    valores_semana = _columna("Semana")
    valores_factura = _columna("Fact. 4 Digitos")
    valores_destino = _columna("Destino")

    for indice in range(filas):
        valor_anio = valores_anio[indice]
        try:
            anio_existente = int(float(valor_anio))
        except (TypeError, ValueError):
            continue

        try:
            semana_existente, anio_en_semana = (
                interpretar_semana(valores_semana[indice])
            )
        except Exception:
            continue

        if (
            anio_en_semana is not None
            and anio_en_semana != anio
        ):
            continue

        factura_existente = normalizar_factura_corta(
            valores_factura[indice]
        )
        destino_existente = normalizar_texto(
            valores_destino[indice]
        )

        if (
            anio_existente == anio
            and semana_existente == semana
            and factura_existente == factura_buscada
            and destino_existente == destino_buscado
        ):
            return True

    return False


def escribir_valores_fila(
    rango_fila: Any,
    posiciones: dict[str, int],
    valores: dict[str, Any],
) -> None:
    for nombre_columna, valor in valores.items():
        rango_fila.Cells(
            1,
            posiciones[nombre_columna],
        ).Value = valor


def escribir_valores_bloque(
    hoja: Any,
    fila_inicial: int,
    fila_final: int,
    posiciones: dict[str, int],
    filas_valores: list[dict[str, Any]],
) -> None:
    """Asigna valores de entrada por columna con Value2."""
    if not filas_valores:
        return

    columnas = list(filas_valores[0].keys())

    for nombre_columna in columnas:
        indice = posiciones[nombre_columna]
        matriz = [
            [fila[nombre_columna]]
            for fila in filas_valores
        ]
        hoja.Range(
            hoja.Cells(fila_inicial, indice),
            hoja.Cells(fila_final, indice),
        ).Value2 = matriz


def construir_valores_fila(
    procesamiento: ResultadoPreparacionDimanno,
    indice_linea: int,
) -> dict[str, Any]:
    linea_preparada = (
        procesamiento.validacion.lineas_preparadas[
            indice_linea
        ]
    )

    despacho = linea_preparada.despacho
    liquidacion = procesamiento.liquidacion
    gastos = liquidacion.gastos

    if procesamiento.destino_final is None:
        raise ProcesamientoNoListoError(
            "No se ha definido el destino final."
        )

    return {
        "Año": despacho.anio,
        "Semana": (
            f"{despacho.semana:02d}-{despacho.anio}"
        ),
        "Cliente": despacho.cliente,
        "Nave": despacho.barco,
        "Contenedor ": despacho.contenedor,
        "Destino": procesamiento.destino_final,
        "Tipo de fruta": despacho.tipo_empaque,
        "Cartón": despacho.carton,
        "# Calibre": despacho.calibre,
        "Total Cajas": despacho.total_cajas,
        "Flete Eu": decimal_a_excel(
            gastos["Flete Eu"]
        ),
        "Control calidad Eu": decimal_a_excel(
            gastos["Control calidad Eu"]
        ),
        "Aduanas ": decimal_a_excel(
            gastos["Aduanas"]
        ),
        "THC ": decimal_a_excel(
            gastos["THC"]
        ),
        "Transporte ": decimal_a_excel(
            gastos["Transporte"]
        ),
        "Comisión": decimal_a_excel(
            gastos["Comisión"]
        ),
        "Precio de Venta €": decimal_a_excel(
            linea_preparada.precio_venta_eur
        ),
    }


def escribir_archivo_dimanno(
    procesamiento: ResultadoPreparacionDimanno,
    ruta_archivo_cliente: str | Path,
    ruta_salida: str | Path,
    recalcular_al_final: bool = False,
) -> ResultadoEscrituraDimanno:
    if not procesamiento.puede_escribir:
        raise ProcesamientoNoListoError(
            "El procesamiento no está listo para escribir. "
            f"Estado actual: {procesamiento.estado}."
        )

    if procesamiento.destino_final is None:
        raise ProcesamientoNoListoError(
            "No se ha confirmado el destino final."
        )

    origen = Path(ruta_archivo_cliente).resolve()
    salida = Path(ruta_salida).resolve()
    inicio_total = time.perf_counter()

    if not origen.is_file():
        raise FileNotFoundError(
            f"No existe el archivo del cliente: {origen}"
        )

    if origen == salida:
        raise ErrorEscrituraDimanno(
            "El archivo de salida no puede ser el mismo "
            "archivo de origen."
        )

    if salida.exists():
        raise FileExistsError(
            f"El archivo de salida ya existe: {salida}"
        )

    if not procesamiento.validacion.lineas_preparadas:
        raise ProcesamientoNoListoError(
            "No existen líneas preparadas para escribir."
        )

    salida.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Trabajo Excel en disco local para evitar latencia de
    # OneDrive/SharePoint sobre media/ del proyecto.
    carpeta_trabajo = Path(
        tempfile.mkdtemp(prefix="dimanno_write_")
    )
    salida_trabajo = carpeta_trabajo / "trabajo.xlsx"

    aplicacion: xw.App | None = None
    libro: xw.Book | None = None
    escritura_completada = False
    resultado: ResultadoEscrituraDimanno | None = None

    try:
        inicio = time.perf_counter()
        shutil.copy2(origen, salida_trabajo)

        aplicacion = xw.App(
            visible=False,
            add_book=False,
        )

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

        esperar_excel_listo(
            aplicacion=aplicacion,
            timeout_segundos=30,
        )
        _log_fase(
            "copiar_abrir_acumulativo",
            time.perf_counter() - inicio,
        )

        inicio = time.perf_counter()
        nombres_hojas = [
            hoja.name
            for hoja in libro.sheets
        ]

        if HOJA_RAW_DATA not in nombres_hojas:
            raise EstructuraRawDataError(
                f"No existe la hoja '{HOJA_RAW_DATA}'."
            )

        hoja = libro.sheets[HOJA_RAW_DATA]

        try:
            tabla = hoja.api.ListObjects(
                TABLA_RAW_DATA
            )
        except Exception as error:
            raise EstructuraRawDataError(
                f"No existe la tabla '{TABLA_RAW_DATA}' "
                f"en la hoja '{HOJA_RAW_DATA}'."
            ) from error

        _, posiciones = obtener_encabezados_tabla(
            tabla
        )

        validar_formulas_ultima_fila(
            tabla=tabla,
            posiciones=posiciones,
        )

        liquidacion = procesamiento.liquidacion

        if existe_liquidacion_duplicada(
            tabla=tabla,
            posiciones=posiciones,
            anio=procesamiento.despachos.anio_buscado,
            semana=liquidacion.semana,
            factura_corta=liquidacion.factura_corta,
            destino=procesamiento.destino_final,
        ):
            raise LiquidacionDuplicadaError(
                "La liquidación ya existe en Raw Data "
                "para el mismo año, semana, factura y "
                "destino."
            )
        _log_fase(
            "localizar_hoja_tabla",
            time.perf_counter() - inicio,
        )

        cuerpo_inicial = tabla.DataBodyRange
        filas_antes = int(cuerpo_inicial.Rows.Count)
        cantidad_lineas = len(
            procesamiento.validacion.lineas_preparadas
        )

        inicio = time.perf_counter()
        filas_excel_agregadas: list[int] = []
        for _ in range(cantidad_lineas):
            list_row = agregar_listrow_con_reintentos(
                tabla=tabla,
                aplicacion=aplicacion,
            )
            filas_excel_agregadas.append(
                int(list_row.Range.Row)
            )
        _log_fase(
            "agregar_listrows",
            time.perf_counter() - inicio,
        )

        fila_plantilla = tabla.ListRows(filas_antes).Range
        fila_inicial_nueva = min(filas_excel_agregadas)
        fila_final_nueva = max(filas_excel_agregadas)
        columna_inicial = int(tabla.Range.Column)
        columna_final = (
            columna_inicial + int(tabla.Range.Columns.Count) - 1
        )
        rango_filas_nuevas = hoja.api.Range(
            hoja.api.Cells(
                fila_inicial_nueva,
                columna_inicial,
            ),
            hoja.api.Cells(
                fila_final_nueva,
                columna_final,
            ),
        )

        inicio = time.perf_counter()
        propagar_formulas_filas_nuevas(
            fila_plantilla=fila_plantilla,
            rango_filas_nuevas=rango_filas_nuevas,
            posiciones=posiciones,
            aplicacion=aplicacion,
        )
        try:
            aplicacion.api.CutCopyMode = False
        except com_error:
            pass
        _log_fase(
            "copiar_extender_formulas",
            time.perf_counter() - inicio,
        )

        filas_valores = [
            construir_valores_fila(
                procesamiento=procesamiento,
                indice_linea=indice,
            )
            for indice in range(cantidad_lineas)
        ]

        inicio = time.perf_counter()
        escribir_valores_bloque(
            hoja=hoja.api,
            fila_inicial=fila_inicial_nueva,
            fila_final=fila_final_nueva,
            posiciones=posiciones,
            filas_valores=filas_valores,
        )
        _log_fase(
            "escribir_valores",
            time.perf_counter() - inicio,
        )

        if recalcular_al_final:
            inicio = time.perf_counter()
            modo = recalcular_dirigido_con_fallback(
                aplicacion=aplicacion,
                hoja=hoja,
                rango_filas_nuevas=rango_filas_nuevas,
                fila_inicial=fila_inicial_nueva,
                fila_final=fila_final_nueva,
                posiciones=posiciones,
            )
            _log_fase(
                f"recalcular_{modo}",
                time.perf_counter() - inicio,
            )
        else:
            _log_fase("recalcular_omitido", 0.0)
            try:
                aplicacion.calculation = "manual"
            except com_error:
                pass

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
        resultado = ResultadoEscrituraDimanno(
            archivo_origen=origen.name,
            archivo_salida=salida.name,
            filas_agregadas=len(
                filas_excel_agregadas
            ),
            fila_inicial=min(
                filas_excel_agregadas
            ),
            fila_final=max(
                filas_excel_agregadas
            ),
            destino_final=(
                procesamiento.destino_final
            ),
            factura_corta=(
                procesamiento.liquidacion
                .factura_corta
            ),
            semana=(
                procesamiento.liquidacion.semana
            ),
            anio=(
                procesamiento.despachos
                .anio_buscado
            ),
            rango_tabla=rango_tabla,
        )

    except Exception:
        raise

    finally:
        inicio_cierre = time.perf_counter()
        cerrar_aplicacion_excel(
            libro=libro,
            aplicacion=aplicacion,
            guardar=False,
        )
        libro = None
        aplicacion = None
        _log_fase(
            "cerrar_excel",
            time.perf_counter() - inicio_cierre,
        )

        if escritura_completada and salida_trabajo.is_file():
            inicio_copia = time.perf_counter()
            try:
                shutil.copy2(salida_trabajo, salida)
                _log_fase(
                    "copiar_resultado_final",
                    time.perf_counter() - inicio_copia,
                )
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

        try:
            shutil.rmtree(carpeta_trabajo, ignore_errors=True)
        except OSError:
            pass

        _log_fase(
            "total_writer",
            time.perf_counter() - inicio_total,
        )

    if resultado is None:
        raise ErrorEscrituraDimanno(
            "El archivo de salida no fue creado."
        )
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Procesa una liquidación Di Manno y agrega "
            "las líneas al archivo acumulativo."
        )
    )

    parser.add_argument(
        "--liquidacion",
        required=True,
    )

    parser.add_argument(
        "--hoja",
        required=True,
    )

    parser.add_argument(
        "--despachos",
        required=True,
    )

    parser.add_argument(
        "--anio",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--archivo-cliente",
        required=True,
    )

    parser.add_argument(
        "--salida",
        required=True,
    )

    parser.add_argument(
        "--cliente",
        default="DI MANNO",
    )

    parser.add_argument(
        "--destino-confirmado",
        default=None,
    )

    argumentos = parser.parse_args()

    procesamiento = preparar_procesamiento_dimanno(
        ruta_liquidacion=argumentos.liquidacion,
        nombre_hoja=argumentos.hoja,
        ruta_despachos=argumentos.despachos,
        anio=argumentos.anio,
        cliente=argumentos.cliente,
        destino_confirmado=(
            argumentos.destino_confirmado
        ),
    )

    resultado = escribir_archivo_dimanno(
        procesamiento=procesamiento,
        ruta_archivo_cliente=(
            argumentos.archivo_cliente
        ),
        ruta_salida=argumentos.salida,
    )

    print(
        json.dumps(
            asdict(resultado),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()