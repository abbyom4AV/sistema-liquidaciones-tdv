from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procesamientos", "0010_glamour"),
    ]

    operations = [
        migrations.AddField(
            model_name="procesamientoglamour",
            name="destino_ui",
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
