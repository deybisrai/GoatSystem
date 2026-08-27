""" Ventas: pedidos del canal online (stock que sale) """

from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from ..avisos import avisar_pago_declarado
from .inventario import Inventario
from .kardex import MovimientoInventario


class Pedido(models.Model):

    SOLICITADO = '0'
    PAGADO = '1'
    ENVIADO = '2'
    ENTREGADO = '3'
    CANCELADO = '4'
    # los codigos 5 y 6 van fuera de orden porque llegaron despues: renumerar
    # obligaria a migrar los pedidos ya registrados. El orden real del ciclo lo
    # definen las TRANSICIONES, no el valor del codigo.
    EN_VALIDACION = '5'
    LISTO_RECOJO = '6'

    ESTADO_CHOICES = (
        (SOLICITADO, 'Solicitado'),
        (EN_VALIDACION, 'En validacion'),
        (PAGADO, 'Pagado'),
        (ENVIADO, 'Enviado'),
        (LISTO_RECOJO, 'Listo para recojo'),
        (ENTREGADO, 'Entregado'),
        (CANCELADO, 'Cancelado'),
    )

    ENVIO = 'E'
    RECOJO = 'R'
    MODO_ENTREGA_CHOICES = (
        (ENVIO, 'Envio a domicilio'),
        (RECOJO, 'Recojo en tienda'),
    )

    # El ciclo se bifurca despues de Pagado: un pedido que se retira no se
    # "envia", queda listo en el mostrador. Solo avanza, y ni entregado ni
    # cancelado se mueven.
    TRANSICIONES = {
        SOLICITADO: (EN_VALIDACION, CANCELADO),
        EN_VALIDACION: (PAGADO, CANCELADO),
        PAGADO: (ENVIADO, CANCELADO),
        ENVIADO: (ENTREGADO, CANCELADO),
        LISTO_RECOJO: (ENTREGADO, CANCELADO),
        ENTREGADO: (),
        CANCELADO: (),
    }

    TRANSICIONES_RECOJO = dict(TRANSICIONES, **{
        PAGADO: (LISTO_RECOJO, CANCELADO),
    })

    # nulo si el pedido fue como invitado
    cliente = models.ForeignKey('web.Cliente', on_delete=models.SET_NULL, null=True, blank=True)

    # datos del comprador: siempre presentes, sea invitado o registrado
    nombre_comprador = models.CharField(max_length=60)
    apellido_comprador = models.CharField(max_length=60)
    email_comprador = models.EmailField()
    telefono_comprador = models.CharField(max_length=20)
    dni_comprador = models.CharField(max_length=8, blank=True)

    nro_pedido = models.CharField(max_length=20, unique=True)
    monto_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    estado = models.CharField(max_length=1, default=SOLICITADO, choices=ESTADO_CHOICES)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    modo_entrega = models.CharField(max_length=1, default=ENVIO, choices=MODO_ENTREGA_CHOICES)

    # vacios si el pedido se retira en un punto
    direccion_envio = models.CharField(max_length=200, blank=True)
    referencia_envio = models.CharField(max_length=200, blank=True)
    distrito_envio = models.CharField(max_length=60, blank=True)
    provincia_envio = models.CharField(max_length=60, blank=True)
    departamento_envio = models.CharField(max_length=60, blank=True)
    telefono_envio = models.CharField(max_length=20)

    # El punto queda referenciado para poder navegar, pero el pedido tambien
    # copia su nombre y direccion: si manana cierra esa agencia, el pedido de
    # ayer tiene que seguir diciendo donde se retiro. Misma regla que el SKU.
    punto_recojo = models.ForeignKey(
        'web.PuntoRecojo', on_delete=models.SET_NULL, null=True, blank=True, related_name='pedidos'
    )
    punto_recojo_nombre = models.CharField(max_length=80, blank=True)
    punto_recojo_direccion = models.CharField(max_length=280, blank=True)

    cupon = models.ForeignKey('web.Cupon', on_delete=models.SET_NULL, null=True, blank=True)
    descuento_aplicado = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    fecha_cancelacion = models.DateTimeField(null=True, blank=True)
    motivo_cancelacion = models.TextField(blank=True)

    # Hasta cuando le guardamos las unidades. Corre corto mientras el cliente va
    # a pagar, y se estira cuando manda su comprobante: a partir de ahi el plazo
    # deja de ser suyo y pasa a ser nuestro para revisarlo.
    reserva_vence = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.nro_pedido

    @property
    def es_invitado(self):
        return self.cliente_id is None

    @property
    def es_recojo(self):
        return self.modo_entrega == self.RECOJO

    @property
    def transiciones(self):
        """ El ciclo cambia despues de Pagado segun como reciba el cliente """
        return self.TRANSICIONES_RECOJO if self.es_recojo else self.TRANSICIONES

    @property
    def donde_recibe(self):
        """ Una linea para mostrar en el pedido, sea envio o recojo """
        if self.es_recojo:
            return f'{self.punto_recojo_nombre} - {self.punto_recojo_direccion}'
        partes = [self.direccion_envio, self.distrito_envio, self.provincia_envio]
        return ', '.join(p for p in partes if p)

    @property
    def descontado(self):
        """ Si las unidades ya salieron del almacen o siguen solo reservadas """
        return self.estado in (self.PAGADO, self.ENVIADO, self.LISTO_RECOJO, self.ENTREGADO)

    @property
    def reserva_vencida(self):
        return (
            self.reserva_vence is not None
            and not self.descontado
            and self.reserva_vence <= timezone.now()
        )

    @property
    def segundos_restantes(self):
        """ Para la cuenta regresiva del checkout. None si no hay reloj corriendo. """
        if self.reserva_vence is None or self.descontado:
            return None
        return max(int((self.reserva_vence - timezone.now()).total_seconds()), 0)

    @property
    def cancelable(self):
        return self.CANCELADO in self.transiciones.get(self.estado, ())

    @property
    def estados_siguientes(self):
        """ A que estados puede pasar hoy, para armar los botones del admin """
        return [
            (codigo, dict(self.ESTADO_CHOICES)[codigo])
            for codigo in self.transiciones.get(self.estado, ())
        ]

    @property
    def pago_pendiente(self):
        """ El comprobante que esta esperando validacion, si hay alguno """
        from .pagos import Pago
        return self.pagos.filter(estado=Pago.PENDIENTE).first()

    @property
    def ultimo_rechazo(self):
        """ Para mostrarle al cliente por que le rebotaron el comprobante """
        from .pagos import Pago
        if self.pagos.filter(estado=Pago.PENDIENTE).exists():
            return None
        return self.pagos.filter(estado=Pago.RECHAZADO).first()

    def declarar_pago(self, cuenta, monto_declarado, nro_operacion, voucher, fecha_pago,
                      base_url=''):
        """
        El cliente dice que ya pago y sube su comprobante. El pedido pasa a
        validacion; nadie da nada por cobrado hasta que una persona lo confirme
        contra la cuenta real.
        """
        from .pagos import Pago

        if self.estado in (self.ENVIADO, self.LISTO_RECOJO, self.ENTREGADO):
            raise ValueError(
                f'El pedido {self.nro_pedido} ya fue despachado: no admite comprobantes nuevos.'
            )
        if self.estado == self.PAGADO:
            raise ValueError(f'El pedido {self.nro_pedido} ya esta pagado.')

        # si la reserva se paso de plazo, se cierra antes de recibir el
        # comprobante: el cliente igual puede declararlo, pero queda marcado
        if self.reserva_vencida:
            self.cancelar('Vencio el plazo para pagar')

        with transaction.atomic():
            pago = Pago.objects.create(
                pedido=self,
                cuenta=cuenta,
                monto_declarado=monto_declarado,
                nro_operacion=nro_operacion.strip(),
                voucher=voucher,
                fecha_pago=fecha_pago,
                # transfirio despues de que se le solto la unidad. No se le
                # cierra la puerta: se marca para que alguien lo mire.
                fuera_de_plazo=(self.estado == self.CANCELADO),
            )
            if self.estado == self.SOLICITADO:
                self.cambiar_estado(self.EN_VALIDACION)
                # el reloj deja de correr contra el cliente: ya hizo su parte.
                # Lo que queda es nuestro plazo para revisarlo.
                self.reserva_vence = timezone.now() + timedelta(hours=settings.HORAS_VALIDACION)
                self.save(update_fields=['reserva_vence'])

            # enterarse es lo que hace la diferencia entre validar en minutos y
            # validar al dia siguiente
            avisar_pago_declarado(pago, base_url=base_url)
        return pago

    def confirmar_venta(self, usuario=None):
        """
        La reserva se vuelve venta. Recien aca las unidades salen del almacen y
        aparecen en el kardex. Lo llama Pago.validar().
        """
        with transaction.atomic():
            detalles = list(self.detalles.order_by('id'))
            items = {
                item.id: item
                for item in Inventario.objects
                .select_for_update()
                .filter(id__in={d.item_id for d in detalles})
            }
            for detalle in detalles:
                items[detalle.item_id].vender_reservado(
                    detalle.cantidad, pedido=self, usuario=usuario
                )
            self.reserva_vence = None
            self.save(update_fields=['reserva_vence'])
        return self

    def revivir_por_pago_tardio(self, usuario=None):
        """
        Unica salida de Cancelado, y a proposito solo por esta puerta: el cliente
        transfirio despues de que se le solto la unidad, y todavia hay stock para
        cumplirle. Si no lo hubiera, reservar falla y corresponde devolverle.
        """
        if self.estado != self.CANCELADO:
            raise ValueError('Este pedido no esta cancelado')

        with transaction.atomic():
            detalles = list(self.detalles.order_by('id'))
            items = {
                item.id: item
                for item in Inventario.objects
                .select_for_update()
                .filter(id__in={d.item_id for d in detalles})
            }
            for detalle in detalles:
                items[detalle.item_id].reservar(detalle.cantidad)

            self.estado = self.PAGADO
            self.fecha_cancelacion = None
            self.motivo_cancelacion = ''
            self.save(update_fields=['estado', 'fecha_cancelacion', 'motivo_cancelacion'])

            for detalle in detalles:
                items[detalle.item_id].vender_reservado(
                    detalle.cantidad, pedido=self, usuario=usuario
                )
            self.reserva_vence = None
            self.save(update_fields=['reserva_vence'])
        return self

    @classmethod
    def reservas_vencidas(cls, items=None):
        """ Los pedidos que se pasaron de plazo y todavia retienen unidades """
        vencidos = cls.objects.filter(
            estado__in=[cls.SOLICITADO, cls.EN_VALIDACION],
            reserva_vence__lt=timezone.now(),
        )
        if items is not None:
            vencidos = vencidos.filter(detalles__item__in=items).distinct()
        return vencidos

    @classmethod
    def vencer_reservas(cls, items=None):
        """
        Cancela los pedidos que se pasaron de plazo y suelta sus unidades.

        `items` acota la busqueda a un puñado de filas de inventario, para que el
        checkout pueda soltar lo vencido justo antes de mirar si hay stock. Asi
        el sistema se corrige solo aunque nadie haya programado el comando.
        """
        cuantos = 0
        for pedido in cls.reservas_vencidas(items):
            pedido.cancelar('Vencio el plazo para pagar')
            cuantos += 1
        return cuantos

    def cambiar_estado(self, nuevo, usuario=None, motivo=''):
        """ Mueve el pedido por su ciclo. Cancelar pasa por `cancelar()`. """
        etiquetas = dict(self.ESTADO_CHOICES)
        if nuevo == self.estado:
            raise ValueError(f'El pedido {self.nro_pedido} ya esta {etiquetas[self.estado].lower()}')
        if nuevo not in self.transiciones.get(self.estado, ()):
            raise ValueError(
                f'Un pedido {etiquetas[self.estado].lower()} no puede pasar a '
                f'{etiquetas.get(nuevo, nuevo).lower()}'
            )
        if nuevo == self.CANCELADO:
            return self.cancelar(motivo, usuario=usuario)

        with transaction.atomic():
            self.estado = nuevo
            self.save(update_fields=['estado'])
            if nuevo == self.PAGADO:
                # unico lugar donde la reserva se vuelve venta, venga por donde
                # venga: asi no se puede llegar a Pagado con las unidades todavia
                # reservadas y sin registrar en el kardex
                self.confirmar_venta(usuario=usuario)
        return self

    def cancelar(self, motivo, usuario=None):
        """
        Devuelve al almacen todo lo que este pedido desconto y libera el uso del
        cupon.

        El pedido no se borra: queda cancelado, con su motivo y con sus lineas
        intactas, porque sigue siendo la fotografia de lo que se vendio ese dia.
        """
        if self.estado == self.CANCELADO:
            raise ValueError('Este pedido ya esta cancelado')
        if self.estado == self.ENTREGADO:
            raise ValueError(
                'Un pedido entregado ya no se cancela: lo que corresponde es registrar '
                'una devolucion cuando la mercaderia vuelva.'
            )
        motivo = (motivo or '').strip()
        if not motivo:
            raise ValueError('Cancelar un pedido exige un motivo')

        # si todavia no se pago, las unidades nunca salieron del almacen: basta
        # con soltar la reserva y el kardex no se entera. Solo un pedido ya
        # descontado necesita un movimiento que devuelva el stock.
        devolver_stock = self.descontado

        with transaction.atomic():
            detalles = list(self.detalles.order_by('id'))
            items = {
                item.id: item
                for item in Inventario.objects
                .select_for_update()
                .filter(id__in={d.item_id for d in detalles})
            }

            # a que costo salio cada unidad, para que vuelva al mismo costo y el
            # promedio del almacen quede como estaba antes de la venta. Los
            # pedidos anteriores al kardex no tienen ese dato: vuelven al
            # promedio actual, que es lo mas cercano disponible.
            costos = dict(
                self.movimientos
                .filter(tipo=MovimientoInventario.VENTA)
                .values_list('item_id', 'costo_unitario')
            )

            for detalle in detalles:
                if devolver_stock:
                    items[detalle.item_id].registrar_movimiento(
                        MovimientoInventario.CANCELA_VENTA,
                        detalle.cantidad,
                        costo_unitario=costos.get(detalle.item_id),
                        pedido=self,
                        motivo=motivo,
                        usuario=usuario,
                    )
                else:
                    items[detalle.item_id].liberar(detalle.cantidad)

            # el cupon vuelve a estar disponible: esta venta no ocurrio
            if self.cupon_id and self.cupon.veces_usado > 0:
                self.cupon.veces_usado -= 1
                self.cupon.save(update_fields=['veces_usado'])

            self.estado = self.CANCELADO
            self.fecha_cancelacion = timezone.now()
            self.motivo_cancelacion = motivo
            self.reserva_vence = None
            self.save(update_fields=[
                'estado', 'fecha_cancelacion', 'motivo_cancelacion', 'reserva_vence',
            ])

        return self


class PedidoDetalle(models.Model):
    """
    Guarda una copia (snapshot) del producto al momento de la venta: sku, nombre,
    valor (talla, capacidad...) y precio no dependen del catalogo actual.
    """
    pedido = models.ForeignKey(Pedido, on_delete=models.RESTRICT, related_name='detalles')
    item = models.ForeignKey('web.Inventario', on_delete=models.RESTRICT)
    sku = models.CharField(max_length=30)
    nombre_producto = models.CharField(max_length=150)
    valor = models.CharField(max_length=30, blank=True)
    precio_unitario = models.DecimalField(max_digits=9, decimal_places=2)
    cantidad = models.PositiveIntegerField(default=1)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f'{self.nombre_producto} ({self.sku}) x{self.cantidad}'
