"""
Lo que todas las pantallas necesitan saber sin que cada vista lo pase.

Por ahora uno solo: si el cliente dejo un pedido a medio pagar. Sin esto, quien
se iba de /pago a mirar otro producto perdia el camino de vuelta -- la pantalla
de pago se encuentra por la sesion y no hay ninguna URL que lleve a ella. La
reserva se le vencia en diez minutos y nunca supo por que.
"""

from .models import Pedido


def pedido_en_curso(request):
    """
    El pedido que quedo esperando pago, para el aviso flotante del layout.

    Devuelve None en la propia pantalla de pago: ahi ya hay un contador y
    repetirlo seria ruido. Tampoco molesta al que no tiene nada pendiente, que
    es la enorme mayoria de las visitas.
    """
    vacio = {'pedido_en_curso': None, 'segundos_en_curso': None}

    pedido_id = request.session.get('ultimo_pedido')
    if not pedido_id or request.path == '/pago':
        return vacio

    pedido = (
        Pedido.objects
        .filter(pk=pedido_id, estado=Pedido.SOLICITADO)
        .only('id', 'codigo_reserva', 'nro_pedido', 'estado', 'reserva_vence', 'monto_total')
        .first()
    )
    if pedido is None:
        return vacio

    # ya vencido: el aviso igual sale, pero diciendo la verdad. Que el cliente
    # se entere aca es mejor que descubrirlo cuando vuelva a /pago
    return {'pedido_en_curso': pedido, 'segundos_en_curso': pedido.segundos_restantes}
