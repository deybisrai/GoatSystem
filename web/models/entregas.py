"""
Donde recibe el cliente: envio a domicilio o recojo en un punto nuestro.

Un punto de recojo es un mostrador, no necesariamente un almacen. Hoy GOAT X y
Monetix guardan mercaderia y ademas atienden; Daily Credits y Vision para crecer
solo atienden. Esa diferencia recien importa cuando exista el stock por
ubicacion, asi que aca el punto se describe por su ciudad y nada mas.
"""

from django.db import models


class PuntoRecojo(models.Model):
    """ Un mostrador donde el cliente retira su pedido """

    nombre = models.CharField(max_length=80)
    direccion = models.CharField(max_length=200)
    distrito = models.CharField(max_length=60)
    provincia = models.CharField(
        max_length=60, help_text='La ciudad. De aca sale el traslado entre ciudades.'
    )
    departamento = models.CharField(max_length=60, default='Huancavelica')
    referencia = models.CharField(max_length=200, blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    horario = models.CharField(
        max_length=120, blank=True, help_text='Ej. Lun a Sab de 9:00 a 19:00'
    )
    ubicacion = models.ForeignKey(
        'web.Ubicacion', on_delete=models.RESTRICT, related_name='puntos',
        help_text='De que almacen se sirve este mostrador. Sin esto no se puede '
                  'saber que ofrecerle al cliente aca.',
    )
    activo = models.BooleanField(default=True)
    orden = models.PositiveSmallIntegerField(default=0, help_text='Menor primero en el checkout.')

    class Meta:
        verbose_name = 'Punto de recojo'
        verbose_name_plural = 'Puntos de recojo'
        ordering = ['orden', 'nombre']

    def __str__(self):
        return f'{self.nombre} - {self.provincia}'

    @property
    def direccion_completa(self):
        """
        Como se le muestra al cliente y como queda copiada en el pedido.

        Si el distrito ya nombra la ciudad no se repite: cargando 'barrio Santa
        Ana, Huancavelica' salia 'Huancavelica - Huancavelica'.
        """
        partes = [self.direccion, self.distrito]
        if self.provincia.lower() not in self.distrito.lower():
            partes.append(self.provincia)
        return ', '.join(p.strip() for p in partes if p.strip())
