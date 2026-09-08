"""Quita de las plantillas las reglas CSS que no aportan nada.

Solo toca dos categorías del análisis:
  redundante  ya está igual en la hoja global; se borra del <style>
  subible     idéntica en todas las pantallas que la traen y nadie
              más usa esos selectores; se borra y se agrega al final
              de la hoja global, que es donde estaba en la cascada

Las conflictivas y las propias no se tocan.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RAIZ = BASE / "procesamientos" / "templates" / "procesamientos"
GLOBAL = (
    BASE / "procesamientos" / "static" / "procesamientos" / "tdv-app.css"
)
CLASIFICADO = BASE / "scripts" / "_css_clasificado.json"

MARCA = "/* ——— Reglas subidas desde los <style> de las pantallas ——— */"


def clave(selector: str, declaraciones: str) -> tuple[str, str]:
    return (
        re.sub(r"\s+", " ", selector).strip(),
        ";".join(
            re.sub(r"\s+", " ", d).strip()
            for d in declaraciones.split(";")
            if d.strip()
        ),
    )


def main() -> None:
    datos = json.loads(CLASIFICADO.read_text(encoding="utf-8"))
    a_borrar = {
        (i["sel"], i["decl"])
        for i in datos["redundante"] + datos["subible"]
    }
    a_subir = [(i["sel"], i["decl"]) for i in datos["subible"]]

    borradas = 0
    tocadas = 0
    for ruta in sorted(RAIZ.glob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        bloque = re.search(
            r"\{% block estilos %\}(.*?)\{% endblock %\}", texto, re.S
        )
        if bloque is None:
            continue

        css = bloque.group(1)
        contexto: list[str] = []
        salida: list[str] = []
        quitadas = 0
        posicion = 0
        cabeza = ""

        while posicion < len(css):
            caracter = css[posicion]
            if caracter == "{":
                titulo = re.sub(r"\s+", " ", cabeza).strip()
                if titulo.startswith("@"):
                    contexto.append(titulo)
                    salida.append(cabeza + "{")
                    cabeza = ""
                    posicion += 1
                    continue
                cierre = css.find("}", posicion)
                if cierre == -1:
                    break
                cuerpo = css[posicion + 1 : cierre]
                prefijo = " ".join(contexto)
                sel, decl = clave(titulo, cuerpo)
                completo = f"{prefijo} @@ {sel}" if prefijo else sel
                if (completo, decl) in a_borrar:
                    quitadas += 1
                    # Se descarta también el salto que la precedía.
                    while salida and salida[-1].strip() == "":
                        salida.pop()
                    cabeza = ""
                    posicion = cierre + 1
                    continue
                salida.append(cabeza + "{" + cuerpo + "}")
                cabeza = ""
                posicion = cierre + 1
                continue
            if caracter == "}":
                if contexto:
                    contexto.pop()
                salida.append(cabeza + "}")
                cabeza = ""
                posicion += 1
                continue
            cabeza += caracter
            posicion += 1

        if quitadas == 0:
            continue

        nuevo_css = "".join(salida) + cabeza
        nuevo_css = re.sub(r"\n{3,}", "\n\n", nuevo_css)
        texto = (
            texto[: bloque.start(1)] + nuevo_css + texto[bloque.end(1) :]
        )
        ruta.write_text(texto, encoding="utf-8")
        borradas += quitadas
        tocadas += 1

    # Las subidas van al final de la hoja global para conservar el
    # mismo orden de cascada que tenían en el <style>.
    por_media: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for sel, decl in a_subir:
        if " @@ " in sel:
            media, real = sel.split(" @@ ", 1)
            por_media[media].append((real, decl))
        else:
            por_media[""].append((sel, decl))

    lineas = ["", "", MARCA]
    for selector, declaraciones in sorted(por_media.pop("", [])):
        cuerpo = "; ".join(declaraciones.split(";"))
        lineas.append(f"{selector} {{ {cuerpo}; }}")
    for media, reglas in sorted(por_media.items()):
        lineas.append("")
        lineas.append(f"{media} {{")
        for selector, declaraciones in sorted(reglas):
            cuerpo = "; ".join(declaraciones.split(";"))
            lineas.append(f"  {selector} {{ {cuerpo}; }}")
        lineas.append("}")

    GLOBAL.write_text(
        GLOBAL.read_text(encoding="utf-8").rstrip()
        + "\n".join(lineas)
        + "\n",
        encoding="utf-8",
    )

    print(f"reglas quitadas de las plantillas: {borradas}")
    print(f"plantillas tocadas: {tocadas}")
    print(f"reglas agregadas a la hoja global: {len(a_subir)}")


if __name__ == "__main__":
    main()
