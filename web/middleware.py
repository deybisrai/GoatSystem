"""
Vence las reservas con el reloj, sin depender de que alguien programe el comando.

El plazo de una reserva se cumple solo, pero pasar el pedido a Cancelado es una
escritura, y alguien tiene que dispararla. Hasta ahora ese alguien era el
checkout de otro cliente, la pantalla de pago o un cron que nadie programo: si
las tres fallaban, el pedido quedaba en Solicitado para siempre.

Esto lo resuelve con el trafico que ya existe. Cada tanto, una peticion
cualquiera paga el costo de barrer lo vencido. No corre en todas: `cache.add`
solo devuelve True para la primera que llega en cada ventana, asi que el barrido
sale una vez por intervalo y no una vez por visita.

Si no hay trafico no se barre, y esta bien: sin nadie mirando la tienda, no hay
a quien le moleste un rotulo atrasado. El comando `vencer_reservas` sigue
existiendo para correrlo a mano o por cron cuando haya servidor.
"""

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class VencerReservasMiddleware:
    """ Barre las reservas vencidas como mucho una vez por intervalo """

    CLAVE = 'barrido_reservas_hecho'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._barrer_si_toca()
        return self.get_response(request)

    def _barrer_si_toca(self):
        # se lee en cada peticion y no al arrancar, para poder apagarlo sin
        # reiniciar el servidor
        intervalo = getattr(settings, 'SEGUNDOS_ENTRE_BARRIDOS', 60)
        if intervalo <= 0:
            return

        # add() escribe solo si la clave no existe. Es atomico, asi que dos
        # peticiones simultaneas no barren las dos.
        if not cache.add(self.CLAVE, True, intervalo):
            return

        from .models import Pedido
        try:
            cuantos = Pedido.vencer_reservas()
        except Exception:
            # un fallo barriendo no puede tumbar la pagina que el cliente pidio
            logger.exception('Fallo el barrido automatico de reservas vencidas')
            return

        if cuantos:
            logger.info('Barrido automatico: %s reserva(s) vencida(s) liberada(s)', cuantos)
