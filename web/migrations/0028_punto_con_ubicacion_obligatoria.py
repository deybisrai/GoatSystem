"""
El punto de recojo no puede quedarse sin almacen.

Sin ubicacion no hay forma de saber que ofrecerle al cliente en ese mostrador, y
el codigo terminaba diciendo "retiralo hoy" para todo, que es lo peor que podia
contestar. La 0027 ya empareja cada punto con su ciudad, asi que el campo pasa a
obligatorio y esa rama deja de existir.

Va escrita a mano porque `makemigrations` pide un valor por defecto que aca no
hace falta.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0027_puntos_a_su_ciudad'),
    ]

    operations = [
        migrations.AlterField(
            model_name='puntorecojo',
            name='ubicacion',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='puntos',
                to='web.ubicacion',
                help_text='De que almacen se sirve este mostrador. Sin esto no se puede '
                          'saber que ofrecerle al cliente aca.',
            ),
        ),
    ]
