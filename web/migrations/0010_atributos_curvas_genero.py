"""
Generaliza la talla a un 'atributo' por categoria.

Una tienda que vende zapatillas, celulares y licuadoras no puede exigir talla
en todo. Ahora cada categoria declara que atributo diferencia sus unidades
vendibles (talla, capacidad, pulgadas) o ninguno.

Las tallas ya cargadas se conservan: se convierten en valores del atributo 'Talla'.
"""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def tallas_a_valores(apps, schema_editor):
    """ Convierte cada Talla en un ValorAtributo del atributo 'Talla' """
    Talla = apps.get_model('web', 'Talla')
    Atributo = apps.get_model('web', 'Atributo')
    ValorAtributo = apps.get_model('web', 'ValorAtributo')
    Inventario = apps.get_model('web', 'Inventario')
    Categoria = apps.get_model('web', 'Categoria')

    if not Talla.objects.exists():
        return

    atributo = Atributo.objects.create(nombre='Talla', nombre_plural='Tallas')

    equivalencias = {}
    for talla in Talla.objects.all():
        equivalencias[talla.id] = ValorAtributo.objects.create(
            atributo=atributo, valor=talla.valor, orden=talla.orden
        )

    for item in Inventario.objects.all():
        item.valor = equivalencias[item.talla_id]
        item.save(update_fields=['valor'])

    # las categorias que ya tenian productos con talla pasan a usar el atributo Talla
    ids = set(
        Inventario.objects
        .values_list('variante__producto__categoria_id', flat=True)
        .distinct()
    )
    Categoria.objects.filter(id__in=ids).update(atributo=atributo)


def valores_a_tallas(apps, schema_editor):
    """ Vuelta atras: recrea las Tallas desde los valores del atributo 'Talla' """
    Talla = apps.get_model('web', 'Talla')
    Atributo = apps.get_model('web', 'Atributo')
    Inventario = apps.get_model('web', 'Inventario')

    atributo = Atributo.objects.filter(nombre='Talla').first()
    if atributo is None:
        return

    equivalencias = {}
    for valor in atributo.valores.all():
        talla, _ = Talla.objects.get_or_create(
            valor=valor.valor, defaults={'orden': valor.orden}
        )
        equivalencias[valor.id] = talla

    for item in Inventario.objects.exclude(valor__isnull=True):
        item.talla = equivalencias[item.valor_id]
        item.save(update_fields=['talla'])


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0009_variante_color_hex'),
    ]

    operations = [
        # --- modelos nuevos ---
        migrations.CreateModel(
            name='Atributo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=40, unique=True)),
                ('nombre_plural', models.CharField(blank=True, max_length=40)),
            ],
            options={'ordering': ['nombre']},
        ),
        migrations.CreateModel(
            name='ValorAtributo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor', models.CharField(max_length=30)),
                ('orden', models.PositiveSmallIntegerField(default=0, help_text='Define el orden en que se muestran')),
                ('atributo', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='valores', to='web.atributo')),
            ],
            options={
                'verbose_name': 'Valor de atributo',
                'verbose_name_plural': 'Valores de atributo',
                'ordering': ['atributo', 'orden', 'valor'],
            },
        ),
        migrations.AddConstraint(
            model_name='valoratributo',
            constraint=models.UniqueConstraint(fields=('atributo', 'valor'), name='unico_valor_por_atributo'),
        ),
        migrations.CreateModel(
            name='Curva',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=40, unique=True)),
                ('atributo', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='curvas', to='web.atributo')),
                ('valores', models.ManyToManyField(related_name='curvas', to='web.valoratributo')),
            ],
            options={
                'verbose_name': 'Curva de tallas',
                'verbose_name_plural': 'Curvas de tallas',
                'ordering': ['atributo', 'nombre'],
            },
        ),

        # --- categoria declara su atributo, producto su genero ---
        migrations.AddField(
            model_name='categoria',
            name='atributo',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT,
                related_name='categorias', to='web.atributo',
                help_text='Que diferencia las unidades vendibles aqui. Vacio si no aplica (ej. licuadoras).',
            ),
        ),
        migrations.AddField(
            model_name='producto',
            name='genero',
            field=models.CharField(
                blank=True, max_length=1,
                choices=[('H', 'Hombre'), ('M', 'Mujer'), ('N', 'Nino'), ('A', 'Nina'), ('U', 'Unisex')],
                help_text='Solo para moda. Dejar vacio en electrodomesticos, tecnologia, etc.',
            ),
        ),

        # --- VarianteTalla pasa a llamarse Inventario ---
        migrations.RemoveConstraint(model_name='variantetalla', name='unico_variante_talla'),
        migrations.RenameModel(old_name='VarianteTalla', new_name='Inventario'),
        migrations.AlterModelOptions(
            name='inventario',
            options={'ordering': ['variante', 'valor'], 'verbose_name': 'Inventario', 'verbose_name_plural': 'Inventario'},
        ),
        migrations.AlterField(
            model_name='inventario',
            name='variante',
            field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='items', to='web.variante'),
        ),
        migrations.AlterField(
            model_name='inventario',
            name='precio_venta_override',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=9, null=True,
                help_text='Dejar vacio para usar el precio de la variante. Solo si esta unidad cuesta distinto.',
            ),
        ),
        migrations.AddField(
            model_name='inventario',
            name='valor',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT,
                related_name='items', to='web.valoratributo',
                help_text='Talla, capacidad, pulgadas... Vacio si la categoria no usa atributo.',
            ),
        ),

        # --- pasar los datos y retirar Talla ---
        migrations.RunPython(tallas_a_valores, valores_a_tallas),
        migrations.RemoveField(model_name='inventario', name='talla'),
        migrations.DeleteModel(name='Talla'),

        migrations.AddConstraint(
            model_name='inventario',
            constraint=models.UniqueConstraint(
                condition=models.Q(('valor__isnull', False)),
                fields=('variante', 'valor'),
                name='unico_valor_por_variante',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventario',
            constraint=models.UniqueConstraint(
                condition=models.Q(('valor__isnull', True)),
                fields=('variante',),
                name='unica_fila_sin_valor',
            ),
        ),

        # --- los detalles apuntan a 'item', no a 'variante_talla' ---
        migrations.RenameField(model_name='compradetalle', old_name='variante_talla', new_name='item'),
        migrations.AlterField(
            model_name='compradetalle',
            name='cantidad',
            field=models.PositiveIntegerField(
                validators=[django.core.validators.MinValueValidator(1)],
                help_text='No registres lineas con cantidad 0: simplemente no incluyas esa fila en la boleta.',
            ),
        ),
        migrations.RenameField(model_name='pedidodetalle', old_name='variante_talla', new_name='item'),
        migrations.RenameField(model_name='pedidodetalle', old_name='talla', new_name='valor'),
        migrations.AlterField(
            model_name='pedidodetalle',
            name='valor',
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
