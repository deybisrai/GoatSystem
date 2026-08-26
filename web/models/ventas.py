""" Ventas: pedidos del canal online (stock que sale) """

from django.db import models


class Pedido(models.Model):

    ESTADO_CHOICES = (
        ('0', 'Solicitado'),
        ('1', 'Pagado'),
    )

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
    estado = models.CharField(max_length=1, default='0', choices=ESTADO_CHOICES)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    direccion_envio = models.CharField(max_length=200)
    referencia_envio = models.CharField(max_length=200, blank=True)
    distrito_envio = models.CharField(max_length=60)
    provincia_envio = models.CharField(max_length=60)
    departamento_envio = models.CharField(max_length=60)
    telefono_envio = models.CharField(max_length=20)

    cupon = models.ForeignKey('web.Cupon', on_delete=models.SET_NULL, null=True, blank=True)
    descuento_aplicado = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return self.nro_pedido

    @property
    def es_invitado(self):
        return self.cliente_id is None


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
