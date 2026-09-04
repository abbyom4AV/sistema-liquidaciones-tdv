from django.contrib import admin

from procesamientos.models import (
    ArchivoPdfKraaijeveld,
    CorreccionGastoDimanno,
    CorreccionGastoMaster,
    GastoProcesamientoDimanno,
    GastoProcesamientoMaster,
    GeneracionDimanno,
    GeneracionGlamour,
    GeneracionKraaijeveld,
    GeneracionMaster,
    GeneracionEurobanan,
    GeneracionNufri,
    GeneracionSifa,
    GeneracionTdvEuropa,
    MapeoGastoGlamour,
    MapeoGastoEurobanan,
    MapeoGastoNufri,
    ProcesamientoDimanno,
    ProcesamientoGlamour,
    ProcesamientoKraaijeveld,
    ProcesamientoMaster,
    ProcesamientoEurobanan,
    ProcesamientoNufri,
    ProcesamientoSifa,
    ProcesamientoTdvEuropa,
    ResolucionDestinoDimanno,
    ArchivoPdfFruver,
    GeneracionFruver,
    ProcesamientoFruver,
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


class ArchivoPdfKraaijeveldInline(admin.TabularInline):
    model = ArchivoPdfKraaijeveld
    extra = 0
    readonly_fields = (
        "archivo",
        "nombre_original",
        "orden",
        "creado_en",
    )
    can_delete = False


@admin.register(ProcesamientoKraaijeveld)
class ProcesamientoKraaijeveldAdmin(admin.ModelAdmin):
    list_display = (
        "destino_ui",
        "semana",
        "anio",
        "estado",
        "puede_escribir",
        "cantidad_contenedores",
        "creado_por_nombre",
        "creado_en",
    )
    list_filter = ("estado", "anio", "incluye_precio_fijo")
    search_fields = ("destino_ui", "factura_corta_fijo")
    readonly_fields = (
        "id",
        "creado_por",
        "creado_por_nombre",
        "creado_en",
        "actualizado_en",
    )
    inlines = (ArchivoPdfKraaijeveldInline,)


@admin.register(GeneracionKraaijeveld)
class GeneracionKraaijeveldAdmin(admin.ModelAdmin):
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


@admin.register(ProcesamientoSifa)
class ProcesamientoSifaAdmin(admin.ModelAdmin):
    list_display = (
        "destino_ui",
        "factura_corta",
        "semana",
        "anio",
        "estado",
        "puede_escribir",
        "cantidad_contenedores",
        "creado_por_nombre",
        "creado_en",
    )
    list_filter = ("estado", "anio")
    search_fields = ("destino_ui", "factura_corta")
    readonly_fields = (
        "id",
        "creado_por",
        "creado_por_nombre",
        "creado_en",
        "actualizado_en",
    )


@admin.register(GeneracionSifa)
class GeneracionSifaAdmin(admin.ModelAdmin):
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


@admin.register(ProcesamientoGlamour)
class ProcesamientoGlamourAdmin(admin.ModelAdmin):
    list_display = (
        "factura_corta",
        "semana",
        "anio",
        "destino_ui",
        "estado",
        "destino_final",
        "puede_escribir",
        "creado_por_nombre",
        "creado_en",
    )
    list_filter = ("estado", "anio")
    search_fields = ("factura_corta", "destino_ui", "destino_final")
    readonly_fields = (
        "id",
        "creado_por",
        "creado_por_nombre",
        "creado_en",
        "actualizado_en",
    )


@admin.register(MapeoGastoGlamour)
class MapeoGastoGlamourAdmin(admin.ModelAdmin):
    list_display = (
        "etiqueta_original",
        "etiqueta_normalizada",
        "columna_destino",
        "creado_por_nombre",
        "actualizado_en",
    )
    search_fields = (
        "etiqueta_original",
        "etiqueta_normalizada",
        "columna_destino",
    )


@admin.register(GeneracionGlamour)
class GeneracionGlamourAdmin(admin.ModelAdmin):
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


@admin.register(ProcesamientoTdvEuropa)
class ProcesamientoTdvEuropaAdmin(admin.ModelAdmin):
    list_display = (
        "factura_corta",
        "semana",
        "anio",
        "destino_ui",
        "estado",
        "destino_final",
        "puede_escribir",
        "creado_por_nombre",
        "creado_en",
    )
    list_filter = ("estado", "anio")
    search_fields = ("factura_corta", "destino_ui", "destino_final")
    readonly_fields = (
        "id",
        "creado_por",
        "creado_por_nombre",
        "creado_en",
        "actualizado_en",
    )


@admin.register(GeneracionTdvEuropa)
class GeneracionTdvEuropaAdmin(admin.ModelAdmin):
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

class ArchivoPdfFruverInline(admin.TabularInline):
    model = ArchivoPdfFruver
    extra = 0
    readonly_fields = (
        "archivo",
        "nombre_original",
        "orden",
        "creado_en",
    )
    can_delete = False


@admin.register(ProcesamientoFruver)
class ProcesamientoFruverAdmin(admin.ModelAdmin):
    list_display = (
        "destino_ui",
        "factura_corta",
        "semana",
        "anio",
        "estado",
        "puede_escribir",
        "cantidad_contenedores",
        "creado_por_nombre",
        "creado_en",
    )
    list_filter = ("estado", "anio")
    search_fields = ("destino_ui", "factura_corta")
    readonly_fields = (
        "id",
        "creado_por",
        "creado_por_nombre",
        "creado_en",
        "actualizado_en",
    )
    inlines = (ArchivoPdfFruverInline,)


@admin.register(GeneracionFruver)
class GeneracionFruverAdmin(admin.ModelAdmin):
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


@admin.register(ProcesamientoNufri)
class ProcesamientoNufriAdmin(admin.ModelAdmin):
    list_display = (
        "factura_corta",
        "semana",
        "anio",
        "pagina_pdf",
        "estado",
        "destino_final",
        "creado_en",
    )
    list_filter = ("estado", "anio")
    search_fields = ("factura_corta", "destino_ui")


@admin.register(MapeoGastoNufri)
class MapeoGastoNufriAdmin(admin.ModelAdmin):
    list_display = (
        "etiqueta_original",
        "etiqueta_normalizada",
        "columna_destino",
    )


@admin.register(GeneracionNufri)
class GeneracionNufriAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "procesamiento",
        "estado",
        "solicitado_por_nombre",
        "solicitado_en",
        "intentos",
    )
    list_filter = ("estado",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProcesamientoEurobanan)
class ProcesamientoEurobananAdmin(admin.ModelAdmin):
    list_display = (
        "factura_corta",
        "semana",
        "anio",
        "estado",
        "destino_final",
        "creado_en",
    )
    list_filter = ("estado", "anio")
    search_fields = ("factura_corta", "destino_ui")


@admin.register(MapeoGastoEurobanan)
class MapeoGastoEurobananAdmin(admin.ModelAdmin):
    list_display = (
        "etiqueta_original",
        "etiqueta_normalizada",
        "columna_destino",
    )


@admin.register(GeneracionEurobanan)
class GeneracionEurobananAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "procesamiento",
        "estado",
        "solicitado_por_nombre",
        "solicitado_en",
        "intentos",
    )
    list_filter = ("estado",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


