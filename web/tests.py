import os
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.core.management import call_command
from django.db import transaction
from django.db.models import RestrictedError
from django.db.utils import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import disponibilidad
from .disponibilidad import para_carrito, para_item
from .models import (
    Categoria,
    Cliente,
    Color,
    CuentaRecaudadora,
    Compra,
    CompraBloqueada,
    CompraDetalle,
    Cupon,
    Pedido,
    Producto,
    Proveedor,
    ValorAtributo,
    Variante,
    Atributo,
    Curva,
    Inventario,
    MovimientoInventario,
    Pago,
    PuntoRecojo,
    Traslado,
    TrasladoDetalle,
    Ubicacion,
)


def _color(nombre='Negro', muestra='#1B1B1B'):
    color, _ = Color.objects.get_or_create(nombre=nombre, defaults={'muestra': muestra})
    return color


def _atributo_talla():
    atributo, _ = Atributo.objects.get_or_create(nombre='Talla')
    return atributo


def _valor_talla(valor, orden):
    return ValorAtributo.objects.create(atributo=_atributo_talla(), valor=valor, orden=orden)


def _categoria_calzado(nombre='Calzado'):
    return Categoria.objects.create(nombre=nombre, atributo=_atributo_talla())


# dos carpetas distintas a proposito: los tests comprueban que el voucher NO
# cae bajo MEDIA_ROOT, y con una sola no se podria distinguir
MEDIA_TEMPORAL = tempfile.mkdtemp()
PRIVADO_TEMPORAL = tempfile.mkdtemp()

ARCHIVOS_APARTE = override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAL, PRIVADO_ROOT=PRIVADO_TEMPORAL
)


def tearDownModule():
    shutil.rmtree(MEDIA_TEMPORAL, ignore_errors=True)
    shutil.rmtree(PRIVADO_TEMPORAL, ignore_errors=True)


def _imagen(nombre='voucher.png', tamano=(40, 40)):
    """ Una imagen valida y minuscula, para no ensuciar los tests con archivos """
    from PIL import Image

    buffer = BytesIO()
    Image.new('RGB', tamano, '#DDDDDD').save(buffer, 'PNG')
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/png')


def _imagen_pesada():
    """ Ruido puro: el PNG no lo puede comprimir, asi que pasa el megabyte """
    from PIL import Image

    img = Image.frombytes('RGB', (900, 900), os.urandom(900 * 900 * 3))
    buffer = BytesIO()
    img.save(buffer, 'PNG')
    return SimpleUploadedFile('grande.png', buffer.getvalue(), content_type='image/png')


class CategoriaModelTests(TestCase):
    def test_str_returns_nombre(self):
        categoria = Categoria.objects.create(nombre='Calzado')
        self.assertEqual(str(categoria), 'Calzado')

    def test_slug_se_genera_solo(self):
        categoria = Categoria.objects.create(nombre='Ropa Deportiva')
        self.assertEqual(categoria.slug, 'ropa-deportiva')


class ProductoModelTests(TestCase):
    def test_str_returns_nombre(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        self.assertEqual(str(producto), 'Adidas Campus 00s')

    def test_slug_se_genera_solo(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        self.assertEqual(producto.slug, 'adidas-campus-00s')


class InventarioModelTests(TestCase):
    def setUp(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        self.variante = Variante.objects.create(
            producto=producto, sku='JP9163', color=_color('Negro'), precio_venta=Decimal('320.00')
        )
        self.talla = _valor_talla('40', 1)

    def test_precio_hereda_de_la_variante(self):
        vt = Inventario.objects.create(variante=self.variante, valor=self.talla)
        self.assertEqual(vt.precio_venta(), Decimal('320.00'))

    def test_precio_override_gana(self):
        vt = Inventario.objects.create(
            variante=self.variante, valor=self.talla, precio_venta_override=Decimal('350.00')
        )
        self.assertEqual(vt.precio_venta(), Decimal('350.00'))

    def test_registrar_compra_calcula_costo_promedio(self):
        vt = Inventario.objects.create(variante=self.variante, valor=self.talla)
        vt.registrar_compra(3, Decimal('300.00'))
        vt.registrar_compra(2, Decimal('320.00'))
        self.assertEqual(vt.stock, 5)
        self.assertEqual(vt.costo_promedio, Decimal('308.00'))


class CompraModelTests(TestCase):
    def setUp(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        variante = Variante.objects.create(
            producto=producto, sku='JP9163', color=_color('Negro'), precio_venta=Decimal('420.00')
        )
        self.t40 = Inventario.objects.create(
            variante=variante, valor=_valor_talla('40', 1)
        )
        self.t41 = Inventario.objects.create(
            variante=variante, valor=_valor_talla('41', 2)
        )
        self.proveedor = Proveedor.objects.create(razon_social='Importaciones SAC', ruc='20123456789')

    def _crear_compra(self, nro_documento, lineas):
        compra = Compra.objects.create(
            proveedor=self.proveedor, nro_documento=nro_documento, fecha_compra='2026-08-25'
        )
        for variante_talla, cantidad, costo in lineas:
            CompraDetalle.objects.create(
                compra=compra, item=variante_talla, cantidad=cantidad, costo_unitario=costo
            )
        return compra

    def test_subtotal_se_calcula_solo(self):
        compra = self._crear_compra('B001-1', [(self.t40, 3, Decimal('300.00'))])
        self.assertEqual(compra.detalles.first().subtotal, Decimal('900.00'))

    def test_recalcular_total_suma_las_lineas(self):
        compra = self._crear_compra(
            'B001-2', [(self.t40, 3, Decimal('300.00')), (self.t41, 2, Decimal('310.00'))]
        )
        self.assertEqual(compra.recalcular_total(), Decimal('1520.00'))

    def test_aplicar_a_inventario_sube_stock_y_costo(self):
        compra = self._crear_compra('B001-3', [(self.t40, 3, Decimal('300.00'))])
        compra.aplicar_a_inventario()
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)
        self.assertEqual(self.t40.costo_promedio, Decimal('300.00'))

    def test_dos_compras_promedian_el_costo(self):
        self._crear_compra('B001-4', [(self.t40, 3, Decimal('300.00'))]).aplicar_a_inventario()
        self._crear_compra('B001-5', [(self.t40, 2, Decimal('320.00'))]).aplicar_a_inventario()
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 5)
        self.assertEqual(self.t40.costo_promedio, Decimal('308.00'))

    def test_no_se_puede_aplicar_dos_veces(self):
        compra = self._crear_compra('B001-6', [(self.t40, 3, Decimal('300.00'))])
        compra.aplicar_a_inventario()
        with self.assertRaises(ValueError):
            compra.aplicar_a_inventario()
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)


DATOS_CHECKOUT = {
    'modo_entrega': 'E',
    'nombre': 'Deybis',
    'apellidos': 'Ccanto',
    'email': 'cliente@ejemplo.com',
    'telefono': '955134139',
    'dni': '10775394',
    'direccion': 'Av. Manchego Munoz 431',
    'referencia': 'Frente al parque',
    'departamento': 'Huancavelica',
    'provincia': 'Huancavelica',
    'distrito': 'Santa Ana',
}


class CheckoutTests(TestCase):
    def setUp(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        self.variante = Variante.objects.create(
            producto=producto, sku='JP9163', color=_color('Negro'), precio_venta=Decimal('429.00')
        )
        self.t40 = Inventario.objects.create(
            variante=self.variante, valor=_valor_talla('40', 1), stock=3
        )
        self.t41 = Inventario.objects.create(
            variante=self.variante, valor=_valor_talla('41', 2), stock=1
        )

    def _agregar(self, variante_talla, cantidad=1):
        return self.client.post(reverse('web:agregarCarrito'), {
            'item_id': variante_talla.id,
            'cantidad': cantidad,
            'sku': variante_talla.variante.sku,
        })

    def _confirmar(self, **cambios):
        # un invitado primero elige "continuar como invitado" en la pantalla previa
        if not self.client.session.get('_auth_user_id'):
            self.client.get(reverse('web:continuarComoInvitado'))
        datos = dict(DATOS_CHECKOUT)
        datos.update(cambios)
        return self.client.post(reverse('web:registrarPedido'), datos, follow=True)

    # --- invitado ---

    def test_invitado_puede_comprar_sin_cuenta(self):
        self._agregar(self.t40, 2)
        respuesta = self._confirmar()

        self.assertEqual(respuesta.status_code, 200)
        pedido = Pedido.objects.get()
        self.assertIsNone(pedido.cliente)
        self.assertTrue(pedido.es_invitado)
        self.assertEqual(pedido.email_comprador, 'cliente@ejemplo.com')
        self.assertEqual(pedido.monto_total, Decimal('858.00'))

    def test_confirmar_reserva_pero_no_descuenta(self):
        self._agregar(self.t40, 2)
        self._confirmar()

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)        # los pares siguen en el estante
        self.assertEqual(self.t40.reservado, 2)    # pero ya no se ofrecen
        self.assertEqual(self.t40.disponible, 1)

    def test_el_detalle_guarda_copia_del_producto(self):
        self._agregar(self.t40, 2)
        self._confirmar()

        detalle = Pedido.objects.get().detalles.get()
        self.assertEqual(detalle.sku, 'JP9163')
        self.assertEqual(detalle.nombre_producto, 'Adidas Campus 00s')
        self.assertEqual(detalle.valor, '40')
        self.assertEqual(detalle.precio_unitario, Decimal('429.00'))
        self.assertEqual(detalle.subtotal, Decimal('858.00'))

    def test_el_detalle_no_cambia_si_sube_el_precio_despues(self):
        self._agregar(self.t40, 1)
        self._confirmar()

        self.variante.precio_venta = Decimal('999.00')
        self.variante.save()

        detalle = Pedido.objects.get().detalles.get()
        self.assertEqual(detalle.precio_unitario, Decimal('429.00'))

    def test_el_carrito_queda_vacio_tras_comprar(self):
        self._agregar(self.t40, 1)
        self._confirmar()
        self.assertEqual(self.client.session.get('cart'), {})

    def test_confirmar_da_codigo_de_reserva_pero_no_numero(self):
        """
        Confirmar el checkout reserva unidades, no vende. El correlativo se
        guarda para las ventas de verdad: un carrito abandonado no lo gasta.
        """
        self._agregar(self.t40, 1)
        self._confirmar()

        pedido = Pedido.objects.get()
        self.assertIsNone(pedido.nro_pedido)
        self.assertRegex(pedido.codigo_reserva, r'^R-[0-9A-F]{8}$')
        self.assertEqual(pedido.referencia, pedido.codigo_reserva)

    def test_compra_de_varias_tallas_a_la_vez(self):
        self._agregar(self.t40, 2)
        self._agregar(self.t41, 1)
        self._confirmar()

        pedido = Pedido.objects.get()
        self.assertEqual(pedido.detalles.count(), 2)
        self.assertEqual(pedido.monto_total, Decimal('1287.00'))
        self.t40.refresh_from_db()
        self.t41.refresh_from_db()
        self.assertEqual(self.t40.disponible, 1)
        self.assertEqual(self.t41.disponible, 0)

    # --- cliente registrado ---

    def test_cliente_registrado_queda_ligado_al_pedido(self):
        usuario = User.objects.create_user(username='deybis', password='clave-de-prueba')
        cliente = Cliente.objects.create(usuario=usuario, dni='10775394', telefono='955134139')
        self.client.force_login(usuario)

        self._agregar(self.t40, 1)
        self._confirmar()

        pedido = Pedido.objects.get()
        self.assertEqual(pedido.cliente, cliente)
        self.assertFalse(pedido.es_invitado)

    # --- casos que deben fallar ---

    def test_no_vende_mas_de_lo_que_hay_en_stock(self):
        self._agregar(self.t41, 1)
        # otro cliente se lleva la ultima unidad justo antes de confirmar
        Inventario.objects.filter(pk=self.t41.pk).update(stock=0)

        self._confirmar()

        self.assertEqual(Pedido.objects.count(), 0)
        self.t41.refresh_from_db()
        self.assertEqual(self.t41.stock, 0)

    def test_no_confirma_si_el_precio_cambio(self):
        self._agregar(self.t40, 1)
        self.variante.precio_venta = Decimal('499.00')
        self.variante.save()

        self._confirmar()

        self.assertEqual(Pedido.objects.count(), 0)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)

    def test_no_confirma_si_el_producto_fue_desactivado(self):
        self._agregar(self.t40, 1)
        self.variante.activo = False
        self.variante.save()

        self._confirmar()

        self.assertEqual(Pedido.objects.count(), 0)

    def test_datos_incompletos_no_generan_pedido(self):
        self._agregar(self.t40, 1)
        respuesta = self._confirmar(email='', direccion='')

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Pedido.objects.count(), 0)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)

    def test_carrito_vacio_redirige_sin_crear_pedido(self):
        respuesta = self.client.get(reverse('web:registrarPedido'))
        self.assertRedirects(respuesta, reverse('web:carrito'))
        self.assertEqual(Pedido.objects.count(), 0)

    # --- confirmacion ---

    def test_gracias_muestra_el_pedido_recien_creado(self):
        self._agregar(self.t40, 1)
        self._confirmar()

        respuesta = self.client.get(reverse('web:gracias'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, Pedido.objects.get().referencia)

    def test_gracias_sin_pedido_previo_redirige_al_inicio(self):
        respuesta = self.client.get(reverse('web:gracias'))
        self.assertRedirects(respuesta, reverse('web:index'))


class IdentificacionTests(TestCase):
    def setUp(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        variante = Variante.objects.create(
            producto=producto, sku='JP9163', color=_color('Negro'), precio_venta=Decimal('429.00')
        )
        self.t40 = Inventario.objects.create(
            variante=variante, valor=_valor_talla('40', 1), stock=5
        )
        self.client.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 1, 'sku': 'JP9163',
        })

    def test_muestra_las_dos_opciones(self):
        respuesta = self.client.get(reverse('web:identificarse'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Iniciar sesion')
        self.assertContains(respuesta, 'Continuar como invitado')

    def test_usuario_logueado_salta_la_pantalla(self):
        usuario = User.objects.create_user(username='deybis', password='clave-de-prueba')
        self.client.force_login(usuario)
        respuesta = self.client.get(reverse('web:identificarse'))
        self.assertRedirects(respuesta, reverse('web:registrarPedido'))

    def test_checkout_sin_identificarse_redirige(self):
        respuesta = self.client.get(reverse('web:registrarPedido'))
        self.assertRedirects(respuesta, reverse('web:identificarse'))

    def test_continuar_como_invitado_da_acceso_al_checkout(self):
        self.client.get(reverse('web:continuarComoInvitado'))
        respuesta = self.client.get(reverse('web:registrarPedido'))
        self.assertEqual(respuesta.status_code, 200)

    def test_carrito_vacio_no_llega_a_identificacion(self):
        self.client.get(reverse('web:limpiarCarrito'))
        respuesta = self.client.get(reverse('web:identificarse'))
        self.assertRedirects(respuesta, reverse('web:carrito'))


class CuponTests(TestCase):
    def setUp(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        self.variante = Variante.objects.create(
            producto=producto, sku='JP9163', color=_color('Negro'), precio_venta=Decimal('100.00')
        )
        self.t40 = Inventario.objects.create(
            variante=self.variante, valor=_valor_talla('40', 1), stock=10
        )
        ahora = timezone.now()
        self.cupon = Cupon.objects.create(
            codigo='BIENVENIDA10', tipo='P', valor=Decimal('10.00'),
            fecha_inicio=ahora - timedelta(days=1), fecha_fin=ahora + timedelta(days=1),
        )

    def _agregar(self, cantidad=1):
        return self.client.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': cantidad, 'sku': 'JP9163',
        })

    def _aplicar(self, codigo):
        return self.client.post(reverse('web:aplicarCupon'), {'codigo': codigo}, follow=True)

    def _comprar(self):
        self.client.get(reverse('web:continuarComoInvitado'))
        return self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT, follow=True)

    # --- aplicar el cupon ---

    def test_cupon_porcentaje_descuenta(self):
        self._agregar(2)                          # 200.00
        respuesta = self._aplicar('BIENVENIDA10')
        self.assertContains(respuesta, 'BIENVENIDA10')
        self.assertContains(respuesta, '20.00')   # 10% de 200

    def test_codigo_en_minusculas_igual_funciona(self):
        self._agregar(1)
        self._aplicar('bienvenida10')
        self.assertEqual(self.client.session.get('cartCupon'), 'BIENVENIDA10')

    def test_cupon_de_monto_fijo(self):
        Cupon.objects.create(
            codigo='FIJO30', tipo='M', valor=Decimal('30.00'),
            fecha_inicio=timezone.now() - timedelta(days=1),
            fecha_fin=timezone.now() + timedelta(days=1),
        )
        self._agregar(1)                          # 100.00
        respuesta = self._aplicar('FIJO30')
        self.assertContains(respuesta, '70.00')   # total tras el descuento

    def test_cupon_inexistente_avisa(self):
        self._agregar(1)
        respuesta = self._aplicar('NOEXISTE')
        self.assertContains(respuesta, 'no existe')
        self.assertIsNone(self.client.session.get('cartCupon'))

    def test_cupon_vencido_se_rechaza(self):
        Cupon.objects.create(
            codigo='VENCIDO', tipo='P', valor=Decimal('50.00'),
            fecha_inicio=timezone.now() - timedelta(days=10),
            fecha_fin=timezone.now() - timedelta(days=5),
        )
        self._agregar(1)
        respuesta = self._aplicar('VENCIDO')
        self.assertContains(respuesta, 'fuera de fecha')

    def test_cupon_agotado_se_rechaza(self):
        Cupon.objects.create(
            codigo='AGOTADO', tipo='P', valor=Decimal('50.00'), usos_maximos=1, veces_usado=1,
            fecha_inicio=timezone.now() - timedelta(days=1),
            fecha_fin=timezone.now() + timedelta(days=1),
        )
        self._agregar(1)
        respuesta = self._aplicar('AGOTADO')
        self.assertContains(respuesta, 'limite de usos')

    def test_cupon_con_monto_minimo_no_alcanzado(self):
        Cupon.objects.create(
            codigo='MINIMO500', tipo='P', valor=Decimal('20.00'),
            monto_minimo_compra=Decimal('500.00'),
            fecha_inicio=timezone.now() - timedelta(days=1),
            fecha_fin=timezone.now() + timedelta(days=1),
        )
        self._agregar(1)                          # 100.00
        respuesta = self._aplicar('MINIMO500')
        self.assertContains(respuesta, 'compra minima')

    def test_descuento_no_puede_superar_la_compra(self):
        Cupon.objects.create(
            codigo='ENORME', tipo='M', valor=Decimal('999.00'),
            fecha_inicio=timezone.now() - timedelta(days=1),
            fecha_fin=timezone.now() + timedelta(days=1),
        )
        self._agregar(1)                          # 100.00
        self._aplicar('ENORME')
        self._comprar()
        self.assertEqual(Pedido.objects.get().monto_total, Decimal('0.00'))

    def test_quitar_cupon(self):
        self._agregar(1)
        self._aplicar('BIENVENIDA10')
        self.client.get(reverse('web:quitarCupon'))
        self.assertIsNone(self.client.session.get('cartCupon'))

    def test_cupon_deja_de_aplicar_si_el_carrito_baja_del_minimo(self):
        self.cupon.monto_minimo_compra = Decimal('150.00')
        self.cupon.save()
        self._agregar(2)                          # 200.00, alcanza el minimo
        self._aplicar('BIENVENIDA10')
        # el cliente reduce la cantidad y ya no alcanza el minimo
        self.client.post(reverse('web:actualizarCarrito', args=[self.t40.id]), {'cantidad': 1})
        respuesta = self.client.get(reverse('web:carrito'))
        self.assertContains(respuesta, 'compra minima')

    # --- al cerrar la venta ---

    def test_el_pedido_guarda_el_descuento(self):
        self._agregar(2)                          # 200.00
        self._aplicar('BIENVENIDA10')
        self._comprar()

        pedido = Pedido.objects.get()
        self.assertEqual(pedido.cupon, self.cupon)
        self.assertEqual(pedido.descuento_aplicado, Decimal('20.00'))
        self.assertEqual(pedido.monto_total, Decimal('180.00'))

    def test_comprar_suma_un_uso_al_cupon(self):
        self._agregar(1)
        self._aplicar('BIENVENIDA10')
        self._comprar()

        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.veces_usado, 1)

    def test_sin_cupon_el_pedido_no_lleva_descuento(self):
        self._agregar(1)
        self._comprar()

        pedido = Pedido.objects.get()
        self.assertIsNone(pedido.cupon)
        self.assertEqual(pedido.descuento_aplicado, Decimal('0'))
        self.assertEqual(pedido.monto_total, Decimal('100.00'))

    def test_cupon_vencido_entre_carrito_y_pago_detiene_la_venta(self):
        self._agregar(1)
        self._aplicar('BIENVENIDA10')
        # el cupon vence mientras el cliente llena sus datos
        self.cupon.fecha_fin = timezone.now() - timedelta(minutes=1)
        self.cupon.save()

        self._comprar()

        # no se cobra con un descuento invalido: la venta se detiene y no toca el stock
        self.assertEqual(Pedido.objects.count(), 0)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 10)


class AtributoPorCategoriaTests(TestCase):
    """ Una tienda multirubro: zapatillas con talla, celulares con capacidad, licuadoras sin nada """

    def setUp(self):
        self.talla = _atributo_talla()
        self.capacidad = Atributo.objects.create(nombre='Capacidad')

        self.calzado = _categoria_calzado()
        self.celulares = Categoria.objects.create(nombre='Celulares', atributo=self.capacidad)
        self.electro = Categoria.objects.create(nombre='Electrodomesticos')  # sin atributo

    def _variante(self, categoria, sku, precio='100.00'):
        producto = Producto.objects.create(categoria=categoria, nombre=f'Producto {sku}')
        return Variante.objects.create(
            producto=producto, sku=sku, color=_color('Negro'), precio_venta=Decimal(precio)
        )

    def test_celular_usa_capacidad_no_talla(self):
        variante = self._variante(self.celulares, 'CEL1')
        gb128 = ValorAtributo.objects.create(atributo=self.capacidad, valor='128GB', orden=1)
        item = Inventario.objects.create(variante=variante, valor=gb128, stock=5)

        self.assertEqual(item.etiqueta, '128GB')
        self.assertEqual(str(item.valor.atributo), 'Capacidad')

    def test_licuadora_no_necesita_valor(self):
        variante = self._variante(self.electro, 'LIC1')
        item = Inventario.objects.create(variante=variante, stock=4)

        self.assertIsNone(item.valor)
        self.assertEqual(item.etiqueta.upper(), 'NEGRO')
        self.assertTrue(item.disponible)

    def test_no_se_puede_poner_talla_a_un_celular(self):
        variante = self._variante(self.celulares, 'CEL2')
        talla40 = _valor_talla('40', 1)
        item = Inventario(variante=variante, valor=talla40)

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_una_zapatilla_exige_valor(self):
        variante = self._variante(self.calzado, 'ZAP1')
        item = Inventario(variante=variante)

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_licuadora_no_admite_dos_filas(self):
        variante = self._variante(self.electro, 'LIC2')
        Inventario.objects.create(variante=variante, stock=1)

        with self.assertRaises(ValidationError):
            Inventario(variante=variante).full_clean()

    def test_no_se_repite_el_mismo_valor_en_una_variante(self):
        variante = self._variante(self.calzado, 'ZAP2')
        talla40 = _valor_talla('40', 1)
        Inventario.objects.create(variante=variante, valor=talla40)

        with self.assertRaises(IntegrityError):
            Inventario.objects.create(variante=variante, valor=talla40)


class CurvaTests(TestCase):
    def setUp(self):
        self.atributo = _atributo_talla()
        self.dama = Curva.objects.create(nombre='Dama', atributo=self.atributo)
        for i, valor in enumerate(['35', '35.5', '36', '36.5', '37', '37.5', '38', '38.5'], start=1):
            self.dama.valores.add(ValorAtributo.objects.create(
                atributo=self.atributo, valor=valor, orden=i
            ))

        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Campus Dama')
        self.variante = Variante.objects.create(
            producto=producto, sku='DAMA1', color=_color('Blanco'), precio_venta=Decimal('399.00')
        )

    def _generar(self):
        creadas = 0
        for valor in self.dama.valores_ordenados():
            _, nuevo = Inventario.objects.get_or_create(variante=self.variante, valor=valor)
            creadas += 1 if nuevo else 0
        return creadas

    def test_la_curva_agrupa_sus_valores(self):
        self.assertEqual(self.dama.valores.count(), 8)
        self.assertEqual(
            [str(v) for v in self.dama.valores_ordenados()],
            ['35', '35.5', '36', '36.5', '37', '37.5', '38', '38.5'],
        )

    def test_generar_crea_todas_las_unidades_de_una_vez(self):
        self.assertEqual(self._generar(), 8)
        self.assertEqual(self.variante.items.count(), 8)

    def test_generar_dos_veces_no_duplica(self):
        self._generar()
        self.assertEqual(self._generar(), 0)
        self.assertEqual(self.variante.items.count(), 8)

    def test_se_puede_ajustar_despues_de_generar(self):
        self._generar()
        # de este modelo no compraste la 35: la quitas
        self.variante.items.filter(valor__valor='35').delete()
        self.assertEqual(self.variante.items.count(), 7)

    def test_las_unidades_generadas_nacen_sin_stock(self):
        self._generar()
        self.assertEqual(sum(i.stock for i in self.variante.items.all()), 0)


class GeneroProductoTests(TestCase):
    def test_producto_de_moda_lleva_genero(self):
        producto = Producto.objects.create(
            categoria=_categoria_calzado(), nombre='Campus Dama', genero='M'
        )
        self.assertEqual(producto.get_genero_display(), 'Mujer')

    def test_producto_de_tecnologia_va_sin_genero(self):
        categoria = Categoria.objects.create(nombre='Celulares')
        producto = Producto.objects.create(categoria=categoria, nombre='Telefono X')
        self.assertEqual(producto.genero, '')


class CarritoSinAtributoTests(TestCase):
    """ Comprar algo que no tiene talla (una licuadora) debe funcionar igual """

    def setUp(self):
        categoria = Categoria.objects.create(nombre='Electrodomesticos')
        producto = Producto.objects.create(categoria=categoria, nombre='Licuadora Oster')
        variante = Variante.objects.create(
            producto=producto, sku='LIC900', color=_color('Plateado'), precio_venta=Decimal('249.00')
        )
        self.item = Inventario.objects.create(variante=variante, stock=3)

    def test_se_agrega_al_carrito_sin_valor(self):
        self.client.post(reverse('web:agregarCarrito'), {
            'item_id': self.item.id, 'cantidad': 1, 'sku': 'LIC900',
        })
        linea = list(self.client.session['cart'].values())[0]
        self.assertEqual(linea['valor'], '')
        self.assertEqual(linea['nombre'], 'Licuadora Oster')

    def test_se_puede_comprar(self):
        self.client.post(reverse('web:agregarCarrito'), {
            'item_id': self.item.id, 'cantidad': 2, 'sku': 'LIC900',
        })
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT, follow=True)

        pedido = Pedido.objects.get()
        self.assertEqual(pedido.monto_total, Decimal('498.00'))
        self.assertEqual(pedido.detalles.get().valor, '')
        self.item.refresh_from_db()
        self.assertEqual(self.item.disponible, 1)

    def test_la_ficha_no_muestra_selector(self):
        respuesta = self.client.get(reverse('web:producto', args=['LIC900']))
        self.assertEqual(respuesta.status_code, 200)
        # sin botones de talla: la unidad va en un campo oculto
        self.assertNotContains(respuesta, 'id="item-')
        self.assertContains(respuesta, f'name="item_id" value="{self.item.id}"')
        self.assertContains(respuesta, 'Agregar al Carrito')

    def test_avisa_cuando_se_agota(self):
        respuesta = self.client.post(reverse('web:agregarCarrito'), {
            'item_id': self.item.id, 'cantidad': 5, 'sku': 'LIC900',
        }, follow=True)
        self.assertContains(respuesta, 'de color')


class ValoresOfrecidosTests(TestCase):
    """ Lo que se ofrece al registrar depende de la categoria y del genero """

    def setUp(self):
        self.talla = _atributo_talla()
        self.memoria = Atributo.objects.create(nombre='Memoria')

        self.zapatillas = Categoria.objects.create(
            nombre='Zapatillas', atributo=self.talla, usa_genero=True
        )
        self.ropa = Categoria.objects.create(nombre='Ropa', atributo=self.talla, usa_genero=True)
        self.celulares = Categoria.objects.create(nombre='Celulares', atributo=self.memoria)
        self.electro = Categoria.objects.create(nombre='Electrodomesticos')

        def curva(nombre, atributo, valores, categoria=None, genero=''):
            c = Curva.objects.create(
                nombre=nombre, atributo=atributo, categoria=categoria, genero=genero
            )
            for i, v in enumerate(valores, start=1):
                c.valores.add(ValorAtributo.objects.get_or_create(
                    atributo=atributo, valor=v, defaults={'orden': i}
                )[0])
            return c

        curva('Dama', self.talla, ['35', '36', '37', '38'], self.zapatillas, 'M')
        curva('Varon', self.talla, ['40', '41', '42', '43'], self.zapatillas, 'H')
        curva('Letras', self.talla, ['S', 'M', 'L', 'XL'], self.ropa)
        curva('Configs', self.memoria, ['8GB/128GB', '8GB/256GB'], self.celulares)

    def _producto(self, categoria, nombre, genero=''):
        return Producto.objects.create(categoria=categoria, nombre=nombre, genero=genero)

    def _valores(self, producto):
        return sorted(str(v) for v in Inventario.valores_validos(producto))

    def test_zapatilla_de_mujer_solo_ofrece_tallas_de_dama(self):
        producto = self._producto(self.zapatillas, 'Campus Dama', 'M')
        self.assertEqual(self._valores(producto), ['35', '36', '37', '38'])

    def test_zapatilla_de_hombre_solo_ofrece_tallas_de_hombre(self):
        producto = self._producto(self.zapatillas, 'Campus Varon', 'H')
        self.assertEqual(self._valores(producto), ['40', '41', '42', '43'])

    def test_una_zapatilla_nunca_ofrece_tallas_de_ropa(self):
        producto = self._producto(self.zapatillas, 'Campus Dama', 'M')
        self.assertNotIn('XL', self._valores(producto))

    def test_ropa_ofrece_letras_para_cualquier_genero(self):
        for genero in ['M', 'H']:
            producto = self._producto(self.ropa, f'Polo {genero}', genero)
            self.assertEqual(self._valores(producto), ['L', 'M', 'S', 'XL'])

    def test_celular_solo_ofrece_configuraciones_de_memoria(self):
        producto = self._producto(self.celulares, 'Galaxy S24')
        self.assertEqual(self._valores(producto), ['8GB/128GB', '8GB/256GB'])

    def test_celular_no_ofrece_tallas(self):
        producto = self._producto(self.celulares, 'Galaxy S24')
        self.assertNotIn('40', self._valores(producto))

    def test_licuadora_no_ofrece_nada(self):
        producto = self._producto(self.electro, 'Licuadora')
        self.assertEqual(self._valores(producto), [])

    def test_sin_genero_definido_se_ofrecen_todas_las_tallas(self):
        """ No se puede acotar hasta saber el genero: mejor mostrar todo que ocultar de mas """
        producto = self._producto(self.zapatillas, 'Campus sin definir')
        valores = self._valores(producto)
        self.assertIn('35', valores)
        self.assertIn('43', valores)


class CurvasAplicablesTests(TestCase):
    def setUp(self):
        self.talla = _atributo_talla()
        self.zapatillas = Categoria.objects.create(
            nombre='Zapatillas', atributo=self.talla, usa_genero=True
        )
        self.dama = Curva.objects.create(
            nombre='Dama', atributo=self.talla, categoria=self.zapatillas, genero='M'
        )
        self.varon = Curva.objects.create(
            nombre='Varon', atributo=self.talla, categoria=self.zapatillas, genero='H'
        )
        self.comodin = Curva.objects.create(nombre='General', atributo=self.talla)

    def test_gana_la_curva_del_genero_exacto(self):
        producto = Producto.objects.create(
            categoria=self.zapatillas, nombre='Campus', genero='M'
        )
        self.assertEqual([c.nombre for c in producto.curvas_disponibles()], ['Dama'])

    def test_otro_genero_recibe_su_propia_curva(self):
        producto = Producto.objects.create(
            categoria=self.zapatillas, nombre='Campus', genero='H'
        )
        self.assertEqual([c.nombre for c in producto.curvas_disponibles()], ['Varon'])

    def test_categoria_sin_atributo_no_tiene_curvas(self):
        electro = Categoria.objects.create(nombre='Electro')
        producto = Producto.objects.create(categoria=electro, nombre='Licuadora')
        self.assertEqual(list(producto.curvas_disponibles()), [])


class GeneroPorCategoriaTests(TestCase):
    def setUp(self):
        self.moda = Categoria.objects.create(
            nombre='Zapatillas', atributo=_atributo_talla(), usa_genero=True
        )
        self.tecno = Categoria.objects.create(nombre='Celulares')

    def test_moda_admite_genero(self):
        producto = Producto(categoria=self.moda, nombre='Campus', genero='M')
        producto.full_clean()   # no debe lanzar

    def test_tecnologia_rechaza_genero(self):
        producto = Producto(categoria=self.tecno, nombre='Galaxy', genero='M')
        with self.assertRaises(ValidationError):
            producto.full_clean()

    def test_tecnologia_sin_genero_es_valido(self):
        producto = Producto(categoria=self.tecno, nombre='Galaxy')
        producto.full_clean()   # no debe lanzar


class CarritoDeVersionAnteriorTests(TestCase):
    """
    Un cliente pudo dejar el carrito abierto antes de que la unidad de inventario
    cambiara de nombre. Al volver, la tienda no debe reventar.
    """

    def setUp(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        variante = Variante.objects.create(
            producto=producto, sku='JP9163', color=_color('Negro'), precio_venta=Decimal('429.00')
        )
        self.item = Inventario.objects.create(
            variante=variante, valor=_valor_talla('42', 1), stock=4
        )

    def _guardar_carrito(self, lineas):
        sesion = self.client.session
        sesion['cart'] = lineas
        sesion.save()

    def _linea_vieja(self):
        return {
            str(self.item.id): {
                'variante_talla_id': self.item.id,      # nombre anterior
                'sku': 'JP9163',
                'nombre': 'Adidas Campus 00s',
                'categoria': 'Calzado',
                'color': 'Negro',
                'talla': '42',                          # nombre anterior
                'imagen': '',
                'precio': '429.00',
                'cantidad': 2,
                'subtotal': '858.00',
            }
        }

    def test_el_carrito_viejo_no_rompe_la_pagina(self):
        self._guardar_carrito(self._linea_vieja())
        respuesta = self.client.get(reverse('web:carrito'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Adidas Campus 00s')

    def test_se_convierte_a_los_nombres_nuevos(self):
        self._guardar_carrito(self._linea_vieja())
        self.client.get(reverse('web:carrito'))

        linea = list(self.client.session['cart'].values())[0]
        self.assertEqual(linea['item_id'], self.item.id)
        self.assertEqual(linea['valor'], '42')
        self.assertEqual(linea['atributo'], 'Talla')
        self.assertNotIn('variante_talla_id', linea)
        self.assertNotIn('talla', linea)

    def test_conserva_cantidad_y_montos(self):
        self._guardar_carrito(self._linea_vieja())
        self.client.get(reverse('web:carrito'))

        linea = list(self.client.session['cart'].values())[0]
        self.assertEqual(linea['cantidad'], 2)
        self.assertEqual(linea['subtotal'], '858.00')

    def test_se_puede_seguir_comprando_desde_un_carrito_viejo(self):
        self._guardar_carrito(self._linea_vieja())
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT, follow=True)

        pedido = Pedido.objects.get()
        self.assertEqual(pedido.detalles.get().valor, '42')
        self.item.refresh_from_db()
        self.assertEqual(self.item.disponible, 2)

    def test_lineas_irreconocibles_se_descartan_sin_romper(self):
        self._guardar_carrito({
            'a': {'nombre': 'sin identificador'},
            'b': 'esto no es una linea',
            'c': None,
        })
        respuesta = self.client.get(reverse('web:carrito'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.client.session['cart'], {})

    def test_el_carrito_actual_no_se_altera(self):
        self.client.post(reverse('web:agregarCarrito'), {
            'item_id': self.item.id, 'cantidad': 1, 'sku': 'JP9163',
        })
        antes = dict(self.client.session['cart'])
        self.client.get(reverse('web:carrito'))
        self.assertEqual(self.client.session['cart'], antes)


class PaletaDeColoresTests(TestCase):
    """ El color se elige de una paleta compartida, no se escribe libremente """

    def setUp(self):
        self.categoria = _categoria_calzado()
        self.producto = Producto.objects.create(
            categoria=self.categoria, nombre='Campus', genero='M'
        )

    def _variante(self, sku, color):
        return Variante.objects.create(
            producto=self.producto, sku=sku, color=color, precio_venta=Decimal('399.00')
        )

    def test_el_color_define_su_muestra_una_sola_vez(self):
        vino = Color.objects.create(nombre='Vino Tinto', muestra='#6E1B2A')
        a = self._variante('SKU1', vino)
        b = self._variante('SKU2', vino)

        self.assertEqual(a.color.muestra, '#6E1B2A')
        self.assertEqual(b.color.muestra, '#6E1B2A')
        self.assertEqual(Color.objects.filter(nombre='Vino Tinto').count(), 1)

    def test_cambiar_la_muestra_afecta_a_todas_las_variantes(self):
        color = Color.objects.create(nombre='Aguamarina', muestra='#2FA8A0')
        variante = self._variante('SKU3', color)

        color.muestra = '#12908A'
        color.save()

        variante.refresh_from_db()
        self.assertEqual(variante.color.muestra, '#12908A')

    def test_cualquier_color_tiene_muestra_no_gris_por_defecto(self):
        """ Antes, un color fuera de la lista del codigo salia gris """
        color = Color.objects.create(nombre='Ocre', muestra='#D9A521')
        variante = self._variante('SKU4', color)
        self.assertEqual(variante.color.muestra, '#D9A521')

    def test_no_se_puede_repetir_el_nombre(self):
        Color.objects.create(nombre='Fucsia', muestra='#D9308F')
        with self.assertRaises(IntegrityError):
            Color.objects.create(nombre='Fucsia', muestra='#000000')

    def test_no_se_borra_un_color_en_uso(self):
        color = Color.objects.create(nombre='Arena', muestra='#D8C7AC')
        self._variante('SKU5', color)
        with self.assertRaises(RestrictedError):
            color.delete()

    def test_la_ficha_pinta_la_muestra_de_la_paleta(self):
        color = Color.objects.create(nombre='Salmon', muestra='#E96A5B')
        variante = self._variante('SKU6', color)
        Inventario.objects.create(
            variante=variante, valor=_valor_talla('36', 1), stock=3
        )

        respuesta = self.client.get(reverse('web:producto', args=['SKU6']))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'background:#E96A5B')
        self.assertContains(respuesta, 'Salmon')

    def test_el_carrito_guarda_el_nombre_del_color(self):
        color = Color.objects.create(nombre='Malva', muestra='#B79BD4')
        variante = self._variante('SKU7', color)
        item = Inventario.objects.create(
            variante=variante, valor=_valor_talla('37', 2), stock=2
        )

        self.client.post(reverse('web:agregarCarrito'), {
            'item_id': item.id, 'cantidad': 1, 'sku': 'SKU7',
        })

        linea = list(self.client.session['cart'].values())[0]
        self.assertEqual(linea['color'], 'Malva')

    def test_el_pedido_conserva_el_color_vendido(self):
        color = Color.objects.create(nombre='Menta', muestra='#8FD3B6')
        variante = self._variante('SKU8', color)
        item = Inventario.objects.create(
            variante=variante, valor=_valor_talla('38', 3), stock=2
        )

        self.client.post(reverse('web:agregarCarrito'), {
            'item_id': item.id, 'cantidad': 1, 'sku': 'SKU8',
        })
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT, follow=True)

        self.assertEqual(Pedido.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.disponible, 1)


# --- Fase 6: integridad del ciclo compra -> venta -------------------------


class _AlmacenMixin:
    """ Un producto con dos tallas y un proveedor: el punto de partida de todo """

    def _montar_almacen(self):
        categoria = _categoria_calzado()
        producto = Producto.objects.create(categoria=categoria, nombre='Adidas Campus 00s')
        self.variante = Variante.objects.create(
            producto=producto, sku='JP9163', color=_color('Negro'), precio_venta=Decimal('429.00')
        )
        self.t40 = Inventario.objects.create(variante=self.variante, valor=_valor_talla('40', 1))
        self.t41 = Inventario.objects.create(variante=self.variante, valor=_valor_talla('41', 2))
        self.proveedor = Proveedor.objects.create(razon_social='Importaciones SAC', ruc='20123456789')

    def _compra(self, nro_documento, lineas, aplicar=False):
        compra = Compra.objects.create(
            proveedor=self.proveedor, nro_documento=nro_documento, fecha_compra='2026-08-26'
        )
        for item, cantidad, costo in lineas:
            CompraDetalle.objects.create(
                compra=compra, item=item, cantidad=cantidad, costo_unitario=costo
            )
        if aplicar:
            compra.aplicar_a_inventario()
        return compra


class _VentaMixin:
    """ Arma pedidos reales pasando por el carrito y el checkout de invitado """

    def _agregar(self, item, cantidad=1):
        return self.client.post(reverse('web:agregarCarrito'), {
            'item_id': item.id, 'cantidad': cantidad, 'sku': item.variante.sku,
        })

    # el ciclo completo, en orden. Un pedido no salta pasos.
    CICLO = (Pedido.EN_VALIDACION, Pedido.PAGADO, Pedido.ENVIADO, Pedido.ENTREGADO)

    def _comprar(self):
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT, follow=True)
        return Pedido.objects.latest('id')

    def _pagar(self, pedido, nro='OP-PRUEBA'):
        """ Declara y valida el pago. Recien aca la reserva se vuelve venta. """
        cuenta = CuentaRecaudadora.objects.filter(activo=True).first()
        pago = pedido.declarar_pago(
            cuenta=cuenta,
            monto_declarado=pedido.monto_total,
            nro_operacion=nro,
            voucher=_imagen(),
            fecha_pago=timezone.localdate(),
        )
        return pago.validar(pedido.monto_total)

    def _avanzar(self, pedido, hasta):
        """ Camina el ciclo hasta el estado pedido, sin saltarse ninguno """
        for destino in self.CICLO:
            pedido.cambiar_estado(destino)
            if destino == hasta:
                break
        return pedido


@ARCHIVOS_APARTE
class KardexTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ Cada unidad que entra o sale deja una linea que explica de donde salio """

    def setUp(self):
        self._montar_almacen()

    def test_aplicar_una_compra_deja_su_movimiento(self):
        self._compra('B001-1', [(self.t40, 3, Decimal('300.00'))], aplicar=True)

        movimiento = self.t40.movimientos.get()
        self.assertEqual(movimiento.tipo, MovimientoInventario.COMPRA)
        self.assertEqual(movimiento.cantidad, 3)
        self.assertEqual(movimiento.stock_anterior, 0)
        self.assertEqual(movimiento.stock_resultante, 3)
        self.assertEqual(movimiento.costo_unitario, Decimal('300.00'))

    def test_el_movimiento_apunta_al_documento_que_lo_respalda(self):
        compra = self._compra('B001-2', [(self.t40, 3, Decimal('300.00'))], aplicar=True)

        movimiento = self.t40.movimientos.get()
        self.assertEqual(movimiento.compra, compra)
        self.assertIn('B001-2', movimiento.documento)

    def test_reservar_no_escribe_en_el_kardex(self):
        self._compra('B001-2b', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        pedido = self._comprar()

        # el par sigue en el estante: no hubo movimiento que registrar
        self.assertFalse(pedido.movimientos.exists())
        self.assertEqual(self.t40.movimientos.count(), 1)     # solo la compra

    def test_la_venta_aparece_recien_al_validar_el_pago(self):
        self._compra('B001-3', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        pedido = self._comprar()

        self.assertFalse(self.t40.movimientos.filter(tipo=MovimientoInventario.VENTA).exists())
        self._pagar(pedido)

        salida = self.t40.movimientos.get(tipo=MovimientoInventario.VENTA)
        self.assertEqual(salida.cantidad, -2)
        self.assertEqual(salida.stock_resultante, 1)
        self.assertEqual(salida.pedido, pedido)

    def test_el_kardex_se_lee_de_corrido(self):
        self._compra('B001-4', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        self._compra('B001-5', [(self.t40, 2, Decimal('320.00'))], aplicar=True)
        self._agregar(self.t40, 4)
        self._pagar(self._comprar())

        saldos = list(self.t40.movimientos.order_by('id').values_list('cantidad', 'stock_resultante'))
        self.assertEqual(saldos, [(3, 3), (2, 5), (-4, 1)])

    def test_el_stock_cuadra_con_la_suma_del_kardex(self):
        self._compra('B001-6', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        self._pagar(self._comprar())

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)
        self.assertEqual(self.t40.stock_segun_kardex, 3)

    def test_la_venta_no_mueve_el_costo_promedio(self):
        self._compra('B001-7', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        self._compra('B001-8', [(self.t40, 2, Decimal('320.00'))], aplicar=True)
        self._agregar(self.t40, 4)
        self._pagar(self._comprar())

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.costo_promedio, Decimal('308.00'))

    def test_un_movimiento_de_cero_unidades_se_rechaza(self):
        with self.assertRaises(ValueError):
            self.t40.registrar_movimiento(MovimientoInventario.AJUSTE, 0)

    def test_no_se_puede_sacar_mas_de_lo_que_hay(self):
        self._compra('B001-9', [(self.t40, 2, Decimal('300.00'))], aplicar=True)
        with self.assertRaises(ValueError):
            self.t40.descontar_stock(3)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 2)


class VerificarKardexTests(_AlmacenMixin, TestCase):
    """ El comando que avisa si el saldo cacheado y el historial se separaron """

    def setUp(self):
        self._montar_almacen()
        self._compra('B002-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)

    def _correr(self, *argumentos):
        salida = StringIO()
        call_command('verificar_kardex', *argumentos, stdout=salida)
        return salida.getvalue()

    def test_avisa_que_todo_cuadra(self):
        self.assertIn('cuadra con el stock', self._correr())

    def test_detecta_un_stock_movido_por_fuera(self):
        # un UPDATE directo saltea registrar_movimiento(), que es justo el caso
        # que el comando existe para encontrar
        Inventario.objects.filter(pk=self.t40.pk).update(stock=9)

        salida = self._correr()
        self.assertIn('stock 9, kardex 5', salida)
        self.assertIn('no cuadran', salida)

    def test_arreglar_registra_la_diferencia_sin_tocar_el_stock(self):
        Inventario.objects.filter(pk=self.t40.pk).update(stock=9)
        self._correr('--arreglar')

        self.t40.refresh_from_db()
        ajuste = self.t40.movimientos.get(tipo=MovimientoInventario.AJUSTE)
        self.assertEqual(ajuste.cantidad, 4)
        self.assertEqual(self.t40.stock, 9)
        self.assertEqual(self.t40.stock_segun_kardex, 9)
        self.assertIn('cuadra con el stock', self._correr())


class CompraAplicadaTests(_AlmacenMixin, TestCase):
    """
    Huecos 1 y 2 de la fase: una boleta que ya sumo stock no se edita ni se borra.
    Antes se podia, y el almacen quedaba diciendo algo distinto a la factura.
    """

    def setUp(self):
        self._montar_almacen()

    def test_editar_una_linea_ya_aplicada_se_rechaza(self):
        compra = self._compra('B003-1', [(self.t40, 3, Decimal('300.00'))], aplicar=True)

        detalle = compra.detalles.get()
        detalle.cantidad = 99
        with self.assertRaises(CompraBloqueada):
            detalle.save()

        detalle.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(detalle.cantidad, 3)
        self.assertEqual(self.t40.stock, 3)

    def test_borrar_una_linea_ya_aplicada_se_rechaza(self):
        compra = self._compra('B003-2', [(self.t40, 3, Decimal('300.00'))], aplicar=True)

        with self.assertRaises(CompraBloqueada):
            compra.detalles.get().delete()
        self.assertEqual(compra.detalles.count(), 1)

    def test_agregar_una_linea_a_una_compra_aplicada_se_rechaza(self):
        compra = self._compra('B003-3', [(self.t40, 3, Decimal('300.00'))], aplicar=True)

        with self.assertRaises(CompraBloqueada):
            CompraDetalle.objects.create(
                compra=compra, item=self.t41, cantidad=2, costo_unitario=Decimal('310.00')
            )
        self.t41.refresh_from_db()
        self.assertEqual(self.t41.stock, 0)

    def test_borrar_una_compra_aplicada_se_rechaza(self):
        compra = self._compra('B003-4', [(self.t40, 5, Decimal('300.00'))], aplicar=True)

        with self.assertRaises(CompraBloqueada):
            compra.delete()

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 5)
        self.assertTrue(Compra.objects.filter(pk=compra.pk).exists())

    def test_una_compra_en_borrador_si_se_corrige(self):
        compra = self._compra('B003-5', [(self.t40, 3, Decimal('300.00'))])

        detalle = compra.detalles.get()
        detalle.cantidad = 4
        detalle.save()

        compra.aplicar_a_inventario()
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 4)

    def test_una_compra_en_borrador_si_se_borra(self):
        compra = self._compra('B003-6', [(self.t40, 3, Decimal('300.00'))])
        compra.detalles.get().delete()
        compra.delete()
        self.assertFalse(Compra.objects.filter(pk=compra.pk).exists())


class AnularCompraTests(_AlmacenMixin, TestCase):
    """ La salida correcta cuando una boleta se registro mal """

    def setUp(self):
        self._montar_almacen()

    def test_anular_devuelve_el_stock(self):
        compra = self._compra('B004-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        compra.anular('Llegaron 5 pares menos de los facturados')

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 0)
        self.assertEqual(self.t40.stock_segun_kardex, 0)

    def test_anular_devuelve_el_costo_promedio_a_donde_estaba(self):
        self._compra('B004-2', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        cara = self._compra('B004-3', [(self.t40, 2, Decimal('500.00'))], aplicar=True)

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.costo_promedio, Decimal('380.00'))

        cara.anular('El proveedor facturo el costo equivocado')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)
        self.assertEqual(self.t40.costo_promedio, Decimal('300.00'))

    def test_la_boleta_anulada_sigue_existiendo_con_su_motivo(self):
        compra = self._compra('B004-4', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        compra.anular('Se registro dos veces la misma factura')

        compra.refresh_from_db()
        self.assertTrue(compra.anulado)
        self.assertEqual(compra.estado, 'Anulada')
        self.assertIsNotNone(compra.fecha_anulacion)
        self.assertIn('dos veces', compra.motivo_anulacion)
        self.assertEqual(compra.detalles.count(), 1)

    def test_el_kardex_conserva_la_entrada_y_la_salida(self):
        compra = self._compra('B004-5', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        compra.anular('Boleta equivocada')

        movimientos = list(self.t40.movimientos.order_by('id').values_list('tipo', 'cantidad'))
        self.assertEqual(movimientos, [
            (MovimientoInventario.COMPRA, 5),
            (MovimientoInventario.ANULA_COMPRA, -5),
        ])

    def test_no_se_anula_si_las_unidades_ya_se_vendieron(self):
        compra = self._compra('B004-6', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        self.t40.descontar_stock(2)

        with self.assertRaises(ValueError):
            compra.anular('Boleta equivocada')

        compra.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertFalse(compra.anulado)
        self.assertEqual(self.t40.stock, 1)

    def test_una_boleta_de_varias_lineas_no_se_anula_a_medias(self):
        compra = self._compra(
            'B004-7',
            [(self.t40, 3, Decimal('300.00')), (self.t41, 2, Decimal('310.00'))],
            aplicar=True,
        )
        self.t41.descontar_stock(2)      # la segunda linea ya no se puede devolver

        with self.assertRaises(ValueError):
            compra.anular('Boleta equivocada')

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 3)

    def test_anular_exige_un_motivo(self):
        compra = self._compra('B004-8', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        with self.assertRaises(ValueError):
            compra.anular('   ')

    def test_no_se_anula_dos_veces(self):
        compra = self._compra('B004-9', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        compra.anular('Boleta equivocada')

        with self.assertRaises(ValueError):
            compra.anular('Otra vez')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 0)

    def test_una_compra_en_borrador_se_borra_no_se_anula(self):
        compra = self._compra('B004-10', [(self.t40, 3, Decimal('300.00'))])
        with self.assertRaises(ValueError):
            compra.anular('Todavia no toco nada')

    def test_una_compra_anulada_no_se_vuelve_a_aplicar(self):
        compra = self._compra('B004-11', [(self.t40, 3, Decimal('300.00'))], aplicar=True)
        compra.anular('Boleta equivocada')

        with self.assertRaises(ValueError):
            compra.aplicar_a_inventario()


class EstadosPedidoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ El pedido avanza por su ciclo: no salta pasos ni retrocede """

    def setUp(self):
        self._montar_almacen()
        self._compra('B005-1', [(self.t40, 10, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        self.pedido = self._comprar()

    def test_nace_solicitado(self):
        self.assertEqual(self.pedido.estado, Pedido.SOLICITADO)

    def test_recorre_el_ciclo_completo(self):
        self._avanzar(self.pedido, Pedido.ENTREGADO)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.ENTREGADO)

    def test_no_salta_de_solicitado_a_entregado(self):
        with self.assertRaises(ValueError):
            self.pedido.cambiar_estado(Pedido.ENTREGADO)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.SOLICITADO)

    def test_no_retrocede(self):
        self._avanzar(self.pedido, Pedido.PAGADO)
        with self.assertRaises(ValueError):
            self.pedido.cambiar_estado(Pedido.SOLICITADO)

    def test_no_pasa_a_pagado_sin_pasar_por_validacion(self):
        with self.assertRaises(ValueError):
            self.pedido.cambiar_estado(Pedido.PAGADO)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.SOLICITADO)

    def test_un_pedido_entregado_ya_no_se_mueve(self):
        self._avanzar(self.pedido, Pedido.ENTREGADO)
        self.assertEqual(self.pedido.estados_siguientes, [])
        self.assertFalse(self.pedido.cancelable)


@ARCHIVOS_APARTE
class CancelarPedidoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ Hueco 3 de la fase: si el cliente se arrepiente, el stock vuelve """

    def setUp(self):
        self._montar_almacen()
        self._compra('B006-1', [(self.t40, 10, Decimal('300.00'))], aplicar=True)

    def test_cancelar_sin_pagar_solo_suelta_la_reserva(self):
        self._agregar(self.t40, 3)
        pedido = self._comprar()

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 10)          # nunca salieron del estante
        self.assertEqual(self.t40.disponible, 7)

        pedido.cancelar('El cliente se arrepintio')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 0)
        self.assertEqual(self.t40.disponible, 10)
        # y el kardex no se entero de nada: no hubo movimiento que registrar
        self.assertFalse(pedido.movimientos.exists())

    def test_cancelar_ya_pagado_si_devuelve_el_stock(self):
        self._agregar(self.t40, 3)
        pedido = self._comprar()
        self._pagar(pedido)

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 7)           # ahora si salieron

        pedido.cancelar('Llego roto y lo devolvio')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 10)
        self.assertEqual(self.t40.stock_segun_kardex, 10)

    def test_cancelar_no_altera_el_costo_promedio(self):
        self._agregar(self.t40, 3)
        pedido = self._comprar()
        self._pagar(pedido)
        pedido.cancelar('El cliente se arrepintio')

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.costo_promedio, Decimal('300.00'))

    def test_el_pedido_cancelado_conserva_su_detalle(self):
        self._agregar(self.t40, 2)
        pedido = self._comprar()
        pedido.cancelar('Direccion de envio inalcanzable')

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        self.assertIsNotNone(pedido.fecha_cancelacion)
        self.assertIn('inalcanzable', pedido.motivo_cancelacion)
        self.assertEqual(pedido.detalles.count(), 1)
        self.assertEqual(pedido.detalles.get().cantidad, 2)

    def test_el_kardex_explica_la_devolucion(self):
        self._agregar(self.t40, 2)
        pedido = self._comprar()
        self._pagar(pedido)
        pedido.cancelar('El cliente se arrepintio')

        vuelta = self.t40.movimientos.get(tipo=MovimientoInventario.CANCELA_VENTA)
        self.assertEqual(vuelta.cantidad, 2)
        self.assertEqual(vuelta.pedido, pedido)
        self.assertIn('arrepintio', vuelta.motivo)

    def test_cancelar_libera_el_uso_del_cupon(self):
        ahora = timezone.now()
        Cupon.objects.create(
            codigo='BIENVENIDA10', tipo='P', valor=Decimal('10.00'),
            fecha_inicio=ahora - timedelta(days=1), fecha_fin=ahora + timedelta(days=1),
        )
        self._agregar(self.t40, 2)
        self.client.post(reverse('web:aplicarCupon'), {'codigo': 'BIENVENIDA10'}, follow=True)
        pedido = self._comprar()

        cupon = Cupon.objects.get(codigo='BIENVENIDA10')
        self.assertEqual(cupon.veces_usado, 1)

        pedido.cancelar('El cliente se arrepintio')
        cupon.refresh_from_db()
        self.assertEqual(cupon.veces_usado, 0)

    def test_cancelar_exige_un_motivo(self):
        self._agregar(self.t40, 2)
        pedido = self._comprar()

        with self.assertRaises(ValueError):
            pedido.cancelar('')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 8)

    def test_no_se_cancela_dos_veces(self):
        self._agregar(self.t40, 2)
        pedido = self._comprar()
        pedido.cancelar('El cliente se arrepintio')

        with self.assertRaises(ValueError):
            pedido.cancelar('Otra vez')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 10)

    def test_un_pedido_entregado_no_se_cancela(self):
        self._agregar(self.t40, 2)
        pedido = self._comprar()
        self._avanzar(pedido, Pedido.ENTREGADO)

        with self.assertRaises(ValueError):
            pedido.cancelar('Tarde')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 8)

    def test_un_pedido_pagado_si_se_cancela(self):
        self._agregar(self.t40, 2)
        pedido = self._comprar()
        self._avanzar(pedido, Pedido.PAGADO)
        pedido.cambiar_estado(Pedido.CANCELADO, motivo='Se cayo el pago')

        pedido.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        self.assertEqual(self.t40.stock, 10)


class AdminFase6Tests(_AlmacenMixin, _VentaMixin, TestCase):
    """ Las mismas reglas, pero por donde las va a usar el dueno de la tienda """

    def setUp(self):
        self._montar_almacen()
        self.client.force_login(
            User.objects.create_superuser('jefe', 'jefe@goatx.pe', 'clave-de-prueba')
        )

    # --- kardex ---

    def test_el_kardex_se_puede_leer(self):
        self._compra('B007-1', [(self.t40, 4, Decimal('300.00'))], aplicar=True)

        respuesta = self.client.get(reverse('admin:web_movimientoinventario_changelist'))
        self.assertContains(respuesta, 'JP9163')
        self.assertContains(respuesta, 'B007-1')

    def test_el_kardex_no_se_escribe_a_mano(self):
        respuesta = self.client.get(reverse('admin:web_movimientoinventario_add'))
        self.assertEqual(respuesta.status_code, 403)

    def test_el_inventario_enlaza_a_su_historial(self):
        self._compra('B007-2', [(self.t40, 4, Decimal('300.00'))], aplicar=True)

        respuesta = self.client.get(reverse('admin:web_inventario_changelist'))
        self.assertContains(respuesta, 'ver historial')
        # los totales de la pantalla siguen en pie (ya se perdieron una vez)
        self.assertEqual(respuesta.context['total_unidades'], 4)

    # --- compras ---

    def test_una_compra_aplicada_se_muestra_congelada(self):
        compra = self._compra('B007-3', [(self.t40, 4, Decimal('300.00'))], aplicar=True)

        respuesta = self.client.get(reverse('admin:web_compra_change', args=[compra.pk]))
        self.assertNotContains(respuesta, 'name="nro_documento"')
        self.assertNotContains(respuesta, '-0-cantidad')

    def test_una_compra_en_borrador_se_deja_editar(self):
        compra = self._compra('B007-4', [(self.t40, 4, Decimal('300.00'))])

        respuesta = self.client.get(reverse('admin:web_compra_change', args=[compra.pk]))
        self.assertContains(respuesta, 'name="nro_documento"')
        self.assertContains(respuesta, '-0-cantidad')

    def test_una_compra_aplicada_no_se_borra(self):
        compra = self._compra('B007-5', [(self.t40, 4, Decimal('300.00'))], aplicar=True)

        respuesta = self.client.get(reverse('admin:web_compra_delete', args=[compra.pk]))
        self.assertEqual(respuesta.status_code, 403)

    def test_anular_pide_el_motivo_antes_de_mover_nada(self):
        compra = self._compra('B007-6', [(self.t40, 4, Decimal('300.00'))], aplicar=True)

        respuesta = self.client.post(reverse('admin:web_compra_changelist'), {
            'action': 'anular_compra', '_selected_action': [compra.pk],
        })
        self.assertContains(respuesta, 'Anular compras')

        compra.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertFalse(compra.anulado)
        self.assertEqual(self.t40.stock, 4)

    def test_anular_desde_el_admin_devuelve_el_stock(self):
        compra = self._compra('B007-7', [(self.t40, 4, Decimal('300.00'))], aplicar=True)

        self.client.post(reverse('admin:web_compra_changelist'), {
            'action': 'anular_compra', '_selected_action': [compra.pk],
            'aplicar': 'Anular', 'motivo': 'La boleta se registro dos veces',
        })

        compra.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertTrue(compra.anulado)
        self.assertEqual(self.t40.stock, 0)
        self.assertEqual(self.t40.stock_segun_kardex, 0)

    def test_aplicar_desde_el_admin_deja_el_movimiento_firmado(self):
        compra = self._compra('B007-8', [(self.t40, 4, Decimal('300.00'))])

        self.client.post(reverse('admin:web_compra_changelist'), {
            'action': 'aplicar_al_inventario', '_selected_action': [compra.pk],
        })

        movimiento = self.t40.movimientos.get()
        self.assertEqual(movimiento.usuario.username, 'jefe')
        self.assertEqual(movimiento.compra, compra)

    # --- pedidos ---

    def test_el_estado_del_pedido_no_se_edita_a_mano(self):
        self._compra('B007-9', [(self.t40, 6, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        pedido = self._comprar()

        respuesta = self.client.get(reverse('admin:web_pedido_change', args=[pedido.pk]))
        self.assertNotContains(respuesta, 'name="estado"')

    def test_un_pedido_no_se_borra(self):
        self._compra('B007-10', [(self.t40, 6, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        pedido = self._comprar()

        respuesta = self.client.get(reverse('admin:web_pedido_delete', args=[pedido.pk]))
        self.assertEqual(respuesta.status_code, 403)

    def test_marcar_enviado_desde_el_admin(self):
        self._compra('B007-11', [(self.t40, 6, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        pedido = self._comprar()
        self._avanzar(pedido, Pedido.PAGADO)

        self.client.post(reverse('admin:web_pedido_changelist'), {
            'action': 'marcar_enviado', '_selected_action': [pedido.pk],
        })
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.ENVIADO)

    def test_cancelar_desde_el_admin_devuelve_el_stock(self):
        self._compra('B007-12', [(self.t40, 6, Decimal('300.00'))], aplicar=True)
        self._agregar(self.t40, 2)
        pedido = self._comprar()

        self.client.post(reverse('admin:web_pedido_changelist'), {
            'action': 'cancelar_pedido', '_selected_action': [pedido.pk],
            'aplicar': 'Cancelar pedidos', 'motivo': 'El cliente se arrepintio',
        })

        pedido.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        self.assertEqual(self.t40.stock, 6)


# --- Fase 7: cobro por transferencia -------------------------------------

def _cuenta_bcp():
    return CuentaRecaudadora.objects.create(
        metodo=CuentaRecaudadora.BANCO, titular='Monetix Retail',
        banco='BCP', tipo_cuenta='CORRIENTE', numero='3507296754036',
    )


def _cuenta_yape():
    return CuentaRecaudadora.objects.create(
        metodo=CuentaRecaudadora.YAPE, titular='Monetix Retail', telefono='955134139',
    )


class CuentaRecaudadoraTests(TestCase):
    """ Cada metodo de cobro pide sus propios datos """

    def test_una_cuenta_bancaria_sin_numero_se_rechaza(self):
        cuenta = CuentaRecaudadora(
            metodo=CuentaRecaudadora.BANCO, titular='Monetix Retail',
            banco='BCP', tipo_cuenta='CORRIENTE',
        )
        with self.assertRaises(ValidationError):
            cuenta.full_clean()

    def test_yape_sin_telefono_se_rechaza(self):
        cuenta = CuentaRecaudadora(metodo=CuentaRecaudadora.YAPE, titular='Monetix Retail')
        with self.assertRaises(ValidationError):
            cuenta.full_clean()

    def test_un_qr_sin_imagen_se_rechaza(self):
        cuenta = CuentaRecaudadora(metodo=CuentaRecaudadora.QR, titular='Monetix Retail')
        with self.assertRaises(ValidationError):
            cuenta.full_clean()

    def test_la_etiqueta_dice_lo_justo(self):
        self.assertEqual(_cuenta_bcp().etiqueta, 'BCP · Corriente')
        self.assertEqual(_cuenta_yape().etiqueta, 'Yape')


@ARCHIVOS_APARTE
class PagoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ El comprobante es una declaracion hasta que alguien lo confirma """

    def setUp(self):
        self._montar_almacen()
        self._compra('B008-1', [(self.t40, 10, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        self._agregar(self.t40, 1)
        self.pedido = self._comprar()

    def _declarar(self, nro='OP-0001', monto=None, cuenta=None):
        return self.pedido.declarar_pago(
            cuenta=cuenta or self.cuenta,
            monto_declarado=monto if monto is not None else self.pedido.monto_total,
            nro_operacion=nro,
            voucher=_imagen(),
            fecha_pago=timezone.localdate(),
        )

    def test_declarar_un_pago_pone_el_pedido_en_validacion(self):
        self.assertEqual(self.pedido.estado, Pedido.SOLICITADO)
        self._declarar()

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.EN_VALIDACION)

    def test_declarar_no_da_nada_por_cobrado(self):
        pago = self._declarar()
        self.assertEqual(pago.estado, Pago.PENDIENTE)
        self.assertIsNone(pago.monto_confirmado)
        self.assertIsNone(pago.diferencia)

    def test_validar_mueve_el_pedido_a_pagado(self):
        pago = self._declarar()
        pago.validar(self.pedido.monto_total)

        self.pedido.refresh_from_db()
        self.assertEqual(pago.estado, Pago.VALIDADO)
        self.assertEqual(self.pedido.estado, Pedido.PAGADO)
        self.assertTrue(pago.cuadra)

    def test_validar_exige_el_monto_que_se_vio(self):
        pago = self._declarar()
        with self.assertRaises(ValueError):
            pago.validar(None)

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.EN_VALIDACION)

    def test_validar_registra_la_diferencia(self):
        pago = self._declarar(monto=Decimal('429.00'))
        pago.validar(Decimal('420.00'))          # entro menos de lo que decia el pedido

        self.assertEqual(pago.diferencia, Decimal('420.00') - self.pedido.monto_total)
        self.assertFalse(pago.cuadra)

    def test_rechazar_cancela_el_pedido_y_suelta_las_unidades(self):
        """
        Un comprobante rechazado es un pedido sin pago detras, y no puede
        retener inventario. Con la validacion sin vencimiento, dejarlo abierto
        congelaba la unidad para siempre.
        """
        pago = self._declarar()
        pago.rechazar('La captura esta borrosa, no se lee el monto')

        self.pedido.refresh_from_db()
        self.assertEqual(pago.estado, Pago.RECHAZADO)
        self.assertEqual(self.pedido.estado, Pedido.CANCELADO)
        self.assertIn('rechazado', self.pedido.motivo_cancelacion.lower())
        self.assertIn('borrosa', self.pedido.ultimo_rechazo.motivo_rechazo)

    def test_rechazar_exige_un_motivo(self):
        pago = self._declarar()
        with self.assertRaises(ValueError):
            pago.rechazar('   ')

    def test_un_pago_validado_ya_no_se_rechaza(self):
        pago = self._declarar()
        pago.validar(self.pedido.monto_total)
        with self.assertRaises(ValueError):
            pago.rechazar('Me arrepenti')

    def test_el_mismo_comprobante_no_se_usa_dos_veces(self):
        self._declarar('OP-0001')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._declarar('OP-0001')

    def test_el_mismo_numero_en_otra_cuenta_si_pasa(self):
        self._declarar('OP-0001')
        self._declarar('OP-0001', cuenta=_cuenta_yape())
        self.assertEqual(self.pedido.pagos.count(), 2)

    def test_un_pedido_despachado_no_admite_comprobantes(self):
        self._pagar(self.pedido, nro='OP-YA')
        self.pedido.refresh_from_db()
        self.pedido.cambiar_estado(Pedido.ENVIADO)
        with self.assertRaises(ValueError):
            self._declarar(nro='OP-TARDE')


@ARCHIVOS_APARTE
class CheckoutPagoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ El paso de pago tal como lo recorre el cliente """

    def setUp(self):
        self._montar_almacen()
        self._compra('B009-1', [(self.t40, 10, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()

    def _formulario(self, **cambios):
        datos = {
            'cuenta': self.cuenta.id,
            'monto_declarado': '429.00',
            'nro_operacion': 'OP-7788',
            'fecha_pago': timezone.localdate().isoformat(),
            'voucher': _imagen(),
        }
        datos.update(cambios)
        return datos

    def test_confirmar_el_pedido_lleva_a_la_pantalla_de_pago(self):
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        respuesta = self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)
        self.assertRedirects(respuesta, reverse('web:pagoPedido'))

    def test_la_pantalla_muestra_las_cuentas_activas(self):
        self._agregar(self.t40, 1)
        self._comprar()

        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertContains(respuesta, '3507296754036')
        self.assertContains(respuesta, 'Monetix Retail')

    def test_solo_se_ofrecen_las_cuentas_activas(self):
        # la migracion 0017 siembra cuentas, asi que se apagan todas y queda una
        CuentaRecaudadora.objects.update(activo=False)
        CuentaRecaudadora.objects.create(
            metodo=CuentaRecaudadora.BANCO, titular='Monetix Retail',
            banco='Interbank', tipo_cuenta='AHORROS', numero='8881112223334',
        )
        self._agregar(self.t40, 1)
        self._comprar()

        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertContains(respuesta, '8881112223334')
        self.assertNotContains(respuesta, '3507296754036')

    def test_sin_pedido_en_sesion_no_hay_pantalla_de_pago(self):
        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertRedirects(respuesta, reverse('web:index'))

    def test_subir_el_comprobante_deja_el_pedido_en_validacion(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()

        respuesta = self.client.post(reverse('web:pagoPedido'), self._formulario())
        self.assertRedirects(respuesta, reverse('web:gracias'))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EN_VALIDACION)
        self.assertEqual(pedido.pagos.get().nro_operacion, 'OP-7788')

    def test_un_comprobante_de_mas_de_un_mega_se_rechaza(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()

        respuesta = self.client.post(
            reverse('web:pagoPedido'), self._formulario(voucher=_imagen_pesada())
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'El maximo es 1 MB')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.SOLICITADO)

    def test_un_numero_de_operacion_repetido_se_avisa(self):
        self._agregar(self.t40, 1)
        self._comprar()
        self.client.post(reverse('web:pagoPedido'), self._formulario())

        # otro pedido intentando declarar el mismo comprobante
        self._agregar(self.t40, 1)
        self._comprar()
        respuesta = self.client.post(reverse('web:pagoPedido'), self._formulario())
        self.assertContains(respuesta, 'ya fue registrado')

    def test_una_fecha_futura_se_rechaza(self):
        self._agregar(self.t40, 1)
        self._comprar()

        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        respuesta = self.client.post(
            reverse('web:pagoPedido'), self._formulario(fecha_pago=manana)
        )
        self.assertContains(respuesta, 'todavia no llego')

    def test_con_un_comprobante_pendiente_no_se_sube_otro(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        self.client.post(reverse('web:pagoPedido'), self._formulario())

        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertContains(respuesta, 'Recibimos tu comprobante')
        self.assertNotContains(respuesta, 'Enviar comprobante')
        self.assertEqual(pedido.pagos.count(), 1)


@ARCHIVOS_APARTE
class VoucherProtegidoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El voucher es el pantallazo bancario del cliente. No puede quedar suelto en
    /media/, donde cualquiera con la URL lo lee.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B010-1', [(self.t40, 10, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        self._agregar(self.t40, 1)
        self.pedido = self._comprar()
        self.pago = self.pedido.declarar_pago(
            cuenta=self.cuenta,
            monto_declarado=self.pedido.monto_total,
            nro_operacion='OP-5555',
            voucher=_imagen(),
            fecha_pago=timezone.localdate(),
        )
        self.url = reverse('web:voucherPago', args=[self.pago.id])

    def test_quien_hizo_el_pedido_lo_ve(self):
        # la sesion del test es la del comprador que acaba de subirlo
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_un_extrano_no_lo_ve(self):
        otro = Client()
        self.assertEqual(otro.get(self.url).status_code, 404)

    def test_un_usuario_cualquiera_tampoco(self):
        otro = Client()
        User.objects.create_user('curioso', 'curioso@ejemplo.com', 'clave-de-prueba')
        otro.login(username='curioso', password='clave-de-prueba')
        self.assertEqual(otro.get(self.url).status_code, 404)

    def test_el_staff_si_lo_ve(self):
        equipo = Client()
        equipo.force_login(
            User.objects.create_superuser('jefa', 'jefa@goatx.pe', 'clave-de-prueba')
        )
        self.assertEqual(equipo.get(self.url).status_code, 200)

    def test_el_archivo_no_vive_bajo_media(self):
        """
        Lo que esta bajo MEDIA_ROOT lo entrega Django en desarrollo y Nginx en
        produccion sin preguntar quien pide. La vista con permisos no sirve de
        nada si el archivo tambien se puede pedir por su ruta.
        """
        ruta = os.path.abspath(self.pago.voucher.path)
        self.assertFalse(ruta.startswith(os.path.abspath(MEDIA_TEMPORAL)))
        self.assertTrue(ruta.startswith(os.path.abspath(PRIVADO_TEMPORAL)))

    def test_el_voucher_no_tiene_url_publica(self):
        with self.assertRaises(ValueError):
            self.pago.voucher.url

    def test_un_voucher_inexistente_da_404(self):
        equipo = Client()
        equipo.force_login(
            User.objects.create_superuser('jefe2', 'jefe2@goatx.pe', 'clave-de-prueba')
        )
        self.assertEqual(equipo.get(reverse('web:voucherPago', args=[99999])).status_code, 404)


@ARCHIVOS_APARTE
class AdminPagoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ La bandeja de comprobantes, por donde se validan """

    def setUp(self):
        self._montar_almacen()
        self._compra('B011-1', [(self.t40, 10, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        self._agregar(self.t40, 1)
        self.pedido = self._comprar()
        self.pago = self.pedido.declarar_pago(
            cuenta=self.cuenta,
            monto_declarado=self.pedido.monto_total,
            nro_operacion='OP-9001',
            voucher=_imagen(),
            fecha_pago=timezone.localdate(),
        )
        self.client.force_login(
            User.objects.create_superuser('cajera', 'cajera@goatx.pe', 'clave-de-prueba')
        )

    def test_la_bandeja_lista_los_comprobantes(self):
        respuesta = self.client.get(reverse('admin:web_pago_changelist'))
        self.assertContains(respuesta, 'OP-9001')
        self.assertContains(respuesta, self.pedido.nro_pedido)

    def test_la_ficha_del_comprobante_abre_sin_romperse(self):
        # el voucher ya no tiene .url: si el admin la pidiera al pintarlo como
        # campo de solo lectura, esta pantalla reventaria
        respuesta = self.client.get(reverse('admin:web_pago_change', args=[self.pago.pk]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'OP-9001')

    def test_un_comprobante_no_se_crea_a_mano(self):
        respuesta = self.client.get(reverse('admin:web_pago_add'))
        self.assertEqual(respuesta.status_code, 403)

    def test_un_comprobante_no_se_borra(self):
        respuesta = self.client.get(reverse('admin:web_pago_delete', args=[self.pago.pk]))
        self.assertEqual(respuesta.status_code, 403)

    def test_validar_pide_el_monto_antes_de_mover_nada(self):
        respuesta = self.client.post(reverse('admin:web_pago_changelist'), {
            'action': 'validar_pago', '_selected_action': [self.pago.pk],
        })
        self.assertContains(respuesta, 'Monto que viste en tu cuenta')

        self.pago.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.pago.estado, Pago.PENDIENTE)
        self.assertEqual(self.pedido.estado, Pedido.EN_VALIDACION)

    def test_validar_desde_el_admin_marca_el_pedido_pagado(self):
        self.client.post(reverse('admin:web_pago_changelist'), {
            'action': 'validar_pago', '_selected_action': [self.pago.pk],
            'aplicar': 'Validar', 'monto_confirmado': str(self.pedido.monto_total),
        })

        self.pago.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.pago.estado, Pago.VALIDADO)
        self.assertEqual(self.pago.validado_por.username, 'cajera')
        self.assertEqual(self.pedido.estado, Pedido.PAGADO)

    def test_rechazar_desde_el_admin_deja_el_motivo(self):
        self.client.post(reverse('admin:web_pago_changelist'), {
            'action': 'rechazar_pago', '_selected_action': [self.pago.pk],
            'aplicar': 'Rechazar', 'motivo': 'El monto no coincide con el pedido',
        })

        self.pago.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.pago.estado, Pago.RECHAZADO)
        self.assertEqual(self.pedido.estado, Pedido.CANCELADO)
        self.assertIn('no coincide', self.pago.motivo_rechazo)

    def test_el_pedido_no_se_marca_pagado_a_mano(self):
        # la accion se quito a proposito: un pedido llega a Pagado con su
        # comprobante detras, nunca con un cambio de estado suelto
        respuesta = self.client.get(reverse('admin:web_pedido_changelist'))
        self.assertNotContains(respuesta, 'marcar_pagado')


@ARCHIVOS_APARTE
class ReservaConVencimientoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Reservar compromete unidades sin sacarlas del almacen, y con plazo.

    Antes, hacer clic en "Realizar pedido" descontaba el stock y escribia una
    venta en el kardex aunque el par siguiera en el estante; si el cliente no
    pagaba, esa unidad quedaba muerta para siempre.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B012-1', [(self.t40, 2, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()

    def _envejecer(self, pedido, minutos=30):
        """ Mueve el vencimiento al pasado, para no esperar el reloj real """
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(minutes=minutos)
        )
        pedido.refresh_from_db()
        return pedido

    # --- el reloj ---

    def test_el_pedido_nace_con_su_plazo(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()

        self.assertIsNotNone(pedido.reserva_vence)
        faltan = pedido.segundos_restantes
        self.assertGreater(faltan, settings.MINUTOS_RESERVA * 60 - 60)
        self.assertLessEqual(faltan, settings.MINUTOS_RESERVA * 60)

    def test_el_comprobante_apaga_el_reloj(self):
        """
        Con el comprobante arriba la unidad queda congelada, sin vencimiento.
        Que nosotros tardemos en validar no puede costarle el producto a quien
        ya transfirio: lo que corre despues es nuestra alarma interna.
        """
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        self.assertIsNotNone(pedido.reserva_vence)

        pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion='OP-3001', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )
        pedido.refresh_from_db()

        self.assertEqual(pedido.estado, Pedido.EN_VALIDACION)
        self.assertIsNone(pedido.reserva_vence)
        self.assertIsNone(pedido.segundos_restantes)

    def test_al_pagar_el_reloj_se_apaga(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        self._pagar(pedido)

        pedido.refresh_from_db()
        self.assertIsNone(pedido.reserva_vence)
        self.assertIsNone(pedido.segundos_restantes)

    # --- vencer suelta las unidades ---

    def test_una_reserva_vencida_suelta_las_unidades(self):
        self._agregar(self.t40, 2)
        pedido = self._envejecer(self._comprar())

        # el plazo se cumplio solo: la unidad ya se puede vender aunque el pedido
        # todavia figure como Solicitado. El contador crudo va atrasado a proposito
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 2)
        self.assertEqual(self.t40.reservado, 2)

        self.assertEqual(Pedido.vencer_reservas(), 1)

        pedido.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)
        self.assertIn('plazo', pedido.motivo_cancelacion)
        self.assertEqual(self.t40.reservado, 0)      # el barrido pone al dia el contador
        self.assertEqual(self.t40.disponible, 2)
        self.assertEqual(self.t40.stock, 2)          # nunca se movieron del estante

    def test_una_reserva_viva_no_se_toca(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()

        self.assertEqual(Pedido.vencer_reservas(), 0)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.SOLICITADO)

    def test_el_checkout_suelta_lo_vencido_antes_de_mirar(self):
        """ El sistema se corrige solo, sin depender de que corra el comando """
        self._agregar(self.t40, 2)
        self._envejecer(self._comprar())

        # otro cliente entra a comprar las mismas unidades
        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 2, 'sku': self.t40.variante.sku,
        })
        otro.get(reverse('web:continuarComoInvitado'))
        otro.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)

        self.assertEqual(Pedido.objects.filter(estado=Pedido.SOLICITADO).count(), 1)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 2)

    def test_dos_clientes_no_reservan_la_misma_unidad(self):
        self._agregar(self.t40, 2)
        self._comprar()

        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 2, 'sku': self.t40.variante.sku,
        })
        respuesta = otro.get(reverse('web:carrito'))
        self.assertContains(respuesta, 'sin stock')
        self.assertEqual(Pedido.objects.count(), 1)

    # --- la costura: el plazo se cumple sin que nadie lo barra ---

    def test_la_ultima_unidad_vencida_vuelve_a_venderse(self):
        """
        El caso que estaba roto. Con la ultima unidad reservada por un pedido
        vencido, el catalogo la daba por agotada, el carrito no dejaba agregarla,
        y lo unico que la soltaba vivia detras del checkout, al que no se llegaba
        sin agregarla antes. Quedaba trabada hasta que alguien corriera el comando.
        """
        self._agregar(self.t40, 2)                   # se lleva todo el stock
        self._envejecer(self._comprar())

        # la ficha ya no dice agotado
        respuesta = self.client.get(reverse('web:producto', args=[self.variante.sku]))
        self.assertTrue(respuesta.context['hay_stock'])

        # y otro cliente puede agregarla: esa era la puerta cerrada
        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 2, 'sku': self.t40.variante.sku,
        })
        otro.get(reverse('web:continuarComoInvitado'))
        otro.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)

        # el viejo quedo cancelado y el nuevo se quedo con las unidades
        self.assertEqual(Pedido.objects.filter(estado=Pedido.EXPIRADO).count(), 1)
        self.assertEqual(Pedido.objects.filter(estado=Pedido.SOLICITADO).count(), 1)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 2)

    def test_una_reserva_viva_sigue_bloqueando(self):
        """ El arreglo no debe abrir la puerta antes de que el plazo se cumpla """
        self._agregar(self.t40, 2)
        self._comprar()                              # sin envejecer: el reloj corre

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 0)

        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 1, 'sku': self.t40.variante.sku,
        })
        self.assertContains(otro.get(reverse('web:carrito')), 'sin stock')

    def test_reservar_mide_igual_que_el_catalogo(self):
        """
        Si la ficha ofrece la unidad, confirmar no puede fallar. `reservar()`
        miraba `stock - reservado` crudo y decia que no habia nada.
        """
        self._agregar(self.t40, 2)
        self._envejecer(self._comprar())

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 2)
        self.t40.reservar(2)                         # no levanta: mide lo mismo
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 0)

    def test_precargar_evita_una_consulta_por_fila(self):
        """ Mirar ocho tallas tiene que costar una consulta, no ocho """
        self._agregar(self.t40, 2)
        self._envejecer(self._comprar())

        items = Inventario.precargar_vencidas(Inventario.objects.filter(variante=self.variante))
        por_id = {i.pk: i for i in items}
        with self.assertNumQueries(0):
            self.assertEqual(por_id[self.t40.pk].disponible, 2)
            self.assertEqual(por_id[self.t41.pk].disponible, 0)

    # --- la pantalla ---

    def test_la_pantalla_muestra_la_cuenta_regresiva(self):
        self._agregar(self.t40, 1)
        self._comprar()

        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertContains(respuesta, 'Te guardamos el producto por')
        self.assertContains(respuesta, 'pg-reloj')
        self.assertGreater(respuesta.context['segundos'], 0)

    def test_al_vencer_vuelve_al_carrito_con_sus_productos(self):
        """
        No perdio su seleccion, perdio la reserva. Volver a un carrito vacio y
        sin explicacion es la peor forma de enterarse.
        """
        self._agregar(self.t40, 2)
        pedido = self._envejecer(self._comprar())
        self.assertEqual(self.client.session.get('cart'), {})    # el checkout lo vacio

        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertRedirects(respuesta, reverse('web:carrito'))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)

        # el carrito volvio con lo que tenia, y las unidades al catalogo
        carrito = self.client.get(reverse('web:carrito'))
        self.assertContains(carrito, self.variante.sku)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 0)
        self.assertEqual(self.t40.disponible, 2)

    def test_al_vencer_se_lo_dice(self):
        self._agregar(self.t40, 1)
        self._envejecer(self._comprar())

        respuesta = self.client.get(reverse('web:pagoPedido'), follow=True)
        self.assertContains(respuesta, 'Se vencio el plazo')
        self.assertContains(respuesta, 'carrito')

    # --- el comando ---

    def test_el_comando_simula_sin_tocar_nada(self):
        self._agregar(self.t40, 1)
        pedido = self._envejecer(self._comprar())

        salida = StringIO()
        call_command('vencer_reservas', '--simular', stdout=salida)
        self.assertIn(pedido.referencia, salida.getvalue())

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.SOLICITADO)

    def test_el_comando_suelta_las_reservas_vencidas(self):
        self._agregar(self.t40, 1)
        pedido = self._envejecer(self._comprar())

        salida = StringIO()
        call_command('vencer_reservas', stdout=salida)
        self.assertIn('vuelven a estar a la venta', salida.getvalue())

        pedido.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)
        self.assertEqual(self.t40.disponible, 2)

    def test_sin_vencidas_el_comando_lo_dice(self):
        salida = StringIO()
        call_command('vencer_reservas', stdout=salida)
        self.assertIn('No hay reservas vencidas', salida.getvalue())


class RecojoEnTiendaTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El cliente elige entre que se lo lleven o retirarlo en un mostrador nuestro.

    Los cuatro puntos los siembra la migracion 0022, asi que estan disponibles
    en cualquier base.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B013-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        self.punto = PuntoRecojo.objects.get(nombre='Monetix')

    def _confirmar(self, **cambios):
        self.client.get(reverse('web:continuarComoInvitado'))
        datos = dict(DATOS_CHECKOUT)
        datos.update(cambios)
        return self.client.post(reverse('web:registrarPedido'), datos)

    def _recoger(self, punto=None, **cambios):
        return self._confirmar(
            modo_entrega='R', punto_recojo=(punto or self.punto).id, **cambios
        )

    # --- la pantalla ---

    def _abrir_checkout(self):
        """ Un invitado primero elige comprar sin cuenta, si no lo mandan a identificarse """
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        return self.client.get(reverse('web:registrarPedido'))

    def test_el_checkout_ofrece_los_cuatro_puntos(self):
        respuesta = self._abrir_checkout()

        for nombre in ('Tienda GOAT X', 'Vision para crecer', 'Daily Credits', 'Monetix'):
            self.assertContains(respuesta, nombre)
        self.assertContains(respuesta, 'Como quieres recibirlo')

    def test_un_punto_desactivado_no_se_ofrece(self):
        PuntoRecojo.objects.filter(nombre='Daily Credits').update(activo=False)

        respuesta = self._abrir_checkout()
        self.assertNotContains(respuesta, 'Daily Credits')
        self.assertContains(respuesta, 'Monetix')

    # --- el pedido ---

    def test_recojo_guarda_el_punto(self):
        self._agregar(self.t40, 1)
        self._recoger()

        pedido = Pedido.objects.latest('id')
        self.assertTrue(pedido.es_recojo)
        self.assertEqual(pedido.punto_recojo, self.punto)

    def test_el_pedido_copia_la_direccion_del_punto(self):
        """ Fotografia, no referencia: si manana cierra, el pedido no cambia """
        self._agregar(self.t40, 1)
        self._recoger()
        pedido = Pedido.objects.latest('id')

        PuntoRecojo.objects.filter(pk=self.punto.pk).update(
            nombre='Otro nombre', direccion='Otra calle 999'
        )
        pedido.refresh_from_db()
        self.assertEqual(pedido.punto_recojo_nombre, 'Monetix')
        self.assertIn('Jr. La Union 104', pedido.punto_recojo_direccion)
        self.assertIn('Lircay', pedido.punto_recojo_direccion)

    def test_recojo_no_exige_direccion(self):
        self._agregar(self.t40, 1)
        respuesta = self._recoger(
            direccion='', departamento='', provincia='', distrito=''
        )
        self.assertRedirects(respuesta, reverse('web:pagoPedido'))

    def test_recojo_descarta_la_direccion_que_se_haya_escrito(self):
        """ Un pedido que se retira no debe guardar una direccion que nadie usa """
        self._agregar(self.t40, 1)
        self._recoger()

        pedido = Pedido.objects.latest('id')
        self.assertEqual(pedido.direccion_envio, '')
        self.assertEqual(pedido.distrito_envio, '')

    def test_envio_sigue_exigiendo_direccion(self):
        self._agregar(self.t40, 1)
        respuesta = self._confirmar(direccion='')

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'hace falta la direccion')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_recojo_sin_elegir_punto_se_rechaza(self):
        self._agregar(self.t40, 1)
        respuesta = self._confirmar(modo_entrega='R')

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Elige donde vas a retirar')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_un_pedido_de_envio_no_guarda_punto(self):
        self._agregar(self.t40, 1)
        self._confirmar()

        pedido = Pedido.objects.latest('id')
        self.assertFalse(pedido.es_recojo)
        self.assertIsNone(pedido.punto_recojo)
        self.assertEqual(pedido.punto_recojo_nombre, '')

    # --- el ciclo se bifurca ---

    def test_el_ciclo_de_recojo_no_pasa_por_enviado(self):
        self._agregar(self.t40, 1)
        self._recoger()
        pedido = Pedido.objects.latest('id')
        self._avanzar(pedido, Pedido.PAGADO)

        siguientes = dict(pedido.estados_siguientes)
        self.assertIn(Pedido.LISTO_RECOJO, siguientes)
        self.assertNotIn(Pedido.ENVIADO, siguientes)

        with self.assertRaises(ValueError):
            pedido.cambiar_estado(Pedido.ENVIADO)

    def test_el_ciclo_de_envio_no_pasa_por_listo_para_recojo(self):
        self._agregar(self.t40, 1)
        self._confirmar()
        pedido = Pedido.objects.latest('id')
        self._avanzar(pedido, Pedido.PAGADO)

        with self.assertRaises(ValueError):
            pedido.cambiar_estado(Pedido.LISTO_RECOJO)

    def test_recojo_llega_hasta_entregado(self):
        self._agregar(self.t40, 1)
        self._recoger()
        pedido = Pedido.objects.latest('id')

        self._avanzar(pedido, Pedido.PAGADO)
        pedido.cambiar_estado(Pedido.LISTO_RECOJO)
        pedido.cambiar_estado(Pedido.ENTREGADO)

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.ENTREGADO)

    def test_listo_para_recojo_sigue_con_el_stock_descontado(self):
        self._agregar(self.t40, 2)
        self._recoger()
        pedido = Pedido.objects.latest('id')
        self._avanzar(pedido, Pedido.PAGADO)
        pedido.cambiar_estado(Pedido.LISTO_RECOJO)

        self.t40.refresh_from_db()
        self.assertTrue(pedido.descontado)
        self.assertEqual(self.t40.stock, 3)
        self.assertEqual(self.t40.reservado, 0)

    # --- lo que ve el cliente despues ---

    def test_gracias_muestra_donde_retirar(self):
        self._agregar(self.t40, 1)
        self._recoger()

        respuesta = self.client.get(reverse('web:gracias'))
        self.assertContains(respuesta, 'Recojo en tienda')
        self.assertContains(respuesta, 'Monetix')
        self.assertContains(respuesta, 'Jr. La Union 104')
        self.assertNotContains(respuesta, 'Telefono:')

    def test_gracias_muestra_la_direccion_si_es_envio(self):
        self._agregar(self.t40, 1)
        self._confirmar()

        respuesta = self.client.get(reverse('web:gracias'))
        self.assertContains(respuesta, 'Envio')
        self.assertContains(respuesta, 'Av. Manchego Munoz 431')

    def test_donde_recibe_resume_en_una_linea(self):
        self._agregar(self.t40, 1)
        self._recoger()
        pedido = Pedido.objects.latest('id')
        self.assertIn('Monetix', pedido.donde_recibe)
        self.assertIn('Lircay', pedido.donde_recibe)


@ARCHIVOS_APARTE
class AdminRecojoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ El mostrador se marca listo desde el admin, y ahi el cliente tiene fecha """

    def setUp(self):
        self._montar_almacen()
        self._compra('B014-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        self.punto = PuntoRecojo.objects.get(nombre='Tienda GOAT X')

        self.client.get(reverse('web:continuarComoInvitado'))
        self._agregar(self.t40, 1)
        datos = dict(DATOS_CHECKOUT, modo_entrega='R', punto_recojo=self.punto.id)
        self.client.post(reverse('web:registrarPedido'), datos)
        self.pedido = Pedido.objects.latest('id')
        self._pagar(self.pedido, nro='OP-RECOJO')
        self.pedido.refresh_from_db()

        self.client.force_login(
            User.objects.create_superuser('tienda', 'tienda@goatx.pe', 'clave-de-prueba')
        )

    def test_los_puntos_se_administran(self):
        respuesta = self.client.get(reverse('admin:web_puntorecojo_changelist'))
        self.assertContains(respuesta, 'Tienda GOAT X')
        self.assertContains(respuesta, 'Lircay')

    def test_marcar_listo_para_recojo_desde_el_admin(self):
        self.client.post(reverse('admin:web_pedido_changelist'), {
            'action': 'marcar_listo_recojo', '_selected_action': [self.pedido.pk],
        })
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.LISTO_RECOJO)

    def test_un_pedido_de_recojo_no_se_marca_enviado(self):
        self.client.post(reverse('admin:web_pedido_changelist'), {
            'action': 'marcar_enviado', '_selected_action': [self.pedido.pk],
        })
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.PAGADO)

    def test_la_lista_dice_donde_recibe(self):
        respuesta = self.client.get(reverse('admin:web_pedido_changelist'))
        self.assertContains(respuesta, 'Recojo')
        self.assertContains(respuesta, 'Tienda GOAT X')


@ARCHIVOS_APARTE
class AvisoDePagoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El cuello de botella para validar rapido no es la pantalla: es enterarse.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B015-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        self._agregar(self.t40, 1)
        self.pedido = self._comprar()

    def _declarar(self, nro='OP-AVISO'):
        # el correo sale con transaction.on_commit, que en un TestCase no corre
        # solo: la transaccion se revierte y nunca hay commit
        with self.captureOnCommitCallbacks(execute=True):
            return self.pedido.declarar_pago(
                cuenta=self.cuenta,
                monto_declarado=self.pedido.monto_total,
                nro_operacion=nro,
                voucher=_imagen(),
                fecha_pago=timezone.localdate(),
            )

    @override_settings(CORREOS_AVISO=['jefe@goatx.pe'])
    def test_llega_un_correo_al_declarar_el_pago(self):
        mail.outbox = []
        self._declarar()

        # dos correos: uno al equipo y otro al cliente
        self.assertEqual(len(mail.outbox), 2)
        aviso = next(m for m in mail.outbox if m.to == ['jefe@goatx.pe'])
        self.assertIn(self.pedido.nro_pedido, aviso.subject)
        self.assertIn('OP-AVISO', aviso.body)
        self.assertIn(str(settings.HORAS_VALIDACION), aviso.body)

    @override_settings(CORREOS_AVISO=['jefe@goatx.pe'])
    def test_el_correo_trae_el_enlace_para_validar(self):
        mail.outbox = []
        pago = self._declarar()
        equipo = next(m for m in mail.outbox if m.to == ['jefe@goatx.pe'])
        self.assertIn(reverse('web:validarPago', args=[pago.id]), equipo.body)

    @override_settings(CORREOS_AVISO=[])
    def test_sin_destinatarios_el_equipo_no_recibe_nada(self):
        """ Pero el cliente si: su correo no depende de la lista del equipo """
        mail.outbox = []
        self._declarar()

        self.assertEqual([m for m in mail.outbox if m.to == ['jefe@goatx.pe']], [])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.pedido.email_comprador])


@ARCHIVOS_APARTE
class BandejaMovilTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ La pantalla de validacion pensada para el celular """

    def setUp(self):
        self._montar_almacen()
        self._compra('B016-1', [(self.t40, 8, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        self._agregar(self.t40, 1)
        self.pedido = self._comprar()
        self.pago = self.pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=self.pedido.monto_total,
            nro_operacion='OP-MOVIL', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )
        self.equipo = Client()
        self.equipo.force_login(
            User.objects.create_superuser('validador', 'v@goatx.pe', 'clave-de-prueba')
        )

    # --- permisos ---

    def test_un_extrano_no_entra_a_la_bandeja(self):
        respuesta = Client().get(reverse('web:validarPagos'))
        self.assertNotEqual(respuesta.status_code, 200)

    def test_un_cliente_logueado_tampoco(self):
        otro = Client()
        User.objects.create_user('cliente', 'c@ejemplo.com', 'clave-de-prueba')
        otro.login(username='cliente', password='clave-de-prueba')
        self.assertNotEqual(otro.get(reverse('web:validarPagos')).status_code, 200)

    # --- la cola ---

    def test_la_bandeja_lista_lo_pendiente(self):
        respuesta = self.equipo.get(reverse('web:validarPagos'))
        self.assertContains(respuesta, self.pedido.nro_pedido)
        self.assertContains(respuesta, 'OP-MOVIL')
        self.assertContains(respuesta, 'evidencia, no prueba')

    def test_el_mas_viejo_va_primero(self):
        self._agregar(self.t40, 1)
        nuevo = self._comprar()
        nuevo.declarar_pago(
            cuenta=self.cuenta, monto_declarado=nuevo.monto_total,
            nro_operacion='OP-NUEVO', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )
        Pago.objects.filter(nro_operacion='OP-MOVIL').update(
            creado=timezone.now() - timedelta(hours=8)
        )

        pendientes = self.equipo.get(reverse('web:validarPagos')).context['pendientes']
        self.assertEqual(list(pendientes)[0].nro_operacion, 'OP-MOVIL')

    def test_sin_pendientes_lo_dice(self):
        self.pago.validar(self.pedido.monto_total)
        respuesta = self.equipo.get(reverse('web:validarPagos'))
        self.assertContains(respuesta, 'No hay nada esperando')

    def test_lo_demorado_se_marca(self):
        Pago.objects.filter(pk=self.pago.pk).update(
            creado=timezone.now() - timedelta(hours=settings.HORAS_ALERTA_VALIDACION + 1)
        )
        self.pago.refresh_from_db()
        self.assertTrue(self.pago.demorado)
        self.assertContains(self.equipo.get(reverse('web:validarPagos')), 'vp-demorado')

    def test_lo_reciente_no_se_marca(self):
        self.assertFalse(self.pago.demorado)
        self.assertIn('h', self.pago.espera)

    # --- validar y rechazar ---

    def test_validar_desde_el_celular(self):
        respuesta = self.equipo.post(
            reverse('web:validarPago', args=[self.pago.id]),
            {'validar': '1', 'monto_confirmado': str(self.pedido.monto_total)},
        )
        self.assertRedirects(respuesta, reverse('web:validarPagos'))

        self.pago.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.pago.estado, Pago.VALIDADO)
        self.assertEqual(self.pago.validado_por.username, 'validador')
        self.assertEqual(self.pedido.estado, Pedido.PAGADO)

    def test_validar_sin_monto_no_pasa(self):
        respuesta = self.equipo.post(
            reverse('web:validarPago', args=[self.pago.id]), {'validar': '1'}
        )
        self.assertEqual(respuesta.status_code, 200)

        self.pago.refresh_from_db()
        self.assertEqual(self.pago.estado, Pago.PENDIENTE)

    def test_una_diferencia_se_avisa(self):
        respuesta = self.equipo.post(
            reverse('web:validarPago', args=[self.pago.id]),
            {'validar': '1', 'monto_confirmado': '100.00'},
            follow=True,
        )
        self.assertContains(respuesta, 'diferencia')

        self.pago.refresh_from_db()
        self.assertFalse(self.pago.cuadra)

    def test_rechazar_desde_el_celular(self):
        self.equipo.post(
            reverse('web:validarPago', args=[self.pago.id]),
            {'rechazar': '1', 'motivo': 'La captura esta cortada, no se lee el monto'},
        )
        self.pago.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.pago.estado, Pago.RECHAZADO)
        self.assertEqual(self.pedido.estado, Pedido.CANCELADO)

    def test_rechazar_sin_motivo_no_pasa(self):
        self.equipo.post(
            reverse('web:validarPago', args=[self.pago.id]), {'rechazar': '1', 'motivo': ''}
        )
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.estado, Pago.PENDIENTE)

    def test_un_pago_resuelto_ya_no_muestra_los_botones(self):
        self.pago.validar(self.pedido.monto_total)
        respuesta = self.equipo.get(reverse('web:validarPago', args=[self.pago.id]))
        self.assertContains(respuesta, 'ya esta validado')
        self.assertNotContains(respuesta, 'Confirmar el pago')

    def test_la_ficha_muestra_donde_recibe(self):
        respuesta = self.equipo.get(reverse('web:validarPago', args=[self.pago.id]))
        self.assertContains(respuesta, 'Total del pedido')
        self.assertContains(respuesta, self.pedido.donde_recibe)


class _CiudadesMixin(_AlmacenMixin):
    """ Las dos ciudades ya vienen sembradas por la migracion 0024 """

    def _montar_ciudades(self):
        self._montar_almacen()
        self.huancavelica = Ubicacion.objects.get(nombre='Huancavelica')
        self.lircay = Ubicacion.objects.get(nombre='Lircay')

    def _viaje(self, dias=3):
        sale = timezone.localdate() + timedelta(days=dias)
        return Traslado.objects.create(
            origen=self.huancavelica,
            destino=self.lircay,
            fecha_despacho=sale,
            fecha_disponible=sale + timedelta(days=self.lircay.dias_viaje),
        )


class TrasladoTests(_CiudadesMixin, TestCase):
    """
    El documento planifica el viaje, no la mercaderia.

    Reservar el lunes lo que sale el sabado bloquea cinco dias un par que
    todavia nadie compro, y se pierden ventas seguras en la ciudad donde esta.
    """

    def setUp(self):
        self._montar_ciudades()
        self._compra('B017-1', [(self.t40, 6, Decimal('300.00'))], aplicar=True)
        self.t40.refresh_from_db()

    def test_el_documento_nace_vacio_y_no_reserva_nada(self):
        self._viaje()

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 0)
        self.assertEqual(self.t40.disponible, 6)

    def test_agregar_por_stock_reserva_recien_ahi(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 2)

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 2)
        self.assertEqual(self.t40.stock, 6)          # siguen en el estante
        self.assertEqual(self.t40.disponible, 4)

    def test_quitar_una_linea_devuelve_las_unidades(self):
        viaje = self._viaje()
        linea = viaje.agregar(self.t40, 2)
        linea.delete()

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 6)

    def test_agregar_crea_la_fila_del_destino(self):
        viaje = self._viaje()
        linea = viaje.agregar(self.t40, 2)

        self.assertEqual(linea.item_destino.ubicacion, self.lircay)
        self.assertEqual(linea.item_destino.stock, 0)
        self.assertEqual(linea.item_destino.variante_id, self.t40.variante_id)

    def test_no_se_manda_algo_que_no_esta_en_el_origen(self):
        viaje = self._viaje()
        ajeno = Inventario.objects.create(
            variante=self.variante, valor=_valor_talla('44', 9), ubicacion=self.lircay
        )
        with self.assertRaises(ValueError):
            viaje.agregar(ajeno, 1)

    # --- despachar ---

    def test_despachar_saca_del_stock_y_escribe_el_kardex(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 2)
        viaje.despachar()

        self.t40.refresh_from_db()
        self.assertEqual(viaje.estado, Traslado.EN_TRANSITO)
        self.assertEqual(self.t40.stock, 4)          # ahora si salieron
        self.assertEqual(self.t40.reservado, 0)

        salida = self.t40.movimientos.get(tipo=MovimientoInventario.TRASLADO_SALIDA)
        self.assertEqual(salida.cantidad, -2)
        self.assertEqual(salida.traslado, viaje)

    def test_un_viaje_vacio_no_sale(self):
        with self.assertRaises(ValueError):
            self._viaje().despachar()

    def test_un_viaje_despachado_no_se_edita(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 1)
        viaje.despachar()

        with self.assertRaises(ValueError):
            viaje.agregar(self.t40, 1)

    def test_no_se_despacha_dos_veces(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 1)
        viaje.despachar()
        with self.assertRaises(ValueError):
            viaje.despachar()

    # --- recibir contando ---

    def test_recibir_suma_al_destino(self):
        viaje = self._viaje()
        linea = viaje.agregar(self.t40, 2)
        viaje.despachar()
        viaje.recibir()

        destino = Inventario.objects.get(pk=linea.item_destino_id)
        self.assertEqual(viaje.estado, Traslado.RECIBIDO)
        self.assertEqual(destino.stock, 2)
        self.assertEqual(destino.stock_segun_kardex, 2)

    def test_recibir_con_faltante_deja_la_diferencia_escrita(self):
        viaje = self._viaje()
        linea = viaje.agregar(self.t40, 3)
        viaje.despachar()
        viaje.recibir(conteo={linea.id: 2})          # llegaron 2 de 3

        linea.refresh_from_db()
        destino = Inventario.objects.get(pk=linea.item_destino_id)
        self.assertEqual(linea.cantidad_recibida, 2)
        self.assertEqual(linea.faltante, 1)
        self.assertEqual(destino.stock, 2)

    def test_lo_que_no_llego_no_aparece_en_el_kardex_del_destino(self):
        viaje = self._viaje()
        linea = viaje.agregar(self.t40, 3)
        viaje.despachar()
        viaje.recibir(conteo={linea.id: 2})

        destino = Inventario.objects.get(pk=linea.item_destino_id)
        entrada = destino.movimientos.get(tipo=MovimientoInventario.TRASLADO_ENTRADA)
        self.assertEqual(entrada.cantidad, 2)

    def test_no_se_recibe_algo_que_no_salio(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 1)
        with self.assertRaises(ValueError):
            viaje.recibir()

    # --- anular ---

    def test_anular_antes_de_salir_devuelve_las_unidades(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 2)
        viaje.anular('Se rompio la camioneta')

        self.t40.refresh_from_db()
        self.assertEqual(viaje.estado, Traslado.ANULADO)
        self.assertEqual(self.t40.disponible, 6)
        self.assertFalse(self.t40.movimientos.filter(traslado=viaje).exists())

    def test_anular_exige_un_motivo(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 1)
        with self.assertRaises(ValueError):
            viaje.anular('  ')

    def test_un_viaje_que_ya_salio_no_se_anula(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 1)
        viaje.despachar()
        with self.assertRaises(ValueError):
            viaje.anular('Tarde')


class DisponibilidadTests(_CiudadesMixin, TestCase):
    """ Que se le promete al cliente en cada mostrador """

    def setUp(self):
        self._montar_ciudades()
        self._compra('B018-1', [(self.t40, 4, Decimal('300.00'))], aplicar=True)
        self.t40.refresh_from_db()
        self.en_hvca = PuntoRecojo.objects.get(nombre='Tienda GOAT X')
        self.en_lircay = PuntoRecojo.objects.filter(nombre__icontains='Monetix').first()
        self.en_lircay.ubicacion = self.lircay
        self.en_lircay.save(update_fields=['ubicacion'])

    def test_con_stock_en_la_ciudad_se_retira_hoy(self):
        clave, fecha = para_item(self.t40, 1, self.en_hvca)
        self.assertEqual(clave, disponibilidad.HOY)
        self.assertIsNone(fecha)

    def test_todo_reservado_no_es_disponible(self):
        self.t40.reservar(4)
        clave, _ = para_item(self.t40, 1, self.en_hvca)
        self.assertEqual(clave, disponibilidad.SIN_STOCK)

    def test_en_otra_ciudad_sin_transporte_no_se_ofrece(self):
        clave, fecha = para_item(self.t40, 1, self.en_lircay)
        self.assertEqual(clave, disponibilidad.SIN_VIAJE)
        self.assertIsNone(fecha)

    def test_en_otra_ciudad_con_transporte_da_la_fecha(self):
        viaje = self._viaje()
        clave, fecha = para_item(self.t40, 1, self.en_lircay)

        self.assertEqual(clave, disponibilidad.ENCARGO)
        self.assertEqual(fecha, viaje.fecha_disponible)

    def test_manda_el_transporte_que_llega_antes(self):
        lejano = self._viaje(dias=10)
        cercano = self._viaje(dias=2)
        _, fecha = para_item(self.t40, 1, self.en_lircay)
        self.assertEqual(fecha, cercano.fecha_disponible)
        self.assertLess(fecha, lejano.fecha_disponible)

    def test_un_producto_que_no_viaja_solo_se_retira_donde_esta(self):
        self._viaje()
        producto = self.variante.producto
        producto.no_se_traslada = True
        producto.save(update_fields=['no_se_traslada'])

        self.assertEqual(para_item(self.t40, 1, self.en_hvca)[0], disponibilidad.HOY)
        self.assertEqual(para_item(self.t40, 1, self.en_lircay)[0], disponibilidad.LEJOS)

    def test_sin_marcar_el_producto_viaja(self):
        """ La regla es que viaja: solo se marca la excepcion """
        self._viaje()
        producto = self.variante.producto

        self.assertFalse(producto.no_se_traslada)
        self.assertTrue(producto.se_traslada)
        self.assertEqual(para_item(self.t40, 1, self.en_lircay)[0], disponibilidad.ENCARGO)

    def test_si_ya_hay_stock_en_destino_se_retira_hoy(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 2)
        viaje.despachar()
        viaje.recibir()

        clave, _ = para_item(self.t40, 1, self.en_lircay)
        self.assertEqual(clave, disponibilidad.HOY)

    # --- el carrito completo ---

    def test_el_carrito_manda_el_peor_caso(self):
        self._viaje()
        self._compra('B018-2', [(self.t41, 2, Decimal('300.00'))], aplicar=True)
        self.t41.refresh_from_db()

        # una linea viaja y la otra no: el pedido entero no se retira alla
        producto = self.variante.producto
        producto.no_se_traslada = True
        producto.save(update_fields=['no_se_traslada'])

        clave, _ = para_carrito([(self.t40, 1), (self.t41, 1)], self.en_lircay)
        self.assertEqual(clave, disponibilidad.LEJOS)

    def test_el_carrito_toma_la_fecha_mas_lejana(self):
        cercano = self._viaje(dias=2)
        self._compra('B018-3', [(self.t41, 2, Decimal('300.00'))], aplicar=True)
        self.t41.refresh_from_db()

        clave, fecha = para_carrito([(self.t40, 1), (self.t41, 1)], self.en_lircay)
        self.assertEqual(clave, disponibilidad.ENCARGO)
        self.assertEqual(fecha, cercano.fecha_disponible)

    def test_todo_en_la_ciudad_es_hoy(self):
        self._compra('B018-4', [(self.t41, 2, Decimal('300.00'))], aplicar=True)
        clave, fecha = para_carrito([(self.t40, 1), (self.t41, 1)], self.en_hvca)
        self.assertEqual(clave, disponibilidad.HOY)
        self.assertIsNone(fecha)


class ModuloTransporteTests(_CiudadesMixin, _VentaMixin, TestCase):
    """
    El punto ciego del modulo: sin viaje programado una ciudad deja de ofrecer
    productos y nadie reclama, porque el cliente no ve un error, ve un catalogo
    mas chico. Todo esto existe para que eso se vea.
    """

    def setUp(self):
        self._montar_ciudades()
        self._compra('B019-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        self.t40.refresh_from_db()
        self.equipo = Client()
        self.equipo.force_login(
            User.objects.create_superuser('logistica', 'log@goatx.pe', 'clave-de-prueba')
        )

    # --- el aviso ---

    def test_una_ciudad_sin_viaje_se_marca(self):
        self.assertTrue(self.lircay.sin_transporte)
        self.assertIsNone(self.lircay.proximo_traslado())

    def test_con_viaje_programado_deja_de_avisar(self):
        viaje = self._viaje()
        self.assertFalse(self.lircay.sin_transporte)
        self.assertEqual(self.lircay.proximo_traslado(), viaje)

    def test_el_almacen_principal_nunca_avisa(self):
        # de Huancavelica sale todo: no hay viaje que la abastezca
        self.assertFalse(self.huancavelica.sin_transporte)

    def test_un_viaje_ya_recibido_no_cuenta_como_programado(self):
        viaje = self._viaje()
        viaje.agregar(self.t40, 1)
        viaje.despachar()
        viaje.recibir()

        self.assertTrue(self.lircay.sin_transporte)

    # --- la pantalla ---

    def test_la_pantalla_avisa_la_ciudad_sin_transporte(self):
        respuesta = self.equipo.get(reverse('web:transportes'))
        self.assertContains(respuesta, 'Sin transporte programado')
        self.assertContains(respuesta, 'Lircay')
        self.assertEqual(len(respuesta.context['sin_transporte']), 1)

    def test_con_viaje_la_pantalla_muestra_las_fechas(self):
        viaje = self._viaje()
        respuesta = self.equipo.get(reverse('web:transportes'))

        self.assertEqual(len(respuesta.context['sin_transporte']), 0)
        self.assertContains(respuesta, viaje.get_estado_display())

    def test_la_pantalla_sirve_para_cualquier_ciudad_nueva(self):
        """ Pampas o Huancayo entran solas: es una fila, no codigo """
        Ubicacion.objects.create(nombre='Huancayo', dia_despacho=2, dias_viaje=1)

        respuesta = self.equipo.get(reverse('web:transportes'))
        self.assertContains(respuesta, 'Huancayo')
        self.assertEqual(len(respuesta.context['sin_transporte']), 2)

    def test_un_extrano_no_entra(self):
        self.assertNotEqual(Client().get(reverse('web:transportes')).status_code, 200)

    # --- pedidos esperando viaje ---

    def test_un_pedido_pagado_de_otra_ciudad_queda_esperando(self):
        punto = PuntoRecojo.objects.filter(nombre__icontains='Monetix').first()
        punto.ubicacion = self.lircay
        punto.save(update_fields=['ubicacion'])
        self._viaje()

        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), dict(
            DATOS_CHECKOUT, modo_entrega='R', punto_recojo=punto.id
        ))
        pedido = Pedido.objects.latest('id')
        self._avanzar(pedido, Pedido.PAGADO)

        esperando = Traslado.pedidos_esperando(destino=self.lircay)
        self.assertEqual(len(esperando), 1)
        self.assertEqual(esperando[0][0], pedido)

    def test_lo_que_ya_esta_en_un_viaje_deja_de_esperar(self):
        punto = PuntoRecojo.objects.filter(nombre__icontains='Monetix').first()
        punto.ubicacion = self.lircay
        punto.save(update_fields=['ubicacion'])
        viaje = self._viaje()

        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), dict(
            DATOS_CHECKOUT, modo_entrega='R', punto_recojo=punto.id
        ))
        pedido = Pedido.objects.latest('id')
        self._avanzar(pedido, Pedido.PAGADO)

        detalle = pedido.detalles.get()
        viaje.agregar(detalle.item, detalle.cantidad,
                      motivo=TrasladoDetalle.POR_PEDIDO, pedido=pedido)

        self.assertEqual(Traslado.pedidos_esperando(destino=self.lircay), [])

    def test_un_pedido_de_su_propia_ciudad_no_espera_nada(self):
        punto = PuntoRecojo.objects.get(nombre='Tienda GOAT X')
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), dict(
            DATOS_CHECKOUT, modo_entrega='R', punto_recojo=punto.id
        ))
        self._avanzar(Pedido.objects.latest('id'), Pedido.PAGADO)

        self.assertEqual(Traslado.pedidos_esperando(), [])

    # --- el comando ---

    def test_el_comando_simula_sin_crear_nada(self):
        salida = StringIO()
        call_command('programar_transportes', '--simular', stdout=salida)

        self.assertIn('SIN TRANSPORTE PROGRAMADO', salida.getvalue())
        self.assertEqual(Traslado.objects.count(), 0)

    def test_el_comando_programa_el_viaje_que_falta(self):
        salida = StringIO()
        call_command('programar_transportes', stdout=salida)

        viaje = self.lircay.proximo_traslado()
        self.assertIsNotNone(viaje)
        self.assertEqual(viaje.origen, self.huancavelica)
        self.assertEqual(viaje.unidades, 0)          # nace vacio: no compromete nada
        self.assertEqual(viaje.fecha_disponible, self.lircay.llegada_de(viaje.fecha_despacho))

    def test_el_comando_no_duplica_lo_que_ya_esta(self):
        self._viaje()
        call_command('programar_transportes', stdout=StringIO())
        self.assertEqual(Traslado.objects.count(), 1)

    def test_sin_ciudades_pendientes_lo_dice(self):
        self._viaje()
        salida = StringIO()
        call_command('programar_transportes', stdout=salida)
        self.assertIn('Todas las ciudades tienen su viaje', salida.getvalue())


class BarridoAutomaticoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El plazo se cumple con el reloj; pasar el pedido a Cancelado es una escritura
    y alguien tiene que dispararla. Antes ese alguien era el checkout de otro
    cliente, la pantalla de pago, o un cron que nadie programo. Ahora tambien lo
    dispara el trafico normal, cada tanto.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B090-1', [(self.t40, 2, Decimal('300.00'))], aplicar=True)
        _cuenta_bcp()
        cache.clear()

    def _envejecer(self, pedido, minutos=30):
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(minutes=minutos)
        )
        pedido.refresh_from_db()
        return pedido

    def _otro_pedido(self, cantidad=1):
        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': cantidad, 'sku': self.t40.variante.sku,
        })
        otro.get(reverse('web:continuarComoInvitado'))
        otro.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)
        return Pedido.objects.latest('id')

    def test_navegar_cancela_lo_vencido(self):
        """ Sin comando, sin checkout ajeno y sin volver a /pago """
        self._agregar(self.t40, 1)
        pedido = self._envejecer(self._comprar())
        cache.clear()                       # ventana de barrido limpia

        self.client.get(reverse('web:index'))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)
        self.assertIn('plazo', pedido.motivo_cancelacion)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 0)

    def test_una_reserva_viva_no_se_toca(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()            # sin envejecer
        cache.clear()

        self.client.get(reverse('web:index'))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.SOLICITADO)

    def test_no_barre_en_cada_peticion(self):
        """ Una vez por ventana, no una vez por visita """
        primero = self._envejecer(self._otro_pedido())
        cache.clear()
        self.client.get(reverse('web:index'))
        primero.refresh_from_db()
        self.assertEqual(primero.estado, Pedido.EXPIRADO)

        # otro vencido dentro de la misma ventana: todavia no le toca
        segundo = self._envejecer(self._otro_pedido())
        self.client.get(reverse('web:index'))

        segundo.refresh_from_db()
        self.assertEqual(segundo.estado, Pedido.SOLICITADO)

    @override_settings(SEGUNDOS_ENTRE_BARRIDOS=0)
    def test_se_puede_apagar(self):
        pedido = self._envejecer(self._otro_pedido())
        cache.clear()

        self.client.get(reverse('web:index'))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.SOLICITADO)

    def test_un_fallo_barriendo_no_tumba_la_pagina(self):
        """ El cliente pidio una pagina, no un barrido: si falla, que falle solo """
        from unittest.mock import patch
        self._envejecer(self._otro_pedido())
        cache.clear()

        with patch.object(Pedido, 'vencer_reservas', side_effect=RuntimeError('boom')):
            respuesta = self.client.get(reverse('web:index'))

        self.assertEqual(respuesta.status_code, 200)


class NumeroDePedidoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Dos identificadores con dos significados.

    El codigo de reserva dice "estas unidades son de esta persona" y nace al
    confirmar. El numero de pedido numera una venta y nace con el comprobante.
    Antes habia uno solo, sacado del id, y cada checkout abandonado se comia un
    correlativo de la serie.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B100-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _declarar(self, pedido, nro='OP-1'):
        return pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion=nro, voucher=_imagen(), fecha_pago=timezone.localdate(),
        )

    def test_el_comprobante_emite_el_numero(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        self.assertIsNone(pedido.nro_pedido)

        self._declarar(pedido)

        pedido.refresh_from_db()
        self.assertRegex(pedido.nro_pedido, r'^P\d{8}-\d{5}$')
        self.assertEqual(pedido.estado, Pedido.EN_VALIDACION)
        self.assertEqual(pedido.referencia, pedido.nro_pedido)

    def test_un_pedido_abandonado_no_gasta_numero(self):
        """ Lo que motivo el cambio: el carrito que nadie pago no quema serie """
        self._agregar(self.t40, 1)
        abandonado = self._comprar()
        Pedido.objects.filter(pk=abandonado.pk).update(
            reserva_vence=timezone.now() - timedelta(minutes=30)
        )
        Pedido.vencer_reservas()

        abandonado.refresh_from_db()
        self.assertEqual(abandonado.estado, Pedido.EXPIRADO)
        self.assertIsNone(abandonado.nro_pedido)

        # el que si paga se lleva el primer numero del dia, sin hueco
        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 1, 'sku': self.t40.variante.sku,
        })
        otro.get(reverse('web:continuarComoInvitado'))
        otro.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)
        pagado = Pedido.objects.latest('id')
        self._declarar(pagado, nro='OP-2')

        pagado.refresh_from_db()
        self.assertTrue(pagado.nro_pedido.endswith('-00001'))

    def test_el_correlativo_del_dia_no_deja_huecos(self):
        numeros = []
        for i in range(3):
            cliente = Client()
            cliente.post(reverse('web:agregarCarrito'), {
                'item_id': self.t40.id, 'cantidad': 1, 'sku': self.t40.variante.sku,
            })
            cliente.get(reverse('web:continuarComoInvitado'))
            cliente.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)
            pedido = Pedido.objects.latest('id')
            self._declarar(pedido, nro=f'OP-{i}')
            pedido.refresh_from_db()
            numeros.append(pedido.nro_pedido)

        self.assertEqual([n[-5:] for n in numeros], ['00001', '00002', '00003'])

    def test_no_se_reemite_si_ya_tiene_numero(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        self._declarar(pedido)
        pedido.refresh_from_db()
        primero = pedido.nro_pedido

        self.assertEqual(pedido.emitir_nro_pedido(), primero)
        pedido.refresh_from_db()
        self.assertEqual(pedido.nro_pedido, primero)

    def test_los_codigos_de_reserva_no_se_repiten(self):
        codigos = set()
        for i in range(4):
            cliente = Client()
            cliente.post(reverse('web:agregarCarrito'), {
                'item_id': self.t40.id, 'cantidad': 1, 'sku': self.t40.variante.sku,
            })
            cliente.get(reverse('web:continuarComoInvitado'))
            cliente.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)
            codigos.add(Pedido.objects.latest('id').codigo_reserva)
        self.assertEqual(len(codigos), 4)


class ValidacionCongelaTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El plazo de validacion es nuestro y no le quita la unidad a nadie.

    Antes, a las 12 horas de subir el comprobante la unidad volvia al catalogo,
    otro cliente se la llevaba, y al validar habia que devolver plata. El cliente
    ya habia hecho su parte: el que tardaba eramos nosotros.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B110-1', [(self.t40, 1, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _en_validacion(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion='OP-CONGELA', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )
        pedido.refresh_from_db()
        return pedido

    def test_la_unidad_no_se_suelta_por_mas_que_pase_el_tiempo(self):
        pedido = self._en_validacion()
        # aunque alguien le deje un vencimiento viejo a mano, no vence
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(days=3)
        )

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 0)
        self.assertEqual(Pedido.vencer_reservas(), 0)

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EN_VALIDACION)

    def test_el_barrido_automatico_tampoco_lo_toca(self):
        pedido = self._en_validacion()
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(days=3)
        )
        cache.clear()

        self.client.get(reverse('web:index'))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EN_VALIDACION)

    def test_la_reserva_sin_comprobante_si_vence(self):
        """ El arreglo no debe congelar tambien lo que nadie pago """
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(minutes=30)
        )

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 1)
        self.assertEqual(Pedido.vencer_reservas(), 1)


class SeguimientoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ Los cuatro pasos que ve el cliente, al estilo de cualquier tienda """

    def setUp(self):
        self._montar_almacen()
        self._compra('B120-1', [(self.t40, 4, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _pedido_en(self, estado, recojo=False):
        sufijo = 'R' if recojo else 'E'
        return Pedido.objects.create(
            codigo_reserva='R-SEG' + estado + sufijo,
            nombre_comprador='Ana', apellido_comprador='Perez',
            email_comprador='a@b.pe', telefono_comprador='999',
            modo_entrega=Pedido.RECOJO if recojo else Pedido.ENVIO,
            monto_total=Decimal('100.00'), estado=estado,
        )

    def test_una_reserva_no_muestra_seguimiento(self):
        """ Todavia no es un pedido: un paso 1 le prometeria algo que no compro """
        self.assertEqual(self._pedido_en(Pedido.SOLICITADO).pasos_seguimiento, [])

    def test_un_cancelado_no_muestra_seguimiento(self):
        self.assertEqual(self._pedido_en(Pedido.CANCELADO).pasos_seguimiento, [])

    def test_en_validacion_es_el_primer_paso(self):
        pasos = self._pedido_en(Pedido.EN_VALIDACION).pasos_seguimiento
        self.assertEqual([p['nombre'] for p in pasos],
                         ['Recibido', 'Confirmado', 'Enviado', 'Entregado'])
        self.assertTrue(pasos[0]['actual'])
        self.assertFalse(any(p['hecho'] for p in pasos))

    def test_el_recojo_cambia_el_tercer_paso(self):
        pasos = self._pedido_en(Pedido.PAGADO, recojo=True).pasos_seguimiento
        self.assertEqual(pasos[2]['nombre'], 'Listo para recoger')
        self.assertTrue(pasos[0]['hecho'])
        self.assertTrue(pasos[1]['actual'])

    def test_entregado_completa_los_cuatro(self):
        pasos = self._pedido_en(Pedido.ENTREGADO).pasos_seguimiento
        self.assertTrue(pasos[3]['actual'])
        self.assertEqual(sum(1 for p in pasos if p['hecho']), 3)

    def test_la_pantalla_pinta_los_pasos(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion='OP-SEG', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )

        respuesta = self.client.get(reverse('web:gracias'))
        self.assertContains(respuesta, 'seg-actual')
        self.assertContains(respuesta, 'Recibido')
        self.assertContains(respuesta, 'Entregado')


class WhatsappTests(_AlmacenMixin, _VentaMixin, TestCase):
    """ Lo que una pantalla no resuelve lo atiende una persona """

    def setUp(self):
        self._montar_almacen()
        self._compra('B130-1', [(self.t40, 2, Decimal('300.00'))], aplicar=True)
        _cuenta_bcp()
        cache.clear()

    def test_pago_ofrece_al_asesor_con_el_pedido_escrito(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()

        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertContains(respuesta, 'wa.me/' + settings.WHATSAPP_ASESOR)
        self.assertContains(respuesta, 'Escribinos por WhatsApp')
        self.assertIn(pedido.referencia, respuesta.context['whatsapp'])

    def test_gracias_tambien_ofrece_al_asesor(self):
        self._agregar(self.t40, 1)
        self._comprar()

        respuesta = self.client.get(reverse('web:gracias'))
        self.assertContains(respuesta, 'wa.me/' + settings.WHATSAPP_ASESOR)

    @override_settings(WHATSAPP_ASESOR='')
    def test_sin_numero_configurado_no_se_muestra(self):
        self._agregar(self.t40, 1)
        self._comprar()

        respuesta = self.client.get(reverse('web:pagoPedido'))
        self.assertEqual(respuesta.context['whatsapp'], '')
        self.assertNotContains(respuesta, 'Escribinos por WhatsApp')


class RechazoLiberaInventarioTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Rechazar un comprobante devuelve la unidad al catalogo.

    Con la validacion sin vencimiento, un pedido rechazado que se quedaba
    abierto congelaba su unidad para siempre: nada lo barria nunca.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B140-1', [(self.t40, 1, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _en_validacion(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        pago = pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion='OP-RECH', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )
        pedido.refresh_from_db()
        return pedido, pago

    def test_la_unidad_vuelve_al_catalogo(self):
        pedido, pago = self._en_validacion()
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 0)

        pago.rechazar('El monto no coincide')

        pedido.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        self.assertEqual(self.t40.reservado, 0)
        self.assertEqual(self.t40.disponible, 1)
        self.assertEqual(self.t40.stock, 1)      # nunca salio del estante

    def test_otro_cliente_puede_comprarla_enseguida(self):
        _, pago = self._en_validacion()
        pago.rechazar('Captura ilegible')

        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 1, 'sku': self.t40.variante.sku,
        })
        otro.get(reverse('web:continuarComoInvitado'))
        otro.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)

        self.assertEqual(Pedido.objects.filter(estado=Pedido.SOLICITADO).count(), 1)

    def test_el_pedido_conserva_su_numero(self):
        """ Un numero emitido no se devuelve, aunque el pedido termine cancelado """
        pedido, pago = self._en_validacion()
        numero = pedido.nro_pedido
        self.assertIsNotNone(numero)

        pago.rechazar('El monto no coincide')

        pedido.refresh_from_db()
        self.assertEqual(pedido.nro_pedido, numero)

class IdempotenciaCheckoutTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Apretar Confirmar diez veces tiene que dar lo mismo que apretarlo una.

    El bloqueo de filas ya impedia que dos clientes se llevaran la misma unidad,
    pero no que un cliente se llevara dos pedidos: con stock de sobra, el
    segundo clic reservaba otro par y creaba un pedido fantasma que retenia
    inventario hasta vencer.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B150-1', [(self.t40, 5, Decimal('300.00'))], aplicar=True)
        _cuenta_bcp()
        cache.clear()

    def _confirmar_con(self, token, cliente=None):
        cliente = cliente or self.client
        datos = dict(DATOS_CHECKOUT, token_checkout=token)
        return cliente.post(reverse('web:registrarPedido'), datos)

    def _preparar(self):
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))

    # --- el caso que motivo el cambio ---

    def test_dos_envios_con_el_mismo_token_dan_un_solo_pedido(self):
        self._preparar()
        self._confirmar_con('tok-doble-clic')

        # el carrito quedo vacio, asi que el segundo envio ni llega a crear:
        # se prueba directo contra el servicio, que es donde vive la garantia
        self.assertEqual(Pedido.objects.count(), 1)
        primero = Pedido.objects.get()
        self.assertEqual(primero.token_checkout, 'tok-doble-clic')

    def test_el_servicio_devuelve_el_pedido_ya_creado(self):
        """ El corazon del asunto: mismo token, mismo pedido, no uno nuevo """
        self._preparar()
        self._confirmar_con('tok-repetido')
        primero = Pedido.objects.get()

        # el cliente vuelve a cargar el carrito y reenvia el mismo formulario
        # (la marca de invitado se consume al confirmar, asi que se re-identifica)
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        respuesta = self._confirmar_con('tok-repetido')

        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(Pedido.objects.get().pk, primero.pk)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('pago', respuesta.url)

    def test_el_segundo_envio_no_reserva_otra_unidad(self):
        self._preparar()
        self._confirmar_con('tok-una-sola')
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 1)

        self._agregar(self.t40, 1)
        self._confirmar_con('tok-una-sola')

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 1)     # sigue siendo una

    def test_tokens_distintos_son_compras_distintas(self):
        """ Comprar dos veces a proposito tiene que seguir funcionando """
        self._preparar()
        self._confirmar_con('tok-compra-1')

        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        self._confirmar_con('tok-compra-2')

        self.assertEqual(Pedido.objects.count(), 2)

    def test_sin_token_se_compra_igual(self):
        """ Una pagina vieja, sin el campo, no puede quedarse sin comprar """
        self._preparar()
        self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)

        self.assertEqual(Pedido.objects.count(), 1)
        self.assertIsNone(Pedido.objects.get().token_checkout)

    # --- la carrera de verdad, contra el unique de la base ---

    def test_el_empate_lo_resuelve_la_base(self):
        """
        Los dos envios pasan el atajo y llegan a crear. El unique deja pasar
        uno; el que pierde devuelve el del ganador en vez de reventar.
        """
        from web.pedidos import _crear_cabecera

        comunes = dict(
            nombre_comprador='Ana', apellido_comprador='Perez',
            email_comprador='a@b.pe', telefono_comprador='999',
            modo_entrega=Pedido.ENVIO, monto_total=Decimal('100.00'),
        )
        ganador, cree_el_primero = _crear_cabecera(token='tok-empate', **comunes)
        perdedor, cree_el_segundo = _crear_cabecera(token='tok-empate', **comunes)

        self.assertTrue(cree_el_primero)
        self.assertFalse(cree_el_segundo)     # y eso es la orden de abandonar
        self.assertEqual(perdedor.pk, ganador.pk)
        self.assertEqual(Pedido.objects.filter(token_checkout='tok-empate').count(), 1)

    def test_un_choque_por_otra_cosa_no_se_traga(self):
        """ Solo se recupera del choque de token: los demas tienen que doler """
        from web.pedidos import _crear_cabecera

        comunes = dict(
            nombre_comprador='Ana', apellido_comprador='Perez',
            email_comprador='a@b.pe', telefono_comprador='999',
            modo_entrega=Pedido.ENVIO, monto_total=Decimal('100.00'),
        )
        _crear_cabecera(token=None, codigo_reserva='R-CHOQUE01', **comunes)
        with self.assertRaises(IntegrityError):
            _crear_cabecera(token=None, codigo_reserva='R-CHOQUE01', **comunes)

    def test_el_perdedor_de_la_carrera_no_duplica_lineas(self):
        """
        Regresion. El envio que pierde recibe el pedido del ganador. Si en vez
        de abandonar sigue adelante, le agrega sus lineas y sus reservas encima:
        un solo pedido, con el doble de todo. Los tests no lo agarraron; lo
        agarro mandar dos POST de verdad contra el servidor.
        """
        from unittest.mock import patch

        self._preparar()
        self._confirmar_con('tok-ganador')
        ganador = Pedido.objects.get()
        self.t40.refresh_from_db()
        self.assertEqual(ganador.detalles.count(), 1)
        self.assertEqual(self.t40.reservado, 1)

        # el perdedor llega hasta crear la cabecera y recibe la del ganador
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        with patch('web.pedidos._crear_cabecera', return_value=(ganador, False)):
            self._confirmar_con('tok-del-perdedor')

        ganador.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(ganador.detalles.count(), 1)    # sigue con una linea
        self.assertEqual(self.t40.reservado, 1)          # y una sola reserva
        self.assertEqual(Pedido.objects.count(), 1)

    # --- la pantalla ---

    def test_el_formulario_pinta_el_token(self):
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        respuesta = self.client.get(reverse('web:registrarPedido'))

        self.assertContains(respuesta, 'name="token_checkout"')
        self.assertContains(respuesta, 'type="hidden"')
        token = respuesta.context['frmPedido'].initial['token_checkout']
        self.assertEqual(len(token), 32)

    def test_cada_visita_trae_un_token_distinto(self):
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        uno = self.client.get(reverse('web:registrarPedido'))
        otro = self.client.get(reverse('web:registrarPedido'))

        self.assertNotEqual(uno.context['frmPedido'].initial['token_checkout'],
                            otro.context['frmPedido'].initial['token_checkout'])


class MensajesDeTransicionTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Un error tiene que sennalar lo que esta mal, no lo primero que no encaja.

    "Un pedido pagado no puede pasar a listo para recojo" manda a mirar el
    estado, y el estado esta bien: desde pagado se avanza sin problema. Lo que
    no encaja es que sea un pedido con envio a domicilio.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B160-1', [(self.t40, 4, Decimal('300.00'))], aplicar=True)
        _cuenta_bcp()
        cache.clear()

    def _pagado(self, recojo=False):
        return Pedido.objects.create(
            codigo_reserva='R-MSG' + ('R' if recojo else 'E'),
            nombre_comprador='Ana', apellido_comprador='Perez',
            email_comprador='a@b.pe', telefono_comprador='999',
            modo_entrega=Pedido.RECOJO if recojo else Pedido.ENVIO,
            monto_total=Decimal('100.00'), estado=Pedido.PAGADO,
        )

    def test_el_error_culpa_al_modo_de_entrega_y_no_al_estado(self):
        pedido = self._pagado(recojo=False)

        with self.assertRaises(ValueError) as caso:
            pedido.cambiar_estado(Pedido.LISTO_RECOJO)

        mensaje = str(caso.exception)
        self.assertIn('envio a domicilio', mensaje)
        self.assertIn('listo para recojo', mensaje)
        self.assertNotIn('Un pedido pagado', mensaje)

    def test_tambien_al_reves(self):
        pedido = self._pagado(recojo=True)

        with self.assertRaises(ValueError) as caso:
            pedido.cambiar_estado(Pedido.ENVIADO)

        self.assertIn('recojo en tienda', str(caso.exception))

    def test_el_mensaje_dice_que_corresponde_hacer(self):
        pedido = self._pagado(recojo=False)

        with self.assertRaises(ValueError) as caso:
            pedido.cambiar_estado(Pedido.LISTO_RECOJO)

        self.assertIn('enviado', str(caso.exception).lower())

    def test_cuando_el_estado_si_es_el_problema_lo_dice(self):
        """ El mensaje viejo sigue vivo donde de verdad corresponde """
        pedido = self._pagado(recojo=False)
        pedido.cambiar_estado(Pedido.ENVIADO)
        pedido.cambiar_estado(Pedido.ENTREGADO)

        with self.assertRaises(ValueError) as caso:
            pedido.cambiar_estado(Pedido.ENVIADO)

        self.assertIn('entregado', str(caso.exception).lower())


class CarritoAvisaFaltantesTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El carrito vive en la sesion y no sabe nada del almacen.

    Una linea puede pasar horas ahi mientras otro cliente se lleva la ultima
    unidad. Antes eso se descubria en el checkout, con el formulario ya lleno.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B170-1', [(self.t40, 1, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _otro_se_la_lleva(self):
        """ Otro cliente compra la unica unidad y sube su comprobante """
        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t40.id, 'cantidad': 1, 'sku': self.t40.variante.sku,
        })
        otro.get(reverse('web:continuarComoInvitado'))
        otro.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)
        pedido = Pedido.objects.latest('id')
        pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion='OP-GANADOR', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )
        return pedido

    def test_avisa_que_el_producto_se_agoto(self):
        self._agregar(self.t40, 1)
        self._otro_se_la_lleva()

        respuesta = self.client.get(reverse('web:carrito'))

        self.assertTrue(respuesta.context['hay_fallas'])
        self.assertContains(respuesta, 'se agoto')
        self.assertContains(respuesta, 'Quitalo del carrito para continuar')

    def test_frena_el_boton_de_realizar_pedido(self):
        self._agregar(self.t40, 1)
        self._otro_se_la_lleva()

        respuesta = self.client.get(reverse('web:carrito'))

        # aria-disabled solo existe en el marcado; boton-inerte tambien esta en el CSS
        self.assertContains(respuesta, 'aria-disabled="true"')
        self.assertNotContains(respuesta, reverse('web:identificarse'))

    def test_ofrece_el_asesor_por_si_ya_pago(self):
        self._agregar(self.t40, 1)
        self._otro_se_la_lleva()

        respuesta = self.client.get(reverse('web:carrito'))
        self.assertContains(respuesta, 'wa.me/' + settings.WHATSAPP_ASESOR)

    def test_un_carrito_sano_no_muestra_nada_de_esto(self):
        self._agregar(self.t40, 1)

        respuesta = self.client.get(reverse('web:carrito'))

        self.assertFalse(respuesta.context['hay_fallas'])
        self.assertNotContains(respuesta, 'aria-disabled="true"')
        self.assertContains(respuesta, reverse('web:identificarse'))

    def test_avisa_cuando_quedan_menos_de_las_que_pidio(self):
        self._compra('B170-2', [(self.t41, 3, Decimal('300.00'))], aplicar=True)
        self.t41.refresh_from_db()
        self._agregar(self.t41, 3)

        # otro cliente se lleva dos
        otro = Client()
        otro.post(reverse('web:agregarCarrito'), {
            'item_id': self.t41.id, 'cantidad': 2, 'sku': self.t41.variante.sku,
        })
        otro.get(reverse('web:continuarComoInvitado'))
        otro.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)

        respuesta = self.client.get(reverse('web:carrito'))
        self.assertContains(respuesta, 'solo queda')

    def test_la_reserva_restaurada_conserva_la_cantidad(self):
        """ Volvieron dos unidades, no una """
        self._compra('B170-3', [(self.t41, 2, Decimal('300.00'))], aplicar=True)
        self.t41.refresh_from_db()
        self._agregar(self.t41, 2)
        pedido = self._comprar()
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(minutes=30)
        )

        self.client.get(reverse('web:pagoPedido'))

        lineas = self.client.session['cart'].values()
        self.assertEqual(sum(l['cantidad'] for l in lineas), 2)


class EstadoExpiradoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Se le acabo el tiempo no es lo mismo que lo cancelaron.

    Los dos terminaban en CANCELADO y solo los separaba el texto del motivo.
    Filtrar por texto libre no sirve para contar, y sin contar cuantas reservas
    se caen solas no hay forma de saber si el plazo esta bien puesto.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B180-1', [(self.t40, 2, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _envejecer(self, pedido, minutos=30):
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(minutes=minutos)
        )
        pedido.refresh_from_db()
        return pedido

    # --- los dos finales, separados ---

    def test_el_plazo_vencido_expira_no_cancela(self):
        self._agregar(self.t40, 1)
        pedido = self._envejecer(self._comprar())

        Pedido.vencer_reservas()

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)
        self.assertNotEqual(pedido.estado, Pedido.CANCELADO)

    def test_el_rechazo_cancela_no_expira(self):
        """ Ese lo cerramos nosotros, y se cuenta aparte """
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        pago = pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion='OP-EXP-1', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )
        pago.rechazar('El monto no coincide')

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)

    def test_se_pueden_contar_por_separado(self):
        """ Lo que motiva el estado: poder medir """
        self._agregar(self.t40, 1)
        self._envejecer(self._comprar())
        Pedido.vencer_reservas()

        self._agregar(self.t40, 1)
        otro = self._comprar()
        otro.cancelar('Me arrepenti')

        self.assertEqual(Pedido.objects.filter(estado=Pedido.EXPIRADO).count(), 1)
        self.assertEqual(Pedido.objects.filter(estado=Pedido.CANCELADO).count(), 1)

    # --- expirar hace el mismo trabajo sucio que cancelar ---

    def test_expirar_suelta_las_unidades(self):
        self._agregar(self.t40, 2)
        pedido = self._envejecer(self._comprar())
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 2)

        pedido.expirar()

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.reservado, 0)
        self.assertEqual(self.t40.disponible, 2)
        self.assertEqual(self.t40.stock, 2)      # nunca salio del estante

    def test_un_expirado_no_se_vuelve_a_cerrar(self):
        self._agregar(self.t40, 1)
        pedido = self._envejecer(self._comprar())
        pedido.expirar()

        with self.assertRaises(ValueError) as caso:
            pedido.cancelar('Otra vez')
        self.assertIn('expirado', str(caso.exception).lower())

    def test_un_expirado_no_muestra_seguimiento(self):
        self._agregar(self.t40, 1)
        pedido = self._envejecer(self._comprar())
        pedido.expirar()

        self.assertEqual(pedido.pasos_seguimiento, [])

    # --- el hueco que motivo todo esto ---

    def test_si_el_barrido_llego_primero_igual_recupera_su_carrito(self):
        """
        Regresion. La vista preguntaba "se acaba de vencer?" en vez de "murio?".
        Como el barrido corre cada minuto, casi siempre llegaba antes y el
        cliente volvia a /pago sin que le devolvieran nada.
        """
        self._agregar(self.t40, 2)
        pedido = self._envejecer(self._comprar())

        # el barrido pasa primero, como pasa casi siempre en la vida real
        Pedido.vencer_reservas()
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)
        self.assertIsNone(pedido.reserva_vence)
        self.assertFalse(pedido.reserva_vencida)     # ya no "se acaba de vencer"

        respuesta = self.client.get(reverse('web:pagoPedido'))

        self.assertRedirects(respuesta, reverse('web:carrito'))
        lineas = self.client.session['cart'].values()
        self.assertEqual(sum(l['cantidad'] for l in lineas), 2)


class PedidoMuertoNoAdmiteComprobantesTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Un comprobante solo existe sobre un pedido vivo.

    Antes se aceptaba igual y se marcaba `fuera_de_plazo`, para que el sistema
    despues intentara resucitar el pedido. Sin esa resurreccion, aceptarlo solo
    creaba un pago que nadie podia procesar: ni validar ni cumplir. Ahora el
    cliente rehace la compra y sube el mismo voucher sobre el pedido nuevo.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B190-1', [(self.t40, 2, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _declarar(self, pedido, nro='OP-MUERTO'):
        return pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion=nro, voucher=_imagen(), fecha_pago=timezone.localdate(),
        )

    def _muerto(self, expirado=True):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        if expirado:
            Pedido.objects.filter(pk=pedido.pk).update(
                reserva_vence=timezone.now() - timedelta(minutes=30)
            )
            Pedido.vencer_reservas()
        else:
            self._declarar(pedido, nro='OP-PRIMERO').rechazar('Captura ilegible')
        pedido.refresh_from_db()
        return pedido

    def test_un_expirado_no_admite_comprobante(self):
        pedido = self._muerto(expirado=True)
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)

        with self.assertRaises(ValueError) as caso:
            self._declarar(pedido)
        self.assertIn('rehacer la compra', str(caso.exception))

    def test_un_cancelado_tampoco(self):
        pedido = self._muerto(expirado=False)
        self.assertEqual(pedido.estado, Pedido.CANCELADO)

        with self.assertRaises(ValueError) as caso:
            self._declarar(pedido, nro='OP-SEGUNDO')
        self.assertIn('rehacer la compra', str(caso.exception))

    def test_no_queda_ningun_pago_colgado(self):
        """ Lo que se evita: pagos en la bandeja que nadie puede resolver """
        pedido = self._muerto(expirado=True)
        with self.assertRaises(ValueError):
            self._declarar(pedido)

        self.assertEqual(pedido.pagos.count(), 0)

    def test_un_pedido_vivo_si_lo_admite(self):
        """ La regla corta lo muerto, no lo normal """
        self._agregar(self.t40, 1)
        pedido = self._comprar()

        pago = self._declarar(pedido, nro='OP-VIVO')

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EN_VALIDACION)
        self.assertEqual(pago.estado, Pago.PENDIENTE)

    def test_el_camino_bueno_es_rehacer_la_compra(self):
        """
        Al cliente no se le cierra la puerta: compra de nuevo con la unidad ya
        liberada y sube el mismo voucher sobre el pedido nuevo.
        """
        self._muerto(expirado=True)
        self.t40.refresh_from_db()
        self.assertEqual(self.t40.disponible, 2)     # la unidad volvio

        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        self.client.post(reverse('web:registrarPedido'), DATOS_CHECKOUT)
        nuevo = Pedido.objects.latest('id')

        self._declarar(nuevo, nro='OP-MISMO-VOUCHER').validar(nuevo.monto_total)

        nuevo.refresh_from_db()
        self.t40.refresh_from_db()
        self.assertEqual(nuevo.estado, Pedido.PAGADO)
        self.assertEqual(self.t40.stock, 1)          # se vendio de verdad

    def test_la_pantalla_de_pago_lo_manda_al_carrito(self):
        """ El cliente no ve un formulario inutil: ve su carrito de vuelta """
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        self._declarar(pedido, nro='OP-RECHAZADO').rechazar('El monto no coincide')

        respuesta = self.client.get(reverse('web:pagoPedido'), follow=True)

        self.assertRedirects(respuesta, reverse('web:carrito'))
        self.assertContains(respuesta, 'No pudimos confirmar tu pago')
        lineas = self.client.session['cart'].values()
        self.assertEqual(sum(l['cantidad'] for l in lineas), 1)


class ContadorNoSeAdelantaTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El contador llegaba a cero antes que el servidor y la pantalla se moria.

    `int()` truncaba, asi que el navegador siempre arrancaba por debajo del
    tiempo real: su cuenta terminaba mientras el servidor todavia daba la
    reserva por viva. Recargaba, el servidor no lo mandaba a ningun lado, y como
    la plantilla trataba el cero como "no hay reloj", la pagina quedaba quieta
    con el producto retenido hasta que alguien la refrescara a mano.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B200-1', [(self.t40, 2, Decimal('300.00'))], aplicar=True)
        _cuenta_bcp()
        cache.clear()

    def _con_vencimiento_en(self, segundos):
        self._agregar(self.t40, 1)
        pedido = self._comprar()
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() + timedelta(seconds=segundos)
        )
        pedido.refresh_from_db()
        return pedido

    def test_una_fraccion_de_segundo_cuenta_como_uno(self):
        """ Truncando daba 0 con la reserva todavia viva: ahi nacia el cuelgue """
        pedido = self._con_vencimiento_en(0.6)

        self.assertFalse(pedido.reserva_vencida)
        self.assertEqual(pedido.segundos_restantes, 1)

    def test_el_reloj_nunca_llega_a_cero_antes_que_el_servidor(self):
        """ Mientras la reserva este viva, el contador tiene que mostrar algo """
        for fraccion in (0.1, 0.5, 0.9, 1.4, 59.2):
            pedido = self._con_vencimiento_en(fraccion)
            self.assertFalse(pedido.reserva_vencida, fraccion)
            self.assertGreater(pedido.segundos_restantes, 0, fraccion)
            pedido.cancelar('limpieza del test')

    def test_la_pantalla_pinta_el_reloj_aunque_quede_poco(self):
        self._con_vencimiento_en(0.6)

        respuesta = self.client.get(reverse('web:pagoPedido'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'pg-reloj')
        self.assertContains(respuesta, 'data-segundos="1"')

    def test_ya_vencido_no_pinta_reloj_porque_se_va_al_carrito(self):
        pedido = self._con_vencimiento_en(-30)

        respuesta = self.client.get(reverse('web:pagoPedido'))

        self.assertRedirects(respuesta, reverse('web:carrito'))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EXPIRADO)

    def test_el_plazo_completo_no_pierde_el_ultimo_segundo(self):
        self._agregar(self.t40, 1)
        pedido = self._comprar()

        # con truncado daba MINUTOS_RESERVA*60 - 1; ahora llega al valor entero
        self.assertEqual(pedido.segundos_restantes, settings.MINUTOS_RESERVA * 60)


class ListadoDePedidosTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El listado de pedidos se escanea, no se lee.

    No tenia orden declarado ni en el modelo ni en el admin, asi que MySQL
    devolvia por insercion y lo primero que se veia al entrar era el pedido mas
    viejo de todos. Y con 44 de 47 filas terminadas, las ventas reales quedaban
    enterradas. Se resolvio con dos canales: color para que paso, negrita para
    lo que espera una accion tuya.
    """

    URL = '/admin/web/pedido/'

    def setUp(self):
        self._montar_almacen()
        self._compra('B210-1', [(self.t40, 6, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()
        self.client.force_login(
            User.objects.create_superuser('jefa', 'jefa@goatx.pe', 'clave-de-prueba')
        )

    def _pedido(self, estado, dias_atras=0):
        pedido = Pedido.objects.create(
            codigo_reserva=f'R-LST{estado}{dias_atras}',
            nombre_comprador='Ana', apellido_comprador='Perez',
            email_comprador='a@b.pe', telefono_comprador='999',
            modo_entrega=Pedido.ENVIO, monto_total=Decimal('100.00'), estado=estado,
        )
        if dias_atras:
            Pedido.objects.filter(pk=pedido.pk).update(
                fecha_registro=timezone.now() - timedelta(days=dias_atras)
            )
        return pedido

    # --- el orden ---

    def test_lo_mas_nuevo_va_primero(self):
        viejo = self._pedido(Pedido.ENTREGADO, dias_atras=5)
        nuevo = self._pedido(Pedido.ENTREGADO, dias_atras=0)

        respuesta = self.client.get(self.URL)
        cuerpo = respuesta.content.decode()

        self.assertLess(cuerpo.index(nuevo.referencia), cuerpo.index(viejo.referencia))

    # --- el color dice que paso ---

    def test_cada_situacion_trae_su_color(self):
        # el atributo completo: el fragmento 'sit-nada' tambien vive en el CSS
        casos = {
            Pedido.EN_VALIDACION: 'class="sit sit-accion"',
            Pedido.PAGADO: 'class="sit sit-accion"',
            Pedido.ENVIADO: 'class="sit sit-curso"',
            Pedido.ENTREGADO: 'class="sit sit-bien"',
            Pedido.SOLICITADO: 'class="sit sit-reserva"',
            Pedido.EXPIRADO: 'class="sit sit-nada"',
            Pedido.CANCELADO: 'class="sit sit-cerrado"',
        }
        for estado, clase in casos.items():
            self._pedido(estado)

        cuerpo = self.client.get(self.URL).content.decode()
        for estado, clase in casos.items():
            self.assertIn(clase, cuerpo, estado)

    def test_el_expirado_no_se_pinta_de_alarma(self):
        """
        Es el estado mas numeroso y el que menos significa: una reserva vencida
        no es una falla. Con el color de atencion, el listado quedaria en rojo y
        el rojo dejaria de decir nada.
        """
        self._pedido(Pedido.EXPIRADO)

        cuerpo = self.client.get(self.URL).content.decode()

        self.assertIn('class="sit sit-nada"', cuerpo)
        self.assertNotIn('class="sit sit-accion"', cuerpo)

    # --- el negrita dice si te espera ---

    def test_lo_que_espera_accion_se_marca(self):
        self._pedido(Pedido.EN_VALIDACION)

        cuerpo = self.client.get(self.URL).content.decode()

        self.assertIn('class="sit sit-accion"', cuerpo)
        self.assertIn(':has(.sit-accion)', cuerpo)      # la regla que engrosa la fila

    def test_sin_nada_pendiente_no_hay_ninguna_marcada(self):
        self._pedido(Pedido.ENTREGADO)
        self._pedido(Pedido.EXPIRADO)
        self._pedido(Pedido.CANCELADO)

        cuerpo = self.client.get(self.URL).content.decode()

        self.assertNotIn('class="sit sit-accion"', cuerpo)

    # --- el filtro agrupado ---

    def test_el_filtro_agrupa_por_lo_que_hay_que_hacer(self):
        cuerpo = self.client.get(self.URL).content.decode()
        for etiqueta in ('Necesita accion', 'En curso', 'Reservas vivas',
                         'Terminadas bien', 'Sin venta'):
            self.assertIn(etiqueta, cuerpo)

    def test_el_grupo_sin_venta_junta_expirados_y_cancelados(self):
        expirado = self._pedido(Pedido.EXPIRADO)
        cancelado = self._pedido(Pedido.CANCELADO)
        vivo = self._pedido(Pedido.ENTREGADO)

        cuerpo = self.client.get(self.URL, {'situacion': 'sin_venta'}).content.decode()

        self.assertIn(expirado.referencia, cuerpo)
        self.assertIn(cancelado.referencia, cuerpo)
        self.assertNotIn(vivo.referencia, cuerpo)

    def test_sin_filtro_se_ve_todo(self):
        """ No filtra por defecto: esconder filas genera "por que no aparece este pedido" """
        expirado = self._pedido(Pedido.EXPIRADO)
        entregado = self._pedido(Pedido.ENTREGADO)

        cuerpo = self.client.get(self.URL).content.decode()

        self.assertIn(expirado.referencia, cuerpo)
        self.assertIn(entregado.referencia, cuerpo)


class CorreosAlClienteTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    Hasta ahora el cliente dejaba su correo en el checkout y no volvia a saber
    de nosotros. Ni cuando confirmabamos su pago, ni cuando su pedido quedaba
    esperandolo en el mostrador.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B220-1', [(self.t40, 6, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _pedido(self, recojo=False):
        self._agregar(self.t40, 1)
        self.client.get(reverse('web:continuarComoInvitado'))
        datos = dict(DATOS_CHECKOUT)
        if recojo:
            datos['modo_entrega'] = Pedido.RECOJO
            datos['punto_recojo'] = PuntoRecojo.objects.filter(activo=True).first().pk
        self.client.post(reverse('web:registrarPedido'), datos)
        return Pedido.objects.latest('id')

    def _declarar(self, pedido, nro='OP-CORREO'):
        with self.captureOnCommitCallbacks(execute=True):
            return pedido.declarar_pago(
                cuenta=self.cuenta, monto_declarado=pedido.monto_total,
                nro_operacion=nro, voucher=_imagen(), fecha_pago=timezone.localdate(),
            )

    def _mover(self, pedido, estado):
        with self.captureOnCommitCallbacks(execute=True):
            pedido.cambiar_estado(estado)

    def _al_cliente(self, pedido):
        return [m for m in mail.outbox if m.to == [pedido.email_comprador]]

    # --- comprobante recibido ---

    def test_le_avisamos_que_su_comprobante_llego(self):
        pedido = self._pedido()
        mail.outbox = []
        self._declarar(pedido)

        pedido.refresh_from_db()
        correo = self._al_cliente(pedido)[0]
        self.assertIn('Recibimos tu comprobante', correo.subject)
        self.assertIn(pedido.nro_pedido, correo.subject)
        self.assertIn(str(settings.HORAS_VALIDACION), correo.body)

    def test_el_correo_trae_el_detalle_y_el_total(self):
        pedido = self._pedido()
        mail.outbox = []
        self._declarar(pedido)

        cuerpo = self._al_cliente(pedido)[0].body
        self.assertIn('Adidas Campus 00s', cuerpo)
        self.assertIn(str(pedido.monto_total), cuerpo)

    # --- pago confirmado ---

    def test_le_avisamos_que_confirmamos_el_pago(self):
        pedido = self._pedido()
        self._declarar(pedido)
        mail.outbox = []
        self._mover(pedido, Pedido.PAGADO)

        correo = self._al_cliente(pedido)[0]
        self.assertIn('Confirmamos tu pago', correo.subject)
        self.assertIn('preparando', correo.body)          # es envio

    def test_con_recojo_le_dice_donde_lo_va_a_retirar(self):
        pedido = self._pedido(recojo=True)
        self._declarar(pedido, nro='OP-RECOJO')
        mail.outbox = []
        self._mover(pedido, Pedido.PAGADO)

        self.assertIn(pedido.punto_recojo_nombre, self._al_cliente(pedido)[0].body)

    # --- listo para recojo: el que mas falta hacia ---

    def test_le_avisamos_que_su_pedido_lo_espera(self):
        pedido = self._pedido(recojo=True)
        self._declarar(pedido, nro='OP-ESPERA')
        self._mover(pedido, Pedido.PAGADO)
        mail.outbox = []
        self._mover(pedido, Pedido.LISTO_RECOJO)

        correo = self._al_cliente(pedido)[0]
        self.assertIn('te espera', correo.subject)
        self.assertIn(pedido.punto_recojo_nombre, correo.body)
        self.assertIn(pedido.punto_recojo_direccion, correo.body)
        self.assertIn('documento', correo.body)

    def test_el_correo_de_recojo_trae_el_horario(self):
        punto = PuntoRecojo.objects.filter(activo=True).first()
        punto.horario = 'Lun a Sab de 9:00 a 19:00'
        punto.save(update_fields=['horario'])

        pedido = self._pedido(recojo=True)
        self._declarar(pedido, nro='OP-HORARIO')
        self._mover(pedido, Pedido.PAGADO)
        mail.outbox = []
        self._mover(pedido, Pedido.LISTO_RECOJO)

        self.assertIn('Lun a Sab de 9:00 a 19:00', self._al_cliente(pedido)[0].body)

    # --- enviado ---

    def test_le_avisamos_que_salio(self):
        pedido = self._pedido()
        self._declarar(pedido, nro='OP-ENVIO')
        self._mover(pedido, Pedido.PAGADO)
        mail.outbox = []
        self._mover(pedido, Pedido.ENVIADO)

        correo = self._al_cliente(pedido)[0]
        self.assertIn('va en camino', correo.subject)
        self.assertIn(pedido.direccion_envio, correo.body)

    # --- rechazado ---

    def test_el_rechazo_le_dice_por_que_y_como_seguir(self):
        pedido = self._pedido()
        pago = self._declarar(pedido, nro='OP-RECHAZO')
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            pago.rechazar('La captura no deja ver el monto')

        correo = self._al_cliente(pedido)[0]
        self.assertIn('No pudimos confirmar tu pago', correo.subject)
        self.assertIn('La captura no deja ver el monto', correo.body)
        self.assertIn('devolucion', correo.body)

    # --- lo que NO debe pasar ---

    def test_al_entregar_le_pedimos_que_avise_si_no_lo_recibio(self):
        """
        Marcar como entregado es el unico paso que hacemos nosotros solos. Sin
        este correo, un error nuestro -- el pedido que no era, el motorizado que
        dijo que entrego -- solo se descubre cuando el cliente reclama.
        """
        pedido = self._pedido()
        self._declarar(pedido, nro='OP-FIN')
        self._mover(pedido, Pedido.PAGADO)
        self._mover(pedido, Pedido.ENVIADO)
        mail.outbox = []
        self._mover(pedido, Pedido.ENTREGADO)

        correo = self._al_cliente(pedido)[0]
        self.assertIn('Entregamos tu pedido', correo.subject)
        self.assertIn('Si no lo recibiste', correo.body)
        self.assertIn(pedido.direccion_envio, correo.body)

    def test_el_de_entrega_por_recojo_dice_donde_lo_retiro(self):
        pedido = self._pedido(recojo=True)
        self._declarar(pedido, nro='OP-FIN-R')
        self._mover(pedido, Pedido.PAGADO)
        self._mover(pedido, Pedido.LISTO_RECOJO)
        mail.outbox = []
        self._mover(pedido, Pedido.ENTREGADO)

        cuerpo = self._al_cliente(pedido)[0].body
        self.assertIn('Lo retiraste en', cuerpo)
        self.assertIn(pedido.punto_recojo_nombre, cuerpo)

    def test_todos_traen_el_enlace_al_asesor(self):
        pedido = self._pedido()
        mail.outbox = []
        self._declarar(pedido, nro='OP-ASESOR')

        self.assertIn(settings.WHATSAPP_ASESOR, self._al_cliente(pedido)[0].body)

    def test_el_correo_va_con_fail_silently(self):
        """
        Que el servidor de correo este caido no puede impedir que se confirme un
        pago. Se verifica el contrato -- que pedimos `fail_silently` -- y no que
        Django lo cumpla, que es cosa suya.
        """
        from unittest.mock import patch

        pedido = self._pedido()
        self._declarar(pedido, nro='OP-CAIDO')

        with patch('web.avisos.send_mail') as enviar:
            with self.captureOnCommitCallbacks(execute=True):
                pedido.cambiar_estado(Pedido.PAGADO)

        self.assertTrue(enviar.called)
        for llamada in enviar.call_args_list:
            self.assertTrue(llamada.kwargs.get('fail_silently'))

    def test_el_correo_sale_recien_al_confirmar_la_transaccion(self):
        """ Si la venta se cae, no puede haber salido un correo diciendo que ocurrio """
        pedido = self._pedido()
        self._declarar(pedido, nro='OP-COMMIT')
        mail.outbox = []

        # sin ejecutar los callbacks, no hay correo todavia
        with self.captureOnCommitCallbacks(execute=False):
            pedido.cambiar_estado(Pedido.PAGADO)
        self.assertEqual(self._al_cliente(pedido), [])


class AvisoPedidoEnCursoTests(_AlmacenMixin, _VentaMixin, TestCase):
    """
    El camino de vuelta a la pantalla de pago.

    /pago se encuentra por la sesion y no hay ninguna URL que lleve a ella. Quien
    se iba a mirar otro producto perdia el rastro, se le vencia la reserva en
    diez minutos y nunca supo por que. Era el unico agujero que costaba una venta
    de alguien que ya habia decidido comprar.
    """

    def setUp(self):
        self._montar_almacen()
        self._compra('B230-1', [(self.t40, 4, Decimal('300.00'))], aplicar=True)
        self.cuenta = _cuenta_bcp()
        cache.clear()

    def _con_pedido(self):
        self._agregar(self.t40, 1)
        return self._comprar()

    def _mirar(self, url=None):
        return self.client.get(url or reverse('web:index'))

    # --- cuando debe aparecer ---

    def test_aparece_en_cualquier_pantalla(self):
        pedido = self._con_pedido()

        for url in (reverse('web:index'),
                    reverse('web:carrito'),
                    reverse('web:producto', args=[self.variante.sku])):
            respuesta = self.client.get(url)
            self.assertContains(respuesta, 'Tenes un pedido esperando pago', msg_prefix=url)
            self.assertContains(respuesta, pedido.referencia, msg_prefix=url)

    def test_trae_el_enlace_de_vuelta_y_el_monto(self):
        pedido = self._con_pedido()

        respuesta = self._mirar()
        self.assertContains(respuesta, reverse('web:pagoPedido'))
        self.assertContains(respuesta, 'Completar mi pago')
        self.assertContains(respuesta, str(pedido.monto_total))

    def test_lleva_los_segundos_para_el_contador(self):
        self._con_pedido()

        respuesta = self._mirar()
        segundos = respuesta.context['segundos_en_curso']
        self.assertGreater(segundos, 0)
        self.assertLessEqual(segundos, settings.MINUTOS_RESERVA * 60)

    # --- cuando NO debe aparecer ---

    def test_no_molesta_a_quien_no_tiene_nada_pendiente(self):
        respuesta = self._mirar()

        self.assertIsNone(respuesta.context['pedido_en_curso'])
        self.assertNotContains(respuesta, 'Tenes un pedido esperando pago')

    def test_no_se_repite_en_la_propia_pantalla_de_pago(self):
        """ Ahi ya hay un contador: mostrar dos es ruido """
        self._con_pedido()

        respuesta = self.client.get(reverse('web:pagoPedido'))

        self.assertIsNone(respuesta.context['pedido_en_curso'])
        self.assertNotContains(respuesta, 'Completar mi pago')

    def test_desaparece_cuando_el_pedido_avanza(self):
        pedido = self._con_pedido()
        pedido.declarar_pago(
            cuenta=self.cuenta, monto_declarado=pedido.monto_total,
            nro_operacion='OP-BANNER', voucher=_imagen(), fecha_pago=timezone.localdate(),
        )

        self.assertIsNone(self._mirar().context['pedido_en_curso'])

    def test_desaparece_cuando_el_pedido_muere(self):
        pedido = self._con_pedido()
        pedido.expirar()

        self.assertIsNone(self._mirar().context['pedido_en_curso'])

    # --- el caso limite ---

    def test_vencido_pero_sin_barrer_avisa_igual(self):
        """
        Entre que se cumple el plazo y algo lo barre, el aviso sigue saliendo con
        cero segundos. El JS lo convierte en "se vencio" en vez de dejar un
        contador congelado.
        """
        pedido = self._con_pedido()
        Pedido.objects.filter(pk=pedido.pk).update(
            reserva_vence=timezone.now() - timedelta(minutes=1)
        )

        respuesta = self._mirar()

        self.assertIsNotNone(respuesta.context['pedido_en_curso'])
        self.assertEqual(respuesta.context['segundos_en_curso'], 0)
        self.assertContains(respuesta, 'data-segundos="0"')

    def test_no_se_escapa_el_comentario_de_la_plantilla(self):
        """
        Regresion. El comentario iba con {# #}, que en Django es de UNA linea:
        el resto se imprimia como texto arriba de la pagina. Los tests no lo
        agarraron porque buscaban lo que TIENE que estar, no lo que no.
        """
        self._con_pedido()

        cuerpo = self._mirar().content.decode()

        self.assertNotIn('El camino de vuelta a la pantalla de pago', cuerpo)
        self.assertNotIn('buscador de pedidos', cuerpo)

    def test_una_sesion_no_ve_el_pedido_de_otra(self):
        """ Se lee de la sesion: otro navegador no tiene por que enterarse """
        self._con_pedido()

        otro = Client()
        self.assertIsNone(otro.get(reverse('web:index')).context['pedido_en_curso'])
