"""
Avisos por correo.

El cuello de botella para validar rapido no es la pantalla: es enterarse. Sin
esto habria que acordarse de entrar a mirar la bandeja, que es justamente lo que
no pasa un sabado a las nueve de la noche.

Los correos se mandan con `transaction.on_commit`: si la transaccion se cae, el
comprobante no existe y el aviso tampoco sale. Al reves seria peor, avisar de un
pago que despues no quedo guardado.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse


def _destinatarios():
    return [correo for correo in getattr(settings, 'CORREOS_AVISO', []) if correo]


def avisar_pago_declarado(pago, base_url=''):
    """ Le avisa al equipo que hay un comprobante esperando validacion """
    destinos = _destinatarios()
    if not destinos:
        return 0

    pedido = pago.pedido
    enlace = f'{base_url}{reverse("web:validarPago", args=[pago.id])}'

    asunto = f'Pago por validar: {pedido.referencia} - s/{pago.monto_declarado}'
    cuerpo = '\n'.join([
        f'{pedido.nombre_comprador} {pedido.apellido_comprador} declara haber pagado.',
        '',
        f'Pedido    : {pedido.referencia}',
        f'Total     : s/{pedido.monto_total}',
        f'Declara   : s/{pago.monto_declarado}',
        f'Cuenta    : {pago.cuenta}',
        f'Operacion : {pago.nro_operacion}',
        f'Entrega   : {pedido.donde_recibe}',
        '',
        f'Tenes {settings.HORAS_VALIDACION} horas para confirmarlo.',
        'Revisa tu cuenta y validalo aca:',
        enlace,
    ])

    # fuera de la transaccion: si algo falla al guardar, no sale ningun aviso
    transaction.on_commit(lambda: send_mail(
        asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, destinos, fail_silently=True,
    ))
    return len(destinos)
