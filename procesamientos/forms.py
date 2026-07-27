from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import BaseModelFormSet, modelformset_factory

from procesamientos.models import GastoProcesamientoDimanno


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


class FormularioMotivoCorreccion(forms.Form):
    motivo = forms.CharField(
        label="Motivo de la corrección",
        widget=forms.Textarea(attrs={"rows": 3}),
        error_messages={
            "required": "Indique el motivo de la corrección.",
        },
    )
    responsable = forms.CharField(
        label="Responsable",
        required=False,
        max_length=150,
        help_text=(
            "Obligatorio si no ha iniciado sesión."
        ),
    )

    def __init__(self, *args, usuario_autenticado=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario_autenticado = usuario_autenticado
        if usuario_autenticado:
            self.fields["responsable"].widget = forms.HiddenInput()
            self.fields["responsable"].required = False
        else:
            self.fields["responsable"].required = True
            self.fields["responsable"].error_messages = {
                "required": (
                    "Indique el nombre del responsable."
                ),
            }

    def clean_responsable(self):
        valor = (self.cleaned_data.get("responsable") or "").strip()
        if not self.usuario_autenticado and not valor:
            raise forms.ValidationError(
                "Indique el nombre del responsable."
            )
        return valor

    def clean_motivo(self):
        valor = (self.cleaned_data.get("motivo") or "").strip()
        if not valor:
            raise forms.ValidationError(
                "Indique el motivo de la corrección."
            )
        return valor


class FormularioValorGasto(forms.ModelForm):
    valor_aplicado = forms.DecimalField(
        max_digits=18,
        decimal_places=6,
        localize=True,
        error_messages={
            "required": "Indique el valor aplicado.",
            "invalid": "El valor aplicado no es numérico.",
        },
    )

    class Meta:
        model = GastoProcesamientoDimanno
        fields = ("valor_aplicado",)

    def clean_valor_aplicado(self):
        valor = self.cleaned_data.get("valor_aplicado")
        if valor is None:
            raise forms.ValidationError(
                "Indique el valor aplicado."
            )
        try:
            decimal_valor = Decimal(valor)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise forms.ValidationError(
                "El valor aplicado no es numérico."
            ) from error
        return decimal_valor


class BaseFormsetGastosDimanno(BaseModelFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return


FormsetGastosDimanno = modelformset_factory(
    GastoProcesamientoDimanno,
    form=FormularioValorGasto,
    formset=BaseFormsetGastosDimanno,
    extra=0,
    can_delete=False,
)
