"""
Cobro por transferencia: el cliente paga a nuestras cuentas y declara su pago.

No hay pasarela. El cliente deposita, sube su comprobante y alguien lo valida
mirando la cuenta real. Ese "mirando la cuenta real" es la regla central del
modulo: el voucher es evidencia, no prueba.
"""

import os
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import models, transaction
from django.utils import timezone

from ..avisos import avisar_comprobante_rechazado


class AlmacenPrivado(FileSystemStorage):
    """
    Archivos que no tienen URL publica.

    Un comprobante guardado bajo MEDIA_ROOT queda accesible en /media/... para
    cualquiera que adivine el nombre: ni Django en desarrollo ni Nginx en
    produccion preguntan quien es antes de entregarlo. La vista con permisos no
    alcanza si el archivo tambien se puede pedir por su ruta.

    Las rutas se leen del setting en cada acceso (no en __init__) para que
    override_settings funcione en los tests.
    """

    @property
    def base_location(self):
        return settings.PRIVADO_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError(
            'Un comprobante no tiene URL publica. Se entrega por la vista web:voucherPago.'
        )


class CuentaRecaudadora(models.Model):
    """
    Donde el cliente deposita. Es lo unico de este modulo que ve antes de pagar,
    asi que los datos tienen que estar completos y ser copiables de un toque.
    """

    BANCO = 'BANCO'
    YAPE = 'YAPE'
    PLIN = 'PLIN'
    QR = 'QR'

    METODO_CHOICES = (
        (BANCO, 'Transferencia o deposito'),
        (YAPE, 'Yape'),
        (PLIN, 'Plin'),
        (QR, 'Codigo QR'),
    )

    TIPO_CUENTA_CHOICES = (
        ('AHORROS', 'Ahorros'),
        ('CORRIENTE', 'Corriente'),
    )

    MONEDA_CHOICES = (
        ('PEN', 'Soles'),
        ('USD', 'Dolares'),
    )

    metodo = models.CharField(max_length=10, choices=METODO_CHOICES)
    titular = models.CharField(max_length=120, help_text='A nombre de quien esta la cuenta.')
    moneda = models.CharField(max_length=3, default='PEN', choices=MONEDA_CHOICES)

    # solo para BANCO
    banco = models.CharField(max_length=40, blank=True, help_text='BCP, Interbank, BBVA...')
    tipo_cuenta = models.CharField(max_length=10, blank=True, choices=TIPO_CUENTA_CHOICES)
    numero = models.CharField(max_length=25, blank=True)
    cci = models.CharField(
        max_length=20, blank=True,
        help_text='Codigo interbancario, para que le transfieran desde otro banco.'
    )

    # solo para YAPE y PLIN
    telefono = models.CharField(max_length=15, blank=True)

    # el QR lo necesita; Yape y Plin tambien tienen el suyo y se puede cargar aca
    imagen_qr = models.ImageField(upload_to='cuentas', blank=True)

    instrucciones = models.CharField(
        max_length=200, blank=True,
        help_text='Una linea extra que se muestra al cliente. Opcional.'
    )
    activo = models.BooleanField(default=True)
    orden = models.PositiveSmallIntegerField(default=0, help_text='Menor primero en el checkout.')

    class Meta:
        verbose_name = 'Cuenta de recaudacion'
        verbose_name_plural = 'Cuentas de recaudacion'
        ordering = ['orden', 'id']

    def __str__(self):
        if self.metodo == self.BANCO:
            return f'{self.banco} {self.get_tipo_cuenta_display()} {self.numero}'.strip()
        if self.metodo in (self.YAPE, self.PLIN):
            return f'{self.get_metodo_display()} {self.telefono}'
        return f'QR {self.titular}'

    @property
    def etiqueta(self):
        """ Como se nombra el metodo en el checkout, corto """
        if self.metodo == self.BANCO:
            return f'{self.banco} · {self.get_tipo_cuenta_display()}'
        return self.get_metodo_display()

    @property
    def es_bancaria(self):
        return self.metodo == self.BANCO

    @property
    def usa_telefono(self):
        return self.metodo in (self.YAPE, self.PLIN)

    def clean(self):
        """
        Cada metodo pide sus propios datos. Mismo criterio que Inventario.clean():
        el modelo es uno solo y la coherencia se valida aca, no con cuatro tablas.
        """
        if self.metodo == self.BANCO:
            faltan = [
                etiqueta for campo, etiqueta in
                (('banco', 'banco'), ('tipo_cuenta', 'tipo de cuenta'), ('numero', 'numero de cuenta'))
                if not getattr(self, campo)
            ]
            if faltan:
                raise ValidationError(
                    f'Una cuenta bancaria necesita {", ".join(faltan)}.'
                )
        elif self.usa_telefono:
            if not self.telefono:
                raise ValidationError({'telefono': f'{self.get_metodo_display()} necesita un numero.'})
        elif self.metodo == self.QR and not self.imagen_qr:
            raise ValidationError({'imagen_qr': 'Un metodo QR necesita la imagen del codigo.'})


class Pago(models.Model):
    """
    Lo que el cliente DECLARA haber pagado. Sigue siendo una declaracion hasta
    que alguien la valida contra la cuenta real.

    Un pedido puede tener varios: si se rechaza un comprobante, el cliente sube
    otro y quedan los dos, con su motivo. El documento no se corrige, se repite.
    """

    PENDIENTE = 'P'
    VALIDADO = 'V'
    RECHAZADO = 'R'

    ESTADO_CHOICES = (
        (PENDIENTE, 'Pendiente'),
        (VALIDADO, 'Validado'),
        (RECHAZADO, 'Rechazado'),
    )

    pedido = models.ForeignKey('web.Pedido', on_delete=models.RESTRICT, related_name='pagos')
    cuenta = models.ForeignKey(CuentaRecaudadora, on_delete=models.RESTRICT, related_name='pagos')

    monto_declarado = models.DecimalField(max_digits=10, decimal_places=2)
    nro_operacion = models.CharField(
        max_length=40, help_text='El numero que genera el banco o Yape al transferir.'
    )
    voucher = models.ImageField(upload_to='vouchers/%Y/%m', storage=AlmacenPrivado())
    fecha_pago = models.DateField(help_text='La fecha que figura en el comprobante.')

    # transfirio despues de que se le solto la unidad. Se acepta igual, para que
    # el cliente no quede con la plata movida y sin forma de avisar, pero llega
    # marcado: puede que ya no haya stock y corresponda devolverle.

    estado = models.CharField(max_length=1, default=PENDIENTE, choices=ESTADO_CHOICES)
    monto_confirmado = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='El monto que viste en tu cuenta. No el que declaro el cliente.'
    )
    validado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_validacion = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.TextField(blank=True)

    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        constraints = [
            # el mismo comprobante no se usa dos veces. Es la defensa mas barata
            # contra reenviar un screenshot viejo, y en MySQL ademas ignora
            # mayusculas, asi que 'OP123' y 'op123' chocan solos.
            models.UniqueConstraint(
                fields=['cuenta', 'nro_operacion'], name='unica_operacion_por_cuenta'
            ),
        ]

    def __str__(self):
        return f'{self.nro_operacion} - {self.pedido.referencia}'

    @property
    def horas_esperando(self):
        """ Hace cuanto el cliente esta esperando su confirmacion """
        if self.estado != self.PENDIENTE:
            return None
        return (timezone.now() - self.creado).total_seconds() / 3600

    @property
    def espera(self):
        """ La espera en palabras, para la bandeja: '4h 20m' """
        horas = self.horas_esperando
        if horas is None:
            return ''
        minutos = int(horas * 60)
        return f'{minutos // 60}h {minutos % 60:02d}m'

    @property
    def demorado(self):
        """ Paso la alarma interna: hay que mirarlo antes de incumplir la promesa """
        from django.conf import settings

        horas = self.horas_esperando
        return horas is not None and horas >= settings.HORAS_ALERTA_VALIDACION

    @property
    def diferencia(self):
        """
        Contra lo que el pedido debia, no contra lo que el cliente declaro.
        Positiva si pago de mas, negativa si falto.
        """
        if self.monto_confirmado is None:
            return None
        return self.monto_confirmado - self.pedido.monto_total

    @property
    def cuadra(self):
        return self.diferencia == Decimal('0.00')

    def validar(self, monto_confirmado, usuario=None):
        """
        Da el pago por bueno y mueve el pedido a Pagado.

        `monto_confirmado` es obligatorio a proposito: para validar hay que tipear
        el monto que se vio en la cuenta. Si el sistema dejara aceptar de un clic
        el monto que declaro el cliente, en tres semanas nadie estaria mirando.
        """
        if self.estado == self.VALIDADO:
            raise ValueError('Este pago ya fue validado')
        if monto_confirmado is None:
            raise ValueError('Escribe el monto que viste en la cuenta para validar')
        if monto_confirmado <= 0:
            raise ValueError('El monto confirmado debe ser mayor a 0')

        from .ventas import Pedido

        with transaction.atomic():
            self.estado = self.VALIDADO
            self.monto_confirmado = monto_confirmado
            self.validado_por = usuario
            self.fecha_validacion = timezone.now()
            self.motivo_rechazo = ''
            self.save(update_fields=[
                'estado', 'monto_confirmado', 'validado_por', 'fecha_validacion', 'motivo_rechazo',
            ])

            pedido = self.pedido
            if pedido.cerrado_sin_venta:
                # el pedido ya termino y solto sus unidades. Darlo por pagado
                # desde aca lo resucitaria a espaldas del almacen; y decidir si
                # corresponde cumplirle o devolverle no es algo que el sistema
                # pueda resolver mirando una captura. Lo resuelve una persona.
                raise ValueError(
                    f'El pedido {pedido.referencia} esta '
                    f'{pedido.get_estado_display().lower()} y ya solto sus unidades. '
                    'Coordinalo con el cliente por WhatsApp: si el pago fue real, '
                    'que rehaga la compra y suba este mismo comprobante. Mientras '
                    'tanto, rechaza este pago con el motivo para sacarlo de la bandeja.'
                )
            if pedido.estado != Pedido.PAGADO:
                # pasar a Pagado convierte la reserva en venta: recien ahi las
                # unidades salen del almacen y aparecen en el kardex
                pedido.cambiar_estado(Pedido.PAGADO, usuario=usuario)
        return self

    def rechazar(self, motivo, usuario=None):
        """
        Descarta el comprobante y cierra el pedido, soltando las unidades.

        Un comprobante rechazado es un pedido sin pago detras, y un pedido sin
        pago no puede retener inventario: mientras espera, ese par no se le
        ofrece a nadie mas. Con la validacion sin vencimiento, dejarlo abierto
        lo congelaba para siempre.

        Al cliente no se le cierra la puerta. El pedido queda cancelado con el
        motivo a la vista, y si de verdad pago puede subir un comprobante
        corregido: al validarlo el pedido revive, si todavia hay unidad. Si otra
        persona ya se la llevo, lo atiende un asesor por WhatsApp.
        """
        from .ventas import Pedido

        if self.estado == self.VALIDADO:
            raise ValueError('Este pago ya fue validado: no se puede rechazar despues')
        motivo = (motivo or '').strip()
        if not motivo:
            raise ValueError('Rechazar un pago exige un motivo: el cliente lo va a leer')

        with transaction.atomic():
            self.estado = self.RECHAZADO
            self.motivo_rechazo = motivo
            self.validado_por = usuario
            self.fecha_validacion = timezone.now()
            self.save(update_fields=[
                'estado', 'motivo_rechazo', 'validado_por', 'fecha_validacion',
            ])

            pedido = self.pedido
            if not pedido.cerrado_sin_venta:
                pedido.cancelar(f'Comprobante rechazado: {motivo}', usuario=usuario)

            # el correo mas delicado: el cliente cree que pago y se entera de
            # que su pedido se cerro. Tiene que decir por que y como seguir
            avisar_comprobante_rechazado(pedido, motivo)
        return self
