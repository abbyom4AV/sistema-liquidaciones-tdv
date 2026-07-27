from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import xlwings as xw


class ErrorConservacionLibro(Exception):
    """Error durante la prueba de apertura y guardado con Excel."""


def guardar_copia_sin_modificaciones(
    ruta_origen: str | Path,
    ruta_salida: str | Path,
) -> Path:
    origen = Path(ruta_origen).resolve()
    salida = Path(ruta_salida).resolve()

    if not origen.is_file():
        raise FileNotFoundError(
            f"No existe el archivo de origen: {origen}"
        )

    if origen.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ErrorConservacionLibro(
            "El archivo debe ser .xlsx o .xlsm."
        )

    if origen == salida:
        raise ErrorConservacionLibro(
            "La salida no puede ser el mismo archivo de origen."
        )

    if salida.exists():
        raise FileExistsError(
            f"El archivo de salida ya existe: {salida}"
        )

    salida.parent.mkdir(parents=True, exist_ok=True)

    # Primero se crea una copia física para garantizar que
    # el archivo operativo original nunca sea sobrescrito.
    shutil.copy2(origen, salida)

    aplicacion: xw.App | None = None
    libro: xw.Book | None = None

    try:
        aplicacion = xw.App(
            visible=False,
            add_book=False,
        )

        aplicacion.display_alerts = False
        aplicacion.screen_updating = False

        libro = aplicacion.books.open(
            str(salida),
            update_links=False,
            read_only=False,
            add_to_mru=False,
        )

        # No se modifica ninguna celda.
        # Excel únicamente abre y vuelve a guardar la copia.
        libro.save()
        libro.close()
        libro = None

    except Exception as error:
        # Se elimina la copia incompleta para no confundirla
        # con un resultado válido.
        if salida.exists():
            try:
                salida.unlink()
            except OSError:
                pass

        raise ErrorConservacionLibro(
            "Excel no pudo abrir y guardar correctamente "
            f"la copia: {error}"
        ) from error

    finally:
        if libro is not None:
            try:
                libro.close()
            except Exception:
                pass

        if aplicacion is not None:
            try:
                aplicacion.quit()
            except Exception:
                pass

    return salida


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Abre y guarda una copia del archivo Di Manno "
            "sin modificar datos."
        )
    )

    parser.add_argument(
        "--origen",
        required=True,
        help="Archivo original que se desea probar.",
    )

    parser.add_argument(
        "--salida",
        required=True,
        help="Ruta de la copia generada.",
    )

    argumentos = parser.parse_args()

    salida = guardar_copia_sin_modificaciones(
        ruta_origen=argumentos.origen,
        ruta_salida=argumentos.salida,
    )

    print("Prueba completada correctamente.")
    print(f"Copia generada: {salida}")


if __name__ == "__main__":
    main()