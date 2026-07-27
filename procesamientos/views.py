from __future__ import annotations

import logging
import tempfile
from decimal import Decimal
from pathlib import Path

from django.shortcuts import render

from procesamientos.forms import FormularioCargaDimanno
from services.dimanno.extractor import ErrorExtraccionDimanno
from services.dimanno.matcher import ErrorMatcherDimanno
from services.dimanno.processor import (
    ErrorProcesamientoDimanno,
    ResultadoPreparacionDimanno,
    preparar_procesamiento_dimanno,
)

logger = logging.getLogger(__name__)

RUBROS_GASTOS_ORDEN = (
    "Comisión",
    "Flete Eu",
    "Control calidad Eu",
    "THC",
    "Transporte",
    "Aduanas",
)


def _guardar_archivo_subido(
    archivo,
    carpeta: Path,
    nombre_interno: str,
) -> Path:
    destino = carpeta / nombre_interno
    with destino.open("wb") as destino_archivo:
        for fragmento in archivo.chunks():
            destino_archivo.write(fragmento)
    return destino


def _preparar_gastos_extraidos(
    procesamiento: ResultadoPreparacionDimanno,
) -> list[dict[str, str | Decimal]]:
    """
    Lista de presentación ordenada a partir de
    liquidacion.gastos (dict[str, Decimal]).
    """
    gastos = procesamiento.liquidacion.gastos
    if not gastos:
        return []

    return [
        {
            "rubro": rubro,
            "valor": gastos[rubro],
        }
        for rubro in RUBROS_GASTOS_ORDEN
        if rubro in gastos
    ]


def cargar_dimanno(request):
    if request.method != "POST":
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": FormularioCargaDimanno(),
            },
        )

    formulario = FormularioCargaDimanno(
        request.POST,
        request.FILES,
    )

    if not formulario.is_valid():
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": formulario,
            },
            status=400,
        )

    datos = formulario.cleaned_data

    try:
        with tempfile.TemporaryDirectory(
            prefix="dimanno_carga_",
        ) as carpeta_temporal:
            carpeta = Path(carpeta_temporal)

            ruta_despachos = _guardar_archivo_subido(
                datos["archivo_despachos"],
                carpeta,
                "despachos.xlsx",
            )
            ruta_liquidacion = _guardar_archivo_subido(
                datos["archivo_liquidacion"],
                carpeta,
                "liquidacion.xlsx",
            )
            # Se guarda para la etapa de escritura;
            # en esta fase no se usa.
            _guardar_archivo_subido(
                datos["archivo_cliente"],
                carpeta,
                "cliente.xlsx",
            )

            procesamiento = preparar_procesamiento_dimanno(
                ruta_liquidacion=ruta_liquidacion,
                nombre_hoja=datos["nombre_hoja"],
                ruta_despachos=ruta_despachos,
                anio=datos["anio"],
            )

            return render(
                request,
                "procesamientos/dimanno_validacion.html",
                {
                    "procesamiento": procesamiento,
                    "gastos_extraidos": (
                        _preparar_gastos_extraidos(
                            procesamiento
                        )
                    ),
                },
            )

    except (
        ErrorExtraccionDimanno,
        ErrorMatcherDimanno,
        ErrorProcesamientoDimanno,
    ) as error:
        logger.exception(
            "Error conocido al procesar Di Manno."
        )
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": formulario,
                "error_proceso": str(error),
            },
            status=400,
        )

    except Exception:
        logger.exception(
            "Error inesperado al procesar Di Manno."
        )
        return render(
            request,
            "procesamientos/dimanno_cargar.html",
            {
                "formulario": formulario,
                "error_proceso": (
                    "Ocurrió un error inesperado al validar "
                    "los archivos. Revise el registro del "
                    "servidor para más detalle."
                ),
            },
            status=500,
        )
