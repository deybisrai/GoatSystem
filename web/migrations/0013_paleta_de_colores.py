"""
El color deja de ser texto libre y pasa a una paleta compartida.

Antes cada variante guardaba el nombre del color y, opcionalmente, su codigo hex.
Eso permitia que 'NEGRO', 'Negro' y 'negr0' fueran tres colores distintos, y
obligaba a repetir el mismo hex en cada variante.

Los colores ya usados se conservan: se convierten en filas de la paleta.
"""

import django.db.models.deletion
from django.db import migrations, models

# la tabla que estaba en el codigo, para darle su tono a los colores ya cargados
TONOS_CONOCIDOS = {
    'NEGRO': '#1B1B1B', 'BLANCO': '#F5F5F5', 'GRIS': '#9A9A9A',
    'PLOMO': '#9A9A9A', 'AZUL': '#1F4E9C', 'CELESTE': '#7FC4E8',
    'ROJO': '#C0342B', 'VERDE': '#2E7D46', 'AMARILLO': '#E8C33B',
    'NARANJA': '#E0762B', 'MARRON': '#6B4B32', 'BEIGE': '#D8C7AC',
    'CREMA': '#EFE4CE', 'ROSADO': '#E39BB4', 'MORADO': '#6B3FA0',
    'DORADO': '#C9A227', 'PLATEADO': '#C4C4C4',
}

# arranque de la paleta, para no empezar de cero
PALETA_INICIAL = dict(TONOS_CONOCIDOS, **{
    'VINO': '#6E1B2A', 'TURQUESA': '#2FA8A0', 'MOSTAZA': '#D9A521',
    'MILITAR': '#4A5A3A', 'CORAL': '#E96A5B', 'LILA': '#B79BD4',
})


def texto_a_paleta(apps, schema_editor):
    """ Cada nombre de color ya usado se vuelve una fila de la paleta """
    Color = apps.get_model('web', 'Color')
    Variante = apps.get_model('web', 'Variante')

    for nombre, tono in PALETA_INICIAL.items():
        Color.objects.get_or_create(nombre=nombre, defaults={'muestra': tono})

    for variante in Variante.objects.all():
        nombre = (variante.color or '').strip().upper() or 'SIN COLOR'
        color, _ = Color.objects.get_or_create(
            nombre=nombre,
            defaults={'muestra': variante.color_hex or TONOS_CONOCIDOS.get(nombre, '#CCCCCC')},
        )
        # si la variante traia un hex propio, ese manda sobre el de la tabla
        if variante.color_hex and color.muestra != variante.color_hex:
            color.muestra = variante.color_hex
            color.save(update_fields=['muestra'])

        variante.color_nuevo = color
        variante.save(update_fields=['color_nuevo'])


def paleta_a_texto(apps, schema_editor):
    """ Vuelta atras: devuelve el nombre y el tono a cada variante """
    Variante = apps.get_model('web', 'Variante')
    for variante in Variante.objects.select_related('color_nuevo'):
        if variante.color_nuevo_id:
            variante.color = variante.color_nuevo.nombre
            variante.color_hex = variante.color_nuevo.muestra
            variante.save(update_fields=['color', 'color_hex'])


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0012_curvas_por_categoria_genero'),
    ]

    operations = [
        migrations.CreateModel(
            name='Color',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=30, unique=True)),
                ('muestra', models.CharField(
                    default='#CCCCCC', max_length=7,
                    help_text='Como se ve el circulo en la tienda.',
                )),
            ],
            options={
                'verbose_name': 'Color',
                'verbose_name_plural': 'Colores',
                'ordering': ['nombre'],
            },
        ),

        migrations.AddField(
            model_name='variante',
            name='color_nuevo',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.RESTRICT,
                related_name='variantes', to='web.color',
            ),
        ),

        migrations.RunPython(texto_a_paleta, paleta_a_texto),

        migrations.RemoveField(model_name='variante', name='color'),
        migrations.RemoveField(model_name='variante', name='color_hex'),
        migrations.RenameField(model_name='variante', old_name='color_nuevo', new_name='color'),
        migrations.AlterField(
            model_name='variante',
            name='color',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='variantes', to='web.color',
            ),
        ),
        migrations.AlterModelOptions(
            name='variante',
            options={'ordering': ['producto', 'color']},
        ),
    ]
