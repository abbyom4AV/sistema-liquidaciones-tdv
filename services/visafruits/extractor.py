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


PATRON_NUMERO = re.compile(
    r"("
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|"  # 12.600,00
    r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|"  # 12,600.00
    r"-?\d+[.,]\d+|"  # 17,00
    r"-?\d+"
    r")"
)

# Los tres importes que cierran cada línea de distribución:
# número de cajas, precio por caja e importe total.
PATRON_COLA_PRECIO = re.compile(
    r"^(?P<etiqueta>.+?)\s+"
    r"(?P<cajas>-?[\d.,]+)\s+"
    r"(?P<precio>-?[\d.,]+)\s*€?\s+"
    r"(?P<importe>-?[\d.,]+)\s*€?\s*$"
)

PATRON_ETIQUETA_VALOR = re.compile(
    r"^(?P<etiqueta>[^\d]+?)\s+(?P<valor>-?[\d.,]+)\s*€?\s*$"
)

PATRON_CALIBRE = re.compile(r"CALIBRE\s+(\d+)")
PATRON_SEMANA = re.compile(r"WEEK\s*(\d{1,2})", re.IGNORECASE)
PATRON_CONTENEDOR = re.compile(r"[A-Z]{4}\d{6,7}")

# Rubro del PDF (normalizado) → columna digitada del acumulativo.
# Orden importa: los patrones más específicos van primero.
MAPEO_GASTOS: tuple[tuple[str, str], ...] = (
    ("DESPACHO +ADUANAS", "Despachos + Aduanas"),
    ("DESPACHO + ADUANAS", "Despachos + Aduanas"),
    ("DESPACHOS + ADUANAS", "Despachos + Aduanas"),
    ("DESPACHOS +ADUANAS", "Despachos + Aduanas"),
    ("DESPACHO Y ADUANAS", "Despachos + Aduanas"),
    ("DESPACHO ADUANAS", "Despachos + Aduanas"),
    ("TRANSPORTE", "Transporte"),
)

COLUMNAS_GASTO = ("Transporte", "Despachos + Aduanas")

# Encabezados, subtotales y totales del PDF. No son gastos, así que
# no deben reportarse como rubros sin mapear.
GASTOS_IGNORADOS: frozenset[str] = frozenset(
    {
        "VARIEDAD",
        "ORDEN NUMERO",
        "FACTURA",
        "ETD",
        "ETA",
        "CONTENEDORES",
        "NUMERO DE CONTENEDOR",
        "NUMERO DE CONTENEDORES",
        "DISTRIBUCION",
        "NUMERO DE CAJAS",
        "PRECIO CAJA",
        "IMPORTE TOTAL",
        "TOTAL DE CAJAS",
        "TOTAL CAJAS",
        # Subtotal de transporte + despachos; ya viene desglosado.
        "GASTOS LOGISTICOS",
        "GASTOS LOGISTICO",
        "COMISION VISAFRUIT",
        "COMISION VISAFRUITS",
        "COMISION",
        "TOTAL A PAGAR",
        "TOTAL",
    }
)

TIPO_ESPECIAL = "ESPECIAL"
TIPO_VERDE = "VERDE"
TIPO_INTERMEDIO = "INTERMEDIO"


class ErrorExtraccionVisafruits(Exception):
    """Error general al leer un PDF de VISAFRUITS."""


class FormatoLiquidacionVisafruitsError(ErrorExtraccionVisafruits):
    """El PDF no tiene el formato esperado."""


@dataclass(frozen=True)
class LineaPrecioVisafruits:
    """Una fila del cuadro de distribución del PDF."""

    etiqueta: str
    tipo_fruta: str
    calibre: int
    es_vertical: bool
    total_cajas: int
    precio_eur: Decimal
    importe_eur: Decimal


@dataclass(frozen=True)
class LiquidacionVisafruits:
    archivo: str
    variedad: str
    orden_numero: str
    factura: str
    factura_corta: str
    etd_texto: str
    eta_texto: str
    semana_etd: int
    contenedores: tuple[str, ...]
    contenedores_declarados: int
    precios: tuple[LineaPrecioVisafruits, ...]
    total_cajas: int
    importe_total_eur: Decimal
    comision_eur: Decimal
    gastos: dict[str, Decimal]
    total_a_pagar_eur: Decimal
    rubros_no_mapeados: tuple[str, ...]
    texto: str = ""


def normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        c for c in texto if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def parsear_numero(valor: Any) -> Decimal:
    if valor is None:
        raise FormatoLiquidacionVisafruitsError(
            "Se esperaba un número y el valor está vacío."
        )
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))

    texto = (
        str(valor)
        .replace("€", "")
        .replace("$", "")
        .replace("%", "")
        .replace("\xa0", "")
        .strip()
    )
    if not texto or texto in {"-", "–", "—"}:
        return Decimal("0")

    coincidencia = PATRON_NUMERO.search(texto.replace(" ", ""))
    if coincidencia is None:
        raise FormatoLiquidacionVisafruitsError(
            f"No se pudo interpretar el número '{valor}'."
        )
    bruto = coincidencia.group(1)

    if "," in bruto and "." in bruto:
        if bruto.rfind(",") > bruto.rfind("."):
            bruto = bruto.replace(".", "").replace(",", ".")
        else:
            bruto = bruto.replace(",", "")
    elif "," in bruto:
        partes = bruto.split(",")
        if (
            len(partes) > 1
            and all(len(p) == 3 for p in partes[1:])
            and len(partes[-1]) == 3
        ):
            bruto = "".join(partes)
        else:
            bruto = bruto.replace(",", ".")
    elif "." in bruto:
        partes = bruto.split(".")
        if (
            len(partes) > 1
            and all(len(p) == 3 for p in partes[1:])
            and len(partes[-1]) == 3
        ):
            bruto = "".join(partes)

    try:
        return Decimal(bruto)
    except InvalidOperation as error:
        raise FormatoLiquidacionVisafruitsError(
            f"No se pudo interpretar el número '{valor}'."
        ) from error


def obtener_factura_corta(factura: str) -> str:
    """Los últimos 4 dígitos, que es como se cruza con Despachos."""
    digitos = re.sub(r"\D", "", str(factura))
    if len(digitos) < 4:
        return digitos
    return digitos[-4:]


def _columna_para_rubro(etiqueta: str) -> str | None:
    norma = normalizar_texto(etiqueta)
    for patron, columna in MAPEO_GASTOS:
        if normalizar_texto(patron) in norma:
            return columna
    return None


def _es_ignorable(etiqueta: str) -> bool:
    norma = normalizar_texto(etiqueta)
    if not norma:
        return True
    if norma in GASTOS_IGNORADOS:
        return True
    return any(norma.startswith(ign) for ign in GASTOS_IGNORADOS)


def _clasificar_producto(etiqueta: str) -> tuple[str, int, bool]:
    """Devuelve (tipo de fruta, calibre, si es cartón vertical)."""
    norma = normalizar_texto(etiqueta)

    if TIPO_VERDE in norma:
        tipo = TIPO_VERDE
    elif TIPO_INTERMEDIO in norma:
        tipo = TIPO_INTERMEDIO
    else:
        tipo = TIPO_ESPECIAL

    coincidencia = PATRON_CALIBRE.search(norma)
    calibre = int(coincidencia.group(1)) if coincidencia else 0

    return tipo, calibre, "VERTICAL" in norma


def _valor_de_cabecera(lineas: list[str], etiqueta: str) -> str:
    """Texto que sigue a una etiqueta de la cabecera del PDF."""
    objetivo = normalizar_texto(etiqueta)
    for linea in lineas:
        norma = normalizar_texto(linea)
        if not norma.startswith(objetivo):
            continue
        resto = norma[len(objetivo) :].strip(" :")
        if resto:
            return resto
    return ""


def parsear_texto_liquidacion_visafruits(
    texto: str,
    archivo: str = "",
) -> LiquidacionVisafruits:
    """Interpreta el texto de un PDF de VISAFRUITS.

    Se separa del PDF para poder probar el parseo con fixtures.
    """
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    if not lineas:
        raise FormatoLiquidacionVisafruitsError(
            "El PDF no tiene texto extraíble."
        )

    variedad = _valor_de_cabecera(lineas, "VARIEDAD")
    orden_numero = _valor_de_cabecera(lineas, "ORDEN NUMERO")
    factura = _valor_de_cabecera(lineas, "FACTURA")
    etd_texto = _valor_de_cabecera(lineas, "ETD")
    eta_texto = _valor_de_cabecera(lineas, "ETA")

    contenedores: tuple[str, ...] = ()
    for linea in lineas:
        norma = normalizar_texto(linea)
        if not norma.startswith("CONTENEDORES"):
            continue
        hallados = PATRON_CONTENEDOR.findall(norma)
        if hallados:
            contenedores = tuple(dict.fromkeys(hallados))
            break

    declarados_texto = _valor_de_cabecera(
        lineas, "NUMERO DE CONTENEDORES"
    ) or _valor_de_cabecera(lineas, "NUMERO DE CONTENEDOR")
    try:
        contenedores_declarados = int(
            parsear_numero(declarados_texto or "0")
        )
    except FormatoLiquidacionVisafruitsError:
        contenedores_declarados = 0

    semana_etd = 0
    coincidencia = PATRON_SEMANA.search(etd_texto)
    if coincidencia:
        semana_etd = int(coincidencia.group(1))

    precios: list[LineaPrecioVisafruits] = []
    total_cajas = 0
    importe_total = Decimal("0")
    comision = Decimal("0")
    total_a_pagar = Decimal("0")
    gastos: dict[str, Decimal] = {}
    no_mapeados: list[str] = []
    vistos: set[str] = set()

    for linea in lineas:
        norma = normalizar_texto(linea)

        # Fila del cuadro de distribución: etiqueta + 3 importes.
        if norma.startswith("PINA") or norma.startswith("PIÑA"):
            coincidencia = PATRON_COLA_PRECIO.match(linea)
            if coincidencia is None:
                continue
            etiqueta = coincidencia.group("etiqueta").strip()
            tipo, calibre, vertical = _clasificar_producto(etiqueta)
            precios.append(
                LineaPrecioVisafruits(
                    etiqueta=etiqueta,
                    tipo_fruta=tipo,
                    calibre=calibre,
                    es_vertical=vertical,
                    total_cajas=int(
                        parsear_numero(coincidencia.group("cajas"))
                    ),
                    precio_eur=parsear_numero(
                        coincidencia.group("precio")
                    ),
                    importe_eur=parsear_numero(
                        coincidencia.group("importe")
                    ),
                )
            )
            continue

        coincidencia = PATRON_ETIQUETA_VALOR.match(linea)
        if coincidencia is None:
            continue
        etiqueta = coincidencia.group("etiqueta").strip(" :")
        norma_etiqueta = normalizar_texto(etiqueta)
        try:
            valor = parsear_numero(coincidencia.group("valor"))
        except FormatoLiquidacionVisafruitsError:
            continue

        if norma_etiqueta.startswith("TOTAL DE CAJAS"):
            total_cajas = int(valor)
            continue
        if norma_etiqueta.startswith("IMPORTE TOTAL"):
            importe_total = valor
            continue
        if norma_etiqueta.startswith("COMISION"):
            comision = valor
            continue
        if norma_etiqueta.startswith("TOTAL A PAGAR"):
            total_a_pagar = valor
            continue

        columna = _columna_para_rubro(etiqueta)
        if columna is not None:
            gastos[columna] = gastos.get(columna, Decimal("0")) + valor
            continue

        if _es_ignorable(etiqueta):
            continue

        # Un rubro de gasto siempre trae importe; si llegó hasta
        # aquí es dinero que no tiene columna en el acumulativo.
        if norma_etiqueta in vistos:
            continue
        vistos.add(norma_etiqueta)
        no_mapeados.append(etiqueta)

    if not precios:
        raise FormatoLiquidacionVisafruitsError(
            "No se encontró el cuadro de distribución en el PDF."
        )

    for columna in COLUMNAS_GASTO:
        gastos.setdefault(columna, Decimal("0"))

    return LiquidacionVisafruits(
        archivo=archivo,
        variedad=variedad,
        orden_numero=orden_numero,
        factura=factura,
        factura_corta=obtener_factura_corta(factura),
        etd_texto=etd_texto,
        eta_texto=eta_texto,
        semana_etd=semana_etd,
        contenedores=contenedores,
        contenedores_declarados=contenedores_declarados,
        precios=tuple(precios),
        total_cajas=total_cajas,
        importe_total_eur=importe_total,
        comision_eur=comision,
        gastos=gastos,
        total_a_pagar_eur=total_a_pagar,
        rubros_no_mapeados=tuple(no_mapeados),
        texto=texto,
    )


def _extraer_texto_pdf(ruta: Path) -> str:
    with pdfplumber.open(ruta) as pdf:
        partes = [
            pagina.extract_text() or "" for pagina in pdf.pages
        ]
    texto = "\n".join(partes).strip()
    if not texto:
        raise FormatoLiquidacionVisafruitsError(
            f"El PDF no tiene texto extraíble: {ruta.name}"
        )
    return texto


def extraer_liquidacion_visafruits(
    ruta: str | Path,
) -> LiquidacionVisafruits:
    camino = Path(ruta)
    if not camino.is_file():
        raise ErrorExtraccionVisafruits(
            f"No existe el archivo {camino}."
        )
    texto = _extraer_texto_pdf(camino)
    return parsear_texto_liquidacion_visafruits(texto, camino.name)


def convertir_a_json(valor: Any) -> Any:
    if is_dataclass(valor) and not isinstance(valor, type):
        return convertir_a_json(asdict(valor))
    if isinstance(valor, dict):
        return {k: convertir_a_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [convertir_a_json(v) for v in valor]
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lee una liquidación PDF de VISAFRUITS."
    )
    parser.add_argument("pdf", help="Ruta del PDF de la liquidación.")
    parser.add_argument(
        "--texto",
        action="store_true",
        help="Muestra el texto extraído en vez del JSON.",
    )
    argumentos = parser.parse_args()

    if argumentos.texto:
        print(_extraer_texto_pdf(Path(argumentos.pdf)))
        return

    liquidacion = extraer_liquidacion_visafruits(argumentos.pdf)
    datos = convertir_a_json(liquidacion)
    datos.pop("texto", None)
    print(json.dumps(datos, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
