from __future__ import annotations

import argparse
import ctypes
import gc
import json
import re
import shutil
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


HOJA_RAW_DATA = "Raw Data"
TABLA_RAW_DATA = "Tabla1"


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


class ErrorEscrituraDimanno(Exception):
    """Error general al escribir el archivo Di Manno."""


class EstructuraRawDataError(ErrorEscrituraDimanno):
    """La hoja Raw Data o Tabla1 no tiene la estructura esperada."""


class LiquidacionDuplicadaError(ErrorEscrituraDimanno):
    """La liquidación ya está registrada en Raw Data."""


class ProcesamientoNoListoError(ErrorEscrituraDimanno):
    """El procesamiento todavía contiene errores pendientes."""

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


def guardar_libro_con_reintentos(
    libro: xw.Book,
    aplicacion: xw.App,
    maximo_intentos: int = 10,
) -> None:
    """
    Guarda el libro reintentando si Excel está ocupado.
    Usa Save de COM directamente para evitar el contexto
    de display_alerts de xlwings, que también falla
    cuando Excel rechaza llamadas (0x800ac472).
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
    Reactiva el cálculo automático y fuerza un recálculo
    reintentando si Excel está ocupado.
    """
    ultimo_error: com_error | None = None

    for intento in range(1, maximo_intentos + 1):
        try:
            esperar_excel_listo(
                aplicacion=aplicacion,
                timeout_segundos=60,
            )

            aplicacion.calculation = "automatic"
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

    for indice_fila in range(
        1,
        cuerpo.Rows.Count + 1,
    ):
        fila = cuerpo.Rows(indice_fila)

        valor_anio = fila.Cells(
            1,
            posiciones["Año"],
        ).Value

        try:
            anio_existente = int(float(valor_anio))
        except (TypeError, ValueError):
            continue

        valor_semana = fila.Cells(
            1,
            posiciones["Semana"],
        ).Value

        try:
            semana_existente, anio_en_semana = (
                interpretar_semana(valor_semana)
            )
        except Exception:
            continue

        if (
            anio_en_semana is not None
            and anio_en_semana != anio
        ):
            continue

        factura_existente = normalizar_factura_corta(
            fila.Cells(
                1,
                posiciones["Fact. 4 Digitos"],
            ).Value
        )

        destino_existente = normalizar_texto(
            fila.Cells(
                1,
                posiciones["Destino"],
            ).Value
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
    recalcular_al_final: bool = True,
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

    shutil.copy2(origen, salida)

    aplicacion: xw.App | None = None
    libro: xw.Book | None = None
    escritura_completada = False

    try:
        aplicacion = xw.App(
            visible=False,
            add_book=False,
        )

        aplicacion.display_alerts = False
        aplicacion.screen_updating = False

        libro = aplicacion.books.open(
            str(salida),
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

        cuerpo_inicial = tabla.DataBodyRange
        ultima_fila_origen = cuerpo_inicial.Rows(
            cuerpo_inicial.Rows.Count
        )

        filas_excel_agregadas: list[int] = []

        for indice_linea in range(
            len(
                procesamiento.validacion
                .lineas_preparadas
            )
        ):
            nueva_fila_tabla = tabla.ListRows.Add()
            rango_nueva_fila = nueva_fila_tabla.Range

            copiar_rango_con_reintentos(
                origen=ultima_fila_origen,
                destino=rango_nueva_fila,
                aplicacion=aplicacion,
            )

            valores = construir_valores_fila(
                procesamiento=procesamiento,
                indice_linea=indice_linea,
            )

            escribir_valores_fila(
                rango_fila=rango_nueva_fila,
                posiciones=posiciones,
                valores=valores,
            )

            filas_excel_agregadas.append(
                int(rango_nueva_fila.Row)
            )

        aplicacion.api.CutCopyMode = False

        if recalcular_al_final:
            calcular_con_reintentos(
                aplicacion=aplicacion,
            )

        rango_tabla = str(tabla.Range.Address)

        guardar_libro_con_reintentos(
            libro=libro,
            aplicacion=aplicacion,
        )

        escritura_completada = True

        return ResultadoEscrituraDimanno(
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
        # Una sola limpieza: éxito o error.
        cerrar_aplicacion_excel(
            libro=libro,
            aplicacion=aplicacion,
            guardar=False,
        )
        libro = None
        aplicacion = None

        if not escritura_completada and salida.exists():
            try:
                salida.unlink()
            except OSError:
                pass


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