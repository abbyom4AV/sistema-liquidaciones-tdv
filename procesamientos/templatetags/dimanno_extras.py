from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter(name="decimal_es")
def decimal_es(valor) -> str:
    """
    Presentación decimal en español sin ceros finales.
    No altera el valor almacenado.
    """
    if valor is None or valor == "":
        return ""

    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return str(valor)

    if numero == 0:
        return "0"

    texto = format(numero, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")
