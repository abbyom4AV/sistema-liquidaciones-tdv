from __future__ import annotations

from pathlib import Path

from django.http import FileResponse, Http404


CONTENT_TYPE_XLSX = (
    "application/vnd.openxmlformats-officedocument"
    ".spreadsheetml.sheet"
)


def respuesta_descarga_xlsx(
    ruta: Path,
    nombre: str,
) -> FileResponse:
    """
    Descarga Excel forzada (attachment) con tipo MIME correcto.

    Evita que el navegador intente renderizar el .xlsx en la
    pestaña (pagina en blanco) y reduce cortes en redes locales.
    """
    if not ruta.is_file():
        raise Http404("El archivo ya no existe.")

    try:
        handle = ruta.open("rb")
    except OSError as error:
        raise Http404("No se pudo abrir el archivo.") from error

    respuesta = FileResponse(
        handle,
        as_attachment=True,
        filename=nombre,
        content_type=CONTENT_TYPE_XLSX,
    )
    respuesta["Content-Length"] = str(ruta.stat().st_size)
    respuesta["Cache-Control"] = "no-store"
    return respuesta
