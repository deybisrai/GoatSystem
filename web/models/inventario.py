""" Inventario: stock y costo real de cada unidad vendible """

from decimal import Decimal

from django.db import models


class Inventario(models.Model):
    """
    La unidad real de inventario: una variante (color) en un valor concreto
    del atributo de su categoria.

      zapatilla  -> variante NEGRO + talla 40
      celular    -> variante NEGRO + capacidad 128GB
      licuadora  -> variante NEGRO, sin valor (su categoria no usa atributo)

    Es lo que se compra al proveedor y lo que se vende al cliente.
    """
    variante = models.ForeignKey('web.Variante', on_delete=models.RESTRICT, related_name='items')
    valor = models.ForeignKey(
        'web.ValorAtributo', on_delete=models.RESTRICT, null=True, blank=True, related_name='items',
        help_text='Talla, capacidad, pulgadas... Vacio si la categoria no usa atributo.'
    )
    precio_venta_override = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        help_text='Dejar vacio para usar el precio de la variante. Solo si esta unidad cuesta distinto.'
    )
    costo_promedio = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    stock = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Inventario'
        verbose_name_plural = 'Inventario'
        ordering = ['variante', 'valor']
        # MySQL no soporta constraints con condicion, asi que va uno simple:
        # cubre el caso normal (no repetir un valor dentro de la variante).
        # El caso de "una sola fila sin valor" se valida en clean(), abajo.
        constraints = [
            models.UniqueConstraint(fields=['variante', 'valor'], name='unico_valor_por_variante'),
        ]

    def __str__(self):
        if self.valor_id is None:
            return str(self.variante)
        return f'{self.variante} - {self.valor}'

    @property
    def etiqueta(self):
        """ Como se nombra esta unidad en la tienda: '40', '128GB' o el color solo """
        return str(self.valor) if self.valor_id else self.variante.color.nombre

    @staticmethod
    def valores_validos(producto):
        """
        Que valores tiene sentido ofrecer para este producto.
        Zapatilla de mujer -> solo tallas de dama. Celular -> solo capacidades.
        Si no hay curva declarada, se ofrecen todos los valores del atributo.
        """
        from .catalogo import Curva, ValorAtributo

        categoria = producto.categoria
        if categoria.atributo_id is None:
            return ValorAtributo.objects.none()

        valores = ValorAtributo.objects.filter(atributo_id=categoria.atributo_id)

        # si la categoria usa genero pero el producto aun no lo tiene definido,
        # no hay forma de acotar: se ofrecen todos hasta que se elija el genero
        if categoria.usa_genero and not producto.genero:
            return valores

        curvas = Curva.aplicables(categoria, producto.genero)
        if curvas.exists():
            acotados = valores.filter(curvas__in=curvas).distinct()
            if acotados.exists():
                return acotados

        return valores

    def clean(self):
        """
        Coherencia entre la categoria y esta fila:
        - si la categoria usa un atributo, el valor es obligatorio y debe ser de ese atributo
        - si no lo usa, la variante tiene una sola fila y sin valor
        """
        from django.core.exceptions import ValidationError

        if not self.variante_id:
            return

        atributo = self.variante.producto.categoria.atributo

        if atributo is None:
            if self.valor_id:
                raise ValidationError(
                    {'valor': f'La categoria {self.variante.producto.categoria} no usa atributos. Deja el valor vacio.'}
                )
            hermanas = Inventario.objects.filter(variante=self.variante, valor__isnull=True)
            if self.pk:
                hermanas = hermanas.exclude(pk=self.pk)
            if hermanas.exists():
                raise ValidationError('Esta variante ya tiene su fila de inventario.')
            return

        if not self.valor_id:
            raise ValidationError({'valor': f'Elige un valor de {atributo.nombre}.'})

        if self.valor.atributo_id != atributo.id:
            raise ValidationError(
                {'valor': f'La categoria {self.variante.producto.categoria} usa {atributo.nombre}, '
                          f'no {self.valor.atributo}.'}
            )

    def precio_venta(self):
        """ Precio de lista, sin descuentos de campana """
        return self.precio_venta_override or self.variante.precio_venta

    def precio_final(self):
        """ Precio que realmente paga el cliente (con descuento de campana si hay) """
        base = self.precio_venta()
        campana = self.variante.campana_vigente()
        if not campana:
            return base
        descuento = base * campana.porcentaje_descuento / Decimal('100')
        return (base - descuento).quantize(Decimal('0.01'))

    @property
    def disponible(self):
        return self.stock > 0

    def registrar_compra(self, cantidad, costo_unitario):
        """ Suma stock nuevo y recalcula el costo promedio ponderado """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')
        valor_actual = self.stock * self.costo_promedio
        valor_nuevo = cantidad * costo_unitario
        self.stock += cantidad
        self.costo_promedio = (valor_actual + valor_nuevo) / self.stock
        self.save()

    def descontar_stock(self, cantidad):
        """ Descuenta stock por una venta. Falla si no hay suficiente. """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')
        if cantidad > self.stock:
            raise ValueError(f'Solo quedan {self.stock} unidades de {self}')
        self.stock -= cantidad
        self.save(update_fields=['stock'])
