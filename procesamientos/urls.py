from django.urls import path

from procesamientos import views

app_name = "procesamientos"

urlpatterns = [
    path(
        "",
        views.panel_control,
        name="panel",
    ),
    path(
        "dimanno/",
        views.cargar_dimanno,
        name="dimanno_cargar",
    ),
    path(
        "dimanno/<uuid:procesamiento_id>/",
        views.detalle_dimanno,
        name="dimanno_detalle",
    ),
    path(
        "dimanno/<uuid:procesamiento_id>/gastos/editar/",
        views.editar_gastos_dimanno,
        name="dimanno_gastos_editar",
    ),
    path(
        "dimanno/<uuid:procesamiento_id>/destino/resolver/",
        views.resolver_destino_dimanno,
        name="dimanno_destino_resolver",
    ),
    path(
        "dimanno/<uuid:procesamiento_id>/generar/",
        views.solicitar_generacion_dimanno,
        name="dimanno_generar",
    ),
    path(
        "dimanno/generaciones/<uuid:generacion_id>/",
        views.detalle_generacion_dimanno,
        name="dimanno_generacion_detalle",
    ),
    path(
        (
            "dimanno/generaciones/<uuid:generacion_id>/"
            "descargar/"
        ),
        views.descargar_generacion_dimanno,
        name="dimanno_generacion_descargar",
    ),
]
