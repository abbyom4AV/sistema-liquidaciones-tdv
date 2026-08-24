from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procesamientos", "0014_fruver_factura_corta"),
    ]

    operations = [
        migrations.AddField(
            model_name="procesamientokraaijeveld",
            name="contenedor_fijo",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="procesamientokraaijeveld",
            name="mapeos_precio_fijo",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="procesamientokraaijeveld",
            name="modo_precio_fijo",
            field=models.CharField(
                blank=True,
                default="",
                max_length=20,
            ),
        ),
    ]
