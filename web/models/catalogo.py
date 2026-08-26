""" Catalogo: lo que se vende y su inventario """

from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Atributo(models.Model):
    """
    Lo que diferencia una unidad vendible de otra dentro del mismo color.
    En calzado y ropa es la talla; en celulares la capacidad; en TVs las pulgadas.
    Hay categorias que no usan ninguno (una licuadora solo tiene color).
    """
    nombre = models.CharField(max_length=40, unique=True)
    nombre_plural = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class ValorAtributo(models.Model):
    """ Un valor concreto: talla 40, capacidad 128GB, 55 pulgadas """
    atributo = models.ForeignKey(Atributo, on_delete=models.RESTRICT, related_name='valores')
    valor = models.CharField(max_length=30)
    orden = models.PositiveSmallIntegerField(default=0, help_text='Define el orden en que se muestran')

    class Meta:
        ordering = ['atributo', 'orden', 'valor']
        verbose_name = 'Valor de atributo'
        verbose_name_plural = 'Valores de atributo'
        constraints = [
            models.UniqueConstraint(fields=['atributo', 'valor'], name='unico_valor_por_atributo')
        ]

    def __str__(self):
        return self.valor


GENERO_CHOICES = (
    ('H', 'Hombre'),
    ('M', 'Mujer'),
    ('N', 'Nino'),
    ('A', 'Nina'),
    ('U', 'Unisex'),
)


class Curva(models.Model):
    """
    Un conjunto de valores que se compran juntos, para no cargarlos uno por uno.
    Ej. 'Dama' = 35, 35.5, 36 ... 38.5   |   'Nino' = 20, 21 ... 26

    Declara donde aplica, para que al registrar una zapatilla de mujer solo
    se ofrezcan las tallas de dama y no las 38 tallas del catalogo completo.
    """
    nombre = models.CharField(max_length=40, unique=True)
    atributo = models.ForeignKey(Atributo, on_delete=models.RESTRICT, related_name='curvas')
    categoria = models.ForeignKey(
        'web.Categoria', on_delete=models.CASCADE, null=True, blank=True, related_name='curvas',
        help_text='Dejar vacio para que sirva en cualquier categoria que use este atributo.'
    )
    genero = models.CharField(
        max_length=1, choices=GENERO_CHOICES, blank=True,
        help_text='Dejar vacio para que sirva en cualquier genero (ej. tallas S-XXL de ropa).'
    )
    valores = models.ManyToManyField(ValorAtributo, related_name='curvas')

    class Meta:
        ordering = ['atributo', 'nombre']
        verbose_name = 'Curva de valores'
        verbose_name_plural = 'Curvas de valores'

    def __str__(self):
        partes = [self.nombre]
        if self.genero:
            partes.append(self.get_genero_display())
        return ' - '.join(partes)

    def valores_ordenados(self):
        return self.valores.order_by('orden', 'valor')

    @classmethod
    def aplicables(cls, categoria, genero=''):
        """
        Las curvas que tienen sentido para esta categoria y genero, de lo
        mas especifico a lo mas general. Gana el primer nivel que tenga algo:

          1. curva de esta categoria y este genero   (ZAPATILLAS + Mujer -> Dama)
          2. curva de esta categoria sin genero      (ROPA -> S a XXL)
          3. curva comodin, sin categoria

        Asi una zapatilla de mujer no termina ofreciendo tallas de nino ni XL.
        """
        if categoria is None or categoria.atributo_id is None:
            return cls.objects.none()

        base = cls.objects.filter(atributo_id=categoria.atributo_id)

        if genero:
            exactas = base.filter(categoria=categoria, genero=genero)
            if exactas.exists():
                return exactas

        de_la_categoria = base.filter(categoria=categoria, genero='')
        if de_la_categoria.exists():
            return de_la_categoria

        return base.filter(categoria__isnull=True)


class Categoria(models.Model):
    nombre = models.CharField(max_length=30)
    slug = models.SlugField(max_length=50, unique=True, blank=True)
    atributo = models.ForeignKey(
        Atributo, on_delete=models.RESTRICT, null=True, blank=True, related_name='categorias',
        help_text='Que diferencia las unidades vendibles aqui. Vacio si no aplica (ej. licuadoras).'
    )
    usa_genero = models.BooleanField(
        'Usa genero', default=False,
        help_text='Marcar solo en moda (calzado, ropa). En tecnologia o electrodomesticos no aplica.'
    )
    activo = models.BooleanField(default=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Categorias'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)


class Color(models.Model):
    """
    Paleta de la tienda. Cada color se define una sola vez con su muestra,
    y las variantes lo eligen de una lista.

    Evita que 'NEGRO', 'Negro' y 'negr0' terminen siendo tres colores distintos,
    que es lo que rompe el filtro por color en el catalogo.
    """
    nombre = models.CharField(max_length=30, unique=True)
    muestra = models.CharField(
        max_length=7, default='#CCCCCC',
        help_text='Como se ve el circulo en la tienda.'
    )

    class Meta:
        ordering = ['nombre']
        verbose_name = 'Color'
        verbose_name_plural = 'Colores'

    def __str__(self):
        return self.nombre


class ProductoVisibleManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(activo=True)


class Producto(models.Model):
    """ Modelo base: lo que el cliente reconoce, ej. 'Adidas Campus 00s' """

    GENERO_CHOICES = GENERO_CHOICES

    categoria = models.ForeignKey(Categoria, on_delete=models.RESTRICT, related_name='productos')
    nombre = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    descripcion = models.TextField(blank=True)
    marca = models.CharField(max_length=50, blank=True)
    genero = models.CharField(
        max_length=1, choices=GENERO_CHOICES, blank=True,
        help_text='Solo para moda. Dejar vacio en electrodomesticos, tecnologia, etc.'
    )
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    objects = models.Manager()
    visibles = ProductoVisibleManager()

    class Meta:
        ordering = ['-creado']
        indexes = [models.Index(fields=['slug'])]

    def __str__(self):
        return self.nombre

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.genero and self.categoria_id and not self.categoria.usa_genero:
            raise ValidationError(
                {'genero': f'La categoria {self.categoria} no usa genero. Dejalo vacio.'}
            )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)

    def curvas_disponibles(self):
        return Curva.aplicables(self.categoria, self.genero)


class Variante(models.Model):
    """ Un color especifico de un producto. JP9163 = Campus 00s negro """
    producto = models.ForeignKey(Producto, on_delete=models.RESTRICT, related_name='variantes')
    sku = models.CharField('SKU', max_length=20, unique=True, db_index=True)
    color = models.ForeignKey(Color, on_delete=models.RESTRICT, related_name='variantes')
    precio_venta = models.DecimalField(max_digits=9, decimal_places=2)
    imagen = models.ImageField(upload_to='variantes', blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['producto', 'color']

    def __str__(self):
        return f'{self.producto.nombre} - {self.color} ({self.sku})'

    def campana_vigente(self):
        from .promociones import Campana

        ahora = timezone.now()
        return Campana.objects.filter(
            models.Q(variantes=self) | models.Q(categorias=self.producto.categoria),
            activo=True, fecha_inicio__lte=ahora, fecha_fin__gte=ahora,
        ).order_by('-porcentaje_descuento').first()

    def precio_actual(self):
        campana = self.campana_vigente()
        if not campana:
            return self.precio_venta
        descuento = self.precio_venta * campana.porcentaje_descuento / Decimal('100')
        return (self.precio_venta - descuento).quantize(Decimal('0.01'))
