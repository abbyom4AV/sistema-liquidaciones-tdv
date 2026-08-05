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


COLUMNAS_GASTO = (
    "TL Euros",
    "TT Euros",
    "NOATUM Spain EUR",
    "TUCC Euros",
    "Transitario UCC Shipment EUR",
    "CMA EUR",
    "TFC Euros",
    " CONTROL SOLUTIONS EUR",
    "T Euros",
    "Destrucción Eur",
    "Trans Transbull",
    "Trans Hapag-Lloyd",
    "Trans Transitainer",
    "Comision Eur",
)

# Patrones en texto PDF → columna digitada (orden: más específico primero).
MAPEO_GASTOS_BASE: tuple[tuple[tuple[str, ...], str], ...] = (
    (("COMISION", "COMISIÓN"), "Comision Eur"),
    (("TRANSPORTE",), "T Euros"),
    (("LEMAIRE",), "TL Euros"),
    (("FRUPORT", "TARRAGONA"), "TT Euros"),
    (("TRANSITAINER",), "Trans Transitainer"),
    (("NOATUM",), "NOATUM Spain EUR"),
    (("TRANSBULL",), "Trans Transbull"),
    (("HAPAG",), "Trans Hapag-Lloyd"),
    (("CMA",), "CMA EUR"),
    (("FRESH CONNECTION", "FRESHCONNECTION"), "TFC Euros"),
    (("CONTROL SOLUTIONS", "CONTROL SOLUTION"), " CONTROL SOLUTIONS EUR"),
    (("UCC SHIPMENT", "UCC SHIP"), "Transitario UCC Shipment EUR"),
    (("UCC SPAIN", "UCC-OR", "TRANSITARIO UCC"), "TUCC Euros"),
    (("DESTRUCCION", "DESTRUCCIÓN", "DESTRU"), "Destrucción Eur"),
)

_CONTENEDOR_RE = re.compile(r"\b([A-Z]{4}\d{6,7})\b", re.IGNORECASE)
_PRODUCTO_RE = re.compile(
    r"^(\d+)\s+PI[NÑ]A\s+MARITIMA.*?CAL\s+(\d+)\s+"
    r"[\d.,]+\s+([\d.,]+)\s+([\d.,]+)",
    re.IGNORECASE,
)
_FACTURA_RE = re.compile(
    r"Factura en consignaci[oó]n:\s*(\d+)",
    re.IGNORECASE,
)
_DESTINO_HEADER_RE = re.compile(
    r"Factura en consignaci[oó]n:\s*\d+\s*/\s*[^/]+/\s*([A-ZÁÉÍÓÚÑ ]+?)"
    r"\s+TROPICALES",
    re.IGNORECASE,
)
_COMISION_RE = re.compile(
    r"COMISI[OÓ]N\s+([\d.,]+)\s*%?\s+([\d.,]+)",
    re.IGNORECASE,
)
_GASTO_LINEA_RE = re.compile(
    r"^(.*?)([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|[\d]+[.,]\d{2})\s*$"
)
_TOTAL_IMPORTE_RE = re.compile(
    r"TOTAL IMPORTE\s*\(EUROS\)\s*([\d.,]+)",
    re.IGNORECASE,
)
_NUMERO_RE = re.compile(
    r"-?\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?"  # 1.800,00 / 45.483,62
    r"|-?\d+,\d+"  # 11,96 / 510,00
    r"|-?\d+(?:\.\d+)?"  # 11.96 / 1800
)


class ErrorExtraccionGlamour(Exception):
    """Error al leer una liquidación Glamour."""


class FormatoLiquidacionGlamourError(ErrorExtraccionGlamour):
    """El PDF no cumple el formato esperado."""


@dataclass(frozen=True)
class LineaProductoGlamour:
    calibre: int
    bultos: int
    precio_eur: Decimal
    importe_eur: Decimal
    descripcion: str


@dataclass(frozen=True)
class LiquidacionGlamour:
    archivo: str
    factura_corta: str
    referencia: str
    destino_pdf: str
    contenedores: tuple[str, ...]
    productos: tuple[LineaProductoGlamour, ...]
    gastos: dict[str, Decimal]
    rubros_mapeados: tuple[str, ...]
    rubros_no_mapeados: tuple[tuple[str, Decimal], ...]
    total_cajas: int
    total_venta_eur: Decimal
    total_importe_neto_eur: Decimal | None
    comision_pct: Decimal | None


def normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        ch for ch in texto if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def parsear_numero(valor: Any) -> Decimal:
    if valor is None or valor == "":
        raise FormatoLiquidacionGlamourError("Número vacío.")
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
        raise FormatoLiquidacionGlamourError(
            f"No se pudo parsear número: {valor!r}"
        )
    crudo = match.group(0).replace(" ", "").replace("\xa0", "")
    if "," in crudo and "." in crudo:
        # Formato EU: miles con punto, decimales con coma.
        if crudo.rfind(",") > crudo.rfind("."):
            crudo = crudo.replace(".", "").replace(",", ".")
        else:
            crudo = crudo.replace(",", "")
    elif "," in crudo:
        partes = crudo.split(",")
        if len(partes) == 2 and len(partes[1]) <= 2:
            crudo = crudo.replace(",", ".")
        else:
            # miles con coma
            crudo = crudo.replace(",", "")
    elif "." in crudo:
        partes = crudo.split(".")
        # 1.800 sin decimales → miles EU
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
        raise FormatoLiquidacionGlamourError(
            f"Número inválido: {valor!r}"
        ) from error


def clave_gasto(texto: str) -> str:
    return normalizar_texto(texto)


def mapear_columna_gasto(
    etiqueta: str,
    mapeos_extra: Mapping[str, str] | None = None,
) -> str | None:
    n = clave_gasto(etiqueta)
    if not n:
        return None
    if mapeos_extra:
        if n in mapeos_extra:
            return mapeos_extra[n]
        for clave, col in mapeos_extra.items():
            if clave and clave in n:
                return col
    for patrones, columna in MAPEO_GASTOS_BASE:
        if all(p in n for p in patrones) or any(
            len(p) >= 5 and p in n for p in patrones
        ):
            # FRUPORT+TARRAGONA: require FRUPORT OR (TRANSITARIO and TARRAGONA without TRANSITAINER)
            if columna == "TT Euros":
                if "FRUPORT" in n or (
                    "TARRAGONA" in n
                    and "TRANSIT" in n
                    and "TRANSITAINER" not in n
                ):
                    return columna
                continue
            if any(p in n for p in patrones):
                return columna
    return None


def _mapear_gasto_robusto(
    etiqueta: str,
    mapeos_extra: Mapping[str, str] | None,
) -> str | None:
    n = clave_gasto(etiqueta)
    if not n or n.startswith("NOTA:"):
        return None
    if "TOTAL IMPORTE" in n or n.startswith("IMPORTE "):
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
        ("COMISION", "Comision Eur"),
        ("COMISI", "Comision Eur"),
        ("TRANSPORTE", "T Euros"),
        ("LEMAIRE", "TL Euros"),
        ("FRUPORT", "TT Euros"),
        ("TRANSITAINER", "Trans Transitainer"),
        ("NOATUM", "NOATUM Spain EUR"),
        ("TRANSBULL", "Trans Transbull"),
        ("HAPAG", "Trans Hapag-Lloyd"),
        ("CMA", "CMA EUR"),
        ("FRESH CONNECTION", "TFC Euros"),
        ("CONTROL SOLUTIONS", " CONTROL SOLUTIONS EUR"),
        ("UCC SHIPMENT", "Transitario UCC Shipment EUR"),
        ("UCC SHIP", "Transitario UCC Shipment EUR"),
        ("DESTRUCCION", "Destrucción Eur"),
        ("DESTRUCCI", "Destrucción Eur"),
    ]
    for token, col in reglas:
        if token in n:
            return col
    # UCC genérico (Spain) — después de UCC Shipment
    if "UCC" in n and "SHIP" not in n:
        return "TUCC Euros"
    # Transitario Tarragona genérico
    if "TARRAGONA" in n and "TRANSIT" in n and "TRANSITAINER" not in n:
        return "TT Euros"
    return None


def extraer_liquidacion_glamour(
    ruta_pdf: str | Path,
    mapeos_extra: Mapping[str, str] | None = None,
) -> LiquidacionGlamour:
    ruta = Path(ruta_pdf)
    if not ruta.is_file():
        raise ErrorExtraccionGlamour(
            f"No existe el PDF: {ruta}"
        )

    with pdfplumber.open(ruta) as pdf:
        texto = "\n".join(
            (page.extract_text() or "") for page in pdf.pages
        )

    if not texto.strip():
        raise FormatoLiquidacionGlamourError(
            "El PDF no tiene texto extraíble."
        )

    factura_match = _FACTURA_RE.search(texto)
    if factura_match is None:
        raise FormatoLiquidacionGlamourError(
            "No se encontró el número de factura."
        )
    factura = factura_match.group(1)
    factura_corta = factura[-4:] if len(factura) >= 4 else factura

    destino = ""
    dest_match = _DESTINO_HEADER_RE.search(texto)
    if dest_match:
        destino = dest_match.group(1).strip().upper()

    # Referencia entre barras
    ref = ""
    ref_match = re.search(
        r"Factura en consignaci[oó]n:\s*\d+\s*/\s*([^/]+)/",
        texto,
        re.IGNORECASE,
    )
    if ref_match:
        ref = ref_match.group(1).strip()

    productos: list[LineaProductoGlamour] = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        match = _PRODUCTO_RE.match(limpia)
        if match is None:
            continue
        bultos = int(match.group(1))
        calibre = int(match.group(2))
        precio = parsear_numero(match.group(3))
        importe = parsear_numero(match.group(4))
        productos.append(
            LineaProductoGlamour(
                calibre=calibre,
                bultos=bultos,
                precio_eur=precio,
                importe_eur=importe,
                descripcion=limpia,
            )
        )

    if not productos:
        raise FormatoLiquidacionGlamourError(
            "No se encontraron líneas de producto PIÑA…CAL."
        )

    contenedores: list[str] = []
    for match in _CONTENEDOR_RE.finditer(texto.upper()):
        cont = match.group(1).upper()
        # Filtrar falsos positivos cortos raros
        if len(cont) < 11:
            # algunos IDs en PDF salen truncados (TTNU889124)
            pass
        if cont not in contenedores:
            contenedores.append(cont)

    gastos: dict[str, Decimal] = {
        col: Decimal("0") for col in COLUMNAS_GASTO
    }
    rubros_mapeados: list[str] = []
    rubros_no: list[tuple[str, Decimal]] = []
    comision_pct: Decimal | None = None

    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia:
            continue
        n = normalizar_texto(limpia)

        com = _COMISION_RE.search(limpia)
        if com:
            try:
                comision_pct = parsear_numero(com.group(1))
                monto = parsear_numero(com.group(2))
            except FormatoLiquidacionGlamourError:
                continue
            gastos["Comision Eur"] += monto
            rubros_mapeados.append(
                f"COMISION {com.group(1)}%→Comision Eur={monto}"
            )
            continue

        if not any(
            k in n
            for k in (
                "TRANSIT",
                "TRANSPORT",
                "NOATUM",
                "CMA",
                "LEMAIRE",
                "UCC",
                "HAPAG",
                "CONTROL",
                "FRESH",
                "DESTRU",
                "FRUPORT",
                "TRANSBULL",
            )
        ):
            continue

        # Quitar contenedores del texto del gasto
        etiqueta = _CONTENEDOR_RE.sub(" ", limpia)
        etiqueta = re.sub(r"\s+", " ", etiqueta).strip()
        monto_match = _GASTO_LINEA_RE.match(etiqueta)
        if monto_match is None:
            # a veces monto está solo: buscar último número
            nums = list(_NUMERO_RE.finditer(etiqueta))
            if not nums:
                continue
            monto = parsear_numero(nums[-1].group(0))
            etiqueta_txt = etiqueta[: nums[-1].start()].strip()
        else:
            etiqueta_txt = monto_match.group(1).strip(" :-")
            monto = parsear_numero(monto_match.group(2))

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

    total_neto = None
    tot = _TOTAL_IMPORTE_RE.search(texto)
    if tot:
        total_neto = parsear_numero(tot.group(1))

    return LiquidacionGlamour(
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
        total_venta_eur=sum(
            (p.importe_eur for p in productos),
            Decimal("0"),
        ),
        total_importe_neto_eur=total_neto,
        comision_pct=comision_pct,
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
        description="Extrae liquidación Glamour PDF.",
    )
    parser.add_argument("pdf")
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                extraer_liquidacion_glamour(args.pdf)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
