from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Mapping

import pdfplumber

FamiliaProductoEurobanan = Literal[
    "SUMMUM",
    "SUMMUM_ALTA",
    "TREE_RIPE",
]

COLUMNAS_GASTO = (
    "Riu Fra",
    "CMA",
    "Transito",
    "Raminatrans",
    "Transitainer",
    "Hapag",
    "Altius",
    "Calidad",
    "Noatum M.",
    "Fru Port",
    "Noatum FR.",
    "Arola FR.",
    "Marmedsa FRA",
    "UCC",
    "Garland",
    "Handling",
    "Transito2",
    "Americold",
    "Cold Storage",
    "Transporte",
)

_CONTENEDOR_RE = re.compile(r"\b([A-Z]{4}\d{6,7})\b", re.IGNORECASE)
_FACTURA_RE = re.compile(
    r"Factura en consignaci[oó]n:\s*(\d+)",
    re.IGNORECASE,
)
_DESTINO_HEADER_RE = re.compile(
    r"Factura en consignaci[oó]n:\s*\d+\s*/\s*[^/]+/\s*([A-ZÁÉÍÓÚÑ ]+?)"
    r"\s+TROPICALES",
    re.IGNORECASE,
)
_TOTAL_SUMA_RE = re.compile(
    r"TOTAL\s+SUMA\s+([\d.,]+)",
    re.IGNORECASE,
)
_TOTAL_IMPORTE_RE = re.compile(
    r"TOTAL\s+IMPORTE\s*\(?EUROS?\)?\s*([\d.,]+)",
    re.IGNORECASE,
)
_GASTO_LINEA_RE = re.compile(
    r"^(.*?)([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|[\d]+[.,]\d{2})\s*$"
)
_NUMERO_RE = re.compile(
    r"-?\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?"
    r"|-?\d+,\d+"
    r"|-?\d+(?:\.\d+)?"
)
_OCR_REPETIDO_RE = re.compile(r"(.)\1{3,}")
_PRODUCTO_RE = re.compile(
    r"^([\d.]+)\s+PI[NÑ]A\b.+?CAL\.?\s*(\d+)\b",
    re.IGNORECASE,
)
_COMISION_RE = re.compile(
    r"COMISI[OÓ]N\s+([\d.,]+)\s*%?\s+([\d.,]+)",
    re.IGNORECASE,
)


class ErrorExtraccionEurobanan(Exception):
    """Error al leer una liquidación EUROBANAN."""


class FormatoLiquidacionEurobananError(ErrorExtraccionEurobanan):
    """El PDF no cumple el formato esperado."""


@dataclass(frozen=True)
class LineaProductoEurobanan:
    familia: FamiliaProductoEurobanan
    calibre: int
    bultos: int
    precio_eur: Decimal
    importe_eur: Decimal
    descripcion: str


@dataclass(frozen=True)
class LiquidacionEurobanan:
    archivo: str
    factura_corta: str
    referencia: str
    destino_pdf: str
    contenedores: tuple[str, ...]
    productos: tuple[LineaProductoEurobanan, ...]
    gastos: dict[str, Decimal]
    rubros_mapeados: tuple[str, ...]
    rubros_no_mapeados: tuple[tuple[str, Decimal], ...]
    total_cajas: int
    total_venta_eur: Decimal
    total_suma_pdf: Decimal | None
    total_importe_neto_eur: Decimal | None
    comision_pct: Decimal | None
    comision_eur: Decimal


def normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        ch for ch in texto if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def colapsar_ocr_repetido(texto: str) -> str:
    return _OCR_REPETIDO_RE.sub(r"\1", texto)


def parsear_numero(valor: Any) -> Decimal:
    if valor is None or valor == "":
        raise FormatoLiquidacionEurobananError("Número vacío.")
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))
    texto = (
        str(valor)
        .replace("€", "")
        .replace("EUR", "")
        .replace("\xa0", " ")
        .strip()
    )
    match = _NUMERO_RE.search(texto)
    if match is None:
        raise FormatoLiquidacionEurobananError(
            f"No se pudo parsear número: {valor!r}"
        )
    crudo = match.group(0).replace(" ", "").replace("\xa0", "")
    if "," in crudo and "." in crudo:
        if crudo.rfind(",") > crudo.rfind("."):
            crudo = crudo.replace(".", "").replace(",", ".")
        else:
            crudo = crudo.replace(",", "")
    elif "," in crudo:
        partes = crudo.split(",")
        if len(partes) == 2 and len(partes[1]) <= 2:
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
        raise FormatoLiquidacionEurobananError(
            f"Número inválido: {valor!r}"
        ) from error


def clave_gasto(texto: str) -> str:
    return normalizar_texto(texto)


def clasificar_familia_producto(
    descripcion: str,
) -> FamiliaProductoEurobanan:
    n = normalizar_texto(colapsar_ocr_repetido(descripcion))
    if "TREE RIPE" in n and "SUMMUM" not in n:
        return "TREE_RIPE"
    if "ALTA" in n:
        return "SUMMUM_ALTA"
    return "SUMMUM"


def familia_desde_despacho(
    tipo_empaque: str,
    carton: str,
) -> FamiliaProductoEurobanan:
    """
    Cruce con líneas del PDF:
    - INTERMEDIO → TREE RIPE
    - cartón con VERTICAL → SUMMUM ALTA (precio alto)
    - resto ESPECIAL (incl. cartón \"SUMMUM ALTA\") → SUMMUM

    En Despachos el vertical caro se marca como VERTICAL…;
    el cartón \"SUMMUM ALTA\" suele cuadrar con SUMMUM del PDF
    (totales de cajas), no con la línea SUMMUM ALTA.
    """
    tipo = normalizar_texto(tipo_empaque)
    cart = normalizar_texto(carton)
    if tipo == "INTERMEDIO":
        return "TREE_RIPE"
    if "VERTICAL" in cart:
        return "SUMMUM_ALTA"
    return "SUMMUM"


def tipo_fruta_digitada(tipo_empaque: str) -> str:
    if normalizar_texto(tipo_empaque) == "INTERMEDIO":
        return "Intermedio"
    return "Especial"


def _mapear_gasto_robusto(
    etiqueta: str,
    mapeos_extra: Mapping[str, str] | None,
) -> str | None:
    n = clave_gasto(colapsar_ocr_repetido(etiqueta))
    if not n or n.startswith("NOTA:"):
        return None
    if "TOTAL IMPORTE" in n or n.startswith("IMPORTE LIQUIDO"):
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
        ("TRANSITAINER", "Transitainer"),
        ("RAMINATRANS", "Raminatrans"),
        ("TRANSPORTE", "Transporte"),
        ("NOATUM M", "Noatum M."),
        ("NOATUM FR", "Noatum FR."),
        ("NOATUM", "Noatum M."),
        ("FRU PORT", "Fru Port"),
        ("AROLA", "Arola FR."),
        ("MARMEDSA", "Marmedsa FRA"),
        ("GARLAND", "Garland"),
        ("HANDLING", "Handling"),
        ("AMERICOLD", "Americold"),
        ("COLD STORAGE", "Cold Storage"),
        ("HAPAG", "Hapag"),
        ("ALTIUS", "Altius"),
        ("CALIDAD", "Calidad"),
        ("RIU FRA", "Riu Fra"),
        ("RIU", "Riu Fra"),
        ("RUI", "Riu Fra"),
        ("CMA", "CMA"),
        ("UCC", "UCC"),
        ("TRANSITO", "Transito"),
    ]
    for token, col in reglas:
        if token in n:
            return col
    return None


def _normalizar_linea_producto(linea: str) -> str:
    """
    Repara montos partidos por OCR:

    - ``4 .545,00`` → ``4.545,00``
    - ``1 3.612,50`` → ``13.612,50``
    - ``1 5,15`` → ``15,15`` (precio)
    - ``9 15,79`` → ``915,79`` (gasto)

    No une el final de un precio con el inicio del importe
    (``15,15 4.545,00`` / ``12,10 13.612,50`` quedan intactos).
    """
    texto = re.sub(
        r"(\d)\s+(\.\d{3},\d{2})\b",
        r"\1\2",
        linea,
    )
    texto = re.sub(
        r"(?<![\d.,])(\d)\s+(\d\.\d{3},\d{2})\b",
        r"\1\2",
        texto,
    )
    # Miles partidos: ``9 15,79`` → ``915,79``
    texto = re.sub(
        r"(?<![\d.,])(\d)\s+(\d{2},\d{2})\b",
        r"\1\2",
        texto,
    )
    return re.sub(
        r"(?<![\d.,])(\d)\s+(\d{1,2},\d{2})\b",
        r"\1\2",
        texto,
    )


def _extraer_montos_linea_producto(
    limpia: str,
    bultos: int | None = None,
) -> tuple[Decimal, Decimal] | None:
    limpia = _normalizar_linea_producto(limpia)
    numeros = [
        parsear_numero(m.group(0))
        for m in _NUMERO_RE.finditer(limpia)
    ]
    if len(numeros) < 3:
        return None
    importe = numeros[-1]
    candidatos = [
        valor
        for valor in numeros[:-1]
        if valor != importe
        and Decimal("3") <= valor <= Decimal("50")
        and valor < importe
    ]
    if not candidatos:
        candidatos = [
            valor
            for valor in numeros[:-1]
            if valor != importe and valor < importe
        ]
    if not candidatos:
        return None

    if bultos and bultos > 0:
        cajas = Decimal(bultos)

        def error_vs_importe(precio: Decimal) -> Decimal:
            return abs((precio * cajas) - importe)

        precio = min(candidatos, key=error_vs_importe)
        esperado = precio * cajas
        # Si el OCR comió miles del importe (4.545 → 545).
        if esperado != importe and esperado > importe:
            sufijo = format(importe, "f").rstrip("0").rstrip(".")
            esperado_txt = format(esperado, "f").rstrip("0").rstrip(".")
            if esperado_txt.endswith(sufijo):
                importe = esperado
        return precio, importe

    return candidatos[-1], importe


def extraer_liquidacion_eurobanan(
    ruta_pdf: str | Path,
    mapeos_extra: Mapping[str, str] | None = None,
) -> LiquidacionEurobanan:
    ruta = Path(ruta_pdf)
    if not ruta.is_file():
        raise ErrorExtraccionEurobanan(
            f"No existe el PDF: {ruta}"
        )

    with pdfplumber.open(ruta) as pdf:
        texto = "\n".join(
            (page.extract_text() or "") for page in pdf.pages
        )

    if not texto.strip():
        raise FormatoLiquidacionEurobananError(
            "El PDF no tiene texto extraíble."
        )

    texto_limpio = colapsar_ocr_repetido(texto)

    factura_match = _FACTURA_RE.search(texto_limpio)
    if factura_match is None:
        raise FormatoLiquidacionEurobananError(
            "No se encontró el número de factura."
        )
    factura = factura_match.group(1)
    factura_corta = factura[-4:] if len(factura) >= 4 else factura

    destino = ""
    dest_match = _DESTINO_HEADER_RE.search(texto_limpio)
    if dest_match:
        destino = dest_match.group(1).strip().upper()

    ref = ""
    ref_match = re.search(
        r"Factura en consignaci[oó]n:\s*\d+\s*/\s*([^/]+)/",
        texto_limpio,
        re.IGNORECASE,
    )
    if ref_match:
        ref = ref_match.group(1).strip()

    productos: list[LineaProductoEurobanan] = []
    for linea in texto_limpio.splitlines():
        limpia = _normalizar_linea_producto(
            colapsar_ocr_repetido(linea.strip())
        )
        match = _PRODUCTO_RE.match(limpia)
        if match is None:
            continue
        bultos = int(
            str(match.group(1)).replace(".", "").replace(",", "")
        )
        calibre = int(match.group(2))
        montos = _extraer_montos_linea_producto(
            limpia,
            bultos=bultos,
        )
        if montos is None:
            continue
        precio, importe = montos
        productos.append(
            LineaProductoEurobanan(
                familia=clasificar_familia_producto(limpia),
                calibre=calibre,
                bultos=bultos,
                precio_eur=precio,
                importe_eur=importe,
                descripcion=limpia,
            )
        )

    if not productos:
        raise FormatoLiquidacionEurobananError(
            "No se encontraron líneas de producto PIÑA…CAL."
        )

    contenedores: list[str] = []
    for match in _CONTENEDOR_RE.finditer(texto_limpio.upper()):
        cont = colapsar_ocr_repetido(match.group(1)).upper()
        if cont not in contenedores:
            contenedores.append(cont)

    gastos: dict[str, Decimal] = {
        col: Decimal("0") for col in COLUMNAS_GASTO
    }
    rubros_mapeados: list[str] = []
    rubros_no: list[tuple[str, Decimal]] = []
    comision_pct: Decimal | None = None
    comision_eur = Decimal("0")

    for linea in texto_limpio.splitlines():
        limpia_com = _normalizar_linea_producto(
            colapsar_ocr_repetido(linea.strip())
        )
        if not limpia_com:
            continue
        com = _COMISION_RE.search(limpia_com)
        if com is None:
            continue
        try:
            comision_pct = parsear_numero(com.group(1))
            comision_eur = parsear_numero(com.group(2))
            rubros_mapeados.append(
                f"COMISION {com.group(1)}%→Comision €={comision_eur}"
            )
        except FormatoLiquidacionEurobananError:
            continue

    for linea in texto_limpio.splitlines():
        limpia = _normalizar_linea_producto(
            colapsar_ocr_repetido(linea.strip())
        )
        if not limpia:
            continue
        n = normalizar_texto(limpia)

        if not any(
            k in n
            for k in (
                "TRANSIT",
                "TRANSPORT",
                "NOATUM",
                "CMA",
                "HAPAG",
                "RAMINATRANS",
                "HANDLING",
                "GARLAND",
                "UCC",
                "ALTIUS",
                "CALIDAD",
                "RIU",
                "RUI",
                "FRU PORT",
                "AROLA",
                "MARMEDSA",
                "AMERICOLD",
                "COLD STORAGE",
            )
        ):
            continue

        etiqueta = _CONTENEDOR_RE.sub(" ", limpia)
        etiqueta = re.sub(r"\s+", " ", etiqueta).strip()
        nums = list(_NUMERO_RE.finditer(etiqueta))
        if not nums:
            continue
        # Tomar el último número “monto” (con decimales), no un
        # folio de factura tipo 5186.
        monto_match = None
        for candidato in reversed(nums):
            crudo = candidato.group(0)
            if "," in crudo or (
                "." in crudo and len(crudo.split(".")[-1]) == 2
            ):
                monto_match = candidato
                break
        if monto_match is None:
            monto_match = nums[-1]
        monto = parsear_numero(monto_match.group(0))
        etiqueta_txt = etiqueta[: monto_match.start()].strip(" :-")

        etiqueta_txt = re.sub(
            r"^Observaciones:\s*",
            "",
            etiqueta_txt,
            flags=re.IGNORECASE,
        ).strip()
        if not etiqueta_txt or monto == 0:
            continue
        if normalizar_texto(etiqueta_txt).startswith("TOTAL"):
            continue

        columna = _mapear_gasto_robusto(
            etiqueta_txt,
            mapeos_extra,
        )
        if columna is None:
            rubros_no.append((etiqueta_txt, monto))
            continue
        gastos[columna] = gastos.get(columna, Decimal("0")) + monto
        rubros_mapeados.append(
            f"{etiqueta_txt}→{columna}={monto}"
        )

    total_suma = None
    tot = _TOTAL_SUMA_RE.search(texto_limpio)
    if tot:
        total_suma = parsear_numero(tot.group(1))

    total_neto = None
    neto = _TOTAL_IMPORTE_RE.search(texto_limpio)
    if neto:
        total_neto = parsear_numero(neto.group(1))

    total_venta = sum(
        (p.importe_eur for p in productos),
        Decimal("0"),
    )

    return LiquidacionEurobanan(
        archivo=ruta.name,
        factura_corta=factura_corta,
        referencia=ref,
        destino_pdf=destino,
        contenedores=tuple(contenedores),
        productos=tuple(productos),
        gastos=gastos,
        rubros_mapeados=tuple(rubros_mapeados),
        rubros_no_mapeados=tuple(rubros_no),
        total_cajas=sum(p.bultos for p in productos),
        total_venta_eur=total_venta,
        total_suma_pdf=total_suma,
        total_importe_neto_eur=total_neto,
        comision_pct=comision_pct,
        comision_eur=comision_eur,
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
        description="Extrae liquidación EUROBANAN PDF.",
    )
    parser.add_argument("pdf")
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                extraer_liquidacion_eurobanan(args.pdf)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
