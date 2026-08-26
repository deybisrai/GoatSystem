""" Promociones: descuentos automaticos por campana y cupones manuales """

from decimal import Decimal

from django.db import models
from django.utils import timezone


class Campana(models.Model):
    """ Descuento temporal (ej. CyberWow) que no toca el precio_venta real """
    nombre = models.CharField(max_length=100)
    fecha_inicio = models.DateTimeField()
    fecha_fin = models.DateTimeField()
    porcentaje_descuento = models.DecimalField(max_digits=5, decimal_places=2)
    activo = models.BooleanField(default=True)
    variantes = models.ManyToManyField('web.Variante', blank=True, related_name='campanas')
    categorias = models.ManyToManyField('web.Categoria', blank=True, related_name='campanas')

    def __str__(self):
        return self.nombre


class Cupon(models.Model):
    """ Codigo de descuento que el cliente escribe a mano en el carrito """
    TIPO_CHOICES = (
        ('P', 'Porcentaje'),
        ('M', 'Monto fijo'),
    )

    codigo = models.CharField(max_length=20, unique=True)
    tipo = models.CharField(max_length=1, choices=TIPO_CHOICES, default='P')
    valor = models.DecimalField(max_digits=9, decimal_places=2)
    monto_minimo_compra = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    fecha_inicio = models.DateTimeField()
    fecha_fin = models.DateTimeField()
    usos_maximos = models.PositiveIntegerField(null=True, blank=True, help_text='Vacio = ilimitado')
    veces_usado = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.codigo

    def es_valido(self, monto_compra):
        ahora = timezone.now()
        if not self.activo:
            return False, 'Este cupon ya no esta disponible'
        if not (self.fecha_inicio <= ahora <= self.fecha_fin):
            return False, 'Este cupon esta fuera de fecha'
        if self.usos_maximos is not None and self.veces_usado >= self.usos_maximos:
            return False, 'Este cupon alcanzo su limite de usos'
        if monto_compra < self.monto_minimo_compra:
            return False, f'Este cupon requiere una compra minima de S/ {self.monto_minimo_compra}'
        return True, ''

    def calcular_descuento(self, monto_compra):
        if self.tipo == 'P':
            descuento = monto_compra * self.valor / Decimal('100')
        else:
            descuento = self.valor
        return min(descuento, monto_compra)
