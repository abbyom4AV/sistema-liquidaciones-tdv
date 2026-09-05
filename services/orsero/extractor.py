from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None  # type: ignore[assignment]


def _configurar_ruta_tesseract() -> None:
    """En Windows, usa la ruta típica si tesseract no está en PATH."""
    if pytesseract is None:
        return
    candidatos = (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    )
    for ruta in candidatos:
        if ruta.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(ruta)
            return


_configurar_ruta_tesseract()


PATRON_NUMERO = re.compile(
    r"(-?\d+(?:[.,]\d+)?)"
)
PATRON_SEMANA_NAVE = re.compile(
    r"([A-Za-zÁÉÍÓÚáéíóúüÜñÑ0-9 ]+?)\s*\.\s*(\d{1,2})\b",
)
PATRON_CAMBIO = re.compile(
    r"cambio\s*\$\s*/\s*€\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
    re.IGNORECASE,
)
PATRON_CAL = re.compile(
    r"cal\s*([0-9sóó?]|[0-9]{1,2})\b",
    re.IGNORECASE,
)
PATRON_DESTINO_BLOQUE = re.compile(
    r"\b(Setubal|Setubat|Vado|Genova|Génova|Livorno|Algeciras|"
    r"Rotterdam|Antwerp|Hamburg|Valencia|Barcelona)\b",
    re.IGNORECASE,
)

MAPEO_GASTOS = (
    ("COSTOS DE ORIGEN", "Costo en Origen Form."),
    ("INLAND", "Inland Form."),
    ("THC ORIGEN", "THC Origen Form."),
    ("FLETE + BAF + ETS", "Flete Form."),
    ("FLETE+BAF+ETS", "Flete Form."),
    ("INSURANCE", "Insurance Form."),
    ("THC DESTINO", "THC Destino Form."),
    ("FORWARDING", "Forwarding Form."),
    ("TRANSPORT IN+OUT", "Transport In Form."),
    ("TRANSPORT IN + OUT", "Transport In Form."),
    ("TRANSPORTIN+OUT", "Transport In Form."),
    ("TRANSPORTIN+QUT", "Transport In Form."),
    ("TRANSPORT IN+QUT", "Transport In Form."),
    ("COMISION", "Comision Form"),
    ("COMISIÓN", "Comision Form"),
)

GASTOS_IGNORADOS = {
    "VENTA",
    "COSTOS TOTALES",
    "VALOR NETTO EXW",
    "COSTE EXW",
    "DESCRIPCION DEL COSTO",
    "DESCRIPCIÓN DEL COSTO",
}


class ErrorExtraccionOrsero(Exception):
    """Error general al leer una liquidación Orsero."""


class FormatoLiquidacionOrseroError(ErrorExtraccionOrsero):
    """El screenshot no tiene el formato esperado."""


@dataclass(frozen=True)
class LineaPrecioOrsero:
    destino: str
    calibre: int
    total_cajas: int
    precio_eur: Decimal


@dataclass(frozen=True)
class LiquidacionOrsero:
    archivo: str
    nave_texto: str
    semana: int
    tipo_cambio_usd_eur: Decimal
    total_cajas: int
    precios: tuple[LineaPrecioOrsero, ...]
    gastos: dict[str, Decimal]
    texto_ocr: str
    rubros_no_mapeados: tuple[str, ...] = ()


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
        raise FormatoLiquidacionOrseroError(
            "Se esperaba un número y el valor está vacío."
        )
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))

    texto = str(valor).strip()
    texto = (
        texto.replace("€", "")
        .replace("$", "")
        .replace("USD", "")
        .replace("EUR", "")
        .replace("%", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .strip()
    )
    if not texto or texto in {"-", "–", "—"}:
        return Decimal("0")

    coincidencia = PATRON_NUMERO.search(texto)
    if coincidencia is None:
        raise FormatoLiquidacionOrseroError(
            f"No se pudo interpretar el número '{valor}'."
        )
    bruto = coincidencia.group(1)
    if "," in bruto and "." in bruto:
        if bruto.rfind(",") > bruto.rfind("."):
            bruto = bruto.replace(".", "").replace(",", ".")
        else:
            bruto = bruto.replace(",", "")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")
    try:
        return Decimal(bruto)
    except InvalidOperation as error:
        raise FormatoLiquidacionOrseroError(
            f"No se pudo interpretar el número '{valor}'."
        ) from error


def _numeros_en_linea(linea: str) -> list[Decimal]:
    """Extrae montos de una línea OCR (miles europeos con punto)."""
    limpia = (
        linea.replace("\xa0", " ")
        .replace("€", " ")
        .replace("$", " ")
    )
    # Solo punto como separador de miles (1.960 / 10.500,00).
    # No usar espacio: en tablas OCR separa columnas (70 840).
    limpia = re.sub(
        r"(\d)\.(\d{3})\b",
        r"\1\2",
        limpia,
    )
    limpia = re.sub(
        r"(\d)\.(\d{3})([.,]\d+)",
        r"\1\2\3",
        limpia,
    )
    # Miles con espacio solo si vienen con decimales: "2 300,00"
    limpia = re.sub(
        r"(\d)\s(\d{3})([.,]\d+)",
        r"\1\2\3",
        limpia,
    )
    resultado: list[Decimal] = []
    for match in PATRON_NUMERO.findall(limpia):
        try:
            resultado.append(parsear_numero(match))
        except FormatoLiquidacionOrseroError:
            continue
    return resultado


def _preparar_imagen_ocr(imagen: Image.Image) -> Image.Image:
    """Escala y convierte a gris: mejora tablas de screenshots."""
    if imagen.mode in {"RGBA", "P"}:
        imagen = imagen.convert("RGB")
    gris = ImageOps.grayscale(imagen)
    ancho, alto = gris.size
    # Screenshots chicos (correo/Teams) fallan sin upscale.
    if max(ancho, alto) < 1400:
        gris = gris.resize(
            (ancho * 2, alto * 2),
            Image.Resampling.LANCZOS,
        )
    return gris


def ocr_imagen(ruta: str | Path) -> str:
    if pytesseract is None:
        raise ErrorExtraccionOrsero(
            "Falta pytesseract. Instale pytesseract y Tesseract OCR."
        )
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ErrorExtraccionOrsero(
            f"No existe el screenshot: {ruta}"
        )
    imagen = Image.open(ruta)
    preparada = _preparar_imagen_ocr(imagen)
    try:
        return pytesseract.image_to_string(
            preparada,
            lang="spa+eng",
            config="--psm 4",
        )
    except pytesseract.TesseractNotFoundError as error:
        raise ErrorExtraccionOrsero(
            "Tesseract OCR no está instalado o no está en el PATH."
        ) from error


def _interpretar_calibre_ocr(token: str) -> int | None:
    texto = token.strip().lower()
    if texto.isdigit():
        valor = int(texto)
        return valor if 1 <= valor <= 20 else None
    # Errores típicos OCR: s→5, ó/o→6, ?→7
    mapa = {"s": 5, "ó": 6, "o": 6, "?": 7}
    if texto in mapa:
        return mapa[texto]
    return None


def _extraer_semana_nave(texto: str) -> tuple[str, int]:
    for coincidencia in PATRON_SEMANA_NAVE.finditer(texto):
        nave = coincidencia.group(1).strip()
        semana = int(coincidencia.group(2))
        if 1 <= semana <= 53 and len(nave) >= 3:
            # Quitar sufijo "v" de "Cala Pino v. 24"
            nave = re.sub(
                r"\s+v$",
                "",
                nave,
                flags=re.IGNORECASE,
            ).strip()
            return nave, semana
    # Fallback: "Cala Pino v. 24" / "Cala Pino v 24"
    alt = re.search(
        r"([A-Za-z][A-Za-z0-9 ]{2,}?)\s+v\.?\s*(\d{1,2})\b",
        texto,
        re.IGNORECASE,
    )
    if alt:
        return alt.group(1).strip(), int(alt.group(2))
    raise FormatoLiquidacionOrseroError(
        "No se encontró la semana en el nombre de la nave "
        "(ej. 'Cala Pino.24' o 'Cala Pino v. 24')."
    )


def _extraer_tipo_cambio(texto: str) -> Decimal:
    m = PATRON_CAMBIO.search(texto)
    if not m:
        raise FormatoLiquidacionOrseroError(
            "No se encontró el tipo de cambio $/€."
        )
    return parsear_numero(m.group(1))


def _normalizar_destino(nombre: str) -> str:
    destino = nombre.strip().upper()
    alias = {
        "SETUBAT": "SETUBAL",
        "GENOVA": "GENOVA",
        "GÉNOVA": "GENOVA",
    }
    return alias.get(destino, destino)


def _extraer_precios(texto: str) -> tuple[LineaPrecioOrsero, ...]:
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    destino_actual = ""
    precios: list[LineaPrecioOrsero] = []
    caja_plt_tipicos = {65, 70, 80, 90, 96, 100}

    for linea in lineas:
        baja = linea.lower()
        if baja.startswith("total") or "grand total" in baja:
            continue

        dest = PATRON_DESTINO_BLOQUE.search(linea)
        if dest and "cal" not in baja:
            destino_actual = _normalizar_destino(dest.group(1))
            continue

        cal = PATRON_CAL.search(linea)
        if not cal or not destino_actual:
            continue

        calibre = _interpretar_calibre_ocr(cal.group(1))
        if calibre is None:
            continue

        try:
            valores = _numeros_en_linea(linea)
        except FormatoLiquidacionOrseroError:
            continue
        if len(valores) < 2:
            continue

        # Quitar el calibre si aparece como primer entero.
        montos = list(valores)
        if (
            montos
            and montos[0] == montos[0].to_integral_value()
            and int(montos[0]) == calibre
        ):
            montos = montos[1:]

        # Precio = último valor 4–45 que venga después de cajas
        # (evita tomar plt=12/28 como precio; 14,00 puede verse entero).
        candidatos_precio = [
            (i, valor)
            for i, valor in enumerate(montos)
            if Decimal("4") <= valor <= Decimal("45")
        ]
        idx_precio = None
        precio = None
        for i, valor in candidatos_precio:
            hay_cajas_antes = any(
                m == m.to_integral_value()
                and 50 <= int(m) <= 50000
                for m in montos[:i]
            )
            if hay_cajas_antes:
                idx_precio = i
                precio = valor
        if idx_precio is None and candidatos_precio:
            # OCR sin coma: 1250 → 12,50
            for i, valor in enumerate(montos):
                if (
                    valor == valor.to_integral_value()
                    and 400 <= int(valor) <= 4500
                ):
                    precio = (valor / Decimal("100")).quantize(
                        Decimal("0.01")
                    )
                    idx_precio = i
                    montos[i] = precio
                    break
        if idx_precio is None and candidatos_precio:
            idx_precio, precio = candidatos_precio[-1]

        if idx_precio is None or precio is None:
            continue

        candidatos_cajas = [
            int(v)
            for v in montos[:idx_precio]
            if v == v.to_integral_value()
            and 50 <= int(v) <= 50000
            and int(v) not in caja_plt_tipicos
        ]
        if not candidatos_cajas:
            candidatos_cajas = [
                int(v)
                for v in montos[:idx_precio]
                if v == v.to_integral_value()
                and 50 <= int(v) <= 50000
            ]
        if not candidatos_cajas:
            continue
        total_cajas = candidatos_cajas[-1]

        precios.append(
            LineaPrecioOrsero(
                destino=destino_actual,
                calibre=calibre,
                total_cajas=total_cajas,
                precio_eur=precio,
            )
        )

    if not precios:
        raise FormatoLiquidacionOrseroError(
            "No se encontraron precios por destino/calibre "
            "en el screenshot."
        )
    return tuple(precios)


def _extraer_gastos(
    texto: str,
) -> tuple[dict[str, Decimal], tuple[str, ...]]:
    gastos: dict[str, Decimal] = {
        "Costo en Origen Form.": Decimal("0"),
        "Inland Form.": Decimal("0"),
        "THC Origen Form.": Decimal("0"),
        "Flete Form.": Decimal("0"),
        "Insurance Form.": Decimal("0"),
        "THC Destino Form.": Decimal("0"),
        "Forwarding Form.": Decimal("0"),
        "Transport In Form.": Decimal("0"),
        "Comision Form": Decimal("0"),
    }
    no_mapeados: list[str] = []
    vistos: set[str] = set()
    for linea in texto.splitlines():
        norma = normalizar_texto(linea)
        if not norma or any(
            norma.startswith(ign) for ign in GASTOS_IGNORADOS
        ):
            continue
        if norma.startswith("VENTA"):
            continue

        rubro = None
        for etiqueta, columna in MAPEO_GASTOS:
            if etiqueta in norma:
                rubro = columna
                break
        montos = _numeros_en_linea(linea)
        if rubro is None:
            # Línea de costo con monto pero sin columna digitada.
            if montos and re.search(
                r"[A-Za-zÁÉÍÓÚáéíóúüÜñÑ]{3,}",
                linea,
            ):
                etiqueta = re.sub(
                    r"[\d.,€$%\-\s]+$",
                    "",
                    linea,
                ).strip(" :.-")
                clave = normalizar_texto(etiqueta)
                if (
                    etiqueta
                    and clave
                    and clave not in vistos
                    and clave not in GASTOS_IGNORADOS
                ):
                    vistos.add(clave)
                    no_mapeados.append(etiqueta)
            continue

        # Preferir el último monto de la línea (columna $).
        if not montos:
            continue
        # Comisión: evitar tomar el 8% como monto.
        valor = montos[-1]
        if rubro == "Comision Form" and valor <= Decimal("100"):
            if len(montos) >= 2:
                valor = montos[-1]
                if valor <= Decimal("100") and len(montos) > 2:
                    valor = montos[-2]
            if valor <= Decimal("100"):
                continue
        gastos[rubro] = valor

    return gastos, tuple(no_mapeados)


def parsear_texto_liquidacion_orsero(
    texto: str,
    archivo: str = "",
) -> LiquidacionOrsero:
    nave, semana = _extraer_semana_nave(texto)
    cambio = _extraer_tipo_cambio(texto)
    precios = _extraer_precios(texto)
    gastos, rubros_no_mapeados = _extraer_gastos(texto)
    total_cajas = sum(p.total_cajas for p in precios)
    return LiquidacionOrsero(
        archivo=archivo,
        nave_texto=nave,
        semana=semana,
        tipo_cambio_usd_eur=cambio,
        total_cajas=total_cajas,
        precios=precios,
        gastos=gastos,
        texto_ocr=texto,
        rubros_no_mapeados=rubros_no_mapeados,
    )


def extraer_liquidacion_orsero(
    ruta_imagen: str | Path,
) -> LiquidacionOrsero:
    ruta = Path(ruta_imagen)
    texto = ocr_imagen(ruta)
    return parsear_texto_liquidacion_orsero(
        texto,
        archivo=str(ruta),
    )


def convertir_a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if is_dataclass(valor) and not isinstance(valor, type):
        return convertir_a_json(asdict(valor))
    if isinstance(valor, dict):
        return {
            k: convertir_a_json(v) for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return [convertir_a_json(v) for v in valor]
    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrae liquidación Orsero desde screenshot.",
    )
    parser.add_argument("--imagen", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                extraer_liquidacion_orsero(args.imagen)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
