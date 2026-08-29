"""
Un solo check para decir que un producto no viaja.

Antes eran dos campos y tres estados: `Categoria.trasladable`,
`Producto.trasladable` (si / no / vacio = heredar de la categoria). Tres estados
para describir una excepcion que casi nunca ocurre. Ahora la regla es que todo
viaja y solo se marca lo que no.

Nada se pierde: se calcula el valor que tenia cada producto con las dos reglas
viejas y se guarda en el campo nuevo antes de borrar los anteriores.
"""

from django.db import migrations, models


def guardar_lo_que_no_viaja(apps, schema_editor):
    """ Traduce las dos reglas viejas al check nuevo, producto por producto """
    Producto = apps.get_model('web', 'Producto')
    for producto in Producto.objects.select_related('categoria'):
        if producto.trasladable is None:
            viaja = producto.categoria.trasladable
        else:
            viaja = producto.trasladable
        if not viaja:
            producto.no_se_traslada = True
            producto.save(update_fields=['no_se_traslada'])


def devolver_los_campos_viejos(apps, schema_editor):
    """ Marcha atras: el check vuelve al override del producto """
    Producto = apps.get_model('web', 'Producto')
    for producto in Producto.objects.all():
        producto.trasladable = not producto.no_se_traslada
        producto.save(update_fields=['trasladable'])


class Migration(migrations.Migration):

    dependencies = [('web', '0030_idempotencia_del_checkout')]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='no_se_traslada',
            field=models.BooleanField(
                default=False,
                verbose_name='No se traslada a otras ciudades',
                help_text='Marcar solo si el producto no puede viajar: una refrigeradora '
                          'cuesta mas trasladarla que lo que deja la venta. Sin marcar, viaja.',
            ),
        ),
        migrations.RunPython(guardar_lo_que_no_viaja, devolver_los_campos_viejos),
        migrations.RemoveField(model_name='producto', name='trasladable'),
        migrations.RemoveField(model_name='categoria', name='trasladable'),
    ]
