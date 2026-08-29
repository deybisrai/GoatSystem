"""
Separa "se le acabo el tiempo" de "alguien lo cancelo".

Los dos terminaban en CANCELADO, distinguidos solo por el texto de
`motivo_cancelacion`. Filtrar por texto libre no sirve para contar, y contar es
justo lo que hace falta: sin saber cuantas reservas se caen solas no hay forma
de saber si el plazo esta bien puesto.

Las cancelaciones historicas cuyo motivo dice que vencio el plazo pasan a
EXPIRADO. Las demas se quedan como estan: las cancelo alguien.
"""

from django.db import migrations, models

MOTIVO_DEL_PLAZO = 'Vencio el plazo para pagar'


def separar_los_expirados(apps, schema_editor):
    Pedido = apps.get_model('web', 'Pedido')
    Pedido.objects.filter(estado='4', motivo_cancelacion=MOTIVO_DEL_PLAZO).update(estado='7')


def volver_a_juntarlos(apps, schema_editor):
    Pedido = apps.get_model('web', 'Pedido')
    Pedido.objects.filter(estado='7').update(estado='4')


class Migration(migrations.Migration):

    dependencies = [('web', '0031_un_solo_check_de_traslado')]

    operations = [
        migrations.AlterField(
            model_name='pedido',
            name='estado',
            field=models.CharField(
                choices=[
                    ('0', 'Solicitado'),
                    ('5', 'En validacion'),
                    ('1', 'Pagado'),
                    ('2', 'Enviado'),
                    ('6', 'Listo para recojo'),
                    ('3', 'Entregado'),
                    ('7', 'Expirado'),
                    ('4', 'Cancelado'),
                ],
                default='0',
                max_length=1,
            ),
        ),
        migrations.RunPython(separar_los_expirados, volver_a_juntarlos),
    ]
