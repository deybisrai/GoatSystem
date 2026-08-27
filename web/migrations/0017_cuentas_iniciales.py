"""
Las cuentas con las que arranca el cobro.

Mismo criterio que la paleta de colores de la 0013: se siembra lo que ya existe
en el negocio para no empezar con la pantalla vacia. Se editan desde el admin
como cualquier otra fila.

El QR de Yape esta cargado y es el real. La imagen se guarda reducida a 800px y
con paleta corta: un QR son dos colores mas el logo, y a tamano original pesaba
un mega en una pantalla que se abre casi siempre desde el celular.
"""

from django.db import migrations

CUENTAS = [
    {
        'metodo': 'QR',
        'titular': 'Monetix Retail',
        'moneda': 'PEN',
        'imagen_qr': 'cuentas/qr_yape_retail.png',
        'instrucciones': 'Escanea el codigo con tu app de Yape. Vas a ver el nombre MONETIX RETAIL.',
        'orden': 10,
    },
    {
        'metodo': 'YAPE',
        'titular': 'Monetix Retail',
        'moneda': 'PEN',
        'telefono': '955134139',
        'orden': 20,
    },
    {
        'metodo': 'BANCO',
        'titular': 'Monetix Retail',
        'moneda': 'PEN',
        'banco': 'BCP',
        # revisar: se asumio CORRIENTE por ser cuenta de empresa. Se cambia en
        # un clic desde el admin si en realidad es de ahorros.
        'tipo_cuenta': 'CORRIENTE',
        'numero': '3507296754036',
        'cci': '00235000729675403679',
        'orden': 30,
    },
]


def sembrar(apps, schema_editor):
    CuentaRecaudadora = apps.get_model('web', 'CuentaRecaudadora')
    for datos in CUENTAS:
        CuentaRecaudadora.objects.get_or_create(
            metodo=datos['metodo'],
            titular=datos['titular'],
            numero=datos.get('numero', ''),
            telefono=datos.get('telefono', ''),
            defaults=datos,
        )


def quitar(apps, schema_editor):
    CuentaRecaudadora = apps.get_model('web', 'CuentaRecaudadora')
    for datos in CUENTAS:
        CuentaRecaudadora.objects.filter(
            metodo=datos['metodo'],
            titular=datos['titular'],
            numero=datos.get('numero', ''),
            telefono=datos.get('telefono', ''),
            pagos__isnull=True,
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0016_cobro_por_transferencia'),
    ]

    operations = [
        migrations.RunPython(sembrar, quitar),
    ]
