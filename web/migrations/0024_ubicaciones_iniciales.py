"""
Las dos ciudades donde vive el stock, y a cual pertenece lo que ya hay.

Todo el inventario cargado hasta hoy esta fisicamente en GOAT X, que es el
almacen general: la migracion se lo asigna a Huancavelica. Lircay arranca en
cero y se llena con traslados, que es como funciona en la realidad.

Los puntos de recojo quedan asociados a su ciudad por el nombre de la provincia,
que es el dato que ya venian cargando.
"""

from django.db import migrations

UBICACIONES = [
    {
        'nombre': 'Huancavelica',
        'es_principal': True,
        # el almacen general no recibe traslados: de aca sale todo
        'dia_despacho': 5,
        'dias_viaje': 0,
    },
    {
        'nombre': 'Lircay',
        'es_principal': False,
        'dia_despacho': 5,      # sabado
        'dias_viaje': 2,        # disponible el lunes
    },
]


def sembrar(apps, schema_editor):
    Ubicacion = apps.get_model('web', 'Ubicacion')
    Inventario = apps.get_model('web', 'Inventario')

    creadas = {}
    for datos in UBICACIONES:
        ubicacion, _ = Ubicacion.objects.get_or_create(
            nombre=datos['nombre'], defaults=datos
        )
        creadas[datos['nombre']] = ubicacion

    # lo que ya existe esta en el almacen general
    Inventario.objects.filter(ubicacion__isnull=True).update(
        ubicacion=creadas['Huancavelica']
    )


def quitar(apps, schema_editor):
    Ubicacion = apps.get_model('web', 'Ubicacion')
    Inventario = apps.get_model('web', 'Inventario')

    Inventario.objects.update(ubicacion=None)
    Ubicacion.objects.filter(nombre__in=[u['nombre'] for u in UBICACIONES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0023_stock_por_ubicacion'),
    ]

    operations = [
        migrations.RunPython(sembrar, quitar),
    ]
