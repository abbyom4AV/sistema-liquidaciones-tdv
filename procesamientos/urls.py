from django.urls import path

from procesamientos import views

app_name = "procesamientos"

urlpatterns = [
    path(
        "dimanno/",
        views.cargar_dimanno,
        name="dimanno_cargar",
    ),
]
