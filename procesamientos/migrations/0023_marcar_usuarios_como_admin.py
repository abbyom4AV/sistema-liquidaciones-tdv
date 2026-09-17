from django.conf import settings
from django.db import migrations


def marcar_usuarios_existentes_como_admin(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    PerfilUsuario = apps.get_model("procesamientos", "PerfilUsuario")
    for usuario in User.objects.all():
        PerfilUsuario.objects.update_or_create(
            usuario=usuario,
            defaults={"rol": "admin"},
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("procesamientos", "0022_perfil_usuario_roles"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            marcar_usuarios_existentes_como_admin,
            noop_reverse,
        ),
    ]
