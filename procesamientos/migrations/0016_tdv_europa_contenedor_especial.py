from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procesamientos", "0015_kraaijeveld_precio_fijo_contenedor"),
    ]

    operations = [
        migrations.AddField(
            model_name="procesamientotdveuropa",
            name="contenedor_especial",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="procesamientotdveuropa",
            name="incluye_contenedor_especial",
            field=models.BooleanField(default=False),
        ),
    ]
