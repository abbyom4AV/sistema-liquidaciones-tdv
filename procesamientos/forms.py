from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import BaseModelFormSet, modelformset_factory

from procesamientos.models import (
    GastoProcesamientoDimanno,
    GastoProcesamientoMaster,
    ProcesamientoDimanno,
)


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


PREFIJO_OPCION_DESPACHOS = "despachos|"


class FormularioResolucionDestinoDimanno(forms.Form):
    opcion_destino = forms.ChoiceField(
        label="Destino a aplicar",
        widget=forms.RadioSelect,
        error_messages={
            "required": "Seleccione una opción de destino.",
            "invalid_choice": (
                "La opción de destino no es válida."
            ),
        },
    )
    destino_manual = forms.CharField(
        label="Otro destino",
        required=False,
        max_length=150,
    )
    motivo = forms.CharField(
        label="Motivo de la elección o corrección",
        widget=forms.Textarea(attrs={"rows": 3}),
        error_messages={
            "required": (
                "Indique el motivo de la elección o corrección."
            ),
        },
    )

    def __init__(
        self,
        *args,
        procesamiento: ProcesamientoDimanno,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.procesamiento = procesamiento

        destino_liq = (
            procesamiento.destino_liquidacion or ""
        ).strip()
        destinos_desp = []
        vistos: set[str] = set()
        for destino in procesamiento.destinos_despachos or []:
            texto = str(destino).strip()
            if not texto or texto in vistos:
                continue
            vistos.add(texto)
            destinos_desp.append(texto)
        self.destinos_despachos_unicos = destinos_desp

        opciones: list[tuple[str, str]] = []
        if destino_liq:
            opciones.append(
                (
                    "liquidacion",
                    (
                        "Usar destino de la liquidación: "
                        f"{destino_liq}"
                    ),
                )
            )
        for destino in destinos_desp:
            opciones.append(
                (
                    f"{PREFIJO_OPCION_DESPACHOS}{destino}",
                    f"Usar destino de Despachos: {destino}",
                )
            )
        opciones.append(
            ("manual", "Ingresar otro destino")
        )
        self.fields["opcion_destino"].choices = opciones

    def clean_destino_manual(self):
        valor = self.cleaned_data.get("destino_manual") or ""
        normalizado = " ".join(valor.split()).upper()
        return normalizado

    def clean_motivo(self):
        valor = (self.cleaned_data.get("motivo") or "").strip()
        if not valor:
            raise forms.ValidationError(
                "Indique el motivo de la elección o corrección."
            )
        return valor

    def clean(self):
        cleaned = super().clean()
        opcion = cleaned.get("opcion_destino")
        if not opcion:
            return cleaned

        destino_liq = (
            self.procesamiento.destino_liquidacion or ""
        ).strip()
        destinos_permitidos = {
            str(d).strip()
            for d in (self.procesamiento.destinos_despachos or [])
            if str(d).strip()
        }

        if opcion == "liquidacion":
            if not destino_liq:
                self.add_error(
                    "opcion_destino",
                    "No hay destino de liquidación disponible.",
                )
                return cleaned
            cleaned["destino_nuevo"] = destino_liq
            cleaned["origen_seleccionado"] = "liquidacion"
            return cleaned

        if opcion.startswith(PREFIJO_OPCION_DESPACHOS):
            destino = opcion[len(PREFIJO_OPCION_DESPACHOS):]
            if destino not in destinos_permitidos:
                self.add_error(
                    "opcion_destino",
                    (
                        "El destino de Despachos seleccionado "
                        "no pertenece a la lista persistida."
                    ),
                )
                return cleaned
            cleaned["destino_nuevo"] = destino
            cleaned["origen_seleccionado"] = "despachos"
            return cleaned

        if opcion == "manual":
            destino_manual = cleaned.get("destino_manual") or ""
            if not destino_manual:
                self.add_error(
                    "destino_manual",
                    "Indique el destino manual.",
                )
                return cleaned
            cleaned["destino_nuevo"] = destino_manual
            cleaned["origen_seleccionado"] = "manual"
            return cleaned

        self.add_error(
            "opcion_destino",
            "La opción de destino no es válida.",
        )
        return cleaned


class FormularioCargaMaster(forms.Form):
    archivo_despachos = forms.FileField(
        label="Archivo de despachos",
        error_messages={
            "required": "Seleccione el archivo de despachos.",
            "invalid": "El archivo de despachos no es válido.",
        },
    )
    archivo_liquidacion = forms.FileField(
        label="Liquidación Master Fruits (PDF)",
        error_messages={
            "required": "Seleccione el PDF de liquidación.",
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

    def clean_archivo_despachos(self):
        archivo = self.cleaned_data.get("archivo_despachos")
        if archivo is None:
            return archivo
        nombre = getattr(archivo, "name", "") or ""
        if not nombre.lower().endswith(".xlsx"):
            raise forms.ValidationError(
                "El archivo de despachos debe ser .xlsx."
            )
        return archivo

    def clean_archivo_liquidacion(self):
        archivo = self.cleaned_data.get("archivo_liquidacion")
        if archivo is None:
            return archivo
        nombre = getattr(archivo, "name", "") or ""
        if not nombre.lower().endswith(".pdf"):
            raise forms.ValidationError(
                "La liquidación Master Fruits debe ser .pdf."
            )
        return archivo

    def clean_archivo_cliente(self):
        archivo = self.cleaned_data.get("archivo_cliente")
        if archivo is None:
            return archivo
        nombre = getattr(archivo, "name", "") or ""
        if not nombre.lower().endswith(".xlsx"):
            raise forms.ValidationError(
                "El acumulativo del cliente debe ser .xlsx."
            )
        return archivo


class FormularioValorGastoMaster(forms.ModelForm):
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
        model = GastoProcesamientoMaster
        fields = ("valor_aplicado",)

    def clean_valor_aplicado(self):
        valor = self.cleaned_data.get("valor_aplicado")
        if valor is None:
            raise forms.ValidationError(
                "Indique el valor aplicado."
            )
        try:
            return Decimal(valor)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise forms.ValidationError(
                "El valor aplicado no es numérico."
            ) from error


class BaseFormsetGastosMaster(BaseModelFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return


FormsetGastosMaster = modelformset_factory(
    GastoProcesamientoMaster,
    form=FormularioValorGastoMaster,
    formset=BaseFormsetGastosMaster,
    extra=0,
    can_delete=False,
)
