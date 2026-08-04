# Generated manually for SIFA Django layer

import django.db.models.deletion
import procesamientos.models
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procesamientos', '0007_kraaijeveld'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProcesamientoSifa',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('anio', models.PositiveIntegerField(default=0)),
                ('semana', models.PositiveIntegerField(default=0)),
                ('semana_texto', models.CharField(blank=True, max_length=20)),
                ('destino_ui', models.CharField(blank=True, max_length=150)),
                ('factura_corta', models.CharField(blank=True, max_length=10)),
                ('estado', models.CharField(max_length=40)),
                ('destinos_despachos', models.JSONField(blank=True, default=list)),
                ('cantidad_contenedores', models.PositiveIntegerField(default=0)),
                ('total_cajas_liquidacion', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=18)),
                ('total_cajas_despachos', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=18)),
                ('comision_total', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=18)),
                ('lineas_con_comision', models.PositiveIntegerField(default=0)),
                ('lineas_sin_comision', models.PositiveIntegerField(default=0)),
                ('puede_escribir', models.BooleanField(default=False)),
                ('errores', models.JSONField(blank=True, default=list)),
                ('advertencias', models.JSONField(blank=True, default=list)),
                ('lineas_preparadas', models.JSONField(blank=True, default=list)),
                ('resumen_gastos', models.JSONField(blank=True, default=dict)),
                ('archivo_despachos', models.FileField(upload_to=procesamientos.models.ruta_archivo_despachos_sifa)),
                ('archivo_liquidacion', models.FileField(upload_to=procesamientos.models.ruta_archivo_liquidacion_sifa)),
                ('archivo_cliente', models.FileField(upload_to=procesamientos.models.ruta_archivo_cliente_sifa)),
                ('creado_por_nombre', models.CharField(blank=True, max_length=150)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('creado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='procesamientos_sifa_creados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-creado_en'],
            },
        ),
        migrations.CreateModel(
            name='GeneracionSifa',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('estado', models.CharField(choices=[('pendiente', 'Pendiente'), ('procesando', 'Procesando'), ('completado', 'Completado'), ('error', 'Error')], default='pendiente', max_length=20)),
                ('solicitado_por_nombre', models.CharField(max_length=150)),
                ('solicitado_en', models.DateTimeField(auto_now_add=True)),
                ('iniciado_en', models.DateTimeField(blank=True, null=True)),
                ('finalizado_en', models.DateTimeField(blank=True, null=True)),
                ('archivo_resultado', models.FileField(blank=True, upload_to=procesamientos.models.ruta_archivo_resultado_generacion_sifa)),
                ('nombre_descarga', models.CharField(blank=True, max_length=255)),
                ('mensaje_error', models.TextField(blank=True)),
                ('filas_agregadas', models.PositiveIntegerField(blank=True, null=True)),
                ('fila_inicial', models.PositiveIntegerField(blank=True, null=True)),
                ('fila_final', models.PositiveIntegerField(blank=True, null=True)),
                ('rango_tabla', models.CharField(blank=True, max_length=100)),
                ('intentos', models.PositiveSmallIntegerField(default=0)),
                ('solicitado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generaciones_sifa_solicitadas', to=settings.AUTH_USER_MODEL)),
                ('procesamiento', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='generaciones', to='procesamientos.procesamientosifa')),
            ],
            options={
                'ordering': ['-solicitado_en'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('estado__in', ['pendiente', 'procesando'])), fields=('procesamiento',), name='uniq_generacion_sifa_activa_por_procesamiento')],
            },
        ),
    ]
