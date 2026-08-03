from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, is_dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber


PATRON_NUMERO = re.compile(
    r"("
    r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|"  # 1.008,00 / -1.008,00
    r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|"  # 1,008.00
    r"-?\d+[.,]\d+|"  # 525,00 / 9.00
    r"-?\d+"
    r")"
)
PATRON_CONTENEDOR = re.compile(
    r"Container\s+([A-Z0-9]+)",
    re.IGNORECASE,
)
PATRON_FACTURA = re.compile(
    r"External\s+reference\s+(\d+)",
    re.IGNORECASE,
)
PATRON_COMMISSION_ORDER = re.compile(
    r"Commission\s+order\s+(\S+)",
    re.IGNORECASE,
)
PATRON_SUPER_SWEET = re.compile(
    r"Pineapple\s+Super\s+Sweet\s+(\d+)\b",
    re.IGNORECASE,
)
PATRON_PINEAPPLES = re.compile(
    r"Pineapples?\s+(\d+)\b",
    re.IGNORECASE,
)
PATRON_CROWNLESS = re.compile(
    r"Crownless",
    re.IGNORECASE,
)
PATRON_CROWNLESS_CALIBRE = re.compile(
    r"Crownless\s+(\d+)\b",
    re.IGNORECASE,
)

# Rubro PDF (normalizado) → columna digitada.
# Orden importa: patrones más específicos primero.
MAPEO_GASTOS: tuple[tuple[str, str], ...] = (
    ("LOGISTIC HANDLING CHARGES", "Logistics.C"),
    ("IMPORT TRANSPORT FROM HARBOUR TO KRAAIJEVELD", "Import.T"),
    ("IMPORT TRANSPORT BY SEA", "Import.T S.A"),
    ("IMPORT TRANSPORT BY AIR", "Import.T S.A"),
    ("IMPORT TRANSPORT SEA", "Import.T S.A"),
    ("IMPORT TRANSPORT AIR", "Import.T S.A"),
    ("SEA / BY AIR", "Import.T S.A"),
    ("I.T SEA", "Import.T S.A"),
    ("OTHER IMPORT COSTS", "Other Import C."),
    ("SCANNING COST", "Scanning Cost"),
    ("SCANNING", "Scanning Cost"),
    ("DEMURRAGE", "Demourge Cost"),
    ("DEMURAGE", "Demourge Cost"),
    ("EXPORT COSTS SALES", "Export C.S"),
    ("EXPORT COSTS", "Export C.S"),
    ("REPACK COSTS", "Repack Costs"),
    ("REPACK", "Repack Costs"),
)

COLUMNAS_GASTO = (
    "Logistics.C",
    "Import.T",
    "Import.T S.A",
    "Scanning Cost",
    "Other Import C.",
    "Demourge Cost",
    "Export C.S",
    "Repack Costs",
)


class ErrorExtraccionKraaijeveld(Exception):
    """Error general al leer un PDF Kraaijeveld."""


class FormatoLiquidacionKraaijeveldError(ErrorExtraccionKraaijeveld):
    """El PDF no tiene el formato esperado."""


@dataclass(frozen=True)
class LineaProductoKraaijeveld:
    descripcion: str
    variante: str  # ESPECIAL | VERDE | CROWNLESS
    calibre: int
    precio_eur: Decimal
    cantidad: int
    comision_pct: Decimal | None


@dataclass(frozen=True)
class LiquidacionKraaijeveld:
    archivo: str
    contenedor: str
    factura: str
    factura_corta: str
    commission_order: str
    productos: tuple[LineaProductoKraaijeveld, ...]
    comision: Decimal
    gastos: dict[str, Decimal]
    rubros_no_mapeados: tuple[str, ...]
    tiene_crownless: bool
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
        raise FormatoLiquidacionKraaijeveldError(
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
        raise FormatoLiquidacionKraaijeveldError(
            f"No se pudo interpretar el número '{valor}'."
        )
    bruto = coincidencia.group(1)

    if "," in bruto and "." in bruto:
        # EU: 1.008,00  |  US: 1,008.00
        if bruto.rfind(",") > bruto.rfind("."):
            bruto = bruto.replace(".", "").replace(",", ".")
        else:
            bruto = bruto.replace(",", "")
    elif "," in bruto:
        # 525,00 o 1,008 (miles US sin decimales)
        partes = bruto.split(",")
        if (
            len(partes) > 1
            and all(len(p) == 3 for p in partes[1:])
            and len(partes[-1]) == 3
            and "." not in bruto
        ):
            bruto = "".join(partes)
        else:
            bruto = bruto.replace(",", ".")
    elif "." in bruto:
        # 9.00 decimal  |  1.008 miles EU sin decimales
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
        raise FormatoLiquidacionKraaijeveldError(
            f"No se pudo interpretar el número '{valor}'."
        ) from error


def obtener_factura_corta(factura: str) -> str:
    digitos = re.sub(r"\D", "", str(factura))
    if len(digitos) < 4:
        raise FormatoLiquidacionKraaijeveldError(
            f"La factura '{factura}' no tiene 4 dígitos finales."
        )
    return digitos[-4:]


def clasificar_descripcion(descripcion: str) -> tuple[str, int]:
    if PATRON_CROWNLESS.search(descripcion):
        # El número tras Crownless se ignora para matching de precio;
        # se guarda solo como referencia si aparece.
        cal = PATRON_CROWNLESS_CALIBRE.search(descripcion)
        calibre = int(cal.group(1)) if cal else 0
        return "CROWNLESS", calibre

    sweet = PATRON_SUPER_SWEET.search(descripcion)
    if sweet:
        return "ESPECIAL", int(sweet.group(1))

    pine = PATRON_PINEAPPLES.search(descripcion)
    if pine:
        return "VERDE", int(pine.group(1))

    raise FormatoLiquidacionKraaijeveldError(
        f"No se pudo clasificar el producto: {descripcion!r}."
    )


def _extraer_texto_pdf(ruta: Path) -> str:
    with pdfplumber.open(ruta) as pdf:
        partes = []
        for pagina in pdf.pages:
            partes.append(pagina.extract_text() or "")
    texto = "\n".join(partes).strip()
    if not texto:
        raise FormatoLiquidacionKraaijeveldError(
            f"El PDF no tiene texto extraíble: {ruta.name}"
        )
    return texto


def _parsear_productos(
    texto: str,
) -> tuple[LineaProductoKraaijeveld, ...]:
    productos: list[LineaProductoKraaijeveld] = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia or not re.match(r"^\d{4,5}\b", limpia):
            continue
        if "Pineapple" not in limpia and "pineapple" not in limpia:
            continue

        try:
            variante, calibre = clasificar_descripcion(limpia)
        except FormatoLiquidacionKraaijeveldError:
            continue
        # Crownless: el número es irrelevante; no descartar si falta.
        if calibre <= 0 and variante != "CROWNLESS":
            continue

        # Quitar el line no. inicial y tomar montos de la cola:
        # Net HE, Price, Quantity HE, Com %, Revenue
        sin_codigo = re.sub(r"^\d{4,5}\s+", "", limpia)
        numeros = [
            parsear_numero(n)
            for n in PATRON_NUMERO.findall(
                sin_codigo.replace(" ", "")
                if False
                else sin_codigo
            )
        ]
        # Mejor: números de la línea completa tras description keywords
        nums = [parsear_numero(n) for n in PATRON_NUMERO.findall(limpia)]
        # nums[0] suele ser line no. (10000); luego calibre ya capturado
        # Típico: line, calibre?, net, price, qty, com%, revenue
        # En texto: "10000 Pineapple Super Sweet 5, CR... 11,00 9,00 1275 3,0 11.475,00"
        candidatos = [
            n
            for n in nums
            if not (
                n == n.to_integral_value()
                and int(n) in {calibre, int(nums[0])}
            )
        ]
        # Preferir estructura: net(~11), price(4-40), qty(>=10), com%, revenue
        precio = None
        cantidad = None
        comision_pct = None
        for i, valor in enumerate(candidatos):
            if (
                Decimal("4") <= valor <= Decimal("40")
                and precio is None
                and (
                    valor != valor.to_integral_value()
                    or int(valor) not in {11}
                )
            ):
                # net HE suele ser 11,00 — saltar el primero ~11 si hay otro precio
                pass
        # Heurística más estable: últimos 4 números útiles antes de revenue
        # Buscar qty entero >= 10, precio 4-40 distinto de 11 si hay 11 antes
        enteros_grandes = [
            int(v)
            for v in candidatos
            if v == v.to_integral_value() and 10 <= int(v) <= 50000
        ]
        precios_pos = [
            v
            for v in candidatos
            if Decimal("3") <= v <= Decimal("50")
        ]
        # net=11, price, qty, com%, revenue
        # Tomar: penúltimo decimal/entero pequeño como com%, precio el de ventas
        if len(candidatos) >= 4:
            # revenue = último (grande), com% = penúltimo si <= 20
            # qty = enteros grandes no revenue
            revenue = candidatos[-1]
            posible_com = candidatos[-2]
            if Decimal("0") <= posible_com <= Decimal("30"):
                comision_pct = posible_com
                resto = candidatos[:-2]
            else:
                resto = candidatos[:-1]
            # qty = último entero grande en resto
            for v in reversed(resto):
                if v == v.to_integral_value() and int(v) >= 10:
                    cantidad = int(v)
                    break
            # price = valor 3-50 en resto que no sea 11 si hay alternativas,
            # preferir el que esté justo antes de qty
            if cantidad is not None:
                idx_qty = None
                for i, v in enumerate(resto):
                    if v == v.to_integral_value() and int(v) == cantidad:
                        idx_qty = i
                if idx_qty is not None and idx_qty > 0:
                    precio = resto[idx_qty - 1]
            if precio is None:
                for v in resto:
                    if Decimal("4") <= v <= Decimal("40") and v != Decimal("11"):
                        precio = v
                        break
            if precio is None:
                for v in resto:
                    if Decimal("4") <= v <= Decimal("40"):
                        precio = v
                        break

        if precio is None or cantidad is None:
            continue

        productos.append(
            LineaProductoKraaijeveld(
                descripcion=limpia,
                variante=variante,
                calibre=calibre,
                precio_eur=precio,
                cantidad=cantidad,
                comision_pct=comision_pct,
            )
        )

    if not productos:
        raise FormatoLiquidacionKraaijeveldError(
            "No se encontraron líneas de producto en el PDF."
        )
    return tuple(productos)


def _mapear_gasto(descripcion: str) -> str | None:
    norma = normalizar_texto(descripcion)
    for etiqueta, columna in MAPEO_GASTOS:
        if etiqueta in norma:
            return columna
    return None


def _extraer_comision_y_gastos(
    texto: str,
) -> tuple[Decimal, dict[str, Decimal], tuple[str, ...]]:
    gastos = {col: Decimal("0") for col in COLUMNAS_GASTO}
    comision = Decimal("0")
    no_mapeados: list[str] = []
    en_costos = False

    for linea in texto.splitlines():
        limpia = linea.strip()
        norma = normalizar_texto(limpia)
        if norma.startswith("DESCRIPTION") and "COST" in norma:
            en_costos = True
            continue
        if not en_costos:
            continue
        if norma.startswith("SUBTOTAL") or norma.startswith(
            "TO BE INVOICED"
        ):
            break
        if not limpia or limpia.startswith("-") is False and (
            "Commission" not in limpia
            and not any(
                k.lower() in limpia.lower()
                for k, _ in MAPEO_GASTOS
            )
            and "cost" not in limpia.lower()
            and "transport" not in limpia.lower()
            and "handling" not in limpia.lower()
            and "scanning" not in limpia.lower()
            and "repack" not in limpia.lower()
            and "demur" not in limpia.lower()
            and "export" not in limpia.lower()
        ):
            # Línea de costo típica tiene montos negativos
            if not PATRON_NUMERO.search(limpia):
                continue

        montos = [parsear_numero(n) for n in PATRON_NUMERO.findall(limpia)]
        if not montos:
            continue
        # Usar valor absoluto del último monto (columna Costs).
        valor = abs(montos[-1])

        if "COMMISSION" in norma and "ORDER" not in norma:
            comision = valor
            continue

        columna = _mapear_gasto(limpia)
        if columna:
            gastos[columna] = valor
            continue

        # Posible rubro nuevo con monto
        if valor > 0 and not norma.startswith("SUBTOTAL"):
            # Evitar basura
            if len(limpia) >= 4 and any(c.isalpha() for c in limpia):
                no_mapeados.append(limpia)

    return comision, gastos, tuple(dict.fromkeys(no_mapeados))


def parsear_texto_liquidacion_kraaijeveld(
    texto: str,
    archivo: str = "",
) -> LiquidacionKraaijeveld:
    cont = PATRON_CONTENEDOR.search(texto)
    fact = PATRON_FACTURA.search(texto)
    if cont is None:
        raise FormatoLiquidacionKraaijeveldError(
            "No se encontró el contenedor en el PDF."
        )
    if fact is None:
        raise FormatoLiquidacionKraaijeveldError(
            "No se encontró External reference (factura) en el PDF."
        )

    contenedor = cont.group(1).strip().upper()
    factura = fact.group(1).strip()
    factura_corta = obtener_factura_corta(factura)
    order = PATRON_COMMISSION_ORDER.search(texto)
    commission_order = order.group(1).strip() if order else ""

    productos = _parsear_productos(texto)
    comision, gastos, no_mapeados = _extraer_comision_y_gastos(texto)
    tiene_crownless = any(
        p.variante == "CROWNLESS" for p in productos
    )

    return LiquidacionKraaijeveld(
        archivo=archivo,
        contenedor=contenedor,
        factura=factura,
        factura_corta=factura_corta,
        commission_order=commission_order,
        productos=productos,
        comision=comision,
        gastos=gastos,
        rubros_no_mapeados=no_mapeados,
        tiene_crownless=tiene_crownless,
        texto=texto,
    )


def extraer_liquidacion_kraaijeveld(
    ruta_pdf: str | Path,
) -> LiquidacionKraaijeveld:
    ruta = Path(ruta_pdf)
    if not ruta.is_file():
        raise ErrorExtraccionKraaijeveld(
            f"No existe el PDF: {ruta}"
        )
    texto = _extraer_texto_pdf(ruta)
    return parsear_texto_liquidacion_kraaijeveld(
        texto,
        archivo=ruta.name,
    )


def extraer_liquidaciones_kraaijeveld(
    rutas: list[str | Path],
) -> tuple[LiquidacionKraaijeveld, ...]:
    return tuple(
        extraer_liquidacion_kraaijeveld(ruta) for ruta in rutas
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
        description="Extrae liquidación Kraaijeveld desde PDF.",
    )
    parser.add_argument("--pdf", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            convertir_a_json(
                extraer_liquidacion_kraaijeveld(args.pdf)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
