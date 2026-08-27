"""
Los cuatro mostradores donde el cliente puede retirar.

GOAT X y Monetix ademas guardan mercaderia; Daily Credits y Vision para crecer
solo atienden. Esa diferencia no se modela todavia: recien importa cuando exista
el stock por ubicacion, y hasta entonces los cuatro se comportan igual.

Se siembran como la paleta de la 0013: son datos del negocio que ya existen, y
se editan desde el admin como cualquier otra fila.
"""

from django.db import migrations

PUNTOS = [
    {
        'nombre': 'Tienda GOAT X',
        'direccion': 'Av. Manchego Muñoz 431',
        'distrito': 'Santa Ana',
        'provincia': 'Huancavelica',
        'referencia': 'Almacen general y tienda',
        'orden': 10,
    },
    {
        'nombre': 'Vision para crecer',
        'direccion': 'Av. Manchego Muñoz 712',
        'distrito': 'Santa Ana',
        'provincia': 'Huancavelica',
        'orden': 20,
    },
    {
        'nombre': 'Daily Credits',
        'direccion': 'Jr. Agustin Gamarra 496',
        'distrito': 'Cercado',
        'provincia': 'Huancavelica',
        'orden': 30,
    },
    {
        'nombre': 'Monetix',
        'direccion': 'Jr. La Union 104',
        'distrito': 'Bellavista',
        'provincia': 'Lircay',
        'orden': 40,
    },
]


def sembrar(apps, schema_editor):
    PuntoRecojo = apps.get_model('web', 'PuntoRecojo')
    for datos in PUNTOS:
        PuntoRecojo.objects.get_or_create(
            nombre=datos['nombre'], provincia=datos['provincia'], defaults=datos
        )


def quitar(apps, schema_editor):
    PuntoRecojo = apps.get_model('web', 'PuntoRecojo')
    for datos in PUNTOS:
        PuntoRecojo.objects.filter(
            nombre=datos['nombre'], provincia=datos['provincia'], pedidos__isnull=True
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0021_recojo_en_tienda'),
    ]

    operations = [
        migrations.RunPython(sembrar, quitar),
    ]
