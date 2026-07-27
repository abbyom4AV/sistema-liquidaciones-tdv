from django.apps import AppConfig


class ProcesamientosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "procesamientos"

    def ready(self) -> None:
        from procesamientos import signals  # noqa: F401
