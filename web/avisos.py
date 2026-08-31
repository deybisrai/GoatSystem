"""
Avisos por correo, y el enlace al asesor.

Dos publicos distintos. Al EQUIPO se le avisa cuando llega un comprobante: el
cuello de botella para validar rapido no es la pantalla, es enterarse. Al
CLIENTE se le avisa en cada paso que le cambia algo, porque hasta ahora no se le
escribia nunca: dejaba su correo en el checkout y no volvia a saber de nosotros
ni cuando su pedido quedaba esperandolo en el mostrador.

Todos salen con `transaction.on_commit`: si la transaccion se cae, el hecho no
ocurrio y el aviso tampoco sale. Al reves seria peor, avisar de algo que despues
no quedo guardado.

Y todos van con `fail_silently`: que el servidor de correo este caido no puede
tumbar una venta.
"""

from urllib.parse import quote

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


# ------------------------------------------------------------------ #
#  El asesor                                                          #
# ------------------------------------------------------------------ #

def enlace_whatsapp(pedido=None):
    """
    Enlace al asesor, con el pedido ya escrito en el mensaje.

    Lo que una pantalla no resuelve -- una devolucion, un caso raro -- lo
    atiende una persona. Que el cliente no tenga que explicar cual es su pedido
    es la diferencia entre que escriba y que abandone.
    """
    numero = getattr(settings, 'WHATSAPP_ASESOR', '')
    if not numero:
        return ''
    texto = 'Hola, necesito ayuda con mi pedido'
    if pedido is not None:
        texto = f'{texto} {pedido.referencia}'
    return f'https://wa.me/{numero}?text={quote(texto)}'


# ------------------------------------------------------------------ #
#  Correos al cliente                                                 #
# ------------------------------------------------------------------ #

def _detalle(pedido):
    """
    Las lineas del pedido, tal como quedaron congeladas al comprarlo.

    El ancho se calcula sobre el contenido y no se fija a mano: con un nombre
    largo, un padding fijo deja el total desalineado y el correo se ve descuidado.
    """
    filas = [
        (f'{d.cantidad} x {d.nombre_producto}{f" {d.valor}" if d.valor else ""}',
         f's/{d.subtotal}')
        for d in pedido.detalles.all()
    ]
    filas.append(('Total', f's/{pedido.monto_total}'))

    ancho_texto = max(len(t) for t, _ in filas)
    ancho_monto = max(len(m) for _, m in filas)
    return [f'  {texto:<{ancho_texto}}   {monto:>{ancho_monto}}' for texto, monto in filas]


def _pie(pedido):
    """ Como seguir la conversacion si algo no cuadra """
    pie = ['', 'Cualquier duda, respondenos este correo.']
    enlace = enlace_whatsapp(pedido)
    if enlace:
        pie.append(f'O escribinos por WhatsApp: {enlace}')
    pie += ['', 'GOAT X']
    return pie


def _escribirle(pedido, asunto, cuerpo):
    """
    Le manda un correo al comprador. Devuelve si habia a quien mandarselo.

    Un pedido de invitado siempre tiene email -- el checkout lo exige -- pero
    los pedidos viejos de una migracion podrian no tenerlo, y quedarse sin
    avisar es mejor que reventar en medio de una venta.
    """
    if not pedido.email_comprador:
        return False

    texto = '\n'.join(cuerpo + _pie(pedido))
    transaction.on_commit(lambda: send_mail(
        asunto, texto, settings.DEFAULT_FROM_EMAIL,
        [pedido.email_comprador], fail_silently=True,
    ))
    return True


def avisar_comprobante_recibido(pedido):
    """ Lo mando y quiere saber que llego. Aca nace su numero de pedido """
    return _escribirle(
        pedido,
        f'Recibimos tu comprobante - pedido {pedido.referencia}',
        [
            f'Hola {pedido.nombre_comprador},',
            '',
            f'Recibimos tu comprobante y tu pedido quedo registrado con el numero '
            f'{pedido.referencia}.',
            '',
            'Lo estamos verificando contra nuestra cuenta. Te escribimos apenas '
            f'quede confirmado, dentro de las proximas {settings.HORAS_VALIDACION} horas.',
            '',
            'Detalle:',
        ] + _detalle(pedido),
    )


def avisar_pago_confirmado(pedido):
    """ El momento que estaba esperando """
    if pedido.es_recojo:
        siguiente = (
            'Te avisamos apenas este listo para que lo retires en '
            f'{pedido.punto_recojo_nombre}.'
        )
    else:
        siguiente = 'Ya lo estamos preparando para enviartelo a tu direccion.'

    return _escribirle(
        pedido,
        f'Confirmamos tu pago - pedido {pedido.referencia}',
        [
            f'Hola {pedido.nombre_comprador},',
            '',
            f'Confirmamos tu pago del pedido {pedido.referencia}. Gracias.',
            '',
            siguiente,
            '',
            'Detalle:',
        ] + _detalle(pedido),
    )


def avisar_listo_para_recojo(pedido):
    """
    El correo que mas falta hacia.

    Sin esto el cliente no se enteraba nunca de que su pedido lo estaba
    esperando, y la mercaderia se quedaba ocupando el mostrador.
    """
    punto = pedido.punto_recojo
    donde = [f'  {pedido.punto_recojo_nombre}', f'  {pedido.punto_recojo_direccion}']
    if punto is not None:
        if punto.horario:
            donde.append(f'  Horario: {punto.horario}')
        if punto.telefono:
            donde.append(f'  Telefono: {punto.telefono}')

    return _escribirle(
        pedido,
        f'Tu pedido {pedido.referencia} te espera',
        [
            f'Hola {pedido.nombre_comprador},',
            '',
            f'Tu pedido {pedido.referencia} ya esta listo para que lo retires.',
            '',
            'Donde:',
        ] + donde + [
            '',
            'Anda con tu documento y el numero de pedido.',
            '',
            'Detalle:',
        ] + _detalle(pedido),
    )


def avisar_enviado(pedido):
    """ Salio para su direccion """
    return _escribirle(
        pedido,
        f'Tu pedido {pedido.referencia} va en camino',
        [
            f'Hola {pedido.nombre_comprador},',
            '',
            f'Tu pedido {pedido.referencia} salio para tu direccion:',
            '',
            f'  {pedido.donde_recibe}',
            '',
            'Detalle:',
        ] + _detalle(pedido),
    )


def avisar_comprobante_rechazado(pedido, motivo):
    """
    No pudimos confirmar el pago. Es el correo mas delicado de los cinco: el
    cliente cree que pago y se entera de que su pedido se cerro. Tiene que decir
    por que, y sobre todo como seguir.
    """
    return _escribirle(
        pedido,
        f'No pudimos confirmar tu pago - pedido {pedido.referencia}',
        [
            f'Hola {pedido.nombre_comprador},',
            '',
            f'Revisamos el comprobante del pedido {pedido.referencia} y no pudimos '
            'confirmarlo:',
            '',
            f'  {motivo}',
            '',
            'Soltamos los productos, asi que otra persona ya puede comprarlos.',
            '',
            'Si tu pago fue real, escribinos y lo resolvemos: si el producto sigue '
            'disponible te lo despachamos, y si no, coordinamos la devolucion de tu '
            'dinero. No te quedes sin avisarnos.',
        ],
    )


def avisar_entregado(pedido):
    """
    Cierra el pedido, y sobre todo abre la puerta al reclamo temprano.

    Marcar como entregado es el unico paso del ciclo que hacemos nosotros solos:
    todos los demas los dispara el cliente. Si nos equivocamos -- el pedido que
    no era, el motorizado que dijo que entrego, alguien que retiro por el -- sin
    este correo el cliente se entera cuando reclama, y para entonces no hay como
    reconstruir que paso. Con el correo, un error nuestro vuelve el mismo dia.

    Por eso la linea que importa no es "gracias por tu compra", es "si no lo
    recibiste, avisanos ahora".
    """
    if pedido.es_recojo:
        como = f'Lo retiraste en {pedido.punto_recojo_nombre}.'
    else:
        como = f'Lo entregamos en {pedido.donde_recibe}.'

    return _escribirle(
        pedido,
        f'Entregamos tu pedido {pedido.referencia}',
        [
            f'Hola {pedido.nombre_comprador},',
            '',
            f'Registramos la entrega de tu pedido {pedido.referencia}.',
            como,
            '',
            'Si no lo recibiste, escribinos AHORA y lo revisamos. Cuanto antes '
            'nos enteremos, mas facil es resolverlo.',
            '',
            'Detalle:',
        ] + _detalle(pedido),
    )
