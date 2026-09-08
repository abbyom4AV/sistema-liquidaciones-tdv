"""Comprueba que mover CSS no cambiÃ³ lo que ve cada pantalla.

Para cada plantilla arma la cascada completa â€”primero la hoja global,
despuÃ©s su <style>â€” y se queda con el valor final de cada selector.
Compara ese resultado contra el de un commit anterior. Si coincide en
las 45 pantallas, ninguna cambiÃ³ de aspecto.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "scripts"))

from _analizar_css import clases_del_selector, reglas_de  # noqa: E402

RUTA_GLOBAL = "procesamientos/static/procesamientos/tdv-app.css"
RUTA_PLANTILLAS = "procesamientos/templates/procesamientos"
RE_ESTILOS = "{% block estilos %}"


def desde_git(referencia: str, ruta: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "show", f"{referencia}:{ruta}"],
            cwd=BASE,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
    except subprocess.CalledProcessError:
        return None


def estilos_locales(texto: str) -> str:
    inicio = texto.find(RE_ESTILOS)
    if inicio == -1:
        return ""
    inicio += len(RE_ESTILOS)
    fin = texto.find("{% endblock %}", inicio)
    return texto[inicio:fin] if fin != -1 else ""


def cascada(css_global: str, css_local: str) -> dict[str, str]:
    """Valor final de cada selector, en orden de documento.

    El :root se desglosa variable por variable: lo que importa no es
    dónde se declara la paleta sino con qué valor termina cada token.
    """
    final: dict[str, str] = {}
    for css in (css_global, css_local):
        for selector, declaraciones in reglas_de(css):
            if selector.endswith(":root"):
                for nombre, valor in re.findall(
                    r"(--[\w-]+)\s*:\s*([^;]+)", declaraciones
                ):
                    final[f":root {nombre}"] = valor.strip()
                continue
            final[selector] = declaraciones
    return final


def clases_de_la_pantalla(snapshots: Path, nombre: str) -> set[str]:
    archivo = snapshots / f"procesamientos__{nombre}"
    if not archivo.is_file():
        return set()
    html = archivo.read_text(encoding="utf-8")
    clases: set[str] = set()
    for valor in re.findall(r'class="([^"]*)"', html):
        clases.update(valor.split())
    return clases


def afecta(selector: str, clases: set[str]) -> bool:
    """Si el selector no menciona ninguna clase presente, la regla es
    inerte en esa pantalla y da igual dÃ³nde estÃ© declarada."""
    del_selector = clases_del_selector(selector)
    if not del_selector:
        return True  # selector de elemento: siempre puede aplicar
    return bool(del_selector & clases)


def main() -> None:
    referencia = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    snapshots = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    global_antes = desde_git(referencia, RUTA_GLOBAL)
    if global_antes is None:
        raise SystemExit(f"No se pudo leer la hoja global en {referencia}")
    global_ahora = (BASE / RUTA_GLOBAL).read_text(encoding="utf-8")

    iguales = 0
    problemas: list[str] = []

    for ruta in sorted((BASE / RUTA_PLANTILLAS).glob("*.html")):
        relativa = f"{RUTA_PLANTILLAS}/{ruta.name}"
        antes = desde_git(referencia, relativa)
        if antes is None:
            continue
        ahora = ruta.read_text(encoding="utf-8")

        a = cascada(global_antes, estilos_locales(antes))
        b = cascada(global_ahora, estilos_locales(ahora))

        if snapshots is not None:
            clases = clases_de_la_pantalla(snapshots, ruta.name)
            a = {s: d for s, d in a.items() if afecta(s, clases)}
            b = {s: d for s, d in b.items() if afecta(s, clases)}

        if a == b:
            iguales += 1
            continue

        detalles = []
        for selector in sorted(set(a) | set(b)):
            if a.get(selector) != b.get(selector):
                detalles.append(
                    f"    {selector}\n"
                    f"      antes:  {a.get(selector)}\n"
                    f"      ahora:  {b.get(selector)}"
                )
        problemas.append(
            f"  {ruta.name}\n" + "\n".join(detalles[:5])
        )

    print(f"pantallas con el mismo CSS efectivo: {iguales}")
    if problemas:
        print(f"pantallas que cambiaron: {len(problemas)}")
        for p in problemas[:10]:
            print(p)
    else:
        print("ninguna pantalla cambiÃ³ de estilos")


if __name__ == "__main__":
    main()

