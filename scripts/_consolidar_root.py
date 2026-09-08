"""Junta en la hoja global los :root repetidos de las plantillas.

Cada pantalla arrastraba su propia paleta con nombres alternativos
(--fondo, --tarjeta, --suave...). Los valores coinciden entre sí, así
que se declaran una sola vez en la hoja global y se borran de las
plantillas. Si alguna define un valor distinto al consolidado, esa
declaración se conserva donde está.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RAIZ = BASE / "procesamientos" / "templates" / "procesamientos"
GLOBAL = (
    BASE / "procesamientos" / "static" / "procesamientos" / "tdv-app.css"
)

RE_ROOT = re.compile(r"[ \t]*:root\s*\{([^}]*)\}\s*", re.S)
RE_VAR = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")

MARCA = "  /* Alias heredados de los <style> de las pantallas. */"


def main() -> None:
    css_global = GLOBAL.read_text(encoding="utf-8")
    bloque_global = css_global[: css_global.index("}")]
    ya_global = {
        k: v.strip() for k, v in RE_VAR.findall(bloque_global)
    }

    # Cuántas plantillas usan cada valor de cada variable.
    conteo: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for ruta in sorted(RAIZ.glob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        for m in RE_ROOT.finditer(texto):
            for nombre, valor in RE_VAR.findall(m.group(1)):
                conteo[nombre][valor.strip()] += 1

    # Se consolida el valor mayoritario de cada variable nueva.
    consolidado: dict[str, str] = {}
    for nombre, valores in conteo.items():
        if nombre in ya_global:
            continue
        consolidado[nombre] = max(valores.items(), key=lambda x: x[1])[0]

    quitadas = 0
    conservadas = 0
    tocadas = 0
    for ruta in sorted(RAIZ.glob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        if not RE_ROOT.search(texto):
            continue

        def reemplazar(m: re.Match[str]) -> str:
            nonlocal quitadas, conservadas
            sobreviven = []
            for nombre, valor in RE_VAR.findall(m.group(1)):
                valor = valor.strip()
                esperado = consolidado.get(nombre, ya_global.get(nombre))
                if esperado == valor:
                    quitadas += 1
                else:
                    sobreviven.append(f"        {nombre}: {valor};")
                    conservadas += 1
            if not sobreviven:
                return ""
            cuerpo = "\n".join(sobreviven)
            return f"      :root {{\n{cuerpo}\n      }}\n\n"

        nuevo = RE_ROOT.sub(reemplazar, texto)
        if nuevo != texto:
            ruta.write_text(nuevo, encoding="utf-8")
            tocadas += 1

    lineas = [MARCA] + [
        f"  {nombre}: {valor};"
        for nombre, valor in sorted(consolidado.items())
    ]
    corte = css_global.index("}")
    css_global = (
        css_global[:corte].rstrip()
        + "\n\n"
        + "\n".join(lineas)
        + "\n"
        + css_global[corte:]
    )
    GLOBAL.write_text(css_global, encoding="utf-8")

    print(f"variables consolidadas en la hoja global: {len(consolidado)}")
    print(f"declaraciones borradas de las plantillas: {quitadas}")
    print(f"declaraciones conservadas por diferir: {conservadas}")
    print(f"plantillas tocadas: {tocadas}")


if __name__ == "__main__":
    main()
