"""Compara dos snapshots de plantillas y reporta las diferencias.

El <head> se compara como conjunto de líneas, porque el refactor
reordena a propósito el <title> y unifica el parámetro de versión del
CSS. El resto del documento se compara línea por línea.
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path


def partir(html: str) -> tuple[list[str], list[str]]:
    marca = html.find("</head>")
    if marca == -1:
        return [], html.splitlines()
    cabeza = html[:marca].splitlines()
    resto = html[marca:].splitlines()
    return cabeza, resto


def normalizar_cabeza(lineas: list[str]) -> list[str]:
    limpias = []
    for linea in lineas:
        linea = re.sub(r"tdv-app\.css\?v=[^\"']*", "tdv-app.css", linea)
        if linea.strip():
            limpias.append(linea.strip())
    return sorted(limpias)


def main() -> None:
    antes = Path(sys.argv[1])
    despues = Path(sys.argv[2])

    nombres = sorted(
        {p.name for p in antes.glob("*")}
        | {p.name for p in despues.glob("*")}
    )

    iguales = 0
    con_diferencias: list[tuple[str, list[str]]] = []
    solo_en_uno: list[str] = []

    for nombre in nombres:
        a, b = antes / nombre, despues / nombre
        if not a.is_file() or not b.is_file():
            solo_en_uno.append(nombre)
            continue

        cabeza_a, cuerpo_a = partir(a.read_text(encoding="utf-8"))
        cabeza_b, cuerpo_b = partir(b.read_text(encoding="utf-8"))

        problemas: list[str] = []
        if normalizar_cabeza(cabeza_a) != normalizar_cabeza(cabeza_b):
            problemas += [
                "  <head> distinto:",
                *list(
                    difflib.unified_diff(
                        normalizar_cabeza(cabeza_a),
                        normalizar_cabeza(cabeza_b),
                        lineterm="",
                        n=0,
                    )
                )[2:],
            ]
        if cuerpo_a != cuerpo_b:
            diff = list(
                difflib.unified_diff(
                    cuerpo_a, cuerpo_b, lineterm="", n=1
                )
            )[2:]
            problemas += ["  cuerpo distinto:", *diff[:24]]
            if len(diff) > 24:
                problemas.append(f"  ... y {len(diff) - 24} líneas más")

        if problemas:
            con_diferencias.append((nombre, problemas))
        else:
            iguales += 1

    print(f"idénticas: {iguales} de {len(nombres)}")
    if solo_en_uno:
        print(f"presentes en un solo snapshot: {solo_en_uno}")
    for nombre, problemas in con_diferencias:
        print(f"\n### {nombre}")
        for linea in problemas:
            print(linea)


if __name__ == "__main__":
    main()
