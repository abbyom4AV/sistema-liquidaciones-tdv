from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber


COLUMNAS_GASTO = (
    "Demora Eur",
    "Portes Eur",
    "Gasto Puerto eur",
    "Aduanas Eur",
    "Otros3",
)

# Rubro PDF (normalizado) → columna digitada.
MAPEO_GASTOS: tuple[tuple[str, str], ...] = (
    ("DEMORA EN PUERTO", "Demora Eur"),
    ("DEMORA PUERTO", "Demora Eur"),
    ("PORTES", "Portes Eur"),
    ("GASTOS PUERTO", "Gasto Puerto eur"),
    ("GASTO PUERTO", "Gasto Puerto eur"),
    ("GASTO EN PUERTO", "Gasto Puerto eur"),
    ("ADUANAS", "Aduanas Eur"),
    ("OTROS", "Otros3"),
)

ALIAS_DESTINO = {
    "ALG": "ALGECIRAS",
    "ALGECIRAS": "ALGECIRAS",
    "ANTWERP": "AMBERES",
    "AMBERES": "AMBERES",
    "VALENCIA": "VALENCIA",
    "ROTTERDAM": "ROTTERDAM",
    "VLISSINGEN": "VLISSINGEN",
}

PATRON_NUMERO = re.compile(
    r"("
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|"
    r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\d)|"
    r"-?\d+[.,]\d+|"
    r"-?\d+"
    r")"
)
PATRON_CONTENEDOR = re.compile(
    r"\b([A-Z]{4}\d{7})\b",
    re.IGNORECASE,
)
PATRON_FILA_FACTURA = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{2,4})\s+\d+\s+\d+\s+(\d{4,})\s+Euro",
    re.IGNORECASE,
)
PATRON_FACTURA_LARGA = re.compile(r"\b(00\d{14,})\b")
PATRON_FLETE = re.compile(
    r"\bFLETE\b(?!\s*INTERNO)",
    re.IGNORECASE,
)
_CALIBRE_DESC = re.compile(
    r"(?:CAT\.?\s*1|CAT1|EXTRA)\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_ETIQUETAS_GASTO = (
    (re.compile(r"DEMORA(?:\s+EN)?\s+PUERTO\s*:?", re.I), "Demora Eur"),
    (re.compile(r"GASTOS?\s+(?:EN\s+)?PUERTO\s*:?", re.I), "Gasto Puerto eur"),
    (re.compile(r"\bADUANAS\s*:?", re.I), "Aduanas Eur"),
    (re.compile(r"\bPORTES\s*:?", re.I), "Portes Eur"),
    (re.compile(r"\bOTROS\s*:?", re.I), "Otros3"),
)
_PATRON_COMISION = re.compile(
    r"COMISI[OÓ]N\s*:?",
    re.IGNORECASE,
)
_PATRON_DTOS = re.compile(r"\bDTOS\.?\s*:?", re.IGNORECASE)
_PATRON_FLETE_ETQ = re.compile(r"\bFLETE\s*:?", re.IGNORECASE)
_PATRON_PORCENTAJE = re.compile(r"^\s*\d+[.,]?\d*\s*%\s*")


class ErrorExtraccionFruver(Exception):
    """Error general al leer un PDF FRU&VER."""


class FormatoLiquidacionFruverError(ErrorExtraccionFruver):
    """El PDF no tiene el formato esperado."""


@dataclass(frozen=True)
class LineaProductoFruver:
    descripcion: str
    calibre: int
    cajas: Decimal
    precio_eur: Decimal
    venta_eur: Decimal


@dataclass(frozen=True)
class LiquidacionFruver:
    archivo: str
    contenedor: str
    factura: str
    factura_corta: str
    productos: tuple[LineaProductoFruver, ...]
    comision: Decimal
    gastos: dict[str, Decimal]
    flete_eur: Decimal
    total_cajas: Decimal
    total_venta_eur: Decimal
    total_gastos_eur: Decimal
    rubros_no_mapeados: tuple[str, ...]
    texto: str = ""


def normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        ch for ch in texto if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def normalizar_destino(valor: Any) -> str:
    texto = normalizar_texto(valor)
    return ALIAS_DESTINO.get(texto, texto)


def formatear_destino_excel(valor: Any) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    canonico = normalizar_destino(texto)
    base = canonico if canonico else texto
    return base[:1].upper() + base[1:].lower()


def parsear_numero(valor: Any) -> Decimal:
    if valor is None or valor == "":
        raise FormatoLiquidacionFruverError("Número vacío.")
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))
    texto = (
        str(valor)
        .replace("€", "")
        .replace("$", "")
        .replace("%", "")
        .replace("EUR", "")
        .replace("\xa0", " ")
        .strip()
    )
    if not texto or texto in {"-", "–", "—"}:
        return Decimal("0")
    match = PATRON_NUMERO.search(texto.replace(" ", ""))
    if match is None:
        raise FormatoLiquidacionFruverError(
            f"No se pudo parsear número: {valor!r}"
        )
    crudo = match.group(1)
    if "," in crudo and "." in crudo:
        if crudo.rfind(",") > crudo.rfind("."):
            crudo = crudo.replace(".", "").replace(",", ".")
        else:
            crudo = crudo.replace(",", "")
    elif "," in crudo:
        partes = crudo.split(",")
        if len(partes) == 2 and len(partes[1]) <= 5:
            crudo = crudo.replace(",", ".")
        else:
            crudo = crudo.replace(",", "")
    elif "." in crudo:
        partes = crudo.split(".")
        if (
            len(partes) > 1
            and all(len(p) == 3 for p in partes[1:])
            and len(partes[-1]) == 3
            and len(partes[0]) <= 3
        ):
            crudo = crudo.replace(".", "")
    try:
        return Decimal(crudo)
    except (InvalidOperation, ValueError) as error:
        raise FormatoLiquidacionFruverError(
            f"Número inválido: {valor!r}"
        ) from error


def obtener_factura_corta(factura: str) -> str:
    digitos = re.sub(r"\D", "", str(factura))
    if len(digitos) < 4:
        raise FormatoLiquidacionFruverError(
            f"La factura '{factura}' no tiene 4 dígitos finales."
        )
    return digitos[-4:]


def _primer_monto_tras(texto: str, inicio: int) -> Decimal | None:
    resto = texto[inicio:]
    resto = _PATRON_PORCENTAJE.sub("", resto, count=1)
    match = PATRON_NUMERO.search(resto[:80])
    if match is None:
        return None
    return parsear_numero(match.group(1))


def _monto_etiqueta(texto: str, patron: re.Pattern) -> Decimal | None:
    for match in patron.finditer(texto):
        monto = _primer_monto_tras(texto, match.end())
        if monto is not None:
            return monto
    return None


def _extraer_factura(texto: str) -> str:
    lineas = texto.splitlines()
    for indice, linea in enumerate(lineas):
        n = normalizar_texto(linea)
        if "FACTURA" not in n or "FECHA" not in n:
            continue
        if indice + 1 >= len(lineas):
            continue
        match = PATRON_FILA_FACTURA.search(lineas[indice + 1])
        if match is not None:
            return match.group(2)
    match = PATRON_FILA_FACTURA.search(texto)
    if match is not None:
        return match.group(2)
    larga = PATRON_FACTURA_LARGA.search(texto)
    if larga is not None:
        return larga.group(1)
    raise FormatoLiquidacionFruverError(
        "No se encontró el número de factura en el PDF."
    )


def _mapear_gasto(rubro: str) -> str | None:
    n = normalizar_texto(rubro)
    if not n or n.startswith("TOTAL") or n.startswith("T. "):
        return None
    if n.startswith("FLETE") and "INTERNO" not in n:
        return None
    for patron, columna in MAPEO_GASTOS:
        if n == patron or n.startswith(patron + " "):
            return columna
    for patron, columna in MAPEO_GASTOS:
        if patron in n:
            return columna
    return None


def _extraer_flete(texto: str) -> Decimal:
    monto = _monto_etiqueta(texto, _PATRON_FLETE_ETQ)
    return monto if monto is not None else Decimal("0")


def _extraer_comision(texto: str) -> Decimal:
    comision = _monto_etiqueta(texto, _PATRON_COMISION)
    dtos = _monto_etiqueta(texto, _PATRON_DTOS)
    if (
        comision is not None
        and dtos is not None
        and dtos > 0
        and abs(comision - dtos) <= Decimal("1")
    ):
        return dtos
    if comision is not None and comision > 0:
        return comision
    if dtos is not None and dtos > 0:
        return dtos
    raise FormatoLiquidacionFruverError(
        "No se encontró la comisión en el PDF."
    )


def _extraer_gastos(
    texto: str,
) -> tuple[dict[str, Decimal], tuple[str, ...]]:
    gastos = {col: Decimal("0") for col in COLUMNAS_GASTO}
    for patron, columna in _ETIQUETAS_GASTO:
        monto = _monto_etiqueta(texto, patron)
        if monto is None:
            continue
        gastos[columna] += monto
    return gastos, ()


def _calibre_desde_linea(
    linea: str,
    nums: list[Decimal],
) -> int | None:
    match = _CALIBRE_DESC.search(linea)
    if match is not None:
        calibre = int(match.group(1))
        if 4 <= calibre <= 12:
            return calibre
    for num in nums[1:]:
        if num != int(num):
            continue
        calibre = int(num)
        if 4 <= calibre <= 12:
            return calibre
    return None


def _precio_desde_nums(nums: list[Decimal]) -> Decimal:
    if len(nums) >= 4:
        return nums[-2]
    return nums[-1]


def _extraer_productos(texto: str) -> tuple[LineaProductoFruver, ...]:
    productos: list[LineaProductoFruver] = []
    for linea in texto.splitlines():
        n = normalizar_texto(linea)
        if "PINA" not in n:
            continue
        nums = [
            parsear_numero(m.group(1))
            for m in PATRON_NUMERO.finditer(linea)
        ]
        if len(nums) < 2:
            continue
        calibre = _calibre_desde_linea(linea, nums)
        if calibre is None:
            continue
        cajas = nums[0]
        precio = _precio_desde_nums(nums)
        if precio > Decimal("100"):
            candidatos = [
                x
                for x in nums[1:]
                if Decimal("1") <= x <= Decimal("50")
            ]
            if candidatos:
                precio = candidatos[-1]
        venta_calc = (cajas * precio).quantize(Decimal("0.01"))
        venta = venta_calc
        if len(nums) >= 4:
            importe = nums[-1]
            if abs(venta_calc - importe) <= Decimal("0.05"):
                venta = importe
        productos.append(
            LineaProductoFruver(
                descripcion=linea.strip(),
                calibre=calibre,
                cajas=cajas,
                precio_eur=precio,
                venta_eur=venta,
            )
        )
    if not productos:
        raise FormatoLiquidacionFruverError(
            "No se encontraron líneas de producto (calibre/precio)."
        )
    return tuple(productos)


def _extraer_total_venta(texto: str, fallback: Decimal) -> Decimal:
    for etiqueta in (
        "TOTAL EN DIVISA",
        "T. NETO",
        "T.NETO",
    ):
        clave = normalizar_texto(etiqueta)
        for linea in texto.splitlines():
            n = normalizar_texto(linea)
            if clave not in n:
                continue
            nums = PATRON_NUMERO.findall(linea)
            if nums:
                return parsear_numero(nums[-1])
    for linea in texto.splitlines():
        n = normalizar_texto(linea)
        if not n.startswith("MERCANCIA"):
            continue
        if "PINA" in n:
            continue
        nums = PATRON_NUMERO.findall(linea)
        if nums:
            return parsear_numero(nums[-1])
    return fallback


def _extraer_total_gastos(texto: str, fallback: Decimal) -> Decimal:
    for linea in texto.splitlines():
        n = normalizar_texto(linea)
        if "TOTAL GASTOS" in n or n.startswith("T. GASTOS"):
            nums = PATRON_NUMERO.findall(linea)
            if nums:
                return parsear_numero(nums[-1])
    return fallback


def parsear_texto_liquidacion_fruver(
    texto: str,
    archivo: str = "",
) -> LiquidacionFruver:
    if not texto.strip():
        raise FormatoLiquidacionFruverError(
            "El PDF no tiene texto extraíble."
        )

    factura = _extraer_factura(texto)
    conts = PATRON_CONTENEDOR.findall(texto)
    if not conts:
        raise FormatoLiquidacionFruverError(
            "No se encontró el contenedor en el PDF."
        )
    contenedor = conts[0].upper()
    productos = _extraer_productos(texto)
    gastos, no_mapeados = _extraer_gastos(texto)
    comision = _extraer_comision(texto)
    flete = _extraer_flete(texto)
    total_cajas = sum((p.cajas for p in productos), Decimal("0"))
    venta_calc = sum((p.venta_eur for p in productos), Decimal("0"))
    total_venta = _extraer_total_venta(texto, venta_calc)
    total_gastos = _extraer_total_gastos(
        texto,
        sum(gastos.values(), Decimal("0")),
    )
    return LiquidacionFruver(
        archivo=archivo,
        contenedor=contenedor,
        factura=factura,
        factura_corta=obtener_factura_corta(factura),
        productos=productos,
        comision=comision,
        gastos=gastos,
        flete_eur=flete,
        total_cajas=total_cajas,
        total_venta_eur=total_venta,
        total_gastos_eur=total_gastos,
        rubros_no_mapeados=no_mapeados,
        texto=texto,
    )


def extraer_liquidacion_fruver(
    ruta_pdf: str | Path,
) -> LiquidacionFruver:
    ruta = Path(ruta_pdf)
    if not ruta.is_file():
        raise ErrorExtraccionFruver(f"No existe el PDF: {ruta}")
    with pdfplumber.open(ruta) as pdf:
        texto = "\n".join(
            (pagina.extract_text() or "") for pagina in pdf.pages
        )
    if not texto.strip():
        raise FormatoLiquidacionFruverError(
            f"El PDF no tiene texto extraíble: {ruta.name}"
        )
    return parsear_texto_liquidacion_fruver(
        texto,
        archivo=ruta.name,
    )


def extraer_liquidaciones_fruver(
    rutas: list[str | Path],
) -> tuple[LiquidacionFruver, ...]:
    return tuple(extraer_liquidacion_fruver(r) for r in rutas)


def _json_default(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if is_dataclass(valor):
        return asdict(valor)
    raise TypeError(type(valor))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="+")
    args = parser.parse_args()
    datos = extraer_liquidaciones_fruver(args.pdfs)
    print(json.dumps(datos, default=_json_default, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
