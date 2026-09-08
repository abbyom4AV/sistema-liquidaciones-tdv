"""Clasifica las reglas CSS embebidas en las plantillas.

Mover una regla de un <style> local a la hoja global cambia el orden
de la cascada, así que antes de tocar nada hay que separar las reglas
en las que se pueden mover sin riesgo y las que no.

Categorías:
  redundante  ya existe igual en la hoja global; se puede borrar
  subible     mismo valor en todas las plantillas que la traen, y
              ningún otro sitio usa el selector; se puede globalizar
  conflictiva mismo selector con valores distintos según la pantalla
  propia      solo la usa una pantalla; se queda donde está
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RAIZ = BASE / "procesamientos" / "templates" / "procesamientos"
GLOBAL = BASE / "procesamientos" / "static" / "procesamientos" / "tdv-app.css"

RE_STYLE = re.compile(r"\{% block estilos %\}(.*?)\{% endblock %\}", re.S)


def limpiar(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # Las etiquetas <style> no son CSS; si se dejan, se pegan al
    # primer selector del bloque.
    return re.sub(r"</?style[^>]*>", "", css)


def reglas_de(css: str) -> list[tuple[str, str]]:
    """(selector normalizado, declaraciones normalizadas).

    El selector lleva por delante la media query que lo envuelve, si
    la hay: una regla dentro de `@media (max-width: 900px)` no es la
    misma que la de fuera aunque comparta selector.
    """
    fuera: list[tuple[str, str]] = []
    texto = limpiar(css)
    pila: list[str] = []
    posicion = 0
    buffer = ""

    while posicion < len(texto):
        caracter = texto[posicion]
        if caracter == "{":
            cabeza = re.sub(r"\s+", " ", buffer).strip()
            if cabeza.startswith("@"):
                pila.append(cabeza)
                buffer = ""
                posicion += 1
                continue
            cierre = texto.find("}", posicion)
            if cierre == -1:
                break
            cuerpo = texto[posicion + 1 : cierre]
            decl = ";".join(
                re.sub(r"\s+", " ", d).strip()
                for d in cuerpo.split(";")
                if d.strip()
            )
            if cabeza:
                prefijo = " ".join(pila)
                sel = f"{prefijo} @@ {cabeza}" if prefijo else cabeza
                fuera.append((sel, decl))
            buffer = ""
            posicion = cierre + 1
            continue
        if caracter == "}":
            if pila:
                pila.pop()
            buffer = ""
            posicion += 1
            continue
        buffer += caracter
        posicion += 1

    return fuera


def clases_del_selector(selector: str) -> set[str]:
    return set(re.findall(r"\.([A-Za-z0-9_-]+)", selector))


def main() -> None:
    snapshots = Path(sys.argv[1]) if len(sys.argv) > 1 else None

    globales: dict[str, str] = {}
    for sel, decl in reglas_de(GLOBAL.read_text(encoding="utf-8")):
        globales[sel] = decl  # la última definición es la que manda

    # regla -> plantillas que la traen
    por_regla: dict[tuple[str, str], set[str]] = defaultdict(set)
    # selector -> plantillas que lo definen
    por_selector: dict[str, set[str]] = defaultdict(set)

    for ruta in sorted(RAIZ.glob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        m = RE_STYLE.search(texto)
        if m is None:
            continue
        for sel, decl in reglas_de(m.group(1)):
            por_regla[(sel, decl)].add(ruta.name)
            por_selector[sel].add(ruta.name)

    # Qué pantallas usan realmente cada clase, según el HTML rendido.
    usos: dict[str, set[str]] = defaultdict(set)
    if snapshots is not None:
        for archivo in snapshots.glob("procesamientos__*"):
            nombre = archivo.name.replace("procesamientos__", "")
            html = archivo.read_text(encoding="utf-8")
            for valor in re.findall(r'class="([^"]*)"', html):
                for clase in valor.split():
                    usos[clase].add(nombre)

    categorias: dict[str, list] = {
        "redundante": [],
        "subible": [],
        "conflictiva": [],
        "propia": [],
    }

    for (sel, decl), plantillas in sorted(por_regla.items()):
        if globales.get(sel) == decl:
            categorias["redundante"].append(
                {"sel": sel, "decl": decl, "en": sorted(plantillas)}
            )
            continue

        valores = {
            d for (s, d) in por_regla if s == sel
        }
        if len(valores) > 1:
            categorias["conflictiva"].append(
                {"sel": sel, "decl": decl, "en": sorted(plantillas)}
            )
            continue

        if len(plantillas) < 2:
            categorias["propia"].append(
                {"sel": sel, "decl": decl, "en": sorted(plantillas)}
            )
            continue

        # Solo se puede globalizar lo que se sabe a quién afecta. Un
        # selector sin clases (h1, button, input[type=file]) alcanza a
        # cualquier pantalla, así que se descarta.
        clases = clases_del_selector(sel)
        if not clases or snapshots is None:
            categorias["conflictiva"].append(
                {
                    "sel": sel,
                    "decl": decl,
                    "en": sorted(plantillas),
                    "motivo": "selector sin clases: alcanzaría a "
                    "pantallas que hoy no lo tienen",
                }
            )
            continue

        usuarias: set[str] = set()
        for clase in clases:
            usuarias |= usos.get(clase, set())
        if not usuarias <= plantillas:
            categorias["conflictiva"].append(
                {
                    "sel": sel,
                    "decl": decl,
                    "en": sorted(plantillas),
                    "motivo": "la clase se usa en pantallas que "
                    "no traen la regla",
                    "extra": sorted(usuarias - plantillas)[:6],
                }
            )
            continue

        categorias["subible"].append(
            {"sel": sel, "decl": decl, "en": sorted(plantillas)}
        )

    for nombre, items in categorias.items():
        instancias = sum(len(i["en"]) for i in items)
        print(f"{nombre:>12}: {len(items):>4} reglas, "
              f"{instancias:>5} instancias en plantillas")

    salida = BASE / "scripts" / "_css_clasificado.json"
    salida.write_text(
        json.dumps(categorias, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\ndetalle en {salida.name}")

    print("\n=== las 10 subibles más repetidas ===")
    for item in sorted(
        categorias["subible"], key=lambda i: -len(i["en"])
    )[:10]:
        print(f"  x{len(item['en']):>2}  {item['sel'][:70]}")

    print("\n=== las 10 conflictivas más repetidas ===")
    for item in sorted(
        categorias["conflictiva"], key=lambda i: -len(i["en"])
    )[:10]:
        motivo = item.get("motivo", "valores distintos por pantalla")
        print(f"  x{len(item['en']):>2}  {item['sel'][:50]}  ({motivo})")


if __name__ == "__main__":
    main()
