"""Reescribe las plantillas para que extiendan procesamientos/base.html.

Cada plantilla pierde su <head>, su <body> y el <div class="contenido">
y conserva todo lo demás dentro de los bloques de la base. El <style>
local no se toca en este paso: eso se limpia después.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RAIZ = BASE / "procesamientos" / "templates" / "procesamientos"

# El login no lleva sidebar ni el layout de la app.
EXCLUIDAS = {"base.html"}

RE_TITLE = re.compile(r"[ \t]*<title>(.*?)</title>\s*", re.S)
RE_STYLE = re.compile(r"[ \t]*<style>(.*?)</style>\s*", re.S)
RE_REFRESH = re.compile(
    r"[ \t]*\{%\s*if recargar_automaticamente\s*%\}.*?"
    r"\{%\s*endif\s*%\}\s*",
    re.S,
)
RE_LOAD = re.compile(r"\{%\s*load [^%]*%\}")
RE_SCRIPT_EXTERNO = re.compile(
    r"[ \t]*<script src=\"https?://[^\"]+\"></script>\s*"
)
RE_ABRE_CONTENIDO = re.compile(
    r"<div class=\"contenido([^\"]*)\">\s*"
)


def sangrar(texto: str, espacios: int = 2) -> str:
    relleno = " " * espacios
    return "\n".join(
        (relleno + linea) if linea.strip() else linea
        for linea in texto.splitlines()
    )


def migrar(ruta: Path) -> str | None:
    original = ruta.read_text(encoding="utf-8")
    if "<html" not in original or "{% extends" in original:
        return None
    if 'class="contenido' not in original:
        return f"{ruta.name}: no tiene <div class=\"contenido\">"

    cabeza = original[: original.index("<body")]
    cuerpo = original[original.index("<body") :]
    cuerpo = cuerpo[cuerpo.index(">") + 1 :]
    cuerpo = cuerpo[: cuerpo.rindex("</body>")]

    titulo = ""
    if (m := RE_TITLE.search(cabeza)) is not None:
        titulo = m.group(1).strip()

    meta = ""
    if (m := RE_REFRESH.search(cabeza)) is not None:
        meta = re.sub(r"\s+", " ", m.group(0)).strip()

    head_extra = "\n".join(
        s.strip() for s in RE_SCRIPT_EXTERNO.findall(cabeza)
    )

    estilos = ""
    if (m := RE_STYLE.search(cabeza)) is not None:
        estilos = m.group(0).strip("\n")

    # Los {% load %} viven en la plantilla hija, no se heredan.
    cargas = sorted(
        {c.strip() for c in RE_LOAD.findall(original)}
        - {"{% load static %}"}
    )

    # Fuera la sidebar: la pone la base.
    cuerpo = re.sub(
        r"[ \t]*\{%\s*include \"procesamientos/_sidebar\.html\" %\}\s*",
        "",
        cuerpo,
        count=1,
    )

    m = RE_ABRE_CONTENIDO.search(cuerpo)
    if m is None:
        return f"{ruta.name}: no se ubicó la apertura del contenido"
    clases = m.group(1)
    cuerpo = cuerpo[m.end() :]

    # Los {% load %} ya quedaron arriba, junto al {% extends %}.
    cuerpo = re.sub(r"[ \t]*\{%\s*load [^%]*%\}\n?", "", cuerpo)

    cierre = cuerpo.rindex("</div>")
    cola = cuerpo[cierre + len("</div>") :].strip()
    contenido = cuerpo[:cierre].rstrip()

    partes = ["{% extends \"procesamientos/base.html\" %}"]
    for carga in cargas:
        partes.append(carga)
    partes.append("")
    if titulo:
        partes.append(f"{{% block titulo %}}{titulo}{{% endblock %}}")
    if clases:
        partes.append(
            f"{{% block clases_contenido %}}{clases}{{% endblock %}}"
        )
    if meta:
        partes.append(f"{{% block meta %}}{meta}{{% endblock %}}")
    if head_extra:
        partes.append("{% block head %}")
        partes.append(sangrar(head_extra))
        partes.append("{% endblock %}")
    if estilos:
        partes.append("{% block estilos %}")
        partes.append(sangrar(estilos))
        partes.append("{% endblock %}")
    partes.append("")
    partes.append("{% block contenido %}")
    partes.append(contenido)
    partes.append("{% endblock %}")
    if cola:
        partes.append("")
        partes.append("{% block scripts %}")
        partes.append(sangrar(cola))
        partes.append("{% endblock %}")

    nuevo = "\n".join(partes).rstrip() + "\n"
    ruta.write_text(nuevo, encoding="utf-8")
    return None


def main() -> None:
    solo = set(sys.argv[1:])
    hechas = 0
    problemas: list[str] = []
    for ruta in sorted(RAIZ.glob("*.html")):
        if ruta.name in EXCLUIDAS:
            continue
        if solo and ruta.name not in solo:
            continue
        aviso = migrar(ruta)
        if aviso:
            problemas.append(aviso)
        else:
            hechas += 1
    print(f"plantillas migradas: {hechas}")
    for p in problemas:
        print(f"  omitida → {p}")


if __name__ == "__main__":
    main()
