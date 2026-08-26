import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def populate_categoria_slug(apps, schema_editor):
    Categoria = apps.get_model('web', 'Categoria')
    for categoria in Categoria.objects.all():
        categoria.slug = slugify(categoria.nombre)
        categoria.save(update_fields=['slug'])


def populate_producto_slug(apps, schema_editor):
    Producto = apps.get_model('web', 'Producto')
    for producto in Producto.objects.all():
        producto.slug = slugify(producto.nombre)
        producto.save(update_fields=['slug'])


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0003_pedido_pedidodetalle'),
    ]

    operations = [
        # --- Categoria ---
        migrations.AddField(
            model_name='categoria',
            name='slug',
            field=models.SlugField(blank=True, max_length=50, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='categoria',
            name='activo',
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(populate_categoria_slug, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='categoria',
            name='slug',
            field=models.SlugField(blank=True, max_length=50, unique=True),
        ),
        migrations.AlterModelOptions(
            name='categoria',
            options={'ordering': ['nombre'], 'verbose_name_plural': 'Categorias'},
        ),

        # --- Producto ---
        migrations.RemoveField(model_name='producto', name='precio'),
        migrations.RemoveField(model_name='producto', name='imagen'),
        migrations.RenameField(model_name='producto', old_name='fecha_registro', new_name='creado'),
        migrations.AlterField(
            model_name='producto',
            name='nombre',
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name='producto',
            name='descripcion',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='producto',
            name='slug',
            field=models.SlugField(blank=True, max_length=120, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='producto',
            name='marca',
            field=models.CharField(blank=True, default='', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producto',
            name='activo',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='producto',
            name='actualizado',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.RunPython(populate_producto_slug, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='producto',
            name='slug',
            field=models.SlugField(blank=True, max_length=120, unique=True),
        ),
        migrations.AlterField(
            model_name='producto',
            name='categoria',
            field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='productos', to='web.categoria'),
        ),
        migrations.AlterModelOptions(
            name='producto',
            options={'ordering': ['-creado']},
        ),
        migrations.AddIndex(
            model_name='producto',
            index=models.Index(fields=['slug'], name='web_product_slug_08bdd3_idx'),
        ),

        # --- Talla ---
        migrations.CreateModel(
            name='Talla',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor', models.CharField(max_length=10, unique=True)),
                ('orden', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'verbose_name_plural': 'Tallas',
                'ordering': ['orden', 'valor'],
            },
        ),

        # --- Variante ---
        migrations.CreateModel(
            name='Variante',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sku', models.CharField(db_index=True, max_length=20, unique=True, verbose_name='SKU')),
                ('color', models.CharField(max_length=30)),
                ('precio_venta', models.DecimalField(decimal_places=2, max_digits=9)),
                ('imagen', models.ImageField(blank=True, upload_to='variantes')),
                ('activo', models.BooleanField(default=True)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('producto', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='variantes', to='web.producto')),
            ],
            options={
                'ordering': ['producto', 'color'],
            },
        ),

        # --- VarianteTalla ---
        migrations.CreateModel(
            name='VarianteTalla',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('precio_venta_override', models.DecimalField(blank=True, decimal_places=2, help_text='Dejar vacio para usar el precio de la variante. Solo llenar si esta talla puntual cuesta distinto.', max_digits=9, null=True)),
                ('costo_promedio', models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ('stock', models.PositiveIntegerField(default=0)),
                ('talla', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='web.talla')),
                ('variante', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='tallas', to='web.variante')),
            ],
        ),
        migrations.AddConstraint(
            model_name='variantetalla',
            constraint=models.UniqueConstraint(fields=('variante', 'talla'), name='unico_variante_talla'),
        ),

        # --- Campana ---
        migrations.CreateModel(
            name='Campana',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=100)),
                ('fecha_inicio', models.DateTimeField()),
                ('fecha_fin', models.DateTimeField()),
                ('porcentaje_descuento', models.DecimalField(decimal_places=2, max_digits=5)),
                ('activo', models.BooleanField(default=True)),
                ('categorias', models.ManyToManyField(blank=True, related_name='campanas', to='web.categoria')),
                ('variantes', models.ManyToManyField(blank=True, related_name='campanas', to='web.variante')),
            ],
        ),

        # --- Cupon ---
        migrations.CreateModel(
            name='Cupon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.CharField(max_length=20, unique=True)),
                ('tipo', models.CharField(choices=[('P', 'Porcentaje'), ('M', 'Monto fijo')], default='P', max_length=1)),
                ('valor', models.DecimalField(decimal_places=2, max_digits=9)),
                ('monto_minimo_compra', models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ('fecha_inicio', models.DateTimeField()),
                ('fecha_fin', models.DateTimeField()),
                ('usos_maximos', models.PositiveIntegerField(blank=True, help_text='Vacio = ilimitado', null=True)),
                ('veces_usado', models.PositiveIntegerField(default=0)),
                ('activo', models.BooleanField(default=True)),
            ],
        ),

        # --- Cliente ---
        migrations.AlterField(
            model_name='cliente',
            name='fecha_nacimiento',
            field=models.DateField(blank=True, null=True),
        ),

        # --- Pedido ---
        migrations.AlterField(
            model_name='pedido',
            name='cliente',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='web.cliente'),
        ),
        migrations.AddField(
            model_name='pedido',
            name='nombre_comprador',
            field=models.CharField(default='', max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='apellido_comprador',
            field=models.CharField(default='', max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='email_comprador',
            field=models.EmailField(default='', max_length=254),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='telefono_comprador',
            field=models.CharField(default='', max_length=20),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='dni_comprador',
            field=models.CharField(blank=True, default='', max_length=8),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='pedido',
            name='nro_pedido',
            field=models.CharField(max_length=20, unique=True),
        ),
        migrations.AddField(
            model_name='pedido',
            name='direccion_envio',
            field=models.CharField(default='', max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='referencia_envio',
            field=models.CharField(blank=True, default='', max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='distrito_envio',
            field=models.CharField(default='', max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='provincia_envio',
            field=models.CharField(default='', max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='departamento_envio',
            field=models.CharField(default='', max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='telefono_envio',
            field=models.CharField(default='', max_length=20),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedido',
            name='cupon',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='web.cupon'),
        ),
        migrations.AddField(
            model_name='pedido',
            name='descuento_aplicado',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='pedido',
            name='actualizado',
            field=models.DateTimeField(auto_now=True),
        ),

        # --- PedidoDetalle ---
        migrations.RemoveField(model_name='pedidodetalle', name='producto'),
        migrations.AlterField(
            model_name='pedidodetalle',
            name='pedido',
            field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='detalles', to='web.pedido'),
        ),
        migrations.AddField(
            model_name='pedidodetalle',
            name='variante_talla',
            field=models.ForeignKey(default=None, on_delete=django.db.models.deletion.RESTRICT, to='web.variantetalla'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedidodetalle',
            name='sku',
            field=models.CharField(default='', max_length=30),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedidodetalle',
            name='nombre_producto',
            field=models.CharField(default='', max_length=150),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedidodetalle',
            name='talla',
            field=models.CharField(default='', max_length=10),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pedidodetalle',
            name='precio_unitario',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='pedidodetalle',
            name='cantidad',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
