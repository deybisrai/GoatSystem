"""
Conversion del carrito en un Pedido real.

Vive aparte de views.py porque el punto de venta (app pos) va a necesitar
exactamente esta misma logica de descuento de stock.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Cliente, Cupon, Pedido, PedidoDetalle, Inventario


class PedidoError(Exception):
    """ Algo impide cerrar la venta (sin stock, precio cambiado, producto retirado) """


def _sufijo(linea):
    """ ', talla 40' / ', capacidad 128GB' / '' si el producto no usa atributo """
    if not linea.get('valor'):
        return ''
    return f", {linea.get('atributo', '').lower()} {linea['valor']}".rstrip()


@transaction.atomic
def crear_pedido(carrito, datos, usuario=None):
    """
    Crea el Pedido con sus detalles y RESERVA el inventario.

    Reservar no descuenta: las unidades quedan comprometidas y dejan de ofrecerse,
    pero siguen en el almacen hasta que alguien valide el pago. El pedido nace con
    un plazo (MINUTOS_RESERVA) y, si vence sin pagar, las suelta.

    Todo ocurre dentro de una transaccion con las filas de inventario bloqueadas
    (select_for_update), para que dos clientes no puedan reservar la ultima unidad
    al mismo tiempo. Si algo falla, no se guarda nada.
    """
    lineas = list(carrito)
    if not lineas:
        raise PedidoError('Tu carrito esta vacio')

    ids = [linea['item_id'] for linea in lineas]

    # antes de mirar si hay stock se sueltan las reservas que se pasaron de plazo.
    # Asi el sistema se corrige solo aunque nadie haya programado el comando: las
    # unidades se liberan justo cuando alguien las necesita.
    Pedido.vencer_reservas(items=ids)

    inventario = {
        item.id: item
        for item in (
            Inventario.objects
            .select_for_update()
            .select_related('variante__producto', 'valor__atributo')
            .filter(id__in=ids)
        )
    }

    cliente = None
    if usuario is not None and usuario.is_authenticated:
        cliente = Cliente.objects.filter(usuario=usuario).first()

    punto = datos.get('punto_recojo') if datos.get('modo_entrega') == Pedido.RECOJO else None

    pedido = Pedido.objects.create(
        cliente=cliente,
        nro_pedido=uuid4().hex[:20],          # provisional, se reemplaza abajo
        nombre_comprador=datos['nombre'],
        apellido_comprador=datos['apellidos'],
        email_comprador=datos['email'],
        telefono_comprador=datos['telefono'],
        dni_comprador=datos.get('dni', ''),
        modo_entrega=datos.get('modo_entrega', Pedido.ENVIO),
        direccion_envio=datos.get('direccion', ''),
        referencia_envio=datos.get('referencia', ''),
        distrito_envio=datos.get('distrito', ''),
        provincia_envio=datos.get('provincia', ''),
        departamento_envio=datos.get('departamento', ''),
        telefono_envio=datos['telefono'],
        punto_recojo=punto,
        # copia, no referencia: si manana cierra esa agencia, el pedido de ayer
        # tiene que seguir diciendo donde se retiro
        punto_recojo_nombre=punto.nombre if punto else '',
        punto_recojo_direccion=punto.direccion_completa if punto else '',
        reserva_vence=timezone.now() + timedelta(minutes=settings.MINUTOS_RESERVA),
    )

    monto_total = Decimal('0')

    for linea in lineas:
        item = inventario.get(linea['item_id'])
        if item is None or not item.variante.activo or not item.variante.producto.activo:
            raise PedidoError(
                f"'{linea['nombre']}' ({linea['color']}{_sufijo(linea)}) "
                'ya no esta disponible. Quitalo del carrito para continuar.'
            )

        nombre_item = f'{item.variante.producto.nombre} ({item.variante.color}{_sufijo(linea)})'

        cantidad = linea['cantidad']
        if cantidad > item.disponible:
            cuantas = (
                'no queda ninguna unidad' if item.disponible == 0
                else f'solo quedan {item.disponible}'
            )
            raise PedidoError(f'De {nombre_item} {cuantas}. Ajusta tu carrito.')

        # el precio manda desde la base de datos, no desde la sesion del navegador
        precio = item.precio_final()
        if precio != Decimal(linea['precio']):
            raise PedidoError(
                f'El precio de {nombre_item} cambio a S/ {precio}. '
                'Revisa tu carrito antes de confirmar.'
            )

        subtotal = precio * cantidad
        monto_total += subtotal

        PedidoDetalle.objects.create(
            pedido=pedido,
            item=item,
            sku=item.variante.sku,
            nombre_producto=item.variante.producto.nombre,
            valor=item.valor.valor if item.valor_id else '',
            precio_unitario=precio,
            cantidad=cantidad,
            subtotal=subtotal,
        )

        # se reserva, no se descuenta: el par sigue en el estante hasta que el
        # pago se valide. El kardex no registra promesas, solo movimientos reales.
        item.reservar(cantidad)

    # el cupon se revalida aqui: pudo vencer o agotarse mientras el cliente compraba.
    # si dejo de aplicar, se detiene la venta en vez de cobrar mas de lo que el cliente vio.
    motivo = carrito.motivo_cupon_invalido
    if motivo:
        raise PedidoError(f'{motivo}. Quitalo del carrito para continuar.')

    cupon = carrito.cupon
    descuento = Decimal('0.00')
    if cupon is not None:
        cupon = Cupon.objects.select_for_update().get(pk=cupon.pk)
        valido, motivo = cupon.es_valido(monto_total)
        if not valido:
            raise PedidoError(f'{motivo}. Quitalo del carrito para continuar.')

        descuento = cupon.calcular_descuento(monto_total).quantize(Decimal('0.01'))
        cupon.veces_usado += 1
        cupon.save(update_fields=['veces_usado'])

        pedido.cupon = cupon
        pedido.descuento_aplicado = descuento

    pedido.monto_total = monto_total - descuento
    pedido.nro_pedido = f'P{pedido.fecha_registro:%Y%m%d}-{pedido.id:05d}'
    pedido.save(update_fields=['monto_total', 'nro_pedido', 'cupon', 'descuento_aplicado'])

    return pedido
