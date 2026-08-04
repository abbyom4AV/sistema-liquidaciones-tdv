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


# ---------------------------------------------------------------------------
# Orsero
# ---------------------------------------------------------------------------


def _ruta_base_procesamiento_orsero(
    instance: "ProcesamientoOrsero",
) -> str:
    return f"procesamientos/orsero/{instance.id}"


def ruta_archivo_despachos_orsero(instance, filename: str) -> str:
    return f"{_ruta_base_procesamiento_orsero(instance)}/despachos.xlsx"


def ruta_archivo_liquidacion_orsero(
    instance,
    filename: str,
) -> str:
    extension = Path(filename or "").suffix.lower() or ".png"
    if extension not in {".png", ".jpg", ".jpeg"}:
        extension = ".png"
    return (
        f"{_ruta_base_procesamiento_orsero(instance)}"
        f"/liquidacion{extension}"
    )


def ruta_archivo_cliente_orsero(instance, filename: str) -> str:
    return (
        f"{_ruta_base_procesamiento_orsero(instance)}"
        "/cliente.xlsx"
    )


def ruta_archivo_resultado_generacion_orsero(
    instance: "GeneracionOrsero",
    filename: str,
) -> str:
    return (
        f"procesamientos/orsero/"
        f"{instance.procesamiento_id}/resultados/"
        f"{instance.id}/resultado.xlsx"
    )


RUBROS_GASTOS_ORSERO_DEFINICION = (
    ("costo_origen", "Costo en Origen Form.", 1),
    ("inland", "Inland Form.", 2),
    ("thc_origen", "THC Origen Form.", 3),
    ("flete", "Flete Form.", 4),
    ("insurance", "Insurance Form.", 5),
    ("thc_destino", "THC Destino Form.", 6),
    ("forwarding", "Forwarding Form.", 7),
    ("transport_in", "Transport In Form.", 8),
    ("comision", "Comision Form", 9),
)

CODIGO_POR_NOMBRE_ORSERO = {
    nombre: codigo
    for codigo, nombre, _orden in RUBROS_GASTOS_ORSERO_DEFINICION
}

NOMBRE_POR_CODIGO_ORSERO = {
    codigo: nombre
    for codigo, nombre, _orden in RUBROS_GASTOS_ORSERO_DEFINICION
}

ETIQUETAS_ESTADO_ORSERO = {
    "listo": "Listo",
    "invalido": "Con errores",
}


class ProcesamientoOrsero(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    anio = models.PositiveIntegerField(default=0)
    semana = models.PositiveIntegerField(default=0)
    nave_texto = models.CharField(max_length=150, blank=True)
    tipo_cambio = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
    )
    estado = models.CharField(max_length=40)
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
        upload_to=ruta_archivo_despachos_orsero,
    )
    archivo_liquidacion = models.FileField(
        upload_to=ruta_archivo_liquidacion_orsero,
    )
    archivo_cliente = models.FileField(
        upload_to=ruta_archivo_cliente_orsero,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="procesamientos_orsero_creados",
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
            f"ORSERO {self.nave_texto} "
            f"W{self.semana} ({self.anio})"
        )

    @property
    def carpeta_media(self) -> Path:
        return (
            Path(settings.MEDIA_ROOT)
            / "procesamientos"
            / "orsero"
            / str(self.id)
        )

    @property
    def estado_legible(self) -> str:
        return ETIQUETAS_ESTADO_ORSERO.get(
            self.estado,
            self.estado,
        )

    def obtener_gastos_aplicados(self) -> dict[str, Decimal]:
        resultado: dict[str, Decimal] = {}
        for gasto in self.gastos.order_by("orden"):
            nombre = NOMBRE_POR_CODIGO_ORSERO.get(
                gasto.codigo,
                gasto.nombre,
            )
            resultado[nombre] = gasto.valor_aplicado
        return resultado


class GastoProcesamientoOrsero(models.Model):
    procesamiento = models.ForeignKey(
        ProcesamientoOrsero,
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
                    "uniq_gasto_orsero_procesamiento"
                    "_codigo"
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nombre} ({self.procesamiento_id})"

    @property
    def fue_modificado(self) -> bool:
        return self.valor_aplicado != self.valor_original


class CorreccionGastoOrsero(models.Model):
    gasto = models.ForeignKey(
        GastoProcesamientoOrsero,
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


class GeneracionOrsero(models.Model):
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
        ProcesamientoOrsero,
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
        related_name="generaciones_orsero_solicitadas",
    )
    solicitado_por_nombre = models.CharField(max_length=150)
    solicitado_en = models.DateTimeField(auto_now_add=True)
    iniciado_en = models.DateTimeField(null=True, blank=True)
    finalizado_en = models.DateTimeField(null=True, blank=True)
    archivo_resultado = models.FileField(
        upload_to=ruta_archivo_resultado_generacion_orsero,
        blank=True,
    )
    nombre_descarga = models.CharField(
        max_length=255,
        blank=True,
    )
    mensaje_error = models.TextField(blank=True)
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
                    "uniq_generacion_orsero_activa"
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
# Kraaijeveld
# ---------------------------------------------------------------------------


def _ruta_base_procesamiento_kraaijeveld(
    instance: "ProcesamientoKraaijeveld",
) -> str:
    return f"procesamientos/kraaijeveld/{instance.id}"


def ruta_archivo_despachos_kraaijeveld(
    instance,
    filename: str,
) -> str:
    return (
        f"{_ruta_base_procesamiento_kraaijeveld(instance)}"
        "/despachos.xlsx"
    )


def ruta_archivo_cliente_kraaijeveld(
    instance,
    filename: str,
) -> str:
    return (
        f"{_ruta_base_procesamiento_kraaijeveld(instance)}"
        "/cliente.xlsx"
    )


def ruta_archivo_pdf_kraaijeveld(
    instance: "ArchivoPdfKraaijeveld",
    filename: str,
) -> str:
    nombre = Path(filename or "").name or (
        f"liquidacion_{instance.orden}.pdf"
    )
    return (
        f"procesamientos/kraaijeveld/"
        f"{instance.procesamiento_id}/pdfs/"
        f"{instance.orden:02d}_{nombre}"
    )


def ruta_archivo_resultado_generacion_kraaijeveld(
    instance: "GeneracionKraaijeveld",
    filename: str,
) -> str:
    return (
        f"procesamientos/kraaijeveld/"
        f"{instance.procesamiento_id}/resultados/"
        f"{instance.id}/resultado.xlsx"
    )


ETIQUETAS_ESTADO_KRAAIJEVELD = {
    "listo": "Listo",
    "invalido": "Con errores",
}


class ProcesamientoKraaijeveld(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    anio = models.PositiveIntegerField(default=0)
    semana = models.PositiveIntegerField(default=0)
    semana_texto = models.CharField(max_length=20, blank=True)
    destino_ui = models.CharField(max_length=150, blank=True)
    estado = models.CharField(max_length=40)
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
    resumen_gastos_contenedores = models.JSONField(
        default=list,
        blank=True,
    )
    incluye_precio_fijo = models.BooleanField(default=False)
    factura_corta_fijo = models.CharField(
        max_length=10,
        blank=True,
    )
    precio_fijo = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    moneda_fijo = models.CharField(max_length=10, blank=True)
    archivo_despachos = models.FileField(
        upload_to=ruta_archivo_despachos_kraaijeveld,
    )
    archivo_cliente = models.FileField(
        upload_to=ruta_archivo_cliente_kraaijeveld,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="procesamientos_kraaijeveld_creados",
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
            f"KRAAIJEVELD {self.destino_ui} "
            f"W{self.semana} ({self.anio})"
        )

    @property
    def carpeta_media(self) -> Path:
        return (
            Path(settings.MEDIA_ROOT)
            / "procesamientos"
            / "kraaijeveld"
            / str(self.id)
        )

    @property
    def estado_legible(self) -> str:
        return ETIQUETAS_ESTADO_KRAAIJEVELD.get(
            self.estado,
            self.estado,
        )


class ArchivoPdfKraaijeveld(models.Model):
    procesamiento = models.ForeignKey(
        ProcesamientoKraaijeveld,
        related_name="pdfs",
        on_delete=models.CASCADE,
    )
    archivo = models.FileField(
        upload_to=ruta_archivo_pdf_kraaijeveld,
    )
    nombre_original = models.CharField(
        max_length=255,
        blank=True,
    )
    orden = models.PositiveSmallIntegerField(default=0)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["orden"]

    def __str__(self) -> str:
        return self.nombre_original or f"PDF {self.orden}"


class GeneracionKraaijeveld(models.Model):
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
        ProcesamientoKraaijeveld,
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
        related_name="generaciones_kraaijeveld_solicitadas",
    )
    solicitado_por_nombre = models.CharField(max_length=150)
    solicitado_en = models.DateTimeField(auto_now_add=True)
    iniciado_en = models.DateTimeField(null=True, blank=True)
    finalizado_en = models.DateTimeField(null=True, blank=True)
    archivo_resultado = models.FileField(
        upload_to=ruta_archivo_resultado_generacion_kraaijeveld,
        blank=True,
    )
    nombre_descarga = models.CharField(
        max_length=255,
        blank=True,
    )
    mensaje_error = models.TextField(blank=True)
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
                    "uniq_generacion_kraaijeveld_activa"
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


def _ruta_base_procesamiento_sifa(
    instance: "ProcesamientoSifa",
) -> str:
    return f"procesamientos/sifa/{instance.id}"


def ruta_archivo_despachos_sifa(instance, filename: str) -> str:
    return f"{_ruta_base_procesamiento_sifa(instance)}/despachos.xlsx"


def ruta_archivo_liquidacion_sifa(
    instance,
    filename: str,
) -> str:
    return (
        f"{_ruta_base_procesamiento_sifa(instance)}"
        "/liquidacion.xlsx"
    )


def ruta_archivo_cliente_sifa(instance, filename: str) -> str:
    return (
        f"{_ruta_base_procesamiento_sifa(instance)}"
        "/cliente.xlsx"
    )


def ruta_archivo_resultado_generacion_sifa(
    instance: "GeneracionSifa",
    filename: str,
) -> str:
    return (
        f"procesamientos/sifa/"
        f"{instance.procesamiento_id}/resultados/"
        f"{instance.id}/resultado.xlsx"
    )


ETIQUETAS_ESTADO_SIFA = {
    "listo": "Listo",
    "invalido": "Con errores",
}


class ProcesamientoSifa(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    anio = models.PositiveIntegerField(default=0)
    semana = models.PositiveIntegerField(default=0)
    semana_texto = models.CharField(max_length=20, blank=True)
    destino_ui = models.CharField(max_length=150, blank=True)
    factura_corta = models.CharField(max_length=10, blank=True)
    estado = models.CharField(max_length=40)
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
    comision_total = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
    )
    lineas_con_comision = models.PositiveIntegerField(default=0)
    lineas_sin_comision = models.PositiveIntegerField(default=0)
    puede_escribir = models.BooleanField(default=False)
    errores = models.JSONField(default=list, blank=True)
    advertencias = models.JSONField(default=list, blank=True)
    lineas_preparadas = models.JSONField(
        default=list,
        blank=True,
    )
    resumen_gastos = models.JSONField(
        default=dict,
        blank=True,
    )
    resumen_contenedores = models.JSONField(
        default=list,
        blank=True,
    )
    total_venta_eur = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
    )
    archivo_despachos = models.FileField(
        upload_to=ruta_archivo_despachos_sifa,
    )
    archivo_liquidacion = models.FileField(
        upload_to=ruta_archivo_liquidacion_sifa,
    )
    archivo_cliente = models.FileField(
        upload_to=ruta_archivo_cliente_sifa,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="procesamientos_sifa_creados",
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
            f"SIFA {self.destino_ui} "
            f"W{self.semana} ({self.anio})"
        )

    @property
    def carpeta_media(self) -> Path:
        return (
            Path(settings.MEDIA_ROOT)
            / "procesamientos"
            / "sifa"
            / str(self.id)
        )

    @property
    def estado_legible(self) -> str:
        return ETIQUETAS_ESTADO_SIFA.get(
            self.estado,
            self.estado,
        )


class GeneracionSifa(models.Model):
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
        ProcesamientoSifa,
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
        related_name="generaciones_sifa_solicitadas",
    )
    solicitado_por_nombre = models.CharField(max_length=150)
    solicitado_en = models.DateTimeField(auto_now_add=True)
    iniciado_en = models.DateTimeField(null=True, blank=True)
    finalizado_en = models.DateTimeField(null=True, blank=True)
    archivo_resultado = models.FileField(
        upload_to=ruta_archivo_resultado_generacion_sifa,
        blank=True,
    )
    nombre_descarga = models.CharField(
        max_length=255,
        blank=True,
    )
    mensaje_error = models.TextField(blank=True)
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
                    "uniq_generacion_sifa_activa"
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
