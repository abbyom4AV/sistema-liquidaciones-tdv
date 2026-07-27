from __future__ import annotations

from django import forms


class FormularioCargaDimanno(forms.Form):
    anio = forms.IntegerField(
        label="Año",
        min_value=2024,
        max_value=2100,
        initial=2026,
        error_messages={
            "required": "Indique el año de la liquidación.",
            "invalid": "El año debe ser un número entero.",
            "min_value": "El año mínimo permitido es 2024.",
            "max_value": "El año máximo permitido es 2100.",
        },
    )

    nombre_hoja = forms.CharField(
        label="Nombre de la hoja",
        max_length=100,
        help_text="Ejemplo: FT 5292 W15",
        error_messages={
            "required": "Indique el nombre de la hoja de la liquidación.",
            "max_length": (
                "El nombre de la hoja no puede superar "
                "los 100 caracteres."
            ),
        },
    )

    archivo_despachos = forms.FileField(
        label="Archivo de despachos",
        error_messages={
            "required": "Seleccione el archivo de despachos.",
            "invalid": "El archivo de despachos no es válido.",
        },
    )

    archivo_liquidacion = forms.FileField(
        label="Archivo de liquidación",
        error_messages={
            "required": "Seleccione el archivo de liquidación.",
            "invalid": "El archivo de liquidación no es válido.",
        },
    )

    archivo_cliente = forms.FileField(
        label="Archivo acumulativo del cliente",
        error_messages={
            "required": (
                "Seleccione el archivo acumulativo del cliente."
            ),
            "invalid": (
                "El archivo del cliente no es válido."
            ),
        },
    )

    def _validar_extension_xlsx(
        self,
        archivo,
        nombre_campo: str,
    ):
        if archivo is None:
            return archivo

        nombre = getattr(archivo, "name", "") or ""
        if not nombre.lower().endswith(".xlsx"):
            raise forms.ValidationError(
                f"El archivo de {nombre_campo} debe tener "
                "extensión .xlsx."
            )

        return archivo

    def clean_archivo_despachos(self):
        return self._validar_extension_xlsx(
            self.cleaned_data.get("archivo_despachos"),
            "despachos",
        )

    def clean_archivo_liquidacion(self):
        return self._validar_extension_xlsx(
            self.cleaned_data.get("archivo_liquidacion"),
            "liquidación",
        )

    def clean_archivo_cliente(self):
        return self._validar_extension_xlsx(
            self.cleaned_data.get("archivo_cliente"),
            "cliente",
        )
