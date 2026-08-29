""" Ventas: pedidos del canal online (stock que sale) """

from datetime import timedelta
from math import ceil
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from ..avisos import avisar_pago_declarado
from .inventario import Inventario
from .kardex import MovimientoInventario


def codigo_de_reserva():
    """
    Identificador de una reserva, no de una venta.

    Nace con el pedido y no es correlativo a proposito: un carrito abandonado no
    tiene por que gastarse un numero de la serie. Se lo puede leer por telefono.
    """
    return f'R-{uuid4().hex[:8].upper()}'


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
    # Se le acabo el tiempo, que no es lo mismo que lo cancelaron. Un expirado
    # es un cliente que no llego; un cancelado es una decision de alguien. Sin
    # separarlos no se puede medir cuantas reservas se caen solas, que es el
    # unico numero que dice si el plazo esta bien puesto.
    EXPIRADO = '7'

    ESTADO_CHOICES = (
        (SOLICITADO, 'Solicitado'),
        (EN_VALIDACION, 'En validacion'),
        (PAGADO, 'Pagado'),
        (ENVIADO, 'Enviado'),
        (LISTO_RECOJO, 'Listo para recojo'),
        (ENTREGADO, 'Entregado'),
        (EXPIRADO, 'Expirado'),
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
        # solo una reserva expira: con el comprobante arriba ya no corre reloj
        SOLICITADO: (EN_VALIDACION, EXPIRADO, CANCELADO),
        EN_VALIDACION: (PAGADO, CANCELADO),
        PAGADO: (ENVIADO, CANCELADO),
        ENVIADO: (ENTREGADO, CANCELADO),
        LISTO_RECOJO: (ENTREGADO, CANCELADO),
        ENTREGADO: (),
        EXPIRADO: (),
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

    # dos identificadores, y la diferencia importa: el codigo de reserva nace
    # con el pedido y solo dice "estas unidades son de esta persona"; el numero
    # de pedido nace con el comprobante y numera una venta de verdad. Un
    # checkout abandonado se lleva un codigo, nunca un numero.
    codigo_reserva = models.CharField(
        max_length=20, unique=True, default=codigo_de_reserva, editable=False,
        help_text='Identifica la reserva desde que se confirma el pedido.',
    )
    nro_pedido = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        help_text='Correlativo de la venta. Se emite al recibir el comprobante.',
    )
    # El identificador del CLIC, no del pedido ni del producto. El bloqueo de
    # filas impide que dos clientes se lleven la misma unidad; esto impide que
    # un cliente se lleve dos pedidos por apretar dos veces. Son problemas
    # distintos. La garantia la da el unique de la base y no una consulta
    # previa: entre consultar y crear hay una rendija, que es justo el caso que
    # queremos cerrar.
    token_checkout = models.CharField(
        max_length=32, unique=True, null=True, blank=True, editable=False,
        help_text='Identifica el envio del formulario, para no duplicar el pedido.',
    )
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

    # Los cuatro pasos que ve el cliente, al estilo de cualquier tienda. La
    # reserva no aparece: todavia no es un pedido, y mostrarla como "paso 0"
    # le prometeria un avance que no compro.
    SEGUIMIENTO = {
        EN_VALIDACION: 1,
        PAGADO: 2,
        ENVIADO: 3,
        LISTO_RECOJO: 3,
        ENTREGADO: 4,
    }

    @property
    def pasos_seguimiento(self):
        """
        El semaforo del pedido: cuatro pasos, con cual esta hecho y cual es el
        actual. Vacio mientras sea una reserva o si se cancelo, porque ahi no
        hay avance que mostrar.
        """
        actual = self.SEGUIMIENTO.get(self.estado)
        if actual is None:
            return []

        tercero = 'Listo para recoger' if self.es_recojo else 'Enviado'
        nombres = ['Recibido', 'Confirmado', tercero, 'Entregado']
        return [
            {'nombre': nombre, 'hecho': numero < actual, 'actual': numero == actual}
            for numero, nombre in enumerate(nombres, start=1)
        ]

    @property
    def referencia(self):
        """ Como se lo nombra en pantalla: su numero si ya lo tiene, si no su codigo """
        return self.nro_pedido or self.codigo_reserva

    def emitir_nro_pedido(self):
        """
        Le pone numero de venta al pedido, si todavia no lo tiene.

        El correlativo es por dia y sin huecos. Se calcula sobre el mayor ya
        emitido de esa fecha y no sobre la cantidad, para no chocar con los que
        se emitieron cuando el numero salia del id.
        """
        if self.nro_pedido:
            return self.nro_pedido

        prefijo = f'P{timezone.localtime(self.fecha_registro):%Y%m%d}-'
        for _ in range(5):
            mayor = 0
            emitidos = (
                Pedido.objects
                .filter(nro_pedido__startswith=prefijo)
                .values_list('nro_pedido', flat=True)
            )
            for nro in emitidos:
                try:
                    mayor = max(mayor, int(nro[len(prefijo):]))
                except (TypeError, ValueError):
                    continue
            try:
                with transaction.atomic():
                    self.nro_pedido = f'{prefijo}{mayor + 1:05d}'
                    self.save(update_fields=['nro_pedido'])
                return self.nro_pedido
            except IntegrityError:
                # otro pedido se llevo ese numero entre el calculo y el guardado
                self.nro_pedido = None
        raise IntegrityError('No se pudo emitir un numero de pedido para ' + self.codigo_reserva)

    def __str__(self):
        return self.referencia

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

    TERMINADOS = (EXPIRADO, CANCELADO)

    @property
    def cerrado_sin_venta(self):
        """ Termino sin vender: se le acabo el tiempo o alguien lo cancelo """
        return self.estado in self.TERMINADOS

    @property
    def reserva_vencida(self):
        return (
            self.reserva_vence is not None
            and not self.descontado
            and self.reserva_vence <= timezone.now()
        )

    @property
    def segundos_restantes(self):
        """
        Para la cuenta regresiva del checkout. None si no hay reloj corriendo.

        Redondea hacia arriba a proposito. Truncando, el navegador arrancaba
        siempre por debajo del tiempo real y su cuenta llegaba a cero mientras
        el servidor todavia daba la reserva por viva: recargaba, el servidor no
        lo mandaba a ningun lado, y la pantalla quedaba quieta con el producto
        retenido. Sobrar un segundo no le cuesta nada a nadie.
        """
        if self.reserva_vence is None or self.descontado:
            return None
        return max(ceil((self.reserva_vence - timezone.now()).total_seconds()), 0)

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
                f'El pedido {self.referencia} ya fue despachado: no admite comprobantes nuevos.'
            )
        if self.estado == self.PAGADO:
            raise ValueError(f'El pedido {self.referencia} ya esta pagado.')

        # un comprobante solo existe sobre un pedido vivo. Si el pedido murio
        # -- se vencio o lo cerramos -- el cliente rehace la compra y sube el
        # mismo voucher sobre el pedido nuevo. Aceptarlo aca creaba un pago que
        # despues nadie podia procesar.
        if self.reserva_vencida:
            self.expirar()
        if self.cerrado_sin_venta:
            raise ValueError(
                f'El pedido {self.referencia} esta '
                f'{self.get_estado_display().lower()} y ya solto sus unidades. '
                'No admite comprobantes: hay que rehacer la compra.'
            )

        with transaction.atomic():
            # el numero de venta nace con el comprobante, no con el checkout.
            # Tambien para un pedido ya cancelado que paga tarde: mando su
            # comprobante, entra a la serie.
            self.emitir_nro_pedido()

            pago = Pago.objects.create(
                pedido=self,
                cuenta=cuenta,
                monto_declarado=monto_declarado,
                nro_operacion=nro_operacion.strip(),
                voucher=voucher,
                fecha_pago=fecha_pago,
            )
            if self.estado == self.SOLICITADO:
                self.cambiar_estado(self.EN_VALIDACION)
                # el reloj se apaga, no se reprograma. Con el comprobante arriba
                # la unidad queda congelada hasta que una persona decida: que
                # nosotros tardemos en validar no puede costarle el producto a
                # quien ya transfirio. Lo que corre a partir de aca es la alarma
                # interna de la bandeja (HORAS_ALERTA_VALIDACION), que nos avisa
                # a nosotros y no le saca nada a nadie.
                self.reserva_vence = None
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

    @classmethod
    def reservas_vencidas(cls, items=None):
        """ Los pedidos que se pasaron de plazo y todavia retienen unidades """
        # solo SOLICITADO: un pedido en validacion ya tiene comprobante y su
        # unidad no se suelta por tiempo, se suelta cuando alguien lo rechaza
        vencidos = cls.objects.filter(
            estado=cls.SOLICITADO,
            reserva_vence__lt=timezone.now(),
        )
        if items is not None:
            vencidos = vencidos.filter(detalles__item__in=items).distinct()
        return vencidos

    @classmethod
    def unidades_vencidas_por_item(cls, items):
        """
        Cuantas unidades de cada item retiene un pedido que ya se paso del plazo.

        `Inventario.reservado` es un contador que solo baja cuando alguien cancela
        el pedido. Hasta que eso pase, una reserva muerta sigue restando del
        disponible. Esto dice cuanto de ese contador ya no vale, para descontarlo
        al leer sin esperar a que nadie la venga a soltar.

        Devuelve {item_id: unidades}, en una sola consulta.
        """
        items = [i for i in items if getattr(i, 'pk', i) is not None]
        if not items:
            return {}
        filas = (
            PedidoDetalle.objects
            .filter(
                item__in=items,
                pedido__estado=cls.SOLICITADO,
                pedido__reserva_vence__lt=timezone.now(),
            )
            .values('item_id')
            .annotate(total=models.Sum('cantidad'))
        )
        return {fila['item_id']: fila['total'] for fila in filas}

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
            pedido.expirar()
            cuantos += 1
        return cuantos

    def _por_que_no_puede(self, nuevo, etiquetas):
        """
        Explica por que no se puede mover, distinguiendo las dos razones.

        Un pedido pagado con envio no puede pasar a "listo para recojo", pero no
        porque este pagado: desde pagado avanza perfecto, solo que a "enviado".
        Lo que no encaja es el modo de entrega. Decir "un pedido pagado no puede"
        manda a mirar el estado, que es justo lo unico que esta bien.
        """
        destino = etiquetas.get(nuevo, nuevo).lower()

        # si el destino si existiria con el otro modo de entrega, el obstaculo
        # es el modo y no el estado
        otro_ciclo = self.TRANSICIONES if self.es_recojo else self.TRANSICIONES_RECOJO
        if nuevo in otro_ciclo.get(self.estado, ()):
            modo = self.get_modo_entrega_display().lower()
            siguientes = [
                etiquetas[codigo]
                for codigo in self.transiciones.get(self.estado, ())
                if codigo != self.CANCELADO
            ]
            arreglo = f' Lo que sigue para el es {siguientes[0].lower()}.' if siguientes else ''
            return f'Un pedido con {modo} no puede pasar a {destino}.{arreglo}'

        return (
            f'Un pedido {etiquetas[self.estado].lower()} no puede pasar a {destino}'
        )

    def cambiar_estado(self, nuevo, usuario=None, motivo=''):
        """ Mueve el pedido por su ciclo. Cancelar pasa por `cancelar()`. """
        etiquetas = dict(self.ESTADO_CHOICES)
        if nuevo == self.estado:
            raise ValueError(f'El pedido {self.referencia} ya esta {etiquetas[self.estado].lower()}')
        if nuevo not in self.transiciones.get(self.estado, ()):
            raise ValueError(self._por_que_no_puede(nuevo, etiquetas))
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

    def cancelar(self, motivo, usuario=None, estado_final=None):
        """
        Devuelve al almacen todo lo que este pedido desconto y libera el uso del
        cupon.

        El pedido no se borra: queda cancelado, con su motivo y con sus lineas
        intactas, porque sigue siendo la fotografia de lo que se vendio ese dia.
        """
        if self.estado in self.TERMINADOS:
            raise ValueError(
                f'Este pedido ya esta {dict(self.ESTADO_CHOICES)[self.estado].lower()}'
            )
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

            self.estado = estado_final or self.CANCELADO
            self.fecha_cancelacion = timezone.now()
            self.motivo_cancelacion = motivo
            self.reserva_vence = None
            self.save(update_fields=[
                'estado', 'fecha_cancelacion', 'motivo_cancelacion', 'reserva_vence',
            ])

        return self

    def expirar(self, usuario=None):
        """
        Se le acabo el plazo sin comprobante: suelta las unidades y se cierra.

        Hace lo mismo que cancelar -- devuelve el stock, libera el cupon,
        conserva el detalle -- pero aterriza en EXPIRADO. La diferencia no es
        cosmetica: un expirado es un cliente que no llego, y eso se cuenta
        aparte de los que cancelamos nosotros.
        """
        return self.cancelar(
            'Vencio el plazo para pagar', usuario=usuario, estado_final=self.EXPIRADO
        )


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
