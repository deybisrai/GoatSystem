""" Compras: boletas/facturas de proveedores (stock que entra) """

from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from .inventario import Inventario
from .kardex import MovimientoInventario


class CompraBloqueada(ValueError):
    """ La boleta ya movio stock: no se corrige editandola, se anula """


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

    anulado = models.BooleanField(default=False)
    fecha_anulacion = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha_compra']
        constraints = [
            models.UniqueConstraint(fields=['proveedor', 'nro_documento'], name='unico_doc_por_proveedor')
        ]

    def __str__(self):
        return f'{self.get_tipo_documento_display()} {self.nro_documento} - {self.proveedor}'

    @property
    def estado(self):
        if self.anulado:
            return 'Anulada'
        if self.aplicado_a_inventario:
            return 'Aplicada'
        return 'Borrador'

    @property
    def editable(self):
        """ Una boleta se corrige mientras no haya tocado el inventario """
        return not self.aplicado_a_inventario

    def verificar_editable(self):
        """
        Corta cualquier cambio en una boleta ya aplicada.

        El documento en si (proveedor, numero, fecha) se congela desde el admin;
        aqui se blindan las lineas, que son las unicas capaces de separar lo que
        dice la factura de lo que hay en el almacen.
        """
        if not self.editable:
            raise CompraBloqueada(
                f'{self} ya sumo stock al inventario: sus lineas no se editan ni se borran. '
                'Si esta equivocada, anulala para devolver las unidades.'
            )

    def delete(self, *args, **kwargs):
        # borrar una boleta aplicada dejaba unidades sin ningun papel que las
        # respalde. Ademas el kardex la referencia con RESTRICT, asi que la base
        # de datos tampoco la deja ir.
        self.verificar_editable()
        return super().delete(*args, **kwargs)

    def aplicar_a_inventario(self, usuario=None):
        """
        Suma al stock cada linea de la compra y recalcula su costo promedio,
        dejando un movimiento de kardex por linea.
        La bandera aplicado_a_inventario evita que la misma boleta sume dos veces.
        """
        if self.anulado:
            raise ValueError('Esta compra esta anulada')
        if self.aplicado_a_inventario:
            raise ValueError('Esta compra ya fue aplicada al inventario')

        with transaction.atomic():
            self.recalcular_total()
            for detalle in self.detalles.select_related('item'):
                detalle.item.registrar_compra(
                    detalle.cantidad, detalle.costo_unitario, compra=self, usuario=usuario
                )
            self.aplicado_a_inventario = True
            self.save(update_fields=['aplicado_a_inventario'])

    def anular(self, motivo, usuario=None):
        """
        Deshace una boleta ya aplicada: saca del almacen las unidades que trajo y
        devuelve el costo promedio a donde estaba.

        La boleta no se borra. Queda anulada, con su motivo y con sus movimientos
        de entrada y de salida, para que el kardex siga explicando cada unidad.
        """
        if not self.aplicado_a_inventario:
            raise ValueError('Esta compra todavia no toco el inventario: borrala en vez de anularla')
        if self.anulado:
            raise ValueError('Esta compra ya esta anulada')
        motivo = (motivo or '').strip()
        if not motivo:
            raise ValueError('Anular una compra exige un motivo')

        with transaction.atomic():
            detalles = list(self.detalles.order_by('id'))
            items = {
                item.id: item
                for item in Inventario.objects
                .select_for_update()
                .filter(id__in={d.item_id for d in detalles})
            }

            # se revisa todo antes de mover nada: una boleta anulada a medias
            # seria peor que la boleta equivocada
            for detalle in detalles:
                item = items[detalle.item_id]
                if detalle.cantidad > item.stock:
                    raise ValueError(
                        f'No se puede anular: la boleta trajo {detalle.cantidad} de {item} '
                        f'y solo quedan {item.stock}. Esas unidades ya salieron, asi que '
                        'corresponde un ajuste de inventario, no anular la compra.'
                    )

            for detalle in detalles:
                items[detalle.item_id].registrar_movimiento(
                    MovimientoInventario.ANULA_COMPRA,
                    -detalle.cantidad,
                    costo_unitario=detalle.costo_unitario,
                    compra=self,
                    motivo=motivo,
                    usuario=usuario,
                )

            self.anulado = True
            self.fecha_anulacion = timezone.now()
            self.motivo_anulacion = motivo
            self.save(update_fields=['anulado', 'fecha_anulacion', 'motivo_anulacion'])

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
        self.compra.verificar_editable()
        self.subtotal = self.cantidad * self.costo_unitario
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.compra.verificar_editable()
        return super().delete(*args, **kwargs)
