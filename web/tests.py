from decimal import Decimal

from django.test import TestCase

from .models import Categoria, Producto


class CategoriaModelTests(TestCase):
    def test_str_returns_nombre(self):
        categoria = Categoria.objects.create(nombre='Lacteos')
        self.assertEqual(str(categoria), 'Lacteos')


class ProductoModelTests(TestCase):
    def test_str_returns_nombre(self):
        categoria = Categoria.objects.create(nombre='Lacteos')
        producto = Producto.objects.create(
            categoria=categoria,
            nombre='Queso de cabra',
            precio=Decimal('12.5'),
        )
        self.assertEqual(str(producto), 'Queso de cabra')
