from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

import pdfplumber

try:
    import pytesseract
except ImportError:
    pytesseract = None  # type: ignore[assignment]

COLUMNAS_GASTO = (
    "Inland",
    "Werehousing",
    "Transporte",
    "almacenaje",
    "shipping line",
    "Phytosanitary",
    "Costoms & clear",
    "Demurage",
    "Aranceles",
    "Manip fru",
    "Transp",
    "imp.plas",
    "gest adua",
    "serv log",
)

_CONTENEDOR_RE = re.compile(r"\b([A-Z]{4}\d{6,7})\b", re.IGNORECASE)
_NAVE_RE = re.compile(
    r"(?:Barco|Vessel\s+Name)\s*:\s*([A-Z0-9][A-Z0-9 \-]{1,40})",
    re.IGNORECASE,
)
_DESTINO_RE = re.compile(
    r"(?:Puerto|Port)\s*:?\s*.*?([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ \-]{2,40})",
    re.IGNORECASE,
)
_NUMERO_RE = re.compile(
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?"
    r"|-?\d+,\d+"
    r"|-?\d+(?:\.\d+)?"
)
_PRODUCTO_RE = re.compile(
    r"^([\d.,]+)\s*Box\b",
    re.IGNORECASE,
)
_CALIBRE_RE = re.compile(r"(\d+)\s*PIEZAS", re.IGNORECASE)
_IMPORTE_EUR_RE = re.compile(
    r"([\d.,]+)\s*EUR\s*$",
    re.IGNORECASE,
)
_COMISION_RE = re.compile(
    r"COMISI[OÓ]N\s*/?\s*.*?([\d.,]+)\s*%\s*([\d.,]+)\s*EUR",
    re.IGNORECASE,
)
_TOTAL_REALIZADO_RE = re.compile(
    r"Total\s+realizado\s*/?\s*Total\s+Realised\s+([\d.,]+)\s*EUR",
    re.IGNORECASE,
)
_TOTAL_NETO_RE = re.compile(
    r"Realizado\s+Neto\s*/?\s*Net\s+Proceeds\s+([\d.,]+)\s*EUR",
    re.IGNORECASE,
)
_MONTO_SOLO_RE = re.compile(
    r"^-?[\d.,]+\s*EUR\s*[.|]?\s*$",
    re.IGNORECASE,
)
_ETIQUETAS_NO_GASTO = (
    "TOTAL REALIZADO",
    "TOTAL COSTES",
    "NET PROCEEDS",
    "REALIZADO NETO",
    "ADVANCED",
    "ADELANTO",
    "AMOUNT DUE",
    "IMPORTE A LIQUIDAR",
    "COST/BOX",
    "COSTE/CAJA",
    "COSTE/KG",
    "SUB-TOTAL",
    "PRODUCE SUB",
    "GASTOS Y SERVICIOS",
    "COSTS & SERVICES",
    "TOTAL COSTS",
    "COSTES Y SERVICIOS",
)


class ErrorExtraccionNufri(Exception):
    """Error al leer una liquidación NUFRI."""


class FormatoLiquidacionNufriError(ErrorExtraccionNufri):
    """El PDF no cumple el formato esperado."""


@dataclass(frozen=True)
class LineaProductoNufri:
    calibre: int
    bultos: int
    importe_eur: Decimal
    descripcion: str
    es_vertical: bool


def es_caja_vertical_pdf(descripcion: str) -> bool:
    """Env:CRV / Env.CRV en el PDF = caja vertical."""
    return bool(
        re.search(r"\bCRV\b", normalizar_texto(descripcion))
    )


def es_caja_vertical_despacho(carton: str) -> bool:
    """Cartón Despachos que contiene VERTICAL."""
    return "VERTICAL" in normalizar_texto(carton)


@dataclass(frozen=True)
class LiquidacionNufri:
    archivo: str
    pagina_pdf: int
    nave: str
    destino_pdf: str
    contenedores: tuple[str, ...]
    productos: tuple[LineaProductoNufri, ...]
    gastos: dict[str, Decimal]
    comision_pct: Decimal | None
    comision_eur: Decimal
    rubros_mapeados: tuple[str, ...]
    rubros_no_mapeados: tuple[tuple[str, Decimal], ...]
    total_cajas: int
    total_venta_eur: Decimal
    total_neto_eur: Decimal | None


def normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        ch for ch in texto if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def clave_gasto(etiqueta: str) -> str:
    return normalizar_texto(etiqueta)


def parsear_numero(valor: str) -> Decimal:
    texto = (valor or "").strip().replace(" ", "")
    if not texto:
        raise FormatoLiquidacionNufriError("Número vacío.")
    negativo = texto.startswith("-")
    if negativo:
        texto = texto[1:]
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        if len(partes[-1]) <= 2:
            texto = "".join(partes[:-1]).replace(".", "") + "." + partes[-1]
        else:
            texto = texto.replace(",", "")
    else:
        pass
    try:
        numero = Decimal(texto)
    except InvalidOperation as error:
        raise FormatoLiquidacionNufriError(
            f"No se pudo interpretar el número {valor!r}."
        ) from error
    return -numero if negativo else numero


def parsear_cajas_eu(valor: str) -> int:
    texto = (valor or "").strip()
    if not texto:
        raise FormatoLiquidacionNufriError("Cantidad vacía.")
    entero = parsear_numero(texto.split()[0] if " " in texto else texto)
    return int(entero)


def _configurar_tesseract() -> None:
    if pytesseract is None:
        return
    for ruta in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if ruta.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(ruta)
            break


_configurar_tesseract()


def ocr_pagina_pdf(ruta_pdf: Path, pagina: int) -> str:
    if pytesseract is None:
        raise ErrorExtraccionNufri(
            "Falta pytesseract. Instale pytesseract y Tesseract OCR."
        )
    if pagina < 1:
        raise FormatoLiquidacionNufriError(
            "La página del PDF debe ser >= 1."
        )
    with pdfplumber.open(ruta_pdf) as pdf:
        if pagina > len(pdf.pages):
            raise FormatoLiquidacionNufriError(
                f"El PDF tiene {len(pdf.pages)} páginas; "
                f"se pidió la página {pagina}."
            )
        page = pdf.pages[pagina - 1]
        texto = page.extract_text() or ""
        if texto.strip():
            return texto
        imagen = page.to_image(resolution=200).original
        try:
            return pytesseract.image_to_string(
                imagen,
                lang="spa+eng",
            )
        except Exception as error:
            raise ErrorExtraccionNufri(
                f"No se pudo aplicar OCR a la página {pagina}."
            ) from error


def _mapear_gasto_robusto(
    etiqueta: str,
    mapeos_extra: Mapping[str, str] | None,
) -> str | None:
    n = clave_gasto(etiqueta)
    if not n:
        return None
    if "COMISION" in n or "COMISI" in n:
        return None
    if mapeos_extra:
        if n in mapeos_extra:
            return mapeos_extra[n]
        for clave, col in sorted(
            mapeos_extra.items(),
            key=lambda x: -len(x[0]),
        ):
            if clave and clave in n:
                return col

    reglas: list[tuple[str, str]] = [
        ("INLAND TRANSPORT", "Inland"),
        ("TRANSPORTES INTERNOS", "Inland"),
        ("WEREHOUS", "Werehousing"),
        ("WAREHOUS", "Werehousing"),
        ("SHIPPING LINES DESTINATION", "shipping line"),
        ("GASTOS DESTINO NAVIERA", "shipping line"),
        ("DEMURRAGE", "Demurage"),
        ("DEMORAS Y OCUPACIONES", "Demurage"),
        ("CUSTOMS", "Costoms & clear"),
        ("GASTOS ADUANAS", "Costoms & clear"),
        ("PHYTOSANITARY", "Phytosanitary"),
        ("ARANCEL", "Aranceles"),
        ("MANIP", "Manip fru"),
        ("ALMACEN", "almacenaje"),
        ("TRANSPORTE", "Transporte"),
        ("IMP PLAST", "imp.plas"),
        ("IMP. PLAST", "imp.plas"),
        ("GEST ADUAN", "gest adua"),
        ("GEST. ADUAN", "gest adua"),
        ("SERV LOG", "serv log"),
        ("SERV. LOG", "serv log"),
    ]
    for token, col in reglas:
        if token in n:
            return col
    return None


def _parsear_linea_producto(linea: str) -> LineaProductoNufri | None:
    limpia = linea.strip()
    if not limpia or not _PRODUCTO_RE.match(limpia):
        return None
    if "PIÑA" not in limpia.upper() and "PINA" not in limpia.upper():
        return None
    if "SUB-TOTAL" in limpia.upper() or "SUBTOTAL" in limpia.upper():
        return None

    match_cajas = _PRODUCTO_RE.match(limpia)
    if match_cajas is None:
        return None
    bultos = parsear_cajas_eu(match_cajas.group(1))

    match_cal = _CALIBRE_RE.search(limpia)
    if match_cal is None:
        return None
    calibre = int(match_cal.group(1))

    match_imp = _IMPORTE_EUR_RE.search(limpia)
    if match_imp is None:
        nums = list(_NUMERO_RE.finditer(limpia))
        if not nums:
            return None
        importe = parsear_numero(nums[-1].group(0))
    else:
        importe = parsear_numero(match_imp.group(1))

    return LineaProductoNufri(
        calibre=calibre,
        bultos=bultos,
        importe_eur=importe,
        descripcion=limpia,
        es_vertical=es_caja_vertical_pdf(limpia),
    )


def _es_linea_excluida_gasto(normalizada: str) -> bool:
    return any(k in normalizada for k in _ETIQUETAS_NO_GASTO)


def _monto_eur_en_linea(limpia: str) -> tuple[Decimal, int] | None:
    """Devuelve (monto, índice_inicio) si hay un EUR con número."""
    if "EUR" not in limpia.upper():
        return None
    nums = list(_NUMERO_RE.finditer(limpia))
    if not nums:
        return None
    monto_match = None
    for candidato in reversed(nums):
        if "," in candidato.group(0):
            monto_match = candidato
            break
    if monto_match is None:
        monto_match = nums[-1]
    try:
        monto = parsear_numero(monto_match.group(0))
    except FormatoLiquidacionNufriError:
        return None
    if monto == 0:
        return None
    return monto, monto_match.start()


def _extraer_gastos_y_comision(
    texto: str,
    mapeos_extra: Mapping[str, str] | None,
) -> tuple[
    dict[str, Decimal],
    Decimal | None,
    Decimal,
    tuple[str, ...],
    tuple[tuple[str, Decimal], ...],
]:
    gastos: dict[str, Decimal] = {
        col: Decimal("0") for col in COLUMNAS_GASTO
    }
    rubros_mapeados: list[str] = []
    rubros_no: list[tuple[str, Decimal]] = []
    comision_pct: Decimal | None = None
    comision_eur = Decimal("0")

    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    etiquetas_sueltas: list[tuple[str, str]] = []

    for limpia in lineas:
        n = normalizar_texto(limpia)

        com = _COMISION_RE.search(limpia)
        if com:
            try:
                comision_pct = parsear_numero(com.group(1))
                comision_eur = parsear_numero(com.group(2))
                rubros_mapeados.append(
                    f"COMISION {com.group(1)}%={comision_eur}"
                )
            except FormatoLiquidacionNufriError:
                pass
            continue

        monto_info = _monto_eur_en_linea(limpia)
        if monto_info is None:
            if _es_linea_excluida_gasto(n):
                continue
            columna = _mapear_gasto_robusto(limpia, mapeos_extra)
            if columna is not None:
                etiquetas_sueltas.append((limpia, columna))
            continue

        monto, inicio = monto_info
        if _MONTO_SOLO_RE.match(limpia):
            # Monto huérfano: se empareja después.
            continue

        etiqueta = limpia[:inicio].strip(" :-|>Eea853rd(")
        if not etiqueta or len(etiqueta) < 4:
            continue
        if _es_linea_excluida_gasto(n):
            continue

        columna = _mapear_gasto_robusto(etiqueta, mapeos_extra)
        if columna is None:
            if any(
                k in n
                for k in (
                    "TRANSPORT",
                    "GASTO",
                    "DEMOR",
                    "CUSTOM",
                    "INLAND",
                    "SHIPPING",
                    "WAREHOUS",
                    "ALMACEN",
                )
            ):
                rubros_no.append((etiqueta, monto))
            continue
        gastos[columna] = gastos.get(columna, Decimal("0")) + monto
        rubros_mapeados.append(
            f"{etiqueta}→{columna}={monto}"
        )

    # OCR a veces deja etiqueta y monto en líneas distintas.
    pendientes = [
        (eti, col)
        for eti, col in etiquetas_sueltas
        if gastos.get(col, Decimal("0")) == 0
    ]
    if pendientes:
        idx_total = next(
            (
                i
                for i, ln in enumerate(lineas)
                if "TOTAL COSTES" in normalizar_texto(ln)
                or "TOTAL COSTS" in normalizar_texto(ln)
            ),
            len(lineas),
        )
        montos_sueltos: list[Decimal] = []
        for ln in lineas[:idx_total]:
            if not _MONTO_SOLO_RE.match(ln):
                continue
            monto_info = _monto_eur_en_linea(ln)
            if monto_info is None:
                continue
            montos_sueltos.append(monto_info[0])

        for (etiqueta, columna), monto in zip(
            pendientes,
            montos_sueltos,
        ):
            gastos[columna] = (
                gastos.get(columna, Decimal("0")) + monto
            )
            rubros_mapeados.append(
                f"{etiqueta}→{columna}={monto}"
            )

        if len(pendientes) > len(montos_sueltos):
            for etiqueta, _col in pendientes[len(montos_sueltos):]:
                rubros_no.append((etiqueta, Decimal("0")))

    return (
        gastos,
        comision_pct,
        comision_eur,
        tuple(rubros_mapeados),
        tuple(rubros_no),
    )


def extraer_liquidacion_nufri(
    ruta_pdf: str | Path,
    *,
    pagina_pdf: int,
    mapeos_extra: Mapping[str, str] | None = None,
) -> LiquidacionNufri:
    ruta = Path(ruta_pdf)
    if not ruta.is_file():
        raise ErrorExtraccionNufri(
            f"No existe el PDF: {ruta}"
        )

    texto = ocr_pagina_pdf(ruta, int(pagina_pdf))
    if not texto.strip():
        raise FormatoLiquidacionNufriError(
            f"La página {pagina_pdf} no tiene texto legible."
        )

    nave = ""
    match_nave = _NAVE_RE.search(texto)
    if match_nave:
        nave = match_nave.group(1).strip().split("\n")[0].strip()

    destino = ""
    for linea in texto.splitlines():
        n = normalizar_texto(linea)
        if "ALGECIRAS" in n or "BARCELONA" in n or "VALENCIA" in n:
            if "PUERTO" in n or "COSTA RICA" in n:
                partes = re.split(r"[.\-]", linea, maxsplit=1)
                candidato = partes[-1].strip() if partes else linea
                for puerto in re.findall(
                    r"\b([A-ZÁÉÍÓÚÑ]{4,20})\b",
                    candidato.upper(),
                ):
                    if puerto not in (
                        "COSTA",
                        "RICA",
                        "TROPICALES",
                        "DEL",
                        "VALLE",
                    ):
                        destino = puerto
                        break
        if destino:
            break
    if not destino:
        match_dest = re.search(
            r"\.\s*([A-ZÁÉÍÓÚÑ]{4,20})\s*$",
            texto,
            re.MULTILINE,
        )
        if match_dest:
            destino = match_dest.group(1).strip()

    contenedores: list[str] = []
    for match in _CONTENEDOR_RE.finditer(texto.upper()):
        cont = match.group(1).upper()
        if cont not in contenedores:
            contenedores.append(cont)

    productos: list[LineaProductoNufri] = []
    for linea in texto.splitlines():
        producto = _parsear_linea_producto(linea)
        if producto is not None:
            productos.append(producto)

    if not productos:
        raise FormatoLiquidacionNufriError(
            "No se encontraron líneas de producto PIÑA…Box."
        )

    (
        gastos,
        comision_pct,
        comision_eur,
        rubros_mapeados,
        rubros_no_mapeados,
    ) = _extraer_gastos_y_comision(texto, mapeos_extra)

    total_venta = sum(
        (p.importe_eur for p in productos),
        Decimal("0"),
    )
    match_total = _TOTAL_REALIZADO_RE.search(texto)
    if match_total:
        total_pdf = parsear_numero(match_total.group(1))
        if abs(total_pdf - total_venta) > Decimal("1"):
            total_venta = total_pdf

    total_neto = None
    match_neto = _TOTAL_NETO_RE.search(texto)
    if match_neto:
        total_neto = parsear_numero(match_neto.group(1))

    return LiquidacionNufri(
        archivo=ruta.name,
        pagina_pdf=int(pagina_pdf),
        nave=nave,
        destino_pdf=destino,
        contenedores=tuple(contenedores),
        productos=tuple(productos),
        gastos=gastos,
        comision_pct=comision_pct,
        comision_eur=comision_eur,
        rubros_mapeados=rubros_mapeados,
        rubros_no_mapeados=rubros_no_mapeados,
        total_cajas=sum(p.bultos for p in productos),
        total_venta_eur=total_venta,
        total_neto_eur=total_neto,
    )


def convertir_a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if is_dataclass(valor) and not isinstance(valor, type):
        return convertir_a_json(asdict(valor))
    if isinstance(valor, dict):
        return {k: convertir_a_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [convertir_a_json(v) for v in valor]
    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrae liquidación NUFRI de una página PDF.",
    )
    parser.add_argument("pdf")
    parser.add_argument(
        "--pagina",
        type=int,
        required=True,
        help="Número de página (1-based).",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                extraer_liquidacion_nufri(
                    args.pdf,
                    pagina_pdf=args.pagina,
                )
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
