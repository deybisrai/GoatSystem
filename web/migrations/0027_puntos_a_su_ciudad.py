"""
Cada mostrador queda atado al almacen de su ciudad.

Los tres puntos de Huancavelica se sirven del almacen general; Monetix se sirve
del de Lircay. La provincia que ya venia cargada en cada punto es el dato que los
empareja.
"""

from django.db import migrations


def emparejar(apps, schema_editor):
    PuntoRecojo = apps.get_model('web', 'PuntoRecojo')
    Ubicacion = apps.get_model('web', 'Ubicacion')

    ubicaciones = {u.nombre.lower(): u for u in Ubicacion.objects.all()}
    principal = Ubicacion.objects.filter(es_principal=True).first()

    for punto in PuntoRecojo.objects.filter(ubicacion__isnull=True):
        # se busca por la ciudad que ya tenia cargada; si no coincide con
        # ninguna, cae al almacen general, que es lo mas probable
        clave = (punto.provincia or '').strip().lower()
        punto.ubicacion = ubicaciones.get(clave, principal)
        punto.save(update_fields=['ubicacion'])


def desemparejar(apps, schema_editor):
    PuntoRecojo = apps.get_model('web', 'PuntoRecojo')
    PuntoRecojo.objects.update(ubicacion=None)


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0026_punto_recojo_ubicacion'),
    ]

    operations = [
        migrations.RunPython(emparejar, desemparejar),
    ]
