"""Mensajes compartidos para gastos de liquidación no mapeados."""

from __future__ import annotations


def mensaje_gastos_no_mapeados(
    rubros: list[str] | tuple[str, ...],
) -> str:
    limpios = [str(r).strip() for r in rubros if str(r).strip()]
    lista = ", ".join(limpios) if limpios else "(sin detalle)"
    return (
        f"El PDF incluye gastos no mapeados: {lista}. "
        "Agréguelos en el archivo del cliente y vuelva a "
        "generar la solicitud."
    )


def etiquetas_rubros(
    rubros: list | tuple,
) -> list[str]:
    """Normaliza rubros str o (etiqueta, monto) a lista de etiquetas."""
    out: list[str] = []
    for item in rubros:
        if isinstance(item, (tuple, list)) and item:
            out.append(str(item[0]).strip())
        else:
            out.append(str(item).strip())
    return [x for x in out if x]
