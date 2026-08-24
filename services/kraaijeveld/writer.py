from __future__ import annotations

import contextvars
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from services.dimanno.matcher import (
    interpretar_semana,
    normalizar_texto,
)
from services.dimanno.writer import decimal_a_excel
from services.kraaijeveld.extractor import COLUMNAS_GASTO
from services.kraaijeveld.processor import ResultadoPreparacionKraaijeveld

logger = logging.getLogger(__name__)

_contexto_generacion: contextvars.ContextVar[str] = (
    contextvars.ContextVar(
        "kraaijeveld_generacion_id",
        default="-",
    )
)

HOJA_RAW_DATA = "Raw Data"
TABLA_RAW_DATA = "Tabla1"
NOMBRE_DESCARGA_KRAAIJEVELD = "Kraaijeveld Liquidaciones V.2.xlsx"
HOJA_XML = "xl/worksheets/sheet1.xml"
TABLA_XML = "xl/tables/table1.xml"
SHARED_STRINGS_XML = "xl/sharedStrings.xml"

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
    "Logistics.C",
    "Import.T",
    "Import.T S.A",
    "Scanning Cost",
    "Other Import C.",
    "Demourge Cost",
    "Export C.S",
    "Repack Costs",
    "Storage Fee",
    "Comisión",
    "Precio de Venta €",
    "Precio de Venta $",
}

ALIAS_COLUMNAS = {
    "Contenedor": ("Contenedor", "Contenedor "),
    "Other Import C.": (
        "Other Import C.",
        "Other Import C. ",
        "Other Import C",
        "OIC",
    ),
    "Logistics.C": ("Logistics.C", "Logistics.C "),
    "Import.T": ("Import.T", "Import.T "),
    "Import.T S.A": ("Import.T S.A", "Import.T S.A "),
    "Scanning Cost": ("Scanning Cost", "Scanning Cost "),
    "Demourge Cost": ("Demourge Cost", "Demourge Cost "),
    "Export C.S": ("Export C.S", "Export C.S "),
    "Repack Costs": ("Repack Costs", "Repack Costs "),
    "Storage Fee": ("Storage Fee", "Storage Fee "),
    "Comisión": ("Comisión", "Comision", "Comisión "),
    "Precio de Venta €": (
        "Precio de Venta €",
        "Precio de Venta € ",
    ),
    "Precio de Venta $": (
        "Precio de Venta $",
        "Precio de Venta $ ",
    ),
}

_CELL_RE = re.compile(
    r'<c r="([A-Z]+)(\d+)"([^>]*)(?:/>|>(.*?)</c>)',
    re.DOTALL,
)
_ROW_OPEN_RE = re.compile(r'<row r="(\d+)"([^>]*)>')
_ROW_RE = re.compile(r"<row\b[^>]*>.*?</row>", re.DOTALL)
_FORMULA_RE = re.compile(r"<f\b[^>]*>(.*?)</f>", re.DOTALL)
_TABLE_REF_RE = re.compile(r'\bref="([^"]+)"')
_DIMENSION_RE = re.compile(r'<dimension[^>]*ref="([^"]+)"[^>]*/>')
_V_RE = re.compile(r"<v>(.*?)</v>", re.DOTALL)
_INLINE_T_RE = re.compile(r"<t[^>]*>(.*?)</t>", re.DOTALL)
_ATTR_T_RE = re.compile(r'\bt="([^"]*)"')


def get_column_letter(col: int) -> str:
    if col < 1:
        raise ValueError(col)
    letras: list[str] = []
    while col:
        col, resto = divmod(col - 1, 26)
        letras.append(chr(65 + resto))
    return "".join(reversed(letras))


def column_index_from_string(letra: str) -> int:
    total = 0
    for ch in letra.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError(letra)
        total = total * 26 + (ord(ch) - 64)
    return total


class ErrorEscrituraKraaijeveld(Exception):
    """Error general al escribir el acumulativo Kraaijeveld."""


class EstructuraRawDataKraaijeveldError(ErrorEscrituraKraaijeveld):
    """Tabla1 no tiene la estructura esperada."""


class ContenedorDuplicadoKraaijeveldError(ErrorEscrituraKraaijeveld):
    """Uno de los contenedores ya está en Raw Data."""


class ProcesamientoKraaijeveldNoListoError(ErrorEscrituraKraaijeveld):
    """El procesamiento no está listo para escribir."""


@dataclass(frozen=True)
class ResultadoEscrituraKraaijeveld:
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
        "generacion_kraaijeveld=%s fase=%s segundos=%.3f",
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


def _xml_escape(texto: str) -> str:
    return (
        texto.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _xml_unescape(texto: str) -> str:
    return (
        texto.replace("&quot;", '"')
        .replace("&gt;", ">")
        .replace("&lt;", "<")
        .replace("&amp;", "&")
    )


def _cargar_shared_strings(zipped: ZipFile) -> list[str]:
    if SHARED_STRINGS_XML not in zipped.namelist():
        return []
    root = ET.fromstring(zipped.read(SHARED_STRINGS_XML))
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    }
    cadenas: list[str] = []
    for si in root.findall("m:si", ns):
        textos = [t.text or "" for t in si.findall(".//m:t", ns)]
        if textos:
            cadenas.append("".join(textos))
            continue
        cadenas.append("")
    return cadenas


def _valor_desde_celda_xml(
    attrs: str,
    cuerpo: str | None,
    shared: list[str],
) -> Any:
    cuerpo = cuerpo or ""
    tipo_match = _ATTR_T_RE.search(attrs)
    tipo = tipo_match.group(1) if tipo_match else ""

    if tipo == "inlineStr":
        partes = _INLINE_T_RE.findall(cuerpo)
        return _xml_unescape("".join(partes)) if partes else ""

    valor_match = _V_RE.search(cuerpo)
    if valor_match is None:
        return None
    crudo = _xml_unescape(valor_match.group(1))

    if tipo == "s":
        try:
            return shared[int(crudo)]
        except (ValueError, IndexError):
            return crudo

    if tipo == "b":
        return crudo in {"1", "true", "TRUE"}

    try:
        if "." in crudo or "e" in crudo.lower():
            return float(crudo)
        return int(crudo)
    except ValueError:
        return crudo


def _celdas_de_fila_xml(
    fila_xml: str,
    shared: list[str],
) -> dict[int, Any]:
    valores: dict[int, Any] = {}
    for match in _CELL_RE.finditer(fila_xml):
        letra, _fila, attrs, cuerpo = match.groups()
        col = column_index_from_string(letra)
        valores[col] = _valor_desde_celda_xml(attrs, cuerpo, shared)
    return valores


def _leer_encabezados_xml(
    sheet_xml: str,
    shared: list[str],
    col_ini: int,
    col_fin: int,
) -> dict[str, int]:
    fila1 = _extraer_fila_xml(sheet_xml, 1)
    valores = _celdas_de_fila_xml(fila1, shared)
    encabezados: dict[str, int] = {}
    for col in range(col_ini, col_fin + 1):
        crudo = valores.get(col)
        nombre = "" if crudo is None else str(crudo)
        if nombre in encabezados:
            raise EstructuraRawDataKraaijeveldError(
                f"El encabezado {nombre!r} está repetido."
            )
        encabezados[nombre] = col

    encontrados = set(encabezados)
    faltantes: list[str] = []
    posiciones: dict[str, int] = {}
    for nombre in COLUMNAS_ENTRADA:
        resuelto = _resolver_nombre_columna(encontrados, nombre)
        if resuelto not in encontrados:
            faltantes.append(nombre)
            continue
        posiciones[nombre] = encabezados[resuelto]

    if faltantes:
        raise EstructuraRawDataKraaijeveldError(
            "Faltan columnas digitadas en Tabla1: "
            + ", ".join(repr(n) for n in sorted(faltantes))
        )
    return posiciones


def _existe_duplicado_xml(
    sheet_xml: str,
    shared: list[str],
    posiciones: dict[str, int],
    contenedores: set[str],
    *,
    anio: int,
    semana: int,
    destino: str,
    fila_fin: int,
) -> str | None:
    if fila_fin < 2 or not contenedores:
        return None

    buscados = {
        c.strip().upper() for c in contenedores if c.strip()
    }
    destino_n = normalizar_texto(destino)
    col_cont = posiciones["Contenedor"]
    col_anio = posiciones["Año"]
    col_semana = posiciones["Semana"]
    col_destino = posiciones["Destino"]

    for fila_xml in _ROW_RE.finditer(sheet_xml):
        abierta = _ROW_OPEN_RE.match(fila_xml.group(0))
        if abierta is None:
            continue
        num_fila = int(abierta.group(1))
        if num_fila < 2 or num_fila > fila_fin:
            continue

        celdas = _celdas_de_fila_xml(fila_xml.group(0), shared)
        actual = str(celdas.get(col_cont) or "").strip().upper()
        if not actual or actual not in buscados:
            continue

        try:
            anio_existente = int(float(celdas.get(col_anio)))
        except (TypeError, ValueError):
            continue
        if anio_existente != int(anio):
            continue

        try:
            semana_existente, anio_en_semana = interpretar_semana(
                celdas.get(col_semana)
            )
        except Exception:
            continue
        if anio_en_semana is not None and anio_en_semana != anio:
            continue
        if semana_existente != int(semana):
            continue

        destino_fila = normalizar_texto(celdas.get(col_destino))
        if not destino_fila:
            continue
        if not (
            destino_fila == destino_n
            or destino_n in destino_fila
            or destino_fila in destino_n
        ):
            continue
        return actual
    return None


def _attrs_sin_tipo(attrs: str) -> str:
    limpio = re.sub(r'\bt="[^"]*"', "", attrs)
    return re.sub(r"\s+", " ", limpio).rstrip()


def _celda_valor_xml(
    col_letra: str,
    fila: int,
    attrs: str,
    valor: Any,
) -> str:
    ref = f"{col_letra}{fila}"
    base = _attrs_sin_tipo(attrs)
    if valor is None or valor == "":
        return f'<c r="{ref}"{base}/>'

    if isinstance(valor, bool):
        return (
            f'<c r="{ref}"{base} t="b">'
            f"<v>{1 if valor else 0}</v></c>"
        )

    if isinstance(valor, Decimal):
        valor = float(valor)

    if isinstance(valor, (int, float)) and not isinstance(
        valor, bool
    ):
        return f'<c r="{ref}"{base}><v>{valor}</v></c>'

    texto = _xml_escape(str(valor))
    return (
        f'<c r="{ref}"{base} t="inlineStr">'
        f"<is><t>{texto}</t></is></c>"
    )


def _fila_digitada_xml(
    fila: int,
    valores_por_col: dict[int, Any],
    *,
    col_ini: int,
    col_fin: int,
) -> str:
    """
    Solo escribe columnas digitadas. Sin fórmulas: el usuario
    las bajará manualmente al abrir el acumulativo.
    """
    spans = f"{col_ini}:{col_fin}"
    partes = [f'<row r="{fila}" spans="{spans}">']
    for col in sorted(valores_por_col):
        partes.append(
            _celda_valor_xml(
                get_column_letter(col),
                fila,
                "",
                valores_por_col[col],
            )
        )
    partes.append("</row>")
    return "".join(partes)


def construir_valores_fila_kraaijeveld(
    procesamiento: ResultadoPreparacionKraaijeveld,
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

    precio_eur = ""
    if linea.precio_venta_eur is not None and linea.precio_encontrado:
        precio_eur = decimal_a_excel(linea.precio_venta_eur)

    precio_usd = ""
    if linea.precio_venta_usd is not None and linea.precio_encontrado:
        precio_usd = decimal_a_excel(linea.precio_venta_usd)

    fila: dict[str, Any] = {
        "Semana": str(semana_texto),
        "Año": str(despacho.anio),
        "Cliente": despacho.cliente,
        "Nave": despacho.barco,
        "Contenedor": despacho.contenedor,
        "Destino": despacho.puerto_destino,
        "Tipo de fruta": despacho.tipo_fruta,
        "Cartón": despacho.carton,
        "# Calibre": str(despacho.calibre),
        "Total Cajas": despacho.total_cajas,
        "Comisión": decimal_a_excel(linea.comision),
        "Precio de Venta €": precio_eur,
        "Precio de Venta $": precio_usd,
    }
    for columna in COLUMNAS_GASTO:
        fila[columna] = decimal_a_excel(
            gastos.get(columna, Decimal("0"))
        )
    return fila


def _parsear_ref_tabla(ref: str) -> tuple[int, int, int, int]:
    # A1:CT7459
    izquierda, derecha = ref.split(":")
    match_a = re.match(r"([A-Z]+)(\d+)", izquierda)
    match_b = re.match(r"([A-Z]+)(\d+)", derecha)
    if match_a is None or match_b is None:
        raise EstructuraRawDataKraaijeveldError(
            f"Referencia de tabla inválida: {ref!r}"
        )
    col_ini = column_index_from_string(match_a.group(1))
    fila_ini = int(match_a.group(2))
    col_fin = column_index_from_string(match_b.group(1))
    fila_fin = int(match_b.group(2))
    return col_ini, fila_ini, col_fin, fila_fin


def _extraer_ref_tabla(tabla_xml: str) -> str:
    match = _TABLE_REF_RE.search(tabla_xml)
    if match is None:
        raise EstructuraRawDataKraaijeveldError(
            "No se encontró ref en table1.xml."
        )
    return match.group(1)


def _actualizar_ref_tabla(tabla_xml: str, nueva_ref: str) -> str:
    return _TABLE_REF_RE.sub(f'ref="{nueva_ref}"', tabla_xml, count=1)


def _actualizar_dimension(sheet_xml: str, nueva_ref: str) -> str:
    if _DIMENSION_RE.search(sheet_xml):
        return _DIMENSION_RE.sub(
            f'<dimension ref="{nueva_ref}"/>',
            sheet_xml,
            count=1,
        )
    return sheet_xml


def _extraer_fila_xml(sheet_xml: str, fila: int) -> str:
    match = re.search(
        rf'<row r="{fila}"[^>]*>.*?</row>',
        sheet_xml,
        re.DOTALL,
    )
    if match is None:
        raise EstructuraRawDataKraaijeveldError(
            f"No se encontró la fila plantilla {fila} en sheet1.xml."
        )
    return match.group(0)


def _insertar_filas_en_sheet(
    sheet_xml: str,
    filas_xml: str,
) -> str:
    marca = "</sheetData>"
    idx = sheet_xml.rfind(marca)
    if idx < 0:
        raise ErrorEscrituraKraaijeveld(
            "sheet1.xml no contiene </sheetData>."
        )
    return sheet_xml[:idx] + filas_xml + sheet_xml[idx:]


def _reescribir_xlsx(
    ruta: Path,
    sheet_xml: str,
    tabla_xml: str,
) -> None:
    temporal = ruta.with_suffix(".tmp.xlsx")
    with ZipFile(ruta, "r") as zin, ZipFile(
        temporal, "w"
    ) as zout:
        for info in zin.infolist():
            datos = zin.read(info.filename)
            if info.filename == HOJA_XML:
                datos = sheet_xml.encode("utf-8")
            elif info.filename == TABLA_XML:
                datos = tabla_xml.encode("utf-8")
            # Conservar metadatos de compresión del origen.
            nuevo = ZipInfo(filename=info.filename, date_time=info.date_time)
            nuevo.compress_type = info.compress_type or ZIP_DEFLATED
            nuevo.external_attr = info.external_attr
            nuevo.flag_bits = info.flag_bits
            zout.writestr(nuevo, datos)
    temporal.replace(ruta)


def escribir_archivo_kraaijeveld(
    procesamiento: ResultadoPreparacionKraaijeveld,
    ruta_archivo_cliente: str | Path,
    ruta_salida: str | Path,
    recalcular_al_final: bool = False,
) -> ResultadoEscrituraKraaijeveld:
    """
    Escribe solo columnas digitadas, sin Excel COM ni openpyxl.save.

    No copia fórmulas: al abrir el archivo el usuario las baja
    manualmente. Se preservan slicers/pivots al parchear solo
    sheet1.xml y table1.xml dentro del ZIP.
    """
    del recalcular_al_final

    if not procesamiento.puede_escribir:
        raise ProcesamientoKraaijeveldNoListoError(
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
        raise ErrorEscrituraKraaijeveld(
            "La salida no puede ser el mismo archivo de origen."
        )
    if salida.exists():
        raise FileExistsError(
            f"El archivo de salida ya existe: {salida}"
        )
    if not procesamiento.validacion.lineas_preparadas:
        raise ProcesamientoKraaijeveldNoListoError(
            "No hay líneas preparadas."
        )

    salida.parent.mkdir(parents=True, exist_ok=True)
    carpeta_trabajo = Path(
        tempfile.mkdtemp(prefix="kraaijeveld_write_")
    )
    salida_trabajo = carpeta_trabajo / "trabajo.xlsx"
    escritura_completada = False
    resultado: ResultadoEscrituraKraaijeveld | None = None

    try:
        inicio = time.perf_counter()
        shutil.copy2(origen, salida_trabajo)
        _log_fase(
            "copiar_acumulativo",
            time.perf_counter() - inicio,
        )

        inicio = time.perf_counter()
        with ZipFile(salida_trabajo, "r") as zipped:
            if HOJA_XML not in zipped.namelist():
                raise EstructuraRawDataKraaijeveldError(
                    f"No existe {HOJA_XML} en el acumulativo."
                )
            if TABLA_XML not in zipped.namelist():
                raise EstructuraRawDataKraaijeveldError(
                    f"No existe {TABLA_XML} en el acumulativo."
                )
            shared = _cargar_shared_strings(zipped)
            tabla_xml = zipped.read(TABLA_XML).decode("utf-8")
            sheet_xml = zipped.read(HOJA_XML).decode("utf-8")
            ref_actual = _extraer_ref_tabla(tabla_xml)
            col_ini, fila_enc, col_fin, fila_fin = (
                _parsear_ref_tabla(ref_actual)
            )
            if fila_enc != 1:
                raise EstructuraRawDataKraaijeveldError(
                    "Se esperaba encabezado de Tabla1 en fila 1."
                )

            posiciones = _leer_encabezados_xml(
                sheet_xml,
                shared,
                col_ini,
                col_fin,
            )
            destino = (
                procesamiento.validacion.destino_ui
                or procesamiento.despachos.destino_buscado
            )
            contenedores = {
                linea.despacho.contenedor
                for linea in procesamiento.validacion.lineas_preparadas
            }
            duplicado = _existe_duplicado_xml(
                sheet_xml,
                shared,
                posiciones,
                contenedores,
                anio=procesamiento.despachos.anio,
                semana=procesamiento.despachos.semana,
                destino=destino,
                fila_fin=fila_fin,
            )
            if duplicado:
                raise ContenedorDuplicadoKraaijeveldError(
                    f"El contenedor {duplicado} ya existe en Raw Data "
                    f"para semana {procesamiento.despachos.semana}, "
                    f"año {procesamiento.despachos.anio} y destino "
                    f"{destino}."
                )
        _log_fase(
            "validar_y_cargar_xml",
            time.perf_counter() - inicio,
        )

        cantidad = len(
            procesamiento.validacion.lineas_preparadas
        )
        fila_inicial = fila_fin + 1
        fila_final = fila_fin + cantidad
        nueva_ref = (
            f"{get_column_letter(col_ini)}{fila_enc}:"
            f"{get_column_letter(col_fin)}{fila_final}"
        )

        inicio = time.perf_counter()
        nuevas_filas: list[str] = []
        for offset in range(cantidad):
            fila_excel = fila_inicial + offset
            valores = construir_valores_fila_kraaijeveld(
                procesamiento=procesamiento,
                indice_linea=offset,
            )
            valores_por_col = {
                posiciones[nombre]: valor
                for nombre, valor in valores.items()
                if nombre in posiciones
            }
            if offset == 0:
                logger.info(
                    "generacion_kraaijeveld=%s digitado_cols=%s "
                    "(sin formulas; fill-down manual)",
                    _contexto_generacion.get(),
                    len(valores_por_col),
                )
            nuevas_filas.append(
                _fila_digitada_xml(
                    fila_excel,
                    valores_por_col,
                    col_ini=col_ini,
                    col_fin=col_fin,
                )
            )
        sheet_xml = _insertar_filas_en_sheet(
            sheet_xml, "".join(nuevas_filas)
        )
        sheet_xml = _actualizar_dimension(sheet_xml, nueva_ref)
        tabla_xml = _actualizar_ref_tabla(tabla_xml, nueva_ref)
        _log_fase(
            "construir_filas_xml",
            time.perf_counter() - inicio,
        )

        inicio = time.perf_counter()
        _reescribir_xlsx(
            salida_trabajo,
            sheet_xml=sheet_xml,
            tabla_xml=tabla_xml,
        )
        _log_fase(
            "guardar_zip",
            time.perf_counter() - inicio,
        )

        shutil.copy2(salida_trabajo, salida)
        escritura_completada = True
        resultado = ResultadoEscrituraKraaijeveld(
            archivo_origen=origen.name,
            archivo_salida=salida.name,
            filas_agregadas=cantidad,
            fila_inicial=fila_inicial,
            fila_final=fila_final,
            semana=procesamiento.despachos.semana,
            anio=procesamiento.despachos.anio,
            rango_tabla=nueva_ref,
        )
    finally:
        if not escritura_completada and salida.exists():
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
        raise ErrorEscrituraKraaijeveld(
            "El archivo de salida no fue creado."
        )
    return resultado
