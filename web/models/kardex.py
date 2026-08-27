"""
Kardex: el historial de cada entrada y salida del inventario.

Responde "por que esta talla tiene 3 unidades" sin tener que cruzar compras y
pedidos por separado.
"""

from django.contrib.auth.models import User
from django.db import models


class MovimientoInventario(models.Model):
    """
    Una linea del kardex: por que cambio el stock de una unidad vendible.

    Es la bitacora, no el saldo. `Inventario.stock` sigue siendo el saldo que
    lee el catalogo (una lectura por fila, sin sumar nada); aqui queda el rastro
    de como llego a ese numero y que documento lo respalda.

    `cantidad` es positiva si entra y negativa si sale. `stock_resultante` guarda
    el saldo justo despues del movimiento, para leer el kardex de corrido sin ir
    acumulando a mano.
    """

    INICIAL = 'INICIAL'
    COMPRA = 'COMPRA'
    ANULA_COMPRA = 'ANULA_COMPRA'
    VENTA = 'VENTA'
    CANCELA_VENTA = 'CANCELA_VENTA'
    TRASLADO_SALIDA = 'TRASLADO_SAL'
    TRASLADO_ENTRADA = 'TRASLADO_ENT'
    AJUSTE = 'AJUSTE'

    TIPO_CHOICES = (
        (INICIAL, 'Saldo inicial'),
        (COMPRA, 'Compra'),
        (ANULA_COMPRA, 'Anulacion de compra'),
        (VENTA, 'Venta'),
        (CANCELA_VENTA, 'Cancelacion de venta'),
        (TRASLADO_SALIDA, 'Traslado: salida'),
        (TRASLADO_ENTRADA, 'Traslado: entrada'),
        (AJUSTE, 'Ajuste manual'),
    )

    item = models.ForeignKey('web.Inventario', on_delete=models.RESTRICT, related_name='movimientos')
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES)
    cantidad = models.IntegerField(help_text='Positiva si entra, negativa si sale.')
    stock_anterior = models.PositiveIntegerField()
    stock_resultante = models.PositiveIntegerField()

    # a que costo entraron o salieron estas unidades, y en cuanto quedo el
    # promedio despues del movimiento
    costo_unitario = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    costo_promedio_resultante = models.DecimalField(max_digits=9, decimal_places=2, default=0)

    # el documento que respalda el movimiento. RESTRICT a proposito: no se borra
    # una boleta que ya movio stock, porque dejaria unidades sin justificacion.
    compra = models.ForeignKey(
        'web.Compra', on_delete=models.RESTRICT, null=True, blank=True, related_name='movimientos'
    )
    pedido = models.ForeignKey(
        'web.Pedido', on_delete=models.RESTRICT, null=True, blank=True, related_name='movimientos'
    )
    traslado = models.ForeignKey(
        'web.Traslado', on_delete=models.RESTRICT, null=True, blank=True, related_name='movimientos'
    )

    motivo = models.TextField(blank=True)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimiento de inventario'
        verbose_name_plural = 'Kardex'
        ordering = ['-fecha', '-id']
        indexes = [
            models.Index(fields=['item', '-fecha'], name='idx_kardex_item_fecha'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.cantidad:+d} - {self.item}'

    @property
    def documento(self):
        """ Como se llama el papel que respalda este movimiento """
        if self.compra_id:
            return str(self.compra)
        if self.pedido_id:
            return f'Pedido {self.pedido.nro_pedido}'
        if self.traslado_id:
            return str(self.traslado)
        return ''
