from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path(
        "",
        RedirectView.as_view(
            pattern_name="login",
            permanent=False,
        ),
    ),
    path("admin/", admin.site.urls),
    path(
        "cuentas/iniciar-sesion/",
        LoginView.as_view(
            template_name="registration/login.html",
        ),
        name="login",
    ),
    path(
        "cuentas/cerrar-sesion/",
        LogoutView.as_view(),
        name="logout",
    ),
    path(
        "procesamientos/",
        include("procesamientos.urls"),
    ),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
