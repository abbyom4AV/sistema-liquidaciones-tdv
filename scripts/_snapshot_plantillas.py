"""Renderiza todas las plantillas con contexto vacío y guarda el HTML.

Sirve de red de seguridad al refactorizar: se corre antes y después
del cambio y se comparan los dos snapshots. Si el HTML no cambia, el
refactor no alteró ninguna pantalla.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.template.loader import render_to_string  # noqa: E402


RAICES = (
    BASE / "procesamientos" / "templates",
    BASE / "templates",
)

UUID_FALSO = "00000000-0000-4000-8000-000000000000"


class Cualquiera:
    """Objeto comodín para renderizar plantillas sin datos reales.

    Responde a cualquier atributo, se puede iterar y sirve como
    argumento de `{% url %}`. Así se ejercitan las ramas de las
    plantillas sin tener que armar objetos de verdad.
    """

    def __init__(self, profundidad: int = 0) -> None:
        self._profundidad = profundidad

    def __getattr__(self, nombre: str):
        if nombre.startswith("_"):
            raise AttributeError(nombre)
        if nombre in {"id", "pk", "procesamiento_id", "generacion_id"}:
            return UUID_FALSO
        if self._profundidad >= 4:
            return ""
        return Cualquiera(self._profundidad + 1)

    def __getitem__(self, clave):
        return self.__getattr__(str(clave))

    def __iter__(self):
        if self._profundidad >= 2:
            return iter(())
        return iter((Cualquiera(self._profundidad + 1),))

    def __len__(self) -> int:
        return 1

    def __str__(self) -> str:
        return "—"

    def __bool__(self) -> bool:
        return True


class ContextoComodin(dict):
    """Cualquier variable que pida la plantilla existe.

    Django pregunta primero con `in`, así que hay que responder que
    sí a todo para que después llegue a `__missing__`.
    """

    def __contains__(self, clave: object) -> bool:
        return True

    def __missing__(self, clave: str):
        return Cualquiera()


# Estas plantillas arman URLs con nombres de vista que vienen en los
# datos, o serializan el contexto a JSON. El comodín no sirve ahí, así
# que se les pasan colecciones vacías.
CONTEXTOS_A_MEDIDA: dict[str, dict[str, object]] = {
    "procesamientos/panel.html": {
        "clientes_panel": [],
        "procesamientos_recientes": [],
    },
    "procesamientos/bitacoras.html": {
        "eventos": [],
        "eventos_pagina": [],
    },
    "procesamientos/ingresos.html": {
        "chart_payload": {},
    },
}


def nombres_de_plantillas() -> list[str]:
    nombres: list[str] = []
    for raiz in RAICES:
        for ruta in sorted(raiz.rglob("*.html")):
            texto = ruta.read_text(encoding="utf-8")
            es_pagina = "<html" in texto or "{% extends" in texto
            if not es_pagina or ruta.name == "base.html":
                continue  # parciales y la propia base
            nombres.append(ruta.relative_to(raiz).as_posix())
    return nombres


def normalizar(html: str) -> str:
    """Ignora diferencias de espacios en blanco entre etiquetas."""
    html = re.sub(r">\s+<", ">\n<", html)
    html = re.sub(r"[ \t]+", " ", html)
    return "\n".join(
        linea.strip() for linea in html.splitlines() if linea.strip()
    )


def main() -> None:
    destino = Path(sys.argv[1])
    destino.mkdir(parents=True, exist_ok=True)

    fallos: list[tuple[str, str]] = []
    escritas = 0
    for nombre in nombres_de_plantillas():
        contexto = ContextoComodin()
        contexto.update(CONTEXTOS_A_MEDIDA.get(nombre, {}))
        try:
            html = render_to_string(nombre, contexto)
        except Exception as error:  # noqa: BLE001
            fallos.append((nombre, f"{type(error).__name__}: {error}"))
            continue
        archivo = destino / nombre.replace("/", "__")
        archivo.write_text(normalizar(html), encoding="utf-8")
        escritas += 1

    print(f"plantillas renderizadas: {escritas}")
    if fallos:
        print(f"no se pudieron renderizar: {len(fallos)}")
        for nombre, error in fallos:
            print(f"  {nombre}: {error}")


if __name__ == "__main__":
    main()
