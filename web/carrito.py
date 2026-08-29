"""
Carrito de compras guardado en la sesion del navegador.

La unidad del carrito es Inventario (color + talla/capacidad/etc), no Producto:
es la unica que tiene precio y stock propios.
"""

from decimal import Decimal


class Cart:

    CLAVE_SESION = 'cart'
    CLAVE_TOTAL = 'cartMontoTotal'
    CLAVE_CUPON = 'cartCupon'

    def __init__(self, request):
        self.session = request.session
        cart = self.session.get(self.CLAVE_SESION)
        if not isinstance(cart, dict):
            cart = {}
            self.session[self.CLAVE_SESION] = cart
            self.cart = cart
            return

        normalizado = self._normalizar(cart)
        if normalizado != cart:
            # el carrito venia de una version anterior: se guarda ya convertido
            self.session[self.CLAVE_SESION] = normalizado
            self.session.modified = True
        self.cart = normalizado

    @staticmethod
    def _normalizar(cart):
        """
        Adapta carritos guardados con el formato viejo, de cuando la unidad se
        llamaba VarianteTalla. Sin esto, un cliente con el carrito abierto desde
        antes del cambio veria un error al entrar.
        """
        limpio = {}
        for clave, linea in cart.items():
            if not isinstance(linea, dict):
                continue

            item_id = linea.get('item_id') or linea.get('variante_talla_id')
            if not item_id:
                continue                      # linea irreconocible: se descarta

            linea = dict(linea)
            linea['item_id'] = item_id
            linea.pop('variante_talla_id', None)

            if 'valor' not in linea:
                linea['valor'] = linea.pop('talla', '')
            else:
                linea.pop('talla', None)

            if 'atributo' not in linea:
                linea['atributo'] = 'Talla' if linea['valor'] else ''

            limpio[clave] = linea
        return limpio

    # ------------------------------------------------------------------ #

    @staticmethod
    def _unidades(cantidad):
        return '1 unidad' if cantidad == 1 else f'{cantidad} unidades'

    @staticmethod
    def _nombrar(item):
        """ 'la talla 40', 'la capacidad 128GB', o el color si no tiene atributo """
        if item.valor_id is None:
            return f'color {item.variante.color}'
        return f'{item.valor.atributo.nombre.lower()} {item.valor}'

    def add(self, item, cantidad=1):
        """
        Agrega (o suma) unidades de una unidad de inventario al carrito.
        Lanza ValueError si no hay stock suficiente.
        """
        if cantidad <= 0:
            raise ValueError('La cantidad debe ser mayor a 0')

        clave = str(item.id)
        en_carrito = self.cart.get(clave, {}).get('cantidad', 0)
        nueva_cantidad = en_carrito + cantidad

        if nueva_cantidad > item.disponible:
            nombre = self._nombrar(item)
            if item.disponible == 0:
                raise ValueError(f'{nombre.capitalize()}: sin stock')

            disponible = item.disponible - en_carrito
            if disponible <= 0:
                raise ValueError(
                    f'Ya tienes en tu carrito {self._unidades(item.disponible)} '
                    f'de {nombre}, que es todo el stock disponible'
                )
            raise ValueError(f'Solo quedan {self._unidades(disponible)} mas de {nombre}')

        self.cart[clave] = self._linea(item, nueva_cantidad)
        self.save()

    @staticmethod
    def _linea(item, cantidad):
        """ La fotografia de una linea del carrito, sin mirar disponibilidad """
        variante = item.variante
        precio = item.precio_final()
        return {
            'item_id': item.id,
            'sku': variante.sku,
            'nombre': variante.producto.nombre,
            'categoria': variante.producto.categoria.nombre,
            'color': variante.color.nombre,
            'atributo': item.valor.atributo.nombre if item.valor_id else '',
            'valor': item.valor.valor if item.valor_id else '',
            'imagen': variante.imagen.url if variante.imagen else '',
            'precio': str(precio),
            'cantidad': cantidad,
            'subtotal': str(precio * cantidad),
        }

    def restaurar(self, pares):
        """
        Devuelve al carrito lo que tenia un pedido, cueste lo que cueste.

        A diferencia de `add`, no comprueba disponibilidad: si mientras el
        cliente no pagaba otro se llevo la unidad, la linea igual tiene que
        volver. Verla en el carrito con el aviso de que se agoto es mucho mas
        claro que encontrarse el carrito vacio sin explicacion.

        `pares` son tuplas (Inventario, cantidad).
        """
        for item, cantidad in pares:
            if cantidad <= 0:
                continue
            clave = str(item.id)
            anterior = self.cart.get(clave, {}).get('cantidad', 0)
            self.cart[clave] = self._linea(item, anterior + cantidad)
        self.save()

    def problemas(self):
        """
        Que lineas ya no se pueden comprar, y por que. {item_id: motivo}

        El carrito vive en la sesion y no sabe nada del almacen: una linea puede
        pasar horas ahi mientras otro cliente se lleva la ultima unidad. Esto lo
        dice en el carrito, y no en el checkout, que es donde el golpe llega
        tarde y con el formulario ya lleno.
        """
        from .models import Inventario

        if not self.cart:
            return {}

        ids = [linea['item_id'] for linea in self]
        items = {
            item.id: item
            for item in Inventario.precargar_vencidas(
                Inventario.objects
                .filter(id__in=ids)
                .select_related('variante__producto')
            )
        }

        fallas = {}
        for linea in self:
            item = items.get(linea['item_id'])
            if item is None or not item.variante.activo or not item.variante.producto.activo:
                fallas[linea['item_id']] = 'ya no esta a la venta'
            elif item.disponible == 0:
                fallas[linea['item_id']] = 'se agoto'
            elif linea['cantidad'] > item.disponible:
                unidades = 'unidad' if item.disponible == 1 else 'unidades'
                fallas[linea['item_id']] = f'solo quedan {item.disponible} {unidades}'
        return fallas

    def actualizar(self, item, cantidad):
        """ Fija una cantidad exacta. Si es 0 o menos, quita la linea. """
        clave = str(item.id)
        if clave not in self.cart:
            return
        if cantidad <= 0:
            del self.cart[clave]
            self.save()
            return
        if cantidad > item.disponible:
            raise ValueError(
                f'Solo quedan {self._unidades(item.disponible)} de {self._nombrar(item)}'
            )
        precio = Decimal(self.cart[clave]['precio'])
        self.cart[clave]['cantidad'] = cantidad
        self.cart[clave]['subtotal'] = str(precio * cantidad)
        self.save()

    def delete(self, item):
        clave = str(item.id)
        if clave in self.cart:
            del self.cart[clave]
            self.save()

    def clear(self):
        self.cart = {}
        self.session[self.CLAVE_SESION] = {}
        self.session[self.CLAVE_TOTAL] = '0.00'
        self.session.pop(self.CLAVE_CUPON, None)
        self.session.modified = True

    # ------------------- cupones ------------------- #

    def aplicar_cupon(self, codigo):
        """
        Guarda el cupon en la sesion. Lanza ValueError con el motivo si no aplica.
        Solo se guarda el codigo: el descuento se recalcula en cada vista.
        """
        from .models import Cupon

        codigo = (codigo or '').strip().upper()
        if not codigo:
            raise ValueError('Escribe un codigo de cupon')

        cupon = Cupon.objects.filter(codigo=codigo).first()
        if cupon is None:
            raise ValueError(f'El cupon {codigo} no existe')

        valido, motivo = cupon.es_valido(self.subtotal)
        if not valido:
            raise ValueError(motivo)

        self.session[self.CLAVE_CUPON] = cupon.codigo
        self.save()
        return cupon

    def quitar_cupon(self):
        self.session.pop(self.CLAVE_CUPON, None)
        self.save()

    @property
    def cupon(self):
        """ El cupon guardado, solo si sigue siendo valido para el monto actual """
        from .models import Cupon

        codigo = self.session.get(self.CLAVE_CUPON)
        if not codigo:
            return None

        cupon = Cupon.objects.filter(codigo=codigo).first()
        if cupon is None:
            return None

        valido, _ = cupon.es_valido(self.subtotal)
        return cupon if valido else None

    @property
    def motivo_cupon_invalido(self):
        """ Por que dejo de aplicar el cupon guardado (ej. el carrito bajo del minimo) """
        from .models import Cupon

        codigo = self.session.get(self.CLAVE_CUPON)
        if not codigo:
            return ''
        cupon = Cupon.objects.filter(codigo=codigo).first()
        if cupon is None:
            return f'El cupon {codigo} ya no existe'
        valido, motivo = cupon.es_valido(self.subtotal)
        return '' if valido else motivo

    @property
    def descuento(self):
        cupon = self.cupon
        if cupon is None:
            return Decimal('0.00')
        return cupon.calcular_descuento(self.subtotal).quantize(Decimal('0.01'))

    # ------------------- totales ------------------- #

    @property
    def subtotal(self):
        """ Suma de las lineas, antes del cupon """
        return sum((Decimal(linea['subtotal']) for linea in self.cart.values()), Decimal('0'))

    @property
    def total(self):
        """ Lo que realmente paga el cliente """
        return self.subtotal - self.descuento

    @property
    def cantidad_items(self):
        return sum(linea['cantidad'] for linea in self.cart.values())

    def __len__(self):
        return len(self.cart)

    def __iter__(self):
        return iter(self.cart.values())

    def save(self):
        self.session[self.CLAVE_SESION] = self.cart
        self.session[self.CLAVE_TOTAL] = str(self.total)
        self.session.modified = True
