from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import models


def _ruta_base_procesamiento(instance: "ProcesamientoDimanno") -> str:
    return f"procesamientos/dimanno/{instance.id}"


def ruta_archivo_despachos(instance, filename: str) -> str:
    return f"{_ruta_base_procesamiento(instance)}/despachos.xlsx"


def ruta_archivo_liquidacion(instance, filename: str) -> str:
    return f"{_ruta_base_procesamiento(instance)}/liquidacion.xlsx"


def ruta_archivo_cliente(instance, filename: str) -> str:
    return f"{_ruta_base_procesamiento(instance)}/cliente.xlsx"


def ruta_archivo_resultado_generacion(
    instance: "GeneracionDimanno",
    filename: str,
) -> str:
    return (
        f"procesamientos/dimanno/"
        f"{instance.procesamiento_id}/resultados/"
        f"{instance.id}/resultado.xlsx"
    )


RUBROS_GASTOS_DEFINICION = (
    ("comision", "Comisión", 1),
    ("flete_eu", "Flete Eu", 2),
    ("control_calidad_eu", "Control calidad Eu", 3),
    ("thc", "THC", 4),
    ("transporte", "Transporte", 5),
    ("aduanas", "Aduanas", 6),
)

CODIGO_POR_NOMBRE = {
    nombre: codigo
    for codigo, nombre, _orden in RUBROS_GASTOS_DEFINICION
}

NOMBRE_POR_CODIGO = {
    codigo: nombre
    for codigo, nombre, _orden in RUBROS_GASTOS_DEFINICION
}

ETIQUETAS_ESTADO = {
    "listo": "Listo",
    "requiere_destino": "Requiere definir destino",
    "invalido": "Con errores",
}

ETIQUETAS_ORIGEN_DESTINO = {
    "coincidente": "Coincidencia automática",
    "liquidacion": "Liquidación",
    "despachos": "Despachos",
    "manual": "Ingresado manualmente",
}


class ProcesamientoDimanno(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    anio = models.PositiveIntegerField()
    nombre_hoja = models.CharField(max_length=100)
    factura_corta = models.CharField(max_length=20)
    semana = models.PositiveIntegerField()
    estado = models.CharField(max_length=40)
    destino_liquidacion = models.CharField(
        max_length=150,
        blank=True,
    )
    destinos_despachos = models.JSONField(
        default=list,
        blank=True,
    )
    destino_final = models.CharField(
        max_length=150,
        blank=True,
    )
    origen_destino_final = models.CharField(
        max_length=30,
        blank=True,
    )
    cantidad_contenedores = models.PositiveIntegerField(
        default=0,
    )
    total_cajas_liquidacion = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
    )
    total_cajas_despachos = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
    )
    puede_escribir = models.BooleanField(default=False)
    requiere_resolver_destino = models.BooleanField(
        default=False,
    )
    errores = models.JSONField(default=list, blank=True)
    advertencias = models.JSONField(default=list, blank=True)
    lineas_preparadas = models.JSONField(
        default=list,
        blank=True,
    )
    archivo_despachos = models.FileField(
        upload_to=ruta_archivo_despachos,
    )
    archivo_liquidacion = models.FileField(
        upload_to=ruta_archivo_liquidacion,
    )
    archivo_cliente = models.FileField(
        upload_to=ruta_archivo_cliente,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="procesamientos_dimanno_creados",
    )
    creado_por_nombre = models.CharField(
        max_length=150,
        blank=True,
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado_en"]

    def __str__(self) -> str:
        return (
            f"Di Manno {self.factura_corta} "
            f"W{self.semana} ({self.anio})"
        )

    @property
    def carpeta_media(self) -> Path:
        return (
            Path(settings.MEDIA_ROOT)
            / "procesamientos"
            / "dimanno"
            / str(self.id)
        )

    @property
    def estado_legible(self) -> str:
        return ETIQUETAS_ESTADO.get(self.estado, self.estado)

    @property
    def origen_destino_legible(self) -> str:
        if not self.origen_destino_final:
            return "—"
        return ETIQUETAS_ORIGEN_DESTINO.get(
            self.origen_destino_final,
            self.origen_destino_final,
        )

    @property
    def total_gastos_originales(self) -> Decimal:
        total = Decimal("0")
        for gasto in self.gastos.all():
            total += gasto.valor_original
        return total

    @property
    def total_gastos_aplicados(self) -> Decimal:
        total = Decimal("0")
        for gasto in self.gastos.all():
            total += gasto.valor_aplicado
        return total

    def obtener_gastos_aplicados(self) -> dict[str, Decimal]:
        resultado: dict[str, Decimal] = {}
        for gasto in self.gastos.order_by("orden"):
            nombre = NOMBRE_POR_CODIGO.get(
                gasto.codigo,
                gasto.nombre,
            )
            resultado[nombre] = gasto.valor_aplicado
        return resultado


class GastoProcesamientoDimanno(models.Model):
    class CodigoRubro(models.TextChoices):
        COMISION = "comision", "Comisión"
        FLETE_EU = "flete_eu", "Flete Eu"
        CONTROL_CALIDAD_EU = (
            "control_calidad_eu",
            "Control calidad Eu",
        )
        THC = "thc", "THC"
        TRANSPORTE = "transporte", "Transporte"
        ADUANAS = "aduanas", "Aduanas"

    procesamiento = models.ForeignKey(
        ProcesamientoDimanno,
        related_name="gastos",
        on_delete=models.CASCADE,
    )
    codigo = models.CharField(
        max_length=40,
        choices=CodigoRubro.choices,
    )
    nombre = models.CharField(max_length=100)
    orden = models.PositiveSmallIntegerField()
    valor_original = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    valor_aplicado = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )

    class Meta:
        ordering = ["orden"]
        constraints = [
            models.UniqueConstraint(
                fields=["procesamiento", "codigo"],
                name="uniq_gasto_dimanno_procesamiento_codigo",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nombre} ({self.procesamiento_id})"

    @property
    def fue_modificado(self) -> bool:
        return self.valor_aplicado != self.valor_original


class CorreccionGastoDimanno(models.Model):
    gasto = models.ForeignKey(
        GastoProcesamientoDimanno,
        related_name="correcciones",
        on_delete=models.CASCADE,
    )
    valor_anterior = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    valor_nuevo = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    motivo = models.TextField()
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    usuario_nombre = models.CharField(max_length=150)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]

    def __str__(self) -> str:
        return (
            f"Corrección {self.gasto.nombre}: "
            f"{self.valor_anterior} → {self.valor_nuevo}"
        )


class ResolucionDestinoDimanno(models.Model):
    class OrigenSeleccion(models.TextChoices):
        LIQUIDACION = "liquidacion", "Liquidación"
        DESPACHOS = "despachos", "Despachos"
        MANUAL = "manual", "Manual"

    procesamiento = models.ForeignKey(
        ProcesamientoDimanno,
        related_name="resoluciones_destino",
        on_delete=models.CASCADE,
    )
    destino_anterior = models.CharField(
        max_length=150,
        blank=True,
    )
    destino_nuevo = models.CharField(max_length=150)
    origen_seleccionado = models.CharField(
        max_length=30,
        choices=OrigenSeleccion.choices,
    )
    destino_liquidacion = models.CharField(
        max_length=150,
        blank=True,
    )
    destinos_despachos = models.JSONField(
        default=list,
        blank=True,
    )
    motivo = models.TextField()
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    usuario_nombre = models.CharField(max_length=150)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]

    def __str__(self) -> str:
        return (
            f"Resolución destino "
            f"{self.destino_anterior or '—'} → "
            f"{self.destino_nuevo}"
        )


ETIQUETAS_ESTADO_GENERACION = {
    "pendiente": "Pendiente",
    "procesando": "Procesando",
    "completado": "Completado",
    "error": "Error",
}


class GeneracionDimanno(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PROCESANDO = "procesando", "Procesando"
        COMPLETADO = "completado", "Completado"
        ERROR = "error", "Error"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    procesamiento = models.ForeignKey(
        ProcesamientoDimanno,
        related_name="generaciones",
        on_delete=models.CASCADE,
    )
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generaciones_dimanno_solicitadas",
    )
    solicitado_por_nombre = models.CharField(max_length=150)
    solicitado_en = models.DateTimeField(auto_now_add=True)
    iniciado_en = models.DateTimeField(null=True, blank=True)
    finalizado_en = models.DateTimeField(null=True, blank=True)
    archivo_resultado = models.FileField(
        upload_to=ruta_archivo_resultado_generacion,
        blank=True,
    )
    nombre_descarga = models.CharField(
        max_length=255,
        blank=True,
    )
    mensaje_error = models.TextField(blank=True)
    destino_aplicado = models.CharField(max_length=150)
    origen_destino_aplicado = models.CharField(
        max_length=30,
        blank=True,
    )
    gastos_aplicados = models.JSONField(default=dict)
    filas_agregadas = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    fila_inicial = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    fila_final = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    rango_tabla = models.CharField(
        max_length=100,
        blank=True,
    )
    intentos = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-solicitado_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["procesamiento"],
                condition=models.Q(
                    estado__in=["pendiente", "procesando"]
                ),
                name=(
                    "uniq_generacion_dimanno_activa"
                    "_por_procesamiento"
                ),
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Generación {self.procesamiento_id} "
            f"({self.estado})"
        )

    @property
    def estado_legible(self) -> str:
        return ETIQUETAS_ESTADO_GENERACION.get(
            self.estado,
            self.estado,
        )

    @property
    def esta_activa(self) -> bool:
        return self.estado in {
            self.Estado.PENDIENTE,
            self.Estado.PROCESANDO,
        }

    @property
    def esta_completada(self) -> bool:
        return self.estado == self.Estado.COMPLETADO

    @property
    def tiene_error(self) -> bool:
        return self.estado == self.Estado.ERROR


# ---------------------------------------------------------------------------
# Master Fruits
# ---------------------------------------------------------------------------


def _ruta_base_procesamiento_master(
    instance: "ProcesamientoMaster",
) -> str:
    return f"procesamientos/master/{instance.id}"


def ruta_archivo_despachos_master(instance, filename: str) -> str:
    return f"{_ruta_base_procesamiento_master(instance)}/despachos.xlsx"


def ruta_archivo_liquidacion_master(
    instance,
    filename: str,
) -> str:
    return (
        f"{_ruta_base_procesamiento_master(instance)}"
        "/liquidacion.pdf"
    )


def ruta_archivo_cliente_master(instance, filename: str) -> str:
    return (
        f"{_ruta_base_procesamiento_master(instance)}"
        "/cliente.xlsx"
    )


def ruta_archivo_resultado_generacion_master(
    instance: "GeneracionMaster",
    filename: str,
) -> str:
    return (
        f"procesamientos/master/"
        f"{instance.procesamiento_id}/resultados/"
        f"{instance.id}/resultado.xlsx"
    )


RUBROS_GASTOS_MASTER_DEFINICION = (
    ("lc_euros", "LC Euros", 1),
    ("cust_c", "Cust.C Euros", 2),
    ("import_d", "Import.D Euros", 3),
    ("ener_demur", "Ener&Demur. Euros", 4),
    ("inspection", "Inspection Euros", 5),
    ("transport_pw", "Transport.P-W Euros", 6),
    ("transport_c", "Transport C. Euros", 7),
    ("relabelling", "Relabelling Euros", 8),
    ("comision", "Comision Euros", 9),
)

CODIGO_POR_NOMBRE_MASTER = {
    nombre: codigo
    for codigo, nombre, _orden in RUBROS_GASTOS_MASTER_DEFINICION
}

NOMBRE_POR_CODIGO_MASTER = {
    codigo: nombre
    for codigo, nombre, _orden in RUBROS_GASTOS_MASTER_DEFINICION
}

ETIQUETAS_ESTADO_MASTER = {
    "listo": "Listo",
    "invalido": "Con errores",
}


class ProcesamientoMaster(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    anio = models.PositiveIntegerField(default=0)
    factura_corta = models.CharField(max_length=20)
    semana = models.PositiveIntegerField(default=0)
    semana_texto = models.CharField(max_length=20, blank=True)
    estado = models.CharField(max_length=40)
    destino_final = models.CharField(max_length=150, blank=True)
    origen_destino_final = models.CharField(
        max_length=30,
        blank=True,
    )
    destinos_despachos = models.JSONField(
        default=list,
        blank=True,
    )
    cantidad_contenedores = models.PositiveIntegerField(
        default=0,
    )
    total_cajas_liquidacion = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
    )
    total_cajas_despachos = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
    )
    puede_escribir = models.BooleanField(default=False)
    errores = models.JSONField(default=list, blank=True)
    advertencias = models.JSONField(default=list, blank=True)
    lineas_preparadas = models.JSONField(
        default=list,
        blank=True,
    )
    archivo_despachos = models.FileField(
        upload_to=ruta_archivo_despachos_master,
    )
    archivo_liquidacion = models.FileField(
        upload_to=ruta_archivo_liquidacion_master,
    )
    archivo_cliente = models.FileField(
        upload_to=ruta_archivo_cliente_master,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="procesamientos_master_creados",
    )
    creado_por_nombre = models.CharField(
        max_length=150,
        blank=True,
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado_en"]

    def __str__(self) -> str:
        return (
            f"Master Fruits {self.factura_corta} "
            f"W{self.semana} ({self.anio})"
        )

    @property
    def carpeta_media(self) -> Path:
        return (
            Path(settings.MEDIA_ROOT)
            / "procesamientos"
            / "master"
            / str(self.id)
        )

    @property
    def estado_legible(self) -> str:
        return ETIQUETAS_ESTADO_MASTER.get(
            self.estado,
            self.estado,
        )

    def obtener_gastos_aplicados(self) -> dict[str, Decimal]:
        resultado: dict[str, Decimal] = {}
        for gasto in self.gastos.order_by("orden"):
            nombre = NOMBRE_POR_CODIGO_MASTER.get(
                gasto.codigo,
                gasto.nombre,
            )
            resultado[nombre] = gasto.valor_aplicado
        return resultado


class GastoProcesamientoMaster(models.Model):
    procesamiento = models.ForeignKey(
        ProcesamientoMaster,
        related_name="gastos",
        on_delete=models.CASCADE,
    )
    codigo = models.CharField(max_length=40)
    nombre = models.CharField(max_length=100)
    orden = models.PositiveSmallIntegerField()
    valor_original = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    valor_aplicado = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )

    class Meta:
        ordering = ["orden"]
        constraints = [
            models.UniqueConstraint(
                fields=["procesamiento", "codigo"],
                name=(
                    "uniq_gasto_master_procesamiento"
                    "_codigo"
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nombre} ({self.procesamiento_id})"

    @property
    def fue_modificado(self) -> bool:
        return self.valor_aplicado != self.valor_original


class CorreccionGastoMaster(models.Model):
    gasto = models.ForeignKey(
        GastoProcesamientoMaster,
        related_name="correcciones",
        on_delete=models.CASCADE,
    )
    valor_anterior = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    valor_nuevo = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    motivo = models.TextField()
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    usuario_nombre = models.CharField(max_length=150)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]


class GeneracionMaster(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PROCESANDO = "procesando", "Procesando"
        COMPLETADO = "completado", "Completado"
        ERROR = "error", "Error"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    procesamiento = models.ForeignKey(
        ProcesamientoMaster,
        related_name="generaciones",
        on_delete=models.CASCADE,
    )
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generaciones_master_solicitadas",
    )
    solicitado_por_nombre = models.CharField(max_length=150)
    solicitado_en = models.DateTimeField(auto_now_add=True)
    iniciado_en = models.DateTimeField(null=True, blank=True)
    finalizado_en = models.DateTimeField(null=True, blank=True)
    archivo_resultado = models.FileField(
        upload_to=ruta_archivo_resultado_generacion_master,
        blank=True,
    )
    nombre_descarga = models.CharField(
        max_length=255,
        blank=True,
    )
    mensaje_error = models.TextField(blank=True)
    destino_aplicado = models.CharField(max_length=150)
    origen_destino_aplicado = models.CharField(
        max_length=30,
        blank=True,
    )
    gastos_aplicados = models.JSONField(default=dict)
    filas_agregadas = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    fila_inicial = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    fila_final = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    rango_tabla = models.CharField(
        max_length=100,
        blank=True,
    )
    intentos = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-solicitado_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["procesamiento"],
                condition=models.Q(
                    estado__in=["pendiente", "procesando"]
                ),
                name=(
                    "uniq_generacion_master_activa"
                    "_por_procesamiento"
                ),
            ),
        ]

    @property
    def estado_legible(self) -> str:
        return ETIQUETAS_ESTADO_GENERACION.get(
            self.estado,
            self.estado,
        )

    @property
    def esta_activa(self) -> bool:
        return self.estado in {
            self.Estado.PENDIENTE,
            self.Estado.PROCESANDO,
        }

    @property
    def esta_completada(self) -> bool:
        return self.estado == self.Estado.COMPLETADO

    @property
    def tiene_error(self) -> bool:
        return self.estado == self.Estado.ERROR
