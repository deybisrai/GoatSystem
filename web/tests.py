from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import RestrictedError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Categoria,
    Cliente,
    Color,
    Compra,
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

    def test_la_venta_descuenta_el_stock(self):
        self._agregar(self.t40, 2)
        self._confirmar()

        self.t40.refresh_from_db()
        self.assertEqual(self.t40.stock, 1)

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

    def test_nro_pedido_se_genera_solo(self):
        self._agregar(self.t40, 1)
        self._confirmar()
        self.assertRegex(Pedido.objects.get().nro_pedido, r'^P\d{8}-\d{5}$')

    def test_compra_de_varias_tallas_a_la_vez(self):
        self._agregar(self.t40, 2)
        self._agregar(self.t41, 1)
        self._confirmar()

        pedido = Pedido.objects.get()
        self.assertEqual(pedido.detalles.count(), 2)
        self.assertEqual(pedido.monto_total, Decimal('1287.00'))
        self.t40.refresh_from_db()
        self.t41.refresh_from_db()
        self.assertEqual(self.t40.stock, 1)
        self.assertEqual(self.t41.stock, 0)

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
        self.assertContains(respuesta, Pedido.objects.get().nro_pedido)

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
        self.assertEqual(self.item.stock, 1)

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
        self.assertEqual(self.item.stock, 2)

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
        self.assertEqual(item.stock, 1)
