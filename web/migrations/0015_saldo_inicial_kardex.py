"""
El stock que ya estaba cargado no tiene historia: nacio antes del kardex.

Se le abre un movimiento de saldo inicial por cada unidad vendible con stock,
para que desde el primer dia la suma del kardex cuadre con `Inventario.stock`
y el comando `verificar_kardex` sirva de verdad.
"""

from django.db import migrations

MOTIVO = 'Saldo abierto al poner en marcha el kardex. El stock previo no tenia historial.'


def abrir_saldos(apps, schema_editor):
    Inventario = apps.get_model('web', 'Inventario')
    MovimientoInventario = apps.get_model('web', 'MovimientoInventario')

    MovimientoInventario.objects.bulk_create([
        MovimientoInventario(
            item=item,
            tipo='INICIAL',
            cantidad=item.stock,
            stock_anterior=0,
            stock_resultante=item.stock,
            costo_unitario=item.costo_promedio,
            costo_promedio_resultante=item.costo_promedio,
            motivo=MOTIVO,
        )
        for item in Inventario.objects.filter(stock__gt=0)
    ])


def cerrar_saldos(apps, schema_editor):
    MovimientoInventario = apps.get_model('web', 'MovimientoInventario')
    MovimientoInventario.objects.filter(tipo='INICIAL').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0014_kardex_y_anulaciones'),
    ]

    operations = [
        migrations.RunPython(abrir_saldos, cerrar_saldos),
    ]
