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
    "Gasto Puerto",
    "Gasto Trans",
    "Gasto Handl",
    "G.Inspección",
    "G.Customs Duties",
)

MAPEO_GASTO_HEADER: tuple[tuple[tuple[str, ...], str], ...] = (
    (("PUERTO",), "Gasto Puerto"),
    (("TRANSPORTE", "TRANSPORT"), "Gasto Trans"),
    (("HANDLING", "HANDL"), "Gasto Handl"),
    (("INSPECC", "INSPECTION"), "G.Inspección"),
    (("CUSTOMS", "DUTIES"), "G.Customs Duties"),
)

ALIAS_DESTINO = {
    "ANTWERP": "AMBERES",
    "ANTWERPEN": "AMBERES",
}

_CONTENEDOR_RE = re.compile(r"\b([A-Z]{4}\d{6,7})\b", re.I)
_HEADER_RE = re.compile(
    r"LIQUIDACI[OÓ]N\s+(\d{1,2})-(\d{4})\s+(\S+)\s+(.+?)\s+"
    r"FAC:\s*(\d+)",
    re.I,
)
_LINEA_RE = re.compile(
    r"^(?P<contenedor>[A-Z]{4}\d{6,7})\s+"
    r"(?P<cliente>.+?)\s+"
    r"(?P<fecha>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<tipo>COL|VER)\s+"
    r"(?P<calibre>CAL\d+(?:CL\d+)?)\s+"
    r"(?P<resto>.+)$",
    re.I,
)
_TOTAL_GENERAL_RE = re.compile(
    r"^Total\s+general\s+(.+)$",
    re.I,
)
_TOTAL_CONTENEDOR_RE = re.compile(
    r"^Total\s+[A-Z]{4}\d{6,7}\b",
    re.I,
)
_NOTA_RE = re.compile(
    r"NOTA:\s*(?P<body>.+?)(?=\n\S|\Z)",
    re.I | re.S,
)
_RECLAMO_MONTO_RE = re.compile(
    r"POR\s+([\d.,]+)\s*EUR",
    re.I,
)
_NUMERO_RE = re.compile(
    r"-?\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?"
    r"|-?\d+,\d+"
    r"|-?\d+(?:\.\d+)?"
)
_CALIBRE_NUM_RE = re.compile(r"CAL(\d+)", re.I)
_SIN_COLILLA_RE = re.compile(
    r"\s*SIN\s+COLILLA\s*",
    re.I,
)


class ErrorExtraccionTdvEuropa(Exception):
    """Error al leer una liquidación TDV Europa."""


class FormatoLiquidacionTdvEuropaError(ErrorExtraccionTdvEuropa):
    """El PDF no cumple el formato esperado."""


@dataclass(frozen=True)
class LineaProductoTdvEuropa:
    contenedor: str
    cliente: str
    fecha_llegada: str
    tipo_raw: str
    tipo_fruta: str
    calibre: int
    calibre_raw: str
    carton: str
    carton_clave: str
    cajas_netas: Decimal
    venta_bruta_eur: Decimal
    precio_caja_eur: Decimal
    es_merma: bool


@dataclass(frozen=True)
class ReclamoTdvEuropa:
    contenedor: str
    gasto_columna: str
    monto_eur: Decimal
    columna_reclamo: str
    cliente_detectado: str
    texto: str


@dataclass(frozen=True)
class LiquidacionTdvEuropa:
    archivo: str
    semana: int
    anio: int
    destino_pdf: str
    nave: str
    factura_completa: str
    factura_corta: str
    lineas: tuple[LineaProductoTdvEuropa, ...]
    mermas: tuple[LineaProductoTdvEuropa, ...]
    gastos: dict[str, Decimal]
    comision_eur: Decimal
    total_cajas_netas: Decimal
    total_venta_eur: Decimal
    reclamos: tuple[ReclamoTdvEuropa, ...]
    rubros_no_mapeados: tuple[tuple[str, Decimal], ...]
    advertencias: tuple[str, ...] = ()


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
    """Destino para Excel: solo la primera letra en mayúscula."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    # Usa canónico (Antwerp→AMBERES) y luego Title Case.
    canonico = normalizar_destino(texto)
    base = canonico if canonico else texto
    return base[:1].upper() + base[1:].lower()


def parsear_numero(valor: Any) -> Decimal:
    if valor is None or valor == "":
        raise FormatoLiquidacionTdvEuropaError("Número vacío.")
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
        raise FormatoLiquidacionTdvEuropaError(
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
        raise FormatoLiquidacionTdvEuropaError(
            f"Número inválido: {valor!r}"
        ) from error


def limpiar_carton(valor: str) -> str:
    texto = re.sub(r"\s+", " ", (valor or "").strip())
    texto = _SIN_COLILLA_RE.sub(" ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def clave_carton(valor: str) -> str:
    return normalizar_texto(limpiar_carton(valor))


def tipo_fruta_desde(tipo_raw: str, calibre_raw: str) -> str:
    cal = normalizar_texto(calibre_raw)
    if "CL" in cal:
        return "Crownless Especial"
    tipo = normalizar_texto(tipo_raw)
    if tipo == "VER":
        return "Verde"
    return "Especial"


def calibre_desde(calibre_raw: str) -> int:
    match = _CALIBRE_NUM_RE.search(calibre_raw or "")
    if not match:
        raise FormatoLiquidacionTdvEuropaError(
            f"Calibre inválido: {calibre_raw!r}"
        )
    return int(match.group(1))


def _partir_carton_y_montos(
    resto: str,
) -> tuple[str, Decimal, list[Decimal]]:
    """
    Separa cartón + cajas + montos con €.
    Esperado: '<carton> <cajas> <n> € <n> € ...'
    """
    match = re.search(
        r"^(?P<carton>.+?)\s+"
        r"(?P<cajas>-?[\d.,]+)\s+"
        r"(?P<montos>(?:-?[\d.,]+\s*€\s*)+)$",
        resto.strip(),
        re.I,
    )
    if match is None:
        raise FormatoLiquidacionTdvEuropaError(
            f"No se pudo separar cartón/montos: {resto!r}"
        )
    carton = match.group("carton").strip()
    cajas = parsear_numero(match.group("cajas"))
    montos = [
        parsear_numero(raw)
        for raw in re.findall(
            r"-?[\d.,]+(?=\s*€)",
            match.group("montos"),
        )
    ]
    if len(montos) < 3:
        raise FormatoLiquidacionTdvEuropaError(
            f"Faltan montos en línea: {resto!r}"
        )
    return carton, cajas, montos


def _parsear_linea_producto(
    linea: str,
) -> LineaProductoTdvEuropa | None:
    texto = re.sub(r"\s+", " ", linea.strip())
    if not texto:
        return None
    if _TOTAL_CONTENEDOR_RE.match(texto) or _TOTAL_GENERAL_RE.match(
        texto
    ):
        return None
    if texto.upper().startswith("LIQUIDACI"):
        return None
    if "CONTENEDOR" in texto.upper() and "CALIBRE" in texto.upper():
        return None

    match = _LINEA_RE.match(texto)
    if match is None:
        return None

    carton, cajas, montos = _partir_carton_y_montos(
        match.group("resto")
    )
    venta_bruta = montos[2]
    precio_caja = (
        (venta_bruta / cajas)
        if cajas != 0 and venta_bruta != 0
        else Decimal("0")
    )
    cliente = match.group("cliente").strip()
    calibre_raw = match.group("calibre").upper()
    tipo_raw = match.group("tipo").upper()
    return LineaProductoTdvEuropa(
        contenedor=match.group("contenedor").upper(),
        cliente=cliente,
        fecha_llegada=match.group("fecha"),
        tipo_raw=tipo_raw,
        tipo_fruta=tipo_fruta_desde(tipo_raw, calibre_raw),
        calibre=calibre_desde(calibre_raw),
        calibre_raw=calibre_raw,
        carton=limpiar_carton(carton),
        carton_clave=clave_carton(carton),
        cajas_netas=cajas,
        venta_bruta_eur=venta_bruta,
        precio_caja_eur=precio_caja,
        es_merma=normalizar_texto(cliente) == "MERMA",
    )


def _parsear_total_general(
    linea: str,
) -> tuple[Decimal, Decimal, dict[str, Decimal], Decimal]:
    match = _TOTAL_GENERAL_RE.match(
        re.sub(r"\s+", " ", linea.strip())
    )
    if match is None:
        raise FormatoLiquidacionTdvEuropaError(
            "No se encontró la fila Total general."
        )
    resto = match.group(1)
    # cajas + montos €
    partes = re.match(
        r"^(-?[\d.,]+)\s+((?:-?[\d.,]+\s*€\s*)+)$",
        resto.strip(),
        re.I,
    )
    if partes is None:
        raise FormatoLiquidacionTdvEuropaError(
            f"Total general ilegible: {linea!r}"
        )
    total_cajas = parsear_numero(partes.group(1))
    montos = [
        parsear_numero(raw)
        for raw in re.findall(
            r"-?[\d.,]+(?=\s*€)",
            partes.group(2),
        )
    ]
    # idx: 0 kg, 1 precio caja, 2 venta bruta, 3 puerto,
    # 4 trans, 5 handl, 6 comision, ...
    if len(montos) < 7:
        raise FormatoLiquidacionTdvEuropaError(
            f"Total general con pocos montos: {linea!r}"
        )
    gastos = {
        "Gasto Puerto": montos[3],
        "Gasto Trans": montos[4],
        "Gasto Handl": montos[5],
        "G.Inspección": Decimal("0"),
        "G.Customs Duties": Decimal("0"),
    }
    return total_cajas, montos[2], gastos, montos[6]


def _detectar_rubros_header(texto: str) -> tuple[str, ...]:
    """Busca etiquetas de gasto en el encabezado de columnas."""
    upper = normalizar_texto(texto)
    encontrados: list[str] = []
    if "GASTO EN PUERTO" in upper or "GASTO PUERTO" in upper:
        encontrados.append("Gasto Puerto")
    if "GASTO DE TRANSPORTE" in upper or "GASTO TRANSPORTE" in upper:
        encontrados.append("Gasto Trans")
    if "GASTO HANDLING" in upper or "HANDLING" in upper:
        encontrados.append("Gasto Handl")
    # Rubros adicionales no mapeados a digitadas conocidas
    extras: list[str] = []
    for etiqueta in (
        "INSPECCION",
        "INSPECTION",
        "CUSTOMS",
        "DESTRUCCION",
        "STORAGE",
        "ALMACENAJE",
    ):
        if etiqueta in upper and etiqueta not in (
            "HANDLING",
        ):
            extras.append(etiqueta)
    return tuple(dict.fromkeys(encontrados + extras))


def _parsear_reclamos(texto: str) -> tuple[ReclamoTdvEuropa, ...]:
    reclamos: list[ReclamoTdvEuropa] = []
    for match in _NOTA_RE.finditer(texto):
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        body_n = normalizar_texto(body)
        monto_m = _RECLAMO_MONTO_RE.search(body)
        if monto_m is None or "RECLAMO" not in body_n:
            continue
        monto = parsear_numero(monto_m.group(1))
        cont_m = _CONTENEDOR_RE.search(body)
        contenedor = cont_m.group(1).upper() if cont_m else ""

        if "HANDLING" in body_n or "HANDL" in body_n:
            gasto_col = "Gasto Handl"
        elif "PUERTO" in body_n:
            gasto_col = "Gasto Puerto"
        elif "TRANSPORTE" in body_n or "TRANSPORT" in body_n:
            gasto_col = "Gasto Trans"
        else:
            gasto_col = ""

        if "MERCADONA" in body_n:
            col_reclamo = "Reclamos Mercado"
            cliente = "MERCADONA"
        elif "IRMADONA" in body_n:
            col_reclamo = "Reclamos Irmadoña"
            cliente = "IRMADONA"
        else:
            col_reclamo = ""
            cliente = ""

        reclamos.append(
            ReclamoTdvEuropa(
                contenedor=contenedor,
                gasto_columna=gasto_col,
                monto_eur=monto,
                columna_reclamo=col_reclamo,
                cliente_detectado=cliente,
                texto=body,
            )
        )
    return tuple(reclamos)


def extraer_liquidacion_tdv_europa(
    ruta_archivo: str | Path,
) -> LiquidacionTdvEuropa:
    ruta = Path(ruta_archivo)
    if not ruta.is_file():
        raise ErrorExtraccionTdvEuropa(
            f"No existe el PDF: {ruta}"
        )

    with pdfplumber.open(ruta) as pdf:
        paginas = [
            (pagina.extract_text() or "") for pagina in pdf.pages
        ]
    texto = "\n".join(paginas)
    if not texto.strip():
        raise FormatoLiquidacionTdvEuropaError(
            "El PDF no tiene texto extractable."
        )

    header = None
    for linea in texto.splitlines():
        header = _HEADER_RE.search(
            re.sub(r"\s+", " ", linea.strip())
        )
        if header:
            break
    if header is None:
        raise FormatoLiquidacionTdvEuropaError(
            "No se encontró el encabezado LIQUIDACIÓN "
            "semana-año destino nave FAC."
        )

    semana = int(header.group(1))
    anio = int(header.group(2))
    destino_pdf = normalizar_destino(header.group(3))
    nave = re.sub(r"\s+", " ", header.group(4)).strip()
    factura_completa = header.group(5)
    factura_corta = factura_completa[-4:]
    if not (factura_corta.isdigit() and len(factura_corta) == 4):
        raise FormatoLiquidacionTdvEuropaError(
            f"Factura corta inválida: {factura_completa!r}"
        )

    lineas: list[LineaProductoTdvEuropa] = []
    total_general_line: str | None = None
    for cruda in texto.splitlines():
        limpia = re.sub(r"\s+", " ", cruda.strip())
        if not limpia:
            continue
        if _TOTAL_GENERAL_RE.match(limpia):
            total_general_line = limpia
            continue
        producto = _parsear_linea_producto(limpia)
        if producto is not None:
            lineas.append(producto)

    if not lineas:
        raise FormatoLiquidacionTdvEuropaError(
            "No se extrajeron líneas de producto del PDF."
        )
    if total_general_line is None:
        raise FormatoLiquidacionTdvEuropaError(
            "No se encontró Total general en el PDF."
        )

    total_cajas_pdf, total_venta, gastos, comision = (
        _parsear_total_general(total_general_line)
    )

    reclamos = _parsear_reclamos(texto)
    advertencias: list[str] = []
    rubros_no_mapeados: list[tuple[str, Decimal]] = []

    for reclamo in reclamos:
        if not reclamo.columna_reclamo:
            advertencias.append(
                "Reclamo en NOTA sin cliente Mercadona/"
                f"Irmadona identificable: {reclamo.texto}"
            )
        if not reclamo.gasto_columna:
            advertencias.append(
                "Reclamo en NOTA sin rubro de gasto "
                f"identificable: {reclamo.texto}"
            )
            continue
        actual = gastos.get(reclamo.gasto_columna, Decimal("0"))
        gastos[reclamo.gasto_columna] = actual - reclamo.monto_eur

    # Header: avisar rubros desconocidos (además de los 3 fijos).
    rubros_header = _detectar_rubros_header(texto[:2500])
    conocidos = {
        "Gasto Puerto",
        "Gasto Trans",
        "Gasto Handl",
        "G.Inspección",
        "G.Customs Duties",
    }
    for rubro in rubros_header:
        if rubro not in conocidos:
            rubros_no_mapeados.append((rubro, Decimal("0")))
            advertencias.append(
                f"Rubro de gasto no mapeado en el PDF: {rubro}"
            )

    clientes = tuple(l for l in lineas if not l.es_merma)
    mermas = tuple(l for l in lineas if l.es_merma)
    suma_netas = sum(
        (l.cajas_netas for l in lineas),
        Decimal("0"),
    )
    # Total general incluye mermas en cajas.
    if abs(suma_netas - total_cajas_pdf) > Decimal("0.05"):
        advertencias.append(
            "La suma de cajas de líneas no cuadra con "
            f"Total general ({suma_netas} vs {total_cajas_pdf})."
        )

    suma_venta = sum(
        (l.venta_bruta_eur for l in clientes),
        Decimal("0"),
    )
    if abs(suma_venta - total_venta) > Decimal("0.05"):
        advertencias.append(
            "La suma de venta bruta de clientes no cuadra con "
            f"Total general ({suma_venta} vs {total_venta})."
        )

    return LiquidacionTdvEuropa(
        archivo=ruta.name,
        semana=semana,
        anio=anio,
        destino_pdf=destino_pdf,
        nave=nave,
        factura_completa=factura_completa,
        factura_corta=factura_corta,
        lineas=clientes,
        mermas=mermas,
        gastos=gastos,
        comision_eur=comision,
        total_cajas_netas=sum(
            (l.cajas_netas for l in clientes),
            Decimal("0"),
        ),
        total_venta_eur=total_venta,
        reclamos=reclamos,
        rubros_no_mapeados=tuple(rubros_no_mapeados),
        advertencias=tuple(advertencias),
    )


def convertir_a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if is_dataclass(valor) and not isinstance(valor, type):
        return convertir_a_json(asdict(valor))
    if isinstance(valor, dict):
        return {
            clave: convertir_a_json(contenido)
            for clave, contenido in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return [convertir_a_json(elemento) for elemento in valor]
    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrae liquidación TDV Europa (PDF).",
    )
    parser.add_argument("pdf")
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                extraer_liquidacion_tdv_europa(args.pdf)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
