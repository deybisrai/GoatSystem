"""
Los pedidos abiertos pasan de haber descontado stock a solo reservarlo.

Hasta ahora, hacer clic en "Realizar pedido" restaba del stock y escribia una
Venta en el kardex, aunque el par siguiera en el estante. Los pedidos que
quedaron sin pagar dejaron dos cosas mal:

  - unidades que el sistema da por vendidas y estan en el almacen
  - renglones de Venta en el kardex que describen algo que nunca ocurrio

Esta migracion devuelve esas unidades al stock, las marca como reservadas, y
borra los movimientos de Venta que las respaldaban. Borrar en el kardex va
contra la regla del proyecto, y es a proposito: no se esta tapando historia,
se esta quitando un renglon que afirmaba un movimiento inexistente.

Los pedidos anteriores al kardex descontaron stock sin dejar movimiento. Para
esos se escribe un AJUSTE, que es justamente para lo que existe: una correccion
que queda auditable.
"""

from datetime import timedelta

from django.conf import settings
from django.db import migrations

ABIERTOS = ['0', '5']          # Solicitado, En validacion
MOTIVO_AJUSTE = (
    'Ajuste al separar el stock de las reservas: este pedido habia descontado '
    'unidades que nunca salieron del almacen.'
)


def _detalles_abiertos(apps):
    Pedido = apps.get_model('web', 'Pedido')
    for pedido in Pedido.objects.filter(estado__in=ABIERTOS).prefetch_related('detalles'):
        if pedido.pagos.filter(estado='V').exists():
            continue
        yield pedido, list(pedido.detalles.all())


def a_reserva(apps, schema_editor):
    Inventario = apps.get_model('web', 'Inventario')
    MovimientoInventario = apps.get_model('web', 'MovimientoInventario')

    for pedido, detalles in _detalles_abiertos(apps):
        for detalle in detalles:
            item = Inventario.objects.get(pk=detalle.item_id)

            venta = MovimientoInventario.objects.filter(
                pedido_id=pedido.id, item_id=item.id, tipo='VENTA'
            ).first()

            if venta is not None:
                # el renglon se va y su -cantidad con el, asi que el kardex sigue
                # cuadrando cuando el stock suba de nuevo
                venta.delete()
            else:
                # pedido anterior al kardex: no hay renglon que quitar, hay que
                # explicar de donde salen las unidades que vuelven
                MovimientoInventario.objects.create(
                    item=item,
                    tipo='AJUSTE',
                    cantidad=detalle.cantidad,
                    stock_anterior=item.stock,
                    stock_resultante=item.stock + detalle.cantidad,
                    costo_unitario=item.costo_promedio,
                    costo_promedio_resultante=item.costo_promedio,
                    pedido_id=pedido.id,
                    motivo=MOTIVO_AJUSTE,
                )

            item.stock += detalle.cantidad
            item.reservado += detalle.cantidad
            item.save(update_fields=['stock', 'reservado'])

        # el plazo que le habria tocado. Los pedidos viejos nacen vencidos y el
        # primer checkout que toque esas unidades las suelta.
        if pedido.estado == '5':
            pedido.reserva_vence = pedido.fecha_registro + timedelta(hours=settings.HORAS_VALIDACION)
        else:
            pedido.reserva_vence = pedido.fecha_registro + timedelta(minutes=settings.MINUTOS_RESERVA)
        pedido.save(update_fields=['reserva_vence'])


def a_descuento(apps, schema_editor):
    """
    Vuelta atras: las unidades reservadas se descuentan de nuevo.

    Los renglones de Venta que se borraron no vuelven, y esta bien: describian
    movimientos que nunca ocurrieron.
    """
    Inventario = apps.get_model('web', 'Inventario')
    MovimientoInventario = apps.get_model('web', 'MovimientoInventario')

    for pedido, detalles in _detalles_abiertos(apps):
        for detalle in detalles:
            item = Inventario.objects.get(pk=detalle.item_id)
            item.stock = max(item.stock - detalle.cantidad, 0)
            item.reservado = max(item.reservado - detalle.cantidad, 0)
            item.save(update_fields=['stock', 'reservado'])

        MovimientoInventario.objects.filter(
            pedido_id=pedido.id, tipo='AJUSTE', motivo=MOTIVO_AJUSTE
        ).delete()

        pedido.reserva_vence = None
        pedido.save(update_fields=['reserva_vence'])


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0019_reserva_con_vencimiento'),
    ]

    operations = [
        migrations.RunPython(a_reserva, a_descuento),
    ]
