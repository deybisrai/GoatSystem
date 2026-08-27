"""
La ubicacion pasa a ser obligatoria y entra en la clave unica.

Va escrita a mano porque `makemigrations` pide un valor por defecto para volver
obligatorio un campo que era nulo, y aca no hace falta: la 0024 ya le asigno
Huancavelica a todas las filas.

El constraint cambia de (variante, valor) a (variante, valor, ubicacion): el
mismo SKU puede existir dos veces si esta en dos ciudades, que es justamente el
punto de todo esto.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0024_ubicaciones_iniciales'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='inventario',
            name='unico_valor_por_variante',
        ),
        migrations.AlterField(
            model_name='inventario',
            name='ubicacion',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='items',
                to='web.ubicacion',
                help_text='En que ciudad esta fisicamente esta mercaderia.',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventario',
            constraint=models.UniqueConstraint(
                fields=('variante', 'valor', 'ubicacion'), name='unico_valor_por_variante'
            ),
        ),
    ]
