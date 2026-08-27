"""
Traslados entre ciudades: el viaje, no la mercaderia.

El documento planifica **el transporte**, no lo que va adentro, y nace vacio. Eso
es a proposito: reservar mercaderia el lunes para un viaje del sabado la bloquea
cinco dias esperando a un cliente de Lircay que todavia no existe, mientras se
pierden ventas seguras en Huancavelica. Reservar contra una prediccion es el
error clasico; aca solo se reserva contra demanda real.

Las lineas se suman despues:
  por pedido  las agrega la venta. Ya vienen reservadas por su pedido.
  por stock   las agregas vos, idealmente el viernes: horas de bloqueo, no dias.

Si no hay transporte programado a una ciudad, sus productos no se ofrecen ahi.
No se promete una fecha que no se tiene como cumplir.

Sale de un almacen y entra en otro, y esos dos momentos son movimientos reales
que el kardex tiene que explicar.

Se confirma en dos pasos, igual que la remesa de caja a boveda: quien envia
declara lo que manda, quien recibe cuenta y confirma. Si los numeros no
coinciden, ya se sabe entre que dos personas se perdio.

Cada linea dice por que esta ahi. Lo que un cliente ya compro es una obligacion;
lo que se manda para tener stock es estrategia. Van en el mismo viaje pero no son
lo mismo, y si el sabado no entra todo hay que saber que no se puede dejar.
"""

from django.contrib.auth.models import User
from django.db import models, transaction
from django.utils import timezone

from .inventario import Inventario
from .kardex import MovimientoInventario


class Traslado(models.Model):
    """ Lo que viaja de una ciudad a otra en un despacho """

    PLANIFICADO = 'P'
    EN_TRANSITO = 'T'
    RECIBIDO = 'R'
    ANULADO = 'A'

    ESTADO_CHOICES = (
        (PLANIFICADO, 'Planificado'),
        (EN_TRANSITO, 'En transito'),
        (RECIBIDO, 'Recibido'),
        (ANULADO, 'Anulado'),
    )

    origen = models.ForeignKey(
        'web.Ubicacion', on_delete=models.RESTRICT, related_name='traslados_enviados'
    )
    destino = models.ForeignKey(
        'web.Ubicacion', on_delete=models.RESTRICT, related_name='traslados_recibidos'
    )
    fecha_despacho = models.DateField(help_text='Cuando sale la mercaderia.')
    fecha_disponible = models.DateField(
        help_text='Cuando queda disponible en destino. Es la fecha que ve el cliente.'
    )

    estado = models.CharField(max_length=1, default=PLANIFICADO, choices=ESTADO_CHOICES)
    despachado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='traslados_despachados'
    )
    fecha_salida = models.DateTimeField(null=True, blank=True)
    recibido_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='traslados_recibidos'
    )
    fecha_recepcion = models.DateTimeField(null=True, blank=True)

    observacion = models.TextField(blank=True)
    motivo_anulacion = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Traslado'
        verbose_name_plural = 'Traslados'
        ordering = ['-fecha_despacho', '-id']

    def __str__(self):
        return f'T-{self.id:05d} {self.origen} a {self.destino}'

    def save(self, *args, **kwargs):
        """
        Las fechas salen del calendario de la ciudad destino si no se dan.

        Programar un viaje deberia ser elegir a donde y guardar: el dia de salida
        y el de llegada ya estan declarados en la ubicacion, y tipearlos a mano
        cada semana es una fuente de errores sin ninguna ganancia.
        """
        if self.destino_id:
            if not self.fecha_despacho:
                self.fecha_despacho = self.destino.proximo_despacho()
            if not self.fecha_disponible:
                self.fecha_disponible = self.destino.llegada_de(self.fecha_despacho)
        super().save(*args, **kwargs)

    @property
    def editable(self):
        """ Mientras no salio se puede agregar y quitar lo que sea """
        return self.estado == self.PLANIFICADO

    @property
    def unidades(self):
        return sum(d.cantidad for d in self.detalles.all())

    def verificar_editable(self):
        if not self.editable:
            raise ValueError(
                f'{self} ya {self.get_estado_display().lower()}: sus lineas no se tocan.'
            )

    def _items_bloqueados(self, detalles):
        return {
            item.id: item
            for item in Inventario.objects
            .select_for_update()
            .filter(id__in={d.item_id for d in detalles})
        }

    def agregar(self, item, cantidad, motivo=None, pedido=None):
        """
        Suma una linea, creando la fila de inventario del destino si hace falta.

        La primera vez que un SKU viaja a Lircay no existe alla: la fila nace en
        cero y el traslado la llena. Sin esto habria que crearla a mano antes de
        cada envio nuevo.
        """
        self.verificar_editable()
        if item.ubicacion_id != self.origen_id:
            raise ValueError(f'{item} no esta en {self.origen}')

        destino, _ = Inventario.objects.get_or_create(
            variante_id=item.variante_id,
            valor_id=item.valor_id,
            ubicacion=self.destino,
        )
        return TrasladoDetalle.objects.create(
            traslado=self,
            item=item,
            item_destino=destino,
            cantidad=cantidad,
            costo_unitario=item.costo_promedio,
            motivo=motivo or (TrasladoDetalle.POR_PEDIDO if pedido else TrasladoDetalle.POR_STOCK),
            pedido=pedido,
        )

    @staticmethod
    def pedidos_esperando(destino=None):
        """
        Lo que ya se vendio y todavia tiene que viajar.

        Son pedidos pagados que se retiran en una ciudad donde su mercaderia no
        esta, y que ningun viaje esta llevando. Es la lista que evita que un
        pedido de Lircay se quede en Huancavelica sin que nadie lo note.
        """
        from .ventas import Pedido

        pendientes = []
        pedidos = (
            Pedido.objects
            .filter(modo_entrega=Pedido.RECOJO, estado=Pedido.PAGADO)
            .select_related('punto_recojo__ubicacion')
            .prefetch_related('detalles__item')
        )
        for pedido in pedidos:
            ciudad = getattr(pedido.punto_recojo, 'ubicacion', None)
            if ciudad is None or (destino is not None and ciudad != destino):
                continue
            for detalle in pedido.detalles.all():
                if detalle.item.ubicacion_id == ciudad.id:
                    continue                      # ya esta donde lo van a retirar
                if TrasladoDetalle.objects.filter(
                    pedido=pedido, item=detalle.item,
                    traslado__estado__in=[Traslado.PLANIFICADO, Traslado.EN_TRANSITO],
                ).exists():
                    continue                      # ya esta arriba de un viaje
                pendientes.append((pedido, detalle, ciudad))
        return pendientes

    def despachar(self, usuario=None):
        """
        La mercaderia sale. Deja de estar reservada en origen y sale del stock:
        el kardex escribe la salida y las unidades quedan viajando.
        """
        if self.estado != self.PLANIFICADO:
            raise ValueError(f'{self} ya no esta planificado')
        if not self.detalles.exists():
            raise ValueError('Un traslado vacio no sale a ningun lado')

        with transaction.atomic():
            detalles = list(self.detalles.order_by('id'))
            items = self._items_bloqueados(detalles)
            for detalle in detalles:
                # las lineas por pedido ya salieron del inventario cuando se
                # valido el pago: lo que viaja ahi es mercaderia del cliente, no
                # stock nuestro, y el kardex ya la registro como venta
                if detalle.motivo != TrasladoDetalle.POR_STOCK:
                    continue
                items[detalle.item_id].sacar_reservado(
                    detalle.cantidad,
                    MovimientoInventario.TRASLADO_SALIDA,
                    traslado=self,
                    usuario=usuario,
                )

            self.estado = self.EN_TRANSITO
            self.despachado_por = usuario
            self.fecha_salida = timezone.now()
            self.save(update_fields=['estado', 'despachado_por', 'fecha_salida'])
        return self

    def recibir(self, usuario=None, conteo=None):
        """
        Destino cuenta lo que llego y lo confirma.

        `conteo` es un dict {detalle_id: cantidad} con lo que realmente entro. Si
        no se pasa, se asume que llego todo. Confirmar contando y no aceptando es
        el mismo criterio con el que la boveda recibe una remesa: si los numeros
        no coinciden, la diferencia queda escrita.
        """
        if self.estado != self.EN_TRANSITO:
            raise ValueError(f'{self} no esta en transito')

        with transaction.atomic():
            detalles = list(self.detalles.order_by('id'))
            items = {
                item.id: item
                for item in Inventario.objects
                .select_for_update()
                .filter(id__in={d.item_destino_id for d in detalles})
            }
            for detalle in detalles:
                recibida = detalle.cantidad if conteo is None else conteo.get(detalle.id, 0)
                detalle.cantidad_recibida = recibida
                detalle.save(update_fields=['cantidad_recibida'])
                if recibida and detalle.motivo == TrasladoDetalle.POR_STOCK:
                    items[detalle.item_destino_id].registrar_movimiento(
                        MovimientoInventario.TRASLADO_ENTRADA,
                        recibida,
                        costo_unitario=detalle.costo_unitario,
                        traslado=self,
                        usuario=usuario,
                    )

            self.estado = self.RECIBIDO
            self.recibido_por = usuario
            self.fecha_recepcion = timezone.now()
            self.save(update_fields=['estado', 'recibido_por', 'fecha_recepcion'])
        return self

    def anular(self, motivo, usuario=None):
        """ Se cancela antes de salir: las unidades vuelven a estar a la venta en origen """
        if self.estado != self.PLANIFICADO:
            raise ValueError('Solo se anula un traslado que todavia no salio')
        motivo = (motivo or '').strip()
        if not motivo:
            raise ValueError('Anular un traslado exige un motivo')

        with transaction.atomic():
            detalles = list(self.detalles.order_by('id'))
            items = self._items_bloqueados(detalles)
            for detalle in detalles:
                # una linea por pedido tiene su reserva a nombre del pedido: se
                # suelta cancelando el pedido, no anulando el viaje
                if detalle.motivo == TrasladoDetalle.POR_STOCK:
                    items[detalle.item_id].liberar(detalle.cantidad)

            self.estado = self.ANULADO
            self.motivo_anulacion = motivo
            self.save(update_fields=['estado', 'motivo_anulacion'])
        return self


class TrasladoDetalle(models.Model):
    """
    Una linea del traslado, con el motivo por el que esta ahi.

    `POR_PEDIDO` es una obligacion: un cliente ya lo compro y tiene una fecha
    prometida. `POR_STOCK` es reposicion. Si el sabado no entra todo en la
    camioneta, lo primero no se puede dejar.
    """

    POR_PEDIDO = 'P'
    POR_STOCK = 'S'
    MOTIVO_CHOICES = (
        (POR_PEDIDO, 'Por pedido'),
        (POR_STOCK, 'Por stock'),
    )

    traslado = models.ForeignKey(Traslado, on_delete=models.RESTRICT, related_name='detalles')

    # la fila de inventario de la que sale y la que la recibe: mismo SKU, ciudad distinta
    item = models.ForeignKey(
        'web.Inventario', on_delete=models.RESTRICT, related_name='traslados_salida'
    )
    item_destino = models.ForeignKey(
        'web.Inventario', on_delete=models.RESTRICT, related_name='traslados_entrada'
    )

    cantidad = models.PositiveIntegerField()
    cantidad_recibida = models.PositiveIntegerField(null=True, blank=True)
    costo_unitario = models.DecimalField(max_digits=9, decimal_places=2, default=0)

    motivo = models.CharField(max_length=1, default=POR_STOCK, choices=MOTIVO_CHOICES)
    pedido = models.ForeignKey(
        'web.Pedido', on_delete=models.SET_NULL, null=True, blank=True, related_name='traslados'
    )

    class Meta:
        ordering = ['motivo', 'id']

    def __str__(self):
        return f'{self.item} x{self.cantidad}'

    def save(self, *args, **kwargs):
        """
        Una linea por stock compromete su unidad recien al agregarse.

        El documento nace vacio y no reserva nada: solo se bloquea lo que de
        verdad va a viajar, y lo mas tarde posible. Las lineas por pedido no
        reservan nada porque su pedido ya lo hizo.
        """
        creando = self._state.adding

        # anotar lo que llego no es editar lo que viaja: la recepcion tiene que
        # poder escribir sobre un traslado en transito, que es justo cuando el
        # documento esta cerrado para todo lo demas
        campos = set(kwargs.get('update_fields') or [])
        solo_recepcion = bool(campos) and campos <= {'cantidad_recibida'}
        if not solo_recepcion:
            self.traslado.verificar_editable()

        super().save(*args, **kwargs)
        if creando and self.motivo == self.POR_STOCK:
            self.item.reservar(self.cantidad)

    def delete(self, *args, **kwargs):
        self.traslado.verificar_editable()
        if self.motivo == self.POR_STOCK:
            self.item.liberar(self.cantidad)
        return super().delete(*args, **kwargs)

    @property
    def faltante(self):
        """ Lo que se declaro menos lo que llego. Positivo si falto. """
        if self.cantidad_recibida is None:
            return None
        return self.cantidad - self.cantidad_recibida
