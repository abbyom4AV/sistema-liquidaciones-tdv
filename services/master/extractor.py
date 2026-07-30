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


MAPEO_COSTOS = {
    "LOCAL CHARGES": "LC Euros",
    "CUSTOMS CLEARANCE": "Cust.C Euros",
    "IMPORT DUTIES": "Import.D Euros",
    "ENERGY AND DEMURRAGE": "Ener&Demur. Euros",
    "INSPECTION": "Inspection Euros",
    "TRANSPORT PIER - WAREHOUSE": "Transport.P-W Euros",
    "TRANSPORT PIER-WAREHOUSE": "Transport.P-W Euros",
    "TRANSPORT CLIENTS/ STORAGE": "Transport C. Euros",
    "TRANSPORT CLIENTS/STORAGE": "Transport C. Euros",
    "TRANSPORT CLIENTS / STORAGE": "Transport C. Euros",
    "RELABELLING": "Relabelling Euros",
    "RELABELING": "Relabelling Euros",
}

COSTOS_IGNORADOS = {
    "ANALYSIS",
    "TOTAL LOGISTIC COSTS",
    "COST DESIGNATION",
    "COST VALUE",
}

PATRON_NUMERO = re.compile(
    r"(-?\d[\d\s]*(?:[.,]\d+)?)"
)


class ErrorExtraccionMaster(Exception):
    """Error general al leer una liquidación Master Fruits."""


class FormatoLiquidacionMasterError(ErrorExtraccionMaster):
    """El PDF no cumple el formato esperado de Master Fruits."""


@dataclass(frozen=True)
class LineaVentaMaster:
    descripcion_original: str
    variante: str
    tipo_fruta: str
    calibre: int
    boxes_in: int
    sold_boxes: int
    waste: int
    merma: int
    precio_eur: Decimal
    sale_value_eur: Decimal


@dataclass(frozen=True)
class LiquidacionMaster:
    archivo: str
    factura_corta: str
    referencia: str
    nave: str
    contenedores: tuple[str, ...]
    total_boxes: int
    total_sold_boxes: int
    comision_eur: Decimal
    total_venta_eur: Decimal
    total_costos_eur: Decimal
    productos: tuple[LineaVentaMaster, ...]
    gastos: dict[str, Decimal]
    rubros_no_mapeados: tuple[str, ...]


def normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""

    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def parsear_numero(valor: Any) -> Decimal:
    if valor is None:
        raise FormatoLiquidacionMasterError(
            "Se esperaba un número y el valor está vacío."
        )

    if isinstance(valor, Decimal):
        return valor

    if isinstance(valor, (int, float)):
        return Decimal(str(valor))

    texto = str(valor).strip()
    texto = (
        texto.replace("€", "")
        .replace("EUR", "")
        .replace("USD", "")
        .replace("\xa0", " ")
        .strip()
    )

    if not texto or texto in {"-", "–", "—"}:
        return Decimal("0")

    coincidencia = PATRON_NUMERO.search(texto)
    if coincidencia is None:
        raise FormatoLiquidacionMasterError(
            f"No se pudo interpretar el número '{valor}'."
        )

    bruto = coincidencia.group(1).replace(" ", "")
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
        raise FormatoLiquidacionMasterError(
            f"No se pudo interpretar el número '{valor}'."
        ) from error


def parsear_entero(valor: Any) -> int:
    numero = parsear_numero(valor)
    if numero != numero.to_integral_value():
        raise FormatoLiquidacionMasterError(
            f"Se esperaba un entero y se encontró '{valor}'."
        )
    return int(numero)


def clasificar_producto(descripcion: str) -> tuple[str, str]:
    texto = normalizar_texto(descripcion)

    if "VERTICAL" in texto:
        return "VERTICAL", "ESPECIAL"

    if "ESPECIAL" in texto:
        return "ESPECIAL", "ESPECIAL"

    if "PINA EXPORTACION" in texto or "PINA" in texto:
        return "VERDE", "VERDE"

    raise FormatoLiquidacionMasterError(
        "No se reconoció el tipo de producto "
        f"'{descripcion}'."
    )


def extraer_contenedores(texto: str) -> tuple[str, ...]:
    encontrados: list[str] = []

    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia.startswith("(*)"):
            continue

        cuerpo = limpia[3:].strip()
        partes = re.split(r"[_;,\s]+", cuerpo)
        for parte in partes:
            codigo = re.sub(r"[^A-Z0-9]", "", parte.upper())
            if len(codigo) >= 10:
                encontrados.append(codigo)

    return tuple(dict.fromkeys(encontrados))


def _buscar_campo_cabecera(
    texto: str,
    etiqueta: str,
) -> str | None:
    patron = re.compile(
        rf"{re.escape(etiqueta)}\s+(.+?)(?:\n|$)",
        re.IGNORECASE,
    )
    coincidencia = patron.search(texto)
    if coincidencia is None:
        return None

    valor = coincidencia.group(1).strip()
    # Evitar mezclar columnas contiguas del resumen derecho.
    valor = re.split(
        r"\s{2,}|(?=\bTOTAL\b)|(?=\bCOMISSION\b)|"
        r"(?=\bFINAL\b)|(?=\bEXTRA\b)|(?=\bCREDIT\b)",
        valor,
        maxsplit=1,
    )[0].strip()
    return valor or None


def _extraer_factura_corta(texto: str) -> str:
    # Preferir celda YOUR INVOICE de tablas cuando exista.
    coincidencia = re.search(
        r"YOUR INVOICE\s+(\d{4})\b",
        texto,
        re.IGNORECASE,
    )
    if coincidencia:
        return coincidencia.group(1)

    raise FormatoLiquidacionMasterError(
        "No se encontró YOUR INVOICE en el PDF."
    )


def _extraer_comision(texto: str) -> Decimal:
    coincidencia = re.search(
        r"COMISSION\s+([^\n]+)",
        texto,
        re.IGNORECASE,
    )
    if coincidencia is None:
        raise FormatoLiquidacionMasterError(
            "No se encontró COMISSION en el PDF."
        )
    return parsear_numero(coincidencia.group(1))


def _tabla_por_titulo(
    tablas: list[list[list[Any]]],
    titulo: str,
) -> list[list[Any]] | None:
    objetivo = normalizar_texto(titulo)
    for tabla in tablas:
        if not tabla:
            continue
        primera = normalizar_texto(
            " ".join(
                str(celda or "")
                for celda in tabla[0]
            )
        )
        if objetivo in primera:
            return tabla
    return None


def _extraer_productos(
    tabla: list[list[Any]],
) -> tuple[LineaVentaMaster, ...]:
    if len(tabla) < 3:
        raise FormatoLiquidacionMasterError(
            "La tabla RESUME SALES no tiene filas de producto."
        )

    productos: list[LineaVentaMaster] = []

    for fila in tabla[2:]:
        if not fila or all(
            celda in (None, "") for celda in fila
        ):
            continue

        producto = str(fila[0] or "").strip()
        if not producto:
            continue

        if normalizar_texto(producto).startswith(
            "TOTAL OF SALES"
        ):
            break

        if len(fila) < 7:
            raise FormatoLiquidacionMasterError(
                "Fila de ventas incompleta en RESUME SALES: "
                f"{fila!r}."
            )

        boxes_in = parsear_entero(fila[2])
        sold_boxes = parsear_entero(fila[3])
        waste = parsear_entero(fila[4])
        sale_value = parsear_numero(fila[6])
        calibre = parsear_entero(fila[1])

        if sold_boxes <= 0:
            raise FormatoLiquidacionMasterError(
                "Una línea de ventas tiene SOLD BOXES "
                f"inválido ({sold_boxes})."
            )

        merma = boxes_in - sold_boxes
        if merma < 0:
            raise FormatoLiquidacionMasterError(
                "SOLD BOXES supera BOXES IN en "
                f"'{producto}' calibre {calibre}."
            )

        # Precio = SALE VALUE / SOLD BOXES (sin redondear).
        precio = sale_value / Decimal(sold_boxes)

        variante, tipo_fruta = clasificar_producto(
            producto
        )

        productos.append(
            LineaVentaMaster(
                descripcion_original=producto,
                variante=variante,
                tipo_fruta=tipo_fruta,
                calibre=calibre,
                boxes_in=boxes_in,
                sold_boxes=sold_boxes,
                waste=waste,
                merma=merma,
                precio_eur=precio,
                sale_value_eur=sale_value,
            )
        )

    if not productos:
        raise FormatoLiquidacionMasterError(
            "No se encontraron líneas de producto en "
            "RESUME SALES."
        )

    return tuple(productos)


def _extraer_costos(
    tabla: list[list[Any]],
) -> tuple[dict[str, Decimal], tuple[str, ...]]:
    gastos: dict[str, Decimal] = {
        "LC Euros": Decimal("0"),
        "Cust.C Euros": Decimal("0"),
        "Import.D Euros": Decimal("0"),
        "Ener&Demur. Euros": Decimal("0"),
        "Inspection Euros": Decimal("0"),
        "Transport.P-W Euros": Decimal("0"),
        "Transport C. Euros": Decimal("0"),
        "Relabelling Euros": Decimal("0"),
    }
    no_mapeados: list[str] = []

    for fila in tabla:
        if not fila or len(fila) < 2:
            continue

        designacion = str(fila[0] or "").strip()
        if not designacion:
            continue

        clave = normalizar_texto(designacion)
        if clave in COSTOS_IGNORADOS or clave in {
            "RESUME COSTS",
        }:
            continue

        if clave.startswith("TOTAL"):
            continue

        columna = MAPEO_COSTOS.get(clave)
        if columna is None:
            no_mapeados.append(designacion)
            continue

        gastos[columna] = parsear_numero(fila[1])

    return gastos, tuple(no_mapeados)


def extraer_liquidacion_master(
    ruta_archivo: str | Path,
) -> LiquidacionMaster:
    ruta = Path(ruta_archivo)
    if not ruta.is_file():
        raise ErrorExtraccionMaster(
            f"No existe el archivo PDF: {ruta}"
        )

    try:
        with pdfplumber.open(str(ruta)) as documento:
            if not documento.pages:
                raise FormatoLiquidacionMasterError(
                    "El PDF no contiene páginas."
                )

            pagina = documento.pages[0]
            texto = pagina.extract_text() or ""
            tablas = pagina.extract_tables() or []
    except FormatoLiquidacionMasterError:
        raise
    except Exception as error:
        raise ErrorExtraccionMaster(
            f"No se pudo leer el PDF: {error}"
        ) from error

    if "SALES ACCOUNT" not in normalizar_texto(texto):
        raise FormatoLiquidacionMasterError(
            "El PDF no parece una SALES ACCOUNT de "
            "Master Fruits."
        )

    factura_corta = _extraer_factura_corta(texto)
    comision = _extraer_comision(texto)
    referencia = (
        _buscar_campo_cabecera(texto, "OUR REFERENCE")
        or ""
    )
    nave = _buscar_campo_cabecera(texto, "VESSEL") or ""
    contenedores = extraer_contenedores(texto)

    tabla_ventas = _tabla_por_titulo(tablas, "RESUME SALES")
    if tabla_ventas is None:
        raise FormatoLiquidacionMasterError(
            "No se encontró la tabla RESUME SALES."
        )

    tabla_costos = _tabla_por_titulo(tablas, "RESUME COSTS")
    if tabla_costos is None:
        raise FormatoLiquidacionMasterError(
            "No se encontró la tabla RESUME COSTS."
        )

    productos = _extraer_productos(tabla_ventas)
    gastos, no_mapeados = _extraer_costos(tabla_costos)
    gastos["Comision Euros"] = comision

    total_boxes = sum(p.boxes_in for p in productos)
    total_sold = sum(p.sold_boxes for p in productos)
    total_venta = sum(
        (p.sale_value_eur for p in productos),
        Decimal("0"),
    )
    total_costos = sum(
        (
            valor
            for clave, valor in gastos.items()
            if clave != "Comision Euros"
        ),
        Decimal("0"),
    )

    return LiquidacionMaster(
        archivo=ruta.name,
        factura_corta=factura_corta,
        referencia=referencia.strip(),
        nave=nave.strip(),
        contenedores=contenedores,
        total_boxes=total_boxes,
        total_sold_boxes=total_sold,
        comision_eur=comision,
        total_venta_eur=total_venta,
        total_costos_eur=total_costos,
        productos=productos,
        gastos=gastos,
        rubros_no_mapeados=no_mapeados,
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
        return [
            convertir_a_json(elemento)
            for elemento in valor
        ]

    return valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrae una liquidación Master Fruits PDF.",
    )
    parser.add_argument("--pdf", required=True)
    argumentos = parser.parse_args()
    resultado = extraer_liquidacion_master(argumentos.pdf)
    print(json.dumps(convertir_a_json(resultado), indent=2))


if __name__ == "__main__":
    main()
