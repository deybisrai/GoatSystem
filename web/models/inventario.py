""" Inventario: stock y costo real de cada unidad vendible """

from decimal import Decimal

from django.db import models, transaction

from .kardex import MovimientoInventario


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
    ubicacion = models.ForeignKey(
        'web.Ubicacion', on_delete=models.RESTRICT, related_name='items',
        help_text='En que ciudad esta fisicamente esta mercaderia.',
    )
    valor = models.ForeignKey(
        'web.ValorAtributo', on_delete=models.RESTRICT, null=True, blank=True, related_name='items',
        help_text='Talla, capacidad, pulgadas... Vacio si la categoria no usa atributo.'
    )
    precio_venta_override = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        help_text='Dejar vacio para usar el precio de la variante. Solo si esta unidad cuesta distinto.'
    )
    costo_promedio = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    stock = models.PositiveIntegerField(
        default=0, help_text='Lo que hay en el estante, este prometido o no.'
    )
    reservado = models.PositiveIntegerField(
        default=0,
        help_text='Comprometido por pedidos sin pagar. Sigue en el estante, pero no se vende.',
    )

    class Meta:
        verbose_name = 'Inventario'
        verbose_name_plural = 'Inventario'
        ordering = ['variante', 'valor']
        # MySQL no soporta constraints con condicion, asi que va uno simple:
        # cubre el caso normal (no repetir un valor dentro de la variante en una
        # misma ciudad). El mismo SKU si existe dos veces si esta en dos ciudades.
        # El caso de "una sola fila sin valor" se valida en clean(), abajo.
        constraints = [
            models.UniqueConstraint(
                fields=['variante', 'valor', 'ubicacion'], name='unico_valor_por_variante'
            ),
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

    def save(self, *args, **kwargs):
        """
        Sin ciudad, la mercaderia va al almacen general.

        Es lo que pasa en la realidad: todo lo que se compra entra a GOAT X y de
        ahi se reparte. Que haya que elegir la ciudad para registrar una talla
        seria friccion sin sentido en el caso normal.
        """
        if self.ubicacion_id is None:
            from .ubicaciones import Ubicacion
            principal = Ubicacion.objects.filter(es_principal=True).first()
            if principal is None:
                principal = Ubicacion.objects.filter(activo=True).first()
            self.ubicacion = principal
        super().save(*args, **kwargs)

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
        """
        Lo que se puede vender ahora: lo que hay menos lo comprometido.

        Devuelve un numero, no un booleano. Como 0 es falso, las plantillas que
        preguntan `{% if item.disponible %}` siguen funcionando igual, y las que
        quieran mostrar cuantas quedan ya tienen el dato.
        """
        return max(self.stock - self.reservado, 0)

    def reservar(self, cantidad):
        """
        Compromete unidades sin sacarlas del almacen.

        No escribe kardex a proposito: reservar no es un movimiento de inventario,
        es una promesa. El par sigue en el estante hasta que el pago se valide, y
        el libro solo registra lo que de verdad entro o salio.
        """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')

        with transaction.atomic():
            actual = Inventario.objects.select_for_update().get(pk=self.pk)
            libres = max(actual.stock - actual.reservado, 0)
            if cantidad > libres:
                raise ValueError(f'Solo quedan {libres} unidades de {self}')

            actual.reservado += cantidad
            actual.save(update_fields=['reservado'])
            self.stock, self.reservado = actual.stock, actual.reservado
        return self

    def liberar(self, cantidad):
        """ Suelta una reserva que vencio o se cancelo. Tampoco toca el kardex. """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')

        with transaction.atomic():
            actual = Inventario.objects.select_for_update().get(pk=self.pk)
            # se recorta en vez de fallar: una reserva de mas no debe impedir
            # que el pedido se cancele y el resto vuelva a la venta
            actual.reservado = max(actual.reservado - cantidad, 0)
            actual.save(update_fields=['reservado'])
            self.stock, self.reservado = actual.stock, actual.reservado
        return self

    def sacar_reservado(self, cantidad, tipo, **contexto):
        """
        Una reserva se vuelve salida real: baja `reservado`, baja `stock` y recien
        aca aparece en el kardex. Sirve para la venta y para el traslado, que son
        las dos formas en que una unidad comprometida deja el almacen.
        """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')

        with transaction.atomic():
            actual = Inventario.objects.select_for_update().get(pk=self.pk)
            if cantidad > actual.stock:
                raise ValueError(f'Solo quedan {actual.stock} unidades de {self}')

            actual.reservado = max(actual.reservado - cantidad, 0)
            actual.save(update_fields=['reservado'])
            self.reservado = actual.reservado

            return self.registrar_movimiento(tipo, -cantidad, **contexto)

    def vender_reservado(self, cantidad, pedido=None, usuario=None):
        """ La reserva se vuelve venta """
        return self.sacar_reservado(
            cantidad, MovimientoInventario.VENTA, pedido=pedido, usuario=usuario
        )

    @property
    def stock_segun_kardex(self):
        """ Lo que dice el historial. Deberia coincidir siempre con `stock`. """
        return self.movimientos.aggregate(total=models.Sum('cantidad'))['total'] or 0

    def registrar_movimiento(self, tipo, cantidad, costo_unitario=None, compra=None,
                             pedido=None, traslado=None, motivo='', usuario=None):
        """
        El unico camino por el que cambia el stock: mueve el saldo, recalcula el
        costo promedio y deja la linea del kardex, todo en la misma transaccion.

        `cantidad` positiva entra, negativa sale. `costo_unitario` es a que costo
        entran o salen esas unidades; si no se indica se usa el costo promedio
        actual, que es lo correcto para una venta.
        """
        if cantidad == 0:
            raise ValueError('Un movimiento de 0 unidades no tiene sentido')

        with transaction.atomic():
            # El saldo sale de la base de datos, no de esta copia en memoria, que
            # pudo quedar vieja si otra parte del codigo ya movio la fila. Ademas
            # el candado impide que dos ventas simultaneas descuenten la misma
            # unidad: la segunda espera a que la primera termine.
            actual = Inventario.objects.select_for_update().get(pk=self.pk)

            stock_anterior = actual.stock
            stock_nuevo = stock_anterior + cantidad
            if stock_nuevo < 0:
                raise ValueError(f'Solo quedan {stock_anterior} unidades de {self}')

            if costo_unitario is None:
                costo_unitario = actual.costo_promedio

            # Promedio ponderado en su forma general: el valor del almacen sube o
            # baja segun el costo de las unidades que se mueven.
            #   compra            -> entra a su costo real y mueve el promedio
            #   venta             -> sale al promedio actual, asi que no lo mueve
            #   anular / cancelar -> deshace exactamente lo que el movimiento aporto
            valor = stock_anterior * actual.costo_promedio + cantidad * costo_unitario
            if stock_nuevo == 0:
                costo_promedio_nuevo = Decimal('0.00')
            else:
                costo_promedio_nuevo = (valor / stock_nuevo).quantize(Decimal('0.01'))

            # anular una compra cuyas unidades ya salieron en parte puede dejar el
            # valor bajo cero: el saldo sigue siendo correcto, el promedio deja de
            # tener sentido. Se corta en 0 en vez de arrastrar un costo negativo.
            costo_promedio_nuevo = max(costo_promedio_nuevo, Decimal('0.00'))

            actual.stock = stock_nuevo
            actual.costo_promedio = costo_promedio_nuevo
            actual.save(update_fields=['stock', 'costo_promedio'])

            # y la copia en memoria queda al dia, para quien siga usandola
            self.stock = stock_nuevo
            self.costo_promedio = costo_promedio_nuevo

            return MovimientoInventario.objects.create(
                item=self,
                tipo=tipo,
                cantidad=cantidad,
                stock_anterior=stock_anterior,
                stock_resultante=stock_nuevo,
                costo_unitario=costo_unitario,
                costo_promedio_resultante=costo_promedio_nuevo,
                compra=compra,
                pedido=pedido,
                traslado=traslado,
                motivo=motivo,
                usuario=usuario,
            )

    def registrar_compra(self, cantidad, costo_unitario, compra=None, usuario=None):
        """
        Entra stock de una boleta de proveedor y recalcula el costo promedio.
        `compra` viaja hasta el kardex: es el documento que respalda las unidades.
        """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')
        return self.registrar_movimiento(
            MovimientoInventario.COMPRA, cantidad, costo_unitario, compra=compra, usuario=usuario
        )

    def descontar_stock(self, cantidad, pedido=None, usuario=None):
        """ Sale stock por una venta. Falla si no hay suficiente. """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')
        return self.registrar_movimiento(
            MovimientoInventario.VENTA, -cantidad, pedido=pedido, usuario=usuario
        )
