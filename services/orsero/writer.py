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

from services.dimanno.matcher import interpretar_semana
from services.dimanno.writer import (
    ErrorEscrituraDimanno,
    cerrar_aplicacion_excel,
    copiar_rango_con_reintentos,
    decimal_a_excel,
    esperar_excel_listo,
    escribir_valores_bloque,
    guardar_libro_con_reintentos,
    recalcular_dirigido_con_fallback,
    rellenar_hacia_abajo_con_reintentos,
)
from services.orsero.processor import ResultadoPreparacionOrsero

logger = logging.getLogger(__name__)

_contexto_generacion: contextvars.ContextVar[str] = (
    contextvars.ContextVar(
        "orsero_generacion_id",
        default="-",
    )
)

HOJA_RAW_DATA = "Raw Data"
TABLA_RAW_DATA = "Tabla1"
NOMBRE_DESCARGA_ORSERO = "ORSERO Liquidaciones.xlsx"

COLUMNAS_ENTRADA = {
    "Semana",
    "Año",
    "Cliente",
    "Nave",
    "Contenedor",
    "Destino",
    "Tipo de fruta",
    "Cartón",
    "# Calibre",
    "Total Cajas",
    "T.C Orser",
    "Costo en Origen Form.",
    "Inland Form.",
    "THC Origen Form.",
    "Flete Form.",
    "Insurance Form.",
    "THC Destino Form.",
    "Forwarding Form.",
    "Transport In Form.",
    "Comision Form",
    "Precio de Venta €",
}

# Alias por si Tabla1 usa "Contenedor " como en Master.
ALIAS_COLUMNAS = {
    "Contenedor": ("Contenedor", "Contenedor "),
}

COLUMNAS_FORMULA_CRITICAS: tuple[str, ...] = (
    "Contar Fcls",
    "Fact. 4 Digitos",
    "Total Venta €",
    "Comision %",
    "Comision €",
    "Comision Formula",
    "Retorno  EXW €",
    "Precio de Venta $",
)


class ErrorEscrituraOrsero(Exception):
    """Error general al escribir el acumulativo Orsero."""


class EstructuraRawDataOrseroError(ErrorEscrituraOrsero):
    """Tabla1 no tiene la estructura esperada."""


class LiquidacionDuplicadaOrseroError(ErrorEscrituraOrsero):
    """La liquidación ya está en Raw Data."""


class ProcesamientoOrseroNoListoError(ErrorEscrituraOrsero):
    """El procesamiento no está listo para escribir."""


@dataclass(frozen=True)
class ResultadoEscrituraOrsero:
    archivo_origen: str
    archivo_salida: str
    filas_agregadas: int
    fila_inicial: int
    fila_final: int
    semana: int
    anio: int
    rango_tabla: str


def establecer_contexto_generacion(generacion_id: str):
    return _contexto_generacion.set(str(generacion_id))


def limpiar_contexto_generacion(token) -> None:
    _contexto_generacion.reset(token)


def _log_fase(fase: str, segundos: float) -> None:
    logger.info(
        "generacion_orsero=%s fase=%s segundos=%.3f",
        _contexto_generacion.get(),
        fase,
        segundos,
    )


def _resolver_nombre_columna(
    encontrados: set[str],
    nombre: str,
) -> str:
    if nombre in encontrados:
        return nombre
    for alias in ALIAS_COLUMNAS.get(nombre, ()):
        if alias in encontrados:
            return alias
    return nombre


def obtener_encabezados_tabla_orsero(
    tabla: Any,
) -> tuple[list[str], dict[str, int], set[str]]:
    encabezados: list[str] = []
    posiciones_crudas: dict[str, int] = {}

    for indice in range(1, tabla.ListColumns.Count + 1):
        nombre = str(tabla.ListColumns(indice).Name)
        if nombre in posiciones_crudas:
            raise EstructuraRawDataOrseroError(
                f"El encabezado {nombre!r} está repetido."
            )
        encabezados.append(nombre)
        posiciones_crudas[nombre] = indice

    encontrados = set(encabezados)
    faltantes: list[str] = []
    posiciones: dict[str, int] = {}

    for nombre in COLUMNAS_ENTRADA:
        resuelto = _resolver_nombre_columna(encontrados, nombre)
        if resuelto not in encontrados:
            faltantes.append(nombre)
            continue
        posiciones[nombre] = posiciones_crudas[resuelto]

    if faltantes:
        raise EstructuraRawDataOrseroError(
            "Faltan columnas digitadas en Tabla1: "
            + ", ".join(repr(n) for n in sorted(faltantes))
        )

    # Incluir el resto de columnas (fórmulas) por nombre real.
    for nombre, indice in posiciones_crudas.items():
        if nombre not in posiciones:
            posiciones[nombre] = indice

    formula_cols = encontrados - {
        _resolver_nombre_columna(encontrados, n)
        for n in COLUMNAS_ENTRADA
    }
    return encabezados, posiciones, formula_cols


def validar_formulas_ultima_fila_orsero(
    tabla: Any,
    posiciones: dict[str, int],
    columnas_formula: set[str],
) -> None:
    cuerpo = tabla.DataBodyRange
    if cuerpo is None or cuerpo.Rows.Count < 1:
        raise EstructuraRawDataOrseroError(
            "Tabla1 no tiene fila plantilla de fórmulas."
        )

    ultima = cuerpo.Rows(cuerpo.Rows.Count)
    # Solo críticas: revisar todas las columnas de fórmula
    # vía COM es muy lento en acumulativos anchos.
    sin_formula: list[str] = []
    for nombre in COLUMNAS_FORMULA_CRITICAS:
        if nombre not in posiciones:
            continue
        if nombre not in columnas_formula:
            continue
        celda = ultima.Cells(1, posiciones[nombre])
        if not bool(celda.HasFormula):
            sin_formula.append(nombre)

    if sin_formula:
        raise EstructuraRawDataOrseroError(
            "La última fila no tiene fórmula en: "
            + ", ".join(repr(n) for n in sin_formula)
        )


def existe_liquidacion_duplicada_orsero(
    tabla: Any,
    posiciones: dict[str, int],
    anio: int,
    semana: int,
    cliente: str,
) -> bool:
    cuerpo = tabla.DataBodyRange
    if cuerpo is None:
        return False

    filas = int(cuerpo.Rows.Count)
    if filas < 1:
        return False

    cliente_n = str(cliente).strip().upper().replace(" ", "")

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
    valores_cliente = _col("Cliente")

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

        cliente_fila = str(
            valores_cliente[i] or ""
        ).strip().upper().replace(" ", "")
        if (
            anio_existente == anio
            and semana_existente == semana
            and cliente_fila == cliente_n
        ):
            return True

    return False


def construir_valores_fila_orsero(
    procesamiento: ResultadoPreparacionOrsero,
    indice_linea: int,
) -> dict[str, Any]:
    linea = procesamiento.validacion.lineas_preparadas[
        indice_linea
    ]
    despacho = linea.despacho
    gastos = linea.gastos

    semana_texto = despacho.semana_texto or (
        f"{despacho.semana:02d}-{despacho.anio}"
    )

    return {
        "Semana": semana_texto,
        "Año": despacho.anio,
        "Cliente": despacho.cliente,
        "Nave": despacho.barco,
        "Contenedor": despacho.contenedor,
        "Destino": linea.destino,
        "Tipo de fruta": linea.tipo_fruta,
        "Cartón": despacho.carton,
        "# Calibre": despacho.calibre,
        "Total Cajas": despacho.total_cajas,
        "T.C Orser": decimal_a_excel(
            linea.tipo_cambio_usd_eur
        ),
        "Costo en Origen Form.": decimal_a_excel(
            gastos.get("Costo en Origen Form.", Decimal("0"))
        ),
        "Inland Form.": decimal_a_excel(
            gastos.get("Inland Form.", Decimal("0"))
        ),
        "THC Origen Form.": decimal_a_excel(
            gastos.get("THC Origen Form.", Decimal("0"))
        ),
        "Flete Form.": decimal_a_excel(
            gastos.get("Flete Form.", Decimal("0"))
        ),
        "Insurance Form.": decimal_a_excel(
            gastos.get("Insurance Form.", Decimal("0"))
        ),
        "THC Destino Form.": decimal_a_excel(
            gastos.get("THC Destino Form.", Decimal("0"))
        ),
        "Forwarding Form.": decimal_a_excel(
            gastos.get("Forwarding Form.", Decimal("0"))
        ),
        "Transport In Form.": decimal_a_excel(
            gastos.get("Transport In Form.", Decimal("0"))
        ),
        "Comision Form": decimal_a_excel(
            gastos.get("Comision Form", Decimal("0"))
        ),
        "Precio de Venta €": (
            format(linea.precio_venta_eur, "f")
            if linea.precio_encontrado
            else ""
        ),
    }


def redimensionar_tabla_orsero(
    tabla: Any,
    nuevo_rango: Any,
    aplicacion: xw.App,
    maximo_intentos: int = 8,
) -> None:
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

    raise ErrorEscrituraOrsero(
        "Excel no permitió redimensionar Tabla1 "
        f"después de {maximo_intentos} intentos. "
        "Cierre Excel/ORSERO Liquidaciones si está abierto "
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


def _grupos_columnas_contiguas(
    indices: list[int],
) -> list[tuple[int, int]]:
    """Agrupa índices 1-based en rangos [inicio, fin] inclusive."""
    if not indices:
        return []
    ordenados = sorted(indices)
    grupos: list[tuple[int, int]] = []
    inicio = ordenados[0]
    fin = ordenados[0]
    for indice in ordenados[1:]:
        if indice == fin + 1:
            fin = indice
            continue
        grupos.append((inicio, fin))
        inicio = indice
        fin = indice
    grupos.append((inicio, fin))
    return grupos


def _propagar_formulas_columnas(
    fila_plantilla: Any,
    rango_nuevas: Any,
    posiciones: dict[str, int],
    columnas_formula: set[str],
    aplicacion: xw.App,
) -> None:
    """
    Propaga fórmulas a las filas nuevas.

    Optimización vs copiar toda la fila: solo Copy+FillDown de
    bloques contiguos de columnas de fórmula (las digitadas se
    escriben después). Luego spot-check y reparación.
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
        aplicacion.calculation = "manual"
    except Exception:
        pass

    indices_formula: list[int] = []
    formula_por_indice: dict[int, str] = {}
    for nombre in columnas_formula:
        if nombre not in posiciones:
            continue
        indice = posiciones[nombre]
        try:
            formula = fila_plantilla.Cells(1, indice).FormulaR1C1
        except com_error:
            continue
        if not formula:
            continue
        indices_formula.append(indice)
        formula_por_indice[indice] = formula

    grupos = _grupos_columnas_contiguas(indices_formula)
    bloques_ok = 0

    try:
        for col_ini, col_fin in grupos:
            ancho = col_fin - col_ini + 1
            origen = fila_plantilla.Cells(1, col_ini).Resize(
                1, ancho
            )
            destino_primera = rango_nuevas.Cells(
                1, col_ini
            ).Resize(1, ancho)
            destino_bloque = rango_nuevas.Cells(
                1, col_ini
            ).Resize(filas, ancho)
            try:
                copiar_rango_con_reintentos(
                    origen=origen,
                    destino=destino_primera,
                    aplicacion=aplicacion,
                )
                if filas > 1:
                    rellenar_hacia_abajo_con_reintentos(
                        rango=destino_bloque,
                        aplicacion=aplicacion,
                    )
                try:
                    aplicacion.api.CutCopyMode = False
                except com_error:
                    pass
                bloques_ok += 1
            except ErrorEscrituraDimanno:
                logger.warning(
                    "generacion_orsero=%s Copy+FillDown falló "
                    "en columnas %s-%s; se reparará",
                    _contexto_generacion.get(),
                    col_ini,
                    col_fin,
                )
                try:
                    aplicacion.api.CutCopyMode = False
                except com_error:
                    pass
                for indice in range(col_ini, col_fin + 1):
                    formula = formula_por_indice.get(indice)
                    if not formula:
                        continue
                    try:
                        rango_nuevas.Cells(
                            1, indice
                        ).Resize(filas, 1).FormulaR1C1 = formula
                    except com_error:
                        for fila_i in range(1, filas + 1):
                            try:
                                rango_nuevas.Cells(
                                    fila_i, indice
                                ).FormulaR1C1 = formula
                            except com_error:
                                continue

        # Spot-check: primera y última fila (no todas las celdas).
        filas_check = (1,) if filas == 1 else (1, filas)
        reparadas = 0
        for indice, formula in formula_por_indice.items():
            faltantes = False
            for fila_i in filas_check:
                try:
                    if not bool(
                        rango_nuevas.Cells(
                            fila_i, indice
                        ).HasFormula
                    ):
                        faltantes = True
                        break
                except com_error:
                    faltantes = True
                    break
            if not faltantes:
                continue
            try:
                rango_nuevas.Cells(1, indice).Resize(
                    filas, 1
                ).FormulaR1C1 = formula
                reparadas += 1
            except com_error:
                for fila_i in range(1, filas + 1):
                    try:
                        rango_nuevas.Cells(
                            fila_i, indice
                        ).FormulaR1C1 = formula
                    except com_error:
                        continue
                reparadas += 1

        logger.info(
            "generacion_orsero=%s formula_bloques=%s/%s "
            "columnas=%s reparadas=%s",
            _contexto_generacion.get(),
            bloques_ok,
            len(grupos),
            len(formula_por_indice),
            reparadas,
        )
        esperar_excel_listo(
            aplicacion=aplicacion,
            timeout_segundos=30,
        )
    finally:
        if autofill_previo is not None:
            try:
                aplicacion.api.AutoCorrect.AutoFillFormulasInLists = (
                    autofill_previo
                )
            except (com_error, AttributeError):
                pass


def escribir_archivo_orsero(
    procesamiento: ResultadoPreparacionOrsero,
    ruta_archivo_cliente: str | Path,
    ruta_salida: str | Path,
    recalcular_al_final: bool = False,
) -> ResultadoEscrituraOrsero:
    if not procesamiento.puede_escribir:
        raise ProcesamientoOrseroNoListoError(
            "El procesamiento no está listo para escribir. "
            f"Estado: {procesamiento.estado}."
        )

    origen = Path(ruta_archivo_cliente).resolve()
    salida = Path(ruta_salida).resolve()
    inicio_total = time.perf_counter()

    if not origen.is_file():
        raise FileNotFoundError(
            f"No existe el acumulativo: {origen}"
        )
    if origen == salida:
        raise ErrorEscrituraOrsero(
            "La salida no puede ser el mismo archivo de origen."
        )
    if salida.exists():
        raise FileExistsError(
            f"El archivo de salida ya existe: {salida}"
        )
    if not procesamiento.validacion.lineas_preparadas:
        raise ProcesamientoOrseroNoListoError(
            "No hay líneas preparadas."
        )

    salida.parent.mkdir(parents=True, exist_ok=True)
    carpeta_trabajo = Path(
        tempfile.mkdtemp(prefix="orsero_write_")
    )
    salida_trabajo = carpeta_trabajo / "trabajo.xlsx"

    aplicacion: xw.App | None = None
    libro: xw.Book | None = None
    escritura_completada = False
    resultado: ResultadoEscrituraOrsero | None = None

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
            raise EstructuraRawDataOrseroError(
                f"No existe la hoja '{HOJA_RAW_DATA}'."
            )
        hoja = libro.sheets[HOJA_RAW_DATA]

        try:
            tabla = hoja.api.ListObjects(TABLA_RAW_DATA)
        except Exception as error:
            raise EstructuraRawDataOrseroError(
                f"No existe la tabla '{TABLA_RAW_DATA}'."
            ) from error

        inicio = time.perf_counter()
        _, posiciones, formula_cols = (
            obtener_encabezados_tabla_orsero(tabla)
        )
        validar_formulas_ultima_fila_orsero(
            tabla=tabla,
            posiciones=posiciones,
            columnas_formula=formula_cols,
        )

        if existe_liquidacion_duplicada_orsero(
            tabla=tabla,
            posiciones=posiciones,
            anio=procesamiento.despachos.anio,
            semana=procesamiento.despachos.semana,
            cliente=procesamiento.despachos.cliente_buscado,
        ):
            raise LiquidacionDuplicadaOrseroError(
                "La liquidación ya existe en Raw Data "
                "para el mismo año, semana y cliente Orsero."
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
        redimensionar_tabla_orsero(
            tabla=tabla,
            nuevo_rango=nuevo_rango,
            aplicacion=aplicacion,
        )
        _log_fase(
            "resize_tabla",
            time.perf_counter() - inicio,
        )

        fila_plantilla = hoja.api.Range(
            hoja.api.Cells(fila_plantilla_excel, col_ini),
            hoja.api.Cells(fila_plantilla_excel, col_fin),
        )
        rango_nuevas = hoja.api.Range(
            hoja.api.Cells(fila_inicial, col_ini),
            hoja.api.Cells(fila_final, col_fin),
        )

        inicio = time.perf_counter()
        _propagar_formulas_columnas(
            fila_plantilla=fila_plantilla,
            rango_nuevas=rango_nuevas,
            posiciones=posiciones,
            columnas_formula=formula_cols,
            aplicacion=aplicacion,
        )
        _log_fase(
            "propagar_formulas",
            time.perf_counter() - inicio,
        )

        filas_valores = [
            construir_valores_fila_orsero(
                procesamiento=procesamiento,
                indice_linea=i,
            )
            for i in range(cantidad)
        ]

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
        resultado = ResultadoEscrituraOrsero(
            archivo_origen=origen.name,
            archivo_salida=salida.name,
            filas_agregadas=cantidad,
            fila_inicial=fila_inicial,
            fila_final=fila_final,
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
        raise ErrorEscrituraOrsero(
            "El archivo de salida no fue creado."
        )
    return resultado
