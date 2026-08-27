"""
Donde vive el stock, y como viaja entre ciudades.

Hasta ahora el inventario era un solo numero por talla, sin lugar. Con dos
ciudades eso deja de alcanzar: un par en Huancavelica no es lo mismo que un par
en Lircay, aunque el SKU sea el mismo.

Un punto de recojo NO es una ubicacion. GOAT X, Daily Credits y Vision para
crecer son tres mostradores de la misma ciudad y se sirven del mismo almacen:
llevar un par de la tienda a Daily Credits son tres cuadras, no un traslado.
Por eso la ubicacion es la ciudad.
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone


class Ubicacion(models.Model):
    """
    Una ciudad donde guardamos mercaderia, con su calendario de traslados.

    El calendario existe para poder prometer una fecha antes de que el documento
    de traslado exista: cuando el cliente compra un miercoles, la lista de esa
    semana todavia no esta armada, pero la fecha ya se puede calcular.
    """

    DIAS_SEMANA = (
        (0, 'Lunes'), (1, 'Martes'), (2, 'Miercoles'), (3, 'Jueves'),
        (4, 'Viernes'), (5, 'Sabado'), (6, 'Domingo'),
    )

    nombre = models.CharField(max_length=60, unique=True)
    es_principal = models.BooleanField(
        default=False, help_text='El almacen general, donde entra lo que se compra.'
    )
    dia_despacho = models.PositiveSmallIntegerField(
        choices=DIAS_SEMANA, default=5,
        help_text='Que dia sale la mercaderia hacia aca.',
    )
    dias_viaje = models.PositiveSmallIntegerField(
        default=2, help_text='Cuantos dias tarda en estar disponible desde que sale.'
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Ubicacion'
        verbose_name_plural = 'Ubicaciones'
        ordering = ['-es_principal', 'nombre']

    def __str__(self):
        return self.nombre

    def proximo_despacho(self, desde=None):
        """ El proximo dia que sale mercaderia hacia esta ubicacion """
        hoy = desde or timezone.localdate()
        faltan = (self.dia_despacho - hoy.weekday()) % 7
        # si el despacho es hoy, ya se considera perdido: la camioneta sale
        # temprano y no se le puede prometer a alguien que compra a la tarde
        return hoy + timedelta(days=faltan or 7)

    def llegada_de(self, salida):
        """ Cuando queda disponible aca algo que sale tal dia """
        return salida + timedelta(days=self.dias_viaje)

    def proximo_traslado(self):
        """ El viaje ya programado hacia aca, si hay alguno """
        from .traslados import Traslado

        return (
            self.traslados_recibidos
            .filter(estado__in=[Traslado.PLANIFICADO, Traslado.EN_TRANSITO])
            .order_by('fecha_disponible')
            .first()
        )

    @property
    def sin_transporte(self):
        """
        Nadie programo como llegar aca.

        Es el punto ciego del modulo: sin viaje programado la ciudad deja de
        ofrecer productos y nadie se entera, porque el cliente no ve un error,
        ve un catalogo mas chico. Por eso se mira, no se espera al reclamo.
        """
        return not self.es_principal and self.proximo_traslado() is None
