from django.urls import path

from procesamientos import views

app_name = "procesamientos"

urlpatterns = [
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
]
