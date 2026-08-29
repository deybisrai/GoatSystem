"""
Separa el identificador de la reserva del numero de la venta.

Antes el `nro_pedido` se emitia al confirmar el checkout, asi que un carrito
abandonado se llevaba un correlativo. Ahora el pedido nace con un
`codigo_reserva` no correlativo y el numero se emite recien cuando llega el
comprobante.

Los pedidos ya emitidos conservan su numero: renumerarlos cambiaria papeles que
ya se le mostraron a alguien.
"""

from django.db import migrations, models

import web.models.ventas


def sembrar_codigos(apps, schema_editor):
    """ Un codigo de reserva para cada pedido que ya existe """
    Pedido = apps.get_model('web', 'Pedido')
    for pedido in Pedido.objects.filter(codigo_reserva__isnull=True):
        pedido.codigo_reserva = web.models.ventas.codigo_de_reserva()
        pedido.save(update_fields=['codigo_reserva'])


class Migration(migrations.Migration):

    dependencies = [('web', '0028_punto_con_ubicacion_obligatoria')]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='codigo_reserva',
            field=models.CharField(max_length=20, null=True, editable=False),
        ),
        migrations.RunPython(sembrar_codigos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='pedido',
            name='codigo_reserva',
            field=models.CharField(
                default=web.models.ventas.codigo_de_reserva, editable=False,
                help_text='Identifica la reserva desde que se confirma el pedido.',
                max_length=20, unique=True,
            ),
        ),
        migrations.AlterField(
            model_name='pedido',
            name='nro_pedido',
            field=models.CharField(
                blank=True, null=True, max_length=20, unique=True,
                help_text='Correlativo de la venta. Se emite al recibir el comprobante.',
            ),
        ),
    ]
