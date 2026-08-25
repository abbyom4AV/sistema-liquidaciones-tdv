from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procesamientos", "0016_tdv_europa_contenedor_especial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="procesamientotdveuropa",
            name="contenedor_especial",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
