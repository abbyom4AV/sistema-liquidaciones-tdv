from django.contrib import admin

from procesamientos.models import (
    CorreccionGastoDimanno,
    CorreccionGastoMaster,
    GastoProcesamientoDimanno,
    GastoProcesamientoMaster,
    GeneracionDimanno,
    GeneracionMaster,
    ProcesamientoDimanno,
    ProcesamientoMaster,
    ResolucionDestinoDimanno,
)


@admin.register(ProcesamientoDimanno)
class ProcesamientoDimannoAdmin(admin.ModelAdmin):
    list_display = (
        "factura_corta",
        "semana",
        "anio",
        "estado",
        "destino_final",
        "puede_escribir",
        "creado_por_nombre",
        "creado_en",
    )
    list_filter = ("estado", "anio", "requiere_resolver_destino")
    search_fields = ("factura_corta", "nombre_hoja", "destino_final")
    readonly_fields = (
        "id",
        "creado_por",
        "creado_por_nombre",
        "creado_en",
        "actualizado_en",
    )


@admin.register(GastoProcesamientoDimanno)
class GastoProcesamientoDimannoAdmin(admin.ModelAdmin):
    list_display = (
        "procesamiento",
        "nombre",
        "codigo",
        "valor_original",
        "valor_aplicado",
        "orden",
    )
    list_filter = ("codigo",)
    search_fields = (
        "nombre",
        "procesamiento__factura_corta",
    )


@admin.register(CorreccionGastoDimanno)
class CorreccionGastoDimannoAdmin(admin.ModelAdmin):
    list_display = (
        "gasto",
        "valor_anterior",
        "valor_nuevo",
        "usuario_nombre",
        "creado_en",
    )
    search_fields = (
        "usuario_nombre",
        "gasto__nombre",
        "motivo",
    )
    readonly_fields = (
        "gasto",
        "valor_anterior",
        "valor_nuevo",
        "motivo",
        "usuario",
        "usuario_nombre",
        "creado_en",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ResolucionDestinoDimanno)
class ResolucionDestinoDimannoAdmin(admin.ModelAdmin):
    list_display = (
        "procesamiento",
        "destino_anterior",
        "destino_nuevo",
        "origen_seleccionado",
        "usuario_nombre",
        "creado_en",
    )
    list_filter = ("origen_seleccionado",)
    search_fields = (
        "usuario_nombre",
        "destino_nuevo",
        "procesamiento__factura_corta",
    )
    readonly_fields = (
        "procesamiento",
        "destino_anterior",
        "destino_nuevo",
        "origen_seleccionado",
        "destino_liquidacion",
        "destinos_despachos",
        "motivo",
        "usuario",
        "usuario_nombre",
        "creado_en",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(GeneracionDimanno)
class GeneracionDimannoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "procesamiento",
        "estado",
        "solicitado_por_nombre",
        "solicitado_en",
        "iniciado_en",
        "finalizado_en",
        "destino_aplicado",
        "intentos",
    )
    list_filter = ("estado",)
    search_fields = (
        "solicitado_por_nombre",
        "procesamiento__factura_corta",
        "destino_aplicado",
    )
    readonly_fields = (
        "id",
        "procesamiento",
        "estado",
        "solicitado_por",
        "solicitado_por_nombre",
        "solicitado_en",
        "iniciado_en",
        "finalizado_en",
        "archivo_resultado",
        "nombre_descarga",
        "mensaje_error",
        "destino_aplicado",
        "origen_destino_aplicado",
        "gastos_aplicados",
        "filas_agregadas",
        "fila_inicial",
        "fila_final",
        "rango_tabla",
        "intentos",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProcesamientoMaster)
class ProcesamientoMasterAdmin(admin.ModelAdmin):
    list_display = (
        "factura_corta",
        "semana",
        "anio",
        "estado",
        "destino_final",
        "puede_escribir",
        "creado_por_nombre",
        "creado_en",
    )
    list_filter = ("estado", "anio")
    search_fields = ("factura_corta", "destino_final")
    readonly_fields = (
        "id",
        "creado_por",
        "creado_por_nombre",
        "creado_en",
        "actualizado_en",
    )


@admin.register(GastoProcesamientoMaster)
class GastoProcesamientoMasterAdmin(admin.ModelAdmin):
    list_display = (
        "procesamiento",
        "nombre",
        "codigo",
        "valor_original",
        "valor_aplicado",
        "orden",
    )
    list_filter = ("codigo",)


@admin.register(CorreccionGastoMaster)
class CorreccionGastoMasterAdmin(admin.ModelAdmin):
    list_display = (
        "gasto",
        "valor_anterior",
        "valor_nuevo",
        "usuario_nombre",
        "creado_en",
    )
    readonly_fields = (
        "gasto",
        "valor_anterior",
        "valor_nuevo",
        "motivo",
        "usuario",
        "usuario_nombre",
        "creado_en",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(GeneracionMaster)
class GeneracionMasterAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "procesamiento",
        "estado",
        "solicitado_por_nombre",
        "solicitado_en",
        "intentos",
    )
    list_filter = ("estado",)
    readonly_fields = (
        "id",
        "procesamiento",
        "estado",
        "solicitado_por",
        "solicitado_por_nombre",
        "solicitado_en",
        "iniciado_en",
        "finalizado_en",
        "archivo_resultado",
        "nombre_descarga",
        "mensaje_error",
        "destino_aplicado",
        "origen_destino_aplicado",
        "gastos_aplicados",
        "filas_agregadas",
        "fila_inicial",
        "fila_final",
        "rango_tabla",
        "intentos",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
