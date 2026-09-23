from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()

# Suficiente para montos y evita residuos típicos de float (…0000002).
_PRECISION_UI = Decimal("0.000001")


@register.filter(name="decimal_es")
def decimal_es(valor) -> str:
    """
    Presentación decimal en español sin ceros finales.
    No altera el valor almacenado.
    """
    if valor is None or valor == "":
        return ""

    try:
        if isinstance(valor, float):
            numero = Decimal(str(valor))
        else:
            numero = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return str(valor)

    if not numero.is_finite():
        return str(valor)

    if numero == 0:
        return "0"

    numero = numero.quantize(_PRECISION_UI, rounding=ROUND_HALF_UP)
    texto = format(numero, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")
