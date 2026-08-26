""" Compras: boletas/facturas de proveedores (stock que entra) """

from django.core.validators import MinValueValidator
from django.db import models, transaction


class Proveedor(models.Model):
    razon_social = models.CharField(max_length=150)
    ruc = models.CharField(max_length=11, unique=True)
    telefono = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Proveedores'
        ordering = ['razon_social']

    def __str__(self):
        return self.razon_social


class Compra(models.Model):
    """ Cabecera de la boleta/factura de compra al proveedor """

    TIPO_DOC_CHOICES = (
        ('B', 'Boleta'),
        ('F', 'Factura'),
    )

    proveedor = models.ForeignKey(Proveedor, on_delete=models.RESTRICT, related_name='compras')
    tipo_documento = models.CharField(max_length=1, choices=TIPO_DOC_CHOICES, default='B')
    nro_documento = models.CharField(max_length=30)
    fecha_compra = models.DateField()
    monto_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    observacion = models.TextField(blank=True)
    aplicado_a_inventario = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_compra']
        constraints = [
            models.UniqueConstraint(fields=['proveedor', 'nro_documento'], name='unico_doc_por_proveedor')
        ]

    def __str__(self):
        return f'{self.get_tipo_documento_display()} {self.nro_documento} - {self.proveedor}'

    def aplicar_a_inventario(self):
        """
        Suma al stock cada linea de la compra y recalcula su costo promedio.
        La bandera aplicado_a_inventario evita que la misma boleta sume stock dos veces.
        """
        if self.aplicado_a_inventario:
            raise ValueError('Esta compra ya fue aplicada al inventario')

        with transaction.atomic():
            for detalle in self.detalles.select_related('item'):
                detalle.item.registrar_compra(detalle.cantidad, detalle.costo_unitario)
            self.aplicado_a_inventario = True
            self.save(update_fields=['aplicado_a_inventario'])

    def recalcular_total(self):
        total = sum((d.subtotal for d in self.detalles.all()), 0)
        self.monto_total = total
        self.save(update_fields=['monto_total'])
        return total


class CompraDetalle(models.Model):
    """ Cada linea de la boleta: que compraste, cuanto y a que costo """
    compra = models.ForeignKey(Compra, on_delete=models.RESTRICT, related_name='detalles')
    item = models.ForeignKey('web.Inventario', on_delete=models.RESTRICT, related_name='compras')
    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text='No registres lineas con cantidad 0: simplemente no incluyas esa fila en la boleta.'
    )
    costo_unitario = models.DecimalField(max_digits=9, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f'{self.item} x{self.cantidad}'

    def save(self, *args, **kwargs):
        self.subtotal = self.cantidad * self.costo_unitario
        super().save(*args, **kwargs)
